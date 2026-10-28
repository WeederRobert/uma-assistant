"""赛道模块：赛事目录（master.mdb）+ 赛道几何（BWiki 数据页）。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import RacesService
from .settings import RacesSettings


def setup(ctx: ModuleContext) -> Module:
    options = RacesSettings()
    service = RacesService(ctx.db, ctx.settings, options, ctx.data_dir, logger=ctx.logger)
    summary = service.ensure_catalog()
    if summary is not None:
        ctx.logger.info("启动时补导入赛事目录：%d 场 / %d 条赛道", summary.races, summary.courses)
    ctx.logger.info("赛道模块就绪 | 数据页=%s", options.geometry_page)
    return Module(
        router=build_router(service),
        on_shutdown=service.close,
        status=service.status,
    )
