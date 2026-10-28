from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.skills.importer import SkillsSourceError, import_catalog
from modules.skills.labels import attributes, need_rank_label, skill_rarity_label
from modules.skills.service import SkillsService
from modules.skills.settings import SkillsSettings

# 结构照抄真实 master.mdb：text_data.id 与 category 恒等，实体 id 在 "index" 列；
# available_skill_set 的连接键是 available_skill_set_id（不是 id），
# 协助卡提示走 single_mode_hint_gain 且只有 hint_gain_type = 0 的 hint_value_1 是技能 id。
SKILL_FIELDS = (
    "id",
    "rarity",
    "skill_category",
    "group_id",
    "tag_id",
    "group_rate",
    "grade_value",
    "icon_id",
    "disp_order",
    "plate_type",
    "is_general_skill",
    "activate_lot",
    "priority",
    "potential_per_default",
    "exp_type",
    "unique_skill_id_1",
    "unique_skill_id_2",
    "start_date",
    "end_date",
    "precondition_1",
    "condition_1",
    "float_ability_time_1",
    "ability_time_usage_1",
    "float_cooldown_time_1",
    "ability_type_1_1",
    "ability_value_usage_1_1",
    "ability_value_level_usage_1_1",
    "float_ability_value_1_1",
    "target_type_1_1",
    "target_value_1_1",
    "additional_activate_type_1_1",
    "ability_type_1_2",
    "ability_value_usage_1_2",
    "ability_value_level_usage_1_2",
    "float_ability_value_1_2",
    "target_type_1_2",
    "target_value_1_2",
    "additional_activate_type_1_2",
    "ability_type_1_3",
    "ability_value_usage_1_3",
    "ability_value_level_usage_1_3",
    "float_ability_value_1_3",
    "target_type_1_3",
    "target_value_1_3",
    "additional_activate_type_1_3",
    "precondition_2",
    "condition_2",
    "float_ability_time_2",
    "ability_time_usage_2",
    "float_cooldown_time_2",
    "ability_type_2_1",
    "ability_value_usage_2_1",
    "ability_value_level_usage_2_1",
    "float_ability_value_2_1",
    "target_type_2_1",
    "target_value_2_1",
    "additional_activate_type_2_1",
    "ability_type_2_2",
    "ability_value_usage_2_2",
    "ability_value_level_usage_2_2",
    "float_ability_value_2_2",
    "target_type_2_2",
    "target_value_2_2",
    "additional_activate_type_2_2",
    "ability_type_2_3",
    "ability_value_usage_2_3",
    "ability_value_level_usage_2_3",
    "float_ability_value_2_3",
    "target_type_2_3",
    "target_value_2_3",
    "additional_activate_type_2_3",
)

TEXT_FIELDS = {"tag_id", "precondition_1", "condition_1", "precondition_2", "condition_2"}
REAL_FIELDS = {
    "float_ability_time_1",
    "float_ability_time_2",
    "float_cooldown_time_1",
    "float_cooldown_time_2",
    "float_ability_value_1_1",
    "float_ability_value_1_2",
    "float_ability_value_1_3",
    "float_ability_value_2_1",
    "float_ability_value_2_2",
    "float_ability_value_2_3",
    "target_value_1_1",
    "target_value_1_2",
    "target_value_1_3",
    "target_value_2_1",
    "target_value_2_2",
    "target_value_2_3",
}


def _ddl_type(field: str) -> str:
    if field in TEXT_FIELDS:
        return "TEXT"
    return "REAL" if field in REAL_FIELDS else "INTEGER"


SKILL_DDL = ",\n            ".join(
    f"{field} {_ddl_type(field)}" + (" PRIMARY KEY" if field == "id" else "")
    for field in SKILL_FIELDS
)


