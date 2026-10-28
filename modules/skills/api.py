from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from .importer import SkillsSourceError
from .service import SkillsService


def build_router(service: SkillsService) -> APIRouter:
    router = APIRouter(tags=["skills"])

    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.get("/skills")
    def skills(
        q: Annotated[str | None, Query(description="技能名或描述关键词")] = None,
        skill_category: int | None = None,
        rarity: int | None = None,
        with_source_only: Annotated[
            bool, Query(description="只看能反查到马娘或协助卡的技能")
        ] = False,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        return service.search(
            query=q,
            skill_category=skill_category,
            rarity=rarity,
            with_source_only=with_source_only,
            limit=limit,
        )

    @router.get("/skills/{skill_id}")
    def skill(skill_id: int) -> dict:
        item = service.detail(skill_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"没有这个技能：{skill_id}")
        return item

    @router.post("/refresh")
    def refresh(force: bool = False) -> dict:
        try:
            return service.refresh(force=force)
        except SkillsSourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
