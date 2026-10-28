from __future__ import annotations

import platform
import sys

from fastapi import APIRouter

from .. import __version__
from .config import Settings, is_frozen
from .dataversion import read_data_version
from .modules import ModuleRegistry


def build_system_router(registry: ModuleRegistry, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["system"])

    def _setup(data_known: bool) -> dict:
        """首次使用引导所需的事实。

        只回显「有没有数据 / 能不能在界面里导入 / 哪些模块降级」，具体文案由前端决定。
        """
        modules = registry.describe()
        return {
            "first_run": not data_known,
            "mdb_present": settings.mdb_path.is_file(),
            "can_import_via_ui": any(
                module["id"] == "sync" and module["status"] == "ok" for module in modules
            ),
            "degraded_modules": [
                {"id": module["id"], "name": module["name"], "error": module.get("error")}
                for module in modules
                if module["status"] != "ok"
            ],
        }

    @router.get("/info")
    def info() -> dict:
        data = read_data_version(registry.db)
        return {
            "app": {
                "name": settings.app_name,
                "version": __version__,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "executable": sys.executable,
                "frozen": is_frozen(),
            },
            "paths": {
                "data": str(settings.data_dir),
                "logs": str(settings.log_dir),
                "mdb": str(settings.mdb_path),
                "tmp": str(settings.tmp_dir),
            },
            "data": data.as_dict(),
            "setup": _setup(data.known),
            "modules": registry.describe(),
        }

    @router.get("/modules")
    def modules() -> list[dict]:
        return registry.describe()

    return router
