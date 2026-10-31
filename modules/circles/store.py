"""纯 SQL 读写：不解析 JSON、不算占比。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from app.kernel.database import Database

CIRCLE_COLUMNS = (
    "circle_id",
    "name",
    "member_count",
    "total_fans",
    "month_fans",
    "today_fans",
    "ranking_rank",
    "capture_interval_minutes",
    "month_baseline_at",
    "latest_at",
    "fetched_at",
)

MEMBER_COLUMNS = (
    "circle_id",
    "viewer_id",
    "member_name",
    "fan",
    "month_fan",
    "today_fan",
    "daily_avg",
    "month_training_count",
    "single_mode_play_count",
    "join_time",
    "last_login_time",
    "fetched_at",
)

# 只允许这三种排序，避免把用户输入直接拼进 ORDER BY
_SORT_SQL = {
    "month": "c.month_fans DESC, c.ranking_rank ASC",
    "today": "c.today_fans DESC, c.month_fans DESC",
    "rank": "c.ranking_rank ASC, c.month_fans DESC",
}


def replace_circles(db: Database, rows: Sequence[dict[str, Any]]) -> None:
    """整表以站点当前跟踪名单为准：新行覆盖，消失的行连同成员明细一起删掉。"""
    circle_ids = [int(row["circle_id"]) for row in rows]
    placeholders = ",".join("?" for _ in circle_ids)
    values = [tuple(row.get(column) for column in CIRCLE_COLUMNS) for row in rows]
    columns = ",".join(CIRCLE_COLUMNS)
    markers = ",".join("?" for _ in CIRCLE_COLUMNS)

    with db.transaction() as conn:
        if values:
            conn.executemany(
                f"INSERT INTO circles_circles ({columns}) VALUES ({markers}) "
                "ON CONFLICT(circle_id) DO UPDATE SET "
                + ", ".join(f"{column} = excluded.{column}" for column in CIRCLE_COLUMNS[1:]),
                values,
            )
            conn.execute(
                f"DELETE FROM circles_circles WHERE circle_id NOT IN ({placeholders})",
                circle_ids,
            )
            conn.execute(
                f"DELETE FROM circles_members WHERE circle_id NOT IN ({placeholders})",
                circle_ids,
            )
        else:
            conn.execute("DELETE FROM circles_circles")
            conn.execute("DELETE FROM circles_members")


def replace_members(
    db: Database,
    circle_id: int,
    rows: Sequence[dict[str, Any]],
) -> None:
    """一个社团的成员明细整体替换：退会的人不应该留在列表里。"""
    values = [tuple(row.get(column) for column in MEMBER_COLUMNS) for row in rows]
    columns = ",".join(MEMBER_COLUMNS)
    markers = ",".join("?" for _ in MEMBER_COLUMNS)
    with db.transaction() as conn:
        conn.execute("DELETE FROM circles_members WHERE circle_id = ?", (circle_id,))
        if values:
            conn.executemany(
                f"INSERT INTO circles_members ({columns}) VALUES ({markers})", values
            )


def list_circles(
    db: Database,
    *,
    query: str | None = None,
    sort: str = "month",
    limit: int = 300,
) -> list[sqlite3.Row]:
    sql = [
        "SELECT c.*, (SELECT COUNT(*) FROM circles_members m WHERE m.circle_id = c.circle_id)",
        "AS member_rows FROM circles_circles c",
    ]
    params: list[Any] = []
    if query:
        sql.append("WHERE c.name LIKE ? OR CAST(c.circle_id AS TEXT) LIKE ?")
        params.extend([f"%{query}%", f"%{query}%"])
    sql.append(f"ORDER BY {_SORT_SQL.get(sort, _SORT_SQL['month'])} LIMIT ?")
    params.append(int(limit))
    return db.query(" ".join(sql), params)


def get_circle(db: Database, circle_id: int) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM circles_circles WHERE circle_id = ?", (circle_id,))


def list_members(db: Database, circle_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM circles_members WHERE circle_id = ? ORDER BY month_fan DESC, member_name",
        (circle_id,),
    )


def counts(db: Database) -> dict[str, int]:
    row = db.query_one(
        "SELECT "
        "(SELECT COUNT(*) FROM circles_circles) AS circles, "
        "(SELECT COUNT(*) FROM circles_members) AS members, "
        "(SELECT COUNT(DISTINCT circle_id) FROM circles_members) AS member_circles"
    )
    if row is None:  # pragma: no cover - COUNT 一定返回一行
        return {"circles": 0, "members": 0, "member_circles": 0}
    return {
        "circles": int(row["circles"]),
        "members": int(row["members"]),
        "member_circles": int(row["member_circles"]),
    }


def read_meta(db: Database) -> dict[str, str]:
    return {row["key"]: row["value"] for row in db.query("SELECT key, value FROM circles_meta")}


def write_meta(db: Database, values: dict[str, str]) -> None:
    items: Iterable[tuple[str, str]] = ((key, value) for key, value in values.items())
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO circles_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(items),
        )
