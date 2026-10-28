"""``succession_*`` 表结构。

四张表回答「两只（或三只）马娘之间有多少共享关系组」以及「六人继承布局的总分与等级」：

- ``succession_relations``：关系组编号 → 点数（``succession_relation``）
- ``succession_members``：关系组编号 ↔ 角色（``succession_relation_member``）
- ``succession_ranks``：分数区间 → 相性等级（``succession_relation_rank``）
- ``succession_charas``：参与关系组的角色与名字（``chara_data`` + ``text_data`` 6）
"""

from __future__ import annotations

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS succession_relations (
    relation_type  INTEGER PRIMARY KEY,
    relation_point INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS succession_members (
    relation_type INTEGER NOT NULL,
    chara_id      INTEGER NOT NULL,
    PRIMARY KEY (relation_type, chara_id)
);

CREATE INDEX IF NOT EXISTS idx_succession_members_chara
    ON succession_members(chara_id);

CREATE TABLE IF NOT EXISTS succession_ranks (
    relation_rank  INTEGER PRIMARY KEY,
    rank_value_min INTEGER NOT NULL,
    rank_value_max INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS succession_charas (
    chara_id INTEGER PRIMARY KEY,
    name     TEXT NOT NULL
);
"""

META_SOURCE = "succession.catalog_source"


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
