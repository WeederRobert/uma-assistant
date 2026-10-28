from __future__ import annotations

import platform
import sys

from fastapi import APIRouter

from .. import __version__
from .config import Settings
from .dataversion import read_data_version
from .modules import ModuleRegistry


def build_system_router(registry: ModuleRegistry, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["system"])

    @router.get("/info")
    def info() -> dict:
        return {
            "app": {
                "name": settings.app_name,
                "version": __version__,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "executable": sys.executable,
            },
            "data": read_data_version(registry.db).as_dict(),
            "modules": registry.describe(),
        }

    @router.get("/modules")
    def modules() -> list[dict]:
        return registry.describe()

    return router
