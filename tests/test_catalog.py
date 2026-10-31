from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.kernel.config import Settings
from app.kernel.database import Database
from app.main import create_app
from modules.catalog.importer import MasterMdbError, import_master_mdb
from modules.catalog.labels import attributes
from modules.catalog.service import CatalogService


def _build_mdb(
    path: Path, *, include_second_skill: bool = True, drop_columns: bool = False
) -> None:
    """构造一份结构与真实 master.mdb 一致的最小库。

    真实库里 ``text_data.id`` 与 ``category`` 恒等，实体 id 存在 ``"index"`` 列；
    这里刻意复刻这一点，否则用 ``id`` 做 JOIN 的写法也能通过测试。
    """
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    talent_guts = "talent_guts INTEGER," if not drop_columns else ""
    tag_id = "tag_id TEXT," if not drop_columns else ""
    conn.executescript(
        f"""
        CREATE TABLE text_data (
            id INTEGER NOT NULL,
            category INTEGER NOT NULL,
            "index" INTEGER NOT NULL,
            text TEXT NOT NULL,
            PRIMARY KEY (category, "index")
        );
        CREATE TABLE card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL,
            default_rarity INTEGER NOT NULL,
            limited_chara INTEGER NOT NULL,
            available_skill_set_id INTEGER,
            talent_speed INTEGER,
            talent_stamina INTEGER,
            talent_pow INTEGER,
            {talent_guts}
            talent_wiz INTEGER
        );
        CREATE TABLE support_card_data (
            id INTEGER PRIMARY KEY,
            chara_id INTEGER NOT NULL,
            rarity INTEGER NOT NULL,
            support_card_type INTEGER NOT NULL,
            skill_set_id INTEGER NOT NULL
        );
        CREATE TABLE skill_data (
            id INTEGER PRIMARY KEY,
            rarity INTEGER NOT NULL,
            skill_category INTEGER NOT NULL,
            {tag_id}
            dummy_extra INTEGER
        );
        CREATE TABLE available_skill_set (
            id INTEGER PRIMARY KEY,
            available_skill_set_id INTEGER NOT NULL,
            skill_id INTEGER NOT NULL,
            need_rank INTEGER NOT NULL
        );
        CREATE TABLE support_card_effect_table (
            id INTEGER NOT NULL,
            type INTEGER NOT NULL,
            init INTEGER,
            limit_lv5 INTEGER,
            limit_lv10 INTEGER,
            limit_lv15 INTEGER,
            limit_lv20 INTEGER,
            limit_lv25 INTEGER,
            limit_lv30 INTEGER,
            limit_lv35 INTEGER,
            limit_lv40 INTEGER,
            limit_lv45 INTEGER,
            limit_lv50 INTEGER
        );
        CREATE TABLE support_card_unique_effect (
            id INTEGER NOT NULL,
            lv INTEGER,
            type_0 INTEGER,
            value_0 INTEGER,
            type_1 INTEGER,
            value_1 INTEGER
        );
        CREATE TABLE support_card_limit (
            rarity INTEGER PRIMARY KEY,
            limit_0 INTEGER NOT NULL,
            limit_1 INTEGER NOT NULL,
            limit_2 INTEGER NOT NULL,
            limit_3 INTEGER NOT NULL,
            limit_4 INTEGER NOT NULL
        );
        CREATE TABLE single_mode_hint_gain (
            id INTEGER PRIMARY KEY,
            support_card_id INTEGER NOT NULL,
            hint_gain_type INTEGER NOT NULL,
            hint_value_1 INTEGER
        );
        """
    )
    # (category, index, text)；id 与 category 相同，与真实库一致
    texts = [
        (5, 2001, "日本第一的赛马娘"),
        (6, 1001, "特别周"),
        (6, 1002, "无声铃鹿"),
        (47, 200001, "末脚"),
        (48, 200001, "在最终直线提升速度"),
        (76, 30001, "先行花开"),
        # 效果类型字典：官方措辞与 BWiki 的兜底名不同（14 → 初始友情槽提升）
        (151, 1, "友情加成"),
        (151, 8, "训练效果提升"),
        (151, 14, "初始友情槽提升"),
        (154, 1, "提升友情训练时的效果"),
        # 固有加成文案：条件型加成把发动条件写在句子里（30002）
        (155, 30002, "友情槽达到80时\\n训练效果提升"),
    ]
    if include_second_skill:
        texts.append((47, 200002, "逃亡者"))
        texts.append((48, 200002, "在序盘容易取得领先\\n速度提升"))
    conn.executemany(
        'INSERT INTO text_data (id, category, "index", text) VALUES (?, ?, ?, ?)',
        [(category, category, index, text) for category, index, text in texts],
    )
    conn.executemany(
        "INSERT INTO card_data (id, chara_id, default_rarity, limited_chara, "
        "available_skill_set_id, talent_speed, talent_stamina, talent_pow, talent_wiz) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (2001, 1001, 3, 0, 2001, 10, 0, 0, 0),
            (2002, 1002, 3, 1, 2002, 0, 0, 10, 0),
        ],
    )
    conn.execute(
        "INSERT INTO support_card_data (id, chara_id, rarity, support_card_type, skill_set_id) "
        "VALUES (30001, 1002, 3, 2, 100)"
    )
    # 30002：条件型固有加成（type 101），没有训练效果数据
    conn.execute(
        "INSERT INTO support_card_data (id, chara_id, rarity, support_card_type, skill_set_id) "
        "VALUES (30002, 1001, 3, 1, 101)"
    )
    # card_data.available_skill_set_id 是这张卡自己的 id（真实库里 100101 → 100101）
    conn.executemany(
        "INSERT INTO available_skill_set (id, available_skill_set_id, skill_id, need_rank) "
        "VALUES (?, ?, ?, ?)",
        [
            (1, 2001, 200001, 0),
            (2, 2001, 200002, 3),
            # 干扰行：id 与 available_skill_set_id 不同，用 id 做 JOIN 会连错
            (3, 2002, 200001, 0),
        ],
    )
    conn.executemany(
        "INSERT INTO support_card_effect_table (id, type, init, limit_lv5, limit_lv10, "
        "limit_lv15, limit_lv20, limit_lv25, limit_lv30, limit_lv35, limit_lv40, limit_lv45, "
        "limit_lv50) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (30001, 1, 5, -1, -1, 10, 10, -1, -1, 15, -1, -1, -1),
            (30001, 8, -1, 5, -1, 15, 15, -1, -1, 20, -1, -1, -1),
            # 官方字典与 BWiki 兜底名不一致的类型，用来验证字典优先
            (30001, 14, -1, 10, -1, 15, 15, -1, -1, 20, -1, -1, -1),
            # 未收录的效果类型：必须回显原始编号而不是编名字
            (30001, 99, 3, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
        ],
    )
    conn.executemany(
        "INSERT INTO support_card_unique_effect (id, lv, type_0, value_0, type_1, value_1) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(30001, 25, 2, 15, 99, 7), (30002, 30, 101, 80, 0, 0)],
    )
    # 等级上限：0–4 突（真实 master.mdb 的 support_card_limit）
    conn.executemany(
        "INSERT INTO support_card_limit (rarity, limit_0, limit_1, limit_2, limit_3, limit_4) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, 20, 25, 30, 35, 40),
            (2, 25, 30, 35, 40, 45),
            (3, 30, 35, 40, 45, 50),
        ],
    )
    conn.executemany(
        "INSERT INTO single_mode_hint_gain (id, support_card_id, hint_gain_type, hint_value_1) "
        "VALUES (?, ?, ?, ?)",
        [
            (1, 30001, 0, 200002),
            # hint_gain_type = 1 的 hint_value_1 不是 skill_id，不能混用
            (2, 30001, 1, 3),
        ],
    )
    skills = [(200001, 1, 1)]
    if include_second_skill:
        skills.append((200002, 2, 2))
    conn.executemany("INSERT INTO skill_data (id, rarity, skill_category) VALUES (?, ?, ?)", skills)
    conn.commit()
    conn.close()


