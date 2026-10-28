from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.races import geometry, sources, store
from modules.races.importer import RacesSourceError, import_catalog
from modules.races.service import RacesService
from modules.races.settings import RacesSettings
from modules.races.wikidata import PageResult, WikiError

# 结构照抄 BWiki 数据页，避免测试里的解析逻辑与真实格式脱节
GEOMETRY_TEXT = json.dumps(
    [
        {
            "id": "10602",
            "lane": [
                {"type": "直线", "name": "直线", "start": 0, "end": 550, "length": 550},
                {"type": "弯道", "name": "弯道", "start": 550, "end": 825, "length": 275},
                {"type": "弯道", "name": "终弯", "start": 825, "end": 1100, "length": 275},
                {"type": "直线", "name": "终直", "start": 1100, "end": 1600, "length": 500},
            ],
            "slopes": [
                {"start": 328, "end": 400, "length": 72, "slope": 1},
                {"start": 450, "end": 571, "length": 121, "slope": -1},
            ],
        },
        {
            "id": "10501",
            "lane": [
                {"type": "弯道", "name": "弯道", "start": 300, "end": 650, "length": 350},
                {"type": "弯道", "name": "终弯", "start": 650, "end": 900, "length": 250},
                {"type": "直线", "name": "终直", "start": 900, "end": 1200, "length": 300},
            ],
            "slopes": [{"start": 989, "end": 1115, "length": 126, "slope": 1}],
        },
        {
            "id": "10301",
            "lane": [{"type": "直", "name": "直", "start": 0, "end": 999.9, "length": 999.9}],
            "slopes": [],
        },
        {
            "id": "10613",
            "lane": [
                {"type": "直线", "name": "直线", "start": 0, "end": 1900, "length": 1900},
                {"type": "弯道", "name": "弯道", "start": 2348, "end": 2848, "length": 500},
            ],
            "slopes": [],
        },
    ]
)


# 导入后赛事目录里应该出现的名称（2000 没有名称，导入时跳过）
CHAMPION_NAMES = {
    "群英联赛 金牛杯",
    "群英联赛 金牛杯 决赛",
    "群英联赛 双子杯",
    "群英联赛 双子杯 决赛",
}
CATALOG_NAMES = {"短途者锦标赛", "东京英里赛", "东京新闻杯", "东京大赏"} | CHAMPION_NAMES


class FakeFetcher:
    def __init__(self, text: str | Exception) -> None:
        self.text = text
        self.calls: list[tuple[str, bool]] = []

    def fetch(self, title: str, *, force: bool = False) -> PageResult:
        self.calls.append((title, force))
        if isinstance(self.text, Exception):
            raise self.text
        return PageResult(
            title=title,
            text=self.text,
            url="https://wiki.example.invalid/data.json",
            revid=142169,
            revised_at="2026-05-13T10:32:38Z",
            fetched_at="2026-10-26T14:00:00+00:00",
        )


