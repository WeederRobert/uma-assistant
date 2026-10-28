"""抓 BWiki 数据页（``action=raw``），带限速、TTL 缓存与离线降级。

与 ``modules/events`` 的抓取层思路一致但各自独立（模块之间不互相 import）：

- 串行 + 最小间隔，避免触发反爬
- 响应原文落 ``data/modules/races/cache/``，``cache_hours`` 内复用
- 429 / 5xx / 非 JSON 响应按退避重试，仍失败则用旧缓存并标 ``stale``
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .settings import RacesSettings

_RETRY_STATUS = {429, 500, 502, 503, 504, 567}


class WikiError(RuntimeError):
    """抓取失败且没有可用缓存。"""


@dataclass(frozen=True, slots=True)
class PageResult:
    title: str
    text: str
    url: str
    revid: int | None
    revised_at: str | None
    fetched_at: str
    from_cache: bool = False
    stale: bool = False
    note: str | None = None


class PageFetcher(Protocol):
    def fetch(self, title: str, *, force: bool = False) -> PageResult: ...


class WikiPageFetcher:
    def __init__(
        self,
        options: RacesSettings,
        cache_dir: Path,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
        logger=None,
    ) -> None:
        self.options = options
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep
        self._clock = clock
        self._last_request = 0.0
        self._logger = logger

    # ------------------------------------------------------------------ 公开
    def fetch(self, title: str, *, force: bool = False) -> PageResult:
        cached = self._read_cache(title)
        if not force and cached and self._is_fresh(cached):
            return self._as_result(cached, from_cache=True)

        try:
            text = self._request(title)
            return self._finish(title, text, cached)
        except (httpx.HTTPError, WikiError) as exc:
            if cached:
                if self._logger:
                    self._logger.warning("抓取 %s 失败，使用缓存：%s", title, exc)
                return self._as_result(
                    cached, from_cache=True, stale=True, note=f"抓取失败，使用缓存：{exc}"
                )
            raise WikiError(f"抓取 {title} 失败：{exc}") from exc

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ 内部
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.options.timeout_seconds,
                headers={
                    "User-Agent": self.options.user_agent,
                    "Referer": self.options.referer,
                },
            )
            self._owns_client = True
        return self._client

    def _throttle(self) -> None:
        gap = self.options.interval_seconds
        if gap <= 0:
            return
        wait = self._last_request + gap - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()

    def _raw_url(self, title: str) -> str:
        return f"{self.options.raw_url}?title={quote(title)}&action=raw"

    def _request(self, title: str) -> str:
        url = self._raw_url(title)
        last_error: Exception | None = None
        for attempt in range(max(1, self.options.retries)):
            self._throttle()
            try:
                response = self.client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code in _RETRY_STATUS:
                    last_error = WikiError(f"HTTP {response.status_code}")
                elif response.status_code >= 400:
                    raise WikiError(f"HTTP {response.status_code}")
                else:
                    text = response.text
                    if not text.lstrip().startswith(("<nowiki>", "[")):
                        # 反爬页是 HTML，拿去做 JSON 解析只会得到莫名其妙的错误
                        last_error = WikiError("返回的不是数据页内容（可能是反爬页）")
                    else:
                        return text
            if attempt + 1 < max(1, self.options.retries):
                self._sleep(self.options.backoff_seconds * (attempt + 1))
        raise WikiError(str(last_error) if last_error else "未知错误")

    def _finish(self, title: str, text: str, cached: dict[str, Any] | None) -> PageResult:
        revid, revised_at = self._revision(title)
        payload = {
            "title": title,
            "text": text,
            "url": self._raw_url(title),
            "revid": revid,
            "revised_at": revised_at,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self._write_cache(title, payload)
        return self._as_result(payload, from_cache=False)

    def _revision(self, title: str) -> tuple[int | None, str | None]:
        """取页面版本号，用于「这条结论来自哪一版」。失败不影响主流程。"""
        try:
            self._throttle()
            response = self.client.get(
                self.options.api_url,
                params={
                    "action": "query",
                    "prop": "revisions",
                    "rvprop": "ids|timestamp",
                    "format": "json",
                    "titles": title,
                },
            )
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages") or {}
        except (httpx.HTTPError, ValueError):
            return None, None
        for page in pages.values():
            revisions = page.get("revisions") or []
            if revisions:
                revision = revisions[0]
                return revision.get("revid"), revision.get("timestamp")
        return None, None

    # ------------------------------------------------------------------ 缓存
    def _cache_path(self, title: str) -> Path:
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, title: str) -> dict[str, Any] | None:
        path = self._cache_path(title)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and data.get("text") else None

    def _write_cache(self, title: str, payload: dict[str, Any]) -> None:
        path = self._cache_path(title)
        with suppress(OSError):  # 写缓存失败不该影响主流程
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _is_fresh(self, cached: dict[str, Any]) -> bool:
        hours = self.options.cache_hours
        if hours <= 0:
            return False
        fetched_at = cached.get("fetched_at")
        if not fetched_at:
            return False
        try:
            moment = datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return datetime.now(UTC) - moment < timedelta(hours=hours)

    def _as_result(
        self,
        cached: dict[str, Any],
        *,
        from_cache: bool,
        stale: bool = False,
        note: str | None = None,
    ) -> PageResult:
        return PageResult(
            title=str(cached.get("title") or ""),
            text=str(cached.get("text") or ""),
            url=str(cached.get("url") or ""),
            revid=cached.get("revid"),
            revised_at=cached.get("revised_at"),
            fetched_at=str(cached.get("fetched_at") or ""),
            from_cache=from_cache,
            stale=stale,
            note=note,
        )
