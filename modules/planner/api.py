from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from . import store
from .models import PlanRequest, RefreshRequest
from .service import PlannerService


def build_router(service: PlannerService) -> APIRouter:
    router = APIRouter(tags=["planner"])

    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.get("/forecast")
    def forecast(
        days: Annotated[int | None, Query(ge=1, le=365, description="预测窗口天数")] = None,
        include_overlap: Annotated[
            bool, Query(description="是否包含与 mdb 已确认段重叠的预测行（仅用于对照）")
        ] = False,
    ) -> dict:
        return service.forecast(days=days, include_overlap=include_overlap)

    @router.post("/refresh")
    def refresh(payload: RefreshRequest) -> dict:
        return service.refresh(force=payload.force)

    # ------------------------------------------------------------------ plans
    @router.get("/plans")
    def plans() -> dict:
        return {"plans": service.plans()}

    @router.post("/plans")
    def create_plan(payload: PlanRequest) -> dict:
        data = payload.model_dump()
        plan_id = store.create_plan(
            service.db,
            name=data["name"],
            carats=data["carats"],
            tickets=data["tickets"],
            monthly_income=data["monthly_income"],
            note=data["note"],
            targets=data["targets"],
        )
        return {"id": plan_id}

    @router.get("/plans/{plan_id}")
    def plan(plan_id: int) -> dict:
        found = service.plan(plan_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"没有这个方案：{plan_id}")
        return {"plan": found, "evaluation": service.evaluate(found)}

    @router.put("/plans/{plan_id}")
    def update_plan(plan_id: int, payload: PlanRequest) -> dict:
        data = payload.model_dump()
        updated = store.update_plan(
            service.db,
            plan_id,
            name=data["name"],
            carats=data["carats"],
            tickets=data["tickets"],
            monthly_income=data["monthly_income"],
            note=data["note"],
            targets=data["targets"],
        )
        if not updated:
            raise HTTPException(status_code=404, detail=f"没有这个方案：{plan_id}")
        return {"id": plan_id}

    @router.delete("/plans/{plan_id}")
    def delete_plan(plan_id: int) -> dict:
        if not store.delete_plan(service.db, plan_id):
            raise HTTPException(status_code=404, detail=f"没有这个方案：{plan_id}")
        return {"deleted": True}

    @router.post("/evaluate")
    def evaluate(payload: PlanRequest) -> dict:
        return service.evaluate(payload.model_dump())

    return router
