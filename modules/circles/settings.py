"""社团模块的配置。

环境变量统一 ``UMA_CIRCLES_`` 前缀；``.env`` 与内核共用。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class CirclesSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_CIRCLES_",
        extra="ignore",
    )

    # umamusume.space 只开 HTTP，没有 HTTPS 端点
    base_url: str = "http://umamusume.space/uma/api"
    timeout_seconds: float = 30.0
    user_agent: str = "uma-assistant/1.0"
    # 单个社团的成员明细约 1.4 MB，缓存在本地，避免反复拉取
    member_cache_minutes: float = 10.0
    list_limit: int = 300
