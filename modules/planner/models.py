from __future__ import annotations

from pydantic import BaseModel, Field


class PlanTarget(BaseModel):
    """一个目标池：``pulls`` 为 0 表示按天井抽数算。"""

    pool_uid: str = Field(min_length=1, max_length=200)
    pulls: int = Field(default=0, ge=0, le=1000, description="计划抽数；0 表示按天井")
    priority: int = Field(default=1, ge=1, le=99)


class PlanRequest(BaseModel):
    name: str = Field(default="我的规划", max_length=60)
    carats: int = Field(default=0, ge=0, le=10_000_000, description="当前萝卜")
    tickets: int = Field(default=0, ge=0, le=10_000, description="当前抽券（1 张 = 1 抽）")
    monthly_income: int = Field(default=0, ge=0, le=1_000_000, description="每月萝卜收入，用户自填")
    note: str = Field(default="", max_length=200)
    targets: list[PlanTarget] = Field(default_factory=list)


class RefreshRequest(BaseModel):
    force: bool = Field(default=False, description="忽略缓存，强制回源并重新导入 master.mdb")
