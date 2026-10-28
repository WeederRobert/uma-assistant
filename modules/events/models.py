from __future__ import annotations

from pydantic import BaseModel, Field


class RefreshRequest(BaseModel):
    # 留空表示抓取所有启用的来源
    sources: list[str] | None = Field(default=None, description="要刷新的来源 key，留空表示全部")
    # 面板点「刷新」时传 True，跳过缓存强制回源
    force: bool = Field(default=False, description="忽略缓存有效期，强制重新抓取")
