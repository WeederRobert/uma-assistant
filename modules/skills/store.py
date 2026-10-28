"""``skills_*`` 表的读写。

查询都是「窄 SQL + 显式参数」，筛选条件按需拼 ``WHERE``；导入走整表替换，
一次导入就是一份完整快照，不做增量合并。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.kernel.database import Database

from .schema import META_CATALOG

SKILL_FIELDS = (
    "skill_id",
    "name",
    "description",
    "rarity",
    "skill_category",
    "tag_id",
    "group_id",
    "group_rate",
    "grade_value",
    "icon_id",
    "disp_order",
    "plate_type",
    "is_general_skill",
    "activate_lot",
    "priority",
    "potential_per_default",
    "exp_type",
    "unique_skill_id_1",
    "unique_skill_id_2",
    "start_date",
    "end_date",
)

SKILL_COLUMNS = ", ".join(SKILL_FIELDS)
SKILL_COLUMNS_ALIASED = ", ".join(f"s.{field}" for field in SKILL_FIELDS)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


# ------------------------------------------------------------------- 导入写入
def replace_catalog(
    db: Database,
    skills: list[tuple[Any, ...]],
    slots: list[tuple[Any, ...]],
    effects: list[tuple[Any, ...]],
    characters: list[tuple[Any, ...]],
    support_cards: list[tuple[Any, ...]],
    upgrades: list[tuple[Any, ...]],
) -> None:
    with db.transaction() as conn:
        for table in (
            "skills_catalog",
            "skills_slots",
            "skills_effects",
            "skills_characters",
            "skills_support_cards",
            "skills_upgrades",
        ):
            conn.execute(f"DELETE FROM {table}")
        if skills:
            conn.executemany(
                "INSERT OR REPLACE INTO skills_catalog ("
                "skill_id, name, description, rarity, skill_category, tag_id, group_id, "
                "group_rate, grade_value, icon_id, disp_order, plate_type, is_general_skill, "
                "activate_lot, priority, potential_per_default, exp_type, unique_skill_id_1, "
                "unique_skill_id_2, start_date, end_date, updated_at"
                ") VALUES (" + ", ".join("?" * 22) + ")",
                skills,
            )
        if slots:
            conn.executemany(
                "INSERT OR REPLACE INTO skills_slots (skill_id, slot, precondition, condition, "
                "ability_time, ability_time_usage, cooldown) VALUES (?, ?, ?, ?, ?, ?, ?)",
                slots,
            )
        if effects:
            conn.executemany(
                "INSERT OR REPLACE INTO skills_effects (skill_id, slot, seq, ability_type, "
                "value_usage, value_level_usage, value, target_type, target_value, activate_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                effects,
            )
        if characters:
            conn.executemany(
                "INSERT OR REPLACE INTO skills_characters (skill_id, card_id, need_rank, name, "
                "title) VALUES (?, ?, ?, ?, ?)",
                characters,
            )
        if support_cards:
            conn.executemany(
                "INSERT OR REPLACE INTO skills_support_cards (skill_id, support_card_id, source, "
                "hint_group, name, title) VALUES (?, ?, ?, ?, ?, ?)",
                support_cards,
            )
        if upgrades:
            conn.executemany(
                "INSERT OR REPLACE INTO skills_upgrades (skill_id, source, base_skill_id, "
                "scenario_id, card_id, card_title, rank) VALUES (?, ?, ?, ?, ?, ?, ?)",
                upgrades,
            )


# ------------------------------------------------------------------- 条数统计
def _count(db: Database, table: str) -> int:
    row = db.query_one(f"SELECT COUNT(*) AS n FROM {table}")
    return int(row["n"]) if row is not None else 0


def count_skills(db: Database) -> int:
    return _count(db, "skills_catalog")


def count_slots(db: Database) -> int:
    return _count(db, "skills_slots")


def count_effects(db: Database) -> int:
    return _count(db, "skills_effects")


def count_characters(db: Database) -> int:
    return _count(db, "skills_characters")


def count_support_cards(db: Database) -> int:
    return _count(db, "skills_support_cards")


def count_upgrades(db: Database) -> int:
    return _count(db, "skills_upgrades")


# --------------------------------------------------------------------- 查询
def list_skills(
    db: Database,
    *,
    query: str | None = None,
    skill_category: int | None = None,
    rarity: int | None = None,
    with_source_only: bool = False,
    limit: int = 50,
) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[Any] = []
    if query:
        where.append("(s.name LIKE ? OR s.description LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    if skill_category is not None:
        where.append("s.skill_category = ?")
        params.append(skill_category)
    if rarity is not None:
        where.append("s.rarity = ?")
        params.append(rarity)
    if with_source_only:
        where.append(
            "(EXISTS (SELECT 1 FROM skills_characters c WHERE c.skill_id = s.skill_id) "
            "OR EXISTS (SELECT 1 FROM skills_support_cards p WHERE p.skill_id = s.skill_id))"
        )
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    # ORDER BY 的排序键也带占位符，必须排在 WHERE 参数之后、LIMIT 之前
    params.extend([query or "", f"{query or ''}%", limit])
    sql = f"""
        SELECT {SKILL_COLUMNS_ALIASED},
            (SELECT COUNT(*) FROM skills_characters c WHERE c.skill_id = s.skill_id)
                AS character_count,
            (SELECT COUNT(*) FROM skills_support_cards p WHERE p.skill_id = s.skill_id)
                AS support_card_count
        FROM skills_catalog AS s
        {clause}
        ORDER BY CASE
                     WHEN s.name = ? THEN 0
                     WHEN s.name LIKE ? THEN 1
                     ELSE 2
                 END,
                 length(s.name), s.skill_id
        LIMIT ?
    """
    return db.query(sql, params)


def get_skill(db: Database, skill_id: int) -> sqlite3.Row | None:
    return db.query_one(
        f"SELECT {SKILL_COLUMNS} FROM skills_catalog WHERE skill_id = ?", (skill_id,)
    )


def get_skills(db: Database, skill_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not skill_ids:
        return {}
    placeholders = ", ".join("?" * len(skill_ids))
    rows = db.query(
        f"SELECT {SKILL_COLUMNS} FROM skills_catalog WHERE skill_id IN ({placeholders})",
        skill_ids,
    )
    return {int(row["skill_id"]): row for row in rows}


def list_slots(db: Database, skill_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT skill_id, slot, precondition, condition, ability_time, ability_time_usage, "
        "cooldown FROM skills_slots WHERE skill_id = ? ORDER BY slot",
        (skill_id,),
    )


def list_effects(db: Database, skill_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT skill_id, slot, seq, ability_type, value_usage, value_level_usage, value, "
        "target_type, target_value, activate_type FROM skills_effects "
        "WHERE skill_id = ? ORDER BY slot, seq",
        (skill_id,),
    )


def list_characters(db: Database, skill_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT card_id, need_rank, name, title FROM skills_characters "
        "WHERE skill_id = ? ORDER BY need_rank, card_id",
        (skill_id,),
    )


def list_support_cards(db: Database, skill_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT support_card_id, source, hint_group, name, title FROM skills_support_cards "
        "WHERE skill_id = ? ORDER BY source, support_card_id",
        (skill_id,),
    )


def list_upgrades(db: Database, skill_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT skill_id, source, base_skill_id, scenario_id, card_id, card_title, rank "
        "FROM skills_upgrades WHERE skill_id = ? ORDER BY source",
        (skill_id,),
    )


def list_derivatives(db: Database, base_skill_id: int) -> list[sqlite3.Row]:
    """反查：以某个技能为前身的进化 / 觉醒技能。"""
    return db.query(
        "SELECT skill_id, source, base_skill_id, scenario_id, card_id, card_title, rank "
        "FROM skills_upgrades WHERE base_skill_id = ? ORDER BY skill_id",
        (base_skill_id,),
    )


def related_skills(db: Database, skill_ids: list[int]) -> dict[int, str]:
    if not skill_ids:
        return {}
    placeholders = ", ".join("?" * len(skill_ids))
    rows = db.query(
        f"SELECT skill_id, name FROM skills_catalog WHERE skill_id IN ({placeholders})",
        skill_ids,
    )
    return {int(row["skill_id"]): row["name"] for row in rows}


def facets(db: Database) -> dict[str, list[dict[str, Any]]]:
    categories = db.query(
        "SELECT skill_category AS value, COUNT(*) AS n FROM skills_catalog "
        "GROUP BY skill_category ORDER BY skill_category"
    )
    rarities = db.query(
        "SELECT rarity AS value, COUNT(*) AS n FROM skills_catalog GROUP BY rarity ORDER BY rarity"
    )
    return {
        "skill_categories": [dict(row) for row in categories],
        "rarities": [dict(row) for row in rarities],
    }


def name_counts(db: Database) -> dict[str, int]:
    return {
        "skills": count_skills(db),
        "slots": count_slots(db),
        "effects": count_effects(db),
        "characters": count_characters(db),
        "support_cards": count_support_cards(db),
        "upgrades": count_upgrades(db),
    }


# --------------------------------------------------------------------- meta
def get_source(db: Database, key: str) -> dict[str, Any] | None:
    return _loads(db.get_meta(key), None)


def set_source(db: Database, key: str, value: dict[str, Any]) -> None:
    db.set_meta(key, json.dumps(value, ensure_ascii=False))


def catalog_source(db: Database) -> dict[str, Any] | None:
    return get_source(db, META_CATALOG)
