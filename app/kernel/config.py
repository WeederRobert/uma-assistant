from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
APP_ID = "uma-assistant"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出的独立程序里。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """随程序分发的只读资源（``modules/``、``static/``）。

    源码运行时就是项目根；打包后 PyInstaller 把 datas 解到 ``sys._MEIPASS``。
    """
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else BASE_DIR


def app_root() -> Path:
    """放 ``.env`` 的目录：源码运行是项目根，打包后是 exe 同级目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return BASE_DIR


def default_data_dir() -> Path:
    """运行时数据落点（``app.db`` / ``mdb`` / ``logs`` / ``snapshots`` / ``.tmp``）。

    打包后不能写安装目录（可能在 Program Files、只读、且多用户共享），统一落到
    ``%LOCALAPPDATA%\\uma-assistant\\data``；源码运行仍用项目内 ``data/``，便于调试与清理。
    """
    if not is_frozen():
        return BASE_DIR / "data"
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / APP_ID / "data"


class Settings(BaseSettings):
    """内核配置。

    环境变量统一使用 ``UMA_`` 前缀，避免与模块自己的配置（如 DEEPSEEK_*）冲突。
    """

    model_config = SettingsConfigDict(
        env_file=app_root() / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_",
        extra="ignore",
    )

    app_name: str = "优俊少女助手"
    data_dir: Path = default_data_dir()
    host: str = "127.0.0.1"
    port: int = 8737
    log_level: str = "INFO"
    window_width: int = 1280
    window_height: int = 860

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def snapshots_dir(self) -> Path:
        """每次数据导入的版本留档，文件名是数据版本号。"""
        return self.data_dir / "snapshots"

    @property
    def mdb_dir(self) -> Path:
        """从客户端提取的游戏数据留档目录。"""
        return self.data_dir / "mdb"

    @property
    def mdb_path(self) -> Path:
        """留档的 ``master.mdb``；模块需要读原始库时统一从这里取。"""
        return self.mdb_dir / "master.mdb"

    @property
    def tmp_dir(self) -> Path:
        """工作区内的临时目录。

        mdb 动辄二十几 MB，不放系统临时目录（易被清理、也可能与 C 盘空间冲突）；
        打包后它随 ``data_dir`` 一起落在 ``%LOCALAPPDATA%``。
        """
        return self.data_dir / ".tmp"

    @property
    def modules_dir(self) -> Path:
        return resource_root() / "modules"

    @property
    def static_dir(self) -> Path:
        return resource_root() / "static"

    def module_data_dir(self, module_id: str) -> Path:
        path = self.data_dir / "modules" / module_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.data_dir / "modules",
            self.log_dir,
            self.snapshots_dir,
            self.tmp_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
