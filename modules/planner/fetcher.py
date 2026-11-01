"""抓取层：串行、限速、TTL 缓存。

只负责「拿到 JSON」，解析在 :mod:`modules.planner.sources`。BWiki 不回 ``ETag`` /
``Last-Modified``，拿不到 304，所以只能靠 TTL + 面板上的强制刷新；失败时优先用旧缓存
并把 ``stale`` 标出来，让面板提示「素材可能过期」。

模块之间不互相 import，这里独立实现一套（与 ``events`` 的抓取器同一套礼节）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from .settings import PlannerSettings

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504, 567}


class FetchError(RuntimeError):
    """抓取失败（网络、限速反爬页、非 JSON 响应等）。"""


@dataclass(frozen=True, slots=True)
class FetchResult:
    payload: dict[str, Any]
    url: str
    from_cache: bool = False
    stale: bool = False
    fetched_at: str = ""


class Fetcher(Protocol):
    """抓取层的最小契约，测试里注入假实现。"""

    def get(self, query: str, *, force: bool = False) -> FetchResult: ...


class WikiFetcher:
    def __init__(
        self,
        settings: PlannerSettings,
        cache_dir: Path,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self._lock = threading.Lock()
        self.logger = logger_ or logger
        self._client = client or httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Referer": settings.referer,
                "Accept": "application/json",
            },
            timeout=settings.timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ cache
    def _cache_path(self, query: str) -> Path:
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, query: str) -> FetchResult | None:
        path = self._cache_path(query)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        fetched_at = str(raw.get("fetched_at") or "")
        return FetchResult(
            payload=raw.get("payload") or {},
            url=str(raw.get("url") or self.settings.api_url),
            from_cache=True,
            fetched_at=fetched_at,
        )

    def _write_cache(self, query: str, result: FetchResult) -> None:
        path = self._cache_path(query)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "url": result.url,
                    "payload": result.payload,
                    "fetched_at": result.fetched_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

    @staticmethod
    def _fresh(fetched_at: str, hours: float) -> bool:
        if hours <= 0 or not fetched_at:
            return False
        try:
            stamp = datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        return (datetime.now(UTC) - stamp).total_seconds() < hours * 3600

    # ------------------------------------------------------------------- call
    def _throttle(self) -> None:
        if self._last_call is None:
            return
        elapsed = self._clock() - self._last_call
        wait = self.settings.interval_seconds - elapsed
        if wait > 0:
            self._sleep(wait)

    def get(self, query: str, *, force: bool = False) -> FetchResult:
        with self._lock:
            cached = self._read_cache(query)
            if not force and cached and self._fresh(cached.fetched_at, self.settings.cache_hours):
                return cached

            params = {"action": "ask", "query": query, "format": "json", "formatversion": "2"}
            last_error: Exception | None = None
            for attempt in range(max(self.settings.retries, 1)):
                self._throttle()
                try:
                    response = self._client.get(self.settings.api_url, params=params)
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    self._last_call = self._clock()
                    if response.status_code in RETRY_STATUS:
                        last_error = FetchError(f"BWiki 返回 {response.status_code}")
                    elif response.status_code >= 400:
                        raise FetchError(f"BWiki 返回 {response.status_code}")
                    else:
                        return self._accept(query, response)
                if attempt + 1 < max(self.settings.retries, 1):
                    self._sleep(self.settings.backoff_seconds * (attempt + 1))

            if cached is not None:
                self.logger.warning("抓取失败，改用旧缓存：%s", last_error)
                return FetchResult(
                    payload=cached.payload,
                    url=cached.url,
                    from_cache=True,
                    stale=True,
                    fetched_at=cached.fetched_at,
                )
            raise FetchError(f"抓取失败且没有缓存：{last_error}")

    def _accept(self, query: str, response: httpx.Response) -> FetchResult:
        try:
            payload = response.json()
        except ValueError as exc:
            # 请求过快时 BWiki 会返回 HTML 反爬页
            raise FetchError("BWiki 返回的不是 JSON（可能触发了反爬）") from exc
        if not isinstance(payload, dict) or "error" in payload:
            raise FetchError(
                f"BWiki 返回错误：{payload.get('error') if isinstance(payload, dict) else payload}"
            )
        result = FetchResult(
            payload=payload,
            url=str(response.url),
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._write_cache(query, result)
        return result


def load_cached(cache_dir: Path, query: str) -> FetchResult | None:
    """只读缓存、不联网：冷启动时先把已有的预测数据挂上去。"""
    path = cache_dir / f"{hashlib.sha1(query.encode('utf-8')).hexdigest()[:16]}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return FetchResult(
        payload=raw.get("payload") or {},
        url=str(raw.get("url") or ""),
        from_cache=True,
        fetched_at=str(raw.get("fetched_at") or ""),
    )
