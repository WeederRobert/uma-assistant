from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.kernel.config import Settings
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
