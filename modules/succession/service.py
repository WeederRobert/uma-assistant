"""相性的业务逻辑：共享关系组 → 点数合计 → 等级。

口径全部来自 ``master.mdb`` 的 ``succession_relation*``，返回值里写明「算的是什么、没算什么」：

- **关系组没有名称**：``relation_type`` 只是编号，主数据里没有「室友 / 同冠名」这类字典，
  所以只回显编号、点数与该组成员数。
- **两人相性**只统计两人同时存在的关系组，点数之和就是「两人之间」的贡献；六人继承布局里
  它正好是 1 号位（父母之一）的贡献，而不是游戏内显示的最终相性。
- **六人布局**按社区计算器（BWiki「相性计算器计算方法」，见知识库 06）的分槽口径累加：
  1 号 = f(0,1)；2 号 = f(0,2) + f(1,2)；3/4 号 = f(0,1,祖父母)，与 0 号同角色时记 0；
  5/6 号 = f(0,2,祖父母)，同理。**这是社区口径，不是官方公式。**
- **不含适性（马场 / 距离 / 跑法契合）**：主数据里没有适性表（``%aptitude%`` 查不到，
  ``%proper%`` 只有赛事侧的出现率表），所以算不出来，也不假设计算。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.kernel.config import Settings
from app.kernel.database import Database
from app.kernel.dataversion import read_data_version

from . import store
from .importer import CatalogSummary, SuccessionSourceError, import_catalog
from .schema import ensure_schema

# 社区口径的等级名（BWiki 相性计算器 / 知识库 06）：rank 1 = △、2 = 〇、3 = ⌾
RANK_LABELS = {1: "△", 2: "〇", 3: "⌾"}
RANK_NOTES = {1: "0–50 分", 2: "51–150 分", 3: "151 分以上"}

NOTE_PAIR = (
    "这里统计的是**两只马娘同时存在的关系组的点数之和**，不含适性（马场 / 距离 / 跑法契合），"
    "也不含祖父母参与的关系组。六人继承布局里，这个值等于 1 号位（父母之一）的贡献。"
)
NOTE_LAYOUT = (
    "按社区计算器口径累加六个位置：1 号 = f(0,1)；2 号 = f(0,2) + f(1,2)；"
    "3/4 号 = f(0,1,祖父母)；5/6 号 = f(0,2,祖父母)，祖父母与目标同角色时该部分记 0 分。"
    "这是社区口径，不是官方公式；不含适性加成，也没有算「继承马娘赢过的 G1 有交集」这类额外项。"
)
NOTE_RELATIONS = (
    "`relation_type` 在主数据里没有名称字典（没有「室友 / 同冠名」这类文本），"
    "面板只显示编号、点数与该组人数。"
)


class SuccessionService:
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

    def ensure_catalog(self) -> CatalogSummary | None:
        mdb = self.mdb_path
        if not mdb.exists():
            return None
        source = store.catalog_source(self.db)
        if store.counts(self.db)["relations"] and not self._stale(mdb, source):
            return None
        try:
            return import_catalog(self.db, mdb)
        except SuccessionSourceError as exc:
            self.logger.warning("导入相性关系失败：%s", exc)
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
            raise SuccessionSourceError(
                f"找不到 master.mdb：{mdb}。先在「数据同步」里导入一次客户端数据。"
            )
        if not force and not self._stale(mdb, store.catalog_source(self.db)):
            return {
                "imported": False,
                "reason": "本地相性关系已对应当前 master.mdb，未重复导入",
                "counts": store.counts(self.db),
            }
        summary = import_catalog(self.db, mdb)
        return {"imported": True, "summary": summary.as_dict(), "counts": store.counts(self.db)}

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        mdb = self.mdb_path
        source = store.catalog_source(self.db)
        counts = store.counts(self.db)
        return {
            "counts": counts,
            "catalog": source,
            "stale": bool(counts["relations"]) and self._stale(mdb, source),
            "mdb": {
                "path": str(mdb),
                "exists": mdb.exists(),
                "size_mb": round(mdb.stat().st_size / 1024 / 1024, 1) if mdb.exists() else None,
            },
            "ranks": self._ranks(),
            "data_version": read_data_version(self.db).as_dict(),
            "notes": [NOTE_PAIR, NOTE_LAYOUT, NOTE_RELATIONS],
        }

    # ------------------------------------------------------------------ 查询
    def charas(self, query: str | None = None, limit: int = 500) -> dict[str, Any]:
        items = store.list_charas(self.db, query, max(1, min(limit, 500)))
        return {"items": items, "count": len(items)}

    def pair(self, chara_a: int, chara_b: int) -> dict[str, Any]:
        if chara_a == chara_b:
            raise ValueError("两只马娘不能是同一角色（衣装不同也算同一角色），换一个再算。")
        first = self._require(chara_a)
        second = self._require(chara_b)
        groups = self._shared_groups([chara_a, chara_b])
        total = sum(group["point"] for group in groups)
        return {
            "a": first,
            "b": second,
            "groups": groups,
            "group_count": len(groups),
            "total": total,
            "rank": self._rank_of(total),
            "notes": [NOTE_PAIR, NOTE_RELATIONS],
        }

    def layout(self, slots: dict[str, list[int]]) -> dict[str, Any]:
        """六人布局：``target`` / ``parents``（2 人）/ ``grandparents``（最多 4 人）。"""
        target = slots["target"]
        parents = slots["parents"]
        grandparents = slots["grandparents"]
        if len(parents) != 2:
            raise ValueError("需要两位继承马娘（父母）。")
        if len(set([target, *parents])) != 3:
            raise ValueError("目标马娘与两位继承马娘必须是三个不同角色（衣装不同也算同一角色）。")

        target_row = self._require(target)
        parent_rows = [self._require(item) for item in parents]
        grandparent_rows = [self._require(item) for item in grandparents]
        grandparent_ids = [row["chara_id"] for row in grandparent_rows]
        names = {
            row["chara_id"]: row["name"] for row in [target_row, *parent_rows, *grandparent_rows]
        }

        entries: list[dict[str, Any]] = []

        def add(slot: str, ids: list[int], label: str, *, zeroed: bool = False) -> int:
            groups = [] if zeroed else self._shared_groups(ids)
            subtotal = sum(group["point"] for group in groups)
            entries.append(
                {
                    "slot": slot,
                    "label": label,
                    "chara_ids": ids,
                    "chara_names": [names.get(item, f"#{item}") for item in ids],
                    "groups": groups,
                    "subtotal": subtotal,
                    "zeroed": zeroed,
                }
            )
            return subtotal

        total = 0
        total += add("1", [target, parents[0]], f"目标 × {parent_rows[0]['name']}")
        total += add("2a", [target, parents[1]], f"目标 × {parent_rows[1]['name']}")
        total += add(
            "2b", [parents[0], parents[1]], f"{parent_rows[0]['name']} × {parent_rows[1]['name']}"
        )
        for index, grandparent in enumerate(grandparent_rows[:4]):
            slot = str(3 + index) if index < 2 else str(5 + (index - 2))
            zeroed = grandparent["chara_id"] == target
            total += add(
                slot,
                [target, parents[0] if index < 2 else parents[1], grandparent["chara_id"]],
                f"目标 + {parent_rows[0 if index < 2 else 1]['name']} + {grandparent['name']}",
                zeroed=zeroed,
            )

        return {
            "target": target_row,
            "parents": parent_rows,
            "grandparents": grandparent_rows,
            "slots": entries,
            "total": total,
            "rank": self._rank_of(total),
            "missing_grandparents": 4 - len(grandparent_ids),
            "notes": [NOTE_LAYOUT, NOTE_RELATIONS],
        }

    # ------------------------------------------------------------------ 内部
    def _require(self, chara_id: int) -> dict[str, Any]:
        row = store.get_chara(self.db, chara_id)
        if row is None:
            raise KeyError(f"没有参与任何相性关系组的角色：{chara_id}")
        return row

    def _shared_groups(self, chara_ids: list[int]) -> list[dict[str, Any]]:
        """同时包含所有给定角色的关系组，按点数从高到低。"""
        members = store.relations_by_chara(self.db)
        if not chara_ids:
            return []
        sets = [members.get(chara_id, set()) for chara_id in chara_ids]
        shared = set.intersection(*sets) if sets else set()
        if not shared:
            return []
        metrics = store.metrics(self.db)
        groups = [
            {
                "relation_type": relation_type,
                "point": metrics.get(relation_type, {}).get("point", 0),
                "size": metrics.get(relation_type, {}).get("size", 0),
            }
            for relation_type in shared
        ]
        groups.sort(key=lambda item: (-item["point"], item["relation_type"]))
        return groups

    def _ranks(self) -> list[dict[str, Any]]:
        rows = store.rank_rows(self.db)
        return [
            {
                **row,
                "label": RANK_LABELS.get(row["rank"], f"等级 {row['rank']}"),
                "range": f"{row['min']}–{row['max']}"
                if row["max"] < 9999
                else f"{row['min']} 以上",
            }
            for row in rows
        ]

    def _rank_of(self, score: int) -> dict[str, Any]:
        for row in store.rank_rows(self.db):
            if row["min"] <= score <= row["max"]:
                return {
                    "rank": row["rank"],
                    "label": RANK_LABELS.get(row["rank"], f"等级 {row['rank']}"),
                    "range": RANK_NOTES.get(row["rank"], f"{row['min']}–{row['max']} 分"),
                }
        return {"rank": None, "label": "未知", "range": "—"}
