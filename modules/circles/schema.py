"""``circles_*`` 表结构。

三张表分工：

- ``circles_circles``：榜单级数据（``GET /uma/api/circles`` 一行一个社团），
  ``month_fans`` 就是站点给的 ``month_delta``，即当月增粉。
- ``circles_members``：成员级数据（``GET /uma/api/circles/{id}``），``month_fan``
  是成员的当月增粉；贡献占比在 :mod:`modules.circles.service` 里算，不落库。
  ``viewer_id`` 只用于去重，接口与面板都不展示。
- ``circles_meta``：最近一次拉取时间、当月基线等来源信息。

站点会下线不再跟踪的社团，所以刷新列表后会删掉不再出现的行（含其成员明细）。
"""

from __future__ import annotations

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS circles_circles (
    circle_id                 INTEGER PRIMARY KEY,
    name                      TEXT    NOT NULL,
    member_count              INTEGER,
    total_fans                INTEGER,
    month_fans                INTEGER,
    today_fans                INTEGER,
    ranking_rank              INTEGER,
    capture_interval_minutes  REAL,
    month_baseline_at         TEXT,
    latest_at                 TEXT,
    fetched_at                TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS circles_members (
    circle_id              INTEGER NOT NULL,
    viewer_id              INTEGER NOT NULL,
    member_name            TEXT    NOT NULL,
    fan                    INTEGER,
    month_fan              INTEGER,
    today_fan              INTEGER,
    daily_avg              REAL,
    month_training_count   INTEGER,
    single_mode_play_count INTEGER,
    join_time              TEXT,
    last_login_time        TEXT,
    fetched_at             TEXT    NOT NULL,
    PRIMARY KEY (circle_id, viewer_id)
);

CREATE INDEX IF NOT EXISTS idx_circles_members_month
    ON circles_members(circle_id, month_fan DESC);

CREATE TABLE IF NOT EXISTS circles_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
