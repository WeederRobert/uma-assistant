from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """内核配置。

    环境变量统一使用 ``UMA_`` 前缀，避免与模块自己的配置（如 DEEPSEEK_*）冲突。
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_",
        extra="ignore",
    )

    app_name: str = "优俊少女助手"
    data_dir: Path = BASE_DIR / "data"
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

        mdb 动辄二十几 MB，且多数机器 C 盘更紧张，所以中转文件一律放这里，
        不用系统临时目录。打包成独立程序后再定最终的临时/持久化位置。
        """
        return self.data_dir / ".tmp"

    @property
    def modules_dir(self) -> Path:
        return BASE_DIR / "modules"

    @property
    def static_dir(self) -> Path:
        return BASE_DIR / "static"

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
