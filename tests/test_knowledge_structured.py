from __future__ import annotations

from typing import Any

import pytest

from modules.knowledge.llm import LLMNotConfigured
from modules.knowledge.models import ChatResponse
from modules.knowledge.service import KnowledgeService
from modules.knowledge.settings import KnowledgeSettings
from modules.knowledge.structured import (
    StructuredFact,
    StructuredLookup,
    StructuredResult,
    clean_question,
    detect_topics,
)


class FakeAPI:
    """按路径前缀返回预置 JSON 的假接口；最长前缀优先，模拟真实路由。"""

    def __init__(self, payloads: dict[str, Any]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def __call__(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append(path)
        matches = [prefix for prefix in self.payloads if path.startswith(prefix)]
        if not matches:
            return {}
        return self.payloads[max(matches, key=len)]


def build_lookup(payloads: dict[str, Any]) -> tuple[StructuredLookup, FakeAPI]:
    lookup = StructuredLookup("http://127.0.0.1:1", data_version="mdb-test")
    fake = FakeAPI(payloads)
    lookup._get = fake  # type: ignore[method-assign]
    return lookup, fake


# ------------------------------------------------------------------ 识别
def test_detect_topics_puts_champions_first() -> None:
    topics = detect_topics("这届大赛的赛道终直多长？")

    # 大赛要先查，赛道才能用上它记录里的 course_id
    assert topics == ["champions", "course"]


def test_detect_topics_matches_multiple_topics() -> None:
    assert set(detect_topics("现在卡池有什么，技能加速装置怎么样")) == {"gacha", "skill"}


def test_clean_question_drops_punctuation() -> None:
    assert clean_question("加快步伐，这个技能的条件是什么？") == "加快步伐这个技能的条件是什么"


# ------------------------------------------------------------------ 各话题
def test_course_follows_champion_course_id() -> None:
    lookup, fake = build_lookup(
        {
            "/api/races/champions": {
                "items": [
                    {
                        "champions_id": 28,
                        "name": "群英联赛 长距离",
                        "state": "upcoming",
                        "starts_at": "2026-11-06T12:00:00+08:00",
                        "ends_at": "2026-11-15T11:59:59+08:00",
                        "course_id": "10506",
                        "course_label": "中山 草地 2500m",
                        "weather_label": "雪",
                        "ground_label": "重",
                    }
                ]
            },
            "/api/races/courses/10506": {
                "course_id": "10506",
                "label": "中山 草地 2500m",
                "venue": "中山",
                "surface": "草地",
                "distance": 2500,
                "direction": "右",
                "final_straight": 300,
                "straight_count": 4,
                "straight_total": 1104,
                "opening_straight": 100,
                "slope_positive": 110,
                "slope_negative": 40,
                "races": ["有马纪念"],
            },
        }
    )

    result = lookup.lookup("这届大赛的赛道终直多长？")

    assert [fact.topic for fact in result.facts] == ["champions", "course"]
    course = result.facts[1]
    assert course.endpoint == "/api/races/courses/10506"
    assert any("终直 300m" in line for line in course.lines)
    assert any("含开场切线 100m" in line for line in course.lines)
    assert "/api/races/courses/10506" in fake.calls
    assert result.misses == []


def test_course_matches_venue_and_distance() -> None:
    lookup, _ = build_lookup(
        {
            "/api/races/status": {"venues": [{"venue": "中山"}, {"venue": "阪神"}]},
            "/api/races/courses": {
                "items": [
                    {"course_id": "10501", "distance": 1200},
                    {"course_id": "10506", "distance": 2500},
                ]
            },
            "/api/races/courses/10506": {
                "course_id": "10506",
                "label": "中山 草地 2500m",
                "venue": "中山",
                "surface": "草地",
                "distance": 2500,
                "direction": "右",
                "final_straight": 300,
                "straight_count": 4,
                "straight_total": 1104,
            },
        }
    )

    result = lookup.lookup("中山 2500m 这条赛道终直多长")

    assert result.facts[0].endpoint == "/api/races/courses/10506"


def test_course_miss_is_reported() -> None:
    lookup, _ = build_lookup({"/api/races/status": {"venues": []}})

    result = lookup.lookup("这条赛道终直多长")

    assert result.facts == []
    assert any("没从问题里识别出赛场" in miss for miss in result.misses)


def test_skill_probe_finds_skill_name_inside_sentence() -> None:
    lookup, _ = build_lookup(
        {
            "/api/skills/skills": {"items": [{"skill_id": 200462, "name": "加快步伐"}]},
            "/api/skills/skills/200462": {
                "skill": {
                    "skill_id": 200462,
                    "name": "加快步伐",
                    "description": "在赛事中期赶超成功时\n速度略微提升",
                },
                "attributes": [{"label": "稀有度", "value": "普通"}],
                "slots": [
                    {
                        "slot": 1,
                        "condition": "phase==1&change_order_onetime<0",
                        "effects": [
                            {
                                "ability_type": 27,
                                "ability_type_label": "原始值 27",
                                "value": 1500,
                                "target_type": 1,
                                "target_value": 0,
                            }
                        ],
                    }
                ],
                "characters": [{"chara_id": 1}, {"chara_id": 2}],
                "support_cards": [],
            },
        }
    )

    result = lookup.lookup("加快步伐这个技能的条件是什么")

    assert result.facts, "问句里含短技能名时也要能查到"
    fact = result.facts[0]
    assert any("加快步伐" in line for line in fact.lines)
    assert any("phase==1" in line for line in fact.lines)
    assert any("能学的马娘 2 位" in line for line in fact.lines)


def test_skill_miss_when_nothing_matches() -> None:
    lookup, _ = build_lookup({"/api/skills/skills": {"items": []}})

    result = lookup.lookup("某个不存在的技能条件")

    assert result.facts == []
    assert any("没从问题里识别出技能名" in miss for miss in result.misses)


def test_gacha_fact_lists_active_and_upcoming() -> None:
    lookup, _ = build_lookup(
        {
            "/api/gacha/banners": {
                "counts": {"active": 1, "upcoming": 1},
                "active": [
                    {
                        "title": "精选 优俊少女招募",
                        "starts_local": "2026-10-24T12:00:00+08:00",
                        "ends_local": "2026-11-03T11:59:59+08:00",
                        "pickups": [{"name": "跳舞城", "title": "[GLITTER！]"}],
                    }
                ],
                "upcoming": [
                    {
                        "title": "精选 协助卡招募",
                        "starts_local": "2026-11-05T12:00:00+08:00",
                        "ends_local": "2026-11-14T11:59:59+08:00",
                    }
                ],
            }
        }
    )

    result = lookup.lookup("现在有什么卡池")

    lines = result.facts[0].lines
    assert any(line.startswith("进行中：精选 优俊少女招募") for line in lines)
    assert any("跳舞城" in line for line in lines)
    assert any(line.startswith("未开始：精选 协助卡招募") for line in lines)


def test_catalog_fact_reports_breakthrough_values() -> None:
    lookup, _ = build_lookup(
        {
            "/api/catalog/search": {
                "items": [{"kind": "support_card", "id": 20001, "name": "富士奇石"}]
            },
            "/api/catalog/items/support_card/20001": {
                "kind": "support_card",
                "id": 20001,
                "name": "富士奇石",
                "title": "[哎呀呀，欢迎回来]",
                "attributes": [{"label": "稀有度", "value": "SR"}],
                "breakthroughs": {
                    "level_caps": [25, 30, 35, 40, 45],
                    "rows": [{"label": "友情加成", "values": [10, 10, 10, 20, 20]}],
                },
                "unique": {
                    "text": None,
                    "conditional": False,
                    "need_lv": 25,
                    "unlock_breakthrough": 0,
                    "items": [{"label": "技能点数加成", "value": 1}],
                },
                "skills": [{"name": "夏季优俊少女○"}],
            },
        }
    )

    result = lookup.lookup("富士奇石这张协助卡的属性加成有多少")

    lines = result.facts[0].lines
    assert any("0 突（25 级）+10" in line for line in lines)
    assert any("4 突（45 级）+20" in line for line in lines)
    assert any("（25 级解锁）" in line for line in lines)
    assert any("提示技能（1）" in line for line in lines)


def test_disabled_lookup_returns_nothing() -> None:
    lookup = StructuredLookup("http://127.0.0.1:1", enabled=False)

    result = lookup.lookup("现在有什么卡池")

    assert result.tried == []
    assert result.as_prompt_block() == ""


# ------------------------------------------------------------------ service 编排
class StubLLM:
    """记录收到的上下文，用来验证「结构化块」是否进了提示词。"""

    model = "stub-model"

    def __init__(self, *, configured: bool = True) -> None:
        self.configured = configured
        self.contexts: list[list[str]] = []

    def answer(self, question, contexts, history, *, data_version):
        if not self.configured:
            raise LLMNotConfigured("未配置 DEEPSEEK_API_KEY")
        self.contexts.append(list(contexts))
        return {"answer": "组织好的回答", "usage": None}


class StubLookup:
    def __init__(self, result: StructuredResult) -> None:
        self.result = result

    def lookup(self, question: str) -> StructuredResult:
        return self.result


def build_service(structured: StructuredResult, llm: StubLLM) -> KnowledgeService:
    service = object.__new__(KnowledgeService)
    service.settings = KnowledgeSettings(deepseek_api_key="")  # type: ignore[assignment]
    service._data_version = lambda: "mdb-test"  # type: ignore[method-assign]
    service.structured = StubLookup(structured)  # type: ignore[assignment]
    service.llm = llm  # type: ignore[assignment]
    service._retrieve = lambda *args, **kwargs: []  # type: ignore[method-assign]
    service._to_sources = lambda hits: []  # type: ignore[method-assign]
    return service


def _fact() -> StructuredFact:
    return StructuredFact(
        topic="gacha",
        label="卡池排期",
        endpoint="/api/gacha/banners",
        tables="gacha_catalog / gacha_pickups",
        data_version="mdb-test",
        lines=["进行中：精选 优俊少女招募"],
    )


def test_chat_marks_documents_only_when_structured_misses() -> None:
    llm = StubLLM()
    service = build_service(StructuredResult(misses=["卡池：库里没有卡池排期"]), llm)

    response = service.chat("现在有什么卡池")

    assert isinstance(response, ChatResponse)
    assert response.mode == "documents_only"
    assert response.structured is not None
    assert response.structured["facts"] == []
    # 没命中时提示词里不该出现结构化块
    assert not any("结构化" in ctx for ctx in llm.contexts[0])


def test_chat_puts_structured_facts_first_in_prompt() -> None:
    llm = StubLLM()
    service = build_service(StructuredResult(tried=["gacha"], facts=[_fact()]), llm)

    response = service.chat("现在有什么卡池")

    assert response.mode == "ok"
    first = llm.contexts[0][0]
    assert "结构化" in first
    assert "/api/gacha/banners" in first
    assert "mdb-test" in first


def test_chat_returns_structured_answer_when_llm_missing() -> None:
    llm = StubLLM(configured=False)
    service = build_service(StructuredResult(tried=["gacha"], facts=[_fact()]), llm)

    response = service.chat("现在有什么卡池")

    assert response.mode == "structured_only"
    assert "未配置 DEEPSEEK_API_KEY" in response.answer
    assert "精选 优俊少女招募" in response.answer
    assert "mdb-test" in response.answer


def test_chat_raises_when_neither_llm_nor_facts() -> None:
    llm = StubLLM(configured=False)
    service = build_service(StructuredResult(misses=["没查到"]), llm)

    with pytest.raises(LLMNotConfigured):
        service.chat("现在有什么卡池")
