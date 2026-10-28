"""抓取层：串行、限速、带 ETag 条件请求，失败时降级使用本地缓存。

只负责「拿到 JSON」，不解析业务字段——解析在 :mod:`modules.events.sources`。
这样 BWiki 页面结构变动时只需要改解析层。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from .settings import EventsSettings

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """抓取失败（网络、速率限制、非 JSON 响应等）。"""


@dataclass(frozen=True, slots=True)
class FetchResult:
    payload: dict[str, Any]
    url: str
    etag: str | None = None
    from_cache: bool = False
    # 网络不可用、返回的是本地缓存 → 数据可能过期
    stale: bool = False
    fetched_at: str = ""
    note: str = ""


class Fetcher(Protocol):
    """抓取层的最小契约，测试里可以注入假实现。"""

    def get(self, params: Mapping[str, str], *, force: bool = False) -> FetchResult: ...


@dataclass(slots=True)
class _CacheEntry:
    url: str
    etag: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    fetched_at: str = ""
    last_modified: str | None = None


def _is_fresh(entry: _CacheEntry, hours: float) -> bool:
    if hours <= 0 or not entry.fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(entry.fetched_at)
    except ValueError:
        return False
    age = datetime.now(UTC) - fetched
    return age.total_seconds() < hours * 3600


class WikiFetcher:
    """MediaWiki ``api.php`` 的抓取器。

    - 串行调用，两次请求之间至少间隔 ``interval_seconds``
    - 缓存有效期内直接复用（``force=True`` 跳过），减少对 wiki 的请求
    - 带 ``If-None-Match`` 做条件请求，304 时复用缓存内容
    - BWiki 在请求过快时会返回 HTML 反爬页，这里识别为失败并退避重试
    """

    def __init__(
        self,
        settings: EventsSettings,
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
    def _cache_path(self, params: Mapping[str, str]) -> Path:
        raw = json.dumps({"url": self.settings.api_url, **dict(params)}, sort_keys=True)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, params: Mapping[str, str]) -> _CacheEntry | None:
        path = self._cache_path(params)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return _CacheEntry(
            url=raw.get("url", ""),
            etag=raw.get("etag"),
            payload=raw.get("payload") or {},
            fetched_at=raw.get("fetched_at", ""),
            last_modified=raw.get("last_modified"),
        )

    def _write_cache(self, params: Mapping[str, str], entry: _CacheEntry) -> None:
        path = self._cache_path(params)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "url": entry.url,
                    "etag": entry.etag,
                    "last_modified": entry.last_modified,
                    "fetched_at": entry.fetched_at,
                    "payload": entry.payload,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)

    # ------------------------------------------------------------------- rate
    def _throttle(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last_call is not None:
                wait = self.settings.interval_seconds - (now - self._last_call)
                if wait > 0:
                    self._sleep(wait)
            self._last_call = self._clock()

    # ------------------------------------------------------------------- call
    def get(self, params: Mapping[str, str], *, force: bool = False) -> FetchResult:
        cached = self._read_cache(params)
        if cached is not None and not force and _is_fresh(cached, self.settings.cache_hours):
            return FetchResult(
                payload=cached.payload,
                url=cached.url,
                etag=cached.etag,
                from_cache=True,
                fetched_at=cached.fetched_at,
                note=f"距上次抓取不足 {self.settings.cache_hours:g} 小时，直接用缓存",
            )

        # BWiki 的 api.php 目前既不返回 ETag 也不返回 Last-Modified，条件请求在它身上
        # 不会生效；这里保留 If-None-Match 是为了换镜像/加代理时也能省流量。
        headers = {"If-None-Match": cached.etag} if cached and cached.etag else {}
        query = {**dict(params), "format": "json"}

        last_error = ""
        for attempt in range(self.settings.retries + 1):
            self._throttle()
            try:
                response = self._client.get(self.settings.api_url, params=query, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"网络请求失败：{exc}"
                self.logger.warning("抓取失败（第 %d 次）：%s", attempt + 1, exc)
                self._backoff(attempt)
                continue

            if response.status_code == 304 and cached is not None:
                logger.info("内容未变化（304），复用缓存：%s", cached.url or params)
                return FetchResult(
                    payload=cached.payload,
                    url=cached.url or str(response.url),
                    etag=cached.etag,
                    from_cache=True,
                    fetched_at=cached.fetched_at,
                    note="内容未变化（304 Not Modified），使用上次抓取结果",
                )

            if response.status_code in {429, 500, 502, 503, 504, 567}:
                last_error = f"服务端返回 {response.status_code}，可能触发了 BWiki 的速率限制"
                self.logger.warning(
                    "抓取失败（第 %d 次）：HTTP %s", attempt + 1, response.status_code
                )
                self._backoff(attempt)
                continue

            payload = self._load_json(response, params)
            if payload is None:
                last_error = "返回的不是 JSON（BWiki 在请求过快时会返回反爬页面），请降低频率后重试"
                self._backoff(attempt)
                continue

            if response.status_code >= 400:
                raise FetchError(
                    f"BWiki 返回 HTTP {response.status_code}：{_snippet(response.text)}"
                )

            entry = _CacheEntry(
                url=str(response.url),
                etag=response.headers.get("ETag"),
                payload=payload,
                fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
                last_modified=response.headers.get("Last-Modified"),
            )
            self._write_cache(params, entry)
            note = ""
            if cached is not None and cached.payload == payload:
                note = "响应内容与上次抓取完全一致"
            return FetchResult(
                payload=payload,
                url=entry.url,
                etag=entry.etag,
                fetched_at=entry.fetched_at,
                note=note,
            )

        if cached is not None:
            self.logger.warning("抓取失败，降级使用缓存：%s", last_error)
            return FetchResult(
                payload=cached.payload,
                url=cached.url,
                etag=cached.etag,
                from_cache=True,
                stale=True,
                fetched_at=cached.fetched_at,
                note=f"{last_error}；已改用 {cached.fetched_at[:19]} 的缓存",
            )
        raise FetchError(last_error or "抓取失败")

    def _backoff(self, attempt: int) -> None:
        if attempt < self.settings.retries:
            self._sleep(self.settings.backoff_seconds * (attempt + 1))

    @staticmethod
    def _load_json(response: httpx.Response, params: Mapping[str, str]) -> dict[str, Any] | None:
        text = response.text.lstrip()
        if not text.startswith("{"):
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if "error" in payload:
            info = payload["error"]
            message = info.get("info") if isinstance(info, dict) else str(info)
            raise FetchError(f"BWiki 接口报错：{message}（查询 {dict(params)}）")
        return payload


def _snippet(text: str, limit: int = 120) -> str:
    return " ".join(text.split())[:limit]
