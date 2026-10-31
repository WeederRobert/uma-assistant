"""抓取层：只负责「拿到 JSON」，字段解析与换算在 :mod:`modules.circles.service`。

站点是第三方玩家自建服务，接口没有文档也没有鉴权，所以这里做三件事：

- 显式识别 404：社团没被站点收录，和网络故障是两种完全不同的失败，文案要分开
- 非 JSON 响应一律当失败，避免把 HTML 错误页当成数据写库
- 客户端只在模块关闭时释放，测试可以注入假实现
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from .settings import CirclesSettings

logger = logging.getLogger(__name__)


class CirclesSourceError(RuntimeError):
    """拉取失败：网络不可达、非 JSON、社团未收录等。"""


class CirclesClientProtocol(Protocol):
    def fetch_list(self) -> list[dict[str, Any]]: ...

    def fetch_circle(self, circle_id: int) -> dict[str, Any]: ...


class CirclesClient:
    def __init__(
        self,
        options: CirclesSettings,
        *,
        client: httpx.Client | None = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.options = options
        self.logger = logger_ or logger
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={
                "User-Agent": options.user_agent,
                "Accept": "application/json",
            },
            timeout=options.timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_list(self) -> list[dict[str, Any]]:
        payload = self._get_json("/circles")
        if not isinstance(payload, list):
            raise CirclesSourceError("社团月榜接口返回的不是数组，站点可能改了接口")
        return payload

    def fetch_circle(self, circle_id: int) -> dict[str, Any]:
        payload = self._get_json(f"/circles/{int(circle_id)}")
        if not isinstance(payload, dict):
            raise CirclesSourceError("社团详情接口返回的不是对象，站点可能改了接口")
        return payload

    def _get_json(self, path: str) -> Any:
        url = self.options.base_url.rstrip("/") + path
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise CirclesSourceError(f"无法访问 {url}：{exc}") from exc

        if response.status_code == 404:
            raise CirclesSourceError(f"社团没有被 umamusume.space 收录（{url} 返回 404）")
        if response.status_code >= 400:
            raise CirclesSourceError(f"{url} 返回 HTTP {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise CirclesSourceError(f"{url} 返回的不是 JSON：{exc}") from exc
