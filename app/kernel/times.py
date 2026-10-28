"""时区与时间文本：全应用统一按运行环境的本地时区显示。

各模块都要展示时间（日程开池、大赛窗口），但模块之间不能互相 import，
所以这套口径放在 kernel 里共用。两条硬约束：

- 文本一律用 :func:`zone_label` 生成 ``UTC+08:00``，不要用 ``tzname()``
  （中文系统会返回「中国标准时间」这类本地化文本，前端不好对齐）
- 前端解析 ISO 字符串必须走 ``new Date()``，不要对字符串做 ``slice``
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from functools import lru_cache


@lru_cache(maxsize=1)
def system_zone() -> tzinfo:
    """运行环境的本地时区。"""
    return datetime.now().astimezone().tzinfo or UTC


def offset_seconds(zone: tzinfo) -> int | None:
    try:
        offset = zone.utcoffset(None)
    except (ValueError, OverflowError):  # pragma: no cover - 极端 tzinfo 实现
        return None
    return None if offset is None else int(offset.total_seconds())


def zone_label(zone: tzinfo) -> str:
    """把时区变成 ``UTC+08:00`` 这类稳定文本，避免依赖系统语言。"""
    total = offset_seconds(zone)
    if total is None:  # pragma: no cover - 极端 tzinfo 实现
        return "本地时区"
    if total == 0:
        return "UTC"
    sign = "+" if total > 0 else "-"
    hours, minutes = divmod(abs(total) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def local_iso(stamp: int | float | None) -> str | None:
    """unix 秒 → 本地时区 ISO 文本（带偏移量），前端用 ``new Date()`` 解析。"""
    if stamp is None:
        return None
    return datetime.fromtimestamp(int(stamp), tz=system_zone()).isoformat(timespec="seconds")
