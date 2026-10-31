from __future__ import annotations

import sqlite3
from typing import Any

from app.kernel.database import Database
from app.kernel.dataversion import DataVersion, read_data_version

from .labels import (
    CONDITIONAL_EFFECT_MIN,
    EFFECT_LEVELS,
    MAX_BREAKTHROUGH,
    SUPPORT_EFFECT_SOURCE,
    SUPPORT_LEVEL_CAPS,
    attributes,
    skill_rarity_label,
    support_effect_label,
)
from .schema import ensure_schema

KINDS = ("character", "support_card", "skill")


def _value_at_level(init: int | None, levels: dict[int, int], level: int) -> int | None:
    """等级 ``level`` 时的数值：取不超过它的最近一档，``init`` 相当于 1 级。

    ``support_card_effect_table`` 只在数值变化的档位写值，其余档位是 -1（沿用上一档），
    所以按顺序往后覆盖就是「升到该等级时的值」。``-1`` 已在上游过滤掉。
    """
    value = init
    for step in sorted(levels):
        if step > level:
            break
        value = levels[step]
    return value


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
                "support_limits": self._count("catalog_support_limits"),
                "support_effect_types": self._count("catalog_support_effect_types"),
                "support_unique_text": self._count("catalog_support_unique_text"),
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
            rarity = item["fields"].get("rarity")
            names = self._effect_names()
            item["effects"] = self._support_effects(item_id, names)
            item["unique_effects"] = self._support_unique(item_id, names)
            item["unique"] = self._support_unique_block(
                rarity, item["unique_effects"], self._support_unique_text(item_id)
            )
            item["skills"] = self._support_skills(item_id)
            item["effect_source"] = SUPPORT_EFFECT_SOURCE
            item["breakthroughs"] = self._support_breakthroughs(rarity, item["effects"])
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

    def _support_effects(self, support_card_id: int, names: dict[int, str]) -> list[dict[str, Any]]:
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
                    "label": self._effect_label(names, row["effect_type"]),
                    "init": init,
                    "levels": levels,
                    "max": top,
                }
            )
        return result

    def _support_unique(self, support_card_id: int, names: dict[int, str]) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT slot, effect_type, value, need_lv FROM catalog_support_unique "
            "WHERE support_card_id = ? ORDER BY slot",
            (support_card_id,),
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            effect_type = row["effect_type"]
            # 100 以上是条件型加成，value_N 是条件参数而不是加成数值，不换算也不回显
            conditional = effect_type is not None and int(effect_type) >= CONDITIONAL_EFFECT_MIN
            result.append(
                {
                    "slot": int(row["slot"]),
                    "effect_type": effect_type,
                    "label": None if conditional else self._effect_label(names, effect_type),
                    "value": None if conditional else row["value"],
                    "need_lv": row["need_lv"],
                    "kind": "conditional" if conditional else "plain",
                }
            )
        return result

    def _support_unique_text(self, support_card_id: int) -> str | None:
        """固有效果的官方成句文案（text_data 155），条件型加成的发动条件只在这里有。"""
        row = self.db.query_one(
            "SELECT text FROM catalog_support_unique_text WHERE support_card_id = ?",
            (support_card_id,),
        )
        return row["text"] if row is not None else None

    def _support_unique_block(
        self,
        rarity: int | None,
        unique_effects: list[dict[str, Any]],
        text: str | None,
    ) -> dict[str, Any] | None:
        """固有效果的展示块：官方文案 + 生效等级/突破档 + 可换算的普通槽位数值。"""
        if not unique_effects:
            return None
        caps = self._support_level_caps(rarity)
        need_lv = unique_effects[0]["need_lv"]
        unlock = next(
            (index for index, cap in enumerate(caps) if need_lv is None or cap >= need_lv),
            None,
        )
        return {
            "text": (text or "").replace("\\n", "\n") or None,
            "conditional": any(row["kind"] == "conditional" for row in unique_effects),
            "need_lv": need_lv,
            # 第几突起才练得到这个等级；None = 该稀有度练不到
            "unlock_breakthrough": unlock,
            "items": [
                {"label": row["label"], "value": row["value"]}
                for row in unique_effects
                if row["kind"] == "plain"
            ],
        }

    def _support_level_caps(self, rarity: int | None) -> tuple[int, ...]:
        """该稀有度 0–4 突的等级上限：优先用导入的 support_card_limit，旧库回落到内置表。"""
        if rarity is not None:
            row = self.db.query_one(
                "SELECT limit_0, limit_1, limit_2, limit_3, limit_4 "
                "FROM catalog_support_limits WHERE rarity = ?",
                (rarity,),
            )
            if row is not None:
                return tuple(int(row[f"limit_{index}"]) for index in range(MAX_BREAKTHROUGH + 1))
        return SUPPORT_LEVEL_CAPS.get(rarity, ())

    def _effect_names(self) -> dict[int, str]:
        """效果类型 → 名称：主数据自带字典（text_data 151）优先，缺失时回落 BWiki 映射。"""
        return {
            int(row["effect_type"]): row["name"]
            for row in self.db.query("SELECT effect_type, name FROM catalog_support_effect_types")
        }

    @staticmethod
    def _effect_label(names: dict[int, str], effect_type: Any) -> str:
        try:
            key = int(effect_type)
        except (TypeError, ValueError):
            return support_effect_label(effect_type)
        return names.get(key) or support_effect_label(key)

    def _support_breakthroughs(
        self,
        rarity: int | None,
        effects: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """把训练效果摊成「突破档位 × 数值」表。

        每一列 = 突破到该档并升到该档等级上限时的值；训练效果按
        ``support_card_effect_table`` 的等级档位取，``-1`` 的档沿用上一档。
        固有效果不随突破档位变化（只是有解锁等级），所以不进这张表。
        """
        caps = self._support_level_caps(rarity)
        if not caps:
            return None

        rows = [
            {
                "label": effect["label"],
                "values": [_value_at_level(effect["init"], effect["levels"], cap) for cap in caps],
            }
            for effect in effects
        ]
        return {"level_caps": list(caps), "rows": rows}

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
