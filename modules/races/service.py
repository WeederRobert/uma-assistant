"""赛道模块的业务逻辑：目录导入、几何导入、大赛排期与结构化查询。

判定口径都写在这里，接口层与面板只做展示：

- 赛事目录来自 ``master.mdb``（可信级别 A，客户端原始数据）
- 几何来自 BWiki ``Data:Course/jian data.json``（C 级：字段含义为推断）
- 场地状态概率来自 ``race_condition``：同一 ``(area, season)`` 组内 ``rate`` 之和为 1e8，
  所以 ``rate / 1e8`` 就是出现率；这里只有概率，没有速度 / 耐力修正系数
- 大赛（群英联赛）排期来自 ``champions_*``：每届引用 1 条 ``race_condition``（固定天气与马场）
  和 2 个 ``race_instance``（预赛段与决赛段，共用同一赛道）
- 赛场 / 场地 / 方向 / 级别的编码表在 :mod:`modules.races.sources`
- 时间一律按运行环境的系统时区显示（见 :mod:`app.kernel.times`）
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.kernel.config import Settings
from app.kernel.database import Database
from app.kernel.dataversion import read_data_version
from app.kernel.times import local_iso, system_zone, zone_label

from . import geometry, sources, store
from .importer import CatalogSummary, RacesSourceError, import_catalog
from .schema import META_GEOMETRY, ensure_schema
from .settings import RacesSettings
from .wikidata import PageFetcher, WikiError, WikiPageFetcher


class RacesService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        options: RacesSettings | None = None,
        data_dir: Path | None = None,
        *,
        fetcher: PageFetcher | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.options = options or RacesSettings()
        self.data_dir = data_dir or settings.module_data_dir("races")
        self.logger = logger or logging.getLogger(__name__)
        self._fetcher = fetcher
        ensure_schema(db)

    # ------------------------------------------------------------------ 来源
    @property
    def fetcher(self) -> PageFetcher:
        if self._fetcher is None:
            self._fetcher = WikiPageFetcher(
                self.options,
                self.data_dir / "cache",
                logger=self.logger,
            )
        return self._fetcher

    def close(self) -> None:
        if isinstance(self._fetcher, WikiPageFetcher):
            self._fetcher.close()

    @property
    def mdb_path(self) -> Path:
        return Path(self.options.mdb_path) if self.options.mdb_path else self.settings.mdb_path

    # ------------------------------------------------------------------ 状态
    def ensure_catalog(self) -> CatalogSummary | None:
        """本地库还没有赛事目录、而 master.mdb 在位时，启动即补一次。"""
        if store.count_races(self.db) or not self.mdb_path.exists():
            return None
        try:
            return import_catalog(self.db, self.mdb_path)
        except RacesSourceError as exc:
            self.logger.warning("启动时导入赛道目录失败：%s", exc)
            return None

    def status(self) -> dict[str, Any]:
        mdb = self.mdb_path
        geometry_source = store.geometry_source(self.db)
        return {
            "counts": {
                "races": store.count_races(self.db),
                "official_races": store.count_races(self.db, official_only=True),
                "courses": store.count_courses(self.db),
                "courses_with_geometry": store.count_courses(self.db, geometry_only=True),
                "conditions": store.count_conditions(self.db),
                "champions": store.count_champions(self.db),
            },
            "venues": [
                {"venue": venue, "central": venue in sources.CENTRAL_VENUES}
                for venue in sources.VENUES.values()
            ],
            "surfaces": sorted(set(sources.SURFACES.values())),
            "directions": sorted(set(sources.DIRECTIONS.values())),
            "grades": [{"grade": grade, "label": label} for grade, label in sources.GRADES.items()],
            "catalog": store.catalog_source(self.db),
            "geometry": geometry_source,
            "geometry_page": self.options.geometry_page,
            "mdb": {
                "path": str(mdb),
                "exists": mdb.exists(),
                "size_mb": round(mdb.stat().st_size / 1024 / 1024, 1) if mdb.exists() else None,
            },
            "data_version": read_data_version(self.db).as_dict(),
        }

    # ------------------------------------------------------------------ 查询
    def courses(
        self,
        *,
        venue: str | None = None,
        surface: str | None = None,
        direction: str | None = None,
        distance_min: int | None = None,
        distance_max: int | None = None,
        geometry_only: bool = False,
    ) -> dict[str, Any]:
        rows = store.list_courses(
            self.db,
            venue=venue,
            surface=surface,
            direction=direction,
            distance_min=distance_min,
            distance_max=distance_max,
            geometry_only=geometry_only,
        )
        counts = store.race_counts_by_course(self.db)
        items = [self._course_row(row, counts.get(row["course_id"], 0)) for row in rows]
        return {
            "items": items,
            "counts": {
                "total": len(items),
                "with_geometry": sum(1 for item in items if item["has_geometry"]),
                "returns": sum(item["race_count"] for item in items),
            },
        }

    def course(self, course_id: str) -> dict[str, Any] | None:
        row = store.get_course(self.db, course_id)
        if row is None:
            return None
        counts = store.race_counts_by_course(self.db)
        item = self._course_row(row, counts.get(course_id, 0))
        item["races"] = store.catalog_races(self.db, course_id)
        return item

    def races(
        self,
        *,
        query: str | None = None,
        venue: str | None = None,
        surface: str | None = None,
        direction: str | None = None,
        grade: int | None = None,
        official_only: bool = False,
        distance_min: int | None = None,
        distance_max: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(limit or self.options.search_limit, 200))
        rows = store.list_races(
            self.db,
            query=query,
            venue=venue,
            surface=surface,
            direction=direction,
            grade=grade,
            official_only=official_only,
            distance_min=distance_min,
            distance_max=distance_max,
            limit=limit,
        )
        return {
            "items": [self._race_row(row) for row in rows],
            "limit": limit,
            "returned": len(rows),
            "truncated": len(rows) >= limit,
        }

    def race(self, race_id: int) -> dict[str, Any] | None:
        row = store.get_race(self.db, race_id)
        if row is None:
            return None
        item = self._race_row(row)
        if row["course_id"]:
            item["course"] = self.course(row["course_id"])
        else:
            item["course"] = None
        return item

    # ---------------------------------------------------------------- 场地状态
    def conditions(self, *, area: int | None = None, season: int | None = None) -> dict[str, Any]:
        """按 ``(area, season)`` 分组返回场地状态出现率。

        ``rate`` 是十万分率整数，``rate / 1e8`` 才是概率；一组之和必须等于 1e8，
        否则说明源数据不完整（``complete`` 会是 ``False``）。
        """
        zone = system_zone()
        groups = []
        for row in store.condition_groups(self.db):
            total = int(row["total"] or 0)
            groups.append(
                {
                    "area": int(row["area"]),
                    "area_label": sources.area_label(int(row["area"])),
                    "season": int(row["season"]),
                    "count": int(row["n"]),
                    "total": total,
                    "complete": total == sources.RATE_TOTAL,
                }
            )
        if not groups:
            return {"zone": zone_label(zone), "groups": [], "selected": None, "items": []}

        keys = {(item["area"], item["season"]) for item in groups}
        if (area, season) in keys:
            selected = (area, season)
        elif area is not None and any(item["area"] == area for item in groups):
            selected = next(
                (item["area"], item["season"]) for item in groups if item["area"] == area
            )
        else:
            # 默认展示大赛引用的那张表，避免一进来看到的是没人引用的备用表
            selected = next(
                (
                    (item["area"], item["season"])
                    for item in groups
                    if item["area"] == sources.DEFAULT_AREA
                ),
                (groups[0]["area"], groups[0]["season"]),
            )

        rows = store.list_conditions(self.db, area=selected[0], season=selected[1])
        items = []
        for row in rows:
            rate = int(row["rate"])
            items.append(
                {
                    "weather": int(row["weather"]),
                    "weather_label": sources.weather_label(int(row["weather"])),
                    "ground": int(row["ground"]),
                    "ground_label": sources.ground_label(int(row["ground"])),
                    "rate": rate,
                    "percent": round(rate * 100 / sources.RATE_TOTAL, 2),
                }
            )
        return {
            "zone": zone_label(zone),
            "groups": groups,
            "selected": {
                "area": selected[0],
                "area_label": sources.area_label(selected[0]),
                "season": selected[1],
                "total": sum(item["rate"] for item in items),
            },
            "items": items,
            "note": "rate 是出现率，不是速度或耐力修正系数；mdb 里没有修正数值。",
        }

    # -------------------------------------------------------------------- 大赛
    def champions(self, *, upcoming: bool = False) -> dict[str, Any]:
        zone = system_zone()
        now = datetime.now().timestamp()
        items = [
            self._champion_row(row, now) for row in store.list_champions(self.db, upcoming=upcoming)
        ]
        return {
            "zone": zone_label(zone),
            "counts": {
                "total": store.count_champions(self.db),
                "returned": len(items),
                "upcoming": sum(1 for item in items if item["state"] != "finished"),
            },
            "items": items,
            "note": "赛事名取自 race 表的 text_data（「群英联赛 金牛杯」）；"
            "天气与马场状态是该届的固定值，不是概率。",
        }

    def champion(self, champions_id: int) -> dict[str, Any] | None:
        row = store.get_champion(self.db, champions_id)
        if row is None:
            return None
        item = self._champion_row(row, datetime.now().timestamp())
        item["rounds"] = [
            {
                "round": int(item_row["round"]),
                "round_label": sources.round_label(int(item_row["round"])),
                "round_detail": int(item_row["round_detail"]),
                "starts_at": local_iso(item_row["start_ts"]),
                "ends_at": local_iso(item_row["end_ts"]),
            }
            for item_row in store.champion_rounds(self.db, champions_id)
        ]
        entries: dict[int, list[dict[str, Any]]] = {}
        for item_row in store.champion_entries(self.db, champions_id):
            league = int(item_row["league_type"])
            round_number = int(item_row["round_number"] or 0)
            win = int(item_row["breakthrough_1"] or 0)
            entries.setdefault(league, []).append(
                {
                    "round_id": int(item_row["round_id"]),
                    "round": item_row["round"],
                    "tier": item_row["tier"],
                    "round_number": round_number,
                    "wins_needed": win,
                    "wins_needed_2": int(item_row["breakthrough_2"] or 0),
                    "entry_number": int(item_row["entry_number"] or 0),
                    "free_entry_number": int(item_row["free_entry_number"] or 0),
                    "requirement": f"{round_number} 战 {win} 胜" if round_number and win else None,
                }
            )
        item["leagues"] = [
            {"league_type": league, "label": sources.league_label(league), "entries": rows}
            for league, rows in sorted(entries.items())
        ]
        item["course"] = self.course(item["course_id"]) if item["course_id"] else None
        return item

    # ------------------------------------------------------------------ 刷新
    def refresh(self, *, force: bool = False, mdb_path: str | None = None) -> dict[str, Any]:
        source = Path(mdb_path) if mdb_path else self.mdb_path
        result: dict[str, Any] = {
            "ok": False,
            "catalog": None,
            "geometry": None,
            "error": None,
            "note": None,
            "skipped": [],
        }

        try:
            summary = import_catalog(self.db, source)
        except RacesSourceError as exc:
            result["error"] = str(exc)
            return result
        result["catalog"] = summary.as_dict()

        try:
            page = self.fetcher.fetch(self.options.geometry_page, force=force)
        except WikiError as exc:
            result["error"] = f"赛道几何抓取失败：{exc}"
            result["ok"] = bool(summary.races)
            return result

        distances = store.course_distances(self.db)
        parsed, skipped = geometry.parse_all(page.text, distances)
        registry = {row["course_id"]: row for row in store.list_courses(self.db)}
        rows: list[dict[str, Any]] = []
        unknown: list[str] = []
        for course_id, course in parsed.items():
            base = registry.get(course_id)
            if base is None:
                unknown.append(course_id)
                continue
            row = course.as_row()
            row.update(
                venue=base["venue"],
                surface=base["surface"],
                direction=base.get("direction"),
                course_part=base.get("course_part"),
                has_geometry=1,
                source_url=page.url,
                fetched_at=page.fetched_at,
            )
            rows.append(row)

        store.upsert_courses(self.db, rows)
        inconsistent = [
            f"{course.course_id}：{'；'.join(course.warnings)}"
            for course in parsed.values()
            if course.warnings and course.course_id in registry
        ]
        store.set_source(
            self.db,
            META_GEOMETRY,
            {
                "page": page.title,
                "url": page.url,
                "revid": page.revid,
                "revised_at": page.revised_at,
                "fetched_at": page.fetched_at,
                "from_cache": page.from_cache,
                "stale": page.stale,
                "courses": len(rows),
                "with_warnings": len(inconsistent),
                "note": page.note,
            },
        )
        result["ok"] = True
        result["geometry"] = {
            "page": page.title,
            "url": page.url,
            "revid": page.revid,
            "revised_at": page.revised_at,
            "fetched_at": page.fetched_at,
            "from_cache": page.from_cache,
            "stale": page.stale,
            "courses": len(rows),
            "with_warnings": len(inconsistent),
            "missing_geometry": store.count_courses(self.db)
            - store.count_courses(self.db, geometry_only=True),
        }
        result["skipped"] = skipped + [f"数据页里有本地没有的赛道：{cid}" for cid in unknown]
        result["note"] = page.note
        self.logger.info(
            "赛道刷新完成 | 赛事=%d 赛道=%d 几何=%d | 缓存=%s",
            summary.races,
            summary.courses,
            len(rows),
            page.from_cache,
        )
        return result

    # ------------------------------------------------------------------ 展示
    def _race_row(self, row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["grade_label"] = sources.grade_label(row.get("grade"))
        return item

    def _champion_row(self, row: Any, now: float) -> dict[str, Any]:
        start_ts = row["start_ts"]
        end_ts = row["end_ts"]
        if end_ts is not None and end_ts < now:
            state = "finished"
        elif start_ts is not None and start_ts > now:
            state = "upcoming"
        else:
            state = "running"
        course_label = None
        if row["venue"] and row["distance"]:
            course_label = f"{row['venue']} {row['surface']} {row['distance']}m"
        return {
            "champions_id": int(row["champions_id"]),
            "resource_id": row["resource_id"],
            "start_ts": start_ts,
            "end_ts": end_ts,
            "starts_at": local_iso(start_ts),
            "ends_at": local_iso(end_ts),
            "prelim_race_id": row["prelim_race_id"],
            "prelim_name": row["prelim_name"],
            "final_race_id": row["final_race_id"],
            "final_name": row["final_name"],
            "name": row["prelim_name"] or row["final_name"] or f"第 {row['champions_id']} 届",
            "course_id": row["course_id"],
            "course_label": course_label,
            "venue": row["venue"],
            "surface": row["surface"],
            "distance": row["distance"],
            "course_part": row["course_part"],
            "course_count": int(row["course_count"] or 0),
            "race_condition_id": row["race_condition_id"],
            "weather": row["weather"],
            "weather_label": sources.weather_label(row["weather"]),
            "ground": row["ground"],
            "ground_label": sources.ground_label(row["ground"]),
            "state": state,
        }

    def _course_row(self, row: dict[str, Any], race_count: int) -> dict[str, Any]:
        item = dict(row)
        item["race_count"] = race_count
        item["label"] = f"{row['venue']} {row['surface']} {row['distance']}m"
        item["level"] = "C"
        item["level_label"] = sources.LEVEL_LABELS["C"]
        return item
