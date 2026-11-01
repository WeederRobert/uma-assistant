"""规划模块的业务逻辑：千里眼窗口、预测校正与萝卜规划计算。

口径都写在这里，面板只做展示：

- **已确认段**取 ``master.mdb``：卡池来自 ``gacha_data``（含未来），剧情活动来自
  ``story_event_data``。两者都是客户端内置的**简中服实际排期**（A 级），当前快照覆盖到
  2027-01-26（卡池）/ 2026-12-26（剧情活动）。这一段不需要任何跨服换算。
- **预测段**取 BWiki 的「预测卡池」。wiki 的简中时间按「日服 + 固定偏移」推算，
  **简中服处于加速期，这条轴已经不准**：2026-11-01 用 mdb 实测对齐，wiki 系统性偏晚
  35~50 天且仍在扩大。所以这里不直接采用 wiki 的日期，而是用 mdb 的已确认段做**偏差校正**
  （按精选卡 id 一对一配对，取偏差中位数平移），并把偏差区间一并回显。
- **活动不做跨服换算**：``分类:活动`` 只有日服时间，且简中服的活动顺序与日服并不一致，
  对轴没有意义；剧情活动直接用 mdb 的排期。育成活动（竞速嘉年华等）没有可核对的简中排期，
  不展示。
- **成本**：单抽成本（``gacha_data.cost_single``）与天井抽数（``gacha_exchange.pay_item_num``）
  都是 mdb 的真实列，预测池用已确认段的众数兜底。天井萝卜 = 天井抽数 × 单抽成本。
- **收入只能由用户填**：库里没有任何萝卜收入数据，这里只按用户填的月收入线性折算（30 天/月）。
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
    "predicted": "wiki 预测（已校正）",
    "overlap": "与 mdb 重叠",
}

NOTES = (
    "已确认段来自本机 master.mdb：卡池看 gacha_data、剧情活动看 story_event_data，"
    "都是简中服客户端的实际排期，不需要跨服换算。",
    "预测段的卡池来自 BWiki 的「简中::未实装」表；wiki 按「日服 + 固定偏移」推算，"
    "简中服加速后已经不准（实测偏晚 35~50 天并继续扩大），所以这里用 mdb 实测偏差做了平移校正。",
    "偏差用「同一张精选卡」在 mdb 已确认段与 wiki 预测段之间一对一配对算出，"
    "校正后仍有误差，偏差区间见上方说明。",
    "剧情活动直接用 mdb 的排期；育成活动（竞速嘉年华、大师挑战赛等）没有可核对的简中排期，不展示。",
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
        counts = store.counts(self.db)
        if (
            counts["confirmed"]
            and counts["events"]
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
        """重新导入 mdb 已确认段，并重抓 BWiki 预测卡池。"""
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

        fetched = self._fetch(force=force, result=result)
        pool_rows = sources.parse_pools(fetched.payload) if fetched else []
        if pool_rows:
            result["counts"] = self._write_forecast(pool_rows)
        elif fetched is not None:
            store.clear_wiki(self.db)
            result["counts"] = store.counts(self.db)

        store.set_meta(
            self.db,
            store.META_LAST_REFRESH,
            {"refreshed_at": store.now_iso(), "pools": len(pool_rows)},
        )
        result["counts"] = store.counts(self.db)
        result["calibration"] = self._calibration()
        return result

    def _fetch(self, *, force: bool, result: dict[str, Any]):
        try:
            fetched = self.fetcher.get(
                sources.ask_query(sources.POOL_QUERY, self.options.max_results), force=force
            )
        except FetchError as exc:
            self.logger.warning("预测卡池抓取失败：%s", exc)
            result["sources"].append({"label": "预测卡池", "ok": False, "error": str(exc)})
            return None
        result["sources"].append(
            {
                "label": "预测卡池",
                "ok": True,
                "cached": fetched.from_cache,
                "stale": fetched.stale,
                "fetched_at": fetched.fetched_at,
            }
        )
        return fetched

    # ------------------------------------------------------------------ write
    def _write_forecast(self, pool_rows: list[sources.PredictedPool]) -> dict[str, int]:
        horizon = self._confirmed_horizon()
        unit = self._cost_unit()
        fetched_at = store.now_iso()
        payload: list[tuple[Any, ...]] = []
        for pool in pool_rows:
            segment = (
                "overlap" if horizon and int(pool.start.timestamp()) <= horizon else "predicted"
            )
            payload.append(
                (
                    pool.uid,
                    "wiki-predicted",
                    segment,
                    pool.title,
                    None,
                    None,
                    int(pool.start.timestamp()),
                    int(pool.end.timestamp()) if pool.end else None,
                    int(pool.jp_start.timestamp()) if pool.jp_start else None,
                    unit["cost_single"],
                    unit["ceiling_pulls"],
                    "",
                    json.dumps(pool.ids, ensure_ascii=False),
                    "C",
                    pool.source_url,
                    fetched_at,
                )
            )
        store.replace_wiki(self.db, payload)
        return store.counts(self.db)

    # ------------------------------------------------------------ calibration
    def _calibration(self) -> dict[str, Any]:
        """用「同一期马娘池的精选角色」在 mdb 已确认段与 wiki 预测段之间配对，量出 wiki 偏晚多少天。

        两边都按**角色 id 集合**比：mdb 侧只取马娘池（``card_type = 1``）的精选卡，
        wiki 侧只取 ``角色ID``（不含支援卡），集合完全相等且该组合在 mdb 里只出现一次才算一对。
        八骏 / 必得这类多卡池与自选池会被排除，避免样本被搅浑。
        """
        confirmed = store.list_pools(self.db, segment="confirmed")
        overlap = store.list_pools(self.db, segment="overlap")
        by_cards: dict[frozenset[str], list[dict[str, Any]]] = {}
        for row in confirmed:
            if row["card_type"] != 1:
                continue
            cards = _ids_of(row).get("card_ids") or []
            if not cards:
                continue
            by_cards.setdefault(frozenset(cards), []).append(row)

        samples: list[dict[str, Any]] = []
        for row in overlap:
            chars = _ids_of(row).get("char_ids") or []
            if not chars:
                continue
            matches = by_cards.get(frozenset(chars)) or []
            if len(matches) != 1:
                continue
            bias = (int(row["start_ts"]) - int(matches[0]["start_ts"])) / 86400
            samples.append(
                {
                    "card_ids": chars,
                    "title": row["title"],
                    "wiki_start_ts": int(row["start_ts"]),
                    "actual_start_ts": int(matches[0]["start_ts"]),
                    "bias_days": round(bias, 1),
                    "actual_title": matches[0]["title"],
                }
            )
        if not samples:
            return {
                "pairs": 0,
                "bias_days": 0,
                "min_days": None,
                "max_days": None,
                "samples": [],
                "note": "没有可与 mdb 对齐的样本，预测段未校正（wiki 原值可能偏晚 1~2 个月）。",
            }
        deltas = [sample["bias_days"] for sample in samples]
        return {
            "pairs": len(samples),
            "bias_days": round(statistics.median(deltas), 1),
            "min_days": min(deltas),
            "max_days": max(deltas),
            "samples": sorted(samples, key=lambda item: item["actual_start_ts"]),
            "note": (
                "偏差 = wiki 预测日期 − mdb 实际日期；取中位数做平移校正。"
                "简中服加速时偏差会随时间扩大，越远的预测误差越大。"
            ),
        }

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
        mdb_meta = store.get_meta(self.db, store.META_MDB_SOURCE) or {}
        mdb = self.mdb_path
        signature = source_signature(mdb) if mdb.exists() else None
        return {
            "counts": counts,
            "sources": [
                {"key": key, "label": label, "level": "A" if key == "mdb" else "C"}
                for key, label in sources.SOURCE_LABELS.items()
            ],
            "refreshed_at": last.get("refreshed_at"),
            "calibration": self._calibration(),
            "cost": {
                **self._cost_unit(),
                "source": "master.mdb gacha_data.cost_single / gacha_exchange.pay_item_num",
            },
            "mdb": {
                "path": str(mdb),
                "exists": mdb.exists(),
                "imported_at": mdb_meta.get("imported_at"),
                "pools": counts["confirmed"],
                "events": counts["events"],
                "stale": bool(signature and mdb_meta.get("sha256") != signature["sha256"]),
            },
            "window": {"days": self.options.horizon_days},
            "zone": zone_label(system_zone()),
        }

    # --------------------------------------------------------------- forecast
    def forecast(self, days: int | None = None, *, include_overlap: bool = False) -> dict[str, Any]:
        window_days = days or self.options.horizon_days
        now = datetime.now(UTC)
        end_stamp = int((now + timedelta(days=window_days)).timestamp())
        start_stamp = int((now - timedelta(days=7)).timestamp())
        calibration = self._calibration()
        bias_days = float(calibration.get("bias_days") or 0)

        items = [self._pool_item(row, now, bias_days) for row in store.list_pools(self.db)]
        pools = [
            item
            for item in items
            if item is not None
            and item["start_ts"] <= end_stamp
            and (item["end_ts"] or item["start_ts"]) >= start_stamp
            and (include_overlap or item["segment"] != "overlap")
        ]
        events = [
            self._event_item(row, now)
            for row in store.list_events(self.db)
            if row["start_ts"] and start_stamp <= int(row["start_ts"]) <= end_stamp
        ]
        counts = store.counts(self.db)
        last = store.get_meta(self.db, store.META_LAST_REFRESH) or {}
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "window": {
                "days": window_days,
                "from_local": local_iso(now.timestamp()),
                "to_local": local_iso(now.timestamp() + window_days * 86400),
                "zone": zone_label(system_zone()),
            },
            "horizon": {
                "confirmed_until_local": local_iso(self._confirmed_horizon()),
                "note": "已确认段覆盖到这里；之后的行是 BWiki 预测，且已按 mdb 实测偏差平移校正。",
            },
            "calibration": calibration,
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

    def _pool_item(
        self, row: dict[str, Any], now: datetime, bias_days: float
    ) -> dict[str, Any] | None:
        raw_start = int(row["start_ts"])
        if raw_start >= SENTINEL_TS:
            return None
        raw_end = int(row["end_ts"]) if row["end_ts"] else None
        segment = row["segment"]
        # 预测段的日期按实测偏差平移；已确认段与重叠段不动（重叠段本身就是 mdb 覆盖期内的 wiki 值）
        correction = round(bias_days) if segment == "predicted" else 0
        start_ts = raw_start - correction * 86400
        end_ts = (raw_end - correction * 86400) if raw_end else None
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
            "segment": segment,
            "segment_label": SEGMENT_LABELS.get(segment, segment),
            "title": row["title"],
            "card_type": card_type,
            "card_type_label": CARD_TYPE_LABELS.get(card_type, "—"),
            "gacha_type": row["gacha_type"],
            "start_ts": start_ts,
            "end_ts": end_ts,
            "starts_local": local_iso(start_ts),
            "ends_local": local_iso(end_ts),
            "source_starts_local": local_iso(raw_start),
            "correction_days": correction,
            "jp_starts_local": local_iso(row["jp_start_ts"]),
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
                    "correction_days": pool["correction_days"],
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
            "买不起的目标标「缺口」并且不扣账（视为放弃、把萝卜留给后面的池子）。",
            "预测池的开始时间已按 mdb 实测偏差平移，仍有误差；越远的池子越不确定。",
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


def _ids_of(row: dict[str, Any]) -> dict[str, list[str]]:
    try:
        payload = json.loads(row.get("ids") or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: [str(item) for item in value]
        for key, value in payload.items()
        if isinstance(value, list)
    }


def _days_between(stamp: int | None) -> int | None:
    if stamp is None:
        return None
    zone = system_zone()
    today = datetime.now(zone).date()
    target = datetime.fromtimestamp(int(stamp), tz=zone).date()
    return (target - today).days
