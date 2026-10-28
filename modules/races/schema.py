"""``races_*`` 表结构。

四组表分工：

- ``races_catalog``：赛事目录（来自 ``master.mdb`` 的 ``race`` + ``text_data``），一场一行
- ``races_courses``：赛道登记 + 几何（来自 BWiki 数据页），一条赛道一行
- ``races_conditions``：场地状态概率（``master.mdb`` 的 ``race_condition``），按区组分季节成组
- ``races_champions*``：大赛排期、轮次窗口与晋级门槛（``master.mdb`` 的 ``champions_*``）

赛道与赛事是 1:N：同一赛道会被多场赛事复用（例如东京 2400m 草地既是日本德比也是日本杯）。
大赛与赛道是 N:1：一届大赛固定用一条赛道，全部轮次共用。
"""

from __future__ import annotations

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS races_catalog (
    race_id     INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    grade       INTEGER,
    race_group  INTEGER,
    course_id   TEXT,
    venue       TEXT,
    surface     TEXT,
    distance    INTEGER,
    direction   TEXT,
    course_part TEXT,
    entry_num   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_races_catalog_name ON races_catalog(name);
CREATE INDEX IF NOT EXISTS idx_races_catalog_venue
    ON races_catalog(venue, distance);
CREATE INDEX IF NOT EXISTS idx_races_catalog_course ON races_catalog(course_id);

CREATE TABLE IF NOT EXISTS races_courses (
    course_id       TEXT PRIMARY KEY,
    venue           TEXT NOT NULL,
    surface         TEXT NOT NULL,
    distance        INTEGER NOT NULL,
    direction       TEXT,
    course_part     TEXT,
    segments        TEXT NOT NULL DEFAULT '[]',
    slopes          TEXT NOT NULL DEFAULT '[]',
    straight_count  INTEGER NOT NULL DEFAULT 0,
    straight_total  INTEGER NOT NULL DEFAULT 0,
    unlisted_length INTEGER NOT NULL DEFAULT 0,
    final_straight  INTEGER NOT NULL DEFAULT 0,
    total_length    INTEGER NOT NULL DEFAULT 0,
    slope_positive  INTEGER NOT NULL DEFAULT 0,
    slope_negative  INTEGER NOT NULL DEFAULT 0,
    opening_straight INTEGER NOT NULL DEFAULT 0,
    warnings        TEXT NOT NULL DEFAULT '[]',
    has_geometry    INTEGER NOT NULL DEFAULT 0,
    source_url      TEXT,
    fetched_at      TEXT,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_races_courses_filter
    ON races_courses(venue, surface, distance);

CREATE TABLE IF NOT EXISTS races_conditions (
    area    INTEGER NOT NULL,
    season  INTEGER NOT NULL,
    weather INTEGER NOT NULL,
    ground  INTEGER NOT NULL,
    rate    INTEGER NOT NULL,
    PRIMARY KEY (area, season, weather, ground)
);

CREATE TABLE IF NOT EXISTS races_champions (
    champions_id      INTEGER PRIMARY KEY,
    resource_id       INTEGER,
    start_ts          INTEGER,
    end_ts            INTEGER,
    prelim_race_id    INTEGER,
    prelim_name       TEXT,
    final_race_id     INTEGER,
    final_name        TEXT,
    course_id         TEXT,
    course_count      INTEGER NOT NULL DEFAULT 0,
    race_condition_id INTEGER,
    weather           INTEGER,
    ground            INTEGER,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_races_champions_start ON races_champions(start_ts);

CREATE TABLE IF NOT EXISTS races_champion_rounds (
    champions_id INTEGER NOT NULL,
    round        INTEGER NOT NULL,
    round_detail INTEGER NOT NULL,
    start_ts     INTEGER,
    end_ts       INTEGER,
    PRIMARY KEY (champions_id, round, round_detail)
);

CREATE TABLE IF NOT EXISTS races_champion_entries (
    champions_id    INTEGER NOT NULL,
    league_type     INTEGER NOT NULL,
    round_id        INTEGER NOT NULL,
    round           INTEGER,
    tier            INTEGER,
    round_number    INTEGER,
    breakthrough_1  INTEGER,
    breakthrough_2  INTEGER,
    entry_number    INTEGER,
    free_entry_number INTEGER,
    PRIMARY KEY (champions_id, league_type, round_id)
);
"""

META_CATALOG = "races.catalog_source"
META_GEOMETRY = "races.geometry_source"


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
        # 老库补列：新建库由 SCHEMA 建好，已存在的库需要 ALTER 一次
        migrations = {
            "unlisted_length": "INTEGER NOT NULL DEFAULT 0",
            "opening_straight": "INTEGER NOT NULL DEFAULT 0",
        }
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(races_courses)")}
        for column, definition in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE races_courses ADD COLUMN {column} {definition}")