def _insert_skill(conn: sqlite3.Connection, skill_id: int, **overrides: object) -> None:
    values: dict[str, object] = {field: 0 for field in SKILL_FIELDS}
    values.update({"id": skill_id, "precondition_1": "", "condition_1": ""})
    values.update({"precondition_2": "", "condition_2": ""})
    values.update(overrides)
    columns = ", ".join(values)
    placeholders = ", ".join("?" * len(values))
    conn.execute(
        f"INSERT INTO skill_data ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def build_mdb(path: Path, *, drop_optional: bool = False) -> Path:
    """最小 master.mdb：2 个技能（普通 / 剧本进化）、马娘与协助卡两条反查链路。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE text_data (
            id INTEGER NOT NULL,
            category INTEGER NOT NULL,
            "index" INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (category, "index")
        );
        CREATE TABLE skill_data (
            {SKILL_DDL}
        );
        CREATE TABLE card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL,
            available_skill_set_id INTEGER NOT NULL
        );
        CREATE TABLE available_skill_set (
            id INTEGER PRIMARY KEY,
            available_skill_set_id INTEGER NOT NULL,
            skill_id INTEGER NOT NULL,
            need_rank INTEGER NOT NULL
        );
        CREATE TABLE support_card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL,
            skill_set_id INTEGER NOT NULL
        );
        CREATE TABLE single_mode_hint_gain (
            id INTEGER PRIMARY KEY,
            hint_id INTEGER,
            support_card_id INTEGER,
            hint_group INTEGER,
            hint_gain_type INTEGER,
            hint_value_1 INTEGER,
            hint_value_2 INTEGER
        );
        CREATE TABLE skill_upgrade_speciality (
            id INTEGER PRIMARY KEY,
            scenario_id INTEGER,
            base_skill_id INTEGER,
            skill_id INTEGER,
            start_date INTEGER
        );
        CREATE TABLE skill_upgrade_description (
            id INTEGER PRIMARY KEY,
            card_id INTEGER,
            rank INTEGER,
            skill_id INTEGER,
            start_date INTEGER
        );
        """
    )
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        [
            (5, 5, 2001, "[日本第一的赛马娘]"),
            (6, 6, 1001, "特别周"),
            (47, 47, 200462, "加快步伐"),
            (48, 48, 200462, "在赛事中期赶超成功时\\n速度略微提升"),
            (47, 47, 407011, "闪耀的较量"),
            (48, 48, 407011, "临近赛事中期时\\n速度大幅提升<英里/中距离>"),
            (76, 76, 30001, "[先行花开]"),
            (47, 47, 100101111, "饥肠辘辘大将"),
        ],
    )
    # 200462：单槽位单效果；407011：进化技能（无自身槽位）
    _insert_skill(
        conn,
        200462,
        rarity=1,
        skill_category=2,
        group_id=20046,
        tag_id="302/401",
        group_rate=1,
        icon_id=20011,
        disp_order=2160,
        is_general_skill=1,
        activate_lot=1,
        potential_per_default=10,
        condition_1="phase==1&change_order_onetime<0",
        float_ability_time_1=18000.0,
        ability_time_usage_1=1,
        float_cooldown_time_1=300000.0,
        ability_type_1_1=27,
        ability_value_usage_1_1=1,
        ability_value_level_usage_1_1=1,
        float_ability_value_1_1=1500.0,
        target_type_1_1=1,
    )
    _insert_skill(conn, 407011, rarity=6, skill_category=1, unique_skill_id_1=0)
    _insert_skill(conn, 100101111, rarity=6, skill_category=5)
    conn.executemany(
        "INSERT INTO card_data (id, chara_id, available_skill_set_id) VALUES (?, ?, ?)",
        [(2001, 1001, 5001)],
    )
    conn.executemany(
        "INSERT INTO available_skill_set (id, available_skill_set_id, skill_id, need_rank) "
        "VALUES (?, ?, ?, ?)",
        [(1, 5001, 200462, 0), (2, 5001, 407011, 3)],
    )
    conn.execute(
        "INSERT INTO support_card_data (id, chara_id, skill_set_id) VALUES (30001, 1001, 9081001)"
    )
    conn.executemany(
        "INSERT INTO single_mode_hint_gain (id, hint_id, support_card_id, hint_group, "
        "hint_gain_type, hint_value_1, hint_value_2) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(1, 1, 30001, 2, 0, 200462, 1), (2, 2, 30001, 3, 1, 4, 1)],
    )
    conn.execute(
        "INSERT INTO skill_upgrade_speciality "
        "(id, scenario_id, base_skill_id, skill_id, start_date) "
        "VALUES (407011, 7, 200462, 407011, 0)"
    )
    conn.execute(
        "INSERT INTO skill_upgrade_description (id, card_id, rank, skill_id, start_date) "
        "VALUES (1, 2001, 3, 100101111, 0)"
    )
    if drop_optional:
        for table in (
            "available_skill_set",
            "support_card_data",
            "single_mode_hint_gain",
            "skill_upgrade_speciality",
            "skill_upgrade_description",
        ):
            conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()
    return path


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
def service(db: Database, settings: Settings) -> SkillsService:
    return SkillsService(db, settings, SkillsSettings())


