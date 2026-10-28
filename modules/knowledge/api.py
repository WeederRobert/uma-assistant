from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from .llm import LLMError, LLMNotConfigured
from .loaders import DocumentParseError, UnsupportedFileType
from .models import (
    ChatRequest,
    ChatResponse,
    DocumentInfo,
    IngestResponse,
    SearchRequest,
    SearchResponse,
)
from .service import KnowledgeService
from .settings import KnowledgeSettings

logger = logging.getLogger(__name__)


def build_router(service: KnowledgeService, settings: KnowledgeSettings) -> APIRouter:
    router = APIRouter(tags=["knowledge"])

    @router.get("/health")
    def health() -> dict:
        return {"status": "ok", **service.stats()}

    @router.get("/stats")
    def stats() -> dict:
        return service.stats()

    @router.get("/documents", response_model=list[DocumentInfo])
    def list_documents() -> list[DocumentInfo]:
        return service.list_documents()

    @router.post("/documents", response_model=IngestResponse)
    async def upload_documents(files: Annotated[list[UploadFile], File()]) -> IngestResponse:
        max_bytes = settings.max_upload_mb * 1024 * 1024
        ingested: list[DocumentInfo] = []
        skipped: list[dict] = []

        for upload in files:
            filename = upload.filename or "unnamed"
            try:
                data = await upload.read()
            finally:
                await upload.close()

            if not data:
                skipped.append({"filename": filename, "reason": "文件为空"})
                continue
            if len(data) > max_bytes:
                skipped.append(
                    {"filename": filename, "reason": f"超过大小限制 {settings.max_upload_mb} MB"}
                )
                continue

            try:
                ingested.append(service.ingest_bytes(data, filename))
            except (UnsupportedFileType, DocumentParseError, ValueError) as exc:
                logger.warning("导入失败 %s: %s", filename, exc)
                skipped.append({"filename": filename, "reason": str(exc)})

        if not ingested and skipped:
            raise HTTPException(
                status_code=422,
                detail={"message": "全部文件导入失败", "skipped": skipped},
            )
        return IngestResponse(documents=ingested, skipped=skipped)

    @router.delete("/documents/{doc_id}")
    def delete_document(doc_id: str) -> dict:
        if not service.delete_document(doc_id):
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
        return {"deleted": doc_id}

    @router.post("/search", response_model=SearchResponse)
    def search(payload: SearchRequest) -> SearchResponse:
        return service.search(
            payload.query,
            top_k=payload.top_k,
            doc_ids=payload.doc_ids,
            min_score=payload.min_score,
        )

    @router.post("/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest) -> ChatResponse:
        try:
            return service.chat(
                payload.question,
                top_k=payload.top_k,
                doc_ids=payload.doc_ids,
                min_score=payload.min_score,
                history=payload.history,
            )
        except LLMNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router
