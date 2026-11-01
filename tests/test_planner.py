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
DAY = 86400


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
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get(self, query: str, *, force: bool = False) -> FetchResult:
        self.calls.append(query)
        return FetchResult(
            payload=self.payload,
            url="https://wiki.biligame.com/umamusume/简中卡池",
            fetched_at="2026-11-01T00:00:00+00:00",
        )


def build_mdb(path: Path) -> Path:
    """最小 master.mdb：两个未来池（拉长 horizon）、常驻 / 已结束 / 未定档各一个、两条剧情活动。"""
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
        CREATE TABLE story_event_data (
            story_event_id INTEGER PRIMARY KEY,
            announce_id INTEGER,
            consume_tp_ratio INTEGER,
            bonus_reward_ratio INTEGER,
            notice_date INTEGER,
            start_date INTEGER,
            middle_date_01 INTEGER,
            middle_date_02 INTEGER,
            ending_date INTEGER,
            end_date INTEGER
        );
        """
    )
    now = int(datetime.now().timestamp())
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        [
            (26, 26, 9001, "精选 优俊少女招募"),
            (26, 26, 9002, "精选 优俊少女招募"),
            (26, 26, 9003, "3★必得新人出道招募"),
            (26, 26, 9004, "精选 优俊少女招募"),
            (26, 26, 9005, "3★ WISH&GAZE招募"),
            (214, 214, 5001, "测试剧情活动·上"),
            (214, 214, 5002, "测试剧情活动·下"),
        ],
    )
    conn.executemany(
        "INSERT INTO gacha_data (id, type, card_type, cost_single, start_date, end_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            # 拉长 horizon 用的长池
            (9001, 3, 1, 150, now + 5 * DAY, now + 100 * DAY),
            (9002, 3, 1, 150, now + 60 * DAY, now + 120 * DAY),
            (9003, 2, 1, 0, now - 1500 * DAY, 2_520_000_000),
            (9004, 3, 2, 150, now - 20 * DAY, now - 5 * DAY),
            (9005, 3, 1, 150, 2_530_000_000, 2_530_600_000),
        ],
    )
    conn.executemany(
        "INSERT INTO gacha_exchange (gacha_id, card_id, card_type, pay_item_num, disp_order) "
        "VALUES (?, ?, ?, ?, ?)",
        [(9001, 2001, 1, 200, 1), (9002, 2002, 1, 200, 1)],
    )
    conn.executemany(
        "INSERT INTO gacha_available (gacha_id, card_id, card_type, rarity, is_pickup, "
        "recommend_order) VALUES (?, ?, ?, ?, ?, ?)",
        [(9001, 2001, 1, 3, 1, 1), (9002, 2002, 1, 3, 1, 1)],
    )
    conn.executemany(
        "INSERT INTO story_event_data (story_event_id, start_date, end_date) VALUES (?, ?, ?)",
        [
            (5001, now - 5 * DAY, now + 5 * DAY),
            (5002, now + 10 * DAY, now + 20 * DAY),
        ],
    )
    conn.commit()
    conn.close()
    return path


def pools_payload() -> dict[str, Any]:
    """一条与 mdb 重叠（用于算偏差）、一条落在 horizon 之后（预测段）。"""

    def row(title: str, days: float, card: str) -> dict[str, Any]:
        return {
            "title": title,
            "开始描述": _fmt(_cn(days)),
            "简中开始时间": _fmt(_cn(days)),
            "简中结束时间": _fmt(_cn(days + 10)),
            "角色ID": [card],
        }

    return ask_payload(
        [
            # 9001 的精选卡是 2001、mdb 开在 +5 天；wiki 说 +49 天 → 偏晚 44 天
            row("重叠池 UP卡池", 49, "2001"),
            # horizon（+120 天）之后
            row("预测池 UP卡池", 150, "2003"),
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
        fetcher=FakeFetcher(pools_payload()),
    )


# ------------------------------------------------------------------ sources
def test_parse_pools_skips_rows_without_cn_time() -> None:
    payload = ask_payload(
        [
            {"title": "有简中时间", "简中开始时间": "2026/09/01 12:00", "角色ID": ["108801"]},
            {"title": "只有日服时间", "开始描述": "2023/08/24 12:00"},
        ]
    )

    pools = sources.parse_pools(payload)

    assert [pool.title for pool in pools] == ["有简中时间"]
    assert pools[0].char_ids == ["108801"]


def test_parse_pools_splits_char_and_support_ids() -> None:
    payload = ask_payload(
        [
            {
                "title": "双卡池",
                "简中开始时间": "2026/09/01 12:00",
                "角色ID": "107701",
                "支援卡ID": "30160, 30161",
            }
        ]
    )

    pool = sources.parse_pools(payload)[0]
    assert pool.char_ids == ["107701"]
    assert pool.support_ids == ["30160", "30161"]


# ----------------------------------------------------------------- importer
def test_import_confirmed_reads_pools_and_story_events(db: Database, mdb: Path) -> None:
    summary = read_confirmed(db, mdb, default_cost_single=150, default_ceiling_pulls=200)

    assert summary.pools == 2
    assert summary.future == 2
    assert summary.events == 2
    assert summary.skipped["permanent"] == "常驻池 1 个"
    assert summary.skipped["ended"] == "已结束 1 个"
    assert summary.skipped["unscheduled"] == "未定档占位 1 个"

    pools = store.list_pools(db, segment="confirmed")
    assert {row["uid"] for row in pools} == {"mdb:9001", "mdb:9002"}
    assert pools[0]["cost_single"] == 150
    assert pools[0]["ceiling_pulls"] == 200
    assert pools[0]["source_level"] == "A"
    assert '"2001"' in pools[0]["ids"]

    events = {row["title"]: row for row in store.list_events(db)}
    assert set(events) == {"测试剧情活动·上", "测试剧情活动·下"}
    assert events["测试剧情活动·上"]["kind"] == "剧情活动"
    assert events["测试剧情活动·上"]["source_level"] == "A"


def test_story_events_missing_is_not_fatal(db: Database, settings: Settings) -> None:
    path = settings.mdb_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE text_data (id INTEGER, category INTEGER, "index" INTEGER, text TEXT);
        CREATE TABLE gacha_data (
            id INTEGER PRIMARY KEY, type INTEGER, card_type INTEGER,
            cost_single INTEGER, start_date INTEGER, end_date INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO gacha_data VALUES (1, 3, 1, 150, ?, ?)",
        (int(datetime.now().timestamp()), int(datetime.now().timestamp()) + 10 * DAY),
    )
    conn.commit()
    conn.close()

    summary = read_confirmed(db, path, default_cost_single=150, default_ceiling_pulls=200)

    assert summary.pools == 1
    assert summary.events == 0


def test_import_confirmed_rejects_missing_tables(db: Database, settings: Settings) -> None:
    bad = settings.mdb_path.parent / "bad.mdb"
    bad.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(bad).close()

    with pytest.raises(MdbSourceError):
        read_confirmed(db, bad, default_cost_single=150, default_ceiling_pulls=200)


# ------------------------------------------------------------- calibration
def test_calibration_measures_wiki_lateness(service: PlannerService) -> None:
    service.refresh(force=True)

    calibration = service.status()["calibration"]

    assert calibration["pairs"] == 1
    assert calibration["bias_days"] == pytest.approx(44, abs=0.1)
    assert calibration["min_days"] == pytest.approx(44, abs=0.1)
    assert calibration["samples"][0]["actual_title"] == "精选 优俊少女招募"


def test_predicted_pool_is_shifted_by_measured_bias(service: PlannerService) -> None:
    service.refresh(force=True)

    data = service.forecast(days=365)
    predicted = next(item for item in data["pools"] if item["segment"] == "predicted")

    assert predicted["correction_days"] == 44
    raw = datetime.fromisoformat(predicted["source_starts_local"])
    shifted = datetime.fromisoformat(predicted["starts_local"])
    assert (raw - shifted).days == 44


def test_forecast_hides_overlap_by_default(service: PlannerService) -> None:
    service.refresh(force=True)

    default = service.forecast(days=365)
    with_overlap = service.forecast(days=365, include_overlap=True)

    assert all(item["segment"] != "overlap" for item in default["pools"])
    assert any(item["segment"] == "overlap" for item in with_overlap["pools"])


def test_forecast_window_filters_far_pools(service: PlannerService) -> None:
    service.refresh(force=True)

    short = service.forecast(days=30)

    assert all(
        item["days_to_start"] <= 30 for item in short["pools"] if item["state"] == "upcoming"
    )
    assert all(item["segment"] == "confirmed" for item in short["pools"])


def test_forecast_lists_mdb_story_events(service: PlannerService) -> None:
    service.refresh(force=True)

    data = service.forecast(days=180)

    titles = [item["title"] for item in data["events"]]
    assert titles == ["测试剧情活动·上", "测试剧情活动·下"]
    assert all(item["source_level"] == "A" for item in data["events"])


def test_forecast_reports_costs(service: PlannerService) -> None:
    service.refresh(force=True)

    data = service.forecast(days=180)

    assert data["cost"] == {"cost_single": 150, "ceiling_pulls": 200, "ceiling_carats": 30000}
    assert data["horizon"]["confirmed_until_local"]
    assert any("简中服加速" in note for note in data["notes"])


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
            fetcher=FakeFetcher(pools_payload()),
        )
        result = service.refresh(force=True)

        assert "mdb_error" in result
        assert result["counts"]["confirmed"] == 0
        assert result["counts"]["predicted"] == 2, "没有 mdb 就没有 horizon，两行都算预测"
        assert result["calibration"]["pairs"] == 0
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


def test_evaluate_reports_shortfall(service: PlannerService) -> None:
    service.refresh(force=True)

    result = service.evaluate(_plan(service, carats=0, income=3000, uid="wiki:预测池 UP卡池"))

    row = result["rows"][0]
    assert row["cost_carats"] == 30000
    assert row["affordable"] is False
    assert row["shortfall_carats"] > 15000
    assert result["totals"]["shortfall_carats"] == row["shortfall_carats"]


def test_evaluate_marks_affordable(service: PlannerService) -> None:
    service.refresh(force=True)

    result = service.evaluate(_plan(service, carats=30000, income=0, uid="wiki:预测池 UP卡池"))

    row = result["rows"][0]
    assert row["affordable"] is True
    assert row["shortfall_carats"] == 0
    assert result["totals"]["spent_carats"] == 30000


def test_evaluate_uses_tickets_before_carats(service: PlannerService) -> None:
    service.refresh(force=True)
    plan = _plan(service, carats=30000, income=0, uid="wiki:预测池 UP卡池")
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
        assert status["mdb"]["events"] == 2

        forecast = client.get("/api/planner/forecast").json()
        assert "pools" in forecast and "events" in forecast
        assert "calibration" in forecast
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
