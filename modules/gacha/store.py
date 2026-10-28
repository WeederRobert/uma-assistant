"""``gacha_*`` 表的读写。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.kernel.database import Database

from .schema import META_CATALOG


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


def replace_catalog(
    db: Database,
    banners: list[tuple[Any, ...]],
    pickups: list[tuple[Any, ...]],
) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM gacha_catalog")
        conn.execute("DELETE FROM gacha_pickups")
        if banners:
            conn.executemany(
                "INSERT OR REPLACE INTO gacha_catalog (gacha_id, type, card_type, title, "
                "description, start_ts, end_ts, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                banners,
            )
        if pickups:
            conn.executemany(
                "INSERT OR REPLACE INTO gacha_pickups (gacha_id, card_type, card_id, rarity, "
                "name, title, recommend_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                pickups,
            )


def count_banners(db: Database) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM gacha_catalog")
    return int(row["n"]) if row is not None else 0


def count_pickups(db: Database) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM gacha_pickups")
    return int(row["n"]) if row is not None else 0


def list_banners(db: Database) -> list[sqlite3.Row]:
    return db.query(
        "SELECT gacha_id, type, card_type, title, description, start_ts, end_ts "
        "FROM gacha_catalog ORDER BY start_ts"
    )


def get_banner(db: Database, gacha_id: int) -> sqlite3.Row | None:
    return db.query_one(
        "SELECT gacha_id, type, card_type, title, description, start_ts, end_ts "
        "FROM gacha_catalog WHERE gacha_id = ?",
        (gacha_id,),
    )


def pickups_by_gacha(db: Database) -> dict[int, list[sqlite3.Row]]:
    rows = db.query(
        "SELECT gacha_id, card_type, card_id, rarity, name, title, recommend_order "
        "FROM gacha_pickups ORDER BY gacha_id, recommend_order, card_type, card_id"
    )
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(int(row["gacha_id"]), []).append(row)
    return grouped


def list_pickups(db: Database, gacha_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT gacha_id, card_type, card_id, rarity, name, title, recommend_order "
        "FROM gacha_pickups WHERE gacha_id = ? ORDER BY recommend_order, card_type, card_id",
        (gacha_id,),
    )


def name_counts(db: Database) -> dict[str, int]:
    return {"banners": count_banners(db), "pickups": count_pickups(db)}


# --------------------------------------------------------------------- meta
def get_source(db: Database, key: str) -> dict[str, Any] | None:
    return _loads(db.get_meta(key), None)


def set_source(db: Database, key: str, value: dict[str, Any]) -> None:
    db.set_meta(key, json.dumps(value, ensure_ascii=False))


def catalog_source(db: Database) -> dict[str, Any] | None:
    return get_source(db, META_CATALOG)
