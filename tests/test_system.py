from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.kernel.api import _format_summary
from app.kernel.config import Settings
from app.kernel.database import Database
from app.kernel.dataversion import write_data_version
from app.main import create_app


def test_health_lists_modules(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert {"catalog", "knowledge"} <= set(payload["modules"])


def test_system_info_reports_unknown_data_version(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/system/info").json()

    assert payload["data"]["known"] is False
    assert payload["data"]["version"] == "unknown"
    by_id = {module["id"]: module for module in payload["modules"]}
    assert by_id["catalog"]["status"] == "ok"
    assert by_id["knowledge"]["ui"]["panel"] == "/module-assets/knowledge/panel.js"
    assert by_id["catalog"]["details"]["counts"]["characters"] == 0
    # 侧栏顺序由 order 决定，前端不再自己排
    assert by_id["knowledge"]["order"] < by_id["catalog"]["order"] < by_id["races"]["order"]


def test_refresh_summary_picks_readable_fields() -> None:
    assert _format_summary({"summary": {"races": 1525, "courses": 121}}) == "赛事 1525 · 赛道 121"
    assert _format_summary({"counts": {"predicted": 118, "events": 33}}) == "预测 118 · 活动 33"
    assert (
        _format_summary({"imported": False, "reason": "本地库已是这一版数据"})
        == "本地库已是这一版数据"
    )
    # races 的形状：catalog + geometry
    assert (
        _format_summary({"catalog": {"races": 1525, "courses": 121}, "geometry": {"courses": 107}})
        == "赛事 1525 · 赛道 121 · 几何 107"
    )
    # events 的形状：totals
    assert (
        _format_summary({"totals": {"added": 0, "changed": 2, "removed": 1}})
        == "新增 0 · 变更 2 · 移除 1"
    )
    # 来源元信息不进摘要
    assert _format_summary({"counts": {"skills": 1289, "data_version": "mdb-x"}}) == "技能 1289"
    assert _format_summary(None) == ""


def test_system_info_exposes_first_run_guidance(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/system/info").json()

    setup = payload["setup"]
    assert setup["first_run"] is True
    assert setup["mdb_present"] is False
    assert setup["can_import_via_ui"] is True
    assert setup["degraded_modules"] == []
    assert payload["app"]["frozen"] is False
    assert Path(payload["paths"]["data"]) == settings.data_dir
    assert Path(payload["paths"]["mdb"]) == settings.mdb_path


def test_setup_flips_to_ready_after_import(settings: Settings) -> None:
    db = Database(settings.db_path)
    try:
        write_data_version(db, version="mdb-cn-2026-10-22", game_version="2.0.2", source="cli")
    finally:
        db.close()

    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/system/info").json()

    assert payload["setup"]["first_run"] is False
    assert payload["data"]["known"] is True
    assert payload["data"]["version"] == "mdb-cn-2026-10-22"


def test_knowledge_module_starts_empty(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        stats = client.get("/api/knowledge/stats").json()
        documents = client.get("/api/knowledge/documents").json()

    assert stats["documents"] == 0
    assert stats["chunks"] == 0
    assert documents == []


def test_index_page_served(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "优俊少女助手" in response.text


def test_module_assets_served(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/module-assets/catalog/panel.js")
    assert response.status_code == 200
    assert "mount" in response.text
    assert Path(settings.modules_dir).is_dir()


def test_default_dirs_live_inside_workspace(tmp_path: Path) -> None:
    """默认落点都在工作区内，不往系统盘写数据（打包后再统一定位置）。"""
    assert Path(Settings.model_fields["data_dir"].default).name == "data"

    settings = Settings(data_dir=tmp_path)
    assert settings.snapshots_dir == tmp_path / "snapshots"
    assert settings.tmp_dir == tmp_path / ".tmp"

    settings.ensure_dirs()
    assert settings.snapshots_dir.is_dir()
    assert settings.tmp_dir.is_dir()
