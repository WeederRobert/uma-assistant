"""千里眼的数据来源与「日服时间 → 简中时间」映射。

两条 SMW 查询都打 BWiki 同一个 wiki：

================  ========================================  ==================================
查询               查询串                                     服务器口径
================  ========================================  ==================================
预测卡池            ``[[分类:赛马娘卡池]][[简中::未实装]]``        **简中服预测时间**：wiki 已算好
                  ``简中开始时间`` / ``简中结束时间``          并在页面「预测卡池」表里列出
日服活动            ``[[分类:活动]]``                           日服时间，页面没有简中属性
================  ========================================  ==================================

所以活动要在简中服日程里出现，只能用**卡池反推出的偏移**换算（见 :class:`OffsetMap`），
活动行一律标 C 级并注明依据；卡池行用 wiki 给的简中时间，标 C 级并回显日服原时间。

不抄 wiki 页面上写的「-185 天 / +423 天」这类常数：那些是某一时期的调整记录，
会随简中服追进度而变。这里逐条相减再线性插值，数据变了会自动跟着变。
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

CST = timezone(timedelta(hours=8), "CST")  # 简中服时区，wiki 的简中时间按它填

SOURCE_LABELS = {
    "mdb": "本机 master.mdb（客户端内置排期）",
    "wiki-predicted": "BWiki 预测卡池（简中::未实装）",
    "wiki-event": "BWiki 活动表（日服时间，经卡池偏移换算）",
}

# 页面还没填内容时留的占位行，与 events 模块同一套判定
_PLACEHOLDER = re.compile(r"占位|待补充|敬请期待")
_BRACKET = re.compile(r"「([^」]{2,})」")
_DATE_FORMATS = ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d")


def ask_query(template: str, limit: int) -> str:
    return f"{template}|limit={limit}"


POOL_QUERY = (
    "[[分类:赛马娘卡池]][[简中::未实装]]"
    "|?开始描述|?简中开始时间|?简中结束时间|?角色ID|?支援卡ID"
    "|sort=简中开始时间|order=asc"
)
EVENT_QUERY = "[[分类:活动]]|?名称|?开始描述|?结束描述|?类型|sort=开始时间|order=asc"


def parse_when(text: str) -> datetime | None:
    """``2026/09/01 12:00`` 这类时间；空值与 ``常驻`` 返回 ``None``。"""
    cleaned = unicodedata.normalize("NFKC", (text or "").strip())
    if not cleaned:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def _first(printouts: dict[str, Any], *keys: str) -> str:
    for key in keys:
        for value in printouts.get(key) or []:
            text = str(value).strip()
            if text:
                return text
    return ""


def _id_lists(printouts: dict[str, Any], keys: tuple[str, ...]) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {}
    for key in keys:
        values = [str(v).strip() for v in (printouts.get(key) or []) if str(v).strip()]
        if values:
            ids[key] = values
    return ids


def _results(payload: dict[str, Any]) -> dict[str, Any]:
    """``action=ask`` 的 ``query.results``；新版接口会返回列表，这里统一成字典。"""
    results = ((payload.get("query") or {}).get("results")) or {}
    if isinstance(results, list):
        return {(item.get("fulltext") or item.get("title") or ""): item for item in results}
    return results


@dataclass(frozen=True, slots=True)
class PredictedPool:
    """BWiki 预测卡池里的一行（简中时间已由 wiki 算好）。"""

    title: str
    jp_start: datetime | None
    start: datetime
    end: datetime | None
    ids: dict[str, list[str]]
    source_url: str
    placeholder: bool = False

    @property
    def uid(self) -> str:
        return f"wiki:{self.title}"

    @property
    def delta_days(self) -> float | None:
        if self.jp_start is None:
            return None
        return (self.start - self.jp_start).total_seconds() / 86400


@dataclass(frozen=True, slots=True)
class JpEvent:
    """BWiki 活动表里的一行（只有日服时间）。"""

    title: str
    kind: str
    jp_start: datetime | None
    jp_end: datetime | None
    source_url: str

    @property
    def uid(self) -> str:
        return f"wiki-event:{self.title}"


def parse_pools(payload: dict[str, Any]) -> list[PredictedPool]:
    """解析预测卡池。没有「简中开始时间」的行放不进时间轴，直接跳过。"""
    items: list[PredictedPool] = []
    for title, entry in _results(payload).items():
        if not isinstance(entry, dict):
            continue
        printouts = entry.get("printouts") or {}
        start = parse_when(_first(printouts, "简中开始时间"))
        if start is None:
            continue
        items.append(
            PredictedPool(
                title=(title or "").strip(),
                jp_start=parse_when(_first(printouts, "开始描述")),
                start=start,
                end=parse_when(_first(printouts, "简中结束时间")),
                ids=_id_lists(printouts, ("角色ID", "支援卡ID")),
                source_url=entry.get("fullurl") or "",
                placeholder=bool(_PLACEHOLDER.search(title or "")),
            )
        )
    return items


def parse_events(payload: dict[str, Any]) -> list[JpEvent]:
    items: list[JpEvent] = []
    for title, entry in _results(payload).items():
        if not isinstance(entry, dict):
            continue
        printouts = entry.get("printouts") or {}
        name = _first(printouts, "名称") or title
        items.append(
            JpEvent(
                title=name.strip(),
                kind=_first(printouts, "类型"),
                jp_start=parse_when(_first(printouts, "开始描述")),
                jp_end=parse_when(_first(printouts, "结束描述")),
                source_url=entry.get("fullurl") or "",
            )
        )
    return items


@dataclass(slots=True)
class OffsetMap:
    """日服时间 → 简中时间的天数差，由预测卡池反推。

    ``points`` 是 ``(日服开始时间, 相差天数)``，按日服时间升序；查询时在相邻两点间线性插值，
    落在两端之外则用端点值。没有任何可用数据时 :attr:`known` 为 ``False``。
    """

    points: list[tuple[datetime, float]] = field(default_factory=list)

    @classmethod
    def build(cls, pools: list[PredictedPool]) -> OffsetMap:
        pairs: dict[datetime, float] = {}
        for pool in pools:
            delta = pool.delta_days
            if delta is None or pool.jp_start is None:
                continue
            pairs.setdefault(pool.jp_start, delta)
        return cls(sorted(pairs.items()))

    @property
    def known(self) -> bool:
        return bool(self.points)

    @property
    def median_days(self) -> float | None:
        if not self.points:
            return None
        return float(statistics.median(delta for _, delta in self.points))

    @property
    def spread_days(self) -> tuple[int, int] | None:
        """偏移的最小 / 最大值（取整），用于在界面上说明「不是固定天数」。"""
        if not self.points:
            return None
        values = [round(delta) for _, delta in self.points]
        return min(values), max(values)

    def delta_days(self, jp: datetime) -> float:
        if not self.points:
            raise ValueError("没有可用的卡池偏移数据，无法推算简中时间")
        if jp <= self.points[0][0]:
            return self.points[0][1]
        if jp >= self.points[-1][0]:
            return self.points[-1][1]
        stamps = [point[0] for point in self.points]
        index = bisect_left(stamps, jp)
        before_at, before = self.points[index - 1]
        after_at, after = self.points[index]
        span = (after_at - before_at).total_seconds()
        if span <= 0:
            return after
        ratio = (jp - before_at).total_seconds() / span
        return before + (after - before) * ratio

    def to_cn(self, jp: datetime) -> datetime:
        return jp + timedelta(days=self.delta_days(jp))


def inner_title(title: str) -> str | None:
    """取标题里「」包起来的活动名，用来把活动与活动卡池对上。"""
    found = _BRACKET.search(title or "")
    return found.group(1).strip() if found else None


def match_event_pool(title: str, pools: list[PredictedPool]) -> PredictedPool | None:
    """按「」里的活动名配对。

    活动卡池标题形如 ``「寂寥秋色，连缀思念」活动卡池``，活动标题形如
    ``剧情活动「寂寥秋色，连缀思念」``。
    """
    wanted = inner_title(title)
    if not wanted:
        return None
    for pool in pools:
        if inner_title(pool.title) == wanted:
            return pool
    return None
