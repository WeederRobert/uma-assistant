"""从 ``master.mdb`` 导入赛事目录与赛道登记。

只读打开源库，字段映射全部显式写在这里；缺表或不是 SQLite 时抛
:class:`RacesSourceError`（message 是可直接展示的中文）。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.kernel.database import Database

from . import sources, store
from .schema import META_CATALOG, ensure_schema

logger = logging.getLogger(__name__)

REQUIRED_TABLES = (
    "race",
    "race_course_set",
    "text_data",
    "race_condition",
    "champions_schedule",
    "champions_round_schedule",
    "champions_round_detail",
    "champions_race_condition",
    "race_instance",
)


class RacesSourceError(RuntimeError):
    """master.mdb 不可用：路径不对、不是 SQLite、或缺需要的表。"""


@dataclass(slots=True)
class CatalogSummary:
    races: int = 0
    courses: int = 0
    skipped: int = 0
    conditions: int = 0
    champions: int = 0
    source: str = ""
    missing_tables: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "races": self.races,
            "courses": self.courses,
            "skipped": self.skipped,
            "conditions": self.conditions,
            "champions": self.champions,
            "source": self.source,
            "missing_tables": self.missing_tables,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise RacesSourceError(
            f"找不到 {path.name}：{path}。先在「数据同步」里从客户端导入一次，"
            "或把 master.mdb 放到该路径。"
        )
    if path.is_dir():
        raise RacesSourceError(f"这是一个目录，不是 master.mdb 文件：{path}")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        raise RacesSourceError(f"不是有效的 SQLite 数据库：{path.name}（{exc}）") from exc
    return conn


def _read_conditions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """``race_condition`` 是概率表：同一 ``(area, season)`` 组内 ``rate`` 之和为 1e8。"""
    rows = conn.execute(
        "SELECT area, season, weather, ground, rate FROM race_condition "
        "ORDER BY area, season, weather, ground"
    ).fetchall()
    return [
        {
            "area": int(row["area"]),
            "season": int(row["season"]),
            "weather": int(row["weather"]),
            "ground": int(row["ground"]),
            "rate": int(row["rate"]),
        }
        for row in rows
    ]


def _read_champions(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """把 ``champions_*`` 拼成排期、轮次窗口与晋级门槛三组行。"""
    stamps = store.now_iso()
    weather_by_condition = {
        int(row["id"]): (int(row["weather"]), int(row["ground"]))
        for row in conn.execute("SELECT id, weather, ground FROM race_condition")
    }

    # 每届大赛引用 1 条 race_condition（固定天气与马场）和 2 个 race_instance
    # （预赛段与决赛段）；同一届的所有轮次指向同一 group。
    refs = conn.execute(
        """
        SELECT c.champions_id AS champions_id,
               c.round_id AS round_id,
               c.race_condition_id AS race_condition_id,
               ri.race_id AS race_id,
               r.course_set AS course_id,
               name.text AS race_name
        FROM champions_race_condition AS c
        LEFT JOIN race_instance AS ri ON ri.id = c.race_instance_id
        LEFT JOIN race AS r ON r.id = ri.race_id
        LEFT JOIN text_data AS name
            ON name.category = ? AND name."index" = ri.race_id
        ORDER BY c.champions_id, c.round_id
        """,
        (sources.TEXT_RACE_NAME,),
    ).fetchall()

    grouped: dict[int, dict[str, Any]] = {}
    for row in refs:
        item = grouped.setdefault(
            int(row["champions_id"]),
            {"prelim": None, "final": None, "courses": set(), "condition": None},
        )
        if row["course_id"] is not None:
            item["courses"].add(str(row["course_id"]))
        if row["race_condition_id"] is not None and item["condition"] is None:
            item["condition"] = int(row["race_condition_id"])
        # round_id 0-3 是预选与小组赛（共用预赛赛道），4-6 是决赛三场
        slot = "final" if int(row["round_id"]) >= 4 else "prelim"
        if item[slot] is None and row["race_id"] is not None:
            item[slot] = (int(row["race_id"]), row["race_name"], row["course_id"])

    champions: list[dict[str, Any]] = []
    for row in conn.execute(
        "SELECT id, resource_id, start_date, end_date FROM champions_schedule ORDER BY id"
    ):
        champions_id = int(row["id"])
        info = grouped.get(champions_id, {"prelim": None, "final": None, "courses": set()})
        prelim = info["prelim"]
        final = info["final"]
        primary = prelim or final
        condition_id = info["condition"]
        weather, ground = weather_by_condition.get(condition_id, (None, None))
        course_id = primary[2] if primary else None
        if course_id is None and info["courses"]:
            course_id = sorted(info["courses"])[0]
        champions.append(
            {
                "champions_id": champions_id,
                "resource_id": row["resource_id"],
                "start_ts": row["start_date"],
                "end_ts": row["end_date"],
                "prelim_race_id": prelim[0] if prelim else None,
                "prelim_name": prelim[1] if prelim else None,
                "final_race_id": final[0] if final else None,
                "final_name": final[1] if final else None,
                "course_id": str(course_id) if course_id is not None else None,
                "course_count": len(info["courses"]),
                "race_condition_id": condition_id,
                "weather": weather,
                "ground": ground,
                "updated_at": stamps,
            }
        )

    rounds = [
        {
            "champions_id": int(row["champions_id"]),
            "round": int(row["round"]),
            "round_detail": int(row["round_detail"]),
            "start_ts": row["start_date"],
            "end_ts": row["end_date"],
        }
        for row in conn.execute(
            "SELECT champions_id, round, round_detail, start_date, end_date "
            "FROM champions_round_schedule ORDER BY champions_id, round, round_detail"
        )
    ]

    entries = [
        {
            "champions_id": int(row["champions_id"]),
            "league_type": int(row["league_type"]),
            "round_id": int(row["round_id"]),
            "round": row["round"],
            "tier": row["tier"],
            "round_number": row["round_number"],
            "breakthrough_1": row["breakthrough_number_1"],
            "breakthrough_2": row["breakthrough_number_2"],
            "entry_number": row["entry_number"],
            "free_entry_number": row["free_entry_number"],
        }
        for row in conn.execute(
            "SELECT champions_id, league_type, round_id, round, tier, round_number, "
            "breakthrough_number_1, breakthrough_number_2, entry_number, free_entry_number "
            "FROM champions_round_detail ORDER BY champions_id, league_type, round_id"
        )
    ]
    return champions, rounds, entries


def import_catalog(db: Database, mdb_path: Path) -> CatalogSummary:
    conn = _open_readonly(mdb_path)
    ensure_schema(db)
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = [table for table in REQUIRED_TABLES if table not in tables]
        if missing:
            raise RacesSourceError(f"{mdb_path.name} 缺少表 {'、'.join(missing)}，无法导入赛道数据")
        rows = conn.execute(
            """
            SELECT r.id AS race_id,
                   r.grade AS grade,
                   r."group" AS race_group,
                   r.course_set AS course_id,
                   r.entry_num AS entry_num,
                   cs.distance AS distance,
                   cs.ground AS ground,
                   cs.inout AS inout,
                   cs.turn AS turn,
                   name.text AS name
            FROM race AS r
            LEFT JOIN race_course_set AS cs ON cs.id = r.course_set
            LEFT JOIN text_data AS name
                ON name.category = ? AND name."index" = r.id
            ORDER BY r.id
            """,
            (sources.TEXT_RACE_NAME,),
        ).fetchall()
        course_rows = conn.execute(
            "SELECT id, distance, ground, inout, turn FROM race_course_set ORDER BY id"
        ).fetchall()
        conditions = _read_conditions(conn)
        champions, champion_rounds, champion_entries = _read_champions(conn)
    except sqlite3.DatabaseError as exc:
        raise RacesSourceError(f"读取 {mdb_path.name} 失败（{exc}）") from exc
    finally:
        conn.close()

    catalog: list[dict[str, Any]] = []
    courses: dict[str, dict[str, Any]] = {}
    # 赛道登记取自 race_course_set 全表：有些赛道当前没有任何正式赛事引用，
    # 但仍然是客户端里的真实赛道，面板要能查到它
    for row in course_rows:
        course_id = str(row["id"])
        venue = sources.venue_of(course_id)
        surface = sources.surface_label(row["ground"])
        if not venue or surface is None:
            continue
        courses[course_id] = {
            "course_id": course_id,
            "venue": venue,
            "surface": surface,
            "distance": int(row["distance"] or 0),
            "direction": sources.direction_label(row["turn"]),
            "course_part": sources.course_part_label(row["inout"]),
        }

    skipped = 0
    for row in rows:
        course_id = str(row["course_id"]) if row["course_id"] is not None else None
        # 没有名称的赛事是模板（群英联赛 / 练习 / 团队赛），留着只会干扰检索
        if not row["name"]:
            skipped += 1
            continue
        catalog.append(
            {
                "race_id": int(row["race_id"]),
                "name": row["name"],
                "grade": row["grade"],
                "race_group": row["race_group"],
                "course_id": course_id,
                "venue": sources.venue_of(course_id) if course_id else None,
                "surface": sources.surface_label(row["ground"]),
                "distance": row["distance"],
                "direction": sources.direction_label(row["turn"]),
                "course_part": sources.course_part_label(row["inout"]),
                "entry_num": row["entry_num"],
            }
        )

    store.clear_catalog(db)
    store.upsert_catalog(db, catalog)
    store.ensure_courses(db, list(courses.values()))
    store.replace_conditions(db, conditions)
    store.replace_champions(db, champions, champion_rounds, champion_entries)

    summary = CatalogSummary(
        races=len(catalog),
        courses=len(courses),
        skipped=skipped,
        conditions=len(conditions),
        champions=len(champions),
        source=f"{mdb_path.name} (sha256:{_sha256(mdb_path)[:12]})",
    )
    store.set_source(
        db,
        META_CATALOG,
        {
            "source": summary.source,
            "races": summary.races,
            "courses": summary.courses,
            "skipped": summary.skipped,
            "conditions": summary.conditions,
            "champions": summary.champions,
            "imported_at": store.now_iso(),
        },
    )
    logger.info(
        "赛道目录导入完成 | 赛事=%d 赛道=%d 跳过=%d 场地状态=%d 大赛=%d | %s",
        summary.races,
        summary.courses,
        summary.skipped,
        summary.conditions,
        summary.champions,
        summary.source,
    )
    return summary
