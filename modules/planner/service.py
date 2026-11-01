"""规划模块的业务逻辑：千里眼窗口、活动换算与萝卜规划计算。

口径都写在这里，面板只做展示：

- **已确认段**取 ``master.mdb`` 的 ``gacha_data``，覆盖到客户端排期的最后一刻
  （当前快照是 2027-01-26）。这一段是 A 级数据，规划以它为准。
- **预测段**取 BWiki 的「预测卡池」，用它自己算好的简中时间；落在已确认段之内的行
  标 ``overlap``，只展示不参与规划，避免同一期卡池被算两次。
- **活动**：BWiki 的活动表只有日服时间。能对上同名「活动卡池」的，直接用卡池的简中时间；
  对不上的用卡池反推的偏移（见 :class:`modules.planner.sources.OffsetMap`）换算，标「偏移推算」。
- **成本**：单抽成本与天井抽数都是 mdb 里的真实列，预测池用已确认段的众数兜底。
  天井萝卜 = 天井抽数 × 单抽成本（当前库是 200 × 150 = 30000）。
- **收入只能由用户填**：库里没有任何萝卜收入数据，这里不做估算，只按用户填的月收入
  线性折算到每个目标池开始的那天（30 天/月）。
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.kernel.config import Settings
from app.kernel.database import Database
from app.kernel.times import local_iso, system_zone, zone_label

from . import sources, store
from .fetcher import Fetcher, FetchError, WikiFetcher
from .importer import (
    SENTINEL_TS,
    ConfirmedSummary,
    MdbSourceError,
    read_confirmed,
    source_signature,
)
from .schema import ensure_schema
from .settings import PlannerSettings

logger = logging.getLogger(__name__)

CARD_TYPE_LABELS = {1: "马娘", 2: "协助卡"}
SEGMENT_LABELS = {
    "confirmed": "mdb 已确认",
    "predicted": "wiki 预测",
    "overlap": "与 mdb 重叠",
}
BASIS_LABELS = {"活动卡池": "活动卡池时间", "偏移推算": "偏移推算"}
SOURCE_LEVEL_LABELS = {
    "A": "官方原文（本机 master.mdb）",
    "B": "权威二手",
    "C": "社区整理 / 推算，可能已过期",
    "D": "占位或来源不明",
}

NOTES = (
    "已确认段来自本机 master.mdb 的 gacha_data，是客户端内置排期，"
    "游戏临时调整而客户端没更新时会对不上。",
    "预测段的简中时间是 BWiki 自己算好写在页面上的，属于社区推算（C 级），不是官方公告。",
    "活动表只有日服时间：能对上同名「活动卡池」的用卡池的简中时间，"
    "其余按卡池反推的偏移换算，标「偏移推算」。",
    "预测段里与 mdb 已确认段重叠的行单独标出，只展示、不参与规划，避免同一期被算两次。",
    "天井萝卜 = 天井抽数 × 单抽成本，两者都取自 master.mdb"
    "（gacha_exchange.pay_item_num / gacha_data.cost_single）。",
)


class PlannerService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        options: PlannerSettings,
        data_dir: Path,
        *,
        fetcher: Fetcher | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.options = options
        self.data_dir = data_dir
        self.logger = logger_ or logger
        ensure_schema(db)
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

    @property
    def mdb_path(self) -> Path:
        return self.settings.mdb_path

    # -------------------------------------------------------------- confirmed
    def ensure_catalog(self) -> ConfirmedSummary | None:
        """启动时补一次已确认段导入；mdb 没变就不重复读。"""
        mdb = self.mdb_path
        if not mdb.exists():
            return None
        meta = store.get_meta(self.db, store.META_MDB_SOURCE)
        signature = source_signature(mdb)
        if (
            store.counts(self.db)["confirmed"]
            and meta
            and meta.get("sha256") == signature["sha256"]
        ):
            return None
        return self._import_confirmed(signature)

    def _import_confirmed(self, signature: dict[str, Any]) -> ConfirmedSummary:
        summary = read_confirmed(
            self.db,
            self.mdb_path,
            default_cost_single=self.options.default_cost_single,
            default_ceiling_pulls=self.options.default_ceiling_pulls,
        )
        store.set_meta(
            self.db,
            store.META_MDB_SOURCE,
            {**signature, "imported_at": store.now_iso(), "pools": summary.pools},
        )
        return summary

    # ------------------------------------------------------------------ fetch
    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        """重新导入 mdb 已确认段，并重抓两条 BWiki 来源。"""
        result: dict[str, Any] = {"imported": False, "sources": [], "counts": {}}
        mdb = self.mdb_path
        if mdb.exists():
            signature = source_signature(mdb)
            meta = store.get_meta(self.db, store.META_MDB_SOURCE)
            if force or not meta or meta.get("sha256") != signature["sha256"]:
                try:
                    summary = self._import_confirmed(signature)
                except MdbSourceError as exc:
                    result["mdb_error"] = str(exc)
                else:
                    result["imported"] = True
                    result["mdb"] = summary.as_dict()
        else:
            result["mdb_error"] = f"找不到 master.mdb：{mdb}"

        pools_payload = self._fetch(sources.POOL_QUERY, force=force, result=result)
        events_payload = self._fetch(sources.EVENT_QUERY, force=force, result=result)

        pool_rows = sources.parse_pools(pools_payload.payload) if pools_payload else []
        event_rows = sources.parse_events(events_payload.payload) if events_payload else []
        if pool_rows:
            written = self._write_forecast(pool_rows, event_rows)
            result["counts"] = written
        elif not events_payload:
            store.clear_wiki(self.db)
            result["counts"] = store.counts(self.db)

        store.set_meta(
            self.db,
            store.META_LAST_REFRESH,
            {
                "refreshed_at": store.now_iso(),
                "pools": len(pool_rows),
                "events": len(event_rows),
            },
        )
        result["counts"] = store.counts(self.db)
        return result

    def _fetch(self, query: str, *, force: bool, result: dict[str, Any]):
        label = "预测卡池" if query == sources.POOL_QUERY else "活动表"
        try:
            fetched = self.fetcher.get(
                sources.ask_query(query, self.options.max_results), force=force
            )
        except FetchError as exc:
            self.logger.warning("%s 抓取失败：%s", label, exc)
            result["sources"].append({"label": label, "ok": False, "error": str(exc)})
            return None
        result["sources"].append(
            {
                "label": label,
                "ok": True,
                "cached": fetched.from_cache,
                "stale": fetched.stale,
                "fetched_at": fetched.fetched_at,
            }
        )
        return fetched

    # ------------------------------------------------------------------ write
    def _write_forecast(
        self, pool_rows: list[sources.PredictedPool], event_rows: list[sources.JpEvent]
    ) -> dict[str, int]:
        horizon = self._confirmed_horizon()
        offset_map = sources.OffsetMap.build(pool_rows)
        unit = self._cost_unit()
        fetched_at = store.now_iso()

        pool_payload: list[tuple[Any, ...]] = []
        for pool in pool_rows:
            segment = (
                "overlap" if horizon and int(pool.start.timestamp()) <= horizon else "predicted"
            )
            card_type = None
            if pool.ids.get("角色ID") and not pool.ids.get("支援卡ID"):
                card_type = 1
            elif pool.ids.get("支援卡ID") and not pool.ids.get("角色ID"):
                card_type = 2
            pool_payload.append(
                (
                    pool.uid,
                    "wiki-predicted",
                    segment,
                    pool.title,
                    card_type,
                    None,
                    int(pool.start.timestamp()),
                    int(pool.end.timestamp()) if pool.end else None,
                    int(pool.jp_start.timestamp()) if pool.jp_start else None,
                    unit["cost_single"],
                    unit["ceiling_pulls"],
                    "",
                    json.dumps(pool.ids, ensure_ascii=False, sort_keys=True),
                    "C",
                    pool.source_url,
                    fetched_at,
                )
            )

        event_payload = self._event_rows(event_rows, pool_rows, offset_map, fetched_at)
        store.replace_wiki(self.db, pool_payload, event_payload)
        store.set_meta(
            self.db,
            store.META_OFFSET,
            {
                "median_days": offset_map.median_days,
                "spread": offset_map.spread_days,
                "pairs": len(offset_map.points),
                "computed_at": fetched_at,
            },
        )
        return store.counts(self.db)

    def _event_rows(
        self,
        event_rows: list[sources.JpEvent],
        pools: list[sources.PredictedPool],
        offset_map: sources.OffsetMap,
        fetched_at: str,
    ) -> list[tuple[Any, ...]]:
        payload: list[tuple[Any, ...]] = []
        for event in event_rows:
            if event.jp_start is None:
                continue
            matched = sources.match_event_pool(event.title, pools)
            basis = "活动卡池" if matched is not None else "偏移推算"
            start = matched.start if matched else None
            end = matched.end if matched else None
            delta: float | None = matched.delta_days if matched else None
            if start is None:
                if not offset_map.known:
                    continue
                start = offset_map.to_cn(event.jp_start)
                delta = offset_map.delta_days(event.jp_start)
            if end is None and event.jp_end is not None:
                end = offset_map.to_cn(event.jp_end) if offset_map.known else None
            payload.append(
                (
                    event.uid,
                    event.title,
                    event.kind,
                    int(event.jp_start.timestamp()),
                    int(event.jp_end.timestamp()) if event.jp_end else None,
                    int(start.timestamp()),
                    int(end.timestamp()) if end else None,
                    basis,
                    None if delta is None else round(delta),
                    matched.uid if matched else None,
                    "C",
                    event.source_url,
                    fetched_at,
                )
            )
        return payload

    # -------------------------------------------------------------- internals
    def _confirmed_horizon(self) -> int | None:
        rows = store.list_pools(self.db, segment="confirmed")
        ends = [int(row["end_ts"]) for row in rows if row["end_ts"]]
        return max(ends) if ends else None

    def _cost_unit(self) -> dict[str, Any]:
        """预测池的成本口径：取已确认段出现最多的单抽成本与天井抽数。"""
        rows = store.list_pools(self.db, segment="confirmed")
        costs = [int(row["cost_single"]) for row in rows if row["cost_single"]]
        ceilings = [int(row["ceiling_pulls"]) for row in rows if row["ceiling_pulls"]]
        cost_single = int(statistics.mode(costs)) if costs else self.options.default_cost_single
        ceiling = int(statistics.mode(ceilings)) if ceilings else self.options.default_ceiling_pulls
        return {
            "cost_single": cost_single,
            "ceiling_pulls": ceiling,
            "ceiling_carats": cost_single * ceiling,
        }

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        counts = store.counts(self.db)
        last = store.get_meta(self.db, store.META_LAST_REFRESH) or {}
        offset = store.get_meta(self.db, store.META_OFFSET) or {}
        mdb_meta = store.get_meta(self.db, store.META_MDB_SOURCE) or {}
        unit = self._cost_unit()
        mdb = self.mdb_path
        signature = source_signature(mdb) if mdb.exists() else None
        return {
            "counts": counts,
            "sources": [
                {
                    "key": key,
                    "label": label,
                    "level": "A" if key == "mdb" else "C",
                }
                for key, label in sources.SOURCE_LABELS.items()
            ],
            "refreshed_at": last.get("refreshed_at"),
            "offset": offset,
            "cost": {
                **unit,
                "source": "master.mdb gacha_data.cost_single / gacha_exchange.pay_item_num",
            },
            "mdb": {
                "path": str(mdb),
                "exists": mdb.exists(),
                "imported_at": mdb_meta.get("imported_at"),
                "pools": counts["confirmed"],
                "stale": bool(signature and mdb_meta.get("sha256") != signature["sha256"]),
            },
            "window": {"days": self.options.horizon_days},
            "zone": zone_label(system_zone()),
        }

    # --------------------------------------------------------------- forecast
    def forecast(self, days: int | None = None, *, include_overlap: bool = True) -> dict[str, Any]:
        window_days = days or self.options.horizon_days
        zone = system_zone()
        now = datetime.now(UTC)
        window_end = now + timedelta(days=window_days)
        end_stamp = int(window_end.timestamp())
        start_stamp = int((now - timedelta(days=7)).timestamp())

        pools = [self._pool_item(row, now) for row in store.list_pools(self.db)]
        pools = [item for item in pools if item is not None]
        pools = [
            item
            for item in pools
            if item["start_ts"] <= end_stamp
            and (item["end_ts"] or item["start_ts"]) >= start_stamp
            and (include_overlap or item["segment"] != "overlap")
        ]
        events = [
            self._event_item(row, now)
            for row in store.list_events(self.db)
            if row["start_ts"] and start_stamp <= int(row["start_ts"]) <= end_stamp
        ]
        counts = store.counts(self.db)
        offset = store.get_meta(self.db, store.META_OFFSET) or {}
        last = store.get_meta(self.db, store.META_LAST_REFRESH) or {}
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "window": {
                "days": window_days,
                "from_local": local_iso(now.timestamp()),
                "to_local": local_iso(window_end.timestamp()),
                "zone": zone_label(zone),
            },
            "horizon": {
                "confirmed_until_local": local_iso(self._confirmed_horizon()),
                "note": "已确认段覆盖到这里；之后的行是 BWiki 预测。",
            },
            "offset": {
                "median_days": offset.get("median_days"),
                "spread": offset.get("spread"),
                "pairs": offset.get("pairs"),
                "computed_at": offset.get("computed_at"),
            },
            "cost": self._cost_unit(),
            "counts": {
                **counts,
                "shown_pools": len(pools),
                "shown_events": len(events),
            },
            "pools": pools,
            "events": events,
            "notes": list(NOTES),
            "refreshed_at": last.get("refreshed_at"),
        }

    def _pool_item(self, row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        start_ts = int(row["start_ts"])
        end_ts = int(row["end_ts"]) if row["end_ts"] else None
        if start_ts >= SENTINEL_TS:
            return None
        stamp = int(now.timestamp())
        if end_ts is not None and end_ts < stamp:
            state = "ended"
        elif start_ts > stamp:
            state = "upcoming"
        else:
            state = "active"
        cost_single = int(row["cost_single"] or self.options.default_cost_single)
        ceiling = int(row["ceiling_pulls"] or self.options.default_ceiling_pulls)
        card_type = row["card_type"]
        return {
            "uid": row["uid"],
            "segment": row["segment"],
            "segment_label": SEGMENT_LABELS.get(row["segment"], row["segment"]),
            "title": row["title"],
            "card_type": card_type,
            "card_type_label": CARD_TYPE_LABELS.get(card_type, "—"),
            "gacha_type": row["gacha_type"],
            "start_ts": start_ts,
            "end_ts": end_ts,
            "starts_local": local_iso(start_ts),
            "ends_local": local_iso(end_ts),
            "jp_starts_local": local_iso(row["jp_start_ts"]),
            "offset_days": (
                round((start_ts - int(row["jp_start_ts"])) / 86400) if row["jp_start_ts"] else None
            ),
            "state": state,
            "days_to_start": _days_between(start_ts),
            "days_left": _days_between(end_ts) if end_ts else None,
            "pickups": [item for item in (row["pickups"] or "").split("、") if item],
            "cost_single": cost_single,
            "ceiling_pulls": ceiling,
            "ceiling_carats": cost_single * ceiling,
            "source_level": row["source_level"],
            "source_url": row["source_url"],
        }

    def _event_item(self, row: dict[str, Any], now: datetime) -> dict[str, Any]:
        start_ts = int(row["start_ts"])
        end_ts = int(row["end_ts"]) if row["end_ts"] else None
        stamp = int(now.timestamp())
        if end_ts is not None and end_ts < stamp:
            state = "ended"
        elif start_ts > stamp:
            state = "upcoming"
        else:
            state = "active"
        return {
            "uid": row["uid"],
            "title": row["title"],
            "kind": row["kind"],
            "starts_local": local_iso(start_ts),
            "ends_local": local_iso(end_ts),
            "jp_starts_local": local_iso(row["jp_start_ts"]),
            "offset_days": row["offset_days"],
            "basis": row["basis"],
            "basis_label": BASIS_LABELS.get(row["basis"], row["basis"]),
            "matched_uid": row["matched_uid"],
            "state": state,
            "days_to_start": _days_between(start_ts),
            "days_left": _days_between(end_ts) if end_ts else None,
            "source_level": row["source_level"],
            "source_url": row["source_url"],
        }

    # ----------------------------------------------------------------- plans
    def evaluate(
        self, plan: dict[str, Any], forecast: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = forecast or self.forecast()
        pools = {item["uid"]: item for item in data["pools"]}
        carats = int(plan.get("carats") or 0)
        tickets = int(plan.get("tickets") or 0)
        income = int(plan.get("monthly_income") or 0)
        per_day = income / 30.0
        default_pulls = int(data["cost"]["ceiling_pulls"])

        targets: list[dict[str, Any]] = []
        unknown = 0
        for target in plan.get("targets") or []:
            pool = pools.get(str(target.get("pool_uid")))
            if pool is None:
                unknown += 1
                continue
            targets.append({**target, "_pool": pool})
        targets.sort(key=lambda item: (item["_pool"]["start_ts"], int(item.get("priority") or 1)))

        now = datetime.now(UTC)
        spent = 0
        rows: list[dict[str, Any]] = []
        shortfall_total = 0
        affordable_count = 0
        income_at_last = 0.0
        for target in targets:
            pool = target["_pool"]
            pulls = int(target.get("pulls") or 0) or default_pulls
            wait_days = max((int(pool["start_ts"]) - int(now.timestamp())) / 86400.0, 0.0)
            gained = per_day * wait_days
            income_at_last = max(income_at_last, gained)
            available = carats + gained - spent
            ticket_used = min(max(tickets, 0), pulls)
            need = (pulls - ticket_used) * int(pool["cost_single"])
            affordable = available >= need
            if affordable:
                spent += need
                tickets -= ticket_used
                affordable_count += 1
                shortfall = 0
                balance = available - need
            else:
                shortfall = need - available
                shortfall_total += shortfall
                balance = available
            rows.append(
                {
                    "pool_uid": pool["uid"],
                    "title": pool["title"],
                    "segment": pool["segment"],
                    "starts_local": pool["starts_local"],
                    "days_to_start": round(wait_days),
                    "state": pool["state"],
                    "priority": int(target.get("priority") or 1),
                    "pulls": pulls,
                    "tickets_used": ticket_used,
                    "cost_carats": need,
                    "available_carats": int(available),
                    "shortfall_carats": int(shortfall),
                    "affordable": affordable,
                    "balance_after": int(balance),
                }
            )

        notes = [
            "月收入按 30 天/月线性折算到每个目标池开始的那一天，不按周结算。",
            "券按 1 张 = 1 抽抵扣并优先使用；不区分有偿 / 无偿，也不折算成萝卜。",
            "买不起的目标标「缺口」并且不扣账（视为放弃、把萝卜留给后面的池子）；想改可以先降抽数。",
        ]
        if unknown:
            notes.append(f"有 {unknown} 个目标池已不在当前预测窗口里（可能已刷新或过期），已跳过。")
        return {
            "plan": {
                "name": plan.get("name") or "",
                "carats": carats,
                "tickets": int(plan.get("tickets") or 0),
                "monthly_income": income,
            },
            "rows": rows,
            "totals": {
                "targets": len(rows),
                "affordable": affordable_count,
                "shortfall_carats": int(shortfall_total),
                "spent_carats": int(spent),
                "income_carats": int(income_at_last),
                "ending_carats": int(carats + income_at_last - spent),
                "ending_tickets": int(tickets),
            },
            "notes": notes,
        }

    def plans(self) -> list[dict[str, Any]]:
        return store.list_plans(self.db)

    def plan(self, plan_id: int) -> dict[str, Any] | None:
        return store.get_plan(self.db, plan_id)


def _days_between(stamp: int | None) -> int | None:
    if stamp is None:
        return None
    zone = system_zone()
    today = datetime.now(zone).date()
    target = datetime.fromtimestamp(int(stamp), tz=zone).date()
    return (target - today).days
