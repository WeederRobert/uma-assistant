"""枚举值到可读标签的映射。

只收录在真实 ``master.mdb`` 上核对过的取值；未收录的取值按「原始值 N」显示，
不猜测含义，避免把推断当作事实。

稀有度依据（简中服 2.0.2 / ``master.mdb`` 2026-10-22 快照复核）：

- ``card_data.default_rarity`` / ``support_card_data.rarity``：1/2/3，
  分别对应马娘的 ★1/★2/★3 与协助卡的 R/SR/SSR。
  马娘另有 ``0``，仅 2 条（``9100101`` / ``9101101``），除名字与称号外
  ``available_skill_set_id`` / ``talent_*`` / ``get_piece_id`` 全为 0，
  是占位记录，标为「占位」。
- ``skill_data.rarity``：1 普通、2 稀有（金）、3 固有、4 进化、5 固有、6 升级。
  - 3 与 4 的关系已验证：进化技能 id = 固有 id + 90000（10071 小心波澜炮！→ 100071
    不沉之舰，拔锚起航！）；16 个 rarity=4 的记录中有 13 个能按描述匹配到 rarity=3 的固有技能。
  - 6 拆成两类，依据是主数据里两张参照表的 ``skill_id`` 集合与 skill_data 完全相等：
    338 个 9 位 id（``<马娘卡 id><序号>11/21``）全部出现在
    ``skill_upgrade_description``（字段 ``card_id`` + ``rank`` 3/5），是**觉醒升级**后的固有技能；
    7 个 6 位 id（``407011`` 起）全部出现在 ``skill_upgrade_speciality``
    （``scenario_id=7``），是**剧本专属升级**技能。
"""

from __future__ import annotations

from typing import Any

RARITY_LABELS: dict[str, dict[int, str]] = {
    "character": {0: "占位", 1: "★1", 2: "★2", 3: "★3"},
    "support_card": {1: "R", 2: "SR", 3: "SSR"},
    "skill": {1: "普通", 2: "稀有", 3: "固有", 4: "进化", 5: "固有"},
}

SUPPORT_CARD_TYPE_LABELS = {1: "马娘", 2: "友人", 3: "团队"}

# 协助卡效果类型（support_card_effect_table.type / support_card_unique_effect.type_N）。
# **主数据里有字典**：``text_data`` category 151 = 名称、154 = 描述，导入进
# ``catalog_support_effect_types`` 并优先使用（官方简中服措辞，例如「初始友情槽提升」
# 而不是 BWiki 的「初期羁绊计量格提升」）。
# 下面这份只在 mdb 快照缺 151（老日服库）时兜底，名称来自 BWiki「支援卡效果一览」
# （revid 35946，抓取于 2026-10-27），只覆盖 1–31。
SUPPORT_EFFECT_LABELS: dict[int, str] = {
    1: "友情加成",
    2: "干劲效果提升",
    3: "速度加成",
    4: "耐力加成",
    5: "力量加成",
    6: "毅力加成",
    7: "智力加成",
    8: "训练效果提升",
    9: "初期速度提升",
    10: "初期耐力提升",
    11: "初期力量提升",
    12: "初期毅力提升",
    13: "初期智力提升",
    14: "初期羁绊计量格提升",
    15: "赛后加成",
    16: "粉丝数加成",
    17: "启发等级提升",
    18: "启发发生率提升",
    19: "得意率提升",
    20: "速度最大值提升",
    21: "耐力最大值提升",
    22: "力量最大值提升",
    23: "毅力最大值提升",
    24: "智力最大值提升",
    25: "事件回复量提升",
    26: "事件效果提升",
    27: "失败率下降",
    28: "体力消费下降",
    29: "迷你游戏效果提升",
    30: "技能Pt加成",
    31: "智力友情回复量提升",
}

SUPPORT_EFFECT_SOURCE = (
    "简中服 master.mdb 的 text_data（151 效果名 / 154 效果说明 / 155 固有加成文案）；"
    "缺这张字典的老库回落到 BWiki 支援卡效果一览 revid 35946（2026-10-27 抓取，仅 1–31）"
)

