"""卡池模块的配置。

环境变量统一 ``UMA_GACHA_`` 前缀；``.env`` 与内核共用。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class GachaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_GACHA_",
        extra="ignore",
    )

    # 可选：覆盖 master.mdb 路径（默认为内核的 settings.mdb_path）
    mdb_path: str | None = None
