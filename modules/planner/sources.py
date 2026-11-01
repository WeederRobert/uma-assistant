"""千里眼的预测来源：BWiki 的「预测卡池」表。

只取一条 SMW 查询：

```
[[分类:赛马娘卡池]][[简中::未实装]]|?开始描述|?简中开始时间|?简中结束时间|?角色ID|?支援卡ID
```

``简中::未实装`` 是 wiki 自己的标记（「日服已上线、简中还没实装」），页面把针对简中服算出的
**预测**开始/结束时间写在属性里。这个时间是 wiki 按「日服时间 + 固定偏移」推的，
**简中服处于加速期，这条轴已经不准**，只能当粗糙参考：
2026-11-01 用本机 ``master.mdb`` 的已确认排期对齐，wiki 预测系统性偏晚 35~50 天且仍在扩大。
校正逻辑在 :mod:`modules.planner.service`（用 mdb 实测偏差平移，并标明误差区间）。

活动**不从这里来**：``分类:活动`` 只有日服时间，对轴不可用；剧情活动排期直接读
``master.mdb`` 的 ``story_event_data``（A 级）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

CST = timezone(timedelta(hours=8), "CST")  # 简中服时区，wiki 的简中时间按它填

SOURCE_LABELS = {
    "mdb": "本机 master.mdb（客户端内置排期）",
    "wiki-predicted": "BWiki 预测卡池（简中::未实装）",
}

# 页面还没填内容时留的占位行
_PLACEHOLDER = re.compile(r"占位|待补充|敬请期待")
_DATE_FORMATS = ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d")


POOL_QUERY = (
    "[[分类:赛马娘卡池]][[简中::未实装]]"
    "|?开始描述|?简中开始时间|?简中结束时间|?角色ID|?支援卡ID"
    "|sort=简中开始时间|order=asc"
)


def ask_query(template: str, limit: int) -> str:
    return f"{template}|limit={limit}"


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


def _ids(printouts: dict[str, Any], key: str) -> list[str]:
    """SMW 的多值属性可能是列表，也可能是 ``"30160, 30161"`` 这种字符串。"""
    ids: list[str] = []
    for value in printouts.get(key) or []:
        for part in str(value).replace("，", ",").split(","):
            part = part.strip()
            if part and part not in ids:
                ids.append(part)
    return ids


def _results(payload: dict[str, Any]) -> dict[str, Any]:
    """``action=ask`` 的 ``query.results``；新版接口会返回列表，这里统一成字典。"""
    results = ((payload.get("query") or {}).get("results")) or {}
    if isinstance(results, list):
        return {(item.get("fulltext") or item.get("title") or ""): item for item in results}
    return results


@dataclass(frozen=True, slots=True)
class PredictedPool:
    """BWiki 预测卡池里的一行：``start`` 是 wiki 的简中预测时间，不是实测值。"""

    title: str
    jp_start: datetime | None
    start: datetime
    end: datetime | None
    char_ids: list[str]
    support_ids: list[str]
    source_url: str
    placeholder: bool = False

    @property
    def uid(self) -> str:
        return f"wiki:{self.title}"

    @property
    def ids(self) -> dict[str, list[str]]:
        return {"char_ids": self.char_ids, "support_ids": self.support_ids}


def parse_pools(payload: dict[str, Any]) -> list[PredictedPool]:
    """解析预测卡池；没有「简中开始时间」的行放不进时间轴，直接跳过。"""
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
                char_ids=_ids(printouts, "角色ID"),
                support_ids=_ids(printouts, "支援卡ID"),
                source_url=entry.get("fullurl") or "",
                placeholder=bool(_PLACEHOLDER.search(title or "")),
            )
        )
    return items
