"""技能模块的业务逻辑：导入、列表与详情。

口径与限制全部写在这里，接口与面板只做展示：

- 效果参数（``ability_type`` / ``float_ability_value`` / ``target_type``）**没有字典表**，
  只回显原始编号；``skill_category`` 同理。
- ``precondition`` / ``condition`` 是引擎表达式（如 ``phase==1&change_order_within_top<1``），
  原样展示，不翻译成自然语言。
- ``float_ability_time`` / ``float_cooldown_time`` / ``float_ability_value`` 在主数据里
  没有声明单位，只显示原始值，不做换算。
- 反查马娘用 ``card_data.available_skill_set_id``，反查协助卡提示技能用
  ``single_mode_hint_gain``（``hint_gain_type = 0``）；协助卡「技能组」按
  ``support_card_data.skill_set_id / 1000`` 解析，粒度到角色组，不是单卡。
- 技能数据与 ``catalog`` 模块同源（同一份 ``master.mdb``），但本模块独立建表，
  不复用 ``catalog_skills``，以免版本推进时两边列结构互相牵制。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.kernel.config import Settings
from app.kernel.database import Database
from app.kernel.dataversion import read_data_version

from . import labels, store
from .importer import CatalogSummary, SkillsSourceError, import_catalog
from .schema import ensure_schema

NO_DICT_NOTE = (
    "`ability_type` / `target_type` / `skill_category` 在 master.mdb 里没有字典表，"
    "面板只回显原始编号，不代表已确认的中文含义。"
)
EXPRESSION_NOTE = (
    "`precondition` / `condition` 是游戏引擎的条件表达式，这里原样展示，不翻译成自然语言。"
)
UNIT_NOTE = (
    "`float_ability_time` / `float_cooldown_time` / `float_ability_value` 没有官方单位说明，"
    "只显示原始值。"
)
SUPPORT_CARD_NOTE = (
    "「哪些协助卡给这个技能」来自 `single_mode_hint_gain`（`hint_gain_type = 0` 时 "
    "`hint_value_1` 即技能 id），是逐卡精确结果。"
    "`support_card_data.skill_set_id / 1000` 也能连到 `skill_set`，但 362 张卡只解析出 2 个组、"
    "合计 7 个技能且全部被提示表覆盖，粒度不是单卡，因此不作为反查依据。"
)
UPGRADE_NOTE = (
    "`skill_upgrade_speciality` 给出剧本进化的前后技能；`skill_upgrade_description` 只给出"
    "「哪张马娘卡在几觉解锁哪个技能」，不记录进化前的技能，因此这一类的「进化前」留空。"
)


class SkillsService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        options: Any | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.options = options
        self.logger = logger or logging.getLogger(__name__)
        ensure_schema(db)

    # ------------------------------------------------------------------ 来源
    @property
    def mdb_path(self) -> Path:
        override = getattr(self.options, "mdb_path", None) if self.options else None
        return Path(override) if override else self.settings.mdb_path

    @property
    def search_limit(self) -> int:
        return int(getattr(self.options, "search_limit", 50) or 50)

    # ------------------------------------------------------------------ 导入
    def ensure_catalog(self) -> CatalogSummary | None:
        """本地库为空、或 ``master.mdb`` 比上次导入更新时补一次导入。"""
        mdb = self.mdb_path
        if not mdb.exists():
            return None
        source = store.catalog_source(self.db)
        if store.count_skills(self.db) and not self._stale(mdb, source):
            return None
        try:
            return import_catalog(self.db, mdb)
        except SkillsSourceError as exc:
            self.logger.warning("导入技能数据失败：%s", exc)
            return None

    @staticmethod
    def _stale(mdb: Path, source: dict[str, Any] | None) -> bool:
        """比对 mdb 修改时间，避免同步导入新快照后技能表仍是旧版。"""
        if not source:
            return True
        recorded = source.get("mdb_mtime")
        if recorded is None or source.get("mdb_path") != str(mdb.resolve()):
            return True
        try:
            return mdb.stat().st_mtime > float(recorded) + 1
        except (OSError, TypeError, ValueError):
            return True

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        mdb = self.mdb_path
        if not mdb.exists():
            raise SkillsSourceError(
                f"找不到 master.mdb：{mdb}。先在「数据同步」里导入一次客户端数据。"
            )
        if not force and not self._stale(mdb, store.catalog_source(self.db)):
            return {
                "imported": False,
                "reason": "本地技能数据已对应当前 master.mdb，未重复导入",
                "counts": store.name_counts(self.db),
            }
        summary = import_catalog(self.db, mdb)
        return {
            "imported": True,
            "summary": summary.as_dict(),
            "counts": store.name_counts(self.db),
        }

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        mdb = self.mdb_path
        source = store.catalog_source(self.db)
        return {
            "counts": store.name_counts(self.db),
            "facets": store.facets(self.db),
            "catalog": source,
            "stale": bool(store.count_skills(self.db)) and self._stale(mdb, source),
            "mdb": {
                "path": str(mdb),
                "exists": mdb.exists(),
                "size_mb": round(mdb.stat().st_size / 1024 / 1024, 1) if mdb.exists() else None,
            },
            "data_version": read_data_version(self.db).as_dict(),
            "notes": [NO_DICT_NOTE, EXPRESSION_NOTE, UNIT_NOTE],
        }

    # ------------------------------------------------------------------ 列表
    def search(
        self,
        *,
        query: str | None = None,
        skill_category: int | None = None,
        rarity: int | None = None,
        with_source_only: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(limit or self.search_limit, 200))
        rows = store.list_skills(
            self.db,
            query=(query or "").strip() or None,
            skill_category=skill_category,
            rarity=rarity,
            with_source_only=with_source_only,
            limit=limit,
        )
        items = [self._summary_row(row) for row in rows]
        return {
            "query": query or "",
            "count": len(items),
            "items": items,
            "total": store.count_skills(self.db),
        }

    def _summary_row(self, row: Any) -> dict[str, Any]:
        fields = dict(row)
        skill_id = int(fields["skill_id"])
        return {
            "skill_id": skill_id,
            "name": fields["name"],
            "description": _clean(fields.get("description")),
            "rarity": fields.get("rarity"),
            "rarity_label": labels.skill_rarity_label(fields.get("rarity"), skill_id),
            "skill_category": fields.get("skill_category"),
            "group_id": fields.get("group_id"),
            "character_count": int(fields.get("character_count") or 0),
            "support_card_count": int(fields.get("support_card_count") or 0),
        }

    # ------------------------------------------------------------------ 详情
    def detail(self, skill_id: int) -> dict[str, Any] | None:
        row = store.get_skill(self.db, skill_id)
        if row is None:
            return None
        data = dict(row)

        effects_by_slot: dict[int, list[dict[str, Any]]] = {}
        for effect in store.list_effects(self.db, skill_id):
            effects_by_slot.setdefault(int(effect["slot"]), []).append(self._effect(effect))

        slots = [
            {
                "slot": int(slot["slot"]),
                "precondition": slot["precondition"] or "",
                "condition": slot["condition"] or "",
                "ability_time": slot["ability_time"],
                "ability_time_usage": slot["ability_time_usage"],
                "cooldown": slot["cooldown"],
                "effects": effects_by_slot.get(int(slot["slot"]), []),
            }
            for slot in store.list_slots(self.db, skill_id)
        ]

        characters = [
            {
                "card_id": int(item["card_id"]),
                "name": item["name"],
                "title": item["title"],
                "need_rank": item["need_rank"],
                "need_rank_label": labels.need_rank_label(item["need_rank"]),
            }
            for item in store.list_characters(self.db, skill_id)
        ]

        hints: list[dict[str, Any]] = []
        skill_sets: list[dict[str, Any]] = []
        for item in store.list_support_cards(self.db, skill_id):
            entry = {
                "support_card_id": int(item["support_card_id"]),
                "name": item["name"],
                "title": item["title"],
                "hint_group": item["hint_group"],
            }
            if item["source"] == "hint":
                hints.append(entry)
            else:
                skill_sets.append(entry)
        return {
            "skill": {
                "skill_id": skill_id,
                "name": data["name"],
                "description": _clean(data.get("description")),
                "rarity": data.get("rarity"),
                "skill_category": data.get("skill_category"),
                "tag_id": data.get("tag_id"),
                "group_id": data.get("group_id"),
                "group_rate": data.get("group_rate"),
                "grade_value": data.get("grade_value"),
                "is_general_skill": data.get("is_general_skill"),
                "activate_lot": data.get("activate_lot"),
                "priority": data.get("priority"),
                "potential_per_default": data.get("potential_per_default"),
                "exp_type": data.get("exp_type"),
                "icon_id": data.get("icon_id"),
                "disp_order": data.get("disp_order"),
                "plate_type": data.get("plate_type"),
                "start_date": data.get("start_date"),
                "end_date": data.get("end_date"),
                "unique_skill_id_1": data.get("unique_skill_id_1"),
                "unique_skill_id_2": data.get("unique_skill_id_2"),
            },
            "attributes": labels.attributes(data, skill_id),
            "slots": slots,
            "characters": characters,
            "support_cards": {"hint": hints, "skill_set": skill_sets},
            "upgrades": self._upgrades(skill_id, data.get("unique_skill_id_1")),
            "notes": [NO_DICT_NOTE, EXPRESSION_NOTE, UNIT_NOTE, SUPPORT_CARD_NOTE, UPGRADE_NOTE],
            "data_version": read_data_version(self.db).as_dict(),
        }

    @staticmethod
    def _effect(row: Any) -> dict[str, Any]:
        return {
            "seq": int(row["seq"]),
            "ability_type": row["ability_type"],
            "ability_type_label": labels.raw_label(row["ability_type"]),
            "value_usage": row["value_usage"],
            "value_level_usage": row["value_level_usage"],
            "value": row["value"],
            "target_type": row["target_type"],
            "target_type_label": labels.raw_label(row["target_type"]),
            "target_value": row["target_value"],
            "activate_type": row["activate_type"],
        }

    def _upgrades(self, skill_id: int, unique_id: Any) -> dict[str, Any]:
        entries = store.list_upgrades(self.db, skill_id)
        derived = store.list_derivatives(self.db, skill_id)

        unique_note = None
        if unique_id:
            unique_row = store.get_skill(self.db, int(unique_id))
            unique_note = {
                "unique_skill_id_1": int(unique_id),
                "name": unique_row["name"] if unique_row is not None else None,
            }

        def describe(row: Any) -> dict[str, Any]:
            base_id = row["base_skill_id"]
            base_name = None
            if base_id:
                base_row = store.get_skill(self.db, int(base_id))
                base_name = base_row["name"] if base_row is not None else None
            return {
                "skill_id": int(row["skill_id"]),
                "source": row["source"],
                "source_label": labels.upgrade_source_label(row["source"]),
                "base_skill_id": int(base_id) if base_id else None,
                "base_name": base_name,
                "scenario_id": row["scenario_id"],
                "card_id": int(row["card_id"]) if row["card_id"] else None,
                "card_title": row["card_title"],
                "rank": row["rank"],
            }

        return {
            "evolved_from": [describe(row) for row in entries],
            "evolves_to": [describe(row) for row in derived],
            "unique_skill_id_1": unique_note,
        }


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    # master.mdb 的描述里用字面量 \n 表示换行
    return str(value).replace("\\n", "\n")
