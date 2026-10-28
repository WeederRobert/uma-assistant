"""活动 / 卡池模块：从 BWiki 分类页抓取时效信息，按服区分并保留来源。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import EventsService
from .settings import EventsSettings


def setup(ctx: ModuleContext) -> Module:
    options = EventsSettings()
    service = EventsService(ctx.db, options, ctx.data_dir, logger_=ctx.logger)
    ctx.logger.info(
        "活动模块就绪 | 接口=%s | 展示服=%s | 间隔=%.1fs",
        options.api_url,
        ",".join(options.server_list) or "全部",
        options.interval_seconds,
    )
    return Module(
        router=build_router(service),
        on_shutdown=service.close,
        status=service.status,
    )
