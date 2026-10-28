from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import app.kernel.config as config_module


@pytest.fixture()
def reload_config(monkeypatch: pytest.MonkeyPatch):
    """在「源码运行 / 打包运行」两种形态间切换 ``config`` 模块。

    路径常量在导入时求值，所以只能通过重载模块来验证；teardown 再重载一次复原，
    避免把打了补丁的 ``sys`` 泄漏给其它用例。
    """

    def _reload(frozen: bool, *, bundle: Path | None = None, exe: Path | None = None):
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        if bundle is None:
            monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        else:
            monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        if exe is not None:
            monkeypatch.setattr(sys, "executable", str(exe), raising=False)
        return importlib.reload(config_module)

    yield _reload
    importlib.reload(config_module)


def test_dev_layout_uses_project_dir() -> None:
    assert config_module.is_frozen() is False
    assert config_module.resource_root() == config_module.BASE_DIR
    assert config_module.app_root() == config_module.BASE_DIR
    assert config_module.default_data_dir() == config_module.BASE_DIR / "data"


def test_frozen_layout_moves_writable_data_to_local_app_data(
    reload_config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "bundle"
    exe_dir = tmp_path / "install"
    local = tmp_path / "LocalAppData"
    bundle.mkdir()
    exe_dir.mkdir()
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("UMA_DATA_DIR", raising=False)

    config = reload_config(True, bundle=bundle, exe=exe_dir / "uma-assistant.exe")

    assert config.is_frozen() is True
    assert config.resource_root() == bundle
    assert config.app_root() == exe_dir
    # 只读资源跟随解包目录，可写数据离开安装目录
    assert config.Settings().modules_dir == bundle / "modules"
    assert config.Settings().static_dir == bundle / "static"
    assert config.Settings().data_dir == local / "uma-assistant" / "data"
    assert config.Settings().tmp_dir == local / "uma-assistant" / "data" / ".tmp"


def test_frozen_layout_falls_back_without_local_app_data(
    reload_config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("UMA_DATA_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    config = reload_config(True, bundle=tmp_path / "bundle", exe=tmp_path / "uma-assistant.exe")

    assert config.default_data_dir() == Path.home() / "AppData" / "Local" / "uma-assistant" / "data"
