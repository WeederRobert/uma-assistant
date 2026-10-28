"""卡池模块的业务逻辑：导入、分组与预览。

口径都写在这里，面板只做展示：

- 数据来自 ``master.mdb`` 的 ``gacha_data``（客户端**内置排期**，含未来卡池），
  所以「下一期是什么」不用等外部公告。
- ``gacha_data.type`` 与 ``card_type`` 没有字典表：``card_type`` 的 1 / 2 由
  「能否 JOIN 到 ``card_data`` / ``support_card_data``」验证（100% / 99.5%），
  可以标成马娘 / 协助卡；``type`` 只能回显原始编号。
- 时间都是 unix 秒，按运行环境的系统时区展示（见 :mod:`app.kernel.times`）。
- 未来卡池只是客户端排期，游戏临时调整而客户端没更新时会与实际情况不符，
  面板必须写明这一点。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.kernel.config import Settings
from app.kernel.database import Database
from app.kernel.dataversion import read_data_version
from app.kernel.times import local_iso, system_zone, zone_label

from . import store
from .importer import SENTINEL_TS, CatalogSummary, GachaSourceError, import_catalog
from .schema import ensure_schema

CARD_TYPE_LABELS = {1: "马娘", 2: "协助卡"}

# 客户端用 2050-01-01 表示「不结束」；start_date 也落在 2049/2050 的记录是占位（还没定档），
# 两者都会算出「还有几千天开始 / 还剩几千天」，必须单独识别（阈值与导入器共用）
_SENTINEL_TS = SENTINEL_TS
# 跨度超过它就按「常驻」处理：新人出道这类池子的 end_date 写在 2050 年
_PERMANENT_SECONDS = 5 * 365 * 86400

NO_DICT_NOTE = (
    "`gacha_data.type` 在 master.mdb 里没有字典表，面板只回显「类型 N」，不猜它的含义"
    "（同样是精选池也会出现不同编号）。"
)
SCHEDULE_NOTE = (
    "这里是**客户端内置的排期**：未来卡池与游戏公告一致时会提前出现在列表里，"
    "但游戏临时改动而客户端没更新时可能对不上——以游戏内公告为准。"
)
SENTINEL_NOTE = (
    "客户端用 `2050-01-01` 表示「不结束」：结束时间写在这一天的是**常驻池**（面板标「常驻」）；"
    "开始时间也落在 2049/2050 的记录是**未定档占位**，单独列、不混进「即将开始」。"
)


class GachaService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        options: Any | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.options = options
        self.logger = logger or logging.getLogger(__name__)
        ensure_schema(db)

    # ------------------------------------------------------------------ 来源
    @property
    def mdb_path(self) -> Path:
        override = getattr(self.options, "mdb_path", None) if self.options else None
        return Path(override) if override else self.settings.mdb_path

    # ------------------------------------------------------------------ 导入
    def ensure_catalog(self) -> CatalogSummary | None:
        mdb = self.mdb_path
        if not mdb.exists():
            return None
        source = store.catalog_source(self.db)
        if store.count_banners(self.db) and not self._stale(mdb, source):
            return None
        try:
            return import_catalog(self.db, mdb)
        except GachaSourceError as exc:
            self.logger.warning("导入卡池排期失败：%s", exc)
            return None

    @staticmethod
    def _stale(mdb: Path, source: dict[str, Any] | None) -> bool:
        if not source:
            return True
        recorded = source.get("mdb_mtime")
        if recorded is None or source.get("mdb_path") != str(mdb.resolve()):
            return True
        try:
            return mdb.stat().st_mtime > float(recorded) + 1
        except (OSError, TypeError, ValueError):
            return True

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        mdb = self.mdb_path
        if not mdb.exists():
            raise GachaSourceError(
                f"找不到 master.mdb：{mdb}。先在「数据同步」里导入一次客户端数据。"
            )
        if not force and not self._stale(mdb, store.catalog_source(self.db)):
            return {
                "imported": False,
                "reason": "本地卡池排期已对应当前 master.mdb，未重复导入",
                "counts": store.name_counts(self.db),
            }
        summary = import_catalog(self.db, mdb)
        return {
            "imported": True,
            "summary": summary.as_dict(),
            "counts": store.name_counts(self.db),
        }

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        mdb = self.mdb_path
        source = store.catalog_source(self.db)
        counts = store.name_counts(self.db)
        return {
            "counts": counts,
            "catalog": source,
            "stale": bool(counts["banners"]) and self._stale(mdb, source),
            "mdb": {
                "path": str(mdb),
                "exists": mdb.exists(),
                "size_mb": round(mdb.stat().st_size / 1024 / 1024, 1) if mdb.exists() else None,
            },
            "zone": zone_label(system_zone()),
            "card_types": [
                {"value": value, "label": label} for value, label in CARD_TYPE_LABELS.items()
            ],
            "data_version": read_data_version(self.db).as_dict(),
            "notes": [NO_DICT_NOTE, SCHEDULE_NOTE, SENTINEL_NOTE],
        }

    # ------------------------------------------------------------------ 列表
    def banners(self, *, include_ended: bool = True, limit_ended: int = 20) -> dict[str, Any]:
        now = datetime.now(UTC)
        pickups = store.pickups_by_gacha(self.db)
        items = [
            self._row(row, now, pickups.get(int(row["gacha_id"]), []))
            for row in store.list_banners(self.db)
        ]

        active = [item for item in items if item["state"] == "active"]
        upcoming = [item for item in items if item["state"] == "upcoming"]
        ended = [item for item in items if item["state"] == "ended"]
        unscheduled = [item for item in items if item["state"] == "unscheduled"]
        ended.sort(key=lambda item: item["end_ts"] or 0, reverse=True)

        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "zone": zone_label(system_zone()),
            "counts": {
                "total": len(items),
                "active": len(active),
                "upcoming": len(upcoming),
                "ended": len(ended),
                "unscheduled": len(unscheduled),
                "permanent": sum(1 for item in active if item["permanent"]),
                "with_pickups": sum(1 for item in items if item["pickups"]),
            },
            "active": active,
            "upcoming": upcoming,
            "ended": ended[: max(0, limit_ended)] if include_ended else [],
            "unscheduled": unscheduled,
            "notes": [NO_DICT_NOTE, SCHEDULE_NOTE, SENTINEL_NOTE],
        }

    def banner(self, gacha_id: int) -> dict[str, Any] | None:
        row = store.get_banner(self.db, gacha_id)
        if row is None:
            return None
        now = datetime.now(UTC)
        return self._row(row, now, store.list_pickups(self.db, gacha_id))

    def _row(self, row: Any, now: datetime, pickups: list[Any]) -> dict[str, Any]:
        start_ts = row["start_ts"]
        end_ts = row["end_ts"]
        start = datetime.fromtimestamp(start_ts, UTC) if start_ts else None
        end = datetime.fromtimestamp(end_ts, UTC) if end_ts else None

        # start_date 就落在哨兵年份的记录是「未定档」占位，不能进「即将开始」（会显示还有几千天）
        unscheduled = bool(start_ts and start_ts >= _SENTINEL_TS)
        # 常驻池：结束时间写在 2050 年（哨兵）且还没结束，别显示「还剩 8000 天」
        permanent = bool(
            end_ts
            and end_ts >= _SENTINEL_TS
            and end_ts > now.timestamp()
            and end_ts - (start_ts or 0) > _PERMANENT_SECONDS
        )

        if unscheduled:
            state = "unscheduled"
        elif start is not None and start > now:
            state = "upcoming"
        elif permanent:
            state = "active"
        elif end is not None and end < now:
            state = "ended"
        else:
            state = "active"

        return {
            "gacha_id": int(row["gacha_id"]),
            "title": row["title"],
            "description": row["description"],
            "type": row["type"],
            "type_label": f"类型 {row['type']}" if row["type"] is not None else "类型未知",
            "card_type": row["card_type"],
            "card_type_label": CARD_TYPE_LABELS.get(row["card_type"], "未分类"),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "starts_local": local_iso(start_ts),
            "ends_local": local_iso(end_ts),
            "state": state,
            "permanent": permanent,
            "days_to_start": (start - now).days if start else None,
            "days_left": (end - now).days if end else None,
            "pickups": [
                {
                    "card_type": int(item["card_type"]),
                    "card_type_label": CARD_TYPE_LABELS.get(item["card_type"], "未分类"),
                    "card_id": int(item["card_id"]),
                    "rarity": item["rarity"],
                    "name": item["name"],
                    "title": item["title"],
                }
                for item in pickups
            ],
        }
