"""从 ``master.mdb`` 导入卡池排期与精选卡片。

``master.mdb`` 的 ``gacha_data`` 是**客户端内置的卡池排期**，含未来卡池——所以
「下一期卡池是什么」不需要外部公告来源。已核实：简中服 2.0.2 快照里
``id=30204`` 的排期是 2026-10-24 12:00 → 11-03 11:59、精选「跳舞城[GLITTER！]」，
与 BWiki 简中服当期卡池逐字对得上。

字段来源：

======  ==========================================
来源                       含义
======  ==========================================
``gacha_data.start_date``  unix 秒（与其它 mdb 表一致）
``gacha_data.end_date``    unix 秒
``text_data`` 26           卡池名称（348/348 都有）
``text_data`` 13           卡池说明（348/348 都有）
``gacha_available``        卡池内所有卡；``is_pickup = 1`` 才是精选
``card_data``              ``card_type = 1`` 的马娘卡（名称走 ``text_data`` 6 / 5）
``support_card_data``      ``card_type = 2`` 的协助卡（名称走 ``text_data`` 6 / 76）
======  ==========================================

``gacha_data.type`` **没有字典表**，导入时原样保留，展示层只回显编号。
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

TEXT_GACHA_TITLE = 26
TEXT_GACHA_DESC = 13
TEXT_CARD_TITLE = 5
TEXT_CHARA_NAME = 6
TEXT_SUPPORT_CARD_TITLE = 76

REQUIRED_TABLES = ("gacha_data", "text_data")

# 客户端用这个时间点之后的日期表示「不结束」或「未定档」，两处判断共用同一个阈值
SENTINEL_TS = 2_500_000_000  # ≈ 2049-03-22


class GachaSourceError(RuntimeError):
    """master.mdb 不可用：路径不对、不是 SQLite、或缺卡池主表。"""


@dataclass(slots=True)
class CatalogSummary:
    banners: int = 0
    pickups: int = 0
    upcoming: int = 0
    with_pickups: int = 0
    data_version: str = ""
    game_version: str | None = None
    source: str = ""
    mdb_path: str = ""
    mdb_mtime: float | None = None
    skipped: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "banners": self.banners,
            "pickups": self.pickups,
            "upcoming": self.upcoming,
            "with_pickups": self.with_pickups,
            "data_version": self.data_version,
            "game_version": self.game_version,
            "source": self.source,
            "skipped": self.skipped,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_readonly(path: Path) -> sqlite3.Connection:
    if path.is_dir():
        raise GachaSourceError(f"这是一个目录，不是 master.mdb 文件：{path}")
    if not path.exists():
        raise GachaSourceError(f"找不到 master.mdb：{path}。先在「数据同步」里导入一次客户端数据。")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        raise GachaSourceError(f"不是有效的 SQLite 数据库：{path.name}（{exc}）") from exc
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


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
            raise GachaSourceError(
                f"{mdb_path.name} 缺少 {('、'.join(absent))}，无法导入卡池排期。"
                "请确认选的是游戏目录 master 子目录下的 master.mdb。"
            )

        banners = _read_banners(conn)
        pickups = _read_pickups(conn, tables, summary)
        store.replace_catalog(db, banners, pickups)

        now = int(datetime.now(UTC).timestamp())
        summary.banners = len(banners)
        summary.pickups = len(pickups)
        # 未定档占位（开始时间在 2049 之后）不算「未来卡池」
        summary.upcoming = sum(
            1 for row in banners if (row[5] or 0) > now and (row[5] or 0) < SENTINEL_TS
        )
        summary.with_pickups = len({row[0] for row in pickups})
    except sqlite3.DatabaseError as exc:
        raise GachaSourceError(f"读取 {mdb_path.name} 失败（{exc}）") from exc
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
            "imported_at": store.now_iso(),
            "mdb_mtime": summary.mdb_mtime,
        },
    )
    logger.info(
        "卡池导入完成 | 卡池=%d（未来 %d）精选=%d 条 | %s",
        summary.banners,
        summary.upcoming,
        summary.pickups,
        summary.data_version,
    )
    return summary


def _read_banners(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(gacha_data)")}
    if "id" not in columns:
        raise GachaSourceError("gacha_data 缺少 id 列，无法导入卡池")
    wanted = {
        "type": "type",
        "card_type": "card_type",
        "start_ts": "start_date",
        "end_ts": "end_date",
    }
    select = ", ".join(
        f'"{column}" AS "{alias}"' if column in columns else f'NULL AS "{alias}"'
        for alias, column in wanted.items()
    )
    now = store.now_iso()
    rows = conn.execute(
        f"""
        SELECT g.id AS gacha_id, {select}, title.text AS title, desc_text.text AS description
        FROM gacha_data AS g
        LEFT JOIN text_data AS title
            ON title.category = ? AND title."index" = g.id
        LEFT JOIN text_data AS desc_text
            ON desc_text.category = ? AND desc_text."index" = g.id
        """,
        (TEXT_GACHA_TITLE, TEXT_GACHA_DESC),
    ).fetchall()
    return [
        (
            row["gacha_id"],
            row["type"],
            row["card_type"],
            (row["title"] or f"卡池 #{row['gacha_id']}").strip(),
            (row["description"] or "").strip() or None,
            row["start_ts"],
            row["end_ts"],
            now,
        )
        for row in rows
        if row["gacha_id"] is not None
    ]


def _read_pickups(
    conn: sqlite3.Connection, tables: set[str], summary: CatalogSummary
) -> list[tuple[Any, ...]]:
    if "gacha_available" not in tables:
        summary.skipped["pickups"] = "缺少 gacha_available"
        return []

    available = {row["name"] for row in conn.execute("PRAGMA table_info(gacha_available)")}
    if "is_pickup" not in available:
        summary.skipped["pickups"] = "gacha_available 缺 is_pickup"
        return []

    order = "ga.recommend_order" if "recommend_order" in available else "NULL"
    rows = conn.execute(
        f"""
        SELECT ga.gacha_id, ga.card_type, ga.card_id, ga.rarity, {order} AS recommend_order,
               chara.text AS chara_name, card_title.text AS card_title,
               supp_chara.text AS supp_name, supp_title.text AS supp_title
        FROM gacha_available AS ga
        LEFT JOIN card_data AS cd
            ON cd.id = ga.card_id AND ga.card_type = 1
        LEFT JOIN text_data AS chara
            ON chara.category = ? AND chara."index" = cd.chara_id
        LEFT JOIN text_data AS card_title
            ON card_title.category = ? AND card_title."index" = cd.id
        LEFT JOIN support_card_data AS scd
            ON scd.id = ga.card_id AND ga.card_type = 2
        LEFT JOIN text_data AS supp_chara
            ON supp_chara.category = ? AND supp_chara."index" = scd.chara_id
        LEFT JOIN text_data AS supp_title
            ON supp_title.category = ? AND supp_title."index" = scd.id
        WHERE ga.is_pickup = 1
        ORDER BY ga.gacha_id, {order}, ga.card_type, ga.card_id
        """,
        (TEXT_CHARA_NAME, TEXT_CARD_TITLE, TEXT_CHARA_NAME, TEXT_SUPPORT_CARD_TITLE),
    ).fetchall()

    payload: list[tuple[Any, ...]] = []
    for row in rows:
        if row["card_type"] == 1:
            name, title = row["chara_name"], row["card_title"]
        elif row["card_type"] == 2:
            name, title = row["supp_name"], row["supp_title"]
        else:
            name, title = None, None
        payload.append(
            (
                row["gacha_id"],
                row["card_type"],
                row["card_id"],
                row["rarity"],
                name,
                title,
                row["recommend_order"],
            )
        )
    return payload
