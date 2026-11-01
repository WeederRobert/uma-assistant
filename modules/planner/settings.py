"""规划模块的配置。

环境变量统一 ``UMA_PLANNER_`` 前缀；``.env`` 与内核共用。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class PlannerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="UMA_PLANNER_",
        extra="ignore",
    )

    # BWiki MediaWiki 接口；本机放一份镜像也可以直接改这里
    api_url: str = "https://wiki.biligame.com/umamusume/api.php"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    referer: str = "https://wiki.biligame.com/umamusume/"
    # BWiki 对请求频率敏感，串行且间隔不小于这个值
    interval_seconds: float = 1.5
    timeout_seconds: float = 30.0
    retries: int = 3
    backoff_seconds: float = 3.0
    max_results: int = 500
    # 预测数据变化慢，默认缓存 6 小时；面板的「刷新预测」会绕过缓存
    cache_hours: float = 6.0
    # 千里眼窗口：默认往后看半年
    horizon_days: int = 180
    # 没有兑换行（gacha_exchange）时的兜底口径，与 mdb 实测众数一致
    default_ceiling_pulls: int = 200
    default_cost_single: int = 150