def test_import_and_query(settings: Settings, tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)

    db = Database(settings.db_path)
    try:
        summary = import_master_mdb(db, mdb, game_version="1.15.5")
    finally:
        db.close()

    assert summary.characters == 2
    assert summary.support_cards == 2
    assert summary.skills == 2
    assert summary.character_skills == 3, "2001 有 2 个技能，2002 有 1 个"
    assert summary.support_effects == 4
    assert summary.support_limits == 3, "R / SR / SSR 各一行"
    assert summary.support_effect_types == 3, "text_data 151 的三条名称"
    assert summary.support_unique == 3, "30001 两个槽位 + 30002 一个（type_1 = 0 是空槽）"
    assert summary.support_unique_text == 1, "只有 30002 有 category 155 文案"
    assert summary.support_skills == 1, "只取 hint_gain_type = 0"
    assert summary.data_version == "mdb-1.15.5"
    assert summary.skipped == {}

    with TestClient(create_app(settings)) as client:
        info = client.get("/api/system/info").json()
        assert info["data"]["known"] is True
        assert info["data"]["game_version"] == "1.15.5"
        assert info["data"]["datasets"]["characters"] == 2

        characters = client.get("/api/catalog/search", params={"q": "特别"}).json()["items"]
        assert any(item["kind"] == "character" and item["name"] == "特别周" for item in characters)
        assert characters[0]["fields"]["talent_speed"] == 10

        support_cards = client.get(
            "/api/catalog/search", params={"q": "先行", "kind": "support_card"}
        ).json()["items"]
        assert len(support_cards) == 1
        assert support_cards[0]["title"] == "先行花开"

        skills = client.get("/api/catalog/search", params={"q": "末脚"}).json()["items"]
        assert skills[0]["name"] == "末脚"
        assert skills[0]["description"] == "在最终直线提升速度"

        detail = client.get("/api/catalog/items/skill/200001").json()
        assert detail["name"] == "末脚"
        assert detail["description"] == "在最终直线提升速度"
        assert {"label": "稀有度", "value": "普通"} in detail["attributes"]
        assert detail["data_version"]["version"] == "mdb-1.15.5"

        character_detail = client.get("/api/catalog/items/character/2001").json()
        assert character_detail["title"] == "日本第一的赛马娘"
        attributes = {row["label"]: row["value"] for row in character_detail["attributes"]}
        assert attributes["稀有度"] == "★3"
        assert attributes["限定"] == "否"
        assert attributes["天赋加成"] == "速度 +10%"
        assert character_detail["data_version"]["source"].startswith("master.mdb")

        # 详情要带技能组：名称、描述、稀有度与解锁星级
        skills = character_detail["skills"]
        assert [(row["name"], row["need_rank"]) for row in skills] == [("末脚", 0), ("逃亡者", 3)]
        assert skills[0]["rarity_label"] == "普通"
        assert skills[1]["description"] == "在序盘容易取得领先\n速度提升"

        card_detail = client.get("/api/catalog/items/support_card/30001").json()
        card_attributes = {row["label"]: row["value"] for row in card_detail["attributes"]}
        assert card_attributes["稀有度"] == "SSR"
        assert card_attributes["卡类型"] == "友人"

        # 协助卡详情要带训练效果数值、固有加成与提示技能
        effects = {row["effect_type"]: row for row in card_detail["effects"]}
        assert effects[1]["label"] == "友情加成"
        assert effects[1]["init"] == 5
        assert effects[1]["levels"] == {"15": 10, "20": 10, "35": 15}, "JSON 的等级键是字符串"
        assert effects[1]["max"] == 15
        # 主数据 151 有名字时用官方措辞，不被 BWiki 的「初期羁绊计量格提升」覆盖
        assert effects[14]["label"] == "初始友情槽提升"
        assert effects[99]["label"] == "原始值 99", "字典里没有的类型不能编名字"
        unique = {row["slot"]: row for row in card_detail["unique_effects"]}
        assert unique[0]["label"] == "干劲效果提升"
        assert unique[0]["value"] == 15
        assert unique[0]["need_lv"] == 25
        assert unique[0]["kind"] == "plain"
        assert unique[1]["label"] == "原始值 99"
        # 固有加成块：普通槽位给数值，生效等级换算成「第几突起」
        card_unique = card_detail["unique"]
        assert card_unique["conditional"] is False
        assert card_unique["need_lv"] == 25
        assert card_unique["unlock_breakthrough"] == 0, "SSR 0 突上限 30 级，25 级够用"
        assert card_unique["text"] is None, "没有 category 155 时不给文案"
        assert card_unique["items"] == [
            {"label": "干劲效果提升", "value": 15},
            {"label": "原始值 99", "value": 7},
        ]

        # 突破表：列为 0–4 突，单元格是「升到该档等级上限」时的值
        breakthroughs = card_detail["breakthroughs"]
        assert breakthroughs["level_caps"] == [30, 35, 40, 45, 50]
        rows = {row["label"]: row["values"] for row in breakthroughs["rows"]}
        # 30001 是 SSR：15/20 级的值在 30 级上限下已生效，35 级档再涨一档
        assert rows["友情加成"] == [10, 15, 15, 15, 15]
        assert rows["训练效果提升"] == [15, 20, 20, 20, 20]
        assert rows["初始友情槽提升"] == [15, 20, 20, 20, 20]
        assert rows["原始值 99"] == [3, 3, 3, 3, 3]
        assert len(rows) == 4, "固有效果不随突破档位变化，不进突破表"

        # 条件型固有加成（type 101）：只有官方文案，不给可换算的数值
        conditional = client.get("/api/catalog/items/support_card/30002").json()
        conditional_unique = conditional["unique"]
        assert conditional_unique["conditional"] is True
        assert conditional_unique["items"] == [], "条件型加成的 value 是条件参数，不当数值用"
        assert conditional_unique["text"] == "友情槽达到80时\n训练效果提升"
        assert conditional_unique["need_lv"] == 30
        assert conditional_unique["unlock_breakthrough"] == 0
        assert conditional["breakthroughs"]["rows"] == [], "30002 没有训练效果数据"

        assert [row["name"] for row in card_detail["skills"]] == ["逃亡者"]
        assert "支援卡效果一览" in card_detail["effect_source"]

        luck = client.get("/api/catalog/search", params={"q": "逃亡者"}).json()["items"]
        assert {"label": "稀有度", "value": "稀有"} in luck[0]["attributes"]

        multiline = client.get("/api/catalog/items/skill/200002").json()
        assert multiline["description"] == "在序盘容易取得领先\n速度提升"

        missing = client.get("/api/catalog/items/skill/999")
        assert missing.status_code == 404


