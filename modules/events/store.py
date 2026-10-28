"""``events_*`` 表结构与读写。

三类表分工：

- ``events_items``：当前状态（一次抓取的完整结果，软删除用 ``removed_at``）
- ``events_fetches``：每次抓取的审计行（成功/失败、条数、是否用了缓存）
- ``events_changes``：只记录**真正变化**的字段，用于回答「这版改了什么」
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.kernel.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS events_items (
    uid           TEXT PRIMARY KEY,
    source_key    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    server        TEXT NOT NULL,
    title         TEXT NOT NULL,
    starts_at     TEXT,
    ends_at       TEXT,
    category      TEXT,
    payload       TEXT NOT NULL DEFAULT '{}',
    source_level  TEXT NOT NULL DEFAULT 'B',
    source_url    TEXT NOT NULL DEFAULT '',
    permanent     INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    removed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_items_server
    ON events_items(server, kind, starts_at);
CREATE INDEX IF NOT EXISTS idx_events_items_source
    ON events_items(source_key);

CREATE TABLE IF NOT EXISTS events_fetches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key  TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER NOT NULL DEFAULT 0,
    items       INTEGER NOT NULL DEFAULT 0,
    added       INTEGER NOT NULL DEFAULT 0,
    changed     INTEGER NOT NULL DEFAULT 0,
    removed     INTEGER NOT NULL DEFAULT 0,
    cached      INTEGER NOT NULL DEFAULT 0,
    stale       INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_fetches_source
    ON events_fetches(source_key, id DESC);

CREATE TABLE IF NOT EXISTS events_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_id     INTEGER NOT NULL,
    source_key   TEXT NOT NULL,
    uid          TEXT NOT NULL,
    server       TEXT NOT NULL,
    title        TEXT NOT NULL,
    action       TEXT NOT NULL,
    field        TEXT,
    before_value TEXT,
    after_value  TEXT,
    changed_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_changes_time
    ON events_changes(changed_at DESC);
"""

# 参与变化判定的字段；uid / source_key 是主键，不参与比对
COMPARED_FIELDS = (
    "title",
    "starts_at",
    "ends_at",
    "category",
    "payload",
    "source_level",
    "permanent",
)

META_LAST_REFRESH = "events.last_refresh"
META_LEVEL_NOTE = "events.source_note"


def ensure_schema(db: Database) -> None:
    with db.transaction() as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(events_items)")}
        if "permanent" not in columns:
            conn.execute("ALTER TABLE events_items ADD COLUMN permanent INTEGER NOT NULL DEFAULT 0")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["ids"] = json.loads(data.get("payload") or "{}")
    except json.JSONDecodeError:
        data["ids"] = {}
    return data


def load_items(db: Database, source_key: str) -> dict[str, dict[str, Any]]:
    rows = db.query("SELECT * FROM events_items WHERE source_key = ?", (source_key,))
    return {row["uid"]: _row_to_dict(row) for row in rows}


def purge_sources(db: Database, keep_keys: set[str]) -> dict[str, int]:
    """删掉已不再配置的来源（繁中服 / 日服）留下的行。

    这些来源的抓取代码已经移除，留着只会让面板显示别的服的日程；与「来源消失只软删」
    不同，这里是配置层面的下线，所以连审计行一起删，避免历史列表里全是已下线的来源。
    """
    if not keep_keys:
        return {"items": 0, "changes": 0, "fetches": 0}
    placeholders = ", ".join("?" * len(keep_keys))
    params = tuple(sorted(keep_keys))
    items = db.execute(
        f"DELETE FROM events_items WHERE source_key NOT IN ({placeholders})", params
    ).rowcount
    changes = db.execute(
        f"DELETE FROM events_changes WHERE source_key NOT IN ({placeholders})", params
    ).rowcount
    fetches = db.execute(
        f"DELETE FROM events_fetches WHERE source_key NOT IN ({placeholders})", params
    ).rowcount
    return {"items": items, "changes": changes, "fetches": fetches}


def begin_fetch(db: Database, source_key: str, started_at: str) -> int:
    cursor = db.execute(
        "INSERT INTO events_fetches (source_key, started_at) VALUES (?, ?)",
        (source_key, started_at),
    )
    return int(cursor.lastrowid or 0)


