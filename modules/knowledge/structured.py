"""结构化数据优先：回答前先用本地模块的接口查库。

设计取舍：

- **只走 HTTP**，不 import 别的模块（模块之间不互相依赖）。地址取
  ``UMA_KNOWLEDGE_STRUCTURED_BASE_URL``，留空时用内核 settings 的 ``host:port``。
- **实体识别靠数据本身**：中文问句没法可靠分词，所以先用库里已有的词表（赛场名）
  去问句里找最长匹配；找不到再用 2–8 字的滑动窗口查搜索接口（本地调用，几十毫秒），
  命中即停。
- **查不到就明说**：``misses`` 里写清「已尝试、没查到」，交给 RAG 兜底，
  不让模型凭印象编。
- **不参与生成**：这里只产出「结构化事实块」（带来源接口、涉及的表、数据版本），
  由 :mod:`modules.knowledge.service` 交给 LLM 组织语言，或在 LLM 不可用时直接展示。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


def _plus(value: Any) -> str:
    """加成数值一律带正号；没有这一项（None）时用破折号。"""
    return "—" if value is None else f"+{value}"


# 话题 → 关键词（命中即认为在问该话题）。顺序有意义：大赛要先查，
# 因为「这届大赛的赛道」需要先拿到大赛记录里的 course_id
TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("champions", ("大赛", "冠军大会", "群英联赛", "cm", "杯")),
    ("course", ("赛道", "终直", "坡道", "分段", "弯道", "直线")),
    ("skill", ("技能", "固有", "觉醒", "发动条件", "末脚")),
    ("gacha", ("卡池", "招募", "池子", "抽卡")),
    ("catalog", ("属性加成", "天赋", "协助卡", "支援卡", "这张卡", "马娘")),
)

_PUNCT = re.compile(r"[？?。，,！!、：:；;（）()\[\]「」【】\s]+")
_NUMBERS = re.compile(r"\d{3,4}")


@dataclass(slots=True)
class StructuredFact:
    """一条结构化事实：一个话题查到的东西 + 它的来源。"""

    topic: str
    label: str
    endpoint: str
    tables: str
    data_version: str
    lines: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "label": self.label,
            "endpoint": self.endpoint,
            "tables": self.tables,
            "data_version": self.data_version,
            "lines": self.lines,
        }

    def as_text(self) -> str:
        head = (
            f"[结构化] {self.label}"
            f"（来源 {self.endpoint} · 表 {self.tables} · 数据版本 {self.data_version}）"
        )
        return head + "\n" + "\n".join(f"- {line}" for line in self.lines)


@dataclass(slots=True)
class StructuredResult:
    tried: list[str] = field(default_factory=list)
    facts: list[StructuredFact] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def hit(self) -> bool:
        return bool(self.facts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tried": self.tried,
            "facts": [fact.as_dict() for fact in self.facts],
            "misses": self.misses,
            "errors": self.errors,
        }

    def as_prompt_block(self) -> str:
        if not self.facts:
            return ""
        blocks = [fact.as_text() for fact in self.facts]
        return (
            "以下是本机数据库查到的结构化事实（优先于文档）：\n\n"
            + "\n\n".join(blocks)
            + "\n\n引用这些事实时要写明来源接口与数据版本；"
            "与文档冲突时以结构化数据为准，并说明冲突。"
        )


def detect_topics(question: str) -> list[str]:
    text = (question or "").lower()
    return [topic for topic, keywords in TOPICS if any(word in text for word in keywords)]


def clean_question(question: str) -> str:
    return _PUNCT.sub("", question or "")


class StructuredLookup:
    """把问题变成若干条结构化事实；所有失败都转成 ``misses`` / ``errors``，不抛异常。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 6.0,
        enabled: bool = True,
        data_version: str = "未知",
        max_probes: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.enabled = enabled
        self.data_version = data_version
        self.max_probes = max_probes
        self._venues: list[str] | None = None
        self._probes = 0

    # ------------------------------------------------------------------ 入口
    def lookup(self, question: str) -> StructuredResult:
        result = StructuredResult()
        if not self.enabled or not self.base_url:
            return result
        self._probes = 0
        for topic in detect_topics(question):
            result.tried.append(topic)
            try:
                if topic == "course":
                    self._course(question, result)
                elif topic == "champions":
                    self._champions(result)
                elif topic == "skill":
                    self._skill(question, result)
                elif topic == "gacha":
                    self._gacha(result)
                elif topic == "catalog":
                    self._catalog(question, result)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                result.errors.append(f"{topic}: 连不上本地接口（{exc}）")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                result.errors.append(f"{topic}: 返回格式不符（{exc}）")
        return result

    # ------------------------------------------------------------------ 赛道
    def _course(self, question: str, result: StructuredResult) -> None:
        detail = None
        # 1) 问「大赛」时直接取那一届的赛道（大赛记录里就带 course_id）
        for fact in result.facts:
            if fact.topic == "champions":
                course_id = (fact.payload or {}).get("course_id")
                if course_id:
                    detail = self._get(f"/api/races/courses/{course_id}")
        # 2) 用赛场名匹配（赛场名是库里已有的词表，比中文分词可靠）
        if detail is None:
            detail = self._course_by_venue(question)
        # 3) 还是不行就按 2–8 字窗口查赛事名，再取该赛事的赛道
        if detail is None:
            race = self._probe("/api/races/races", question, limit=1)
            course_id = (race or {}).get("course_id")
            if course_id:
                detail = self._get(f"/api/races/courses/{course_id}")

        if detail is None:
            result.misses.append(
                "赛道：没从问题里识别出赛场或赛事名，可以带上赛场（如「中山 2500m」）再问"
            )
            return

        direction = detail.get("direction") or "未知"
        straight = (
            f"终直 {detail.get('final_straight') or '无'}m；"
            f"直线 {detail.get('straight_count')} 段、合计 {detail.get('straight_total')}m"
        )
        if detail.get("opening_straight"):
            straight += f"（含开场切线 {detail.get('opening_straight')}m）"
        lines = [
            f"{detail.get('label')}（{detail.get('course_id')}）：{detail.get('venue')} · "
            f"{detail.get('surface')} · {detail.get('distance')}m · 方向 {direction}",
            straight,
        ]
        if detail.get("slope_positive") or detail.get("slope_negative"):
            lines.append(
                f"坡道：标号 +1 共 {detail.get('slope_positive')}m、"
                f"标号 -1 共 {detail.get('slope_negative')}m"
                "（标号是数据页原值，上下坡方向未标注）"
            )
        if detail.get("unlisted_length"):
            lines.append(f"另有 {detail.get('unlisted_length')}m 未被数据页分段覆盖")
        if detail.get("races"):
            lines.append("引用赛事：" + "、".join(detail["races"][:5]))
        result.facts.append(
            StructuredFact(
                topic="course",
                label="赛道几何",
                endpoint=f"/api/races/courses/{detail.get('course_id')}",
                tables="races_courses / races_segments / races_slopes",
                data_version=self.data_version,
                lines=lines,
                payload={"course_id": detail.get("course_id")},
            )
        )

    def _course_by_venue(self, question: str) -> dict[str, Any] | None:
        if self._venues is None:
            status = self._get("/api/races/status") or {}
            self._venues = [row["venue"] for row in status.get("venues") or [] if row.get("venue")]
        text = clean_question(question)
        venues = [venue for venue in self._venues if venue and venue in text]
        if not venues:
            return None
        venue = max(venues, key=len)
        data = self._get("/api/races/courses", {"venue": venue}) or {}
        items = data.get("items") or []
        if not items:
            return None
        numbers = [int(value) for value in _NUMBERS.findall(text)]
        for item in items:
            if item.get("distance") in numbers:
                return self._get(f"/api/races/courses/{item['course_id']}")
        shortest = min(items, key=lambda item: item.get("distance") or 9999)
        return self._get(f"/api/races/courses/{shortest['course_id']}")

    # ------------------------------------------------------------------ 大赛
    def _champions(self, result: StructuredResult) -> None:
        data = self._get("/api/races/champions", {"upcoming": "true"}) or {}
        items = data.get("items") or []
        if not items:
            data = self._get("/api/races/champions", {"upcoming": "false"}) or {}
            items = (data.get("items") or [])[-2:]
        if not items:
            result.misses.append("大赛：库里没有大赛排期")
            return
        lines = []
        for item in items[:3]:
            weather = item.get("weather_label") or "—"
            ground = item.get("ground_label") or "—"
            lines.append(
                f"{item.get('name')}（{item.get('state')}）：{item.get('starts_at')} → "
                f"{item.get('ends_at')}；{item.get('course_label') or '赛道未知'}；"
                f"固定天气 {weather} / 马场 {ground}"
            )
        first = items[0]
        result.facts.append(
            StructuredFact(
                topic="champions",
                label="大赛排期",
                endpoint="/api/races/champions",
                tables="races_champions / races_champion_rounds / races_champion_entries",
                data_version=self.data_version,
                lines=lines,
                payload={
                    "count": len(items),
                    "course_id": first.get("course_id"),
                    "champions_id": first.get("champions_id"),
                },
            )
        )

    # ------------------------------------------------------------------ 技能
    def _skill(self, question: str, result: StructuredResult) -> None:
        item = self._probe("/api/skills/skills", question, limit=1)
        if not item:
            result.misses.append("技能：没从问题里识别出技能名")
            return
        detail = self._get(f"/api/skills/skills/{item['skill_id']}") or {}
        skill = detail.get("skill") or item
        attributes = {row["label"]: row["value"] for row in (detail.get("attributes") or [])}
        rarity = attributes.get("稀有度", "稀有度未知")
        lines = [f"{skill.get('name')}（#{skill.get('skill_id')}，{rarity}）"]
        if skill.get("description"):
            lines.append("描述：" + str(skill["description"]).replace("\n", " / "))
        for slot in (detail.get("slots") or [])[:3]:
            condition = slot.get("condition") or slot.get("precondition") or "无"
            lines.append(f"条件槽 {slot.get('slot')}：{condition}")
            for effect in (slot.get("effects") or [])[:2]:
                lines.append(
                    f"  效果 ability_type={effect.get('ability_type')}"
                    f"（{effect.get('ability_type_label')}） value={effect.get('value')}"
                    f" target_type={effect.get('target_type')} weight={effect.get('target_value')}"
                )
        if detail.get("characters"):
            lines.append(f"能学的马娘 {len(detail['characters'])} 位")
        if detail.get("support_cards"):
            lines.append(f"给这个技能的协助卡 {len(detail['support_cards'])} 张")
        result.facts.append(
            StructuredFact(
                topic="skill",
                label="技能条件与效果",
                endpoint=f"/api/skills/skills/{skill.get('skill_id')}",
                tables="skills_skills / skills_slots / skills_abilities / skills_sources",
                data_version=self.data_version,
                lines=lines,
                payload={"skill_id": skill.get("skill_id")},
            )
        )

    # ------------------------------------------------------------------ 卡池
    def _gacha(self, result: StructuredResult) -> None:
        data = self._get("/api/gacha/banners", {"limit_ended": "0"}) or {}
        active = data.get("active") or []
        upcoming = data.get("upcoming") or []
        if not active and not upcoming:
            result.misses.append("卡池：库里没有卡池排期")
            return
        lines = []
        for item in active[:3]:
            picks = "、".join(
                f"{row.get('name') or '未收录'}{row.get('title') or ''}"
                for row in (item.get("pickups") or [])[:3]
            )
            span = f"{item.get('starts_local')} → {item.get('ends_local')}"
            lines.append(
                f"进行中：{item.get('title')}（{span}）"
                + (f" · 精选 {picks}" if picks else " · 无精选标记")
            )
        for item in upcoming[:2]:
            span = f"{item.get('starts_local')} → {item.get('ends_local')}"
            lines.append(f"未开始：{item.get('title')}（{span}）")
        result.facts.append(
            StructuredFact(
                topic="gacha",
                label="卡池排期",
                endpoint="/api/gacha/banners",
                tables="gacha_catalog / gacha_pickups",
                data_version=self.data_version,
                lines=lines,
                payload={"active": len(active), "upcoming": len(upcoming)},
            )
        )

    # ------------------------------------------------------------------ 图鉴
    def _catalog(self, question: str, result: StructuredResult) -> None:
        item = self._probe("/api/catalog/search", question, limit=1)
        if not item:
            result.misses.append("图鉴：没从问题里识别出马娘或协助卡名")
            return
        kind = item.get("kind")
        detail = self._get(f"/api/catalog/items/{kind}/{item.get('id')}") or item
        title = f" · {detail.get('title')}" if detail.get("title") else ""
        lines = [f"{detail.get('name')}{title}（{kind} #{detail.get('id')}）"]
        for row in (detail.get("attributes") or [])[:5]:
            lines.append(f"{row.get('label')}：{row.get('value')}")
        # 训练效果按突破档位给：0 突与满突两端最能说明「练上去能涨多少」
        breakthroughs = detail.get("breakthroughs") or {}
        caps = breakthroughs.get("level_caps") or []
        for row in (breakthroughs.get("rows") or [])[:4]:
            values = row.get("values") or []
            if not values or not caps:
                continue
            lines.append(
                f"训练效果 {row.get('label')}：0 突（{caps[0]} 级）{_plus(values[0])} → "
                f"{len(caps) - 1} 突（{caps[-1]} 级）{_plus(values[-1])}"
            )
        unique = detail.get("unique") or {}
        if unique.get("text") or unique.get("items"):
            unlock = f"（{unique.get('need_lv')} 级解锁）" if unique.get("need_lv") else ""
            described = (unique.get("text") or "").replace("\n", " ").strip()
            items = "、".join(
                f"{item.get('label')} {_plus(item.get('value'))}"
                for item in (unique.get("items") or [])
            )
            lines.append(f"固有加成 {described or items}{unlock}")
        if detail.get("skills"):
            names = "、".join(row.get("name") or "?" for row in detail["skills"][:5])
            key = "技能" if kind == "character" else "提示技能"
            lines.append(f"{key}（{len(detail['skills'])}）：{names}")
        result.facts.append(
            StructuredFact(
                topic="catalog",
                label="图鉴详情",
                endpoint=f"/api/catalog/items/{kind}/{item.get('id')}",
                tables="catalog_characters / catalog_support_cards / catalog_support_effects",
                data_version=self.data_version,
                lines=lines,
                payload={"kind": kind, "id": item.get("id")},
            )
        )

    # ------------------------------------------------------------------ 探测
    def _probe(self, path: str, question: str, limit: int = 1) -> dict[str, Any] | None:
        """用 8→2 字的滑动窗口去查搜索接口，命中即停。

        中文问句没有空格，没法靠分词拿实体名；但库里的名字都是短词，
        小窗口总能命中一次。单次是本地 HTTP，整体有 ``max_probes`` 上限。
        """
        text = clean_question(question)
        if not text:
            return None
        for size in range(8, 1, -1):
            for start in range(0, max(0, len(text) - size) + 1):
                if self._probes >= self.max_probes:
                    return None
                self._probes += 1
                piece = text[start : start + size]
                data = self._get(path, {"q": piece, "limit": limit})
                items = (data or {}).get("items") or []
                if items:
                    return items[0]
        return None

    # ------------------------------------------------------------------ HTTP
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{query}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
