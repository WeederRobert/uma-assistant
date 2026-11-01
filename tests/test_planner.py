from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.planner import sources, store
from modules.planner.fetcher import FetchResult
from modules.planner.importer import MdbSourceError, read_confirmed
from modules.planner.service import PlannerService
from modules.planner.settings import PlannerSettings

CST = timezone(timedelta(hours=8))
OFFSET_DAYS = 1104


def _cn(days_from_now: float) -> datetime:
    return datetime.now(CST) + timedelta(days=days_from_now)


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y/%m/%d %H:%M")


def ask_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按 ``action=ask`` 的真实结构拼响应。"""
    results: dict[str, Any] = {}
    for row in rows:
        title = row["title"]
        printouts: dict[str, list[str]] = {}
        for field, value in row.items():
            if field == "title":
                continue
            printouts[field] = value if isinstance(value, list) else [value]
        results[title] = {
            "printouts": printouts,
            "fulltext": title,
            "fullurl": f"https://wiki.biligame.com/umamusume/{title}",
        }
    return {"query": {"results": results}}


class FakeFetcher:
    def __init__(self, pools: dict[str, Any], events: dict[str, Any]) -> None:
        self.pools = pools
        self.events = events
        self.calls: list[str] = []

    def get(self, query: str, *, force: bool = False) -> FetchResult:
        self.calls.append(query)
        payload = self.pools if "简中::未实装" in query else self.events
        return FetchResult(
            payload=payload,
            url="https://wiki.biligame.com/umamusume/简中卡池",
            fetched_at="2026-11-01T00:00:00+00:00",
        )


def build_mdb(path: Path) -> Path:
    """最小 master.mdb：一个进行中池、常驻池、已结束池、未定档占位。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE text_data (
            id INTEGER NOT NULL,
            category INTEGER NOT NULL,
            "index" INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (category, "index")
        );
        CREATE TABLE gacha_data (
            id INTEGER PRIMARY KEY,
            type INTEGER NOT NULL,
            card_type INTEGER NOT NULL,
            cost_single INTEGER,
            start_date INTEGER NOT NULL,
            end_date INTEGER NOT NULL
        );
        CREATE TABLE gacha_available (
            gacha_id INTEGER NOT NULL,
            card_id INTEGER NOT NULL,
            card_type INTEGER NOT NULL,
            rarity INTEGER,
            odds INTEGER,
            is_pickup INTEGER NOT NULL DEFAULT 0,
            recommend_order INTEGER,
            is_prize_selectable INTEGER
        );
        CREATE TABLE gacha_exchange (
            gacha_id INTEGER NOT NULL,
            card_id INTEGER NOT NULL,
            card_type INTEGER NOT NULL,
            pay_item_num INTEGER NOT NULL,
            disp_order INTEGER
        );
        CREATE TABLE card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL
        );
        CREATE TABLE support_card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL
        );
        """
    )
    now = int(datetime.now().timestamp())
    day = 86400
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        [
            (26, 26, 9001, "精选 优俊少女招募"),
            (26, 26, 9002, "3★必得新人出道招募"),
            (26, 26, 9003, "精选 协助卡招募"),
            (26, 26, 9004, "3★ WISH&GAZE招募"),
            (6, 6, 1001, "里见皇冠"),
        ],
    )
    conn.executemany(
        "INSERT INTO gacha_data (id, type, card_type, cost_single, start_date, end_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (9001, 3, 1, 150, now + 5 * day, now + 15 * day),
            (9002, 2, 1, 0, now - 1500 * day, 2_520_000_000),
            (9003, 3, 2, 150, now - 20 * day, now - 5 * day),
            (9004, 3, 1, 150, 2_530_000_000, 2_530_600_000),
        ],
    )
    conn.execute(
        "INSERT INTO gacha_exchange (gacha_id, card_id, card_type, pay_item_num, disp_order) "
        "VALUES (9001, 2001, 1, 200, 1)"
    )
    conn.execute("INSERT INTO card_data (id, chara_id) VALUES (2001, 1001)")
    conn.execute(
        "INSERT INTO gacha_available (gacha_id, card_id, card_type, rarity, is_pickup, "
        "recommend_order) VALUES (9001, 2001, 1, 3, 1, 1)"
    )
    conn.commit()
    conn.close()
    return path


def pools_payload() -> dict[str, Any]:
    def row(title: str, days: float, **extra: Any) -> dict[str, Any]:
        cn_start = _cn(days)
        jp_start = cn_start - timedelta(days=OFFSET_DAYS)
        return {
            "title": title,
            "开始描述": _fmt(jp_start),
            "简中开始时间": _fmt(cn_start),
            "简中结束时间": _fmt(cn_start + timedelta(days=10)),
            **extra,
        }

    return ask_payload(
        [
            row("「测试活动」活动卡池", 40, 角色ID=["108801"], 支援卡ID=["30195"]),
            row("里见皇冠 UP卡池", 30, 角色ID=["108801"]),
            row("早期预测池", 5, 角色ID=["106703"]),
        ]
    )


def events_payload() -> dict[str, Any]:
    def row(title: str, days: float, kind: str = "剧情活动") -> dict[str, Any]:
        jp_start = _cn(days) - timedelta(days=OFFSET_DAYS)
        return {
            "title": title,
            "名称": title,
            "开始描述": _fmt(jp_start),
            "结束描述": _fmt(jp_start + timedelta(days=12)),
            "类型": kind,
        }

    return ask_payload(
        [
            row("剧情活动「测试活动」", 38),
            row("剧情活动「无对应卡池」", 50),
            {
                "title": "全国公演",
                "名称": "全国公演",
                "开始描述": "常驻",
                "结束描述": "常驻",
                "类型": "育成活动",
            },
        ]
    )


@pytest.fixture()
def options() -> PlannerSettings:
    return PlannerSettings(interval_seconds=0.0, retries=1, backoff_seconds=0.0)


@pytest.fixture()
def db(settings: Settings):
    database = Database(settings.db_path)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture()
def mdb(settings: Settings) -> Path:
    return build_mdb(settings.mdb_path)


@pytest.fixture()
def service(db: Database, settings: Settings, options: PlannerSettings, mdb: Path, tmp_path: Path):
    return PlannerService(
        db,
        settings,
        options,
        tmp_path / "planner",
        fetcher=FakeFetcher(pools_payload(), events_payload()),
    )


# ------------------------------------------------------------------ sources
def test_parse_pools_skips_rows_without_cn_time() -> None:
    payload = ask_payload(
        [
            {
                "title": "有简中时间",
                "简中开始时间": "2026/09/01 12:00",
                "开始描述": "2023/08/24 12:00",
            },
            {"title": "只有日服时间", "开始描述": "2023/08/24 12:00"},
        ]
    )

    pools = sources.parse_pools(payload)

    assert [pool.title for pool in pools] == ["有简中时间"]
    assert pools[0].delta_days == pytest.approx(OFFSET_DAYS, abs=0.01)


def test_parse_events_keeps_kind_and_skips_permanent() -> None:
    events = sources.parse_events(events_payload())

    assert len(events) == 3
    permanent = next(item for item in events if item.title == "全国公演")
    assert permanent.jp_start is None
    assert events[0].kind == "剧情活动"


def test_offset_map_interpolates_between_points() -> None:
    day = timedelta(days=1)
    base = datetime(2023, 8, 24, 12, tzinfo=CST)
    pools = [
        sources.PredictedPool("a", base, base + 2104 * day, None, {}, ""),
        sources.PredictedPool("b", base + 400 * day, base + 2504 * day, None, {}, ""),
    ]

    offset = sources.OffsetMap.build(pools)

    assert offset.known
    assert offset.median_days == pytest.approx(2104)
    assert offset.delta_days(base + 200 * day) == pytest.approx(2104)
    assert offset.delta_days(base - 10 * day) == pytest.approx(2104)
    assert offset.spread_days == (2104, 2104)


def test_offset_map_without_pairs_is_unknown() -> None:
    offset = sources.OffsetMap.build([sources.PredictedPool("a", None, _cn(3), None, {}, "")])

    assert not offset.known
    with pytest.raises(ValueError):
        offset.delta_days(_cn(3))


def test_match_event_pool_uses_bracketed_title() -> None:
    pool = sources.PredictedPool(
        "「寂寥秋色，连缀思念」活动卡池", _cn(-100), _cn(10), _cn(20), {}, ""
    )

    assert sources.match_event_pool("剧情活动「寂寥秋色，连缀思念」", [pool]) is pool
    assert sources.match_event_pool("剧情活动「没有这个」", [pool]) is None


# ----------------------------------------------------------------- importer
def test_import_confirmed_skips_permanent_ended_and_unscheduled(db: Database, mdb: Path) -> None:
    summary = read_confirmed(db, mdb, default_cost_single=150, default_ceiling_pulls=200)

    assert summary.pools == 1
    assert summary.future == 1
    assert summary.skipped["permanent"] == "常驻池 1 个"
    assert summary.skipped["ended"] == "已结束 1 个"
    assert summary.skipped["unscheduled"] == "未定档占位 1 个"

    rows = store.list_pools(db, segment="confirmed")
    assert rows[0]["uid"] == "mdb:9001"
    assert rows[0]["cost_single"] == 150
    assert rows[0]["ceiling_pulls"] == 200
    assert rows[0]["pickups"] == "里见皇冠"
    assert rows[0]["source_level"] == "A"


def test_import_confirmed_rejects_missing_tables(db: Database, settings: Settings) -> None:
    bad = settings.mdb_path.parent / "bad.mdb"
    bad.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(bad).close()

    with pytest.raises(MdbSourceError):
        read_confirmed(db, bad, default_cost_single=150, default_ceiling_pulls=200)


# ------------------------------------------------------------------ refresh
def test_refresh_splits_segments_and_writes_events(service: PlannerService) -> None:
    result = service.refresh(force=True)

    counts = result["counts"]
    assert counts["confirmed"] == 1
    assert counts["predicted"] == 2
    assert counts["overlap"] == 1
    assert counts["events"] == 2

    pools = {row["title"]: row for row in store.list_pools(service.db)}
    assert pools["早期预测池"]["segment"] == "overlap"
    assert pools["里见皇冠 UP卡池"]["segment"] == "predicted"
    assert pools["里见皇冠 UP卡池"]["cost_single"] == 150
    assert pools["里见皇冠 UP卡池"]["ceiling_pulls"] == 200
    assert pools["里见皇冠 UP卡池"]["source_level"] == "C"


def test_refresh_marks_matched_event_with_pool_time(service: PlannerService) -> None:
    service.refresh(force=True)

    events = {row["title"]: row for row in store.list_events(service.db)}
    matched = events["剧情活动「测试活动」"]
    assert matched["basis"] == "活动卡池"
    assert matched["matched_uid"] == "wiki:「测试活动」活动卡池"

    inferred = events["剧情活动「无对应卡池」"]
    assert inferred["basis"] == "偏移推算"
    assert inferred["offset_days"] == OFFSET_DAYS


def test_forecast_reports_window_offset_and_costs(service: PlannerService) -> None:
    service.refresh(force=True)

    data = service.forecast(days=180)

    assert data["window"]["days"] == 180
    assert data["horizon"]["confirmed_until_local"]
    assert data["offset"]["median_days"] == pytest.approx(OFFSET_DAYS, abs=0.01)
    assert data["cost"] == {"cost_single": 150, "ceiling_pulls": 200, "ceiling_carats": 30000}
    assert any(pool["segment"] == "predicted" for pool in data["pools"])
    assert all(pool["starts_local"] for pool in data["pools"])


def test_forecast_can_hide_overlap(service: PlannerService) -> None:
    service.refresh(force=True)

    data = service.forecast(days=180, include_overlap=False)

    assert all(pool["segment"] != "overlap" for pool in data["pools"])


def test_forecast_respects_window(service: PlannerService) -> None:
    service.refresh(force=True)

    short = service.forecast(days=20)

    assert all(pool["segment"] != "predicted" for pool in short["pools"]), (
        "20 天窗口里没有 30 天后的预测池"
    )
    assert all(
        pool["days_to_start"] <= 20 for pool in short["pools"] if pool["state"] == "upcoming"
    )


def test_refresh_reports_missing_mdb(
    settings: Settings, options: PlannerSettings, tmp_path: Path
) -> None:
    database = Database(settings.db_path)
    try:
        service = PlannerService(
            database,
            settings,
            options,
            tmp_path / "planner",
            fetcher=FakeFetcher(pools_payload(), events_payload()),
        )
        result = service.refresh(force=True)

        assert "mdb_error" in result
        assert result["counts"]["confirmed"] == 0
        assert result["counts"]["predicted"] == 3, "没有 mdb 就没有已确认段，三行都算预测"
    finally:
        database.close()


# ----------------------------------------------------------------- evaluate
def _plan(service: PlannerService, carats: int, income: int, uid: str) -> dict[str, Any]:
    return {
        "name": "测试方案",
        "carats": carats,
        "tickets": 0,
        "monthly_income": income,
        "targets": [{"pool_uid": uid, "pulls": 200, "priority": 1}],
    }


def test_evaluate_reports_shortfall_when_carats_are_not_enough(service: PlannerService) -> None:
    service.refresh(force=True)
    uid = "wiki:「测试活动」活动卡池"

    result = service.evaluate(_plan(service, carats=0, income=9000, uid=uid))

    row = result["rows"][0]
    assert row["cost_carats"] == 30000
    assert row["affordable"] is False
    assert row["shortfall_carats"] == pytest.approx(18000, abs=50)
    assert result["totals"]["shortfall_carats"] == row["shortfall_carats"]


def test_evaluate_marks_affordable_and_deducts(service: PlannerService) -> None:
    service.refresh(force=True)
    uid = "wiki:「测试活动」活动卡池"

    result = service.evaluate(_plan(service, carats=30000, income=0, uid=uid))

    row = result["rows"][0]
    assert row["affordable"] is True
    assert row["shortfall_carats"] == 0
    assert row["balance_after"] == 0
    assert result["totals"]["spent_carats"] == 30000


def test_evaluate_uses_tickets_before_carats(service: PlannerService) -> None:
    service.refresh(force=True)
    uid = "wiki:「测试活动」活动卡池"
    plan = _plan(service, carats=30000, income=0, uid=uid)
    plan["tickets"] = 20

    result = service.evaluate(plan)

    row = result["rows"][0]
    assert row["tickets_used"] == 20
    assert row["cost_carats"] == 180 * 150


# ---------------------------------------------------------------------- api
def test_api_forecast_and_plan_crud(db: Database, settings: Settings, mdb: Path) -> None:
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/planner/status").json()
        assert status["window"]["days"] == 180
        assert status["cost"]["ceiling_carats"] == 30000
        assert status["mdb"]["exists"] is True

        forecast = client.get("/api/planner/forecast").json()
        assert "pools" in forecast and "events" in forecast
        assert forecast["notes"]

        created = client.post(
            "/api/planner/plans",
            json={
                "name": "攒萝卜",
                "carats": 12000,
                "tickets": 5,
                "monthly_income": 6000,
                "targets": [],
            },
        ).json()
        plan_id = created["id"]

        detail = client.get(f"/api/planner/plans/{plan_id}").json()
        assert detail["plan"]["name"] == "攒萝卜"
        assert detail["evaluation"]["totals"]["targets"] == 0

        updated = client.put(
            f"/api/planner/plans/{plan_id}",
            json={"name": "改过名", "carats": 1, "tickets": 0, "monthly_income": 0, "targets": []},
        )
        assert updated.status_code == 200
        assert client.get(f"/api/planner/plans/{plan_id}").json()["plan"]["name"] == "改过名"

        assert client.delete(f"/api/planner/plans/{plan_id}").json() == {"deleted": True}
        assert client.get(f"/api/planner/plans/{plan_id}").status_code == 404
        assert client.delete(f"/api/planner/plans/{plan_id}").status_code == 404
        assert client.put("/api/planner/plans/404404", json={"name": "x"}).status_code == 404