def test_unique_effect_hidden_before_unlock(settings: Settings, tmp_path: Path) -> None:
    """固有加成按解锁等级换算出「第几突起才用得上」；练不到时明确说练不到。"""
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)

    db = Database(settings.db_path)
    try:
        import_master_mdb(db, mdb, game_version="1.15.5")
        db.execute("UPDATE catalog_support_unique SET need_lv = 40 WHERE support_card_id = 30001")
        item = CatalogService(db).get("support_card", 30001)
        # R 卡上限 20/25/30/35/40，need_lv 50 超出最高档
        db.execute("UPDATE catalog_support_unique SET need_lv = 50 WHERE support_card_id = 30001")
        db.execute("UPDATE catalog_support_cards SET rarity = 1 WHERE support_card_id = 30001")
        unreachable = CatalogService(db).get("support_card", 30001)
    finally:
        db.close()

    assert item is not None
    # SSR 上限 30/35/40/45/50：40 级解锁 → 第 2 突起才够
    assert item["unique"]["unlock_breakthrough"] == 2
    assert unreachable is not None
    assert unreachable["unique"]["unlock_breakthrough"] is None
    assert unreachable["breakthroughs"]["level_caps"] == [20, 25, 30, 35, 40]


def test_level_caps_fall_back_to_builtin(settings: Settings, tmp_path: Path) -> None:
    """旧库没重新导入 support_card_limit 时用内置上限兜底，详情不能整块消失。"""
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)

    db = Database(settings.db_path)
    try:
        import_master_mdb(db, mdb, game_version="1.15.5")
        db.execute("DELETE FROM catalog_support_limits")
        item = CatalogService(db).get("support_card", 30001)
    finally:
        db.close()

    assert item is not None
    assert item["breakthroughs"]["level_caps"] == [30, 35, 40, 45, 50]
    assert item["breakthroughs"]["rows"], "兜底上限下仍要算出各档数值"


