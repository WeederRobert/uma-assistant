"""活动 / 卡池的业务逻辑：抓取 → 解析 → 差异审计 → 时间轴。

设计要点：

- **只对变化的行写审计**：一次抓取里没有变化的条目不会产生 ``events_changes`` 记录。
- **来源消失不删数据**：软删除（``removed_at``），历史仍可回溯；重新出现会自动复活。
- **只跟简中服**：来源只有 `简中赛马娘卡池` / `简中支援卡卡池` 两个分类（见
  :mod:`modules.events.sources`）；繁中服与日服的来源已移除，库里残留的旧来源行在刷新时清理。
- **时间轴一律按系统时区显示**：状态判定、「今天 / 本周」标记与时间文本都用运行环境的本地时区，
  来源服时区只作为附加说明（简中服是 UTC+8，与国内机器一致时不再重复提示）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any

from app.kernel.database import Database
from app.kernel.times import offset_seconds as _offset_seconds
from app.kernel.times import system_zone, zone_label

from . import sources, store
from .fetcher import Fetcher, FetchError, WikiFetcher
from .settings import EventsSettings
from .sources import SERVER_LABELS, SOURCE_BY_KEY, SOURCES, TimelineItem

logger = logging.getLogger(__name__)


class EventsService:
    def __init__(
        self,
        db: Database,
        options: EventsSettings,
        data_dir: Path,
        *,
        fetcher: Fetcher | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.options = options
        self.data_dir = data_dir
        self.logger = logger_ or logger
        store.ensure_schema(db)
        self._fetcher = fetcher
        self._own_fetcher = fetcher is None

    # ------------------------------------------------------------------ setup
    @property
    def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = WikiFetcher(self.options, self.data_dir / "cache", logger_=self.logger)
        return self._fetcher

    def close(self) -> None:
        if self._own_fetcher and isinstance(self._fetcher, WikiFetcher):
            self._fetcher.close()

    def active_sources(self) -> list[sources.Source]:
        """按 ``UMA_EVENTS_SERVERS`` 过滤要抓/要展示的来源。"""
        servers = self.options.server_list
        return [source for source in SOURCES if not servers or source.server in servers]

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        rows = store.source_counters(self.db)
        counters = {row["source_key"]: row for row in rows}
        enabled = {source.key for source in self.active_sources()}
        total_active = sum(row["active"] or 0 for row in rows)
        by_server = _by_server(rows)
        return {
            "items": {
                "total": sum(row["total"] or 0 for row in rows),
                "active": total_active,
                "by_kind": _by_kind(rows),
                "by_server": by_server,
            },
            "servers": [
                {"code": code, "label": label, "items": by_server.get(code, 0)}
                for code, label in SERVER_LABELS.items()
                # 配置里的服务器 + 库里仍有数据的服务器（配置改小后也还能看到旧数据）
                if code in self.options.server_list
                or not self.options.server_list
                or by_server.get(code)
            ],
            "sources": [
                {
                    "key": source.key,
                    "label": source.label,
                    "category": source.category,
                    "kind": source.kind,
                    "server": source.server,
                    "server_label": SERVER_LABELS.get(source.server, source.server),
                    "source_level": source.source_level,
                    "enabled": source.key in enabled,
                    "items": (counters.get(source.key) or {}).get("active", 0) or 0,
                }
                for source in SOURCES
            ],
            "last_fetches": store.last_fetches(self.db),
            "last_refresh": self.db.get_meta(store.META_LAST_REFRESH),
            "api_url": self.options.api_url,
            "interval_seconds": self.options.interval_seconds,
            "window": {
                "days_before": self.options.days_before,
                "days_after": self.options.days_after,
            },
        }

    def history(self, limit: int = 50) -> dict[str, Any]:
        return {
            "changes": store.recent_changes(self.db, limit),
            "fetches": store.last_fetches(self.db),
        }

    # ---------------------------------------------------------------- refresh
    def refresh(
        self, source_keys: list[str] | None = None, *, force: bool = False
    ) -> dict[str, Any]:
        """抓取并合并；单个来源失败不影响其它来源。

        ``force=False`` 时抓取层会在缓存有效期内直接复用（默认 6 小时），
        面板的「刷新」按钮传 ``force=True`` 保证拿到最新内容。
        """
        targets = self.active_sources()
        if source_keys:
            wanted = set(source_keys)
            targets = [source for source in targets if source.key in wanted]
        if not targets:
            return {
                "ok": False,
                "reason": "没有可抓取的来源（检查 UMA_EVENTS_SERVERS 与来源名）",
                "results": [],
                "totals": {"added": 0, "changed": 0, "removed": 0, "items": 0},
            }

        # 繁中服 / 日服来源已下线，库里若有残留的行（含审计）在这里清掉
        purged = store.purge_sources(self.db, {source.key for source in SOURCES})
        if any(purged.values()):
            self.logger.info(
                "清理已下线来源的数据 | 条目 %d 审计 %d 抓取记录 %d",
                purged["items"],
                purged["changes"],
                purged["fetches"],
            )

        started_at = store.now_iso()
        results: list[dict[str, Any]] = []
        totals = {"added": 0, "changed": 0, "removed": 0, "items": 0}

        for source in targets:
            results.append(self._refresh_one(source, force=force))
            totals["added"] += results[-1]["added"]
            totals["changed"] += results[-1]["changed"]
            totals["removed"] += results[-1]["removed"]
            totals["items"] += results[-1]["items"]

        finished_at = store.now_iso()
        self.db.set_meta(store.META_LAST_REFRESH, finished_at)
        ok = all(result["ok"] for result in results)
        self.logger.info(
            "活动刷新完成 | 来源 %d | 新增 %d 变更 %d 移除 %d | 成功=%s",
            len(results),
            totals["added"],
            totals["changed"],
            totals["removed"],
            ok,
        )
        return {
            "ok": ok,
            "started_at": started_at,
            "finished_at": finished_at,
            "results": results,
            "totals": totals,
        }

    def _refresh_one(self, source: sources.Source, *, force: bool = False) -> dict[str, Any]:
        fetch_id = store.begin_fetch(self.db, source.key, store.now_iso())
        base = {
            "source_key": source.key,
            "label": source.label,
            "server": source.server,
            "kind": source.kind,
            "items": 0,
            "added": 0,
            "changed": 0,
            "removed": 0,
            "cached": False,
            "stale": False,
            "note": "",
        }
        try:
            result = self.fetcher.get(source.params(self.options.max_results), force=force)
            items = sources.parse_ask_payload(source, result.payload)
            plan = diff_items(source, store.load_items(self.db, source.key), items)
            fetched_at = result.fetched_at or store.now_iso()

            store.upsert_items(self.db, [item.as_row() for item in items], fetched_at)
            store.mark_removed(self.db, plan["removed"], store.now_iso())
            store.record_changes(self.db, fetch_id, plan["changes"])

            counts = {
                "items": len(items),
                "added": plan["added"],
                "changed": plan["changed"],
                "removed": len(plan["removed"]),
                "cached": result.from_cache,
                "stale": result.stale,
                "note": result.note,
            }
            store.finish_fetch(self.db, fetch_id, ok=True, **counts)
            self.db.set_meta(store.META_LEVEL_NOTE, source.source_level)
            return {**base, **counts, "ok": True}
        except FetchError as exc:
            store.finish_fetch(self.db, fetch_id, ok=False, error=str(exc))
            self.logger.warning("来源 %s 抓取失败：%s", source.key, exc)
            return {**base, "ok": False, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - 兜底，避免整次刷新中断
            store.finish_fetch(self.db, fetch_id, ok=False, error=str(exc))
            self.logger.exception("来源 %s 处理异常", source.key)
            return {**base, "ok": False, "error": str(exc)}

    # --------------------------------------------------------------- schedule
    def schedule(
        self,
        *,
        servers: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        days_before: int | None = None,
        days_after: int | None = None,
        include_placeholders: bool = False,
    ) -> dict[str, Any]:
        before = self.options.days_before if days_before is None else days_before
        after = self.options.days_after if days_after is None else days_after
        scope = servers if servers is not None else self.options.server_list
        rows = store.list_items(self.db, servers=scope or None, kinds=kinds)

        now = datetime.now(UTC)
        items = [describe(row, now) for row in rows]
        items.sort(key=_schedule_sort_key)

        window_start = now - timedelta(days=before)
        window_end = now + timedelta(days=after)

        def in_window(item: dict[str, Any]) -> bool:
            return (
                item["state"] in {"active", "permanent"}
                or (item["starts_dt"] and window_start <= item["starts_dt"] <= window_end)
                or (item["ends_dt"] and window_start <= item["ends_dt"] <= window_end)
                or item["state"] == "unknown"
            )

        # 占位行（来源页面还没填内容）默认隐藏，避免被当成真实卡池；被藏了几条要单独报出来
        visible = [
            item
            for item in items
            if in_window(item) and (include_placeholders or not item["placeholder"])
        ]
        hidden = sum(1 for item in items if in_window(item) and item["placeholder"])
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "servers": list(scope or ()),
            "window": {"days_before": before, "days_after": after},
            "counts": {
                "active": sum(1 for item in visible if item["state"] == "active"),
                "upcoming": sum(1 for item in visible if item["state"] == "upcoming"),
                "ended": sum(1 for item in visible if item["state"] == "ended"),
                "today": sum(1 for item in visible if item["starts_today"]),
                "this_week": sum(1 for item in visible if item["this_week"]),
                "placeholders": 0 if include_placeholders else hidden,
            },
            "include_placeholders": include_placeholders,
            "near_term": self._near_term(visible, now),
            "items": [_public(item) for item in visible],
        }

    def _near_term(self, items: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
        """「即将开始」与「即将结束」的摘要。

        BWiki 的分类页只收录**已经开始**的卡池与活动，所以 ``upcoming`` 经常是空的；
        这时 ``ending_soon`` 才是「接下来会变的是什么」。两者都按时间排序并给出天数，
        面板不用自己算时区与差值。
        """
        horizon = self.options.near_days
        upcoming = sorted(
            (item for item in items if item["state"] == "upcoming"),
            key=lambda item: item["starts_dt"] or now,
        )
        ending_soon = [
            item
            for item in items
            if item["state"] == "active"
            and item["ends_dt"] is not None
            and timedelta(0) <= item["ends_dt"] - now <= timedelta(days=horizon)
        ]
        ending_soon.sort(key=lambda item: item["ends_dt"])
        return {
            "horizon_days": horizon,
            "upcoming_total": len(upcoming),
            "ending_soon_total": len(ending_soon),
            "upcoming": [_near_item(item) for item in upcoming[:5]],
            "ending_soon": [_near_item(item) for item in ending_soon[:5]],
        }


# ------------------------------------------------------------------ diff 逻辑
def diff_items(
    source: sources.Source,
    existing: dict[str, dict[str, Any]],
    items: list[TimelineItem],
) -> dict[str, Any]:
    """比对库内与本次抓取结果，产出要写的审计行与要软删的 uid。"""
    changed_at = store.now_iso()
    changes: list[dict[str, Any]] = []
    incoming: set[str] = set()
    added = 0
    changed_items = 0

    for item in items:
        row = item.as_row()
        incoming.add(row["uid"])
        previous = existing.get(row["uid"])
        if previous is None or previous.get("removed_at"):
            added += 1
            changes.append(
                {
                    "source_key": source.key,
                    "uid": row["uid"],
                    "server": row["server"],
                    "title": row["title"],
                    "action": "added",
                    "field": None,
                    "before_value": None,
                    "after_value": row["title"],
                    "changed_at": changed_at,
                }
            )
            continue

        item_changed = False
        for field in store.COMPARED_FIELDS:
            before = previous.get(field)
            after = row.get(field)
            if (before or None) == (after or None):
                continue
            item_changed = True
            changes.append(
                {
                    "source_key": source.key,
                    "uid": row["uid"],
                    "server": row["server"],
                    "title": row["title"],
                    "action": "changed",
                    "field": field,
                    "before_value": _as_text(before),
                    "after_value": _as_text(after),
                    "changed_at": changed_at,
                }
            )
        if item_changed:
            changed_items += 1

    removed = [
        uid
        for uid, previous in existing.items()
        if not previous.get("removed_at") and uid not in incoming
    ]
    for uid in removed:
        previous = existing[uid]
        changes.append(
            {
                "source_key": source.key,
                "uid": uid,
                "server": previous["server"],
                "title": previous["title"],
                "action": "removed",
                "field": None,
                "before_value": previous["title"],
                "after_value": None,
                "changed_at": changed_at,
            }
        )

    return {
        "changes": changes,
        "removed": removed,
        "added": added,
        "changed": changed_items,
    }


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


# ------------------------------------------------------------- 时间轴展示逻辑
def _zone_of(row: dict[str, Any]) -> Any:
    source = SOURCE_BY_KEY.get(row["source_key"])
    return source.zone if source else sources.CST


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def describe(
    row: dict[str, Any], now: datetime, *, local_zone: tzinfo | None = None
) -> dict[str, Any]:
    """把一行变成带状态、本地时间与「今天/本周」标记的展示对象。

    ``local_zone`` 默认取运行环境的系统时区；测试可显式传入以摆脱机器时区。
    """
    source_zone = _zone_of(row)
    zone = local_zone or system_zone()
    starts = _parse_dt(row.get("starts_at"))
    ends = _parse_dt(row.get("ends_at"))
    local_now = now.astimezone(zone)

    if starts is not None and starts > now:
        state = "upcoming"
    elif ends is not None and ends < now:
        state = "ended"
    elif starts is None and ends is None:
        state = "permanent" if row.get("permanent") else "unknown"
    else:
        state = "active"

    days_left = None
    if ends is not None:
        days_left = (ends - now).days
    days_to_start = (starts - now).days if starts is not None else None

    starts_today = bool(starts and starts.astimezone(zone).date() == local_now.date())
    this_week = bool(starts and _same_week(starts.astimezone(zone), local_now))

    return {
        **row,
        "zone": zone_label(zone),
        "source_zone": source_zone.tzname(None),
        # 来源服时区与系统时区一致时不必重复说明（简中 / 繁中服在 UTC+8 机器上就是这种情况）
        "same_zone": _offset_seconds(source_zone) == _offset_seconds(zone),
        "starts_dt": starts,
        "ends_dt": ends,
        "starts_local": starts.astimezone(zone).strftime("%Y-%m-%d %H:%M") if starts else None,
        "ends_local": ends.astimezone(zone).strftime("%Y-%m-%d %H:%M") if ends else None,
        # 来源服时区下的原始时间，供面板在时区不同时说明「原本几点」
        "starts_source": (
            starts.astimezone(source_zone).strftime("%Y-%m-%d %H:%M") if starts else None
        ),
        "ends_source": ends.astimezone(source_zone).strftime("%Y-%m-%d %H:%M") if ends else None,
        "state": state,
        "placeholder": sources.is_placeholder(row["title"]),
        "days_left": days_left,
        "days_to_start": days_to_start,
        "starts_today": starts_today,
        "this_week": this_week,
        "server_label": SERVER_LABELS.get(row["server"], row["server"]),
        "source_label": (
            SOURCE_BY_KEY.get(row["source_key"]).label
            if SOURCE_BY_KEY.get(row["source_key"])
            else row["source_key"]
        ),
        "level_label": sources.LEVEL_LABELS.get(row["source_level"], row["source_level"]),
        "kind_label": sources.KIND_LABELS.get(row["kind"], row["kind"]),
    }


def _same_week(moment: datetime, reference: datetime) -> bool:
    start = reference - timedelta(days=reference.weekday())
    week_start = datetime.combine(start.date(), time.min, tzinfo=reference.tzinfo)
    return week_start <= moment < week_start + timedelta(days=7)


def _schedule_sort_key(item: dict[str, Any]) -> tuple[int, float, str]:
    order = {"active": 0, "permanent": 0, "upcoming": 1, "unknown": 2, "ended": 3}
    anchor = item["starts_dt"] or item["ends_dt"]
    # 正在进行按结束时间临近优先；其余按开始时间
    if item["state"] in {"active", "permanent"}:
        anchor = item["ends_dt"] or anchor
    timestamp = anchor.timestamp() if anchor else float("inf")
    if item["state"] in {"ended"} and anchor:
        timestamp = -timestamp
    return (order.get(item["state"], 9), timestamp, item["title"])


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if not key.startswith("_") and not key.endswith("_dt")
    }


def _near_item(item: dict[str, Any]) -> dict[str, Any]:
    """「近期提醒」用的精简条目：只带面板要显示的那几列。"""
    return {
        "uid": item["uid"],
        "title": item["title"],
        "server": item["server"],
        "server_label": item["server_label"],
        "kind": item["kind"],
        "kind_label": item["kind_label"],
        "source_label": item["source_label"],
        "source_level": item["source_level"],
        "level_label": item["level_label"],
        "starts_local": item["starts_local"],
        "ends_local": item["ends_local"],
        "days_to_start": item["days_to_start"],
        "days_left": item["days_left"],
        "zone": item["zone"],
    }


def _by_kind(rows: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        result[row["kind"]] = result.get(row["kind"], 0) + (row["active"] or 0)
    return result


def _by_server(rows: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        result[row["server"]] = result.get(row["server"], 0) + (row["active"] or 0)
    return result
