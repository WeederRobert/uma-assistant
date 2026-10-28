"""从 ``master.mdb`` 导入技能的效果参数、条件与来源反查。

只读打开源库，字段映射全部显式写在这里。缺表不会让整次导入失败：技能主表
（``skill_data``）缺失时直接报错，其余反查表缺失时记入 ``skipped`` 并跳过——
这样日服 / 简中服列数不同、或某一版没有进化表时，其余数据仍然可用。

两条反查链路的连接键都是**在真实快照上验证过的**，写错一个键就会静默匹配 0 行：

- 马娘 → 技能：``card_data.available_skill_set_id`` 必须连
  ``available_skill_set.available_skill_set_id``（**不是** ``available_skill_set.id``）。
- 协助卡 → 提示技能：``single_mode_hint_gain.hint_gain_type = 0`` 时
  ``hint_value_1`` 就是 ``skill_id``；``hint_gain_type != 0`` 的 ``hint_value_1`` 是别的含义，
  不能混用。
- 协助卡 → 技能组：``support_card_data.skill_set_id / 1000`` 才是 ``skill_set.id``
  （``9081001 → 9081``）。这条路粒度到角色组而不是单卡，见 :mod:`modules.skills.service`。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.kernel.database import Database

from . import store
from .schema import ensure_schema

logger = logging.getLogger(__name__)

TEXT_CARD_TITLE = 5
TEXT_CHARA_NAME = 6
TEXT_SKILL_NAME = 47
TEXT_SKILL_DESC = 48
TEXT_SUPPORT_CARD_TITLE = 76

REQUIRED_TABLES = ("skill_data", "text_data")

# 槽位（*_N_* 里的 N）固定为 1 与 2，效果（*_{N}_{M}）最多 3 条
SLOTS = (1, 2)
EFFECT_SEQS = (1, 2, 3)


class SkillsSourceError(RuntimeError):
    """master.mdb 不可用：路径不对、不是 SQLite、或缺技能主表。"""


@dataclass(slots=True)
class CatalogSummary:
    skills: int = 0
    slots: int = 0
    effects: int = 0
    characters: int = 0
    support_cards: int = 0
    upgrades: int = 0
    text_entries: int = 0
    data_version: str = ""
    game_version: str | None = None
    source: str = ""
    mdb_path: str = ""
    mdb_mtime: float | None = None
    skipped: dict[str, str] = field(default_factory=dict)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "skills": self.skills,
            "slots": self.slots,
            "effects": self.effects,
            "characters": self.characters,
            "support_cards": self.support_cards,
            "upgrades": self.upgrades,
            "text_entries": self.text_entries,
            "data_version": self.data_version,
            "game_version": self.game_version,
            "source": self.source,
            "skipped": self.skipped,
            "missing_columns": self.missing_columns,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_readonly(path: Path) -> sqlite3.Connection:
    if path.is_dir():
        raise SkillsSourceError(f"这是一个目录，不是 master.mdb 文件：{path}")
    if not path.exists():
        raise SkillsSourceError(
            f"找不到 master.mdb：{path}。先在「数据同步」里导入一次客户端数据。"
        )
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        raise SkillsSourceError(f"不是有效的 SQLite 数据库：{path.name}（{exc}）") from exc
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _expr(available: set[str], wanted: dict[str, str], prefix: str = "") -> str:
    """按需拼 SELECT 表达式；源表缺列时用 NULL，避免整表导入失败。"""
    return ", ".join(
        f'{prefix}"{column}" AS "{alias}"' if column in available else f'NULL AS "{alias}"'
        for alias, column in wanted.items()
    )


def _missing(available: set[str], wanted: dict[str, str]) -> list[str]:
    return sorted(column for column in wanted.values() if column not in available)


def _record_missing(summary: CatalogSummary, table: str, missing: list[str]) -> None:
    if not missing:
        return
    summary.missing_columns[table] = missing
    logger.warning("%s 缺少列 %s，相关字段降级为 NULL", table, "、".join(missing))


def import_catalog(
    db: Database,
    mdb_path: Path,
    *,
    game_version: str | None = None,
    data_version: str | None = None,
) -> CatalogSummary:
    summary = CatalogSummary(game_version=game_version)
    conn = _open_readonly(mdb_path)
    ensure_schema(db)
    try:
        tables = _tables(conn)
        absent = [name for name in REQUIRED_TABLES if name not in tables]
        if absent:
            raise SkillsSourceError(
                f"{mdb_path.name} 缺少 {('、'.join(absent))}，无法导入技能数据。"
                "请确认选的是游戏目录 master 子目录下的 master.mdb。"
            )

        skills, slots, effects = _read_skills(conn, summary)
        characters = _read_characters(conn, tables, summary)
        support_cards = _read_support_cards(conn, tables, summary)
        upgrades = _read_upgrades(conn, tables, summary)

        store.replace_catalog(db, skills, slots, effects, characters, support_cards, upgrades)

        summary.skills = len(skills)
        summary.slots = len(slots)
        summary.effects = len(effects)
        summary.characters = len(characters)
        summary.support_cards = len(support_cards)
        summary.upgrades = len(upgrades)
        summary.text_entries = int(
            conn.execute("SELECT COUNT(*) AS n FROM text_data").fetchone()["n"]
        )
    except sqlite3.DatabaseError as exc:
        raise SkillsSourceError(f"读取 {mdb_path.name} 失败（{exc}）") from exc
    finally:
        conn.close()

    summary.data_version = data_version or (
        f"mdb-{game_version}" if game_version else f"mdb-{datetime.now(UTC):%Y%m%d}"
    )
    summary.source = f"{mdb_path.name} (sha256:{_sha256(mdb_path)[:12]})"
    summary.mdb_path = str(mdb_path.resolve())
    summary.mdb_mtime = mdb_path.stat().st_mtime

    store.set_source(
        db,
        store.META_CATALOG,
        {
            **summary.as_dict(),
            "mdb_path": summary.mdb_path,
            "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "mdb_mtime": summary.mdb_mtime,
        },
    )
    logger.info(
        "技能导入完成 | 技能=%d 效果=%d 马娘来源=%d 协助卡来源=%d 进化=%d | %s",
        summary.skills,
        summary.effects,
        summary.characters,
        summary.support_cards,
        summary.upgrades,
        summary.data_version,
    )
    return summary


def _read_skills(
    conn: sqlite3.Connection, summary: CatalogSummary
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    columns = _columns(conn, "skill_data")
    wanted: dict[str, str] = {
        "skill_id": "id",
        "rarity": "rarity",
        "skill_category": "skill_category",
        "tag_id": "tag_id",
        "group_id": "group_id",
        "group_rate": "group_rate",
        "grade_value": "grade_value",
        "icon_id": "icon_id",
        "disp_order": "disp_order",
        "plate_type": "plate_type",
        "is_general_skill": "is_general_skill",
        "activate_lot": "activate_lot",
        "priority": "priority",
        "potential_per_default": "potential_per_default",
        "exp_type": "exp_type",
        "unique_skill_id_1": "unique_skill_id_1",
        "unique_skill_id_2": "unique_skill_id_2",
        "start_date": "start_date",
        "end_date": "end_date",
    }
    for slot in SLOTS:
        wanted[f"precondition_{slot}"] = f"precondition_{slot}"
        wanted[f"condition_{slot}"] = f"condition_{slot}"
        wanted[f"ability_time_{slot}"] = f"float_ability_time_{slot}"
        wanted[f"ability_time_usage_{slot}"] = f"ability_time_usage_{slot}"
        wanted[f"cooldown_{slot}"] = f"float_cooldown_time_{slot}"
        for seq in EFFECT_SEQS:
            for name in (
                "ability_type",
                "ability_value_usage",
                "ability_value_level_usage",
                "float_ability_value",
                "target_type",
                "target_value",
                "additional_activate_type",
            ):
                wanted[f"{name}_{slot}_{seq}"] = f"{name}_{slot}_{seq}"

    if "id" not in columns:
        raise SkillsSourceError("skill_data 缺少 id 列，无法导入技能")
    _record_missing(summary, "skill_data", _missing(columns, wanted))

    select = _expr(columns, wanted, prefix="sd.")
    rows = conn.execute(
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

    now = store.now_iso()
    skills: list[tuple[Any, ...]] = []
    slots: list[tuple[Any, ...]] = []
    effects: list[tuple[Any, ...]] = []
    for row in rows:
        skill_id = row["skill_id"]
        if skill_id is None:
            continue
        skills.append(
            (
                skill_id,
                row["name"] or f"#{skill_id}",
                row["description"],
                row["rarity"],
                row["skill_category"],
                row["tag_id"],
                row["group_id"],
                row["group_rate"],
                row["grade_value"],
                row["icon_id"],
                row["disp_order"],
                row["plate_type"],
                row["is_general_skill"],
                row["activate_lot"],
                row["priority"],
                row["potential_per_default"],
                row["exp_type"],
                row["unique_skill_id_1"],
                row["unique_skill_id_2"],
                row["start_date"],
                row["end_date"],
                now,
            )
        )
        for slot in SLOTS:
            slot_effects = [
                (
                    skill_id,
                    slot,
                    seq,
                    row[f"ability_type_{slot}_{seq}"],
                    row[f"ability_value_usage_{slot}_{seq}"],
                    row[f"ability_value_level_usage_{slot}_{seq}"],
                    row[f"float_ability_value_{slot}_{seq}"],
                    row[f"target_type_{slot}_{seq}"],
                    row[f"target_value_{slot}_{seq}"],
                    row[f"additional_activate_type_{slot}_{seq}"],
                )
                for seq in EFFECT_SEQS
                if row[f"ability_type_{slot}_{seq}"]
            ]
            if slot_effects:
                effects.extend(slot_effects)
            has_condition = bool(row[f"precondition_{slot}"] or row[f"condition_{slot}"])
            if has_condition or slot_effects:
                slots.append(
                    (
                        skill_id,
                        slot,
                        row[f"precondition_{slot}"],
                        row[f"condition_{slot}"],
                        row[f"ability_time_{slot}"],
                        row[f"ability_time_usage_{slot}"],
                        row[f"cooldown_{slot}"],
                    )
                )
    return skills, slots, effects


def _read_characters(
    conn: sqlite3.Connection, tables: set[str], summary: CatalogSummary
) -> list[tuple[Any, ...]]:
    """马娘 → 技能：``card_data.available_skill_set_id`` 连 ``available_skill_set_id``。"""
    needed = ("card_data", "available_skill_set")
    absent = [name for name in needed if name not in tables]
    if absent:
        summary.skipped["skill_characters"] = f"缺少 {('、'.join(absent))}"
        return []

    available = _columns(conn, "available_skill_set")
    if not {"available_skill_set_id", "skill_id"}.issubset(available):
        summary.skipped["skill_characters"] = (
            "available_skill_set 缺 available_skill_set_id/skill_id"
        )
        return []

    card_columns = _columns(conn, "card_data")
    if "available_skill_set_id" not in card_columns:
        summary.skipped["skill_characters"] = "card_data 缺 available_skill_set_id"
        return []

    need_rank = "av.need_rank" if "need_rank" in available else "NULL"
    rows = conn.execute(
        f"""
        SELECT cd.id AS card_id, av.skill_id AS skill_id, {need_rank} AS need_rank,
               chara.text AS name, title.text AS title
        FROM card_data AS cd
        JOIN available_skill_set AS av
            ON av.available_skill_set_id = cd.available_skill_set_id
        LEFT JOIN text_data AS chara
            ON chara.category = ? AND chara."index" = cd.chara_id
        LEFT JOIN text_data AS title
            ON title.category = ? AND title."index" = cd.id
        WHERE cd.available_skill_set_id != 0
        """,
        (TEXT_CHARA_NAME, TEXT_CARD_TITLE),
    ).fetchall()
    return [
        (row["skill_id"], row["card_id"], row["need_rank"], row["name"], row["title"])
        for row in rows
        if row["skill_id"] is not None and row["card_id"] is not None
    ]


def _read_support_cards(
    conn: sqlite3.Connection, tables: set[str], summary: CatalogSummary
) -> list[tuple[Any, ...]]:
    """协助卡 → 技能：只有 ``single_mode_hint_gain`` 是逐卡精确的。

    ``support_card_data.skill_set_id / 1000`` 也能连到 ``skill_set``，但经核实已退化：
    简中服 2.0.2 快照里 362 张卡只解析出 2 个组（``9081`` 覆盖 347 张、``9089`` 覆盖 15 张）、
    合计 7 个技能，而且这 7 个技能全部已被提示表覆盖。按「宁缺不猜」的原则不做这条映射，
    免得把「同组」当成「这张卡给这个技能」。
    """
    if "support_card_data" not in tables:
        summary.skipped["skill_support_cards"] = "缺少 support_card_data"
        return []
    if "single_mode_hint_gain" not in tables:
        summary.skipped["skill_support_cards"] = "缺少 single_mode_hint_gain"
        return []
    return _read_hints(conn)


def _read_hints(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    columns = _columns(conn, "single_mode_hint_gain")
    if not {"hint_value_1", "support_card_id"}.issubset(columns):
        return []
    if "hint_gain_type" not in columns:
        return []
    hint_group = "h.hint_group" if "hint_group" in columns else "NULL"
    rows = conn.execute(
        f"""
        SELECT DISTINCT h.hint_value_1 AS skill_id, h.support_card_id AS support_card_id,
               {hint_group} AS hint_group, chara.text AS name, title.text AS title
        FROM single_mode_hint_gain AS h
        JOIN support_card_data AS scd ON scd.id = h.support_card_id
        LEFT JOIN text_data AS chara
            ON chara.category = ? AND chara."index" = scd.chara_id
        LEFT JOIN text_data AS title
            ON title.category = ? AND title."index" = scd.id
        WHERE h.hint_gain_type = 0
        """,
        (TEXT_CHARA_NAME, TEXT_SUPPORT_CARD_TITLE),
    ).fetchall()
    return [
        (
            row["skill_id"],
            row["support_card_id"],
            "hint",
            row["hint_group"],
            row["name"],
            row["title"],
        )
        for row in rows
        if row["skill_id"] is not None
    ]


def _read_upgrades(
    conn: sqlite3.Connection, tables: set[str], summary: CatalogSummary
) -> list[tuple[Any, ...]]:
    payload: list[tuple[Any, ...]] = []

    if "skill_upgrade_speciality" in tables:
        columns = _columns(conn, "skill_upgrade_speciality")
        if {"base_skill_id", "skill_id"}.issubset(columns):
            scenario = "scenario_id" if "scenario_id" in columns else "NULL"
            for row in conn.execute(
                f"SELECT base_skill_id, skill_id, {scenario} AS scenario_id "
                "FROM skill_upgrade_speciality"
            ):
                payload.append(
                    (
                        row["skill_id"],
                        "speciality",
                        row["base_skill_id"],
                        row["scenario_id"],
                        None,
                        None,
                        None,
                    )
                )
        else:
            summary.skipped["skill_upgrades_speciality"] = "缺 base_skill_id/skill_id"
    else:
        summary.skipped["skill_upgrades_speciality"] = "缺少 skill_upgrade_speciality"

    if "skill_upgrade_description" in tables:
        columns = _columns(conn, "skill_upgrade_description")
        if {"card_id", "rank", "skill_id"}.issubset(columns):
            for row in conn.execute(
                """
                SELECT d.card_id AS card_id, d.rank AS rank, d.skill_id AS skill_id,
                       title.text AS card_title
                FROM skill_upgrade_description AS d
                LEFT JOIN text_data AS title
                    ON title.category = ? AND title."index" = d.card_id
                """,
                (TEXT_CARD_TITLE,),
            ):
                payload.append(
                    (
                        row["skill_id"],
                        "awakening",
                        None,
                        None,
                        row["card_id"],
                        row["card_title"],
                        row["rank"],
                    )
                )
        else:
            summary.skipped["skill_upgrades_awakening"] = "缺 card_id/rank/skill_id"
    else:
        summary.skipped["skill_upgrades_awakening"] = "缺少 skill_upgrade_description"

    return payload
