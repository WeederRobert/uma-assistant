"""``skills_*`` 表结构。

六组表分工：

- ``skills_catalog``：技能主记录（``skill_data`` 一行一个技能）
- ``skills_slots``：发动条件与节奏（``precondition_N`` / ``condition_N`` 原始表达式、
  持续时间、冷却），一个技能最多两个槽位
- ``skills_effects``：效果参数（``ability_type_N_M`` / ``float_ability_value_N_M`` /
  ``target_type_N_M``），一个槽位最多三条
- ``skills_characters``：反查「哪些马娘有这个技能」（``available_skill_set`` × ``card_data``）
- ``skills_support_cards``：反查「哪张协助卡给这个技能」（``single_mode_hint_gain``）
- ``skills_upgrades``：进化 / 觉醒前后的对应关系

``slot`` 与 ``seq`` 都是主数据里的原始编号：``slot = 1`` 对应 ``*_1_*`` 系列列，
``slot = 2`` 对应 ``*_2_*``；``seq`` 是槽位内的第几条效果。保留原始编号是为了
能对着 ``skill_data`` 的列名反查，不做压缩重排。

反查表里的 ``name`` / ``title`` 是导入时从同一份 ``text_data`` 解出来的展示名，
写进本模块自己的表，避免运行时再去读 ``catalog_*`` 的表结构。
"""

from __future__ import annotations

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS skills_catalog (
    skill_id       INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT,
    rarity         INTEGER,
    skill_category INTEGER,
    tag_id         TEXT,
    group_id       INTEGER,
    group_rate     INTEGER,
    grade_value    INTEGER,
    icon_id        INTEGER,
    disp_order     INTEGER,
    plate_type     INTEGER,
    is_general_skill INTEGER,
    activate_lot   INTEGER,
    priority       INTEGER,
    potential_per_default INTEGER,
    exp_type       INTEGER,
    unique_skill_id_1 INTEGER,
    unique_skill_id_2 INTEGER,
    start_date     INTEGER,
    end_date       INTEGER,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skills_catalog_name ON skills_catalog(name);
CREATE INDEX IF NOT EXISTS idx_skills_catalog_category
    ON skills_catalog(skill_category, rarity);

CREATE TABLE IF NOT EXISTS skills_slots (
    skill_id         INTEGER NOT NULL,
    slot             INTEGER NOT NULL,
    precondition     TEXT,
    condition        TEXT,
    ability_time     REAL,
    ability_time_usage INTEGER,
    cooldown         REAL,
    PRIMARY KEY (skill_id, slot)
);

CREATE TABLE IF NOT EXISTS skills_effects (
    skill_id     INTEGER NOT NULL,
    slot         INTEGER NOT NULL,
    seq          INTEGER NOT NULL,
    ability_type INTEGER NOT NULL,
    value_usage  INTEGER,
    value_level_usage INTEGER,
    value        REAL,
    target_type  INTEGER,
    target_value REAL,
    activate_type INTEGER,
    PRIMARY KEY (skill_id, slot, seq)
);

CREATE TABLE IF NOT EXISTS skills_characters (
    skill_id  INTEGER NOT NULL,
    card_id   INTEGER NOT NULL,
    need_rank INTEGER,
    name      TEXT,
    title     TEXT,
    PRIMARY KEY (skill_id, card_id)
);

CREATE INDEX IF NOT EXISTS idx_skills_characters_card ON skills_characters(card_id);

CREATE TABLE IF NOT EXISTS skills_support_cards (
    skill_id        INTEGER NOT NULL,
    support_card_id INTEGER NOT NULL,
    source          TEXT    NOT NULL,
    hint_group      INTEGER,
    name            TEXT,
    title           TEXT,
    PRIMARY KEY (skill_id, support_card_id, source)
);

CREATE INDEX IF NOT EXISTS idx_skills_support_cards_card
    ON skills_support_cards(support_card_id);

CREATE TABLE IF NOT EXISTS skills_upgrades (
    skill_id      INTEGER NOT NULL,
    source        TEXT    NOT NULL,
    base_skill_id INTEGER,
    scenario_id   INTEGER,
    card_id       INTEGER,
    card_title    TEXT,
    rank          INTEGER,
    PRIMARY KEY (skill_id, source)
);

CREATE INDEX IF NOT EXISTS idx_skills_upgrades_base ON skills_upgrades(base_skill_id);
CREATE INDEX IF NOT EXISTS idx_skills_upgrades_card ON skills_upgrades(card_id);
"""

META_CATALOG = "skills.catalog_source"


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
