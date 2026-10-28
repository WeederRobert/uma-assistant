# 赛道模块（赛事目录 / 赛道几何 / 大赛 / 场地状态）

`modules/races` 把「有哪些比赛、跑哪条赛道、这条赛道长什么样、下一届大赛什么时候、场地状态怎么分布」变成结构化查询。赛事目录、大赛排期与场地状态概率来自客户端 `master.mdb`，赛道几何来自 BWiki 数据页，两者可信级别不同，界面上分别标注。

## 数据来源

两级来源，职责不重叠：

| 内容 | 来源 | 可信级别 | 说明 |
| --- | --- | --- | --- |
| 赛事目录 | `master.mdb` 的 `race` + `text_data`（category 32） | A | 客户端原始数据，`race.id`、级别、赛道 id、参赛人数都在这里 |
| 赛道登记 | `master.mdb` 的 `race_course_set` | A | 决定「存在哪些赛道」，即使没有赛事引用也会登记 |
| 赛道几何 | BWiki `Data:Course/jian data.json` | C | 分段与坡道；字段含义由页面结构推断，未与客户端逐条比对 |
| 大赛排期 | `master.mdb` 的 `champions_*` 四表 | A | 29 届群英联赛的起止、6 个轮次窗口、两个联赛的晋级门槛 |
| 场地状态概率 | `master.mdb` 的 `race_condition` | A | 按区组 + 季节给出天气与马场的出现率 |

### 赛场编码

`race_course_set.id` 的前三位即赛场编码，`Data:Course/id.json` 与 mdb 完全对得上（mdb 多出 11201/11202 隆尚）：

| 码 | 赛场 | 码 | 赛场 | 码 | 赛场 |
| --- | --- | --- | --- | --- | --- |
| 101 | 札幌 | 106 | 东京 | 111 | 大井 |
| 102 | 函馆 | 107 | 中京 | 112 | 隆尚 |
| 103 | 新潟 | 108 | 京都 | 113 | 川崎 |
| 104 | 福岛 | 109 | 阪神 | 114 | 船桥 |
| 105 | 中山 | 110 | 小仓 | 115 | 盛冈 |

`101`–`110` 是 JRA 中央赛场（`sources.CENTRAL_VENUES`），`111`–`115` 是地方 / 海外赛场。编码表只写在 `modules/races/sources.py`，改映射只动这一处。

### mdb 字段口径

| 字段 | 取值 | 处理 |
| --- | --- | --- |
| `race_course_set.ground` | 1 草地 / 2 泥地 | 映射为「草地 / 泥地」 |
| `race_course_set.turn` | 1 右 / 2 左 / 4 直线 | 4 只出现在 10301 新潟 1000m 与 11201 隆尚 1000m |
| `race_course_set.inout` | 1 未区分 / 2 内 / 3 外 | 只有「同赛场 + 同场地 + 同距离」存在两条时才区分内 / 外 |
| `race.grade` | 0/100/200/300/400/700/800/900/999/1000 | 语义按赛事名抽样推断，见下表 |

| `grade` | 含义 |
| --- | --- |
| 100 / 200 / 300 | GⅠ / GⅡ / GⅢ |
| 400 | 公开赛 |
| 700 | 条件赛 |
| 800 | 新马 / 未胜利 |
| 900 | 练习赛 |
| 999 | 特殊 |
| 1000 | 团队赛 |
| 0 | 剧情 / 其他 |

`official_only` 的白名单是 `(100, 200, 300, 400, 700, 800)`，对应「玩家实际会跑的比赛」。294 条赛事在 `text_data` 里没有名称（群英联赛 / 练习 / 团队赛模板），导入时跳过并计入 `skipped`；大赛赛事名不靠这张目录表，走 `race_instance → race → text_data` 单独取（见下）。

### 场地状态口径

`race_condition` 一张表两种用途，靠 `area` 区分：

| `area` | 含义 | 引用情况 |
| --- | --- | --- |
| `999` | 默认表 | 大赛（`champions_race_condition`）、剧情赛（`main_story_race_data`）、团队赛（`team_building_sp_race_schedule`）都引用它 |
| `5` | 备用表 | 这 67 行里没有任何一行被上述三张表引用，用途未确认 |

