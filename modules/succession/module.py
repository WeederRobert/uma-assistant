"""相性模块：共享关系组 → 点数合计 → 相性等级。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import SuccessionService
from .settings import SuccessionSettings


def setup(ctx: ModuleContext) -> Module:
    options = SuccessionSettings()
    service = SuccessionService(ctx.db, ctx.settings, options, logger=ctx.logger)
    summary = service.ensure_catalog()
    if summary is not None:
        ctx.logger.info(
            "启动时补导入相性关系：%d 个关系组 / %d 条成员",
            summary.relations,
            summary.members,
        )
    ctx.logger.info("相性模块就绪")
    return Module(router=build_router(service), status=service.status)
