"""应用装配：内核 + 模块 + 静态前端。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .kernel.api import build_system_router
from .kernel.config import Settings, get_settings
from .kernel.database import Database
from .kernel.dataversion import read_data_version
from .kernel.modules import ModuleRegistry

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()

    db = Database(settings.db_path)
    registry = ModuleRegistry(settings, db)
    registry.load_all()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        registry.start()
        logger.info(
            "启动完成 | 数据版本=%s | 模块=%d",
            read_data_version(db).version,
            len(registry.loaded),
        )
        try:
            yield
        finally:
            registry.shutdown()
            db.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.registry = registry

    @app.middleware("http")
    async def no_cache_for_assets(request: Request, call_next):
        # 本地应用：升级或改模块后不应被浏览器缓存挡住，静态资源一律不缓存
        response = await call_next(request)
        if request.url.path.startswith(("/static/", "/module-assets/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(build_system_router(registry, settings))

    for loaded in registry.loaded:
        manifest = loaded.manifest
        if loaded.ok and loaded.module is not None and loaded.module.router is not None:
            app.include_router(
                loaded.module.router,
                prefix=manifest.api_prefix or f"/api/{manifest.id}",
            )
        ui_dir = manifest.path / "ui"
        if manifest.ui_panel and ui_dir.is_dir():
            app.mount(
                f"/module-assets/{manifest.id}",
                StaticFiles(directory=ui_dir),
                name=f"assets-{manifest.id}",
            )

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "modules": [m["id"] for m in registry.describe() if m["status"] == "ok"],
            "data_version": read_data_version(db).version,
        }

    if settings.static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(settings.static_dir / "index.html")

    return app


app = create_app()
