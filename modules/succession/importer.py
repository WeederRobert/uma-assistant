"""从 ``master.mdb`` 导入相性关系数据。

四张来源表（简中服 2.0.2 快照实测行数）：

================================  ====  =========================================
表                                  行数   含义
================================  ====  =========================================
``succession_relation``           2133  关系组编号 → 点数（取值只有 1 / 2 / 7 / 8）
``succession_relation_member``    4527  关系组 ↔ 角色（``chara_id``）
``succession_relation_rank``         3  分数区间 → 相性等级（0–50 / 51–150 / 151+）
``chara_data`` + ``text_data`` 6   95  参与关系组的角色名（另 31 名不在任何组内）
================================  ====  =========================================

口径提醒：

- **关系组没有名称字典**：``relation_type`` 只是编号（101…9731），主数据里查不到
  「室友 / 友情 / 同冠名」这类名字，所以只回显编号与「这个组共几人」。
- 关系组里既有 1 人组（612 个，两人之间永远不共享）、2 人组（1125 个，多半是室友 / 特定关系），
  也有 3–87 人的大组（同冠名 / 同队），所以两人共享的组数通常在 0–12 之间。
- 分数区间与原作一致：0–50 / 51–150 / 151+，与知识库 06 记录的 △ / 〇 / ⌾ 三档吻合。
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.kernel.database import Database

from . import store
from .schema import ensure_schema

logger = logging.getLogger(__name__)

TEXT_CHARA_NAME = 6

REQUIRED_TABLES = ("succession_relation", "succession_relation_member")


class SuccessionSourceError(RuntimeError):
    """master.mdb 不可用：路径不对、不是 SQLite、或缺相性关系表。"""


@dataclass(slots=True)
class CatalogSummary:
    relations: int = 0
    members: int = 0
    ranks: int = 0
    charas: int = 0
    data_version: str = ""
    source: str = ""
    skipped: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "relations": self.relations,
            "members": self.members,
            "ranks": self.ranks,
            "charas": self.charas,
            "data_version": self.data_version,
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
        raise SuccessionSourceError(f"这是一个目录，不是 master.mdb 文件：{path}")
    if not path.exists():
        raise SuccessionSourceError(
            f"找不到 master.mdb：{path}。先在「数据同步」里导入一次客户端数据。"
        )
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        raise SuccessionSourceError(f"不是有效的 SQLite 数据库：{path.name}（{exc}）") from exc
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def import_catalog(
    db: Database,
    mdb_path: Path,
    *,
    data_version: str | None = None,
) -> CatalogSummary:
    summary = CatalogSummary()
    conn = _open_readonly(mdb_path)
    ensure_schema(db)
    try:
        tables = _tables(conn)
        absent = [name for name in REQUIRED_TABLES if name not in tables]
        if absent:
            raise SuccessionSourceError(
                f"{mdb_path.name} 缺少 {('、'.join(absent))}，无法导入相性关系。"
                "请确认选的是游戏目录 master 子目录下的 master.mdb。"
            )

        relations = [
            (int(row["relation_type"]), int(row["relation_point"]))
            for row in conn.execute("SELECT relation_type, relation_point FROM succession_relation")
            if row["relation_type"] is not None and row["relation_point"] is not None
        ]
        members = [
            (int(row["relation_type"]), int(row["chara_id"]))
            for row in conn.execute(
                "SELECT relation_type, chara_id FROM succession_relation_member"
            )
            if row["relation_type"] is not None and row["chara_id"] is not None
        ]
        ranks: list[tuple[int, int, int]] = []
        if "succession_relation_rank" in tables:
            ranks = [
                (
                    int(row["relation_rank"]),
                    int(row["rank_value_min"]),
                    int(row["rank_value_max"]),
                )
                for row in conn.execute(
                    "SELECT relation_rank, rank_value_min, rank_value_max "
                    "FROM succession_relation_rank"
                )
            ]
        else:
            summary.skipped["ranks"] = "缺少 succession_relation_rank"

        charas: list[tuple[int, str]] = []
        if "text_data" in tables:
            for row in conn.execute(
                """
                SELECT DISTINCT m.chara_id AS chara_id, t.text AS name
                FROM succession_relation_member AS m
                LEFT JOIN text_data AS t
                    ON t.category = ? AND t."index" = m.chara_id
                WHERE m.chara_id IS NOT NULL
                """,
                (TEXT_CHARA_NAME,),
            ):
                if row["name"]:
                    charas.append((int(row["chara_id"]), row["name"].strip()))
        else:
            summary.skipped["charas"] = "缺少 text_data"

        store.replace_catalog(db, relations, members, ranks, charas)

        summary.relations = len(relations)
        summary.members = len(members)
        summary.ranks = len(ranks)
        summary.charas = len(charas)
    except sqlite3.DatabaseError as exc:
        raise SuccessionSourceError(f"读取 {mdb_path.name} 失败（{exc}）") from exc
    finally:
        conn.close()

    summary.data_version = data_version or f"mdb-{store.now_iso()[:10].replace('-', '')}"
    summary.source = f"{mdb_path.name} (sha256:{_sha256(mdb_path)[:12]})"
    store.set_catalog_source(
        db,
        {
            **summary.as_dict(),
            "mdb_path": str(mdb_path.resolve()),
            "mdb_mtime": mdb_path.stat().st_mtime,
            "imported_at": store.now_iso(),
        },
    )
    logger.info(
        "相性关系导入完成 | 关系组=%d 成员=%d 等级=%d 角色=%d | %s",
        summary.relations,
        summary.members,
        summary.ranks,
        summary.charas,
        summary.data_version,
    )
    return summary
