"""数据同步模块：从本机客户端或用户选定的文件更新结构化数据。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import SyncService
from .settings import SyncSettings


def setup(ctx: ModuleContext) -> Module:
    options = SyncSettings()
    service = SyncService(ctx.db, ctx.settings, options, ctx.logger)
    ctx.logger.info("同步模块就绪 | 快照目录=%s | 包名=%s", service.snapshot_dir, options.package)
    return Module(router=build_router(service), status=service.status)
