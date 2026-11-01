"""从 ``master.mdb`` 读**已确认**的卡池排期、剧情活动排期、单抽成本与天井。

``gacha`` 模块读的是同一批表，但模块之间既不互相 import 也不读对方的表，
所以这里只挑规划需要的东西自己读一遍：

====================  =============================================
表                     用途
====================  =============================================
``gacha_data``         卡池起止时间、单抽成本（``cost_single``）
``text_data`` 26       卡池名称
``gacha_available``    精选（``is_pickup = 1``），取卡 id 用于与 wiki 预测对齐
``gacha_exchange``     兑换 Up 卡需要的点数（``pay_item_num``，即天井抽数）
``story_event_data``   剧情活动起止时间（简中服自己的节奏，A 级）
``text_data`` 214      剧情活动短名
====================  =============================================

卡池只收「还在进行或未来」的普通池：常驻池（``2050-01-01`` 哨兵）与未定档占位（开始时间落在
2049/2050）都不是规划对象，直接排除，排除数量记在 ``skipped`` 里。
"""

from __future__ import annotations

import hashlib
import json
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
TEXT_CHARA_NAME = 6
TEXT_STORY_EVENT_NAME = 214
TEXT_STORY_EVENT_NAME_LONG = 189

REQUIRED_TABLES = ("gacha_data", "text_data")

# 客户端用 2500000000 之后的日期表示「不结束」或「未定档」；跨度超过 5 年的池子按常驻处理
SENTINEL_TS = 2_500_000_000
PERMANENT_SECONDS = 5 * 365 * 86400


class MdbSourceError(RuntimeError):
    """master.mdb 不可用：路径不对、不是 SQLite、或缺卡池主表。"""