- `rate` 是十万分率整数：**同一 `(area, season)` 组内 `rate` 之和恒为 `1e8`**，所以 `rate / 1e8` 就是出现率。导入后 `conditions()` 会复核总和，不等于 `1e8` 就在面板上标「源数据可能有缺失」。
- `season` 取 1–5，mdb 没有字典表，界面按 `季节 1`…`季节 5` 原样展示。`season 5` 与 `season 1` 的分布不同，不能当别名。
- `weather` 1 晴 / 2 阴 / 3 雨 / 4 雪，`ground` 1 良 / 2 稍重 / 3 重 / 4 不良。
- 两者不是自由组合：`雨` 只配 `重` / `不良`，`雪` 只配 `稍重` / `重` 且只出现在 `season 4`，`晴` / `阴` 只配 `良` / `稍重`（另有 3 行 `rate = 0` 的「重」占位行，表示该组合不出现）。
- **只有出现率，没有修正系数**：速度、加速度、耐力消耗的数值修正在 mdb 与 BWiki 数据页里都没有，模块不推导。

### 大赛口径

链路：`champions_race_condition → race_instance → race → race_course_set`。

| 表 | 行数 | 内容 |
| --- | --- | --- |
| `champions_schedule` | 29 | 每届的 `start_date` / `end_date`（unix 秒） |
| `champions_round_schedule` | 174 | 每届 6 个窗口：`round` 0 预选、1 与 2 两轮小组赛、3 的 `round_detail` 1/2/3 是决赛三场 |
| `champions_round_detail` | 406 | 每届 × 2 个 `league_type` × 7 个 `round_id`，带 `tier`、`round_number`、`breakthrough_number_1/2`、名额 |
| `champions_race_condition` | 203 | 每届 × 7 个 `round_id`，指向 `race_instance` 与 1 条 `race_condition` |

- 每届引用 **2 个 `race_instance`**：`round_id` 0–3（预选与小组赛）用预赛段，4–6（决赛三场）用决赛段，两者**共用同一条赛道**（`course_count` 记录实际条数）。
- 每届只引用 **1 条 `race_condition`**，即该届固定的天气与马场（与 `docs/kb/04-races.md` 记的「群英联赛日程 / 赛场 / 距离 / 赛道 / 天气 / 马场状态固定」一致），不是概率。
- 赛事名可取得：`race_instance.race_id` → `text_data` category 32 → 「群英联赛 金牛杯」，决赛段是「群英联赛 金牛杯 决赛」。早期按 `champions_schedule.resource_id` 反查是错的，那个字段是资源 id，与名称无关。
- `champions_schedule.notice_date` 恒为 `-28800`，是时区偏移量不是时间戳，**不要当时间显示**。
- `league_type` 只有 1 / 2 两个值、没有字典，界面按 `联赛 1` / `联赛 2` 展示。
- 时间一律按系统时区显示（见 `app/kernel/times.py`）。

## 几何统计口径

`Data:Course/jian data.json` 的分段**不是连续覆盖全程的**：首段 `start` 可能大于 0（10501 为 300），段与段之间也可能空档（10502 从 500 跳到 700）。

首段 `start > 2` 的那段空档是发走后的开场直线，数据页没有给它名字，模块补一段

```json
{"type": "直线", "name": "切线", "start": 0, "end": 300, "length": 300, "inferred": true}
```

**只有开头的空档叫「切线」**，中段空档仍然只记进 `unlisted_length`，不并进直线段：数据页在弯道之间省略的部分同样是直线，但没有位置信息能证明它连续，贸然补段只会制造假精确。当前 107 条几何里有 12 条带切线（8 条首段是弯道、4 条首段是 `start > 0` 的直线）。

| 字段 | 口径 |
| --- | --- |
| `total_length` | 最后一段的 `end`；不用「首段 `start` + 各段 `length` 之和」 |
| `segments` | 数据页分段原样，另在开头插入 `inferred=true` 的「切线」 |
| `opening_straight` | 切线长度；没有开场空档时为 0 |
| `straight_count` | 数据页显式标为「直线 / 直」的段数 **+ 切线** |
| `straight_total` | 上述直线段的 `length` 之和（含切线） |
| `unlisted_length` | `total_length` 减去分段（含切线）覆盖长度，即中段空档 |
| `final_straight` | `name` 为「终直」那一段的 `length` |
| `slope_positive` / `slope_negative` | 按 `slope` 符号分别求和 `length`；页面没说明正负对应上 / 下坡，不给结论 |

与 mdb `distance` 比较留 2m 容差（11102 大井 1800m 数据页为 1801m）。

几何记录触发的告警（原样保留，不修正数据）：

| 告警 | 触发条件 |
| --- | --- |
| 分段终点与数据库距离不一致 | `abs(total_length - distance) > 2` |
| 第 N 段与上一段重叠 | `segment.start < previous_end - 2` |
| 最后一段越过终点 | `last.end > distance + 2` |
| 没有标记「终直」的分段 | 缺 `name == "终直"` |

