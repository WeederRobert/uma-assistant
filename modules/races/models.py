from __future__ import annotations

from pydantic import BaseModel, Field


class RefreshRequest(BaseModel):
    force: bool = Field(default=False, description="忽略缓存，重新抓数据页")
    mdb_path: str | None = Field(default=None, description="可选：指定 master.mdb 路径")
