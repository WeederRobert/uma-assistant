from __future__ import annotations

from fastapi import APIRouter

from app.kernel.modules import ModuleContext


def build_router(ctx: ModuleContext) -> APIRouter:
    router = APIRouter(tags=["example"])

    @router.get("/ping")
    def ping() -> dict:
        version = ctx.data_version()
        return {"pong": True, "module": ctx.module_id, "data_version": version.version}

    return router
