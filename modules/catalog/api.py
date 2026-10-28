from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .service import KINDS, CatalogService


def build_router(service: CatalogService) -> APIRouter:
    router = APIRouter(tags=["catalog"])

    @router.get("/stats")
    def stats() -> dict:
        return service.stats()

    @router.get("/search")
    def search(
        q: str = Query(min_length=1, description="马娘 / 协助卡 / 技能名称，支持部分匹配"),
        kind: str | None = Query(default=None, description="character / support_card / skill"),
        limit: int = Query(default=30, ge=1, le=100),
    ) -> dict:
        if kind is not None and kind not in KINDS:
            raise HTTPException(status_code=422, detail=f"kind 必须是 {KINDS} 之一")
        return {"query": q, "items": service.search(q, kind=kind, limit=limit)}

    @router.get("/items/{kind}/{item_id}")
    def item(kind: str, item_id: int) -> dict:
        if kind not in KINDS:
            raise HTTPException(status_code=422, detail=f"kind 必须是 {KINDS} 之一")
        found = service.get(kind, item_id)
        if found is None:
            raise HTTPException(status_code=404, detail="未找到该条目")
        return found

    return router
