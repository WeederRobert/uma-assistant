from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import CatalogService


def setup(ctx: ModuleContext) -> Module:
    service = CatalogService(ctx.db)
    return Module(router=build_router(service), status=service.stats)
