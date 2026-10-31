"""社团模块的业务逻辑：拉取、缓存与贡献占比。

口径（面板不再换算，全部在这里定好）：

- 「当月增粉」取站点列表接口的 ``month_delta``。站点是用**当月 1 日 05:00**（游戏换日）
  的总粉丝快照做差得到的，不是游戏内直接给出的字段。
- 「成员贡献占比」= 成员当月增粉 ÷ 本社成员当月增粉合计。分母用成员合计而不是榜单值，
  两者会有 0.0x% 的差值（成员进出、双方快照时间差），差值单独输出，不摊平。
- 站点只收录了约 160 个社团，未收录的社团详情接口返回 404；数据由第三方玩家自采，
  非官方；站点停更时只能展示最后一次缓存。
- ``viewer_id`` 只用于去重，不进接口、不进面板。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from app.kernel.database import Database

from . import store
from .client import CirclesClient, CirclesClientProtocol, CirclesSourceError
from .schema import ensure_schema
from .settings import CirclesSettings

NOTE_SOURCE = (
    "数据来自第三方站点 umamusume.space 的公开接口，由玩家自采，不是官方数据；"
    "口径以站点说明为准。"
)
NOTE_MONTH = (
    "「当月增粉」是站点用总粉丝快照差分得到的，基线为当月 1 日 05:00（游戏换日），"
    "不是游戏内字段。"
)
NOTE_GAP = (
    "成员合计与榜单月增粉丝的差值来自成员进出与快照时间差，不做摊平；"
    "贡献占比的分母是成员合计。"
)
NOTE_PRIVACY = "接口里的 viewer_id 只用于去重，接口与面板都不展示。"
NOTE_EXPERIMENTAL = "实验性功能：第三方接口没有文档与 SLA，站点改版或停更时会失效。"


class CirclesNotFound(LookupError):
    """本机还没有这个社团的榜单数据（先拉一次社团月榜）。"""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_fans(value: int | float | None) -> str:
    """粉丝数按站点习惯缩略：亿 / 万，保留两位 / 一位小数。"""
    if value is None:
        return "—"
    number = float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1e8:
        return f"{sign}{number / 1e8:.2f} 亿"
    if number >= 1e4:
        return f"{sign}{number / 1e4:.1f} 万"
    return f"{sign}{int(number)}"


def _percent_text(gap: int, total: int) -> str | None:
    """差值占比：小于 0.01% 时不显示成 0%，避免看起来像完全对齐。"""
    if not total:
        return None
    percent = gap / total * 100
    if percent == 0:
        return "0%"
    if abs(percent) < 0.01:
        return "<0.01%"
    return f"{percent:.2f}%"


def _age_minutes(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return (now - parsed).total_seconds() / 60


class CirclesService:
    def __init__(
        self,
        db: Database,
        options: CirclesSettings | None = None,
        *,
        client: CirclesClientProtocol | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.options = options or CirclesSettings()
        self.logger = logger_ or logging.getLogger(__name__)
        ensure_schema(db)
        self._owns_client = client is None
        self.client: CirclesClientProtocol = client or CirclesClient(
            self.options, logger_=self.logger
        )

    def close(self) -> None:
        if self._owns_client and isinstance(self.client, CirclesClient):
            self.client.close()

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        counts = store.counts(self.db)
        meta = store.read_meta(self.db)
        interval = _to_float(meta.get("capture_interval_minutes"))
        return {
            "ok": True,
            "experimental": True,
            "source": self.options.base_url,
            "circle_count": counts["circles"],
            "member_circle_count": counts["member_circles"],
            "member_rows": counts["members"],
            "fetched_at": meta.get("fetched_at"),
            "month_baseline_at": meta.get("month_baseline_at"),
            "capture_interval_minutes": interval,
            "member_cache_minutes": self.options.member_cache_minutes,
            "notes": [NOTE_EXPERIMENTAL, NOTE_SOURCE],
        }

    # ------------------------------------------------------------------ 拉取
    def refresh(self) -> dict[str, Any]:
        """拉一次社团月榜（约 100 KB），把社团级数据整体覆盖进库。"""
        started = time.perf_counter()
        items = self.client.fetch_list()
        fetched_at = _now_iso()
        rows = [self._circle_row(item, fetched_at) for item in items]
        rows = [row for row in rows if row["circle_id"]]
        store.replace_circles(self.db, rows)

        baseline = next((row["month_baseline_at"] for row in rows if row["month_baseline_at"]), "")
        interval = next(
            (row["capture_interval_minutes"] for row in rows if row["capture_interval_minutes"]),
            None,
        )
        store.write_meta(
            self.db,
            {
                "fetched_at": fetched_at,
                "month_baseline_at": baseline or "",
                "capture_interval_minutes": str(interval) if interval is not None else "",
            },
        )
        self.logger.info("已拉取社团月榜：%d 个社团", len(rows))
        return {
            "fetched": len(rows),
            "fetched_at": fetched_at,
            "month_baseline_at": baseline or None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "notes": [NOTE_MONTH],
        }

    # ------------------------------------------------------------------ 查询
    def list_circles(
        self,
        *,
        query: str | None = None,
        sort: str = "month",
        limit: int | None = None,
    ) -> dict[str, Any]:
        rows = store.list_circles(
            self.db,
            query=query,
            sort=sort,
            limit=limit or self.options.list_limit,
        )
        meta = store.read_meta(self.db)
        return {
            "fetched_at": meta.get("fetched_at"),
            "month_baseline_at": meta.get("month_baseline_at"),
            "count": len(rows),
            "items": [self._circle_payload(row) for row in rows],
            "notes": [NOTE_MONTH, NOTE_EXPERIMENTAL],
        }

    def circle_detail(self, circle_id: int, *, refresh: bool = False) -> dict[str, Any]:
        circle = store.get_circle(self.db, circle_id)
        if circle is None:
            raise CirclesNotFound(f"本机还没有社团 {circle_id} 的榜单数据，先拉取社团月榜")

        cached = store.list_members(self.db, circle_id)
        age = _age_minutes(cached[0]["fetched_at"]) if cached else None
        fresh = age is not None and age <= self.options.member_cache_minutes

        stale = False
        notes: list[str] = []
        if refresh or not fresh:
            try:
                payload = self.client.fetch_circle(circle_id)
                fetched_at = _now_iso()
                rows = [
                    self._member_row(item, circle_id, fetched_at)
                    for item in payload.get("members") or []
                ]
                store.replace_members(self.db, circle_id, rows)
                cached = store.list_members(self.db, circle_id)
                notes.append(f"成员明细已更新（{len(cached)} 人）。")
            except CirclesSourceError as exc:
                if not cached:
                    raise
                stale = True
                notes.append(f"拉取成员明细失败，展示上次缓存：{exc}")
        else:
            notes.append(f"成员明细来自 {self.options.member_cache_minutes:.0f} 分钟内的缓存。")

        return self._detail_payload(circle, cached, stale=stale, notes=notes)

    # ------------------------------------------------------------------ 组装
    @staticmethod
    def _circle_row(item: dict[str, Any], fetched_at: str) -> dict[str, Any]:
        return {
            "circle_id": _to_int(item.get("circle_id")),
            "name": str(item.get("circle_name") or "未命名社团"),
            "member_count": _to_int(item.get("member_count")),
            "total_fans": _to_int(item.get("total_fans")),
            "month_fans": _to_int(item.get("month_delta")),
            "today_fans": _to_int(item.get("today_delta")),
            "ranking_rank": _to_int(item.get("ranking_rank")),
            "capture_interval_minutes": _to_float(item.get("capture_interval_minutes")),
            "month_baseline_at": item.get("month_baseline_at"),
            "latest_at": item.get("latest_at"),
            "fetched_at": fetched_at,
        }

    @staticmethod
    def _member_row(item: dict[str, Any], circle_id: int, fetched_at: str) -> dict[str, Any]:
        return {
            "circle_id": circle_id,
            "viewer_id": _to_int(item.get("viewer_id")) or 0,
            "member_name": str(item.get("member_name") or "未命名成员"),
            "fan": _to_int(item.get("fan")),
            "month_fan": _to_int(item.get("month_fan")),
            "today_fan": _to_int(item.get("today_delta")),
            "daily_avg": _to_float(item.get("daily_avg")),
            "month_training_count": _to_int(item.get("month_training_count")),
            "single_mode_play_count": _to_int(item.get("single_mode_play_count")),
            "join_time": item.get("join_time"),
            "last_login_time": item.get("last_login_time"),
            "fetched_at": fetched_at,
        }

    @staticmethod
    def _circle_payload(row: Any) -> dict[str, Any]:
        return {
            "circle_id": row["circle_id"],
            "name": row["name"],
            "member_count": row["member_count"],
            "month_fans": row["month_fans"],
            "month_fans_text": format_fans(row["month_fans"]),
            "today_fans": row["today_fans"],
            "today_fans_text": format_fans(row["today_fans"]),
            "ranking_rank": row["ranking_rank"],
            "has_members": bool(row["member_rows"]),
            "fetched_at": row["fetched_at"],
        }

    def _detail_payload(
        self,
        circle: Any,
        members: list[Any],
        *,
        stale: bool,
        notes: list[str],
    ) -> dict[str, Any]:
        total = sum(int(member["month_fan"] or 0) for member in members)
        items: list[dict[str, Any]] = []
        for member in sorted(
            members, key=lambda row: (-(row["month_fan"] or 0), row["member_name"])
        ):
            month_fan = int(member["month_fan"] or 0)
            share = round(month_fan / total * 100, 2) if total else None
            items.append(
                {
                    "member_name": member["member_name"],
                    "month_fan": member["month_fan"],
                    "month_fan_text": format_fans(member["month_fan"]),
                    "share_percent": share,
                    "share_text": f"{share:.2f}%" if share is not None else "—",
                    "fan": member["fan"],
                    "fan_text": format_fans(member["fan"]),
                    "today_fan_text": format_fans(member["today_fan"]),
                    "daily_avg_text": format_fans(member["daily_avg"]),
                    "month_training_count": member["month_training_count"],
                    "last_login_time": member["last_login_time"] or None,
                }
            )

        list_total = int(circle["month_fans"] or 0)
        gap = list_total - total
        return {
            "circle": {
                "circle_id": circle["circle_id"],
                "name": circle["name"],
                "member_count": circle["member_count"],
                "total_fans": circle["total_fans"],
                "total_fans_text": format_fans(circle["total_fans"]),
                "month_fans": list_total,
                "month_fans_text": format_fans(list_total),
                "today_fans_text": format_fans(circle["today_fans"]),
                "ranking_rank": circle["ranking_rank"],
                "month_baseline_at": circle["month_baseline_at"],
                "latest_at": circle["latest_at"],
            },
            "summary": {
                "member_count": len(items),
                "contributor_count": sum(1 for item in items if (item["month_fan"] or 0) > 0),
                "member_month_fans": total,
                "member_month_fans_text": format_fans(total),
                "list_month_fans": list_total,
                "list_month_fans_text": format_fans(list_total),
                "gap_fans": gap,
                "gap_fans_text": format_fans(gap),
                "gap_percent": round(gap / list_total * 100, 2) if list_total else None,
                "gap_percent_text": _percent_text(gap, list_total),
                "per_member_fans_text": format_fans(total / len(items)) if items else "—",
                "top_member": items[0]["member_name"] if items else None,
                "top_share_percent": items[0]["share_percent"] if items else None,
            },
            "members": items,
            "stale": stale,
            "fetched_at": members[0]["fetched_at"] if members else None,
            "notes": [*notes, NOTE_GAP, NOTE_PRIVACY],
        }
