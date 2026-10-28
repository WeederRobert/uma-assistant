# 技能模块（条件 / 效果参数 / 来源反查 / 进化）

`modules/skills` 把 `master.mdb` 的 `skill_data` 变成可查的结论：一个技能在什么条件下发动、效果参数是多少、哪些马娘与协助卡能拿到它、进化前后是谁。**主数据里没有字典表的字段一律只回显原始编号**，不臆造中文标签。

## 数据来源

全部来自本机 `master.mdb`（可信级别 A：客户端原始数据），没有外部抓取。

| 内容 | 来源表 | 说明 |
| --- | --- | --- |
| 技能主记录 | `skill_data` + `text_data`（47 名称 / 48 描述） | 1289 行、78 列 |
| 条件与节奏 | `skill_data.precondition_N` / `condition_N` / `float_ability_time_N` / `ability_time_usage_N` / `float_cooldown_time_N` | `N` 为 1 / 2 两个槽位 |
| 效果参数 | `skill_data.ability_type_N_M` / `float_ability_value_N_M` / `target_type_N_M` / `target_value_N_M` / `ability_value_usage_N_M` / `ability_value_level_usage_N_M` / `additional_activate_type_N_M` | `M` 为槽位内第 1–3 条 |
| 哪些马娘有 | `card_data.available_skill_set_id` → `available_skill_set` + `text_data`（6 角色名 / 5 卡称号） | `need_rank` 是解锁所需觉醒星级 |
| 哪些协助卡给 | `single_mode_hint_gain`（`hint_gain_type = 0`）+ `text_data`（76 系列名） | `hint_value_1` 即技能 id |
| 进化 / 觉醒 | `skill_upgrade_speciality` / `skill_upgrade_description` + `text_data`（5 卡称号） | 前者给进化前后，后者只给「哪张卡几觉解锁」 |

### 三条连接键（写错就静默 0 行）

这三条都是在简中服 2.0.2 / `mdb-cn-20261022` 快照上逐条核对过的：

| 关系 | 正确的连接 | 错误写法与后果 |
| --- | --- | --- |
| 马娘 → 技能 | `card_data.available_skill_set_id = available_skill_set.available_skill_set_id` | 用 `available_skill_set.id` 连会匹配 **0** 行（`id` 是自增主键，不是集合号） |
| 协助卡 → 提示技能 | `single_mode_hint_gain.support_card_id = support_card_data.id` 且 `hint_gain_type = 0`，`hint_value_1` 即技能 id | `hint_gain_type = 1` 的 762 行 `hint_value_1` 是别的含义（值域 1/3/4），混用会得到不存在的技能 |
| 协助卡 → 技能组 | `skill_set.id = support_card_data.skill_set_id / 1000`（`9081001 → 9081`） | 直接 `=` 连接匹配 **0** 行 |

### 为什么不用 `skill_set` 做逐卡反查

`skill_set_id / 1000` 这条链路是真实存在的（362/362 都能连上），但经核实**已退化**：362 张协助卡只解析出 2 个组（`9081` 覆盖 347 张、`9089` 覆盖 15 张），合计只有 7 个技能，而且这 7 个技能全部已被提示表覆盖。把「同组」当成「这张卡给这个技能」会给出错误结论，所以模块只导入提示表，并在口径说明里写明这一点。

## 字段口径

| 字段 | 取值 | 处理 |
| --- | --- | --- |
| `skill_data.rarity` | 1 / 2 / 3 / 4 / 5 / 6 | 1 普通、2 稀有（金）、3 固有、4 进化、5 固有；6 再分两类：9 位 id 是**觉醒**（338 个，全部在 `skill_upgrade_description`）、6 位 id 是**剧本**（7 个，全部在 `skill_upgrade_speciality`） |
| `skill_data.skill_category` | 0–5、101 | **无字典表**，只回显「原始值 N」 |
| `skill_data.ability_type` | 1 / 2 / 5 / 8 / 9 / 21 / 22 / 27 / 28 / 31 / 501 / 502 … | **无字典表**，只回显原值。分布：27 有 641 条、9 有 171 条、31 有 170 条 |
| `skill_data.target_type` | 1 为主（1224 条），另有 4 / 9 / 10 / 11 / 18 / 19 / 20 / 21 / 22 / 23 | **无字典表**，只回显原值 |
| `precondition_N` / `condition_N` | 引擎表达式，如 `phase==1&change_order_onetime<0`、`distance_type==2&phase_laterhalf_random==0@distance_type==3&…` | 原样展示，不翻译。`@` 表示并列的另一种成立条件，`&` 表示同时成立——这是表达式自身的语法，模块不做解释 |
| `float_ability_time_N` / `float_cooldown_time_N` / `float_ability_value_N_M` | 整数（如 18000 / 300000 / 1500） | **单位未经官方确认**，按原始值显示 |
| `available_skill_set.need_rank` | 0 / 2 / 3 / 4 / 5 | 0 显示「初始」，其余显示「觉醒 N」 |
| `skill_upgrade_description.rank` | 3 / 5 | 原样显示为「觉醒 3 / 5」 |

槽位与序号的编号一直保留主数据原样（`slot` 对应 `*_1_*` / `*_2_*`），方便对着 `skill_data` 的列名反查，不做压缩重排。

## 数据表

六组表，前缀 `skills_`：

