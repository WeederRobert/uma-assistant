"""卡池模块：从 master.mdb 读卡池排期（含未来），给出「下一期是什么」。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import GachaService
from .settings import GachaSettings


def setup(ctx: ModuleContext) -> Module:
    options = GachaSettings()
    service = GachaService(ctx.db, ctx.settings, options, logger=ctx.logger)
    summary = service.ensure_catalog()
    if summary is not None:
        ctx.logger.info(
            "启动时补导入卡池排期：%d 个卡池（未来 %d）", summary.banners, summary.upcoming
        )
    ctx.logger.info("卡池模块就绪")
    return Module(router=build_router(service), status=service.status)