def finish_fetch(
    db: Database,
    fetch_id: int,
    *,
    ok: bool,
    items: int = 0,
    added: int = 0,
    changed: int = 0,
    removed: int = 0,
    cached: bool = False,
    stale: bool = False,
    note: str | None = None,
    error: str | None = None,
) -> None:
    db.execute(
        "UPDATE events_fetches SET finished_at = ?, ok = ?, items = ?, added = ?, "
        "changed = ?, removed = ?, cached = ?, stale = ?, note = ?, error = ? WHERE id = ?",
        (
            now_iso(),
            int(ok),
            items,
            added,
            changed,
            removed,
            int(cached),
            int(stale),
            note,
            error,
            fetch_id,
        ),
    )


def upsert_items(db: Database, rows: list[dict[str, Any]], fetched_at: str) -> None:
    if not rows:
        return
    stamp = now_iso()
    payload = [
        (
            row["uid"],
            row["source_key"],
            row["kind"],
            row["server"],
            row["title"],
            row["starts_at"],
            row["ends_at"],
            row["category"],
            row["payload"],
            row["source_level"],
            row["source_url"],
            int(row.get("permanent") or 0),
            fetched_at,
            stamp,
            stamp,
        )
        for row in rows
    ]
    db.executemany(
        """
        INSERT INTO events_items (
            uid, source_key, kind, server, title, starts_at, ends_at, category,
            payload, source_level, source_url, permanent, fetched_at, first_seen_at,
            updated_at, removed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(uid) DO UPDATE SET
            kind         = excluded.kind,
            server       = excluded.server,
            title        = excluded.title,
            starts_at    = excluded.starts_at,
            ends_at      = excluded.ends_at,
            category     = excluded.category,
            payload      = excluded.payload,
            source_level = excluded.source_level,
            source_url   = excluded.source_url,
            permanent    = excluded.permanent,
            fetched_at   = excluded.fetched_at,
            updated_at   = excluded.updated_at,
            removed_at   = NULL
        """,
        payload,
    )


def mark_removed(db: Database, uids: list[str], removed_at: str) -> None:
    if not uids:
        return
    db.executemany(
        "UPDATE events_items SET removed_at = ?, updated_at = ? WHERE uid = ?",
        [(removed_at, removed_at, uid) for uid in uids],
    )


def record_changes(db: Database, fetch_id: int, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    db.executemany(
        """
        INSERT INTO events_changes (
            fetch_id, source_key, uid, server, title, action,
            field, before_value, after_value, changed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                fetch_id,
                row["source_key"],
                row["uid"],
                row["server"],
                row["title"],
                row["action"],
                row.get("field"),
                row.get("before_value"),
                row.get("after_value"),
                row["changed_at"],
            )
            for row in rows
        ],
    )


def list_items(
    db: Database,
    *,
    servers: tuple[str, ...] | None = None,
    kinds: tuple[str, ...] | None = None,
    include_removed: bool = False,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_removed:
        clauses.append("removed_at IS NULL")
    if servers:
        clauses.append(f"server IN ({','.join('?' * len(servers))})")
        params.extend(servers)
    if kinds:
        clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = db.query(
        f"SELECT * FROM events_items {where} ORDER BY starts_at IS NULL, starts_at DESC, title",
        tuple(params),
    )
    return [_row_to_dict(row) for row in rows]


def recent_changes(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM events_changes ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(row) for row in rows]


def last_fetches(db: Database) -> list[dict[str, Any]]:
    """每个来源最近一次抓取，供状态卡展示。"""
    rows = db.query(
        """
        SELECT f.* FROM events_fetches f
        JOIN (SELECT source_key, MAX(id) AS max_id FROM events_fetches GROUP BY source_key) m
          ON f.id = m.max_id
        ORDER BY f.source_key
        """
    )
    return [dict(row) for row in rows]


def source_counters(db: Database) -> list[dict[str, Any]]:
    rows = db.query(
        """
        SELECT source_key, server, kind, COUNT(*) AS total,
               SUM(CASE WHEN removed_at IS NULL THEN 1 ELSE 0 END) AS active
        FROM events_items GROUP BY source_key, server, kind ORDER BY source_key
        """
    )
    return [dict(row) for row in rows]
