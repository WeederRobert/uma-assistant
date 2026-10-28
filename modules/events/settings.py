"""活动 / 卡池模块的配置。

环境变量统一 ``UMA_EVENTS_`` 前缀；``.env`` 与内核共用。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class EventsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_EVENTS_",
        extra="ignore",
    )

    # BWiki MediaWiki 接口；本机放一份镜像也可以直接改这里
    api_url: str = "https://wiki.biligame.com/umamusume/api.php"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    referer: str = "https://wiki.biligame.com/umamusume/"
    # BWiki 有速率限制（连续请求会返回 HTML 反爬页），串行且间隔不小于这个值
    interval_seconds: float = 1.5
    timeout_seconds: float = 30.0
    # 反爬页 / 5xx 时的重试次数与退避基数
    retries: int = 3
    backoff_seconds: float = 3.0
    # 单次 SMW 查询上限
    max_results: int = 500
    # ETag 缓存有效期内复用上次响应，避免重复抓整页
    cache_hours: float = 6.0
    # 只跟简中服的分类（逗号分隔，目前只有 cn）
    servers: str = "cn"
    # 日程窗口：往前看几天、往后看几天
    days_before: int = 7
    days_after: int = 30
    # 「近期提醒」的提前量：剩余 / 等待天数不超过这个值就列出来
    near_days: int = 7

    @property
    def server_list(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.servers.split(",") if item.strip())
