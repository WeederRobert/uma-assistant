from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Database:
    """轻量的 SQLite 封装。

    内核与各模块共享同一个连接（同一进程内串行访问），表名前缀由模块自行约定。
    需要更强隔离的模块可以自行在 ``ctx.data_dir`` 下建独立库。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def transaction(self):
        """返回一个上下文管理器，用于批量写入。"""

        class _Tx:
            def __init__(self, db: Database) -> None:
                self.db = db

            def __enter__(self) -> sqlite3.Connection:
                self.db._lock.acquire()
                return self.db._conn

            def __exit__(self, exc_type, exc, tb) -> None:
                try:
                    if exc_type is None:
                        self.db._conn.commit()
                    else:
                        self.db._conn.rollback()
                finally:
                    self.db._lock.release()

        return _Tx(self)

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row is not None else default

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - 关闭失败不影响退出
                logger.debug("关闭数据库失败", exc_info=True)
