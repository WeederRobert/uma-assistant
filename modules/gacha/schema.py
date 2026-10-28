"""``gacha_*`` 表结构。

两组表：

- ``gacha_catalog``：一条卡池一行（``master.mdb`` 的 ``gacha_data`` 去掉 40 多个
  「抽一发给什么」的数值列），含名称（``text_data`` 26）、说明（13）与起止时间。
- ``gacha_pickups``：卡池里的**精选（pickup）**卡片，马娘与协助卡都走这张表，
  用 ``card_type`` 区分（1 马娘、2 协助卡）。

时区一律按运行环境的系统时区展示，库里存的是 unix 秒（与 ``master.mdb`` 一致）。
"""

from __future__ import annotations

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS gacha_catalog (
    gacha_id    INTEGER PRIMARY KEY,
    type        INTEGER,
    card_type   INTEGER,
    title       TEXT NOT NULL,
    description TEXT,
    start_ts    INTEGER,
    end_ts      INTEGER,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gacha_catalog_start ON gacha_catalog(start_ts);

CREATE TABLE IF NOT EXISTS gacha_pickups (
    gacha_id   INTEGER NOT NULL,
    card_type  INTEGER NOT NULL,
    card_id    INTEGER NOT NULL,
    rarity     INTEGER,
    name       TEXT,
    title      TEXT,
    recommend_order INTEGER,
    PRIMARY KEY (gacha_id, card_type, card_id)
);

CREATE INDEX IF NOT EXISTS idx_gacha_pickups_card ON gacha_pickups(card_type, card_id);
"""

META_CATALOG = "gacha.catalog_source"


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
