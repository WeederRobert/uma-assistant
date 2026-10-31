from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.circles.client import CirclesSourceError
from modules.circles.service import CirclesNotFound, CirclesService, format_fans
from modules.circles.settings import CirclesSettings

CIRCLES = [
    {
        "circle_id": 100000001,
        "circle_name": "甲社",
        "member_count": 2,
        "total_fans": 10_000_000_000,
        "month_delta": 1_050_000_000,
        "today_delta": 100_000_000,
        "ranking_rank": 1,
        "capture_interval_minutes": 60.0,
        "month_baseline_at": "2026-10-01T05:00:00+08:00",
        "latest_at": "2026-10-31T23:05:00+08:00",
    },
    {
        "circle_id": 200000002,
        "circle_name": "乙社",
        "member_count": 1,
        "total_fans": 5_000_000_000,
        "month_delta": 20_000_000,
        "today_delta": 300_000_000,
        "ranking_rank": 9,
        "capture_interval_minutes": 60.0,
        "month_baseline_at": "2026-10-01T05:00:00+08:00",
        "latest_at": "2026-10-31T23:05:00+08:00",
    },
]

DETAIL = {
    100000001: {
        "circle_id": 100000001,
        "members": [
            {
                "viewer_id": 1,
                "member_name": "小甲",
                "fan": 900_000_000,
                "month_fan": 750_000_000,
                "today_delta": 10_000_000,
                "daily_avg": 24_000_000.0,
                "month_training_count": 100,
                "single_mode_play_count": 200,
                "join_time": "2026-03-01 10:13:15",
                "last_login_time": "2026-10-31 21:42:43",
            },
            {
                "viewer_id": 2,
                "member_name": "小乙",
                "fan": 100_000_000,
                "month_fan": 250_000_000,
                "today_delta": 5_000_000,
                "daily_avg": 8_000_000.0,
                "month_training_count": 50,
                "single_mode_play_count": 80,
                "join_time": "2026-04-01 10:00:00",
                "last_login_time": "2026-10-31 20:00:00",
            },
        ],
    }
}


class FakeClient:
    def __init__(self, circles: list[dict[str, Any]] | None = None) -> None:
        self.circles = circles if circles is not None else CIRCLES
        self.detail_calls = 0
        self.fail_detail = False

    def fetch_list(self) -> list[dict[str, Any]]:
        return self.circles

    def fetch_circle(self, circle_id: int) -> dict[str, Any]:
        self.detail_calls += 1
        if self.fail_detail:
            raise CirclesSourceError("社团没有被 umamusume.space 收录（测试）")
        return DETAIL[circle_id]


@pytest.fixture()
def service(settings: Settings) -> CirclesService:
    db = Database(settings.db_path)
    return CirclesService(db, CirclesSettings(), client=FakeClient())


def test_refresh_stores_circles(service: CirclesService) -> None:
    result = service.refresh()
    assert result["fetched"] == 2
    assert result["month_baseline_at"] == "2026-10-01T05:00:00+08:00"

    status = service.status()
    assert status["circle_count"] == 2
    assert status["member_rows"] == 0
    assert status["experimental"] is True


def test_list_sorts_by_month_and_filters(service: CirclesService) -> None:
    service.refresh()
    listing = service.list_circles()
    assert [item["name"] for item in listing["items"]] == ["甲社", "乙社"]
    assert listing["items"][0]["month_fans_text"] == "10.50 亿"

    by_today = service.list_circles(sort="today")
    assert by_today["items"][0]["name"] == "乙社"

    filtered = service.list_circles(query="乙")
    assert filtered["count"] == 1
    assert filtered["items"][0]["circle_id"] == 200000002

    by_id = service.list_circles(query="100000001")
    assert by_id["count"] == 1


def test_detail_computes_share_and_gap(service: CirclesService) -> None:
    service.refresh()
    detail = service.circle_detail(100000001)

    assert detail["circle"]["month_fans"] == 1_050_000_000
    assert detail["summary"]["member_month_fans"] == 1_000_000_000
    assert detail["summary"]["gap_fans"] == 50_000_000
    assert detail["summary"]["gap_percent"] == pytest.approx(4.76)
    assert detail["summary"]["contributor_count"] == 2
    assert detail["summary"]["top_member"] == "小甲"

    members = detail["members"]
    assert [member["member_name"] for member in members] == ["小甲", "小乙"]
    assert members[0]["share_percent"] == pytest.approx(75.0)
    assert members[1]["share_percent"] == pytest.approx(25.0)
    assert members[0]["month_fan_text"] == "7.50 亿"
    assert detail["stale"] is False


def test_detail_is_cached_and_refresh_forces_fetch(service: CirclesService) -> None:
    client = service.client
    assert isinstance(client, FakeClient)
    service.refresh()

    service.circle_detail(100000001)
    service.circle_detail(100000001)
    assert client.detail_calls == 1

    service.circle_detail(100000001, refresh=True)
    assert client.detail_calls == 2


def test_detail_unknown_circle(service: CirclesService) -> None:
    service.refresh()
    with pytest.raises(CirclesNotFound):
        service.circle_detail(999)


def test_detail_falls_back_to_cache_on_error(service: CirclesService) -> None:
    client = service.client
    assert isinstance(client, FakeClient)
    service.refresh()
    service.circle_detail(100000001)

    client.fail_detail = True
    detail = service.circle_detail(100000001, refresh=True)
    assert detail["stale"] is True
    assert detail["summary"]["member_count"] == 2
    assert any("上次缓存" in note for note in detail["notes"])


def test_detail_raises_without_cache(service: CirclesService) -> None:
    client = service.client
    assert isinstance(client, FakeClient)
    service.refresh()
    client.fail_detail = True
    with pytest.raises(CirclesSourceError):
        service.circle_detail(100000001)


def test_refresh_drops_untracked_circles(service: CirclesService) -> None:
    client = service.client
    assert isinstance(client, FakeClient)
    service.refresh()
    service.circle_detail(100000001)

    client.circles = [CIRCLES[1]]
    service.refresh()
    assert service.status()["circle_count"] == 1
    assert service.status()["member_rows"] == 0
    with pytest.raises(CirclesNotFound):
        service.circle_detail(100000001)


def test_format_fans() -> None:
    assert format_fans(None) == "—"
    assert format_fans(158_590_000_000) == "1585.90 亿"
    assert format_fans(30_503_000) == "3050.3 万"
    assert format_fans(999) == "999"
    assert format_fans(-15_000_000) == "-1500.0 万"


def test_api_status_list_and_404(settings: Settings) -> None:
    # 这个用例只走不联网的路径：status、空列表、未收录社团的 404
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/circles/status").json()
        assert status["ok"] is True
        assert status["experimental"] is True

        listing = client.get("/api/circles").json()
        assert listing["count"] == 0

        missing = client.get("/api/circles/123")
        assert missing.status_code == 404

        bad_sort = client.get("/api/circles", params={"sort": "nope"})
        assert bad_sort.status_code == 422
