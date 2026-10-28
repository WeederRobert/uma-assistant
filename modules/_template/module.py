"""模块工厂：内核通过 ``setup(ctx)`` 拿到路由与生命周期钩子。"""

from __future__ import annotations

from app.kernel.modules import Module, ModuleContext

from .api import build_router


def setup(ctx: ModuleContext) -> Module:
    ctx.logger.info("示例模块已加载，数据目录：%s", ctx.data_dir)
    return Module(router=build_router(ctx))
