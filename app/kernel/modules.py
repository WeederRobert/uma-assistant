from __future__ import annotations

import importlib
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from .config import Settings
from .database import Database
from .dataversion import DataVersion, read_data_version

logger = logging.getLogger(__name__)


class ModuleError(RuntimeError):
    """模块清单或加载过程不合法。"""


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    id: str
    name: str
    version: str
    description: str = ""
    order: int = 100
    requires: tuple[str, ...] = ()
    api_prefix: str | None = None
    ui_panel: str | None = None
    ui_label: str | None = None
    path: Path = field(default=Path("."), compare=False, repr=False)

    @classmethod
    def from_file(cls, path: Path) -> ModuleManifest:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        section = raw.get("module") or {}
        module_id = section.get("id") or path.parent.name
        if not module_id.isidentifier():
            raise ModuleError(f"模块 id 必须是合法的 Python 标识符: {module_id!r}")
        if module_id != path.parent.name:
            raise ModuleError(f"模块 id ({module_id}) 必须与目录名 ({path.parent.name}) 一致")
        for key in ("name", "version"):
            if not section.get(key):
                raise ModuleError(f"{path}: 缺少 [module].{key}")

        ui = raw.get("ui") or {}
        api = raw.get("api") or {}
        return cls(
            id=module_id,
            name=str(section["name"]),
            version=str(section["version"]),
            description=str(section.get("description", "")),
            order=int(section.get("order", 100)),
            requires=tuple(section.get("requires", ())),
            api_prefix=api.get("prefix") or f"/api/{module_id}",
            ui_panel=ui.get("panel"),
            ui_label=ui.get("label"),
            path=path.parent,
        )


@dataclass(slots=True)
class Module:
    """模块对外暴露的运行时对象。"""

    router: APIRouter | None = None
    on_startup: Callable[[], None] | None = None
    on_shutdown: Callable[[], None] | None = None
    status: Callable[[], dict[str, Any]] | None = None


@dataclass(slots=True)
class ModuleContext:
    """内核交给模块的运行时上下文。

    模块只依赖这里暴露的能力，不直接访问其它模块，保证可以独立开发与测试。
    """

    settings: Settings
    db: Database
    module_id: str
    logger: logging.Logger

    @property
    def data_dir(self) -> Path:
        """模块专属的持久化目录。"""
        return self.settings.module_data_dir(self.module_id)

    def data_version(self) -> DataVersion:
        return read_data_version(self.db)


@dataclass(slots=True)
class LoadedModule:
    manifest: ModuleManifest
    module: Module | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.module is not None and self.error is None


def _topological_order(manifests: list[ModuleManifest]) -> list[ModuleManifest]:
    by_id = {m.id: m for m in manifests}
    resolved: list[ModuleManifest] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(module_id: str, chain: tuple[str, ...]) -> None:
        if module_id in done:
            return
        if module_id in visiting:
            raise ModuleError(f"模块依赖存在循环: {' -> '.join((*chain, module_id))}")
        manifest = by_id.get(module_id)
        if manifest is None:
            raise ModuleError(f"缺少被依赖的模块: {module_id}")
        visiting.add(module_id)
        for dependency in manifest.requires:
            visit(dependency, (*chain, module_id))
        visiting.discard(module_id)
        done.add(module_id)
        resolved.append(manifest)

    for manifest in sorted(manifests, key=lambda m: (m.order, m.id)):
        visit(manifest.id, ())
    return resolved


class ModuleRegistry:
    """负责发现、校验、加载与关闭模块。"""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.loaded: list[LoadedModule] = []
        self._started = False

    # ------------------------------------------------------------------ discover
    def discover(self) -> list[ModuleManifest]:
        root = self.settings.modules_dir
        if not root.exists():
            return []
        manifests: list[ModuleManifest] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            manifest_file = child / "manifest.toml"
            if not manifest_file.exists():
                logger.warning("跳过缺少 manifest.toml 的目录: %s", child.name)
                continue
            try:
                manifests.append(ModuleManifest.from_file(manifest_file))
            except (ModuleError, tomllib.TOMLDecodeError, OSError, ValueError) as exc:
                logger.error("模块清单解析失败 %s: %s", child.name, exc)
        return manifests

    # ---------------------------------------------------------------------- load
    def load_all(self) -> list[LoadedModule]:
        try:
            ordered = _topological_order(self.discover())
        except ModuleError as exc:
            logger.error("模块依赖解析失败: %s", exc)
            raise

        loaded: list[LoadedModule] = []
        for manifest in ordered:
            loaded.append(self._load_one(manifest))
        self.loaded = loaded
        return loaded

    def _load_one(self, manifest: ModuleManifest) -> LoadedModule:
        ctx = ModuleContext(
            settings=self.settings,
            db=self.db,
            module_id=manifest.id,
            logger=logging.getLogger(f"module.{manifest.id}"),
        )
        try:
            package = importlib.import_module(f"modules.{manifest.id}.module")
            module = package.setup(ctx)
            if not isinstance(module, Module):
                raise ModuleError(f"{manifest.id}.module.setup() 必须返回 Module 实例")
        except Exception as exc:  # noqa: BLE001 - 单个模块失败不应拖垮整个应用
            logger.exception("模块加载失败: %s", manifest.id)
            return LoadedModule(manifest=manifest, error=f"{type(exc).__name__}: {exc}")

        logger.info("已加载模块 %s (%s)", manifest.name, manifest.id)
        return LoadedModule(manifest=manifest, module=module)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._started:
            return
        for loaded in self.loaded:
            if not loaded.ok or loaded.module is None or loaded.module.on_startup is None:
                continue
            try:
                loaded.module.on_startup()
            except Exception as exc:  # noqa: BLE001
                logger.exception("模块启动失败: %s", loaded.manifest.id)
                loaded.error = f"启动失败: {type(exc).__name__}: {exc}"
        self._started = True

    def shutdown(self) -> None:
        for loaded in reversed(self.loaded):
            if loaded.module is None or loaded.module.on_shutdown is None:
                continue
            try:
                loaded.module.on_shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("模块关闭失败: %s", loaded.manifest.id)

    # --------------------------------------------------------------------- report
    def describe(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for loaded in self.loaded:
            manifest = loaded.manifest
            entry: dict[str, Any] = {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "status": "ok" if loaded.ok else "error",
                "error": loaded.error,
                "api": manifest.api_prefix,
                "ui": None,
            }
            if manifest.ui_panel:
                entry["ui"] = {
                    "label": manifest.ui_label or manifest.name,
                    "panel": f"/module-assets/{manifest.id}/{manifest.ui_panel}",
                }
            if loaded.ok and loaded.module is not None and loaded.module.status is not None:
                try:
                    entry["details"] = loaded.module.status()
                except Exception as exc:  # noqa: BLE001
                    entry["status"] = "error"
                    entry["error"] = f"状态查询失败: {type(exc).__name__}: {exc}"
            items.append(entry)
        return items
