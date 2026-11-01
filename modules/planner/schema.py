"""``planner_*`` 表结构。

四组表：

- ``planner_pools``：卡池行。``segment`` 区分口径来源——``confirmed``（``master.mdb``
  内置排期）、``predicted``（BWiki 的简中预测，且落在 mdb 排期之后）、``overlap``
  （BWiki 预测但与 mdb 已确认段重叠，只展示不参与规划，避免重复计数）。
- ``planner_events``：活动行。BWiki 的活动表只有日服时间，简中时间靠卡池反推的偏移换算，
  ``basis`` 记这条用的是「活动卡池」还是「偏移推算」。
- ``planner_plans`` / ``planner_targets``：本地保存的规划方案与目标池。
"""

from __future__ import annotations

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS planner_pools (
    uid           TEXT PRIMARY KEY,
    source_key    TEXT NOT NULL,
    segment       TEXT NOT NULL,
    title         TEXT NOT NULL,
    card_type     INTEGER,
    gacha_type    INTEGER,
    start_ts      INTEGER NOT NULL,
    end_ts        INTEGER,
    jp_start_ts   INTEGER,
    cost_single   INTEGER,
    ceiling_pulls INTEGER,
    pickups       TEXT NOT NULL DEFAULT '',
    ids           TEXT NOT NULL DEFAULT '{}',
    source_level  TEXT NOT NULL DEFAULT 'B',
    source_url    TEXT NOT NULL DEFAULT '',
    fetched_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_planner_pools_start ON planner_pools(start_ts);
CREATE INDEX IF NOT EXISTS idx_planner_pools_segment ON planner_pools(segment, start_ts);

CREATE TABLE IF NOT EXISTS planner_events (
    uid          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    kind         TEXT,
    jp_start_ts  INTEGER,
    jp_end_ts    INTEGER,
    start_ts     INTEGER,
    end_ts       INTEGER,
    basis        TEXT NOT NULL,
    offset_days  INTEGER,
    matched_uid  TEXT,
    source_level TEXT NOT NULL DEFAULT 'C',
    source_url   TEXT NOT NULL DEFAULT '',
    fetched_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_planner_events_start ON planner_events(start_ts);

CREATE TABLE IF NOT EXISTS planner_plans (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    carats         INTEGER NOT NULL DEFAULT 0,
    tickets        INTEGER NOT NULL DEFAULT 0,
    monthly_income INTEGER NOT NULL DEFAULT 0,
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS planner_targets (
    plan_id  INTEGER NOT NULL REFERENCES planner_plans(id) ON DELETE CASCADE,
    pool_uid TEXT NOT NULL,
    pulls    INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (plan_id, pool_uid)
);
"""


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