def test_reimport_replaces_snapshot(settings: Settings, tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)
    db = Database(settings.db_path)
    try:
        import_master_mdb(db, mdb, game_version="1.15.5")
    finally:
        db.close()

    _build_mdb(mdb, include_second_skill=False)
    db = Database(settings.db_path)
    try:
        summary = import_master_mdb(db, mdb, game_version="1.16.0")
        row = db.query_one("SELECT COUNT(*) AS n FROM catalog_skills")
        assert row is not None
        assert row["n"] == 1
        assert summary.data_version == "mdb-1.16.0"
    finally:
        db.close()

    with TestClient(create_app(settings)) as client:
        stats = client.get("/api/catalog/stats").json()
    assert stats["counts"]["skills"] == 1
    assert stats["data_version"]["version"] == "mdb-1.16.0"


def test_search_requires_known_kind(settings: Settings, tmp_path: Path) -> None:
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb)
    db = Database(settings.db_path)
    try:
        import_master_mdb(db, mdb, game_version="1.15.5")
    finally:
        db.close()

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/catalog/search", params={"q": "特别", "kind": "unknown"})
    assert response.status_code == 422


def test_missing_columns_are_reported(settings: Settings, tmp_path: Path) -> None:
    """简中服与日服的列数不同，缺列必须降级并如实报告，而不是静默变空。"""
    mdb = tmp_path / "master.mdb"
    _build_mdb(mdb, drop_columns=True)

    db = Database(settings.db_path)
    try:
        summary = import_master_mdb(db, mdb, game_version="1.15.5")
        row = db.query_one("SELECT tag_id FROM catalog_skills WHERE skill_id = 200001")
    finally:
        db.close()

    assert summary.missing_columns == {
        "card_data": ["talent_guts"],
        "skill_data": ["tag_id"],
    }
    assert summary.skipped == {}
    # 缺列只影响对应字段，其它数据照常导入
    assert summary.characters == 2
    assert summary.skills == 2
    assert row is not None and row["tag_id"] is None


