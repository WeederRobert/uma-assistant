"""赛场编码、场地 / 方向 / 级别映射，以及「哪些结论是从哪来的」。

``master.mdb`` 里没有赛场名，赛场由 ``race_course_set.id`` 的前三位决定。
这张表来自 BWiki ``Data:Course/id.json``（每个赛场下列出 ``草1200`` 这类条目），
并与 mdb 的 ``distance`` 逐条核对过：119 条两端都有，mdb 多出的 ``11201`` /
``11202``（隆尚 1000m 两条）按前缀归到隆尚。修改前请先跑一次核对文档里的脚本。
"""

from __future__ import annotations

VENUES: dict[str, str] = {
    "101": "札幌",
    "102": "函馆",
    "103": "新潟",
    "104": "福岛",
    "105": "中山",
    "106": "东京",
    "107": "中京",
    "108": "京都",
    "109": "阪神",
    "110": "小仓",
    "111": "大井",
    "112": "隆尚",
    "113": "川崎",
    "114": "船桥",
    "115": "盛冈",
}

# 中央竞马场（JRA）与地方 / 海外场地分开，便于「只查中央赛场」这类筛选
CENTRAL_VENUES = ("札幌", "函馆", "福岛", "新潟", "东京", "中山", "中京", "京都", "阪神", "小仓")

# race_course_set.ground
SURFACES = {1: "草地", 2: "泥地"}

# race_course_set.turn：同一赛场内取值恒定，1/2 覆盖 13 个场地，
# 4 只出现在新潟 1000m 与隆尚 1000m 两条直线上。
DIRECTIONS = {1: "右", 2: "左", 4: "直线"}

# race_course_set.inout：只有同一赛场 + 同场地 + 同距离存在两条时才用得上。
# 2/3 的判定见 docs/races.md「内圈 / 外圈」一节（中山 2000 内、中山 1200 外 等）。
COURSE_PARTS = {1: "未区分", 2: "内圈", 3: "外圈"}

# race.grade：按名称抽样推断，不是官方分级表
GRADES = {
    0: "剧情 / 其他",
    100: "GⅠ",
    200: "GⅡ",
    300: "GⅢ",
    400: "公开赛",
    700: "条件赛",
    800: "新马 / 未胜利",
    900: "练习赛",
    999: "特殊",
    1000: "团队赛",
}

# 默认只看正式赛事（排除剧情、练习、团队赛与特殊赛）
OFFICIAL_GRADES = (100, 200, 300, 400, 700, 800)

LEVEL_LABELS = {
    "A": "客户端原始数据",
    "B": "权威二手（分类页结构化字段）",
    "C": "社区整理，字段含义为推断",
    "D": "占位或来源不明",
}

# race_condition.weather / ground：字段没有字典表，标签按「天气—场地」组合推断
# （雨只对应重 / 不良，雪只出现在冬季分组），与 docs/kb/04-races.md 的四档叫法一致。
WEATHER = {1: "晴", 2: "阴", 3: "雨", 4: "雪"}
GROUND = {1: "良", 2: "稍重", 3: "重", 4: "不良"}

# race_condition.area：没有字典表。999 是大赛、剧情赛与团队赛共同引用的那张表，
# 5 只在条件表里出现、没有被这几张表引用，所以只按「默认 / 备用」区分，不推断含义
AREA_LABELS = {999: "默认表", 5: "备用表"}

# champions_round_detail.league_type：KB 记载分 Open / Grade 两个联赛，
# 但 mdb 没有字典能确定 1 与 2 谁是谁，所以只按编号展示
LEAGUE_LABELS = {1: "联赛 1", 2: "联赛 2"}

# champions_round_detail.round：4 个阶段
ROUND_LABELS = {
    0: "第 1 阶段（预选）",
    1: "第 2 阶段（Round 1）",
    2: "第 3 阶段（Round 2）",
    3: "第 4 阶段（决赛）",
}

TEXT_RACE_NAME = 32

# race_condition.rate 是十万分率：同一 (area, season) 组内之和恒为 1e8
RATE_TOTAL = 100_000_000

# 大赛、剧情赛与团队赛都引用 999 这张表的条目，所以它是面板的默认展示组
DEFAULT_AREA = 999


def venue_of(course_id: str) -> str | None:
    return VENUES.get(str(course_id)[:3])


def surface_label(ground: int | None) -> str | None:
    return SURFACES.get(ground) if ground is not None else None


def direction_label(turn: int | None) -> str | None:
    return DIRECTIONS.get(turn) if turn is not None else None


def course_part_label(inout: int | None) -> str | None:
    return COURSE_PARTS.get(inout) if inout is not None else None


def grade_label(grade: int | None) -> str | None:
    return GRADES.get(grade) if grade is not None else None


def weather_label(weather: int | None) -> str | None:
    return WEATHER.get(weather) if weather is not None else None


def ground_label(ground: int | None) -> str | None:
    return GROUND.get(ground) if ground is not None else None


def area_label(area: int | None) -> str | None:
    if area is None:
        return None
    return AREA_LABELS.get(area, f"区域 {area}")


def league_label(league_type: int | None) -> str | None:
    if league_type is None:
        return None
    return LEAGUE_LABELS.get(league_type, f"联赛 {league_type}")


def round_label(round: int | None) -> str | None:
    if round is None:
        return None
    return ROUND_LABELS.get(round, f"第 {round + 1} 阶段")
