"""``planner_*`` 表的读写。

- 卡池与活动按「来源」整批替换：``mdb`` 与两条 BWiki 来源各自独立，互不覆盖。
- 规划方案与目标池是用户数据，只增删自己的行，不会被抓取流程清掉。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.kernel.database import Database

META_LAST_REFRESH = "planner.last_refresh"
META_OFFSET = "planner.offset"
META_MDB_SOURCE = "planner.mdb_source"

POOL_COLUMNS = (
    "uid",
    "source_key",
    "segment",
    "title",
    "card_type",
    "gacha_type",
    "start_ts",
    "end_ts",
    "jp_start_ts",
    "cost_single",
    "ceiling_pulls",
    "pickups",
    "ids",
    "source_level",
    "source_url",
    "fetched_at",
)

EVENT_COLUMNS = (
    "uid",
    "title",
    "kind",
    "jp_start_ts",
    "jp_end_ts",
    "start_ts",
    "end_ts",
    "basis",
    "offset_days",
    "matched_uid",
    "source_level",
    "source_url",
    "fetched_at",
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _placeholders(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return (
        f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
        f"VALUES ({_placeholders(len(columns))})"
    )


def replace_mdb_pools(db: Database, rows: list[tuple[Any, ...]]) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM planner_pools WHERE source_key = 'mdb'")
        if rows:
            conn.executemany(_insert_sql("planner_pools", POOL_COLUMNS), rows)


def replace_mdb_events(db: Database, rows: list[tuple[Any, ...]]) -> None:
    """活动只有 mdb 一个来源，整表替换（顺带清掉旧版本留下的跨服换算行）。"""
    with db.transaction() as conn:
        conn.execute("DELETE FROM planner_events")
        if rows:
            conn.executemany(_insert_sql("planner_events", EVENT_COLUMNS), rows)


def replace_wiki(db: Database, pool_rows: list[tuple[Any, ...]]) -> None:
    """预测段整体替换：BWiki 的行每次刷新重写，mdb 的已确认段不动。"""
    with db.transaction() as conn:
        conn.execute("DELETE FROM planner_pools WHERE source_key = 'wiki-predicted'")
        if pool_rows:
            conn.executemany(_insert_sql("planner_pools", POOL_COLUMNS), pool_rows)


def clear_wiki(db: Database) -> None:
    """来源整体失败时清掉预测段，避免把上一次的成功结果当成这次的结果。"""
    with db.transaction() as conn:
        conn.execute("DELETE FROM planner_pools WHERE source_key = 'wiki-predicted'")


def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def list_pools(db: Database, *, segment: str | None = None) -> list[dict[str, Any]]:
    if segment:
        rows = db.query(
            "SELECT * FROM planner_pools WHERE segment = ? ORDER BY start_ts, uid",
            (segment,),
        )
    else:
        rows = db.query("SELECT * FROM planner_pools ORDER BY start_ts, uid")
    return _rows(rows)


def list_events(db: Database) -> list[dict[str, Any]]:
    return _rows(db.query("SELECT * FROM planner_events ORDER BY start_ts, uid"))


def counts(db: Database) -> dict[str, int]:
    pools = {
        row["segment"]: row["n"]
        for row in db.query("SELECT segment, COUNT(*) AS n FROM planner_pools GROUP BY segment")
    }
    events = db.query_one("SELECT COUNT(*) AS n FROM planner_events")
    return {
        "confirmed": int(pools.get("confirmed", 0)),
        "predicted": int(pools.get("predicted", 0)),
        "overlap": int(pools.get("overlap", 0)),
        "events": int(events["n"]) if events else 0,
    }


# --------------------------------------------------------------------- meta
def set_meta(db: Database, key: str, value: dict[str, Any]) -> None:
    db.set_meta(key, json.dumps(value, ensure_ascii=False))


def get_meta(db: Database, key: str) -> dict[str, Any] | None:
    raw = db.get_meta(key)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


# -------------------------------------------------------------------- plans
def list_plans(db: Database) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT p.*, (SELECT COUNT(*) FROM planner_targets t WHERE t.plan_id = p.id) AS targets "
        "FROM planner_plans p ORDER BY p.updated_at DESC, p.id DESC"
    )
    return _rows(rows)


def get_plan(db: Database, plan_id: int) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM planner_plans WHERE id = ?", (plan_id,))
    if row is None:
        return None
    plan = dict(row)
    plan["targets"] = _rows(
        db.query(
            "SELECT pool_uid, pulls, priority FROM planner_targets "
            "WHERE plan_id = ? ORDER BY priority, pool_uid",
            (plan_id,),
        )
    )
    return plan


def _write_targets(conn: sqlite3.Connection, plan_id: int, targets: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM planner_targets WHERE plan_id = ?", (plan_id,))
    if targets:
        conn.executemany(
            "INSERT OR REPLACE INTO planner_targets (plan_id, pool_uid, pulls, priority) "
            "VALUES (?, ?, ?, ?)",
            [
                (plan_id, str(t["pool_uid"]), int(t.get("pulls") or 0), int(t.get("priority") or 1))
                for t in targets
            ],
        )


def create_plan(
    db: Database,
    *,
    name: str,
    carats: int,
    tickets: int,
    monthly_income: int,
    note: str,
    targets: list[dict[str, Any]],
) -> int:
    stamp = now_iso()
    with db.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO planner_plans (name, carats, tickets, monthly_income, note, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, carats, tickets, monthly_income, note, stamp, stamp),
        )
        plan_id = int(cursor.lastrowid or 0)
        _write_targets(conn, plan_id, targets)
    return plan_id


def update_plan(
    db: Database,
    plan_id: int,
    *,
    name: str,
    carats: int,
    tickets: int,
    monthly_income: int,
    note: str,
    targets: list[dict[str, Any]],
) -> bool:
    if db.query_one("SELECT id FROM planner_plans WHERE id = ?", (plan_id,)) is None:
        return False
    with db.transaction() as conn:
        conn.execute(
            "UPDATE planner_plans SET name = ?, carats = ?, tickets = ?, monthly_income = ?, "
            "note = ?, updated_at = ? WHERE id = ?",
            (name, carats, tickets, monthly_income, note, now_iso(), plan_id),
        )
        _write_targets(conn, plan_id, targets)
    return True


def delete_plan(db: Database, plan_id: int) -> bool:
    with db.transaction() as conn:
        cursor = conn.execute("DELETE FROM planner_plans WHERE id = ?", (plan_id,))
        conn.execute("DELETE FROM planner_targets WHERE plan_id = ?", (plan_id,))
    return bool(cursor.rowcount)
