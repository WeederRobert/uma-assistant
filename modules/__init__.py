"""业务模块目录。

每个子目录是一个独立模块，包含：

- ``manifest.toml``  元数据（id / 名称 / 版本 / 依赖 / API 前缀 / UI 面板）
- ``module.py``      ``setup(ctx) -> Module`` 工厂
- ``api.py``         FastAPI 路由（可选）
- ``ui/panel.js``    前端面板（可选）

以 ``_`` 开头的目录不会被加载（用于模板与草稿）。
"""

from __future__ import annotations
