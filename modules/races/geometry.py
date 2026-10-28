"""把 BWiki 的赛道几何数据页解析成分段与坡道统计。

数据页是 ``Data:Course/jian data.json``，每条记录形如::

    {"id": "10602", "lane": [{"type": "直线", "start": 0, "end": 550, "length": 550,
                              "name": "直线"}, ...],
     "slopes": [{"start": 328, "end": 400, "length": 72, "slope": 1}, ...]}

这里只做纯函数式的换算，不发请求、不写库；来源与可信级别见 ``docs/races.md``。
坡道的 ``slope`` 只有 ``1`` 与 ``-1``，数据页没有说明正负对应上坡还是下坡，
所以本模块按符号求和并原样保留，不给「上 / 下」结论。

数据页往往从第一个弯道（或第二段直线）开始记录，开头 0–start 的空档是发走后的
开场直线。这一段在源数据里没有名字，本模块补成 ``name="切线"`` 并标
``inferred=true``，长度单独记在 ``opening_straight``，避免与源数据列出的直线混淆。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_NOWIKI = re.compile(r"</?nowiki>")
_STRAIGHT_TYPES = {"直线", "直"}
_FINAL_STRAIGHT = "终直"
# 数据页从第一个弯道或第二段直线开始记录时，开头那段没列出的直线就是发走后的开场直线
_OPENING_STRAIGHT = "切线"
# 数据页的分段在首尾与弯道间会留空档（未列出的直线），末端与距离差 1–2m 属正常取整
_GAP_TOLERANCE = 2


class GeometryError(ValueError):
    """数据页内容不是预期的 JSON 数组。"""


@dataclass(slots=True)
class CourseGeometry:
    course_id: str
    distance: int
    segments: list[dict[str, Any]]
    slopes: list[dict[str, Any]]
    straight_count: int
    straight_total: int
    unlisted_length: int
    final_straight: int
    total_length: int
    slope_positive: int
    slope_negative: int
    opening_straight: int = 0
    mdb_distance: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        """几何数据的总长与 mdb 距离是否一致。"""
        return self.mdb_distance in (None, self.total_length)

    def as_row(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "distance": self.distance,
            "segments": json.dumps(self.segments, ensure_ascii=False),
            "slopes": json.dumps(self.slopes, ensure_ascii=False),
            "straight_count": self.straight_count,
            "straight_total": self.straight_total,
            "unlisted_length": self.unlisted_length,
            "final_straight": self.final_straight,
            "total_length": self.total_length,
            "slope_positive": self.slope_positive,
            "slope_negative": self.slope_negative,
            "opening_straight": self.opening_straight,
            "warnings": json.dumps(self.warnings, ensure_ascii=False),
        }


def strip_nowiki(text: str) -> str:
    return _NOWIKI.sub("", text).strip()


def load_records(text: str) -> list[dict[str, Any]]:
    payload = strip_nowiki(text)
    if not payload:
        raise GeometryError("数据页是空的")
    try:
        records = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GeometryError(f"数据页不是合法 JSON：{exc}") from exc
    if not isinstance(records, list):
        raise GeometryError("数据页顶层应为数组")
    return [record for record in records if isinstance(record, dict) and record.get("id")]


def _number(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def parse_course(record: dict[str, Any], *, mdb_distance: int | None = None) -> CourseGeometry:
    course_id = str(record["id"])
    segments = [
        {
            "type": str(item.get("type") or ""),
            "name": str(item.get("name") or ""),
            "start": _number(item.get("start")),
            "end": _number(item.get("end")),
            "length": _number(item.get("length")),
        }
        for item in (record.get("lane") or [])
        if isinstance(item, dict)
    ]
    slopes = [
        {
            "start": _number(item.get("start")),
            "end": _number(item.get("end")),
            "length": _number(item.get("length")),
            "slope": _number(item.get("slope")),
        }
        for item in (record.get("slopes") or [])
        if isinstance(item, dict)
    ]

    # 数据页经常从第一个弯道开始记录，开头这段空档是发走后的开场直线，补一段标为「切线」
    opening_straight = 0
    if segments and segments[0]["start"] > _GAP_TOLERANCE:
        opening_straight = segments[0]["start"]
        segments.insert(
            0,
            {
                "type": "直线",
                "name": _OPENING_STRAIGHT,
                "start": 0,
                "end": opening_straight,
                "length": opening_straight,
                "inferred": True,
            },
        )

    straight_parts = [item for item in segments if item["type"] in _STRAIGHT_TYPES]
    straight_count = len(straight_parts)
    straight_total = sum(item["length"] for item in straight_parts)
    final_straight = next(
        (item["length"] for item in segments if item["name"] == _FINAL_STRAIGHT), 0
    )
    total_length = max((segment["end"] for segment in segments), default=0)
    # 数据页只在弯道附近连续记录，未列出的空档按位置看都是直线，单独统计不并进直线段
    covered = 0
    previous_end = 0
    for segment in segments:
        start = max(segment["start"], previous_end)
        if segment["end"] > start:
            covered += segment["end"] - start
        previous_end = max(previous_end, segment["end"])
    unlisted = max(total_length - covered, 0)

    distance = mdb_distance if mdb_distance is not None else total_length
    geometry = CourseGeometry(
        course_id=course_id,
        distance=distance,
        segments=segments,
        slopes=slopes,
        straight_count=straight_count,
        straight_total=straight_total,
        unlisted_length=unlisted,
        final_straight=final_straight,
        total_length=total_length,
        slope_positive=sum(item["length"] for item in slopes if item["slope"] > 0),
        slope_negative=sum(item["length"] for item in slopes if item["slope"] < 0),
        opening_straight=opening_straight,
        mdb_distance=mdb_distance,
    )
    geometry.warnings = _warnings(geometry)
    return geometry


def _warnings(course: CourseGeometry) -> list[str]:
    warnings: list[str] = []
    if not course.segments:
        warnings.append("数据页没有分段记录")
        return warnings

    if course.mdb_distance and abs(course.total_length - course.mdb_distance) > _GAP_TOLERANCE:
        warnings.append(
            f"分段终点 {course.total_length}m 与数据库距离 {course.mdb_distance}m 不一致"
        )

    previous_end = 0
    for index, segment in enumerate(course.segments):
        if index and segment["start"] < previous_end - _GAP_TOLERANCE:
            warnings.append(f"第 {index + 1} 段与上一段重叠")
            break
        previous_end = segment["end"]

    last = course.segments[-1]
    if course.mdb_distance and last["end"] > course.mdb_distance + _GAP_TOLERANCE:
        warnings.append(f"最后一段越过终点（{last['end']}m > {course.mdb_distance}m）")

    if not course.final_straight:
        warnings.append("没有标记「终直」的分段")
    return warnings


def parse_all(
    text: str, distances: dict[str, int] | None = None
) -> tuple[dict[str, CourseGeometry], list[str]]:
    """解析整页数据，返回 (赛道 id → 几何, 未解析的记录说明)。"""
    distances = distances or {}
    courses: dict[str, CourseGeometry] = {}
    skipped: list[str] = []
    for record in load_records(text):
        course_id = str(record["id"])
        if course_id in courses:
            skipped.append(f"{course_id} 重复出现，已忽略后一条")
            continue
        try:
            courses[course_id] = parse_course(record, mdb_distance=distances.get(course_id))
        except (TypeError, ValueError, KeyError) as exc:  # pragma: no cover - 防御
            skipped.append(f"{course_id} 解析失败：{exc}")
    return courses, skipped