def test_import_counts_and_effects(service: SkillsService, mdb: Path) -> None:
    summary = import_catalog(service.db, mdb)

    assert summary.skills == 3
    assert summary.slots == 1, "407011 / 100101111 没有槽位数据，不应凭空生成"
    assert summary.effects == 1
    assert summary.characters == 2
    assert summary.support_cards == 1, "hint_gain_type != 0 的行不是技能 id，不能导入"
    assert summary.upgrades == 2
    assert summary.skipped == {}
    assert service.status()["counts"]["skills"] == 3


def test_detail_exposes_conditions_and_raw_parameters(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    detail = service.detail(200462)

    assert detail is not None
    assert detail["skill"]["name"] == "加快步伐"
    assert detail["skill"]["description"] == "在赛事中期赶超成功时\n速度略微提升"
    slot = detail["slots"][0]
    assert slot["precondition"] == ""
    assert slot["condition"] == "phase==1&change_order_onetime<0"
    assert slot["ability_time"] == 18000.0
    assert slot["cooldown"] == 300000.0
    effect = slot["effects"][0]
    # 没有字典表：只回显原始编号，不臆造中文标签
    assert effect["ability_type"] == 27
    assert effect["ability_type_label"] == "原始值 27"
    assert effect["target_type_label"] == "原始值 1"
    assert effect["value"] == 1500.0
    assert any("没有字典表" in note for note in detail["notes"])


def test_detail_reverse_looks_up_characters_and_support_cards(
    service: SkillsService, mdb: Path
) -> None:
    import_catalog(service.db, mdb)

    detail = service.detail(200462)

    # available_skill_set 的连接键是 available_skill_set_id，用 id 连会匹配 0 行
    assert [item["card_id"] for item in detail["characters"]] == [2001]
    assert detail["characters"][0]["name"] == "特别周"
    assert detail["characters"][0]["title"] == "[日本第一的赛马娘]"
    assert detail["characters"][0]["need_rank"] == 0
    assert detail["characters"][0]["need_rank_label"] == "初始"

    hints = detail["support_cards"]["hint"]
    assert [item["support_card_id"] for item in hints] == [30001]
    assert hints[0]["hint_group"] == 2
    # skill_set 路径经核实退化，不作为逐卡反查依据
    assert detail["support_cards"]["skill_set"] == []


def test_detail_links_upgrade_before_and_after(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    evolved = service.detail(407011)
    assert evolved is not None
    entry = evolved["upgrades"]["evolved_from"][0]
    assert entry["source"] == "speciality"
    assert entry["source_label"] == "剧本进化"
    assert entry["base_skill_id"] == 200462
    assert entry["base_name"] == "加快步伐"

    base = service.detail(200462)
    assert base is not None
    assert [item["skill_id"] for item in base["upgrades"]["evolves_to"]] == [407011]


def test_awakening_upgrade_keeps_card_and_rank(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    detail = service.detail(100101111)

    assert detail is not None
    entry = detail["upgrades"]["evolved_from"][0]
    assert entry["source"] == "awakening"
    assert entry["source_label"] == "觉醒升级"
    assert entry["card_id"] == 2001
    assert entry["card_title"] == "[日本第一的赛马娘]"
    assert entry["rank"] == 3
    # 这张表不记录进化前的技能，留空而不是猜
    assert entry["base_skill_id"] is None


def test_detail_returns_none_for_unknown_skill(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    assert service.detail(9999999) is None


def test_search_ranks_exact_name_first(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    result = service.search(query="加快步伐")

    assert result["items"][0]["skill_id"] == 200462
    assert result["items"][0]["rarity_label"] == "普通"
    assert result["items"][0]["character_count"] == 1
    assert result["items"][0]["support_card_count"] == 1


def test_with_source_only_filters_skills_without_owner(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    result = service.search(with_source_only=True)

    # 100101111 只出现在觉醒表，没有马娘 / 协助卡来源
    assert {item["skill_id"] for item in result["items"]} == {200462, 407011}


def test_missing_optional_tables_are_reported_not_fatal(
    service: SkillsService, settings: Settings
) -> None:
    mdb = build_mdb(settings.mdb_path, drop_optional=True)

    summary = import_catalog(service.db, mdb)

    assert summary.skills == 3
    assert summary.characters == 0
    assert summary.support_cards == 0
    assert set(summary.skipped) == {
        "skill_characters",
        "skill_support_cards",
        "skill_upgrades_speciality",
        "skill_upgrades_awakening",
    }


def test_missing_required_table_raises_chinese_error(settings: Settings, tmp_path: Path) -> None:
    broken = tmp_path / "master.mdb"
    conn = sqlite3.connect(broken)
    conn.execute(
        'CREATE TABLE text_data (id INTEGER, category INTEGER, "index" INTEGER, text TEXT)'
    )
    conn.commit()
    conn.close()

    db = Database(settings.db_path)
    try:
        with pytest.raises(SkillsSourceError) as excinfo:
            import_catalog(db, broken)
    finally:
        db.close()

    assert "skill_data" in str(excinfo.value)


def test_refresh_skips_when_not_stale(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)

    result = service.refresh()

    assert result["imported"] is False
    assert "未重复导入" in result["reason"]
    assert service.status()["stale"] is False


def test_refresh_reimports_when_mdb_is_newer(service: SkillsService, mdb: Path) -> None:
    import_catalog(service.db, mdb)
    # 把修改时间往后推，模拟同步模块刚导入过一版新快照
    stamp = mdb.stat().st_mtime + 60
    os.utime(mdb, (stamp, stamp))

    assert service.status()["stale"] is True
    result = service.refresh()

    assert result["imported"] is True
    assert result["counts"]["skills"] == 3
    assert service.status()["stale"] is False


def test_ensure_catalog_imports_once(service: SkillsService, settings: Settings) -> None:
    build_mdb(settings.mdb_path)

    first = service.ensure_catalog()
    second = service.ensure_catalog()

    assert first is not None and first.skills == 3
    assert second is None


def test_api_serves_skill_list_and_detail(db: Database, settings: Settings, mdb: Path) -> None:
    import_catalog(db, mdb)

    with TestClient(create_app(settings)) as client:
        listing = client.get("/api/skills/skills", params={"q": "加快"}).json()
        assert listing["total"] == 3
        assert listing["items"][0]["skill_id"] == 200462

        detail = client.get("/api/skills/skills/200462").json()
        assert detail["skill"]["name"] == "加快步伐"
        assert detail["slots"][0]["condition"] == "phase==1&change_order_onetime<0"

        missing = client.get("/api/skills/skills/9999999")
        assert missing.status_code == 404

        status = client.get("/api/skills/status").json()
        assert status["counts"]["skills"] == 3
        assert status["stale"] is False
        # 本模块的导入记录在 meta 里，与 catalog 的数据版本各自独立
        assert status["catalog"]["skills"] == 3
        assert status["catalog"]["source"].startswith("master.mdb")


def test_labels_keep_unknown_enums_raw() -> None:
    assert skill_rarity_label(6, 100101111) == "觉醒"
    assert skill_rarity_label(6, 407011) == "剧本"
    assert skill_rarity_label(9, 1) == "原始值 9"
    assert need_rank_label(0) == "初始"
    assert need_rank_label(5) == "觉醒 5"

    values = {row["label"]: row["value"] for row in attributes({"rarity": 1, "skill_category": 2})}
    assert values["稀有度"] == "普通"
    assert values["技能分类"] == "原始值 2"