# 条件型加成的类型下界：100 以上不是普通效果，而是「满足某条件才生效」的加成，
# 具体条件由 text_data category 155 的整句文案给出（value_N 是各类参数，含义随类型变化）
CONDITIONAL_EFFECT_MIN = 100

# 训练效果按等级逐档取值（``init`` 相当于 1 级）；-1 表示该档不提升（沿用上一档）
EFFECT_LEVELS = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50)

# 最高突破档位：0–4 突，4 突即满破
MAX_BREAKTHROUGH = 4

# 突破档位 → 卡等级上限：``support_card_limit``（简中服 2026-10-22 快照复核：
# R 20/25/30/35/40、SR 25/30/35/40/45、SSR 30/35/40/45/50，362 张卡无缺口）。
# 只有导入的 ``catalog_support_limits`` 没有该稀有度时（旧库未重新导入）才用它兜底，
# 免得详情页因为缺一张小表就整块消失。
SUPPORT_LEVEL_CAPS: dict[int, tuple[int, ...]] = {
    1: (20, 25, 30, 35, 40),
    2: (25, 30, 35, 40, 45),
    3: (30, 35, 40, 45, 50),
}

BOOLEAN_LABELS = {0: "否", 1: "是"}

TALENT_FIELDS = (
    ("talent_speed", "速度"),
    ("talent_stamina", "耐力"),
    ("talent_pow", "力量"),
    ("talent_guts", "毅力"),
    ("talent_wiz", "智力"),
)


def _with_raw_fallback(labels: dict[int, str], value: Any) -> str | None:
    if value is None:
        return None
    try:
        key = int(value)
    except (TypeError, ValueError):
        return str(value)
    return labels.get(key, f"原始值 {key}")


def support_effect_label(effect_type: Any) -> str:
    """协助卡效果类型 → 可读名称；未收录的取值回显原始编号。"""
    try:
        key = int(effect_type)
    except (TypeError, ValueError):
        return f"原始值 {effect_type}"
    return SUPPORT_EFFECT_LABELS.get(key, f"原始值 {key}")


def skill_rarity_label(rarity: Any, skill_id: Any) -> str | None:
    """技能稀有度；``rarity=6`` 要结合 id 位数才能分清觉醒 / 剧本两类。"""
    if rarity is None:
        return None
    try:
        key = int(rarity)
    except (TypeError, ValueError):
        return str(rarity)
    if key == 6:
        digits = len(str(skill_id)) if skill_id is not None else 0
        if digits == 9:
            return "觉醒"
        if digits == 6:
            return "剧本"
        return "升级"
    return RARITY_LABELS["skill"].get(key, f"原始值 {key}")


def attributes(
    kind: str, fields: dict[str, Any], item_id: int | None = None
) -> list[dict[str, str]]:
    """把一条记录的原始字段整理成有序的可读属性，供详情与列表展示。"""
    rows: list[dict[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value is None or value == "":
            return
        rows.append({"label": label, "value": str(value)})

    if kind == "skill":
        add("稀有度", skill_rarity_label(fields.get("rarity"), item_id))
    else:
        add("稀有度", _with_raw_fallback(RARITY_LABELS.get(kind, {}), fields.get("rarity")))

    if kind == "character":
        add("限定", _with_raw_fallback(BOOLEAN_LABELS, fields.get("limited")))
        talents = [
            f"{label} +{fields[key]}%"
            for key, label in TALENT_FIELDS
            if fields.get(key) not in (None, 0)
        ]
        add("天赋加成", " · ".join(talents) if talents else "无")
        add("角色 ID", fields.get("chara_id"))
    elif kind == "support_card":
        add("卡类型", _with_raw_fallback(SUPPORT_CARD_TYPE_LABELS, fields.get("support_card_type")))
        add("角色 ID", fields.get("chara_id"))
        add("技能组 ID", fields.get("skill_set_id"))
    else:
        add("技能分类", fields.get("skill_category"))
        add("标签 ID", fields.get("tag_id"))

    return rows
