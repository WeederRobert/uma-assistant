"""社团模块：第三方公开接口 → 社团月增粉丝与成员贡献占比（实验性）。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import CirclesService
from .settings import CirclesSettings


def setup(ctx: ModuleContext) -> Module:
    options = CirclesSettings()
    service = CirclesService(ctx.db, options, logger_=ctx.logger)
    # 不在启动时联网：没有网络也要能起来，数据由用户点「拉取社团月榜」触发
    ctx.logger.info(
        "社团模块就绪（实验性，未自动拉取）| 来源=%s | 成员明细缓存=%.0f 分钟",
        options.base_url,
        options.member_cache_minutes,
    )
    return Module(
        router=build_router(service),
        on_shutdown=service.close,
        status=service.status,
    )
