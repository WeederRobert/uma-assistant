"""技能模块：条件、效果参数、来源反查与进化关系。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router
from .service import SkillsService
from .settings import SkillsSettings


def setup(ctx: ModuleContext) -> Module:
    options = SkillsSettings()
    service = SkillsService(ctx.db, ctx.settings, options, logger=ctx.logger)
    summary = service.ensure_catalog()
    if summary is not None:
        ctx.logger.info(
            "启动时补导入技能数据：%d 个技能 / %d 条效果", summary.skills, summary.effects
        )
    ctx.logger.info("技能模块就绪")
    return Module(router=build_router(service), status=service.status)