当前 107 条几何里有 4 条带告警，都是源数据缺陷：10613 东京泥地 2400m（末段越过终点 448m、缺终直）、11102 大井 1800m、11103 大井 2000m、10907 阪神 2400m（相邻段重叠）。14 条赛道没有几何——川崎 11301–03、盛冈 11501–04、船桥 11401–04、隆尚 11201–03，与「简中几何数据不含这些场地」一致。

> 这些是赛道形状，不是比赛数值。终直 500m 只说明最后一段直线的长度，推不出加速区间或最高速度。

知识库里保留 6 条高频赛道的完整分段 / 坡道作为示例（[10-course-geometry.md](kb/10-course-geometry.md)），其余记录以本模块的结构化查询为准。

## 抓取策略

数据页用 `action=raw` 取原文，响应是 `<nowiki>[...]</nowiki>` 包裹的 JSON 数组，解析前剥掉标签。`revid` 与时间戳另查 `api.php?action=query&prop=revisions`。

- **限速**：串行请求，间隔 `UMA_RACES_INTERVAL_SECONDS`（默认 1.5s）。
- **TTL 缓存**：原文写 `data/modules/races/cache/`，按 sha1 命名；`UMA_RACES_CACHE_HOURS`（默认 24）内复用。数据页很少变，TTL 比活动模块长。
- **反爬识别**：响应不以 `<nowiki>` 或 `[` 开头就按反爬页报 `WikiError`。
- **降级**：抓取失败时不影响已导入的赛事目录，`refresh` 返回 `ok=true` + `error`，界面提示「几何抓取失败」。

`master.mdb` 不联网获取，路径由内核 `Settings.mdb_path`（默认 `data/mdb/master.mdb`）决定，也可用 `UMA_RACES_MDB_PATH` 临时覆盖；`data-sync` 模块与 `scripts/fetch_mdb_android.py` 用同一个路径。

## 数据表

四组表，前缀 `races_`：

| 表 | 用途 |
| --- | --- |
| `races_catalog` | 赛事目录：`race_id`、名称、`grade`、赛道 id、赛场 / 场地 / 距离 / 方向 / 内圈外圈、参赛人数 |
| `races_courses` | 赛道：几何字段（`segments` / `slopes` / `straight_*` / `opening_straight` / `unlisted_length` / `final_straight` / `total_length` / `slope_*`）、`warnings`、`has_geometry`、`source_url`、`fetched_at` |
| `races_conditions` | 场地状态概率：主键 `(area, season, weather, ground)`，`rate` 为十万分率 |
| `races_champions` / `races_champion_rounds` / `races_champion_entries` | 大赛排期（含预赛 / 决赛赛事名与赛道）、6 个轮次窗口、两个联赛的晋级门槛 |

来源元信息写在 `module_sources`：`races.catalog` 记 mdb 的 sha256 前缀、导入时间与条数（含 `conditions` / `champions`）；`races.geometry` 记数据页标题、`revid`、`revised_at`、`fetched_at`、`from_cache`、`stale`、`courses`、`with_warnings`。

