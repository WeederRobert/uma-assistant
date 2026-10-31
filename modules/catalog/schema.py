from __future__ import annotations

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_characters (
    card_id     INTEGER PRIMARY KEY,
    chara_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    title       TEXT,
    rarity      INTEGER,
    limited     INTEGER NOT NULL DEFAULT 0,
    talent_speed   INTEGER,
    talent_stamina INTEGER,
    talent_pow     INTEGER,
    talent_guts    INTEGER,
    talent_wiz     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_catalog_characters_name ON catalog_characters(name);

CREATE TABLE IF NOT EXISTS catalog_support_cards (
    support_card_id   INTEGER PRIMARY KEY,
    chara_id          INTEGER,
    name              TEXT NOT NULL,
    title             TEXT,
    rarity            INTEGER,
    support_card_type INTEGER,
    skill_set_id      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_catalog_support_cards_name ON catalog_support_cards(name);

-- 协助卡等级上限：support_card_limit，一行一个稀有度。
-- limit_0..limit_4 是 0–4 突的等级上限，「突破一次并升到上限」就是这一档的等级
CREATE TABLE IF NOT EXISTS catalog_support_limits (
    rarity  INTEGER PRIMARY KEY,
    limit_0 INTEGER NOT NULL,
    limit_1 INTEGER NOT NULL,
    limit_2 INTEGER NOT NULL,
    limit_3 INTEGER NOT NULL,
    limit_4 INTEGER NOT NULL
);

-- 协助卡效果类型字典：text_data category 151（名称）/ 154（描述）。
-- 简中服客户端自带这张表，1–31 有官方中文名；101+ 是条件型加成，不在这张字典里
CREATE TABLE IF NOT EXISTS catalog_support_effect_types (
    effect_type INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT
);

-- 协助卡固有效果的官方展示名：text_data category 155，按 unique_effect_id 索引。
-- 一行涵盖同一行的两个槽位；条件型加成（101–120）会把发动条件直接写在这句话里
CREATE TABLE IF NOT EXISTS catalog_support_unique_text (
    support_card_id INTEGER PRIMARY KEY,
    text            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_skills (
    skill_id       INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT,
    rarity         INTEGER,
    skill_category INTEGER,
    tag_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_skills_name ON catalog_skills(name);

-- 马娘卡能学的技能：card_data.available_skill_set_id → available_skill_set.available_skill_set_id
-- （注意不是 available_skill_set.id）；need_rank 是解锁所需星级，0 表示初始可用
CREATE TABLE IF NOT EXISTS catalog_character_skills (
    card_id   INTEGER NOT NULL,
    skill_id  INTEGER NOT NULL,
    need_rank INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (card_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_catalog_character_skills_skill
    ON catalog_character_skills(skill_id);

-- 协助卡训练效果：support_card_effect_table，一行一个效果类型
-- init 是 1 级的值，lv5..lv50 是该等级的值；-1 表示该档不提升（沿用上一档）
CREATE TABLE IF NOT EXISTS catalog_support_effects (
    support_card_id INTEGER NOT NULL,
    effect_type     INTEGER NOT NULL,
    init_value      INTEGER,
    lv5  INTEGER, lv10 INTEGER, lv15 INTEGER, lv20 INTEGER, lv25 INTEGER,
    lv30 INTEGER, lv35 INTEGER, lv40 INTEGER, lv45 INTEGER, lv50 INTEGER,
    PRIMARY KEY (support_card_id, effect_type)
);

-- 协助卡固有加成：support_card_unique_effect，一行两个槽位
CREATE TABLE IF NOT EXISTS catalog_support_unique (
    support_card_id INTEGER NOT NULL,
    slot            INTEGER NOT NULL,
    effect_type     INTEGER,
    value           INTEGER,
    need_lv         INTEGER,
    PRIMARY KEY (support_card_id, slot)
);

-- 协助卡能给的技能：single_mode_hint_gain（hint_gain_type = 0 时 hint_value_1 就是 skill_id）
CREATE TABLE IF NOT EXISTS catalog_support_skills (
    support_card_id INTEGER NOT NULL,
    skill_id        INTEGER NOT NULL,
    need_rank       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (support_card_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_catalog_support_skills_skill
    ON catalog_support_skills(skill_id);
"""


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
