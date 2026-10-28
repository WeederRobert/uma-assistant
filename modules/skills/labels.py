"""技能枚举值到可读标签的映射。

只收录在真实 ``master.mdb`` 上核对过的取值；没有字典的枚举一律按「原始值 N」显示，
不猜测含义。这一点对技能尤其重要：``ability_type`` / ``target_type`` /
``skill_category`` 在主数据里**都没有字典表**（``text_data`` 里也没有对应类别），
所以面板只回显编号，并明确标注「无字典」。
"""

from __future__ import annotations

from typing import Any

# skill_data.rarity 的 1~5；6 分两类，见 :func:`skill_rarity_label`
RARITY_LABELS: dict[int, str] = {
    1: "普通",
    2: "稀有",
    3: "固有",
    4: "进化",
    5: "固有",
}

# 主数据里没有这些字段的字典表，接口与面板只回显编号
NO_DICT_NOTE = "master.mdb 没有该字段的字典表，这里只回显原始编号，不代表已确认的含义"

BOOLEAN_LABELS = {0: "否", 1: "是"}


def raw_label(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"原始值 {int(value)}"
    except (TypeError, ValueError):
        return str(value)


def skill_rarity_label(rarity: Any, skill_id: Any) -> str | None:
    """技能稀有度；``rarity=6`` 要结合 id 位数才能分清觉醒 / 剧本两类。

    依据（已在简中服 2.0.2 快照上复核）：338 个 9 位 id 全部出现在
    ``skill_upgrade_description``（觉醒升级），7 个 6 位 id 全部出现在
    ``skill_upgrade_speciality``（``scenario_id=7`` 的剧本升级）。
    """
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
    return RARITY_LABELS.get(key, f"原始值 {key}")


def need_rank_label(need_rank: Any) -> str | None:
    """``available_skill_set.need_rank`` 是解锁需要的觉醒星级，0 表示初始就可用。"""
    if need_rank is None:
        return None
    try:
        key = int(need_rank)
    except (TypeError, ValueError):
        return str(need_rank)
    return "初始" if key == 0 else f"觉醒 {key}"


def upgrade_source_label(source: str | None) -> str:
    return {"speciality": "剧本进化", "awakening": "觉醒升级"}.get(source or "", source or "")


def attributes(fields: dict[str, Any], skill_id: int | None = None) -> list[dict[str, str]]:
    """把一条技能记录的标量字段整理成有序的可读属性。"""
    rows: list[dict[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value is None or value == "":
            return
        rows.append({"label": label, "value": str(value)})

    add("稀有度", skill_rarity_label(fields.get("rarity"), skill_id or fields.get("skill_id")))
    add("技能分类", raw_label(fields.get("skill_category")))
    add("通用技能", BOOLEAN_LABELS.get(fields.get("is_general_skill")))
    add("标签 ID", fields.get("tag_id"))
    add("分组 ID", fields.get("group_id"))
    add("分组权重", fields.get("group_rate"))
    add("消耗（activate_lot）", fields.get("activate_lot"))
    add("优先级", fields.get("priority"))
    add("等级默认潜力值", fields.get("potential_per_default"))
    add("经验类型（exp_type）", raw_label(fields.get("exp_type")))
    add("图标 ID", fields.get("icon_id"))
    add("显示顺序", fields.get("disp_order"))
    return rows