def test_rejects_mdb_without_text_data(settings: Settings, tmp_path: Path) -> None:
    mdb = tmp_path / "broken.mdb"
    conn = sqlite3.connect(mdb)
    conn.execute("CREATE TABLE something (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    db = Database(settings.db_path)
    try:
        with pytest.raises(MasterMdbError) as excinfo:
            import_master_mdb(db, mdb)
    finally:
        db.close()

    message = str(excinfo.value)
    assert "text_data" in message
    # 报错要说清"这个文件里有什么"，用户才知道自己选错了哪个文件
    assert "something" in message


def test_reports_missing_file_in_chinese(settings: Settings, tmp_path: Path) -> None:
    db = Database(settings.db_path)
    try:
        with pytest.raises(MasterMdbError) as excinfo:
            import_master_mdb(db, tmp_path / "not-there" / "master.mdb")
    finally:
        db.close()

    message = str(excinfo.value)
    assert "找不到 master.mdb" in message
    assert "not-there" in message


def test_reports_directory_path(settings: Settings, tmp_path: Path) -> None:
    folder = tmp_path / "master.mdb"
    folder.mkdir()

    db = Database(settings.db_path)
    try:
        with pytest.raises(MasterMdbError) as excinfo:
            import_master_mdb(db, folder)
    finally:
        db.close()

    assert "目录" in str(excinfo.value)


def test_reports_non_sqlite_file(settings: Settings, tmp_path: Path) -> None:
    fake = tmp_path / "master.mdb"
    fake.write_bytes(b"PK\x03\x04 this is a zip, not a database")

    db = Database(settings.db_path)
    try:
        with pytest.raises(MasterMdbError) as excinfo:
            import_master_mdb(db, fake)
    finally:
        db.close()

    message = str(excinfo.value)
    assert "不是有效的 SQLite 数据库" in message
    assert "master.mdb" in message


def test_cli_reports_error_without_traceback(tmp_path: Path, capsys) -> None:
    """命令行遇到坏文件只打中文提示与退出码，不抛堆栈。"""
    from scripts.import_master_mdb import main as cli_main

    code = cli_main([str(tmp_path / "missing.mdb")])

    assert code == 2
    captured = capsys.readouterr()
    assert "导入失败" in captured.err
    assert "找不到 master.mdb" in captured.err


def test_attributes_fall_back_to_raw_values() -> None:
    """未收录的枚举值不猜测语义，原样回显并标注。"""
    rows = attributes("skill", {"rarity": 9, "skill_category": 5})
    values = {row["label"]: row["value"] for row in rows}
    assert values["稀有度"] == "原始值 9"
    assert values["技能分类"] == "5"
    assert "标签 ID" not in values


def test_attributes_describe_character_talents() -> None:
    fields = {"rarity": 3, "limited": 1, "talent_speed": 10, "talent_stamina": 5}
    values = {row["label"]: row["value"] for row in attributes("character", fields)}
    assert values["稀有度"] == "★3"
    assert values["限定"] == "是"
    assert values["天赋加成"] == "速度 +10% · 耐力 +5%"


def test_attributes_for_skill_use_skill_rarity_words() -> None:
    def label(rarity: int, skill_id: int = 100) -> str:
        rows = attributes("skill", {"rarity": rarity}, skill_id)
        return next(row["value"] for row in rows if row["label"] == "稀有度")

    assert [label(1), label(2), label(3), label(4)] == ["普通", "稀有", "固有", "进化"]


def test_skill_rarity_six_splits_by_id_shape() -> None:
    """rarity=6 有两类：9 位 id 是觉醒升级、6 位 id 是剧本升级。"""

    def label(skill_id: int) -> str:
        rows = attributes("skill", {"rarity": 6}, skill_id)
        return next(row["value"] for row in rows if row["label"] == "稀有度")

    assert label(100101111) == "觉醒"
    assert label(407011) == "剧本"


def test_placeholder_character_rarity() -> None:
    """``default_rarity=0`` 的 9 前缀条目是占位记录。"""
    values = {row["label"]: row["value"] for row in attributes("character", {"rarity": 0}, 9100101)}
    assert values["稀有度"] == "占位"
