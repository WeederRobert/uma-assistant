"""``races_*`` 表的读写。

查询都是「窄 SQL + 显式参数」，不做动态 ORM；筛选条件按需拼 WHERE。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.kernel.database import Database

from .schema import META_CATALOG, META_GEOMETRY


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


# ---------------------------------------------------------------- 赛事目录
def upsert_catalog(db: Database, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    db.executemany(
        """
        INSERT INTO races_catalog (
            race_id, name, grade, race_group, course_id, venue, surface,
            distance, direction, course_part, entry_num
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(race_id) DO UPDATE SET
            name        = excluded.name,
            grade       = excluded.grade,
            race_group  = excluded.race_group,
            course_id   = excluded.course_id,
            venue       = excluded.venue,
            surface     = excluded.surface,
            distance    = excluded.distance,
            direction   = excluded.direction,
            course_part = excluded.course_part,
            entry_num   = excluded.entry_num
        """,
        [
            (
                row["race_id"],
                row["name"],
                row["grade"],
                row["race_group"],
                row["course_id"],
                row["venue"],
                row["surface"],
                row["distance"],
                row["direction"],
                row["course_part"],
                row["entry_num"],
            )
            for row in rows
        ],
    )


def clear_catalog(db: Database) -> None:
    db.execute("DELETE FROM races_catalog")


def catalog_races(db: Database, course_id: str) -> list[str]:
    rows = db.query(
        "SELECT name FROM races_catalog WHERE course_id = ? ORDER BY name", (course_id,)
    )
    return [row["name"] for row in rows]


def list_races(
    db: Database,
    *,
    query: str | None = None,
    venue: str | None = None,
    surface: str | None = None,
    direction: str | None = None,
    grade: int | None = None,
    official_only: bool = False,
    distance_min: int | None = None,
    distance_max: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query:
        clauses.append("name LIKE ?")
        params.append(f"%{query}%")
    if venue:
        clauses.append("venue = ?")
        params.append(venue)
    if surface:
        clauses.append("surface = ?")
        params.append(surface)
    if direction:
        clauses.append("direction = ?")
        params.append(direction)
    if grade is not None:
        clauses.append("grade = ?")
        params.append(grade)
    if official_only:
        clauses.append("grade IN (100, 200, 300, 400, 700, 800)")
    if distance_min is not None:
        clauses.append("distance >= ?")
        params.append(distance_min)
    if distance_max is not None:
        clauses.append("distance <= ?")
        params.append(distance_max)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.query(
        f"SELECT * FROM races_catalog {where} "
        "ORDER BY grade IS NULL, grade, distance, name LIMIT ?",
        (*params, limit),
    )
    return [dict(row) for row in rows]


def count_races(db: Database, *, official_only: bool = False) -> int:
    clause = "WHERE grade IN (100, 200, 300, 400, 700, 800)" if official_only else ""
    row = db.query_one(f"SELECT COUNT(*) AS n FROM races_catalog {clause}")
    return int(row["n"]) if row else 0


def get_race(db: Database, race_id: int) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM races_catalog WHERE race_id = ?", (race_id,))
    return dict(row) if row else None


# -------------------------------------------------------------------- 赛道
def upsert_courses(db: Database, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stamp = now_iso()
    db.executemany(
        """
        INSERT INTO races_courses (
            course_id, venue, surface, distance, direction, course_part,
            segments, slopes, straight_count, straight_total, unlisted_length,
            final_straight, total_length, slope_positive, slope_negative, opening_straight,
            warnings, has_geometry, source_url, fetched_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(course_id) DO UPDATE SET
            venue          = excluded.venue,
            surface        = excluded.surface,
            distance       = excluded.distance,
            direction      = excluded.direction,
            course_part    = excluded.course_part,
            segments       = excluded.segments,
            slopes         = excluded.slopes,
            straight_count = excluded.straight_count,
            straight_total = excluded.straight_total,
            unlisted_length = excluded.unlisted_length,
            final_straight = excluded.final_straight,
            total_length   = excluded.total_length,
            slope_positive = excluded.slope_positive,
            slope_negative = excluded.slope_negative,
            opening_straight = excluded.opening_straight,
            warnings       = excluded.warnings,
            has_geometry   = excluded.has_geometry,
            source_url     = COALESCE(excluded.source_url, races_courses.source_url),
            fetched_at     = COALESCE(excluded.fetched_at, races_courses.fetched_at),
            updated_at     = excluded.updated_at
        """,
        [
            (
                row["course_id"],
                row["venue"],
                row["surface"],
                row["distance"],
                row.get("direction"),
                row.get("course_part"),
                row.get("segments", "[]"),
                row.get("slopes", "[]"),
                row.get("straight_count", 0),
                row.get("straight_total", 0),
                row.get("unlisted_length", 0),
                row.get("final_straight", 0),
                row.get("total_length", 0),
                row.get("slope_positive", 0),
                row.get("slope_negative", 0),
                row.get("opening_straight", 0),
                row.get("warnings", "[]"),
                int(row.get("has_geometry", 0)),
                row.get("source_url"),
                row.get("fetched_at"),
                stamp,
            )
            for row in rows
        ],
    )


def ensure_courses(db: Database, rows: list[dict[str, Any]]) -> None:
    """登记赛道（来自 mdb），不碰几何字段——几何只由数据页导入。"""
    if not rows:
        return
    stamp = now_iso()
    db.executemany(
        """
        INSERT INTO races_courses (
            course_id, venue, surface, distance, direction, course_part, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(course_id) DO UPDATE SET
            venue       = excluded.venue,
            surface     = excluded.surface,
            distance    = excluded.distance,
            direction   = excluded.direction,
            course_part = excluded.course_part,
            updated_at  = excluded.updated_at
        """,
        [
            (
                row["course_id"],
                row["venue"],
                row["surface"],
                row["distance"],
                row.get("direction"),
                row.get("course_part"),
                stamp,
            )
            for row in rows
        ],
    )


def _course_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["segments"] = _loads(data.get("segments"), [])
    data["slopes"] = _loads(data.get("slopes"), [])
    data["warnings"] = _loads(data.get("warnings"), [])
    data["has_geometry"] = bool(data.get("has_geometry"))
    return data


def list_courses(
    db: Database,
    *,
    venue: str | None = None,
    surface: str | None = None,
    direction: str | None = None,
    distance_min: int | None = None,
    distance_max: int | None = None,
    geometry_only: bool = False,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if venue:
        clauses.append("venue = ?")
        params.append(venue)
    if surface:
        clauses.append("surface = ?")
        params.append(surface)
    if direction:
        clauses.append("direction = ?")
        params.append(direction)
    if distance_min is not None:
        clauses.append("distance >= ?")
        params.append(distance_min)
    if distance_max is not None:
        clauses.append("distance <= ?")
        params.append(distance_max)
    if geometry_only:
        clauses.append("has_geometry = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.query(
        f"SELECT * FROM races_courses {where} ORDER BY venue, surface, distance",
        tuple(params),
    )
    return [_course_to_dict(row) for row in rows]


def get_course(db: Database, course_id: str) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM races_courses WHERE course_id = ?", (course_id,))
    return _course_to_dict(row) if row else None


def course_distances(db: Database) -> dict[str, int]:
    rows = db.query("SELECT course_id, distance FROM races_courses")
    return {str(row["course_id"]): int(row["distance"]) for row in rows}


def race_counts_by_course(db: Database) -> dict[str, int]:
    rows = db.query(
        "SELECT course_id, COUNT(*) AS n FROM races_catalog "
        "WHERE course_id IS NOT NULL GROUP BY course_id"
    )
    return {str(row["course_id"]): int(row["n"]) for row in rows}


def count_courses(db: Database, *, geometry_only: bool = False) -> int:
    clause = "WHERE has_geometry = 1" if geometry_only else ""
    row = db.query_one(f"SELECT COUNT(*) AS n FROM races_courses {clause}")
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------- 场地状态
def replace_conditions(db: Database, rows: list[dict[str, Any]]) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM races_conditions")
        conn.executemany(
            "INSERT INTO races_conditions (area, season, weather, ground, rate) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (row["area"], row["season"], row["weather"], row["ground"], row["rate"])
                for row in rows
            ],
        )


def list_conditions(db: Database, *, area: int, season: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT weather, ground, rate FROM races_conditions "
        "WHERE area = ? AND season = ? ORDER BY rate DESC, weather, ground",
        (area, season),
    )


def condition_groups(db: Database) -> list[sqlite3.Row]:
    return db.query(
        "SELECT area, season, COUNT(*) AS n, SUM(rate) AS total FROM races_conditions "
        "GROUP BY area, season ORDER BY area, season"
    )


def count_conditions(db: Database) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM races_conditions")
    return int(row["n"]) if row else 0


# -------------------------------------------------------------------- 大赛
def replace_champions(
    db: Database,
    champions: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM races_champions")
        conn.execute("DELETE FROM races_champion_rounds")
        conn.execute("DELETE FROM races_champion_entries")
        conn.executemany(
            """
            INSERT INTO races_champions (
                champions_id, resource_id, start_ts, end_ts, prelim_race_id, prelim_name,
                final_race_id, final_name, course_id, course_count, race_condition_id,
                weather, ground, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["champions_id"],
                    row["resource_id"],
                    row["start_ts"],
                    row["end_ts"],
                    row["prelim_race_id"],
                    row["prelim_name"],
                    row["final_race_id"],
                    row["final_name"],
                    row["course_id"],
                    row["course_count"],
                    row["race_condition_id"],
                    row["weather"],
                    row["ground"],
                    row["updated_at"],
                )
                for row in champions
            ],
        )
        conn.executemany(
            "INSERT INTO races_champion_rounds "
            "(champions_id, round, round_detail, start_ts, end_ts) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["champions_id"],
                    row["round"],
                    row["round_detail"],
                    row["start_ts"],
                    row["end_ts"],
                )
                for row in rounds
            ],
        )
        conn.executemany(
            """
            INSERT INTO races_champion_entries (
                champions_id, league_type, round_id, round, tier, round_number,
                breakthrough_1, breakthrough_2, entry_number, free_entry_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["champions_id"],
                    row["league_type"],
                    row["round_id"],
                    row["round"],
                    row["tier"],
                    row["round_number"],
                    row["breakthrough_1"],
                    row["breakthrough_2"],
                    row["entry_number"],
                    row["free_entry_number"],
                )
                for row in entries
            ],
        )


CHAMPIONS_SELECT = """
    SELECT c.champions_id, c.resource_id, c.start_ts, c.end_ts,
           c.prelim_race_id, c.prelim_name, c.final_race_id, c.final_name,
           c.course_id, c.course_count, c.race_condition_id, c.weather, c.ground,
           s.venue, s.surface, s.distance, s.direction, s.course_part
    FROM races_champions AS c
    LEFT JOIN races_courses AS s ON s.course_id = c.course_id
"""


def list_champions(db: Database, *, upcoming: bool = False) -> list[sqlite3.Row]:
    clause = ""
    if upcoming:
        clause = "WHERE c.end_ts >= strftime('%s', 'now')"
    return db.query(f"{CHAMPIONS_SELECT} {clause} ORDER BY c.start_ts, c.champions_id")


def get_champion(db: Database, champions_id: int) -> sqlite3.Row | None:
    return db.query_one(f"{CHAMPIONS_SELECT} WHERE c.champions_id = ?", (champions_id,))


def champion_rounds(db: Database, champions_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT round, round_detail, start_ts, end_ts FROM races_champion_rounds "
        "WHERE champions_id = ? ORDER BY round, round_detail",
        (champions_id,),
    )


def champion_entries(db: Database, champions_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT league_type, round_id, round, tier, round_number, breakthrough_1, "
        "breakthrough_2, entry_number, free_entry_number FROM races_champion_entries "
        "WHERE champions_id = ? ORDER BY league_type, round_id",
        (champions_id,),
    )


def count_champions(db: Database) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM races_champions")
    return int(row["n"]) if row else 0


# ------------------------------------------------------------------ 来源信息
def get_source(db: Database, key: str) -> dict[str, Any] | None:
    return _loads(db.get_meta(key), None)


def set_source(db: Database, key: str, value: dict[str, Any]) -> None:
    db.set_meta(key, json.dumps(value, ensure_ascii=False))


def catalog_source(db: Database) -> dict[str, Any] | None:
    return get_source(db, META_CATALOG)


def geometry_source(db: Database) -> dict[str, Any] | None:
    return get_source(db, META_GEOMETRY)
