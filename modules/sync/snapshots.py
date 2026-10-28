"""把每次导入的版本信息留档到 ``data/snapshots/<version>.json``。

一个快照回答四个问题：这版数据是什么、从哪来、什么时候导的、各表多少条。
文件名用数据版本号，重复导入同一版本会覆盖（版本号本身表示同一份数据）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")


def snapshot_path(directory: Path, version: str) -> Path:
    safe = _SAFE_NAME.sub("-", version).strip("-") or "unknown"
    return directory / f"{safe}.json"


def write_snapshot(directory: Path, record: dict[str, Any]) -> dict[str, Any]:
    """写一个快照文件并返回落盘后的内容。

    同一版本重复导入会合并而不是覆盖：新字段优先，但保留首次导入时间与
    之前记录过的来源细节（例如先由设备提取、后来用 CLI 强制重导）。
    """
    payload = dict(record)
    payload.setdefault("imported_at", datetime.now(UTC).isoformat(timespec="seconds"))
    directory.mkdir(parents=True, exist_ok=True)
    target = snapshot_path(directory, str(payload.get("version") or "unknown"))

    existing = read_snapshot(target) if target.is_file() else None
    if existing:
        merged = {**existing, **{key: value for key, value in payload.items() if value is not None}}
        merged["first_imported_at"] = existing.get("first_imported_at") or existing.get(
            "imported_at"
        )
        payload = merged

    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("已记录数据快照 %s", target.name)
    return payload


def read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("快照文件无法解析，已跳过 %s：%s", path.name, exc)
        return None
    return data if isinstance(data, dict) else None


def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
    # 同一秒内导入两次时 imported_at 相同，用版本号做稳定的次级排序
    return (str(item.get("imported_at") or ""), str(item.get("version") or ""))


def list_snapshots(directory: Path, limit: int = 50) -> list[dict[str, Any]]:
    """按导入时间倒序返回快照，最新的在前。"""
    if not directory.is_dir():
        return []
    items = [data for path in directory.glob("*.json") if (data := read_snapshot(path))]
    items.sort(key=_sort_key, reverse=True)
    return items[: max(1, limit)]


def count_snapshots(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for _ in directory.glob("*.json"))


def latest_snapshot(directory: Path) -> dict[str, Any] | None:
    items = list_snapshots(directory, limit=1)
    return items[0] if items else None
