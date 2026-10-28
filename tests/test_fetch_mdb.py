from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from modules.sync.android import (
    DEFAULT_PACKAGE,
    FetchError,
    probe,
    sha256_file,
)
from modules.sync.service import installed_sha


def _make_mdb(path: Path, *, skip: tuple[str, ...] = ()) -> None:
    conn = sqlite3.connect(path)
    for table in ("text_data", "card_data", "support_card_data", "skill_data"):
        if table in skip:
            continue
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.execute(f"INSERT INTO {table} (id) VALUES (1)")
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()


def test_probe_counts_rows(tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _make_mdb(mdb)

    result = probe(mdb)

    assert result.characters == 1
    assert result.support_cards == 1
    assert result.skills == 1
    assert result.text_entries == 1
    assert result.tables == 5


def test_probe_rejects_non_sqlite(tmp_path: Path) -> None:
    fake = tmp_path / "master.mdb"
    fake.write_bytes(b"PK\x03\x04zip content")

    with pytest.raises(FetchError, match="不是 SQLite"):
        probe(fake)


def test_probe_rejects_missing_tables(tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _make_mdb(mdb, skip=("skill_data",))

    with pytest.raises(FetchError, match="skill_data"):
        probe(mdb)


def test_probe_reads_header_without_loading_whole_file(tmp_path: Path) -> None:
    # 只读前 16 字节即可判型：22 MB 的真实 mdb 不应被整体读进内存
    mdb = tmp_path / "master.mdb"
    mdb.write_bytes(b"not a sqlite file" * 4)

    with pytest.raises(FetchError, match="不是 SQLite"):
        probe(mdb)


def test_sha256_matches_hashlib(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"x" * 5000)

    assert sha256_file(target) == hashlib.sha256(b"x" * 5000).hexdigest()


def test_installed_sha_reads_meta(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO meta VALUES (?, ?)",
        ("data.source", "master.mdb (sha256:e0da18e6b9d1)"),
    )
    conn.commit()
    conn.close()

    assert installed_sha(db) == "e0da18e6b9d1"


def test_installed_sha_handles_missing_or_broken_db(tmp_path: Path) -> None:
    assert installed_sha(tmp_path / "nope.db") is None

    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database")
    assert installed_sha(broken) is None


def test_default_package_is_cn_uma() -> None:
    assert DEFAULT_PACKAGE == "com.bilibili.umamusu"