| 表 | 用途 |
| --- | --- |
| `skills_catalog` | 技能主记录：名称、描述、`rarity`、`skill_category`、`tag_id`、`group_id`、`activate_lot`、`priority` 等 21 个标量字段 |
| `skills_slots` | 条件与节奏：主键 `(skill_id, slot)`，`precondition` / `condition` / `ability_time` / `ability_time_usage` / `cooldown` |
| `skills_effects` | 效果参数：主键 `(skill_id, slot, seq)`，`ability_type` / `value` / `value_usage` / `value_level_usage` / `target_type` / `target_value` / `activate_type` |
| `skills_characters` | 反查马娘：`(skill_id, card_id)` + `need_rank` + 卡名与称号 |
| `skills_support_cards` | 反查协助卡：`(skill_id, support_card_id, source)` + `hint_group` + 系列名；`source` 目前只有 `hint` |
| `skills_upgrades` | 进化 / 觉醒：`(skill_id, source)`，`source` 为 `speciality`（带 `base_skill_id` / `scenario_id`）或 `awakening`（带 `card_id` / `rank`） |

来源元信息写在 `meta` 的 `skills.catalog_source`：mdb 路径与 sha256 前缀、`mdb_mtime`、导入时间、六组条数与 `skipped`。**`mdb_mtime` 是「数据是不是旧了」的判据**：`master.mdb` 比上次导入更新时，启动会自动补导入，避免「数据同步」导入新快照后技能表还停在上一版。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/skills/status` | 六组条数、分类与稀有度分布、导入来源与时间、`stale`（mdb 已更新）、mdb 路径与大小、数据版本、口径说明 |
| GET | `/api/skills/skills?q=加速&skill_category=2&rarity=1&with_source_only=true&limit=50` | 技能检索；每条带 `rarity_label` 与 `character_count` / `support_card_count` |
| GET | `/api/skills/skills/{skill_id}` | 技能详情：`attributes`、`slots`（含 `effects`）、`characters`、`support_cards.hint`、`upgrades.evolved_from` / `evolves_to`、`notes` |
| POST | `/api/skills/refresh?force=false` | 从配置的 mdb 重新导入；`force=true` 时忽略 `mdb_mtime` 强制导入，mdb 缺失返回 422 |

## 面板

侧栏「技能」：

- **数据来源卡**：技能数 / 条件槽位数 / 效果参数条数、三类反查的条数、导入来源与导入时间（按系统时区换算）、数据版本；`stale` 时提示「master.mdb 已更新，可刷新」。
- **查技能**：关键词（匹配名称或描述）、分类、稀有度、只看能查到来源；结果行给出稀有度标签、分类原始值、马娘与协助卡数量、描述首行。
- **技能详情**：
  - 属性条（稀有度、分类、通用技能、标签、分组、消耗 `activate_lot`、优先级等，未收录枚举显示「原始值 N」）。
  - 描述（还原 `\n` 换行）。
  - 每个槽位一块：`槽位 N` + 持续时间 / 使用方式 / 冷却原值 + 前置与发动条件表达式 + 效果参数表（序号 / 效果类型 / 数值 / 数值用法 / 等级用法 / 目标类型 / 目标值）。
  - 「哪些马娘有这个技能」、 「哪些协助卡给这个技能」（带提示组）。
  - 「进化 / 觉醒关系」：剧本进化显示「进化前 → 本技能」，觉醒显示「本技能 ← 哪张卡几觉解锁」。
  - 「口径说明」：五条限制原样列在详情里，避免结论被当成官方数值。

## 环境变量

`UMA_SKILLS_` 前缀：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `UMA_SKILLS_MDB_PATH` | 空（用内核 `mdb_path`） | 覆盖 `master.mdb` 路径 |
| `UMA_SKILLS_SEARCH_LIMIT` | `50` | 检索默认条数上限（接口再夹到 200） |

## 已知限制

- **`ability_type` / `target_type` / `skill_category` 没有字典表**：查过全部表名与 `text_data` 的所有类别，主数据里不存在这些字段的映射。模块只回显编号，「27 是速度提升」这类结论需要外部资料，不写进代码。
- **条件表达式不翻译**：`condition` 是引擎语法，翻译成中文极易出错（同一表达式在不同跑法 / 距离下含义不同），因此原样展示。
- **`float_*` 的单位未确认**：`float_ability_time` = 18000、`float_cooldown_time` = 300000 这类值在 mdb 里没有单位声明，社区口径（1/10000 秒）无法从主数据验证，模块不做换算。
- **协助卡反查只有提示表**：`skill_set` 链路退化（2 组 / 7 技能），不做逐卡映射；白技能来源里也不含「继承」与「活动赠送」等其他获取途径。
- **觉醒类进化的「进化前」留空**：`skill_upgrade_description` 只记录「哪张卡在几觉解锁哪个技能」，没有 base 技能字段；按 id 规律猜测（9 位 id 前 6 位是卡 id）属于推断，不做。
- **`unique_skill_id_1/2` 的含义未确认**：159 条非零记录的 id 与技能的 `group_id` 不同、名称与技能本身同名，无法验证它是不是「进化后技能」指针，因此只在详情里原样带出 `unique_skill_id_1`，不做跳转。
- **不覆盖技能学习成本（SP）与提示等级**：mdb 的 `skill_data` 里没有 SP 字段，`skill_set.skill_level` 与提示等级的关系也未确认，这部分留给攻略文档（见 `docs/kb/05-skills.md`）。