赛道登记与几何分开写：`ensure_courses()` 只登记 mdb 里的赛道（`has_geometry=0`），几何抓取成功后才 `upsert_courses()` 覆盖几何列。这样几何抓取失败时不会丢赛道列表。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/races/status` | 条数、赛场清单、级别表、两级来源的版本与抓取时间、mdb 路径与大小、数据版本 |
| GET | `/api/races/courses?venue=东京&surface=草地&direction=左&distance_min=1600&distance_max=2400&geometry_only=true` | 赛道列表；每条带 `label`、`level`（恒为 C）、`race_count` |
| GET | `/api/races/courses/{course_id}` | 赛道详情：分段表、坡道表、告警、引用该赛道的赛事 |
| GET | `/api/races/races?q=有马&venue=中山&grade=100&official_only=true&limit=50` | 赛事检索；`grade_label` 由 `sources.grade_label()` 给出，`returned` / `truncated` 说明是否被 `limit` 截断 |
| GET | `/api/races/races/{race_id}` | 赛事详情：附带对应赛道摘要（`course` 可能为 `null`） |
| GET | `/api/races/conditions?area=999&season=1` | 场地状态出现率；返回 `groups`（全部区组 + 季节）、`selected`、`items`（`rate` 与 `percent`）；参数省略时默认 `area=999` 的第一组 |
| GET | `/api/races/champions?upcoming=true` | 大赛排期；`state` 为 `upcoming` / `running` / `finished`，`zone` 是显示时区 |
| GET | `/api/races/champions/{champions_id}` | 大赛详情：`rounds`（6 个窗口）+ `leagues`（两个联赛的晋级门槛）+ 赛道几何 |
| POST | `/api/races/refresh` | body `{"force": true, "mdb_path": "..."}`，两者都可省略；mdb 缺失时 422 |

## 面板

侧栏「赛道」：

- **数据来源卡**：赛事数 / 赛道数 / 有几何的赛道数 / 大赛届数 / 场地状态条数、目录来源（带 mdb sha256 前缀）、几何 `revid` 与原页链接、数据版本；抓取时间超期时提示。
- **筛选工具条**：赛场、场地、方向、距离区间、只看有几何。
- **赛道列表**：点进详情；有开场的行会标「含切线 Nm」。
- **赛道详情**：分段表（切线带「推断」标签、终直高亮、直段标色）+ 坡道表 + 引用赛事列表 + 告警；显示「终直 / 直线段（列出合计 / 含切线 / 中段未列出长度）」。
- **按赛事查**：关键词 + 只看正式赛事。
- **大赛（群英联赛）**：**已移到侧栏「日程」页**（与卡池排期、BWiki 活动时间轴同页，入口不再单独占位）。那里列表带状态标签（未开始 / 进行中 / 已结束）、起止时间（系统时区）、赛道与固定天气马场，「只看未结束」默认勾选；点一届展开轮次窗口表与两个联赛的晋级门槛表。本页的数据来源卡会显示「大赛排期 N 届 · 在『日程』页查看」，`/api/races/champions[/{id}]` 接口不变。
- **场地状态出现率**：区组 + 季节两个下拉，表格给出天气 / 马场 / 出现率 / `rate` 原值；低到四舍五入会变 0 的组合显示「＜0.01%」。卡片下方明写「这是出现率，不是数值修正」。

## 环境变量

`UMA_RACES_` 前缀，全部有默认值：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `UMA_RACES_RAW_URL` | `https://wiki.biligame.com/umamusume/index.php` | `action=raw` 端点 |
| `UMA_RACES_API_URL` | `https://wiki.biligame.com/umamusume/api.php` | 查 `revid` 用 |
| `UMA_RACES_GEOMETRY_PAGE` | `Data:Course/jian data.json` | 几何数据页标题 |
| `UMA_RACES_USER_AGENT` | 带项目标识的 UA | 请求头 |
| `UMA_RACES_REFERER` | wiki 首页 | 请求头 |
| `UMA_RACES_CACHE_HOURS` | `24` | 缓存有效期，`0` 表示每次都请求 |
| `UMA_RACES_INTERVAL_SECONDS` | `1.5` | 请求最小间隔 |
| `UMA_RACES_TIMEOUT_SECONDS` | `60` | 单次请求超时 |
| `UMA_RACES_RETRIES` | `2` | 退避重试次数 |
| `UMA_RACES_BACKOFF_SECONDS` | `3.0` | 退避基数 |
| `UMA_RACES_MDB_PATH` | 空（用内核 `mdb_path`） | 覆盖 `master.mdb` 路径 |
| `UMA_RACES_SEARCH_LIMIT` | `50` | 赛事检索默认条数上限 |

## 已知限制

- **`grade` 语义是推断的**：mdb 没有级别字典表，映射来自赛事名抽样，可能有个别错值。
- **几何只有 107 条**：地方 / 海外赛场（川崎、船桥、盛冈、隆尚）在简中几何数据里没有记录，面板会显示「无几何」而不是猜。
- **坡道正负没定义**：数据页只给 `slope` 的 `1` / `-1`，无法判断哪边是上坡，因此只求和长度。
- **4 条源数据缺陷保留原样**：重叠与越过终点都按数据页写入并出告警，不静默修正，避免与 wiki 对不上。
- **赛事名缺失的 294 条被跳过**：模板赛 / 练习赛没有 `text_data` 名称，与其编一个名字不如不导入。
- **场地状态只有出现率，没有修正系数**：`race_condition` 能给各组合的出现率，但速度、加速度、耐力消耗的数值修正在 mdb 与 BWiki 数据页里都没有官方公式，模块不推导。
- **`area` / `season` / `league_type` 没有字典**：`999` 是大赛与剧情共用的默认表，`5` 这张备用表没有被引用，用途未确认；季节与联赛编号只按原值展示，不臆测含义。
- **大赛的固定天气马场是推断出来的口径**：每届只引用 1 条 `race_condition`，与知识库记的「大赛天气 / 马场固定」一致，但 mdb 没有字段直接说明「这一条就是固定值」。如果后续发现实际是逐场随机，应改成按同组出现率展示。
- **大赛轮次编号与阶段名没有对应关系**：`round_id` 0–6 与 6 个窗口不是一一对应（`round_id` 2、3 同属 `round` 2），面板按原值列「阶段编号」并说明来源。
