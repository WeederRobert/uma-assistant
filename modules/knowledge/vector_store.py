from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import Chunk

logger = logging.getLogger(__name__)


class VectorStore:
    """基于 Chroma 的本地持久化向量库（余弦相似度）。

    ``chromadb`` 是可选组件（精简安装包不含），所以客户端延迟到第一次用到时才导入：
    缺组件时知识库模块仍能加载，「结构化数据优先」的回答路径照常工作。
    """

    def __init__(self, persist_dir: Path, collection_name: str) -> None:
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._lock = threading.Lock()

    def _ensure_collection(self):
        if self._collection is None:
            with self._lock:
                if self._collection is None:
                    import chromadb
                    from chromadb.config import Settings as ChromaSettings

                    self.persist_dir.mkdir(parents=True, exist_ok=True)
                    self._client = chromadb.PersistentClient(
                        path=str(self.persist_dir),
                        settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
                    )
                    self._collection = self._client.get_or_create_collection(
                        name=self.collection_name,
                        metadata={"hnsw:space": "cosine"},
                    )
        return self._collection

    @property
    def collection(self):
        return self._ensure_collection()

    def add_document(
        self,
        doc_id: str,
        filename: str,
        chunks: Iterable[Chunk],
        embeddings: list[list[float]],
    ) -> int:
        chunks = list(chunks)
        if not chunks:
            return 0
        if len(chunks) != len(embeddings):
            raise ValueError("chunks 与 embeddings 数量不一致")

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for chunk in chunks:
            ids.append(f"{doc_id}:{chunk.index}")
            documents.append(chunk.text)
            metadata: dict[str, Any] = {
                "doc_id": doc_id,
                "filename": filename,
                "chunk_index": chunk.index,
            }
            if chunk.page is not None:
                metadata["page"] = chunk.page
            if chunk.heading:
                metadata["heading"] = chunk.heading
            metadatas.append(metadata)

        # Chroma 单批写入上限较保守，分批提交
        batch_size = 256
        for start in range(0, len(ids), batch_size):
            stop = start + batch_size
            self.collection.add(
                ids=ids[start:stop],
                documents=documents[start:stop],
                metadatas=metadatas[start:stop],
                embeddings=embeddings[start:stop],
            )
        return len(ids)

    def query(
        self,
        embedding: list[float],
        top_k: int = 6,
        doc_ids: list[str] | None = None,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        where = None
        if doc_ids:
            where = {"doc_id": {"$in": doc_ids}} if len(doc_ids) > 1 else {"doc_id": doc_ids[0]}

        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=max(1, top_k),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        hits: list[dict[str, Any]] = []
        for index, chunk_id in enumerate(ids):
            distance = distances[index] if index < len(distances) else 1.0
            score = 1.0 - float(distance)
            if score < min_score:
                continue
            metadata = metadatas[index] or {}
            hits.append(
                {
                    "id": chunk_id,
                    "text": documents[index] if index < len(documents) else "",
                    "metadata": metadata,
                    "score": round(score, 6),
                }
            )
        return hits

    def delete_document(self, doc_id: str) -> None:
        self.collection.delete(where={"doc_id": doc_id})

    def count(self) -> int:
        try:
            return int(self.collection.count())
        except Exception:  # pragma: no cover - chroma 内部异常
            logger.debug("count() 失败", exc_info=True)
            return 0

    def reset(self) -> None:
        collection = self._ensure_collection()
        name = collection.name
        self._client.delete_collection(name)
        self._collection = self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
