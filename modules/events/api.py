from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from .models import RefreshRequest
from .service import EventsService

logger = logging.getLogger(__name__)


def build_router(service: EventsService) -> APIRouter:
    router = APIRouter(tags=["events"])

    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.get("/schedule")
    def schedule(
        servers: Annotated[str | None, Query(description="逗号分隔；目前只有 cn")] = None,
        kinds: Annotated[str | None, Query(description="逗号分隔：pool,event")] = None,
        days_before: int | None = Query(default=None, ge=0, le=365),
        days_after: int | None = Query(default=None, ge=0, le=365),
        include_placeholders: Annotated[
            bool, Query(description="是否显示来源里的占位行（默认隐藏）")
        ] = False,
    ) -> dict:
        return service.schedule(
            servers=_split(servers),
            kinds=_split(kinds),
            days_before=days_before,
            days_after=days_after,
            include_placeholders=include_placeholders,
        )

    @router.get("/history")
    def history(limit: int = Query(default=50, ge=1, le=200)) -> dict:
        return service.history(limit)

    @router.post("/refresh")
    def refresh(payload: RefreshRequest) -> dict:
        result = service.refresh(payload.sources, force=payload.force)
        if not result["results"]:
            raise HTTPException(status_code=422, detail=result.get("reason", "没有可抓取的来源"))
        return result

    return router


def _split(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or None
