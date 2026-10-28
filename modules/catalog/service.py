from __future__ import annotations

import sqlite3
from typing import Any

from app.kernel.database import Database
from app.kernel.dataversion import DataVersion, read_data_version

from .labels import (
    EFFECT_LEVELS,
    SUPPORT_EFFECT_SOURCE,
    attributes,
    skill_rarity_label,
    support_effect_label,
)
from .schema import ensure_schema

KINDS = ("character", "support_card", "skill")

_TABLE = {
    "character": ("catalog_characters", "card_id"),
    "support_card": ("catalog_support_cards", "support_card_id"),
    "skill": ("catalog_skills", "skill_id"),
}

_TITLE_KINDS = {"character", "support_card"}


class CatalogService:
    def __init__(self, db: Database) -> None:
        self.db = db
        ensure_schema(db)

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        version = read_data_version(self.db)
        return {
            "counts": {
                "characters": self._count("catalog_characters"),
                "support_cards": self._count("catalog_support_cards"),
                "skills": self._count("catalog_skills"),
                "character_skills": self._count("catalog_character_skills"),
                "support_effects": self._count("catalog_support_effects"),
                "support_unique": self._count("catalog_support_unique"),
                "support_skills": self._count("catalog_support_skills"),
            },
            "data_version": version.as_dict(),
        }

    def _count(self, table: str) -> int:
        row = self.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row is not None else 0

    # ----------------------------------------------------------------- search
    def search(self, query: str, kind: str | None = None, limit: int = 30) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(limit, 100))
        kinds = [kind] if kind in _TABLE else list(KINDS)

        results: list[dict] = []
        for item_kind in kinds:
            results.extend(self._search_kind(item_kind, query, limit))
        results.sort(key=lambda item: (item["_rank"], len(item["name"] or "")))
        for item in results:
            item.pop("_rank", None)
        return results[:limit]

    def _search_kind(self, kind: str, query: str, limit: int) -> list[dict]:
        table, id_column = _TABLE[kind]
        pattern = f"%{query}%"
        prefix = f"{query}%"
        # 技能表没有 title 列，单独拼 SQL，避免引用不存在的列
        if kind in _TITLE_KINDS:
            sql = f"""
                SELECT *, CASE
                    WHEN name = ? THEN 0
                    WHEN name LIKE ? THEN 1
                    WHEN title LIKE ? THEN 2
                    ELSE 3
                END AS rank
                FROM {table}
                WHERE name LIKE ? OR title LIKE ?
                ORDER BY rank, length(name)
                LIMIT ?
            """
            params = (query, prefix, prefix, pattern, pattern, limit)
        else:
            sql = f"""
                SELECT *, NULL AS title, CASE
                    WHEN name = ? THEN 0
                    WHEN name LIKE ? THEN 1
                    ELSE 2
                END AS rank
                FROM {table}
                WHERE name LIKE ?
                ORDER BY rank, length(name)
                LIMIT ?
            """
            params = (query, prefix, pattern, limit)
        rows = self.db.query(sql, params)
        return [self._normalize(kind, id_column, row) for row in rows]

    def get(self, kind: str, item_id: int) -> dict | None:
        table, id_column = _TABLE[kind]
        row = self.db.query_one(f"SELECT * FROM {table} WHERE {id_column} = ?", (item_id,))
        if row is None:
            return None
        item = self._normalize(kind, id_column, row)
        item.pop("_rank", None)
        if kind == "character":
            item["skills"] = self._character_skills(item_id)
        elif kind == "support_card":
            item["effects"] = self._support_effects(item_id)
            item["unique_effects"] = self._support_unique(item_id)
            item["skills"] = self._support_skills(item_id)
            item["effect_source"] = SUPPORT_EFFECT_SOURCE
        # 详情必须带上数据版本，否则用户无法判断这条描述属于哪一版
        item["data_version"] = self.data_version().as_dict()
        return item

    # ------------------------------------------------------------------ detail
    def _skills_by_ids(self, skill_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not skill_ids:
            return {}
        placeholders = ", ".join("?" * len(skill_ids))
        rows = self.db.query(
            "SELECT skill_id, name, description, rarity FROM catalog_skills "
            f"WHERE skill_id IN ({placeholders})",
            tuple(skill_ids),
        )
        return {
            int(row["skill_id"]): {
                "skill_id": int(row["skill_id"]),
                "name": row["name"],
                "description": (row["description"] or "").replace("\\n", "\n") or None,
                "rarity_label": skill_rarity_label(row["rarity"], row["skill_id"]),
            }
            for row in rows
        }

    def _character_skills(self, card_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT skill_id, need_rank FROM catalog_character_skills "
            "WHERE card_id = ? ORDER BY need_rank, skill_id",
            (card_id,),
        )
        details = self._skills_by_ids([int(row["skill_id"]) for row in rows])
        result: list[dict[str, Any]] = []
        for row in rows:
            skill = details.get(int(row["skill_id"]))
            if skill is None:
                # 技能表里没有的 id 也要列出来，否则会以为这张卡没有这个技能
                skill = {
                    "skill_id": int(row["skill_id"]),
                    "name": f"#{row['skill_id']}",
                    "description": None,
                    "rarity_label": None,
                }
            result.append({**skill, "need_rank": int(row["need_rank"] or 0)})
        return result

    def _support_skills(self, support_card_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT skill_id FROM catalog_support_skills WHERE support_card_id = ? "
            "ORDER BY skill_id",
            (support_card_id,),
        )
        details = self._skills_by_ids([int(row["skill_id"]) for row in rows])
        result: list[dict[str, Any]] = []
        for row in rows:
            skill = details.get(int(row["skill_id"]))
            if skill is None:
                skill = {
                    "skill_id": int(row["skill_id"]),
                    "name": f"#{row['skill_id']}",
                    "description": None,
                    "rarity_label": None,
                }
            result.append(skill)
        return result

    def _support_effects(self, support_card_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM catalog_support_effects WHERE support_card_id = ? ORDER BY effect_type",
            (support_card_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            levels = {
                level: row[f"lv{level}"]
                for level in EFFECT_LEVELS
                if row[f"lv{level}"] not in (None, -1)
            }
            init = None if row["init_value"] in (None, -1) else row["init_value"]
            top = max(levels.values()) if levels else None
            result.append(
                {
                    "effect_type": int(row["effect_type"]),
                    "label": support_effect_label(row["effect_type"]),
                    "init": init,
                    "levels": levels,
                    "max": top,
                    # 满破净增：从初始值到满破值多出来的部分（无初始值时就是满破值本身）
                    "gain": None if top is None else top - (init or 0),
                }
            )
        return result

    def _support_unique(self, support_card_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT slot, effect_type, value, need_lv FROM catalog_support_unique "
            "WHERE support_card_id = ? ORDER BY slot",
            (support_card_id,),
        )
        return [
            {
                "slot": int(row["slot"]),
                "effect_type": row["effect_type"],
                "label": support_effect_label(row["effect_type"]),
                "value": row["value"],
                "need_lv": row["need_lv"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ shape
    def _normalize(self, kind: str, id_column: str, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        item: dict[str, Any] = {
            "kind": kind,
            "id": data.pop(id_column),
            "name": data.pop("name"),
            "title": data.pop("title", None),
            "rank": data.pop("rank", 0),
            "fields": {},
        }
        item["_rank"] = item.pop("rank")
        for key, value in data.items():
            if key == "description":
                continue
            item["fields"][key] = value
        if "description" in data:
            # master.mdb 的描述里用字面量 \n 表示换行，展示前还原
            item["description"] = (data["description"] or "").replace("\\n", "\n")
        item["attributes"] = attributes(kind, item["fields"], item["id"])
        return item

    # --------------------------------------------------------------- version
    def data_version(self) -> DataVersion:
        return read_data_version(self.db)
