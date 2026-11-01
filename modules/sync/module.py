"""数据同步模块：从本机客户端或用户选定的文件更新结构化数据。"""

from __future__ import annotations

from app.kernel.dataversion import read_data_version
from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import SyncService
from .settings import SyncSettings


def setup(ctx: ModuleContext) -> Module:
    options = SyncSettings()
    service = SyncService(ctx.db, ctx.settings, options, ctx.logger)
    ctx.logger.info("同步模块就绪 | 快照目录=%s | 包名=%s", service.snapshot_dir, options.package)

    def refresh() -> dict:
        """「一键刷新」里的第一步：留档的 master.mdb 变了就重导入 catalog_* 与数据版本。

        沿用当前记录的数据版本号与游戏版本，避免刷新把版本标签刷成时间戳；
        文件没变时 `apply_path` 自己会按 sha256 跳过。
        """
        kept = service.kept_mdb
        if not kept.is_file():
            return {"imported": False, "reason": "没有 master.mdb，跳过"}
        current = read_data_version(ctx.db)
        return service.apply_path(
            kept,
            game_version=current.game_version,
            data_version=current.version if current.known else None,
            via="refresh",
        )

    return Module(router=build_router(service), status=service.status, refresh=refresh)
