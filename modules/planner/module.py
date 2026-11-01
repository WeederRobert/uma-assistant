"""规划模块：千里眼（近半年预测）与萝卜规划。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import PlannerService
from .settings import PlannerSettings


def setup(ctx: ModuleContext) -> Module:
    options = PlannerSettings()
    service = PlannerService(ctx.db, ctx.settings, options, ctx.data_dir, logger_=ctx.logger)
    summary = service.ensure_catalog()
    if summary is not None:
        ctx.logger.info("启动时导入已确认卡池排期：%d 个（未来 %d）", summary.pools, summary.future)
    counts = service.status()["counts"]
    ctx.logger.info(
        "规划模块就绪 | 已确认=%d 预测=%d 重叠=%d 活动=%d | 窗口 %d 天",
        counts["confirmed"],
        counts["predicted"],
        counts["overlap"],
        counts["events"],
        options.horizon_days,
    )
    return Module(
        router=build_router(service),
        on_shutdown=service.close,
        status=service.status,
        refresh=lambda: service.refresh(force=True),
    )
