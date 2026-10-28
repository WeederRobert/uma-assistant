"""来源声明与解析：把 SMW 查询结果转成统一的时间轴条目。

BWiki 同一个 wiki 同时维护日服、繁中服、简中服页面，本项目**只跟简中服**：

===============  ==================  ======
分类              内容                 服
===============  ==================  ======
简中赛马娘卡池      育成（马娘）卡池       简中服
简中支援卡卡池      协助卡卡池           简中服
===============  ==================  ======

繁中服与日服的卡池、活动分类已于 2026-10-27 按需求移除：跨服进度与简中服不同步，
留着只会让人误以为是简中服的日程。要恢复只需往 :data:`SOURCES` 里加回条目。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

CST = timezone(timedelta(hours=8), "CST")  # 简中服（UTC+8）

SERVER_LABELS = {"cn": "简中服"}
SERVER_ZONES = {"cn": CST}

KIND_LABELS = {"pool": "卡池", "event": "活动"}

# 可信级别：与 docs/kb/README.md 的定义一致，D 表示来源里的占位或不可校对数据
LEVEL_LABELS = {
    "A": "官方原文",
    "B": "权威二手（分类页结构化字段）",
    "C": "社区整理，可能已过期",
    "D": "占位或来源不明",
}

_PLACEHOLDER = re.compile(r"占位|待补充|敬请期待")
_PERMANENT = {"常驻", "永久", "长期"}


@dataclass(frozen=True, slots=True)
class Source:
    key: str
    label: str
    category: str
    kind: str
    server: str
    fields: tuple[str, ...]
    id_fields: tuple[str, ...] = ()
    type_field: str | None = None
    name_field: str | None = None
    source_level: str = "B"

    @property
    def zone(self) -> timezone:
        return SERVER_ZONES.get(self.server, CST)

    def ask_query(self, limit: int) -> str:
        """拼 SMW ``#ask`` 的查询字符串（与页面里 ``{{#ask:...}}`` 等价）。"""
        parts = [f"[[分类:{self.category}]]"]
        parts.extend(f"?{field}" for field in self.fields)
        parts.append("sort=开始时间")
        parts.append("order=desc")
        parts.append(f"limit={limit}")
        return "|".join(parts)

    def params(self, limit: int) -> dict[str, str]:
        return {"action": "ask", "query": self.ask_query(limit)}


SOURCES: tuple[Source, ...] = (
    Source(
        key="cn-character-pool",
        label="简中服 · 优俊少女招募",
        category="简中赛马娘卡池",
        kind="pool",
        server="cn",
        fields=("开始描述", "结束描述", "开始时间", "结束时间", "角色ID"),
        id_fields=("角色ID",),
    ),
    Source(
        key="cn-support-pool",
        label="简中服 · 协助卡招募",
        category="简中支援卡卡池",
        kind="pool",
        server="cn",
        fields=("开始描述", "结束描述", "开始时间", "结束时间", "支援卡ID", "角色ID"),
        id_fields=("支援卡ID", "角色ID"),
    ),
)

SOURCE_BY_KEY = {source.key: source for source in SOURCES}


@dataclass(frozen=True, slots=True)
class TimelineItem:
    """一条时间轴条目（一次抓取里的原始形态，还没入库）。"""

    uid: str
    source_key: str
    kind: str
    server: str
    title: str
    starts_at: datetime | None
    ends_at: datetime | None
    category: str | None
    ids: dict[str, list[str]]
    source_level: str
    source_url: str
    permanent: bool = False

    def as_row(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "source_key": self.source_key,
            "kind": self.kind,
            "server": self.server,
            "title": self.title,
            "starts_at": iso(self.starts_at),
            "ends_at": iso(self.ends_at),
            "category": self.category,
            "payload": json.dumps(self.ids, ensure_ascii=False, sort_keys=True),
            "source_level": self.source_level,
            "source_url": self.source_url,
            "permanent": int(self.permanent),
        }


def iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="minutes") if value else None


def parse_when(text: str, zone: timezone) -> datetime | None:
    """``2026/10/24 12:00`` 这类时间；``常驻`` 与空值返回 ``None``。"""
    cleaned = (text or "").strip()
    if not cleaned or cleaned in _PERMANENT:
        return None
    cleaned = unicodedata.normalize("NFKC", cleaned)
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=zone)
        except ValueError:
            continue
    return None


def is_permanent(text: str) -> bool:
    """来源写「常驻」而不是具体日期。"""
    return (text or "").strip() in _PERMANENT


def is_placeholder(name: str) -> bool:
    """来源里的占位行（``占位`` / ``待补充`` / ``敬请期待``）。

    这类行没有真实卡池内容，只在页面还没填时占位；降为 D 级并单独标记，
    日程默认把它们隐藏，但数据仍留在库里，便于核对来源确实没内容。
    """
    return bool(_PLACEHOLDER.search(name or ""))


def _first(printouts: dict[str, Any], *keys: str) -> str:
    for key in keys:
        values = printouts.get(key) or []
        for value in values:
            text = str(value).strip()
            if text:
                return text
    return ""


def _id_list(printouts: dict[str, Any], keys: tuple[str, ...]) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {}
    for key in keys:
        values = [str(v).strip() for v in (printouts.get(key) or []) if str(v).strip()]
        if values:
            ids[key] = values
    return ids


def parse_ask_payload(source: Source, payload: dict[str, Any]) -> list[TimelineItem]:
    """把 ``action=ask`` 的 JSON 转成条目列表。

    解析失败不抛异常——单条坏数据只跳过，避免整批抓取作废。
    """
    results = ((payload.get("query") or {}).get("results")) or {}
    items: list[TimelineItem] = []
    for title, entry in results.items():
        if not isinstance(entry, dict):
            continue
        printouts = entry.get("printouts") or {}
        name = title
        if source.name_field:
            name = _first(printouts, source.name_field) or title
        starts_raw = _first(printouts, "开始描述", "开始时间")
        ends_raw = _first(printouts, "结束描述", "结束时间")
        starts_at = parse_when(starts_raw, source.zone)
        ends_at = parse_when(ends_raw, source.zone)
        permanent = is_permanent(starts_raw) or is_permanent(ends_raw)
        category = _first(printouts, source.type_field) if source.type_field else ""
        ids = _id_list(printouts, source.id_fields)

        level = source.source_level
        if _PLACEHOLDER.search(name) or (starts_at is None and ends_at is None and not permanent):
            level = "D"

        items.append(
            TimelineItem(
                uid=f"{source.key}:{title}",
                source_key=source.key,
                kind=source.kind,
                server=source.server,
                title=name.strip(),
                starts_at=starts_at,
                ends_at=ends_at,
                category=category or None,
                ids=ids,
                source_level=level,
                source_url=entry.get("fullurl") or "",
                permanent=permanent,
            )
        )
    return items
