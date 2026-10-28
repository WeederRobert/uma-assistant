from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.events import sources, store
from modules.events.fetcher import FetchError, FetchResult, WikiFetcher
from modules.events.service import EventsService, describe, diff_items
from modules.events.service import zone_label as service_zone_label
from modules.events.settings import EventsSettings

API_URL = "https://wiki.biligame.com/umamusume/api.php"


def ask_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按 ``action=ask`` 的真实结构拼响应。"""
    results: dict[str, Any] = {}
    for row in rows:
        title = row["title"]
        printouts: dict[str, list[str]] = {}
        for field in ("开始描述", "结束描述", "开始时间", "结束时间", "名称", "类型"):
            if field in row:
                printouts[field] = [row[field]]
        for field in ("角色ID", "支援卡ID"):
            if field in row:
                printouts[field] = list(row[field])
        results[title] = {
            "printouts": printouts,
            "fulltext": title,
            "fullurl": f"https://wiki.biligame.com/umamusume/{title}",
            "namespace": 0,
            "exists": "1",
        }
    return {"query": {"results": results}}


class FakeFetcher:
    """按来源 key 返回预置响应，用于替代真实网络。"""

    def __init__(self, payloads: Mapping[str, dict[str, Any]]) -> None:
        self.payloads = dict(payloads)
        self.calls: list[dict[str, str]] = []
        self.forces: list[bool] = []

    def get(self, params: Mapping[str, str], *, force: bool = False) -> FetchResult:
        self.calls.append(dict(params))
        self.forces.append(force)
        query = params["query"]
        category = query.split("[[分类:")[1].split("]]")[0]
        payload = self.payloads.get(category, ask_payload([]))
        return FetchResult(
            payload=payload,
            url=f"{API_URL}?query={category}",
            fetched_at="2026-10-26T14:00:00+00:00",
        )


@pytest.fixture()
def options() -> EventsSettings:
    return EventsSettings(servers="cn", interval_seconds=0.0, retries=1, backoff_seconds=0.0)


@pytest.fixture()
def db(settings: Settings) -> Database:
    database = Database(settings.db_path)
    try:
        yield database
    finally:
        database.close()


def build_service(db: Database, options: EventsSettings, tmp_path: Path, payloads) -> EventsService:
    return EventsService(db, options, tmp_path / "events", fetcher=FakeFetcher(payloads))


# ------------------------------------------------------------------ fetcher
def test_fetcher_enforces_interval_between_calls(tmp_path: Path) -> None:
    sleeps: list[float] = []
    clock = iter([0.0, 0.2, 0.2, 0.5, 0.5, 2.0, 2.0])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    options = EventsSettings(interval_seconds=1.5, cache_hours=0)
    fetcher = WikiFetcher(
        options,
        tmp_path,
        client=client,
        sleep=sleeps.append,
        clock=lambda: next(clock, 3.0),
    )
    fetcher.get({"action": "ask"})
    fetcher.get({"action": "ask"})

    assert sleeps and sleeps[0] > 0
    assert sleeps[0] <= 1.5


def test_fetcher_uses_etag_and_reuses_cache_on_304(tmp_path: Path) -> None:
    seen_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("If-None-Match"))
        if request.headers.get("If-None-Match"):
            return httpx.Response(304)
        return httpx.Response(200, json={"value": 1}, headers={"ETag": 'W/"abc"'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = WikiFetcher(
        EventsSettings(interval_seconds=0.0, cache_hours=0),
        tmp_path,
        client=client,
        sleep=lambda _: None,
    )

    first = fetcher.get({"action": "ask", "query": "x"})
    second = fetcher.get({"action": "ask", "query": "x"})

    assert first.from_cache is False
    assert first.etag == 'W/"abc"'
    assert seen_headers == [None, 'W/"abc"']
    assert second.from_cache is True
    assert second.payload == {"value": 1}


def test_fetcher_reports_rate_limit_page_as_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html><html>challenge</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = WikiFetcher(
        EventsSettings(interval_seconds=0.0, retries=0, cache_hours=0),
        tmp_path,
        client=client,
        sleep=lambda _: None,
    )

    with pytest.raises(FetchError, match="不是 JSON"):
        fetcher.get({"action": "ask"})


def test_fetcher_falls_back_to_cache_when_offline(tmp_path: Path) -> None:
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(200, text="<html>blocked</html>")
        return httpx.Response(200, json={"value": 2}, headers={"ETag": 'W/"v2"'})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = WikiFetcher(
        EventsSettings(interval_seconds=0.0, retries=0, cache_hours=0),
        tmp_path,
        client=client,
        sleep=lambda _: None,
    )
    fetcher.get({"action": "ask"})
    state["fail"] = True
    result = fetcher.get({"action": "ask"})

    assert result.stale is True
    assert result.payload == {"value": 2}
    assert "缓存" in result.note


def test_fetcher_serves_from_cache_within_ttl(tmp_path: Path) -> None:
    hits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(200, json={"value": 3})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = WikiFetcher(
        EventsSettings(interval_seconds=0.0, cache_hours=6.0),
        tmp_path,
        client=client,
        sleep=lambda _: None,
    )

    fetcher.get({"action": "ask"})
    cached = fetcher.get({"action": "ask"})
    forced = fetcher.get({"action": "ask"}, force=True)

    assert len(hits) == 2
    assert cached.from_cache is True
    assert "缓存" in cached.note
    assert forced.from_cache is False


def test_refresh_passes_force_to_fetcher(
    db: Database, options: EventsSettings, tmp_path: Path
) -> None:
    fetcher = FakeFetcher({})
    service = EventsService(db, options, tmp_path / "events", fetcher=fetcher)

    service.refresh(["cn-character-pool"])
    service.refresh(["cn-character-pool"], force=True)

    assert fetcher.forces == [False, True]


def test_refresh_reports_error_without_losing_other_sources(
    db: Database, options: EventsSettings, tmp_path: Path
) -> None:
    class Failing:
        def get(self, params: Mapping[str, str], *, force: bool = False) -> FetchResult:
            category = params["query"].split("[[分类:")[1].split("]]")[0]
            if category == "简中支援卡卡池":
                raise FetchError("模拟限速")
            return FetchResult(
                payload=ask_payload([{"title": "A", "开始描述": "常驻"}]),
                url="https://example.invalid/ok",
            )

    service = EventsService(db, options, tmp_path / "events", fetcher=Failing())

    result = service.refresh()

    assert result["ok"] is False
    failed = [row for row in result["results"] if not row["ok"]]
    assert failed and "模拟限速" in failed[0]["error"]
    # 一个来源失败不影响另一个来源入库
    assert result["totals"]["items"] > 0


# ------------------------------------------------------------------ sources
def test_parse_uses_source_timezone_and_ids() -> None:
    payload = ask_payload(
        [
            {
                "title": "3★跳舞城 优俊少女招募",
                "开始描述": "2026/10/24 12:00",
                "结束描述": "2026/11/03 11:59",
                "角色ID": ["110701"],
            }
        ]
    )
    items = sources.parse_ask_payload(sources.SOURCE_BY_KEY["cn-character-pool"], payload)

    assert len(items) == 1
    item = items[0]
    assert item.server == "cn"
    assert item.ids == {"角色ID": ["110701"]}
    assert item.starts_at.utcoffset().total_seconds() == 8 * 3600
    assert item.starts_at.hour == 12
    assert item.source_level == "B"


def test_parse_marks_placeholder_and_permanent_entries() -> None:
    # 来源只剩简中服两个分类：占位行降 D 级，写「常驻」的行没有具体时间并标记 permanent
    pool_items = sources.parse_ask_payload(
        sources.SOURCE_BY_KEY["cn-character-pool"],
        ask_payload(
            [
                {"title": "无马娘卡池，占位用 20261020", "开始描述": "2026/10/20 12:00"},
                {"title": "优俊少女招募", "开始描述": "常驻", "结束描述": "常驻"},
            ]
        ),
    )

    assert pool_items[0].source_level == "D"
    assert pool_items[1].starts_at is None
    assert pool_items[1].permanent is True
    assert pool_items[1].server == "cn"
    assert pool_items[1].source_level == "B"


def test_ask_query_matches_page_convention() -> None:
    query = sources.SOURCE_BY_KEY["cn-support-pool"].ask_query(500)

    assert query.startswith("[[分类:简中支援卡卡池]]")
    assert "sort=开始时间" in query
    assert "limit=500" in query


# ------------------------------------------------------------------ service
def test_refresh_records_added_then_nothing(
    db: Database, options: EventsSettings, tmp_path: Path
) -> None:
    payloads = {
        "简中赛马娘卡池": ask_payload(
            [
                {
                    "title": "3★跳舞城 优俊少女招募",
                    "开始描述": "2026/10/24 12:00",
                    "结束描述": "2026/11/03 11:59",
                    "角色ID": ["110701"],
                }
            ]
        ),
        "简中支援卡卡池": ask_payload(
            [
                {
                    "title": "精选 协助卡招募",
                    "开始描述": "2026/10/24 12:00",
                    "结束描述": "2026/11/03 11:59",
                }
            ]
        ),
    }
    service = build_service(db, options, tmp_path, payloads)

    first = service.refresh()
    second = service.refresh()

    assert first["ok"] is True
    assert first["totals"]["added"] == 2
    assert second["totals"] == {"added": 0, "changed": 0, "removed": 0, "items": 2}

    changes = store.recent_changes(db)
    assert {row["action"] for row in changes} == {"added"}
    assert len(changes) == 2


def test_refresh_detects_changed_and_removed(
    db: Database, options: EventsSettings, tmp_path: Path
) -> None:
    base = [
        {
            "title": "3★跳舞城 优俊少女招募",
            "开始描述": "2026/10/24 12:00",
            "结束描述": "2026/11/03 11:59",
        },
        {"title": "3★目白拉莫娜 优俊少女招募", "开始描述": "2026/10/02 12:01"},
    ]
    service = build_service(
        db, options, tmp_path, {"简中赛马娘卡池": ask_payload(base), "活动": ask_payload([])}
    )
    service.refresh()

    changed = [dict(base[0], 结束描述="2026/11/05 11:59")]
    service = build_service(
        db, options, tmp_path, {"简中赛马娘卡池": ask_payload(changed), "活动": ask_payload([])}
    )
    result = service.refresh(["cn-character-pool"])

    assert result["totals"]["changed"] == 1
    assert result["totals"]["removed"] == 1
    actions = [row["action"] for row in store.recent_changes(db)]
    assert "changed" in actions and "removed" in actions
    rows = {row["uid"]: row for row in store.list_items(db, include_removed=True)}
    kept = rows["cn-character-pool:3★跳舞城 优俊少女招募"]
    assert kept["ends_at"].startswith("2026-11-05")
    assert rows["cn-character-pool:3★目白拉莫娜 优俊少女招募"]["removed_at"]


def test_schedule_states_and_week_flags(db: Database, tmp_path: Path) -> None:
    options = EventsSettings(servers="cn", days_before=60, days_after=60)
    payloads = {
        "简中赛马娘卡池": ask_payload(
            [
                {"title": "进行中", "开始描述": "2026/10/20 12:00", "结束描述": "2026/11/03 11:59"},
                {"title": "未开始", "开始描述": "2026/11/10 12:00", "结束描述": "2026/11/20 11:59"},
                {"title": "已结束", "开始描述": "2026/09/01 12:00", "结束描述": "2026/09/10 11:59"},
            ]
        ),
        "活动": ask_payload([]),
    }
    service = build_service(db, options, tmp_path, payloads)
    service.refresh(["cn-character-pool"])
    schedule = service.schedule()

    by_title = {item["title"]: item for item in schedule["items"]}
    assert by_title["进行中"]["state"] == "active"
    assert by_title["未开始"]["state"] == "upcoming"
    assert by_title["已结束"]["state"] == "ended"
    assert by_title["进行中"]["days_left"] is not None
    assert schedule["counts"]["active"] == 1
    # 服务器与可信级别要能被前端渲染成角标
    assert by_title["进行中"]["server_label"] == "简中服"
    assert by_title["进行中"]["source_level"] == "B"


def test_near_term_lists_upcoming_and_ending_soon(db: Database, tmp_path: Path) -> None:
    """「近期提醒」要能回答「还有什么要开始 / 什么快结束了」。"""
    now = datetime.now(UTC)

    def stamp(days: float) -> str:
        return (now + timedelta(days=days)).strftime("%Y/%m/%d %H:%M")

    options = EventsSettings(servers="cn", days_before=60, days_after=60, near_days=7)
    payloads = {
        "简中赛马娘卡池": ask_payload(
            [
                {"title": "快结束", "开始描述": stamp(-3), "结束描述": stamp(3)},
                {"title": "还早", "开始描述": stamp(-3), "结束描述": stamp(40)},
                {"title": "五天后开始", "开始描述": stamp(5), "结束描述": stamp(15)},
                {"title": "早就结束", "开始描述": stamp(-20), "结束描述": stamp(-10)},
            ]
        ),
        "活动": ask_payload([]),
    }
    service = build_service(db, options, tmp_path, payloads)
    service.refresh(["cn-character-pool"])

    near = service.schedule()["near_term"]

    assert near["horizon_days"] == 7
    assert near["upcoming_total"] == 1
    assert [item["title"] for item in near["upcoming"]] == ["五天后开始"]
    assert near["upcoming"][0]["days_to_start"] in (4, 5)
    # 只剩 3 天的那条要出现在「即将结束」，还剩 40 天的不算
    assert near["ending_soon_total"] == 1
    assert [item["title"] for item in near["ending_soon"]] == ["快结束"]
    assert near["ending_soon"][0]["days_left"] in (2, 3)
    assert near["ending_soon"][0]["server_label"] == "简中服"
    # 精简条目不带内部 datetime，面板不用自己解析时间
    assert not any(key.endswith("_dt") for key in near["ending_soon"][0])


def test_near_term_is_empty_when_nothing_is_close(db: Database, tmp_path: Path) -> None:
    now = datetime.now(UTC)

    def stamp(days: float) -> str:
        return (now + timedelta(days=days)).strftime("%Y/%m/%d %H:%M")

    options = EventsSettings(servers="cn", days_before=60, days_after=60, near_days=7)
    payloads = {
        "简中赛马娘卡池": ask_payload(
            [{"title": "还早", "开始描述": stamp(-2), "结束描述": stamp(30)}]
        ),
        "活动": ask_payload([]),
    }
    service = build_service(db, options, tmp_path, payloads)
    service.refresh(["cn-character-pool"])

    near = service.schedule()["near_term"]

    assert near["upcoming_total"] == 0
    assert near["ending_soon_total"] == 0
    assert near["upcoming"] == []
    assert near["ending_soon"] == []


def test_schedule_hides_placeholders_by_default(db: Database, tmp_path: Path) -> None:
    """来源里的占位行不该出现在时间轴与近期提醒里，但要报出隐藏条数。"""
    options = EventsSettings(servers="cn", days_before=60, days_after=60)
    payloads = {
        "简中赛马娘卡池": ask_payload(
            [
                {
                    "title": "真实卡池",
                    "开始描述": "2026/10/20 12:00",
                    "结束描述": "2026/11/03 11:59",
                },
                {
                    "title": "无马娘卡池，占位用 20261020",
                    "开始描述": "2026/10/20 12:00",
                    "结束描述": "2026/11/03 11:59",
                },
            ]
        ),
        "活动": ask_payload([]),
    }
    service = build_service(db, options, tmp_path, payloads)
    service.refresh(["cn-character-pool"])

    default = service.schedule()
    assert [item["title"] for item in default["items"]] == ["真实卡池"]
    assert default["counts"]["placeholders"] == 1
    assert default["counts"]["active"] == 1
    assert [item["title"] for item in default["near_term"]["ending_soon"]] == ["真实卡池"]
    assert default["include_placeholders"] is False

    with_placeholders = service.schedule(include_placeholders=True)
    assert {item["title"] for item in with_placeholders["items"]} == {
        "真实卡池",
        "无马娘卡池，占位用 20261020",
    }
    assert with_placeholders["counts"]["placeholders"] == 0
    assert with_placeholders["include_placeholders"] is True
    # 占位行本身要被标记，面板据此加「占位」角标
    marked = {item["title"]: item["placeholder"] for item in with_placeholders["items"]}
    assert marked["无马娘卡池，占位用 20261020"] is True
    assert marked["真实卡池"] is False


def test_schedule_only_has_cn_sources(db: Database, tmp_path: Path) -> None:
    """繁中 / 日服来源已下线：抓取列表与库内数据都只剩简中服。"""
    options = EventsSettings(servers="cn", days_before=60, days_after=60)
    payloads = {
        "简中赛马娘卡池": ask_payload(
            [{"title": "简中池", "开始描述": "2026/10/20 12:00", "结束描述": "2026/11/03 11:59"}]
        ),
        "简中支援卡卡池": ask_payload(
            [
                {
                    "title": "简中协助卡池",
                    "开始描述": "2026/10/20 12:00",
                    "结束描述": "2026/11/03 11:59",
                }
            ]
        ),
    }
    service = build_service(db, options, tmp_path, payloads)

    assert [source.key for source in service.active_sources()] == [
        "cn-character-pool",
        "cn-support-pool",
    ]

    service.refresh()
    everything = service.schedule()
    assert {item["title"] for item in everything["items"]} == {"简中池", "简中协助卡池"}
    assert {item["server"] for item in everything["items"]} == {"cn"}

    servers = {row["code"]: row for row in service.status()["servers"]}
    assert set(servers) == {"cn"}
    assert servers["cn"]["items"] == 2


def test_refresh_purges_removed_sources(db: Database, tmp_path: Path) -> None:
    """旧的繁中 / 日服行在配置下线后应从库里清掉，不再出现在日程里。"""
    store.ensure_schema(db)
    stamp = store.now_iso()
    store.upsert_items(
        db,
        [
            {
                "uid": "jp-event:旧活动",
                "source_key": "jp-event",
                "kind": "event",
                "server": "jp",
                "title": "旧活动",
                "starts_at": None,
                "ends_at": None,
                "category": None,
                "payload": "{}",
                "source_level": "C",
                "source_url": "",
                "permanent": 0,
            }
        ],
        stamp,
    )
    assert store.load_items(db, "jp-event")

    options = EventsSettings(servers="cn", days_before=60, days_after=60)
    service = build_service(db, options, tmp_path, {"简中赛马娘卡池": ask_payload([])})
    service.refresh()

    assert store.load_items(db, "jp-event") == {}


def test_describe_formats_in_system_timezone() -> None:
    from datetime import UTC, datetime

    row = {
        "uid": "cn-character-pool:今天",
        "source_key": "cn-character-pool",
        "kind": "pool",
        "server": "cn",
        "title": "今天",
        "starts_at": "2026-10-26T12:00+08:00",
        "ends_at": "2026-11-05T11:59+08:00",
        "category": None,
        "ids": {},
        "source_level": "B",
        "source_url": "",
        "fetched_at": "2026-10-26T14:00:00+00:00",
        "first_seen_at": "2026-10-26T14:00:00+00:00",
        "updated_at": "2026-10-26T14:00:00+00:00",
        "removed_at": None,
    }
    # 2026-10-26 是周一（UTC 04:00 = UTC+8 12:00）；显式传系统时区，避免依赖跑测试的机器
    described = describe(row, datetime(2026, 10, 26, 4, 0, tzinfo=UTC), local_zone=sources.CST)

    assert described["state"] == "active"
    assert described["starts_today"] is True
    assert described["this_week"] is True
    assert described["starts_local"] == "2026-10-26 12:00"
    assert described["zone"] == "UTC+08:00"
    assert described["source_zone"] == "CST"
    # 简中服就是 UTC+8，与本机一致时不需要重复提示来源时区
    assert described["same_zone"] is True


def test_describe_converts_other_zone_start_to_system_zone() -> None:
    """来源时区与系统时区不同时，展示按系统时区换算，原始时间另存一份供面板说明。"""
    from datetime import UTC, datetime

    row = {
        "uid": "cn-character-pool:赤骥",
        "source_key": "cn-character-pool",
        "kind": "pool",
        "server": "cn",
        "title": "3★大和赤骥",
        "starts_at": "2026-10-26T12:00+08:00",
        "ends_at": "2026-11-19T11:59+08:00",
        "category": None,
        "ids": {},
        "source_level": "B",
        "source_url": "",
        "fetched_at": "2026-10-26T14:00:00+00:00",
        "first_seen_at": "2026-10-26T14:00:00+00:00",
        "updated_at": "2026-10-26T14:00:00+00:00",
        "removed_at": None,
    }
    # 模拟系统时区是 UTC 的机器：简中服 12:00 应显示成 04:00
    described = describe(row, datetime(2026, 10, 26, 4, 0, tzinfo=UTC), local_zone=UTC)

    assert described["starts_local"] == "2026-10-26 04:00"
    assert described["ends_local"] == "2026-11-19 03:59"
    assert described["starts_source"] == "2026-10-26 12:00"
    assert described["source_zone"] == "CST"
    assert described["zone"] == "UTC"
    assert described["same_zone"] is False


def test_zone_label_is_stable_text() -> None:
    from datetime import UTC, timedelta, timezone

    assert service_zone_label(UTC) == "UTC"
    assert service_zone_label(timezone(timedelta(hours=8))) == "UTC+08:00"
    assert service_zone_label(timezone(timedelta(hours=5, minutes=30))) == "UTC+05:30"
    assert service_zone_label(timezone(timedelta(hours=-3))) == "UTC-03:00"


def test_permanent_event_stays_active_without_dates(
    db: Database, options: EventsSettings, tmp_path: Path
) -> None:
    payloads = {
        "简中赛马娘卡池": ask_payload(
            [{"title": "优俊少女招募", "开始描述": "常驻", "结束描述": "常驻"}]
        )
    }
    service = build_service(db, options, tmp_path, payloads)
    service.refresh(["cn-character-pool"])

    schedule = service.schedule(days_before=1, days_after=1)

    assert [item["state"] for item in schedule["items"]] == ["permanent"]
    assert schedule["items"][0]["starts_local"] is None


def test_diff_ignores_unchanged_rows() -> None:
    source = sources.SOURCE_BY_KEY["cn-character-pool"]
    item = sources.TimelineItem(
        uid="cn-character-pool:X",
        source_key=source.key,
        kind="pool",
        server="cn",
        title="X",
        starts_at=None,
        ends_at=None,
        category=None,
        ids={},
        source_level="B",
        source_url="https://example.invalid/x",
    )
    existing = {
        item.uid: {
            "uid": item.uid,
            "server": "cn",
            "title": "X",
            "starts_at": None,
            "ends_at": None,
            "category": None,
            "payload": json.dumps({}, sort_keys=True),
            "source_level": "B",
            "removed_at": None,
        }
    }
    plan = diff_items(source, existing, [item])

    assert plan["changes"] == []
    assert plan["added"] == 0
    assert plan["removed"] == []


# -------------------------------------------------------------------- 接口
def test_events_api_serves_schedule(settings: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/events/status").json()
        schedule = client.get("/api/events/schedule").json()

    assert {source["server"] for source in status["sources"]} == {"cn"}
    assert schedule["items"] == []
    assert schedule["counts"]["active"] == 0


def test_events_module_registered(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        modules = client.get("/api/system/modules").json()

    by_id = {module["id"]: module for module in modules}
    assert by_id["events"]["status"] == "ok"
    assert by_id["events"]["ui"]["panel"] == "/module-assets/events/panel.js"
