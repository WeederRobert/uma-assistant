from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.catalog.importer import MasterMdbError, import_master_mdb
from modules.sync.service import SyncService


def _build_mdb(path: Path, *, version_text: str = "特别周") -> None:
    """结构与真实 master.mdb 一致的最小库：text_data.id 与 category 恒等。"""
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
        CREATE TABLE card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL,
            default_rarity INTEGER NOT NULL,
            limited_chara INTEGER NOT NULL,
            talent_speed INTEGER,
            talent_stamina INTEGER,
            talent_pow INTEGER,
            talent_guts INTEGER,
            talent_wiz INTEGER
        );
        CREATE TABLE support_card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL,
            rarity INTEGER NOT NULL,
            support_card_type INTEGER NOT NULL,
            skill_set_id INTEGER NOT NULL
        );
        CREATE TABLE skill_data (
            id INTEGER PRIMARY KEY,
            rarity INTEGER NOT NULL,
            skill_category INTEGER NOT NULL,
            tag_id TEXT
        );
        """
    )
    texts = [
        (6, 1001, version_text),
        (47, 200001, "末脚"),
        (48, 200001, "在最终直线提升速度"),
    ]
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        [(category, category, index, text) for category, index, text in texts],
    )
    conn.execute(
        "INSERT INTO card_data (id, chara_id, default_rarity, limited_chara) "
        "VALUES (2001, 1001, 3, 0)"
    )
    conn.execute(
        "INSERT INTO support_card_data (id, chara_id, rarity, support_card_type, skill_set_id) "
        "VALUES (30001, 1001, 3, 2, 100)"
    )
    conn.execute("INSERT INTO skill_data (id, rarity, skill_category) VALUES (200001, 1, 1)")
    conn.commit()
    conn.close()


@pytest.fixture()
def service(settings: Settings) -> SyncService:
    db = Database(settings.db_path)
    try:
        yield SyncService(db, settings)
    finally:
        db.close()


def test_apply_path_imports_and_records_snapshot(
    service: SyncService, settings: Settings, tmp_path: Path
) -> None:
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)

    result = service.apply_path(mdb, game_version="2.0.2", data_version="mdb-cn-20261022")

    assert result["imported"] is True
    assert result["sha256"]
    assert result["summary"]["characters"] == 1
    assert result["summary"]["skills"] == 1
    assert result["summary"]["missing_columns"] == {}

    snapshot_file = settings.snapshots_dir / "mdb-cn-20261022.json"
    assert snapshot_file.is_file()
    snapshot = service.history(limit=5)[0]
    assert snapshot["version"] == "mdb-cn-20261022"
    assert snapshot["game_version"] == "2.0.2"
    assert snapshot["via"] == "path"
    assert snapshot["datasets"]["characters"] == 1

    status = service.status()
    assert status["data_version"]["version"] == "mdb-cn-20261022"
    assert status["sha256"] and status["sha256"] == result["sha256"][:12]


def test_reimporting_same_file_is_skipped(service: SyncService, tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)

    first = service.apply_path(mdb, data_version="mdb-cn-20261022")
    second = service.apply_path(mdb, data_version="mdb-cn-20261023")

    assert first["imported"] is True
    assert second["imported"] is False
    assert "已是最新" in second["reason"] or "已是这一版" in second["reason"]
    # 跳过时不应留下新的快照
    assert len(service.history(limit=10)) == 1
    assert service.status()["data_version"]["version"] == "mdb-cn-20261022"


def test_force_reimports_same_file(service: SyncService, tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)
    service.apply_path(mdb, data_version="mdb-cn-20261022")

    again = service.apply_path(mdb, data_version="mdb-cn-20261022", force=True)

    assert again["imported"] is True


def test_same_version_merges_provenance(service: SyncService, tmp_path: Path) -> None:
    """先由设备提取、后用 CLI 强制重导时，不应丢掉设备来源信息。"""
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)
    service.apply_path(mdb, data_version="mdb-cn-20261022", via="android", extra={"device": "mumu"})

    merged = service.apply_path(mdb, data_version="mdb-cn-20261022", via="cli", force=True)

    snapshot = merged["snapshot"]
    assert snapshot["via"] == "cli"
    assert snapshot["device"] == "mumu"
    assert snapshot["first_imported_at"]
    assert len(service.history(limit=10)) == 1


def test_skipping_backfills_missing_snapshot(settings: Settings, tmp_path: Path) -> None:
    """同步模块接入前导入的数据没有快照，第一次跑同步时应补上而不是留空。"""
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)
    db = Database(settings.db_path)
    try:
        import_master_mdb(db, mdb, game_version="2.0.2", data_version="mdb-cn-20261022")
        service = SyncService(db, settings)

        result = service.apply_path(mdb, via="android")

        assert result["imported"] is False
        assert result["snapshot"]["version"] == "mdb-cn-20261022"
        assert result["snapshot"]["backfilled"] is True
        assert result["snapshot"]["datasets"]["characters"] == 1
        # 第二次不再重复补写
        assert service.apply_path(mdb, via="android")["snapshot"] is None
        assert len(service.history(limit=10)) == 1
    finally:
        db.close()


def test_apply_path_reports_missing_file(service: SyncService, tmp_path: Path) -> None:
    with pytest.raises(MasterMdbError, match="找不到 master.mdb"):
        service.apply_path(tmp_path / "nope.mdb")


def test_history_is_newest_first(service: SyncService, tmp_path: Path) -> None:
    for index, name in enumerate(("mdb-cn-20260101", "mdb-cn-20260202")):
        mdb = tmp_path / f"master-{index}.mdb"
        _build_mdb(mdb, version_text=f"马娘{index}")
        service.apply_path(mdb, data_version=name)

    versions = [item["version"] for item in service.history(limit=10)]

    assert versions == ["mdb-cn-20260202", "mdb-cn-20260101"]


def test_apply_upload_rejects_empty_payload(service: SyncService) -> None:
    with pytest.raises(MasterMdbError, match="空文件"):
        service.apply_upload(b"", "master.mdb")


def test_sync_api_imports_from_path(settings: Settings, tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)

    with TestClient(create_app(settings)) as client:
        assert "sync" in client.get("/api/health").json()["modules"]

        response = client.post(
            "/api/sync/mdb",
            json={"path": str(mdb), "game_version": "2.0.2", "data_version": "mdb-cn-20261022"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["imported"] is True

        info = client.get("/api/system/info").json()
        assert info["data"]["version"] == "mdb-cn-20261022"
        assert info["data"]["game_version"] == "2.0.2"

        again = client.post("/api/sync/mdb", json={"path": str(mdb)})
        assert again.status_code == 200
        assert again.json()["imported"] is False

        history = client.get("/api/sync/history").json()
        assert [item["version"] for item in history] == ["mdb-cn-20261022"]

        status = client.get("/api/sync/status").json()
        assert status["snapshot_count"] == 1
        assert status["latest_snapshot"]["version"] == "mdb-cn-20261022"


def test_sync_api_reports_bad_path_in_chinese(settings: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/sync/mdb", json={"path": str(tmp_path / "nope.mdb")})

    assert response.status_code == 422
    assert "找不到 master.mdb" in response.json()["detail"]


def test_sync_api_upload(settings: Settings) -> None:
    mdb_path = settings.data_dir / "upload-source.mdb"
    _build_mdb(mdb_path)

    with TestClient(create_app(settings)) as client, mdb_path.open("rb") as handle:
        response = client.post(
            "/api/sync/upload",
            files={"file": ("master.mdb", handle, "application/octet-stream")},
            data={"game_version": "2.0.2", "data_version": "mdb-cn-20261022"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] is True
    assert payload["snapshot"]["via"] == "upload"


def test_sync_api_upload_rejects_garbage(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/sync/upload",
            files={"file": ("master.mdb", b"definitely not sqlite", "application/octet-stream")},
        )

    assert response.status_code == 422
    assert "SQLite" in response.json()["detail"]


def test_sync_module_manifest_declares_catalog_dependency(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        modules = {m["id"]: m for m in client.get("/api/system/modules").json()}

    assert modules["sync"]["status"] == "ok"
    assert modules["sync"]["ui"]["panel"] == "/module-assets/sync/panel.js"
    # 面板内嵌在概览的「数据版本」卡里，所以不进侧栏
    assert modules["sync"]["ui"]["hidden"] is True
    assert modules["sync"]["api"] == "/api/sync"
    assert modules["sync"]["details"]["data_version"]["version"] == "unknown"
