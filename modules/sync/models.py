"""同步接口的请求体。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MdbPathRequest(BaseModel):
    path: str = Field(description="master.mdb 的绝对路径")
    game_version: str | None = Field(default=None, description="国服游戏版本，例如 2.0.2")
    data_version: str | None = Field(default=None, description="数据版本号，默认 mdb-cn-<今天>")
    force: bool = Field(default=False, description="即使 sha256 与本地库一致也重新导入")


class DeviceRequest(BaseModel):
    serial: str | None = Field(default=None, description="adb 设备序列号，例如 127.0.0.1:16384")
    package: str | None = Field(default=None, description="游戏包名，默认 com.bilibili.umamusu")
    force: bool = Field(default=False, description="即使 sha256 与本地库一致也重新导入")
