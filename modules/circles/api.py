from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from .client import CirclesSourceError
from .service import CirclesNotFound, CirclesService


def build_router(service: CirclesService) -> APIRouter:
    router = APIRouter(tags=["circles"])

    # /status 与 /refresh 必须声明在 /{circle_id} 之前，否则会被当成社团 id
    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.post("/refresh")
    def refresh() -> dict:
        try:
            return service.refresh()
        except CirclesSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("")
    def circles(
        q: Annotated[str | None, Query(description="社团名或社团 ID 关键词")] = None,
        sort: Literal["month", "today", "rank"] = "month",
        limit: int = Query(default=300, ge=1, le=500),
    ) -> dict:
        return service.list_circles(query=q, sort=sort, limit=limit)

    @router.get("/{circle_id}")
    def circle(circle_id: int, refresh: bool = False) -> dict:
        try:
            return service.circle_detail(circle_id, refresh=refresh)
        except CirclesNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CirclesSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
