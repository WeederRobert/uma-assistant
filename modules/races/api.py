from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from .models import RefreshRequest
from .service import RacesService

logger = logging.getLogger(__name__)


def build_router(service: RacesService) -> APIRouter:
    router = APIRouter(tags=["races"])

    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.get("/courses")
    def courses(
        venue: str | None = None,
        surface: str | None = None,
        direction: str | None = None,
        distance_min: int | None = Query(default=None, ge=800, le=4000),
        distance_max: int | None = Query(default=None, ge=800, le=4000),
        geometry_only: bool = False,
    ) -> dict:
        return service.courses(
            venue=venue,
            surface=surface,
            direction=direction,
            distance_min=distance_min,
            distance_max=distance_max,
            geometry_only=geometry_only,
        )

    @router.get("/courses/{course_id}")
    def course(course_id: str) -> dict:
        item = service.course(course_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"没有这条赛道：{course_id}")
        return item

    @router.get("/races")
    def races(
        q: Annotated[str | None, Query(description="赛事名关键词")] = None,
        venue: str | None = None,
        surface: str | None = None,
        direction: str | None = None,
        grade: int | None = None,
        official_only: bool = False,
        distance_min: int | None = Query(default=None, ge=800, le=4000),
        distance_max: int | None = Query(default=None, ge=800, le=4000),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        return service.races(
            query=q,
            venue=venue,
            surface=surface,
            direction=direction,
            grade=grade,
            official_only=official_only,
            distance_min=distance_min,
            distance_max=distance_max,
            limit=limit,
        )

    @router.get("/races/{race_id}")
    def race(race_id: int) -> dict:
        item = service.race(race_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"没有这场比赛：{race_id}")
        return item

    @router.get("/conditions")
    def conditions(area: int | None = None, season: int | None = None) -> dict:
        return service.conditions(area=area, season=season)

    @router.get("/champions")
    def champions(upcoming: bool = False) -> dict:
        return service.champions(upcoming=upcoming)

    @router.get("/champions/{champions_id}")
    def champion(champions_id: int) -> dict:
        item = service.champion(champions_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"没有这届大赛：{champions_id}")
        return item

    @router.post("/refresh")
    def refresh(payload: RefreshRequest | None = None) -> dict:
        body = payload or RefreshRequest()
        result = service.refresh(force=body.force, mdb_path=body.mdb_path)
        if result["catalog"] is None and result["error"]:
            raise HTTPException(status_code=422, detail=result["error"])
        return result

    return router
