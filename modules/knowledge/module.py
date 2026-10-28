from __future__ import annotations

from app.kernel.dataversion import DataVersion
from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import KnowledgeService
from .settings import KnowledgeSettings
from .structured import StructuredLookup


def setup(ctx: ModuleContext) -> Module:
    settings = KnowledgeSettings()

    def current_data_version() -> str:
        version: DataVersion = ctx.data_version()
        return version.version if version.known else "未知（尚未导入结构化数据）"

    # 结构化数据优先：模块之间只走 HTTP，地址默认取内核 settings 的 host:port
    base_url = settings.structured_base_url or f"http://{ctx.settings.host}:{ctx.settings.port}"
    structured = StructuredLookup(
        base_url,
        timeout=settings.structured_timeout,
        enabled=settings.structured_enabled,
        data_version=current_data_version(),
    )

    service = KnowledgeService(settings, ctx.data_dir, current_data_version, structured)
    return Module(
        router=build_router(service, settings),
        on_shutdown=service.close,
        status=service.stats,
    )
