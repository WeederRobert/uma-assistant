from __future__ import annotations

import platform
import sys
import time
from typing import Any

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

    @router.post("/refresh")
    def refresh() -> dict:
        """一键刷新：让每个模块自己决定怎么刷新（mdb 重新导入 / 抓 BWiki）。

        顺序按模块注册顺序，`sync` 先跑（它会重导入 catalog 表并更新数据版本）；
        单个模块失败只记录在结果里，不影响其余模块。
        """
        started = time.monotonic()
        results: list[dict[str, Any]] = []
        for loaded in registry.loaded:
            module = loaded.module
            if not loaded.ok or module is None or module.refresh is None:
                continue
            manifest = loaded.manifest
            began = time.monotonic()
            try:
                payload = module.refresh()
            except Exception as exc:  # noqa: BLE001 - 单个模块失败不该拖垮整次刷新
                results.append(
                    {
                        "id": manifest.id,
                        "name": manifest.name,
                        "ok": False,
                        "duration_ms": int((time.monotonic() - began) * 1000),
                        "summary": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            else:
                results.append(
                    {
                        "id": manifest.id,
                        "name": manifest.name,
                        "ok": True,
                        "duration_ms": int((time.monotonic() - began) * 1000),
                        "summary": _format_summary(payload),
                        "error": None,
                    }
                )
        # sync 负责 mdb → catalog_* 与数据版本，先执行更符合直觉；其余保持注册顺序
        results.sort(key=lambda row: row["id"] != "sync")
        data = read_data_version(registry.db)
        return {
            "ok": all(row["ok"] for row in results),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "results": results,
            "data": data.as_dict(),
        }

    return router


# 各模块 refresh() 返回值里常见的键，翻成中文好放在概览的「一键刷新」结果里
SUMMARY_LABELS = {
    "skills": "技能",
    "slots": "槽位",
    "effects": "效果",
    "characters": "马娘来源",
    "support_cards": "协助卡来源",
    "upgrades": "进化",
    "relations": "关系组",
    "members": "成员",
    "ranks": "等级档",
    "charas": "角色",
    "banners": "卡池",
    "pickups": "精选",
    "upcoming": "未来卡池",
    "with_pickups": "有精选",
    "confirmed": "已确认",
    "predicted": "预测",
    "overlap": "重叠",
    "events": "活动",
    "added": "新增",
    "changed": "变更",
    "removed": "移除",
    "races": "赛事",
    "courses": "赛道",
    "conditions": "场地状态",
    "champions": "大赛",
    "pulls": "抽数",
    "items": "条目",
}
# 这几个键是来源元信息，放进一行摘要只会喧宾夺主
SUMMARY_SKIP = {"data_version", "source", "mdb_path", "imported_at", "skipped", "missing_columns"}


def _format_counts(values: dict[str, Any]) -> str:
    parts = [
        f"{SUMMARY_LABELS.get(key, key)} {value}"
        for key, value in values.items()
        if key not in SUMMARY_SKIP
        and isinstance(value, (int, float, str))
        and value not in ("", None)
    ]
    return " · ".join(parts[:6])


def _format_summary(result: Any) -> str:
    """把各模块 refresh() 的返回值压成一行给人看的话。"""
    if not isinstance(result, dict):
        return ""
    summary = result.get("summary")
    if isinstance(summary, dict) and summary:
        text = _format_counts(summary)
        if text:
            return text
    for key in ("counts", "totals"):
        values = result.get(key)
        if isinstance(values, dict) and values:
            text = _format_counts(values)
            if text:
                return text
    catalog = result.get("catalog")
    if isinstance(catalog, dict) and catalog:
        text = _format_counts(catalog)
        geometry = result.get("geometry")
        if text and isinstance(geometry, dict) and isinstance(geometry.get("courses"), int):
            text += f" · 几何 {geometry['courses']}"
        if text:
            return text
    for key in ("reason", "message", "note"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return ""
