"""赛道模块的配置。

环境变量统一 ``UMA_RACES_`` 前缀；``.env`` 与内核共用。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class RacesSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_RACES_",
        extra="ignore",
    )

    # BWiki 数据页；赛道几何只在刷新时抓，平时读本地缓存
    raw_url: str = "https://wiki.biligame.com/umamusume/index.php"
    api_url: str = "https://wiki.biligame.com/umamusume/api.php"
    geometry_page: str = "Data:Course/jian data.json"
    user_agent: str = "uma-assistant/0.1 (local research)"
    referer: str = "https://wiki.biligame.com/umamusume/"
    # 数据页很少变，缓存时间比活动模块长
    cache_hours: int = 24
    interval_seconds: float = 1.5
    timeout_seconds: int = 60
    retries: int = 2
    backoff_seconds: float = 3.0
    # 可选：覆盖 master.mdb 路径（默认为内核的 settings.mdb_path）
    mdb_path: str | None = None
    search_limit: int = 50
