"""把 ``master.mdb`` 导入本地结构化库。

``master.mdb`` 是游戏自带的 SQLite 数据库，本模块只读取、不修改它。

``text_data`` 的结构是 ``(id, category, "index", text)``，其中 ``id`` 与 ``category``
恒等（都是文本类别编号），真正指向实体的是 ``"index"``。因此所有 JOIN 都是
``text_data.category = <类别> AND text_data."index" = <实体表>.id``。

以下是已对照真实 master.mdb 验证过的类别号（日服 2022-12 快照、简中服 2023-10 快照）：

======  ==========================
category  含义
======  ==========================
4        马娘卡全名（含称号，如 ``[特别追梦者]特别周``）
5        马娘卡称号（含方括号）
6        马娘名（不含称号）
47       技能名
48       技能描述
75       协助卡全名（含系列名）
76       协助卡系列名（含方括号）
======  ==========================

字段缺失时导入会降级（例如没有 ``tag_id`` 就留空），保证跨版本可用。
简中服 2023-10 快照的 ``skill_data`` 只有 62 列、日服 2022-12 快照有 77 列，
两边的共有列才是可以无条件依赖的部分。

不支持的文件或结构会抛 :class:`MasterMdbError`（路径不存在、选到目录、不是 SQLite、
缺 ``text_data``），而不是让整表静默变空；异常 message 是可直接展示给用户的中文，
命令行与接口都不需要额外翻译。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.kernel.database import Database
from app.kernel.dataversion import write_data_version

from .schema import ensure_schema

logger = logging.getLogger(__name__)

TEXT_CARD_TITLE = 5
TEXT_CHARA_NAME = 6
TEXT_SKILL_NAME = 47
TEXT_SKILL_DESC = 48
TEXT_SUPPORT_CARD_TITLE = 76


class MasterMdbError(RuntimeError):
    """master.mdb 无法使用：路径不对、不是 SQLite、或结构不符合预期。

    message 一律是可读中文，命令行与接口可以直接展示给用户，不需要堆栈。
    """


@dataclass(slots=True)
class ImportSummary:
    characters: int = 0
    support_cards: int = 0
    skills: int = 0
    text_entries: int = 0
    character_skills: int = 0
    support_effects: int = 0
    support_unique: int = 0
    support_skills: int = 0
    data_version: str = ""
    game_version: str | None = None
    source: str = ""
    skipped: dict[str, str] = field(default_factory=dict)
    # 表 -> 缺失的列；用于判断哪些字段被降级为 NULL
    missing_columns: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "characters": self.characters,
            "support_cards": self.support_cards,
            "skills": self.skills,
            "text_entries": self.text_entries,
            "character_skills": self.character_skills,
            "support_effects": self.support_effects,
            "support_unique": self.support_unique,
            "support_skills": self.support_skills,
            "data_version": self.data_version,
            "game_version": self.game_version,
            "source": self.source,
            "skipped": self.skipped,
            "missing_columns": self.missing_columns,
        }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _open_source(mdb_path: Path) -> sqlite3.Connection:
    """只读打开源库，把「路径不对」「不是 SQLite」翻译成可读中文错误。"""
    if mdb_path.is_dir():
        raise MasterMdbError(f"路径是一个目录，不是 master.mdb 文件：{mdb_path}")
    if not mdb_path.exists():
        raise MasterMdbError(f"找不到 master.mdb：{mdb_path}")
    if not mdb_path.is_file():
        raise MasterMdbError(f"不是普通文件，无法读取：{mdb_path}")

    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_readonly(mdb_path)
        # 连接是惰性的，先真读一次，避免把「不是数据库」的错误延后到取表
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        return conn
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            conn.close()
        raise MasterMdbError(
            f"不是有效的 SQLite 数据库：{mdb_path.name}（{exc}）。"
            "常见原因是文件下载/解压不完整，或者选错了文件——master.mdb 应在游戏目录的 "
            "master 子目录下。"
        ) from exc
    except OSError as exc:
        if conn is not None:
            conn.close()
        raise MasterMdbError(f"无法读取文件：{mdb_path}（{exc}）") from exc


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {row["name"] for row in rows}


def _expr(available: set[str], wanted: dict[str, str], prefix: str = "") -> str:
    """按需拼 SELECT 表达式；源表缺列时用 NULL，避免整表导入失败。

    prefix 用于多表 JOIN 时限定列来源（例如 ``cd.``），否则同名列会产生歧义。
    """
    parts = []
    for alias, column in wanted.items():
        if column in available:
            parts.append(f'{prefix}"{column}" AS "{alias}"')
        else:
            parts.append(f'NULL AS "{alias}"')
    return ", ".join(parts)


def _missing(available: set[str], wanted: dict[str, str]) -> list[str]:
    """返回 wanted 中在源表里不存在的列，用于把降级事实暴露给调用方。"""
    return sorted(column for column in wanted.values() if column not in available)


def _record_missing(summary: ImportSummary, table: str, missing: list[str]) -> None:
    if not missing:
        return
    summary.missing_columns[table] = missing
    logger.warning("%s 缺少列 %s，相关字段降级为 NULL", table, "、".join(missing))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def import_master_mdb(
    db: Database,
    mdb_path: Path,
    *,
    game_version: str | None = None,
    data_version: str | None = None,
) -> ImportSummary:
    summary = ImportSummary(game_version=game_version)
    # 先验证源文件，避免路径写错时把本地库的表也建出来
    source_conn = _open_source(mdb_path)
    ensure_schema(db)
    try:
        tables = _tables(source_conn)
        if "text_data" not in tables:
            found = "、".join(sorted(tables)) or "（空库）"
            raise MasterMdbError(
                f"{mdb_path.name} 缺少 text_data 表，不是有效的 master.mdb。实际找到的表：{found}"
            )

        text_columns = _columns(source_conn, "text_data")
        if not {"category", "index", "text"}.issubset(text_columns):
            raise MasterMdbError(
                "text_data 表结构不符合预期（需要 category/index/text），"
                f"实际列：{'、'.join(sorted(text_columns)) or '（无）'}"
            )

        with db.transaction() as conn:
            conn.execute("DELETE FROM catalog_characters")
            conn.execute("DELETE FROM catalog_support_cards")
            conn.execute("DELETE FROM catalog_skills")
            conn.execute("DELETE FROM catalog_character_skills")
            conn.execute("DELETE FROM catalog_support_effects")
            conn.execute("DELETE FROM catalog_support_unique")
            conn.execute("DELETE FROM catalog_support_skills")

            summary.characters = _import_characters(source_conn, conn, tables, summary)
            summary.support_cards = _import_support_cards(source_conn, conn, tables, summary)
            summary.skills = _import_skills(source_conn, conn, tables, summary)
            summary.character_skills = _import_character_skills(source_conn, conn, tables, summary)
            summary.support_effects = _import_support_effects(source_conn, conn, tables, summary)
            summary.support_unique = _import_support_unique(source_conn, conn, tables, summary)
            summary.support_skills = _import_support_skills(source_conn, conn, tables, summary)

        summary.text_entries = int(
            source_conn.execute("SELECT COUNT(*) AS n FROM text_data").fetchone()["n"]
        )
    except sqlite3.DatabaseError as exc:
        raise MasterMdbError(
            f"读取 {mdb_path.name} 失败（{exc}）。文件可能已损坏，或写入本地库时出错。"
        ) from exc
    finally:
        source_conn.close()

    summary.data_version = data_version or (
        f"mdb-{game_version}" if game_version else f"mdb-{datetime.now(UTC):%Y%m%d}"
    )
    summary.source = f"{mdb_path.name} (sha256:{_sha256(mdb_path)[:12]})"

    write_data_version(
        db,
        version=summary.data_version,
        game_version=game_version,
        source=summary.source,
        datasets={
            "characters": summary.characters,
            "support_cards": summary.support_cards,
            "skills": summary.skills,
            "text_entries": summary.text_entries,
            "character_skills": summary.character_skills,
            "support_effects": summary.support_effects,
            "support_skills": summary.support_skills,
        },
    )
    logger.info(
        "导入完成 | 马娘=%d 协助卡=%d 技能=%d | 马娘技能=%d 效果=%d 固有=%d 卡技能=%d | %s",
        summary.characters,
        summary.support_cards,
        summary.skills,
        summary.character_skills,
        summary.support_effects,
        summary.support_unique,
        summary.support_skills,
        summary.data_version,
    )
    return summary


def _import_characters(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: set[str],
    summary: ImportSummary,
) -> int:
    if "card_data" not in tables:
        summary.skipped["characters"] = "缺少 card_data"
        return 0

    columns = _columns(src, "card_data")
    wanted = {
        "card_id": "id",
        "chara_id": "chara_id",
        "rarity": "default_rarity",
        "limited": "limited_chara",
        "talent_speed": "talent_speed",
        "talent_stamina": "talent_stamina",
        "talent_pow": "talent_pow",
        "talent_guts": "talent_guts",
        "talent_wiz": "talent_wiz",
    }
    if "id" not in columns:
        raise MasterMdbError("card_data 缺少 id 列，无法导入马娘卡")
    _record_missing(summary, "card_data", _missing(columns, wanted))
    select = _expr(columns, wanted, prefix="cd.")
    rows = src.execute(
        f"""
        SELECT {select}, name.text AS name, title.text AS title
        FROM card_data AS cd
        LEFT JOIN text_data AS name
            ON name.category = ? AND name."index" = cd.chara_id
        LEFT JOIN text_data AS title
            ON title.category = ? AND title."index" = cd.id
        """,
        (TEXT_CHARA_NAME, TEXT_CARD_TITLE),
    ).fetchall()

    payload = []
    for row in rows:
        if row["card_id"] is None:
            continue
        payload.append(
            (
                row["card_id"],
                row["chara_id"] if row["chara_id"] is not None else row["card_id"],
                row["name"] or f"#{row['card_id']}",
                row["title"],
                row["rarity"],
                row["limited"] or 0,
                row["talent_speed"],
                row["talent_stamina"],
                row["talent_pow"],
                row["talent_guts"],
                row["talent_wiz"],
            )
        )
    dst.executemany(
        "INSERT OR REPLACE INTO catalog_characters (card_id, chara_id, name, title, rarity, "
        "limited, talent_speed, talent_stamina, talent_pow, talent_guts, talent_wiz) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        payload,
    )
    return len(payload)


def _import_support_cards(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: set[str],
    summary: ImportSummary,
) -> int:
    if "support_card_data" not in tables:
        summary.skipped["support_cards"] = "缺少 support_card_data"
        return 0

    columns = _columns(src, "support_card_data")
    wanted = {
        "support_card_id": "id",
        "chara_id": "chara_id",
        "rarity": "rarity",
        "support_card_type": "support_card_type",
        "skill_set_id": "skill_set_id",
    }
    if "id" not in columns:
        raise MasterMdbError("support_card_data 缺少 id 列，无法导入协助卡")
    _record_missing(summary, "support_card_data", _missing(columns, wanted))
    select = _expr(columns, wanted, prefix="scd.")
    rows = src.execute(
        f"""
        SELECT {select}, title.text AS title, name.text AS name
        FROM support_card_data AS scd
        LEFT JOIN text_data AS title
            ON title.category = ? AND title."index" = scd.id
        LEFT JOIN text_data AS name
            ON name.category = ? AND name."index" = scd.chara_id
        """,
        (TEXT_SUPPORT_CARD_TITLE, TEXT_CHARA_NAME),
    ).fetchall()

    payload = []
    for row in rows:
        if row["support_card_id"] is None:
            continue
        payload.append(
            (
                row["support_card_id"],
                row["chara_id"],
                row["name"] or f"#{row['support_card_id']}",
                row["title"],
                row["rarity"],
                row["support_card_type"],
                row["skill_set_id"],
            )
        )
    dst.executemany(
        "INSERT OR REPLACE INTO catalog_support_cards "
        "(support_card_id, chara_id, name, title, rarity, support_card_type, skill_set_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        payload,
    )
    return len(payload)


def _import_skills(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: set[str],
    summary: ImportSummary,
) -> int:
    if "skill_data" not in tables:
        summary.skipped["skills"] = "缺少 skill_data"
        return 0

    columns = _columns(src, "skill_data")
    wanted = {
        "skill_id": "id",
        "rarity": "rarity",
        "skill_category": "skill_category",
        "tag_id": "tag_id",
    }
    if "id" not in columns:
        raise MasterMdbError("skill_data 缺少 id 列，无法导入技能")
    _record_missing(summary, "skill_data", _missing(columns, wanted))
    select = _expr(columns, wanted, prefix="sd.")
    rows = src.execute(
        f"""
        SELECT {select}, name.text AS name, desc_text.text AS description
        FROM skill_data AS sd
        LEFT JOIN text_data AS name
            ON name.category = ? AND name."index" = sd.id
        LEFT JOIN text_data AS desc_text
            ON desc_text.category = ? AND desc_text."index" = sd.id
        """,
        (TEXT_SKILL_NAME, TEXT_SKILL_DESC),
    ).fetchall()

    payload = []
    for row in rows:
        if row["skill_id"] is None:
            continue
        payload.append(
            (
                row["skill_id"],
                row["name"] or f"#{row['skill_id']}",
                row["description"],
                row["rarity"],
                row["skill_category"],
                row["tag_id"],
            )
        )
    dst.executemany(
        "INSERT OR REPLACE INTO catalog_skills "
        "(skill_id, name, description, rarity, skill_category, tag_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        payload,
    )
    return len(payload)


def _import_character_skills(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: set[str],
    summary: ImportSummary,
) -> int:
    """马娘卡能学的技能。

    连接键是坑：``card_data.available_skill_set_id`` 对应
    ``available_skill_set.available_skill_set_id``，**不是** ``available_skill_set.id``
    （用错了会匹配 0 行）。``need_rank`` 是解锁所需星级，0 表示初始就有。
    """
    if not {"card_data", "available_skill_set"}.issubset(tables):
        summary.skipped["character_skills"] = "缺少 card_data / available_skill_set"
        return 0
    if "available_skill_set_id" not in _columns(src, "card_data"):
        summary.skipped["character_skills"] = "card_data 缺 available_skill_set_id"
        return 0

    rows = src.execute(
        """
        SELECT cd.id AS card_id, s.skill_id AS skill_id, s.need_rank AS need_rank
        FROM card_data AS cd
        JOIN available_skill_set AS s
            ON s.available_skill_set_id = cd.available_skill_set_id
        WHERE cd.available_skill_set_id IS NOT NULL AND s.skill_id IS NOT NULL
        """
    ).fetchall()
    payload = [
        (row["card_id"], row["skill_id"], row["need_rank"] or 0)
        for row in rows
        if row["card_id"] is not None
    ]
    dst.executemany(
        "INSERT OR REPLACE INTO catalog_character_skills (card_id, skill_id, need_rank) "
        "VALUES (?, ?, ?)",
        payload,
    )
    return len(payload)


def _import_support_effects(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: set[str],
    summary: ImportSummary,
) -> int:
    """协助卡训练效果数值：``support_card_effect_table``。

    ``init`` 是初始值，``limit_lv5..limit_lv50`` 是各突破等级的值，
    ``-1`` 表示该档不提升（沿用上一档），原样存下来让展示层决定怎么显示。
    """
    if "support_card_effect_table" not in tables:
        summary.skipped["support_effects"] = "缺少 support_card_effect_table"
        return 0

    columns = _columns(src, "support_card_effect_table")
    if not {"id", "type"}.issubset(columns):
        summary.skipped["support_effects"] = "support_card_effect_table 缺 id / type"
        return 0

    wanted = {
        "init_value": "init",
        **{f"lv{level}": f"limit_lv{level}" for level in range(5, 51, 5)},
    }
    level_columns = ", ".join(
        f'"{wanted[key]}" AS "{key}"' if wanted[key] in columns else f'NULL AS "{key}"'
        for key in wanted
    )
    rows = src.execute(
        f'SELECT "id" AS support_card_id, "type" AS effect_type, {level_columns} '
        "FROM support_card_effect_table"
    ).fetchall()

    payload = [
        (
            row["support_card_id"],
            row["effect_type"],
            row["init_value"],
            row["lv5"],
            row["lv10"],
            row["lv15"],
            row["lv20"],
            row["lv25"],
            row["lv30"],
            row["lv35"],
            row["lv40"],
            row["lv45"],
            row["lv50"],
        )
        for row in rows
        if row["support_card_id"] is not None and row["effect_type"] is not None
    ]
    dst.executemany(
        "INSERT OR REPLACE INTO catalog_support_effects "
        "(support_card_id, effect_type, init_value, lv5, lv10, lv15, lv20, lv25, lv30, "
        "lv35, lv40, lv45, lv50) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        payload,
    )
    return len(payload)


def _import_support_unique(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: set[str],
    summary: ImportSummary,
) -> int:
    """协助卡固有加成：``support_card_unique_effect`` 一行两个槽位。"""
    if "support_card_unique_effect" not in tables:
        summary.skipped["support_unique"] = "缺少 support_card_unique_effect"
        return 0

    columns = _columns(src, "support_card_unique_effect")
    if not {"id", "type_0"}.issubset(columns):
        summary.skipped["support_unique"] = "support_card_unique_effect 缺 id / type_0"
        return 0

    rows = src.execute("SELECT * FROM support_card_unique_effect").fetchall()
    payload: list[tuple] = []
    for row in rows:
        data = dict(row)
        need_lv = data.get("lv")
        for slot in (0, 1):
            effect_type = data.get(f"type_{slot}")
            value = data.get(f"value_{slot}")
            if effect_type is None:
                continue
            payload.append((data["id"], slot, effect_type, value, need_lv))
    dst.executemany(
        "INSERT OR REPLACE INTO catalog_support_unique "
        "(support_card_id, slot, effect_type, value, need_lv) VALUES (?, ?, ?, ?, ?)",
        payload,
    )
    return len(payload)


def _import_support_skills(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    tables: set[str],
    summary: ImportSummary,
) -> int:
    """协助卡能给的技能（提示技能）。

    ``single_mode_hint_gain`` 里只有 ``hint_gain_type = 0`` 的行，
    ``hint_value_1`` 才是 ``skill_data.id``；其它取值是别的含义，不能混用。
    """
    if "single_mode_hint_gain" not in tables:
        summary.skipped["support_skills"] = "缺少 single_mode_hint_gain"
        return 0

    columns = _columns(src, "single_mode_hint_gain")
    if not {"support_card_id", "hint_gain_type", "hint_value_1"}.issubset(columns):
        summary.skipped["support_skills"] = "single_mode_hint_gain 缺必要列"
        return 0

    rows = src.execute(
        "SELECT DISTINCT support_card_id, hint_value_1 AS skill_id "
        "FROM single_mode_hint_gain WHERE hint_gain_type = 0"
    ).fetchall()
    payload = [
        (row["support_card_id"], row["skill_id"], 0)
        for row in rows
        if row["support_card_id"] is not None and row["skill_id"] is not None
    ]
    dst.executemany(
        "INSERT OR REPLACE INTO catalog_support_skills (support_card_id, skill_id, need_rank) "
        "VALUES (?, ?, ?)",
        payload,
    )
    return len(payload)
