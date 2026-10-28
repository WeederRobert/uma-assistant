"""``succession_*`` 表的读写。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.kernel.database import Database

from .schema import META_SOURCE


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def replace_catalog(
    db: Database,
    relations: list[tuple[int, int]],
    members: list[tuple[int, int]],
    ranks: list[tuple[int, int, int]],
    charas: list[tuple[int, str]],
) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM succession_relations")
        conn.execute("DELETE FROM succession_members")
        conn.execute("DELETE FROM succession_ranks")
        conn.execute("DELETE FROM succession_charas")
        if relations:
            conn.executemany(
                "INSERT OR REPLACE INTO succession_relations (relation_type, relation_point) "
                "VALUES (?, ?)",
                relations,
            )
        if members:
            conn.executemany(
                "INSERT OR REPLACE INTO succession_members (relation_type, chara_id) VALUES (?, ?)",
                members,
            )
        if ranks:
            conn.executemany(
                "INSERT OR REPLACE INTO succession_ranks "
                "(relation_rank, rank_value_min, rank_value_max) VALUES (?, ?, ?)",
                ranks,
            )
        if charas:
            conn.executemany(
                "INSERT OR REPLACE INTO succession_charas (chara_id, name) VALUES (?, ?)",
                charas,
            )


def counts(db: Database) -> dict[str, int]:
    def one(table: str) -> int:
        row = db.query_one(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row is not None else 0

    return {
        "relations": one("succession_relations"),
        "members": one("succession_members"),
        "ranks": one("succession_ranks"),
        "charas": one("succession_charas"),
    }


def all_relations(db: Database) -> list[dict[str, Any]]:
    return [
        {"relation_type": int(row["relation_type"]), "point": int(row["relation_point"])}
        for row in db.query(
            "SELECT relation_type, relation_point FROM succession_relations ORDER BY relation_type"
        )
    ]


def relations_by_chara(db: Database) -> dict[int, set[int]]:
    """角色 → 它参与的关系组编号集合（一次读全表，避免逐对查询）。"""
    result: dict[int, set[int]] = {}
    for row in db.query("SELECT relation_type, chara_id FROM succession_members"):
        result.setdefault(int(row["chara_id"]), set()).add(int(row["relation_type"]))
    return result


def metrics(db: Database) -> dict[str, dict[str, int]]:
    """关系组编号 → 点数与成员数，用于展示「这个组有多大」。"""
    sizes = {
        int(row["relation_type"]): int(row["n"])
        for row in db.query(
            "SELECT relation_type, COUNT(*) AS n FROM succession_members GROUP BY relation_type"
        )
    }
    return {
        int(row["relation_type"]): {
            "point": int(row["relation_point"]),
            "size": sizes.get(int(row["relation_type"]), 0),
        }
        for row in db.query("SELECT relation_type, relation_point FROM succession_relations")
    }


def rank_rows(db: Database) -> list[dict[str, Any]]:
    return [
        {
            "rank": int(row["relation_rank"]),
            "min": int(row["rank_value_min"]),
            "max": int(row["rank_value_max"]),
        }
        for row in db.query(
            "SELECT relation_rank, rank_value_min, rank_value_max FROM succession_ranks "
            "ORDER BY rank_value_min"
        )
    ]


def list_charas(db: Database, query: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    if query:
        rows = db.query(
            "SELECT chara_id, name FROM succession_charas WHERE name LIKE ? "
            "ORDER BY chara_id LIMIT ?",
            (f"%{query}%", limit),
        )
    else:
        rows = db.query(
            "SELECT chara_id, name FROM succession_charas ORDER BY chara_id LIMIT ?", (limit,)
        )
    return [{"chara_id": int(row["chara_id"]), "name": row["name"]} for row in rows]


def get_chara(db: Database, chara_id: int) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT chara_id, name FROM succession_charas WHERE chara_id = ?", (chara_id,)
    )
    if row is None:
        return None
    return {"chara_id": int(row["chara_id"]), "name": row["name"]}


# --------------------------------------------------------------------- meta
def catalog_source(db: Database) -> dict[str, Any] | None:
    try:
        raw = db.get_meta(META_SOURCE)
    except Exception:  # noqa: BLE001 - meta 缺失时按未导入处理
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def set_catalog_source(db: Database, value: dict[str, Any]) -> None:
    db.set_meta(META_SOURCE, json.dumps(value, ensure_ascii=False))