def build_mdb(path: Path) -> Path:
    """最小 master.mdb：赛事目录 + 场地状态概率 + 两届大赛。"""
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
        CREATE TABLE race_course_set (
            id INTEGER PRIMARY KEY,
            race_track_id INTEGER NOT NULL,
            distance INTEGER NOT NULL,
            ground INTEGER NOT NULL,
            inout INTEGER NOT NULL,
            turn INTEGER NOT NULL
        );
        CREATE TABLE race (
            id INTEGER PRIMARY KEY,
            "group" INTEGER NOT NULL,
            grade INTEGER NOT NULL,
            course_set INTEGER NOT NULL,
            entry_num INTEGER NOT NULL
        );
        CREATE TABLE race_instance (
            id INTEGER PRIMARY KEY,
            race_id INTEGER NOT NULL
        );
        CREATE TABLE race_condition (
            id INTEGER PRIMARY KEY,
            area INTEGER NOT NULL,
            season INTEGER NOT NULL,
            weather INTEGER NOT NULL,
            ground INTEGER NOT NULL,
            rate INTEGER NOT NULL
        );
        CREATE TABLE champions_schedule (
            id INTEGER PRIMARY KEY,
            resource_id INTEGER NOT NULL,
            start_date INTEGER NOT NULL,
            end_date INTEGER NOT NULL,
            notice_date INTEGER NOT NULL
        );
        CREATE TABLE champions_round_schedule (
            id INTEGER PRIMARY KEY,
            champions_id INTEGER NOT NULL,
            round INTEGER NOT NULL,
            round_detail INTEGER NOT NULL,
            start_date INTEGER NOT NULL,
            end_date INTEGER NOT NULL,
            interval_start_time INTEGER NOT NULL,
            interval_end_time INTEGER NOT NULL
        );
        CREATE TABLE champions_round_detail (
            id INTEGER PRIMARY KEY,
            champions_id INTEGER NOT NULL,
            league_type INTEGER NOT NULL,
            round_id INTEGER NOT NULL,
            round INTEGER NOT NULL,
            tier INTEGER NOT NULL,
            round_number INTEGER NOT NULL,
            breakthrough_number_1 INTEGER NOT NULL,
            breakthrough_number_2 INTEGER NOT NULL,
            entry_number INTEGER NOT NULL,
            free_entry_number INTEGER NOT NULL
        );
        CREATE TABLE champions_race_condition (
            champions_id INTEGER NOT NULL,
            round_id INTEGER NOT NULL,
            race_instance_id INTEGER NOT NULL,
            race_condition_id INTEGER NOT NULL,
            PRIMARY KEY (champions_id, round_id)
        );
        """
    )
    courses = [
        (10602, 10006, 1600, 1, 1, 2),
        (10501, 10005, 1200, 1, 3, 1),
        (10301, 10003, 1000, 1, 1, 4),
        (10613, 10006, 2400, 2, 1, 2),
    ]
    conn.executemany("INSERT INTO race_course_set VALUES (?, ?, ?, ?, ?, ?)", courses)
    races = [
        (1001, 1, 100, 10501, 16),
        (1011, 1, 100, 10602, 18),
        (1051, 1, 300, 10602, 16),
        (1099, 1, 100, 10613, 16),
        (2000, 1, 900, 10301, 16),
        # 群英联赛：6005 / 6006 是预赛段，6017 / 6018 是决赛段
        (6005, 11, 100, 10602, 9),
        (6017, 11, 100, 10602, 9),
        (6006, 11, 100, 10613, 9),
        (6018, 11, 100, 10613, 9),
    ]
    conn.executemany("INSERT INTO race VALUES (?, ?, ?, ?, ?)", races)
    texts = [
        (32, 1001, "短途者锦标赛"),
        (32, 1011, "东京英里赛"),
        (32, 1051, "东京新闻杯"),
        (32, 1099, "东京大赏"),
        (32, 6005, "群英联赛 金牛杯"),
        (32, 6017, "群英联赛 金牛杯 决赛"),
        (32, 6006, "群英联赛 双子杯"),
        (32, 6018, "群英联赛 双子杯 决赛"),
        # 2000 没有名称，导入时应被跳过
    ]
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        # 真实库里 text_data.id 与 category 恒等，这里保持一致
        [(category, category, index, text) for category, index, text in texts],
    )

    # 每组 (area, season) 的 rate 之和都是 1e8，和真实库口径一致
    conditions = [
        (1, 999, 1, 1, 1, 54_600_000),
        (2, 999, 1, 2, 1, 45_400_000),
        (26, 999, 4, 4, 3, 4_850_000),
        (27, 999, 4, 1, 1, 95_150_000),
        (101, 5, 1, 1, 1, 100_000_000),
    ]
    conn.executemany("INSERT INTO race_condition VALUES (?, ?, ?, ?, ?, ?)", conditions)
    conn.executemany(
        "INSERT INTO race_instance VALUES (?, ?)",
        [(6005, 6005), (6017, 6017), (6006, 6006), (6018, 6018)],
    )

    champion_specs = [
        (1, 5, 1_699_848_000, 6005, 6017, 1),
        (2, 6, 1_702_526_400, 6006, 6018, 26),
    ]
    conn.executemany(
        "INSERT INTO champions_schedule (id, resource_id, start_date, end_date, notice_date) "
        "VALUES (?, ?, ?, ?, -28800)",
        [
            (cid, resource, start, start + 863_999)
            for cid, resource, start, _, _, _ in champion_specs
        ],
    )
    # 每届 6 个窗口：预选 1 个、两轮小组赛各 1 个、决赛 3 个
    windows = [(0, 0), (1, 0), (2, 0), (3, 1), (3, 2), (3, 3)]
    # 每届 7 条门槛：(round_id, round, tier, round_number, b1, b2) —— 与真实库一致
    thresholds = [
        (0, 0, 0, 0, 0, 0),
        (1, 1, 2, 5, 3, 0),
        (2, 2, 1, 5, 3, 0),
        (3, 2, 2, 5, 3, 2),
        (4, 3, 1, 1, 0, 0),
        (5, 3, 2, 1, 0, 0),
        (6, 3, 0, 0, 0, 0),
    ]
    round_rows = []
    entry_rows = []
    ref_rows = []
    for champions_id, _, start, prelim, final, condition in champion_specs:
        for offset, (round_, round_detail) in enumerate(windows):
            round_rows.append(
                (
                    champions_id,
                    round_,
                    round_detail,
                    start + offset * 86_400,
                    start + (offset + 1) * 86_400 - 1,
                )
            )
        for league in (1, 2):
            for round_id, round_, tier, number, first, second in thresholds:
                entry_rows.append(
                    (
                        champions_id,
                        league,
                        round_id,
                        round_,
                        tier,
                        number,
                        first,
                        second,
                        3 if number else 0,
                        1,
                    )
                )
        for round_id in range(7):
            ref_rows.append((champions_id, round_id, final if round_id >= 4 else prelim, condition))

    conn.executemany(
        "INSERT INTO champions_round_schedule "
        "(champions_id, round, round_detail, start_date, end_date, "
        "interval_start_time, interval_end_time) VALUES (?, ?, ?, ?, ?, 0, 0)",
        round_rows,
    )
    conn.executemany(
        "INSERT INTO champions_round_detail "
        "(champions_id, league_type, round_id, round, tier, round_number, "
        "breakthrough_number_1, breakthrough_number_2, entry_number, free_entry_number) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        entry_rows,
    )
    conn.executemany(
        "INSERT INTO champions_race_condition "
        "(champions_id, round_id, race_instance_id, race_condition_id) VALUES (?, ?, ?, ?)",
        ref_rows,
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def db(settings: Settings) -> Database:
    database = Database(settings.db_path)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture()
def mdb(settings: Settings) -> Path:
    return build_mdb(settings.mdb_path)


@pytest.fixture()
def options() -> RacesSettings:
    return RacesSettings(interval_seconds=0.0, retries=1, backoff_seconds=0.0)


def build_service(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, text: str | Exception
) -> RacesService:
    return RacesService(
        db,
        settings,
        options,
        tmp_path / "races",
        fetcher=FakeFetcher(text),
    )


# ------------------------------------------------------------------ 编码表
def test_venue_and_labels() -> None:
    assert sources.venue_of("10602") == "东京"
    assert sources.venue_of("11503") == "盛冈"
    assert sources.venue_of(None) is None
    assert sources.surface_label(1) == "草地"
    assert sources.surface_label(2) == "泥地"
    assert sources.direction_label(1) == "右"
    assert sources.direction_label(4) == "直线"
    assert sources.course_part_label(2) == "内圈"
    assert sources.course_part_label(3) == "外圈"
    assert sources.grade_label(100) == "GⅠ"
    assert sources.grade_label(12345) is None


# -------------------------------------------------------------------- 几何
def test_parse_computes_straights_and_slopes() -> None:
    courses, skipped = geometry.parse_all(GEOMETRY_TEXT, {"10602": 1600, "10501": 1200})

    assert skipped == []
    tokyo = courses["10602"]
    assert tokyo.straight_count == 2
    assert tokyo.straight_total == 1050
    assert tokyo.unlisted_length == 0
    assert tokyo.final_straight == 500
    assert tokyo.total_length == 1600
    assert tokyo.slope_positive == 72
    assert tokyo.slope_negative == 121
    assert tokyo.warnings == []

    nakayama = courses["10501"]
    # 首段起点 300 表示 0–300m 是数据页没列出的开场直线，补成「切线」并标为推断
    assert nakayama.segments[0]["name"] == "切线"
    assert nakayama.segments[0]["type"] == "直线"
    assert nakayama.segments[0]["inferred"] is True
    assert nakayama.opening_straight == 300
    assert nakayama.straight_count == 2
    assert nakayama.straight_total == 600
    assert nakayama.unlisted_length == 0
    assert nakayama.final_straight == 300
    assert nakayama.total_length == 1200


def test_parse_treats_midway_gaps_as_unlisted() -> None:
    record = {
        "id": "10502",
        "lane": [
            {"type": "弯道", "name": "弯道", "start": 50, "end": 500, "length": 450},
            {"type": "弯道", "name": "弯道", "start": 700, "end": 1050, "length": 350},
            {"type": "直线", "name": "终直", "start": 1300, "end": 1600, "length": 300},
        ],
        "slopes": [],
    }

    course = geometry.parse_course(record, mdb_distance=1600)

    # 0–50 是开场直线（切线），500–700、1050–1300 是中段空档，单列为「未列出」
    assert course.opening_straight == 50
    assert course.unlisted_length == 450
    assert course.straight_count == 2
    assert course.straight_total == 350
    assert course.total_length == 1600
    assert course.warnings == []


def test_parse_flags_inconsistent_records() -> None:
    courses, _ = geometry.parse_all(GEOMETRY_TEXT, {"10301": 1000, "10613": 2400})

    straight_only = courses["10301"]
    assert straight_only.straight_count == 1
    assert straight_only.final_straight == 0
    assert any("终直" in text for text in straight_only.warnings)

    broken = courses["10613"]
    assert any("不一致" in text for text in broken.warnings)
    assert any("越过终点" in text for text in broken.warnings)


def test_load_records_strips_nowiki_and_rejects_html() -> None:
    wrapped = f"<nowiki>{GEOMETRY_TEXT}</nowiki>"
    assert len(geometry.load_records(wrapped)) == 4

    with pytest.raises(geometry.GeometryError):
        geometry.load_records("<html><body>反爬页</body></html>")


# ------------------------------------------------------------------ 目录导入
def test_import_catalog_reads_races_and_courses(db: Database, mdb: Path) -> None:
    summary = import_catalog(db, mdb)

    assert summary.races == len(CATALOG_NAMES)
    assert summary.skipped == 1  # 2000 没有名称
    assert summary.courses == 4
    races = store.list_races(db)
    names = {row["name"] for row in races}
    assert names == CATALOG_NAMES
    tokyo_mile = next(row for row in races if row["name"] == "东京英里赛")
    assert tokyo_mile["venue"] == "东京"
    assert tokyo_mile["surface"] == "草地"
    assert tokyo_mile["direction"] == "左"
    assert tokyo_mile["course_part"] == "未区分"

    course = store.get_course(db, "10501")
    assert course is not None
    assert course["course_part"] == "外圈"
    assert course["has_geometry"] is False

    # 场地状态与大赛排期也在同一次导入里落库
    assert summary.conditions == 5
    assert summary.champions == 2
    assert store.condition_groups(db)[0]["total"] == sources.RATE_TOTAL


def test_import_catalog_reports_missing_file(db: Database, tmp_path: Path) -> None:
    with pytest.raises(RacesSourceError) as excinfo:
        import_catalog(db, tmp_path / "missing.mdb")

    assert "找不到" in str(excinfo.value)


def test_import_catalog_reports_missing_champion_tables(
    db: Database, mdb: Path, tmp_path: Path
) -> None:
    conn = sqlite3.connect(mdb)
    conn.execute("DROP TABLE champions_round_detail")
    conn.commit()
    conn.close()

    with pytest.raises(RacesSourceError) as excinfo:
        import_catalog(db, mdb)

    assert "champions_round_detail" in str(excinfo.value)


# -------------------------------------------------------------------- 服务
def test_refresh_imports_catalog_and_geometry(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)

    result = service.refresh(force=True)

    assert result["ok"] is True
    assert result["catalog"]["races"] == len(CATALOG_NAMES)
    assert result["geometry"]["courses"] == 4
    assert result["geometry"]["missing_geometry"] == 0

    course = service.course("10602")
    assert course is not None
    assert course["has_geometry"] is True
    assert course["final_straight"] == 500
    assert course["race_count"] == 4
    assert set(course["races"]) == {
        "东京英里赛",
        "东京新闻杯",
        "群英联赛 金牛杯",
        "群英联赛 金牛杯 决赛",
    }

    status = service.status()
    assert status["counts"]["races"] == len(CATALOG_NAMES)
    assert status["counts"]["courses_with_geometry"] == 4
    assert status["geometry"]["revid"] == 142169


def test_refresh_keeps_catalog_when_geometry_fails(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, WikiError("反爬页"))

    result = service.refresh()

    assert result["catalog"]["races"] == len(CATALOG_NAMES)
    assert result["geometry"] is None
    assert "赛道几何抓取失败" in result["error"]
    assert store.get_course(db, "10602")["has_geometry"] is False


def test_refresh_returns_error_without_mdb(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)

    result = service.refresh()

    assert result["ok"] is False
    assert result["catalog"] is None
    assert "找不到" in result["error"]


def test_ensure_catalog_runs_once(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)

    first = service.ensure_catalog()
    second = service.ensure_catalog()

    assert first is not None and first.races == len(CATALOG_NAMES)
    assert second is None


def test_queries_filter_by_venue_surface_and_grade(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)
    service.refresh()

    tokyo = service.races(venue="东京", limit=10)
    assert {item["name"] for item in tokyo["items"]} == CHAMPION_NAMES | {
        "东京英里赛",
        "东京新闻杯",
        "东京大赏",
    }

    dirt = service.races(surface="泥地")
    assert {item["name"] for item in dirt["items"]} == {
        "东京大赏",
        "群英联赛 双子杯",
        "群英联赛 双子杯 决赛",
    }

    official = service.races(official_only=True)
    assert "短途者锦标赛" in {item["name"] for item in official["items"]}
    # 没有名称的练习赛（10301）被导入时跳过，所以正式赛事就是全部 4 场
    assert official["returned"] == len(CATALOG_NAMES)
    assert official["truncated"] is False

    by_distance = service.courses(distance_min=1500, distance_max=1700)
    assert {item["course_id"] for item in by_distance["items"]} == {"10602"}

    race = service.race(1011)
    assert race is not None
    assert race["grade_label"] == "GⅠ"
    assert race["course"]["course_id"] == "10602"
    assert race["course"]["segments"][-1]["name"] == "终直"


# -------------------------------------------------------------------- 接口
def test_races_api_serves_status_and_courses(settings: Settings, mdb: Path) -> None:
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/races/status").json()
        courses = client.get("/api/races/courses").json()
        races = client.get("/api/races/races?official_only=true").json()

    assert status["counts"]["races"] == len(CATALOG_NAMES)
    assert len(status["venues"]) == 15
    assert courses["counts"]["total"] == 4
    row = courses["items"][0]
    assert row["has_geometry"] is False
    assert row["segments"] == []
    assert row["level"] == "C"
    assert {item["name"] for item in races["items"]} == CATALOG_NAMES


def test_races_api_returns_404_for_unknown_course(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/races/courses/99999")

    assert response.status_code == 404


def test_races_refresh_rejects_missing_mdb(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/races/refresh", json={"force": True})

    assert response.status_code == 422
    assert "找不到" in response.json()["detail"]


# ---------------------------------------------------------------- 场地状态
def test_conditions_become_probabilities(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)
    service.refresh()

    result = service.conditions(area=999, season=1)

    assert result["selected"]["area"] == 999
    assert result["selected"]["total"] == sources.RATE_TOTAL
    assert [(item["weather_label"], item["ground_label"]) for item in result["items"]] == [
        ("晴", "良"),
        ("阴", "良"),
    ]
    assert result["items"][0]["percent"] == 54.6
    assert sum(item["percent"] for item in result["items"]) == 100.0
    # 结论只能给出现率，不能编出修正系数
    assert "修正" in result["note"]
    assert {group["area"] for group in result["groups"]} == {5, 999}
    assert all(group["complete"] for group in result["groups"])


def test_conditions_fall_back_to_first_group(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)
    service.refresh()

    result = service.conditions(area=999, season=99)

    # season 99 不存在，但 area 999 存在 → 取该区的第一组
    assert result["selected"]["area"] == 999
    assert result["selected"]["season"] == 1


def test_conditions_is_empty_without_import(settings: Settings, db: Database) -> None:
    service = RacesService(db, settings, RacesSettings(), None, fetcher=FakeFetcher(GEOMETRY_TEXT))

    result = service.conditions()

    assert result["groups"] == []
    assert result["items"] == []
    assert result["zone"].startswith("UTC")


# -------------------------------------------------------------------- 大赛
def test_champions_join_course_and_condition(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)
    service.refresh()

    result = service.champions()

    assert result["counts"]["total"] == 2
    first = result["items"][0]
    assert first["champions_id"] == 1
    assert first["name"] == "群英联赛 金牛杯"
    assert first["final_name"] == "群英联赛 金牛杯 决赛"
    assert first["course_label"] == "东京 草地 1600m"
    assert first["weather_label"] == "晴"
    assert first["ground_label"] == "良"
    # 预赛段与决赛段共用一条赛道
    assert first["course_count"] == 1
    assert first["state"] == "finished"


def test_champion_detail_lists_rounds_and_leagues(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)
    service.refresh()

    detail = service.champion(1)

    assert detail is not None
    assert [row["round"] for row in detail["rounds"]] == [0, 1, 2, 3, 3, 3]
    assert detail["rounds"][-1]["round_detail"] == 3
    assert detail["rounds"][0]["starts_at"].startswith("2023-11-13")
    assert [league["league_type"] for league in detail["leagues"]] == [1, 2]
    entries = detail["leagues"][0]["entries"]
    assert len(entries) == 7
    assert entries[1]["requirement"] == "5 战 3 胜"
    assert entries[0]["requirement"] is None
    assert detail["course"]["course_id"] == "10602"


def test_champion_returns_none_for_unknown_id(
    db: Database, settings: Settings, options: RacesSettings, tmp_path: Path, mdb: Path
) -> None:
    service = build_service(db, settings, options, tmp_path, GEOMETRY_TEXT)
    service.refresh()

    assert service.champion(999) is None


def test_races_api_serves_conditions_and_champions(settings: Settings, mdb: Path) -> None:
    with TestClient(create_app(settings)) as client:
        client.post("/api/races/refresh", json={"force": False})
        conditions = client.get("/api/races/conditions?area=999&season=1").json()
        champions = client.get("/api/races/champions").json()
        detail = client.get("/api/races/champions/1").json()
        missing = client.get("/api/races/champions/999")

    assert conditions["items"][0]["weather_label"] == "晴"
    assert champions["items"][0]["champions_id"] == 1
    assert detail["rounds"]
    assert missing.status_code == 404


def test_races_module_registered(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        modules = client.get("/api/system/modules").json()

    by_id = {module["id"]: module for module in modules}
    assert by_id["races"]["status"] == "ok"
    assert by_id["races"]["ui"]["label"] == "赛道"
    assert by_id["races"]["ui"]["panel"] == "/module-assets/races/panel.js"
