from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.succession.importer import SuccessionSourceError, import_catalog
from modules.succession.service import SuccessionService
from modules.succession.settings import SuccessionSettings

# 关系组设计：101 只有 1001；202 有 1001+1002；3003 有 1001+1002+1003；
# 3101 有 1001+1002（7 点）；510 只有 1002；9999 只有 1003+1004；
# 7777 只有 1005+1006+1007——给祖父母位准备足够的互不相同的角色。
RELATIONS = [
    (101, 2),
    (202, 2),
    (3003, 7),
    (3101, 7),
    (510, 8),
    (9999, 1),
    (7777, 3),
]
MEMBERS = [
    (101, 1001),
    (202, 1001),
    (202, 1002),
    (3003, 1001),
    (3003, 1002),
    (3003, 1003),
    (3101, 1001),
    (3101, 1002),
    (510, 1002),
    (9999, 1003),
    (9999, 1004),
    (7777, 1005),
    (7777, 1006),
    (7777, 1007),
]
NAMES = {
    1001: "特别周",
    1002: "无声铃鹿",
    1003: "东海帝王",
    1004: "丸善斯基",
    1005: "黄金船",
    1006: "目白麦昆",
    1007: "鲁道夫象征",
}


def build_mdb(path: Path, *, drop_rank: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE text_data (
            id INTEGER NOT NULL,
            category INTEGER NOT NULL,
            "index" INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (category, "index")
        );
        CREATE TABLE succession_relation (
            relation_type INTEGER PRIMARY KEY,
            relation_point INTEGER NOT NULL
        );
        CREATE TABLE succession_relation_member (
            id INTEGER PRIMARY KEY,
            relation_type INTEGER NOT NULL,
            chara_id INTEGER NOT NULL
        );
        """
    )
    if not drop_rank:
        conn.execute(
            "CREATE TABLE succession_relation_rank ("
            "relation_rank INTEGER PRIMARY KEY, rank_value_min INTEGER NOT NULL, "
            "rank_value_max INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO succession_relation_rank VALUES (?, ?, ?)",
            [(1, 0, 50), (2, 51, 150), (3, 151, 9999)],
        )
    conn.executemany("INSERT INTO succession_relation VALUES (?, ?)", RELATIONS)
    conn.executemany(
        "INSERT INTO succession_relation_member (relation_type, chara_id) VALUES (?, ?)",
        MEMBERS,
    )
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        [(6, 6, chara_id, name) for chara_id, name in NAMES.items()],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def db(settings: Settings):
    database = Database(settings.db_path)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture()
def mdb(settings: Settings) -> Path:
    return build_mdb(settings.mdb_path)


@pytest.fixture()
def service(db: Database, settings: Settings) -> SuccessionService:
    return SuccessionService(db, settings, SuccessionSettings())


def test_import_counts(service: SuccessionService, mdb: Path) -> None:
    summary = import_catalog(service.db, mdb)

    assert summary.relations == 7
    assert summary.members == len(MEMBERS)
    assert summary.ranks == 3
    assert summary.charas == 7
    assert service.status()["counts"]["charas"] == 7


def test_pair_sums_shared_groups(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    data = service.pair(1001, 1002)

    # 1001 与 1002 同时出现在 202(2) / 3003(7) / 3101(7)
    assert data["total"] == 16
    assert data["group_count"] == 3
    assert [group["relation_type"] for group in data["groups"]] == [3003, 3101, 202]
    assert data["groups"][0]["size"] == 3
    assert data["a"]["name"] == "特别周"
    assert data["b"]["name"] == "无声铃鹿"
    # 16 分落在 △（0–50）
    assert data["rank"]["rank"] == 1
    assert data["rank"]["label"] == "△"
    # 口径声明必须随结果返回，避免被当成游戏内最终相性
    assert any("不含适性" in note for note in data["notes"])


def test_pair_without_shared_group_is_zero(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    data = service.pair(1001, 1004)

    assert data["total"] == 0
    assert data["group_count"] == 0
    assert data["rank"]["rank"] == 1


def test_pair_rejects_same_character(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    with pytest.raises(ValueError) as excinfo:
        service.pair(1001, 1001)

    assert "同一角色" in str(excinfo.value)


def test_pair_rejects_unknown_character(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    with pytest.raises(KeyError):
        service.pair(1001, 7777)


def test_rank_thresholds_come_from_table(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    ranks = service.status()["ranks"]

    assert [(row["rank"], row["label"]) for row in ranks] == [(1, "△"), (2, "〇"), (3, "⌾")]
    assert ranks[2]["range"] == "151 以上"


def test_layout_accumulates_six_slots(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    data = service.layout(
        {
            "target": 1001,
            "parents": [1002, 1003],
            "grandparents": [1004, 1005, 1006, 1007],
        }
    )

    by_slot = {slot["slot"]: slot for slot in data["slots"]}
    # 1 号：f(1001,1002) = 202 + 3003 + 3101 = 16
    assert by_slot["1"]["subtotal"] == 16
    # 2 号的两部分：f(1001,1003) = 3003 = 7；f(1002,1003) = 3003 = 7
    assert by_slot["2a"]["subtotal"] == 7
    assert by_slot["2b"]["subtotal"] == 7
    # 3/4 号：f(1001,1002,1004) 三人都有的组 = 0
    assert by_slot["3"]["subtotal"] == 0
    assert data["total"] == 30
    assert data["rank"]["rank"] == 1
    assert data["missing_grandparents"] == 0


def test_layout_zeroes_slot_when_grandparent_is_target(
    service: SuccessionService, mdb: Path
) -> None:
    import_catalog(service.db, mdb)

    data = service.layout({"target": 1001, "parents": [1002, 1003], "grandparents": [1001]})

    slot = next(item for item in data["slots"] if item["slot"] == "3")
    assert slot["zeroed"] is True
    assert slot["subtotal"] == 0
    assert data["missing_grandparents"] == 3


def test_layout_rejects_duplicate_parents(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    with pytest.raises(ValueError) as excinfo:
        service.layout({"target": 1001, "parents": [1002, 1002], "grandparents": []})

    assert "三个不同角色" in str(excinfo.value)


def test_layout_rejects_duplicate_grandparents(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    with pytest.raises(ValueError) as excinfo:
        service.layout({"target": 1001, "parents": [1002, 1003], "grandparents": [1004, 1004]})

    assert "祖父母不能重复" in str(excinfo.value)
    assert "丸善斯基" in str(excinfo.value)


def test_layout_rejects_grandparent_equal_to_parent(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    with pytest.raises(ValueError) as excinfo:
        service.layout({"target": 1001, "parents": [1002, 1003], "grandparents": [1002]})

    assert "祖父母不能与父母" in str(excinfo.value)
    assert "无声铃鹿" in str(excinfo.value)


def test_layout_allows_target_as_grandparent(service: SuccessionService, mdb: Path) -> None:
    """目标与祖父母同角色是合法布局，只是该位按 0 分计。"""
    import_catalog(service.db, mdb)

    data = service.layout(
        {"target": 1001, "parents": [1002, 1003], "grandparents": [1001, 1004, 1005, 1006]}
    )

    slots = {item["slot"]: item for item in data["slots"]}
    assert slots["3"]["zeroed"] is True
    assert slots["3"]["subtotal"] == 0
    assert slots["4"]["zeroed"] is False
    assert data["total"] == 30
    assert data["missing_grandparents"] == 0


def test_missing_source_table_raises(settings: Settings, tmp_path: Path) -> None:
    broken = tmp_path / "master.mdb"
    conn = sqlite3.connect(broken)
    conn.execute(
        'CREATE TABLE text_data (id INTEGER, category INTEGER, "index" INTEGER, text TEXT)'
    )
    conn.commit()
    conn.close()

    db = Database(settings.db_path)
    try:
        with pytest.raises(SuccessionSourceError) as excinfo:
            import_catalog(db, broken)
    finally:
        db.close()

    assert "succession_relation" in str(excinfo.value)


def test_missing_rank_table_is_reported(service: SuccessionService, settings: Settings) -> None:
    mdb = build_mdb(settings.mdb_path, drop_rank=True)

    summary = import_catalog(service.db, mdb)

    assert summary.ranks == 0
    assert summary.skipped == {"ranks": "缺少 succession_relation_rank"}
    # 没有等级表时仍能算分数，只是等级未知
    assert service.pair(1001, 1002)["rank"]["rank"] is None


def test_refresh_skips_when_not_stale(service: SuccessionService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    assert service.refresh()["imported"] is False
    assert service.status()["stale"] is False

    stamp = mdb.stat().st_mtime + 60
    os.utime(mdb, (stamp, stamp))
    assert service.status()["stale"] is True
    assert service.refresh()["counts"]["relations"] == 7


def test_api_serves_pair_and_layout(db: Database, settings: Settings, mdb: Path) -> None:
    import_catalog(db, mdb)

    with TestClient(create_app(settings)) as client:
        charas = client.get("/api/succession/charas").json()["items"]
        assert {row["name"] for row in charas} == set(NAMES.values())

        pair = client.get("/api/succession/pairs", params={"a": 1001, "b": 1002}).json()
        assert pair["total"] == 16
        assert pair["rank"]["label"] == "△"

        assert client.get("/api/succession/pairs", params={"a": 1001, "b": 1001}).status_code == 422
        assert client.get("/api/succession/pairs", params={"a": 1001, "b": 7777}).status_code == 404

        bad_layout = client.get(
            "/api/succession/layout",
            params={"target": 1001, "parent1": 1002, "parent2": 1003, "g1": 1004, "g2": 1004},
        )
        assert bad_layout.status_code == 422
        assert "祖父母不能重复" in bad_layout.json()["detail"]

        layout = client.get(
            "/api/succession/layout",
            params={"target": 1001, "parent1": 1002, "parent2": 1003, "g1": 1004},
        ).json()
        assert layout["total"] == 30
        assert layout["missing_grandparents"] == 3

        status = client.get("/api/succession/status").json()
        assert status["counts"]["relations"] == 7
        assert any("不含适性" in note for note in status["notes"])
