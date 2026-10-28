from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .chunker import chunk_document
from .embeddings import Embedder
from .llm import DeepSeekClient, LLMNotConfigured
from .loaders import load_document
from .models import ChatResponse, DocumentInfo, SearchResponse, Source
from .registry import DocumentRegistry
from .settings import KnowledgeSettings
from .structured import StructuredLookup, StructuredResult
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(
        self,
        settings: KnowledgeSettings,
        data_dir: Path,
        data_version_provider: Callable[[], str],
        structured: StructuredLookup | None = None,
    ) -> None:
        self.settings = settings
        self.data_dir = data_dir
        self._data_version = data_version_provider
        self.embedder = Embedder(settings)
        self.store = VectorStore(data_dir / "chroma", settings.collection_name)
        self.registry = DocumentRegistry(data_dir / "documents.json")
        self.llm = DeepSeekClient(settings)
        self.structured = structured
        self._ingest_lock = threading.Lock()

    # ------------------------------------------------------------------ ingest
    def ingest_bytes(self, data: bytes, filename: str) -> DocumentInfo:
        with self._ingest_lock:
            document = load_document(data, filename)

            existing = self.registry.find_by_sha(document.sha256)
            if existing is not None:
                logger.info("文档重复，跳过: %s", filename)
                return existing.model_copy(update={"duplicate": True})

            chunks = chunk_document(
                document,
                chunk_size=self.settings.chunk_size,
                overlap=self.settings.chunk_overlap,
            )
            if not chunks:
                raise ValueError(f"{filename} 未提取到可索引的文本")

            embeddings = self.embedder.encode_documents([c.text for c in chunks])

            doc_id = uuid.uuid4().hex[:12]
            self.store.add_document(doc_id, document.filename, chunks, embeddings)

            if self.settings.keep_uploads:
                upload_dir = self.data_dir / "uploads"
                upload_dir.mkdir(parents=True, exist_ok=True)
                (upload_dir / f"{doc_id}{document.extension}").write_bytes(data)

            info = DocumentInfo(
                id=doc_id,
                filename=document.filename,
                extension=document.extension,
                size_bytes=document.size_bytes,
                sha256=document.sha256,
                chunk_count=len(chunks),
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            self.registry.add(info)
            logger.info("已索引 %s -> %s (%d chunks)", filename, doc_id, len(chunks))
            return info

    # ---------------------------------------------------------------- retrieve
    def search(
        self,
        query: str,
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
        min_score: float | None = None,
    ) -> SearchResponse:
        hits = self._retrieve(query, top_k, doc_ids, min_score)
        return SearchResponse(query=query, sources=self._to_sources(hits))

    def _retrieve(
        self,
        query: str,
        top_k: int | None,
        doc_ids: list[str] | None,
        min_score: float | None,
    ) -> list[dict]:
        if not query.strip():
            return []
        embedding = self.embedder.encode_query(query)
        return self.store.query(
            embedding,
            top_k=top_k or self.settings.top_k,
            doc_ids=doc_ids,
            min_score=self.settings.min_score if min_score is None else min_score,
        )

    def _to_sources(self, hits: list[dict]) -> list[Source]:
        sources: list[Source] = []
        for index, hit in enumerate(hits, start=1):
            metadata = hit["metadata"]
            text = (hit["text"] or "").strip()
            sources.append(
                Source(
                    index=index,
                    doc_id=metadata.get("doc_id", ""),
                    filename=metadata.get("filename", "unknown"),
                    chunk_index=int(metadata.get("chunk_index", 0)),
                    page=int(metadata["page"]) if metadata.get("page") is not None else None,
                    heading=metadata.get("heading"),
                    score=hit["score"],
                    snippet=text if len(text) <= 600 else text[:600] + "…",
                )
            )
        return sources

    # -------------------------------------------------------------------- chat
    def chat(
        self,
        question: str,
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
        min_score: float | None = None,
        history: list[dict] | None = None,
    ) -> ChatResponse:
        """先查结构化数据，再检索文档，最后交给 LLM 组织语言。

        - 命中结构化数据 → 把事实块作为最高优先级的上下文，并标注来源接口与数据版本；
        - 没命中 → 照旧走 RAG，``mode`` 标 ``documents_only``，提醒「来自文档，可能不是当前数据」；
        - LLM 不可用但命中了结构化数据 → 直接返回事实清单，不让用户空手而归。
        """
        data_version = self._data_version()
        structured = self.structured.lookup(question) if self.structured else None
        hits = self._retrieve(question, top_k, doc_ids, min_score)
        sources = self._to_sources(hits)

        prompt_sources = [s.snippet for s in sources]
        if structured is not None and structured.hit:
            prompt_sources.insert(0, structured.as_prompt_block())

        mode = "documents_only" if not (structured and structured.hit) else "ok"
        try:
            result = self.llm.answer(
                question,
                prompt_sources,
                history,
                data_version=data_version,
            )
            answer = result["answer"]
            usage = result.get("usage")
        except LLMNotConfigured:
            if not (structured and structured.hit):
                raise
            mode = "structured_only"
            answer = self._structured_answer(question, structured, data_version)
            usage = None

        return ChatResponse(
            answer=answer,
            sources=sources,
            model=self.llm.model,
            usage=usage,
            data_version=data_version,
            structured=structured.as_dict() if structured is not None else None,
            mode=mode,
        )

    @staticmethod
    def _structured_answer(question: str, structured: StructuredResult, data_version: str) -> str:
        lines = [
            f"（未配置 DEEPSEEK_API_KEY，下面直接给出本机数据库的查询结果，"
            f"数据版本 {data_version}）",
            "",
            structured.as_prompt_block().split("\n\n\n")[0],
        ]
        if structured.misses:
            lines.append("")
            lines.append("没查到的部分：" + "；".join(structured.misses))
        return "\n".join(lines)

    # --------------------------------------------------------------- documents
    def list_documents(self) -> list[DocumentInfo]:
        return self.registry.list()

    def delete_document(self, doc_id: str) -> bool:
        info = self.registry.get(doc_id)
        if info is None:
            return False
        self.store.delete_document(doc_id)
        self.registry.remove(doc_id)
        if self.settings.keep_uploads:
            path = self.data_dir / "uploads" / f"{doc_id}{info.extension}"
            path.unlink(missing_ok=True)
        logger.info("已删除文档 %s (%s)", info.filename, doc_id)
        return True

    def stats(self) -> dict:
        return {
            "documents": len(self.registry.list()),
            "chunks": self.store.count(),
            "collection": self.settings.collection_name,
            "embedding_model": self.settings.embedding_model,
            "embedding_device": self.settings.embedding_device,
            "llm_model": self.llm.model,
            "llm_configured": self.llm.configured,
            "data_version": self._data_version(),
        }

    def warmup(self) -> None:
        self.embedder.encode_query("warmup")

    def close(self) -> None:
        self.llm.close()