@dataclass(slots=True)
class ConfirmedSummary:
    pools: int = 0
    future: int = 0
    events: int = 0
    skipped: dict[str, str] = field(default_factory=dict)
    mdb_path: str = ""
    mdb_mtime: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pools": self.pools,
            "future": self.future,
            "events": self.events,
            "skipped": self.skipped,
            "mdb_path": self.mdb_path,
            "mdb_mtime": self.mdb_mtime,
        }


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise MdbSourceError(f"找不到 master.mdb：{path}。先在「数据同步」里导入一次客户端数据。")
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        raise MdbSourceError(f"不是有效的 SQLite 数据库：{path.name}（{exc}）") from exc
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _pickups(
    conn: sqlite3.Connection, tables: set[str]
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    """精选卡：返回（卡 id 列表, 名称列表），id 用于与 wiki 配对，名称用于展示。"""
    if "gacha_available" not in tables:
        return {}, {}
    available = _columns(conn, "gacha_available")
    if "is_pickup" not in available:
        return {}, {}
    joins = ""
    params: list[Any] = []
    if "card_data" in tables:
        joins += (
            " LEFT JOIN card_data AS cd ON cd.id = ga.card_id AND ga.card_type = 1"
            ' LEFT JOIN text_data AS chara ON chara.category = ? AND chara."index" = cd.chara_id'
        )
        params.append(TEXT_CHARA_NAME)
    else:
        joins += " LEFT JOIN (SELECT NULL AS text) AS chara ON 0"
    if "support_card_data" in tables:
        joins += (
            " LEFT JOIN support_card_data AS scd ON scd.id = ga.card_id AND ga.card_type = 2"
            ' LEFT JOIN text_data AS supp ON supp.category = ? AND supp."index" = scd.chara_id'
        )
        params.append(TEXT_CHARA_NAME)
    else:
        joins += " LEFT JOIN (SELECT NULL AS text) AS supp ON 0"
    rows = conn.execute(
        f"""
        SELECT ga.gacha_id, ga.card_id, ga.card_type, chara.text AS chara_name,
               supp.text AS supp_name
        FROM gacha_available AS ga
        {joins}
        WHERE ga.is_pickup = 1
        ORDER BY ga.gacha_id, ga.card_type, ga.card_id
        """,
        params,
    ).fetchall()
    ids: dict[int, list[str]] = {}
    names: dict[int, list[str]] = {}
    for row in rows:
        gacha_id = int(row["gacha_id"])
        card = str(row["card_id"])
        name = (row["chara_name"] if row["card_type"] == 1 else row["supp_name"]) or ""
        name = name.strip()
        if card not in ids.setdefault(gacha_id, []):
            ids[gacha_id].append(card)
        if name and name not in names.setdefault(gacha_id, []):
            names[gacha_id].append(name)
    return ids, names


def _exchange(conn: sqlite3.Connection, tables: set[str]) -> dict[int, int]:
    """``gacha_exchange.pay_item_num``：兑换本期 UP 卡需要的点数，实测 200，即天井抽数。"""
    if "gacha_exchange" not in tables:
        return {}
    if not {"gacha_id", "pay_item_num"} <= _columns(conn, "gacha_exchange"):
        return {}
    rows = conn.execute(
        "SELECT gacha_id, MIN(pay_item_num) AS pulls FROM gacha_exchange "
        "WHERE pay_item_num > 0 GROUP BY gacha_id"
    ).fetchall()
    return {int(row["gacha_id"]): int(row["pulls"]) for row in rows}


def _story_events(conn: sqlite3.Connection, tables: set[str]) -> list[tuple[Any, ...]]:
    """剧情活动排期；这是简中服客户端内置的时间，不需要跨服换算。"""
    if "story_event_data" not in tables:
        return []
    columns = _columns(conn, "story_event_data")
    if not {"story_event_id", "start_date", "end_date"} <= columns:
        return []
    names = {
        int(row["index"]): row["text"]
        for row in conn.execute(
            'SELECT "index", text FROM text_data WHERE category IN (?, ?)',
            (TEXT_STORY_EVENT_NAME, TEXT_STORY_EVENT_NAME_LONG),
        )
    }
    fetched_at = store.now_iso()
    payload: list[tuple[Any, ...]] = []
    for row in conn.execute(
        "SELECT story_event_id, start_date, end_date FROM story_event_data ORDER BY start_date"
    ):
        start_ts, end_ts = row["start_date"], row["end_date"]
        if not start_ts or not end_ts:
            continue
        event_id = int(row["story_event_id"])
        payload.append(
            (
                f"mdb-event:{event_id}",
                (names.get(event_id) or f"剧情活动 #{event_id}").strip(),
                "剧情活动",
                None,
                None,
                int(start_ts),
                int(end_ts),
                "master.mdb",
                None,
                None,
                "A",
                "",
                fetched_at,
            )
        )
    return payload


def read_confirmed(
    db: Database,
    mdb_path: Path,
    *,
    default_cost_single: int,
    default_ceiling_pulls: int,
    now: datetime | None = None,
) -> ConfirmedSummary:
    """导入已确认段（卡池 + 剧情活动）。卡池表缺一半也没关系，剧情活动能读多少读多少。"""
    summary = ConfirmedSummary()
    ensure_schema(db)
    conn = _open_readonly(mdb_path)
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        absent = [name for name in REQUIRED_TABLES if name not in tables]
        if absent:
            raise MdbSourceError(
                f"{mdb_path.name} 缺少 {'、'.join(absent)}，无法读卡池排期。"
                "请确认选的是游戏目录 master 子目录下的 master.mdb。"
            )

        columns = _columns(conn, "gacha_data")
        cost_column = '"cost_single"' if "cost_single" in columns else "NULL"
        type_column = '"type"' if "type" in columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT g.id AS gacha_id, g.card_type AS card_type, {type_column} AS gacha_type,
                   {cost_column} AS cost_single, g.start_date AS start_ts, g.end_date AS end_ts,
                   title.text AS title
            FROM gacha_data AS g
            LEFT JOIN text_data AS title
                ON title.category = ? AND title."index" = g.id
            """,
            (TEXT_GACHA_TITLE,),
        ).fetchall()

        pickups, pickup_names = _pickups(conn, tables)
        exchange = _exchange(conn, tables)
        events = _story_events(conn, tables)
    except sqlite3.DatabaseError as exc:
        raise MdbSourceError(f"读取 {mdb_path.name} 失败（{exc}）") from exc
    finally:
        conn.close()

    stamp = int((now or datetime.now(UTC)).timestamp())
    fetched_at = store.now_iso()
    payload: list[tuple[Any, ...]] = []
    permanent = 0
    ended = 0
    unscheduled = 0
    for row in rows:
        start_ts = row["start_ts"]
        end_ts = row["end_ts"]
        if not start_ts or not end_ts:
            continue
        if start_ts >= SENTINEL_TS:
            unscheduled += 1
            continue
        if end_ts - start_ts >= PERMANENT_SECONDS:
            permanent += 1
            continue
        if end_ts < stamp:
            ended += 1
            continue
        cost_single = int(row["cost_single"] or 0) or default_cost_single
        card_ids = pickups.get(int(row["gacha_id"]), [])
        payload.append(
            (
                f"mdb:{row['gacha_id']}",
                "mdb",
                "confirmed",
                (row["title"] or f"卡池 #{row['gacha_id']}").strip(),
                row["card_type"],
                row["gacha_type"],
                int(start_ts),
                int(end_ts),
                None,
                cost_single,
                exchange.get(int(row["gacha_id"]), default_ceiling_pulls),
                "、".join(pickup_names.get(int(row["gacha_id"]), []))[:200],
                json.dumps({"card_ids": card_ids}, ensure_ascii=False),
                "A",
                "",
                fetched_at,
            )
        )

    store.replace_mdb_pools(db, payload)
    store.replace_mdb_events(db, events)
    summary.pools = len(payload)
    summary.future = sum(1 for row in payload if row[6] > stamp)
    summary.events = len(events)
    summary.skipped = {
        "permanent": f"常驻池 {permanent} 个",
        "ended": f"已结束 {ended} 个",
        "unscheduled": f"未定档占位 {unscheduled} 个",
    }
    summary.mdb_path = str(mdb_path.resolve())
    summary.mdb_mtime = mdb_path.stat().st_mtime
    logger.info(
        "已确认段导入完成 | 卡池=%d（未来 %d）剧情活动=%d | 跳过常驻 %d / 已结束 %d / 未定档 %d",
        summary.pools,
        summary.future,
        summary.events,
        permanent,
        ended,
        unscheduled,
    )
    return summary


def source_signature(mdb_path: Path) -> dict[str, Any]:
    """mdb 的身份信息，用来判断要不要重新导入。"""
    stat = mdb_path.stat()
    digest = hashlib.sha256()
    with mdb_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "path": str(mdb_path.resolve()),
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "sha256": digest.hexdigest()[:12],
    }
