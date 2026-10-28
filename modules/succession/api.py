from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from .importer import SuccessionSourceError
from .service import SuccessionService


def build_router(service: SuccessionService) -> APIRouter:
    router = APIRouter(tags=["succession"])

    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.get("/charas")
    def charas(
        q: Annotated[str | None, Query(description="按名字过滤")] = None,
        limit: int = Query(default=500, ge=1, le=500),
    ) -> dict:
        return service.charas(q, limit)

    @router.get("/pairs")
    def pairs(a: int, b: int) -> dict:
        if a == b:
            raise HTTPException(
                status_code=422,
                detail="两只马娘不能是同一角色（衣装不同也算同一角色），换一个再算。",
            )
        try:
            return service.pair(a, b)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc

    @router.get("/layout")
    def layout(
        target: int,
        parent1: int,
        parent2: int,
        g1: int | None = None,
        g2: int | None = None,
        g3: int | None = None,
        g4: int | None = None,
    ) -> dict:
        grandparents = [item for item in (g1, g2, g3, g4) if item is not None]
        try:
            return service.layout(
                {
                    "target": target,
                    "parents": [parent1, parent2],
                    "grandparents": grandparents,
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc

    @router.post("/refresh")
    def refresh(force: bool = False) -> dict:
        try:
            return service.refresh(force=force)
        except SuccessionSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
