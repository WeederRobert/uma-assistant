from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .importer import GachaSourceError
from .service import GachaService


def build_router(service: GachaService) -> APIRouter:
    router = APIRouter(tags=["gacha"])

    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.get("/banners")
    def banners(
        include_ended: bool = Query(default=True, description="是否返回已结束的历史卡池"),
        limit_ended: int = Query(default=20, ge=0, le=200),
    ) -> dict:
        return service.banners(include_ended=include_ended, limit_ended=limit_ended)

    @router.get("/banners/{gacha_id}")
    def banner(gacha_id: int) -> dict:
        item = service.banner(gacha_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"没有这个卡池：{gacha_id}")
        return item

    @router.post("/refresh")
    def refresh(force: bool = False) -> dict:
        try:
            return service.refresh(force=force)
        except GachaSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
