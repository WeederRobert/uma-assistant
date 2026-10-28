from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.gacha.importer import GachaSourceError, import_catalog
from modules.gacha.service import GachaService
from modules.gacha.settings import GachaSettings


def _stamp(days: float) -> int:
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


def build_mdb(path: Path, *, drop_available: bool = False) -> Path:
    """最小 master.mdb：进行中 / 未来 / 已结束 / 常驻，外加一个早已关池的长期池。"""
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
        CREATE TABLE gacha_data (
            id INTEGER PRIMARY KEY,
            type INTEGER NOT NULL,
            card_type INTEGER NOT NULL,
            start_date INTEGER NOT NULL,
            end_date INTEGER NOT NULL
        );
        CREATE TABLE gacha_available (
            gacha_id INTEGER NOT NULL,
            card_id INTEGER NOT NULL,
            card_type INTEGER NOT NULL,
            rarity INTEGER,
            odds INTEGER,
            is_pickup INTEGER NOT NULL DEFAULT 0,
            recommend_order INTEGER,
            is_prize_selectable INTEGER
        );
        CREATE TABLE card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL
        );
        CREATE TABLE support_card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL
        );
        """
    )
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        [
            (26, 26, 9001, "精选 优俊少女招募"),
            (26, 26, 9002, "精选 协助卡招募"),
            (26, 26, 9003, "精选 优俊少女招募"),
            (26, 26, 9004, "3★必得新人出道招募"),
            (26, 26, 9005, "优俊少女招募"),
            (26, 26, 9006, "精选 优俊少女招募"),
            (13, 13, 9001, "期间精选优俊少女的出现率提升！"),
            (6, 6, 1001, "跳舞城"),
            (5, 5, 2001, "[GLITTER！]"),
            (6, 6, 1002, "目白光明"),
            (5, 5, 2002, "[澄澈星空下的白雪洛丽塔]"),
            (6, 6, 1003, "万籁争鸣"),
            (76, 76, 3001, "[特雷森学园]"),
        ],
    )
    conn.executemany(
        "INSERT INTO gacha_data (id, type, card_type, start_date, end_date) VALUES (?, ?, ?, ?, ?)",
        [
            (9001, 3, 1, _stamp(-3), _stamp(7)),
            (9002, 3, 2, _stamp(-3), _stamp(7)),
            (9003, 3, 1, _stamp(5), _stamp(15)),
            (9004, 2, 1, _stamp(-1500), 2_520_000_000),
            # 跨度很大但早已关池：不能因为跨度大就标常驻
            (9005, 3, 1, _stamp(-2400), _stamp(-1200)),
            # 开始时间落在 2049/2050 哨兵年份：客户端占位，未定档
            (9006, 3, 1, 2_530_000_000, 2_530_600_000),
        ],
    )
    conn.executemany(
        "INSERT INTO card_data (id, chara_id) VALUES (?, ?)",
        [(2001, 1001), (2002, 1002)],
    )
    conn.execute("INSERT INTO support_card_data (id, chara_id) VALUES (3001, 1003)")
    conn.executemany(
        "INSERT INTO gacha_available (gacha_id, card_id, card_type, rarity, is_pickup, "
        "recommend_order) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (9001, 2001, 1, 3, 1, 1),
            (9001, 2002, 1, 3, 0, 2),
            (9002, 3001, 2, 3, 1, 1),
            (9003, 2002, 1, 3, 1, 1),
        ],
    )
    if drop_available:
        conn.execute("DROP TABLE gacha_available")
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
def service(db: Database, settings: Settings) -> GachaService:
    return GachaService(db, settings, GachaSettings())


def test_import_counts(service: GachaService, mdb: Path) -> None:
    summary = import_catalog(service.db, mdb)

    assert summary.banners == 6
    assert summary.upcoming == 1, "只有 9003 在未来；9006 是未定档占位，不算未来"
    assert summary.pickups == 3, "只有 is_pickup = 1 的才是精选"
    assert summary.with_pickups == 3
    assert service.status()["counts"] == {"banners": 6, "pickups": 3}


def test_banners_group_by_state(service: GachaService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    result = service.banners()

    assert result["counts"]["total"] == 6
    assert result["counts"]["upcoming"] == 1
    assert [item["gacha_id"] for item in result["upcoming"]] == [9003]
    assert result["upcoming"][0]["days_to_start"] in (4, 5)
    # 常驻池（结束时间写在 2050 哨兵）标常驻，不显示「还剩几千天」
    permanent = next(item for item in result["active"] if item["gacha_id"] == 9004)
    assert permanent["permanent"] is True
    assert permanent["state"] == "active"
    # 跨度大但早已关池的旧池仍算已结束，别被「跨度 > 5 年」误判
    assert 9005 in {item["gacha_id"] for item in result["ended"]}
    assert all(item["permanent"] is False for item in result["ended"])


def test_placeholder_dates_go_to_unscheduled_not_upcoming(service: GachaService, mdb: Path) -> None:
    """开始时间落在 2049/2050 的记录是占位，不能进「即将开始」变成「还有 8000 天」。"""
    import_catalog(service.db, mdb)

    result = service.banners()

    assert [item["gacha_id"] for item in result["unscheduled"]] == [9006]
    assert result["counts"]["unscheduled"] == 1
    assert all(item["gacha_id"] != 9006 for item in result["upcoming"])
    assert all(item["gacha_id"] != 9006 for item in result["active"])
    assert 9006 not in {item["gacha_id"] for item in result["ended"]}
    assert result["unscheduled"][0]["state"] == "unscheduled"


def test_pickups_resolve_character_and_support_card_names(service: GachaService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    by_id = {item["gacha_id"]: item for item in service.banners()["active"]}
    character = by_id[9001]
    assert character["title"] == "精选 优俊少女招募"
    assert character["description"] == "期间精选优俊少女的出现率提升！"
    assert character["card_type_label"] == "马娘"
    # type 没有字典表，只回显编号
    assert character["type_label"] == "类型 3"
    assert character["pickups"] == [
        {
            "card_type": 1,
            "card_type_label": "马娘",
            "card_id": 2001,
            "rarity": 3,
            "name": "跳舞城",
            "title": "[GLITTER！]",
        }
    ]

    support = by_id[9002]
    assert support["card_type_label"] == "协助卡"
    assert support["pickups"][0]["name"] == "万籁争鸣"
    assert support["pickups"][0]["title"] == "[特雷森学园]"


def test_banner_detail_and_missing(service: GachaService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    detail = service.banner(9003)
    assert detail is not None
    assert detail["state"] == "upcoming"
    assert [row["name"] for row in detail["pickups"]] == ["目白光明"]
    assert detail["starts_local"] is not None
    assert service.banner(123456) is None


def test_times_use_system_zone(service: GachaService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    result = service.banners()

    assert result["zone"].startswith("UTC")
    # local_iso 产出带偏移量的 ISO 文本，前端才能用 new Date() 解析
    assert "T" in result["upcoming"][0]["starts_local"]


def test_missing_available_table_is_reported(service: GachaService, settings: Settings) -> None:
    mdb = build_mdb(settings.mdb_path, drop_available=True)

    summary = import_catalog(service.db, mdb)

    assert summary.banners == 6
    assert summary.pickups == 0
    assert summary.skipped == {"pickups": "缺少 gacha_available"}


def test_missing_required_table_raises(settings: Settings, tmp_path: Path) -> None:
    broken = tmp_path / "master.mdb"
    conn = sqlite3.connect(broken)
    conn.execute(
        'CREATE TABLE text_data (id INTEGER, category INTEGER, "index" INTEGER, text TEXT)'
    )
    conn.commit()
    conn.close()

    db = Database(settings.db_path)
    try:
        with pytest.raises(GachaSourceError) as excinfo:
            import_catalog(db, broken)
    finally:
        db.close()

    assert "gacha_data" in str(excinfo.value)


def test_refresh_skips_when_not_stale(service: GachaService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    assert service.refresh()["imported"] is False
    assert service.status()["stale"] is False

    stamp = mdb.stat().st_mtime + 60
    os.utime(mdb, (stamp, stamp))
    assert service.status()["stale"] is True
    assert service.refresh()["counts"]["banners"] == 6


def test_api_serves_banners_and_detail(db: Database, settings: Settings, mdb: Path) -> None:
    import_catalog(db, mdb)

    with TestClient(create_app(settings)) as client:
        listing = client.get("/api/gacha/banners").json()
        assert listing["counts"]["upcoming"] == 1
        assert listing["upcoming"][0]["gacha_id"] == 9003

        detail = client.get("/api/gacha/banners/9001").json()
        assert detail["title"] == "精选 优俊少女招募"
        assert detail["pickups"][0]["name"] == "跳舞城"

        assert client.get("/api/gacha/banners/404404").status_code == 404

        status = client.get("/api/gacha/status").json()
        assert status["counts"]["banners"] == 6
        assert any("没有字典表" in note for note in status["notes"])
