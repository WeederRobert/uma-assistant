"""数据同步模块的配置。

环境变量统一 ``UMA_SYNC_`` 前缀；``.env`` 与内核共用。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class SyncSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_SYNC_",
        extra="ignore",
    )

    # 设备提取
    package: str = "com.bilibili.umamusu"
    adb_path: str | None = None
    serial: str | None = None
    # 提取成功后是否把 mdb 留一份在 data/mdb/ 下，便于不连设备也能重导
    keep_mdb: bool = True
    # 面板上传的文件上限
    upload_max_mb: int = 128
    history_limit: int = 50
