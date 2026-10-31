# 图鉴模块（马娘 / 协助卡 / 技能）

`modules/catalog` 把 `master.mdb` 里的卡与技能搬进本地 SQLite，提供检索与**详情**。详情不只是「名字 + 描述」：马娘要看得到能学的技能组，协助卡要看得到**每突破一档、练到该档上限后的训练效果**、固有加成与提示技能。

## 数据表

| 表 | 内容 | 来源 |
| --- | --- | --- |
| `catalog_characters` | 马娘卡：名称、称号、稀有度、限定、五项天赋加成 | `card_data` + `text_data` 5/6 |
| `catalog_support_cards` | 协助卡：名称、系列名、稀有度、卡类型、技能组 id | `support_card_data` + `text_data` 6/76 |
| `catalog_support_limits` | 协助卡各突破档位的等级上限（R/SR/SSR × 0–4 突） | `support_card_limit` |
| `catalog_support_effect_types` | 效果类型字典：官方名称 + 说明（1–31） | `text_data` 151 / 154 |
| `catalog_support_unique_text` | 固有效果的官方成句文案（条件型加成自带发动条件） | `text_data` 155 |
| `catalog_skills` | 技能：名称、描述、稀有度、分类、标签 | `skill_data` + `text_data` 47/48 |
| `catalog_character_skills` | 马娘能学的技能 + 解锁星级 | `card_data` → `available_skill_set` |
| `catalog_support_effects` | 协助卡训练效果：1 级值 + 各等级（`lv5..lv50`）的值 | `support_card_effect_table` |
| `catalog_support_unique` | 协助卡固有加成（一行两个槽位，`type_N = 0` 是空槽位不导入） | `support_card_unique_effect` |
| `catalog_support_skills` | 协助卡提示技能 | `single_mode_hint_gain`（`hint_gain_type = 0`） |

当期简中服快照（`mdb-cn-20261022`）导入结果：马娘 161 / 协助卡 362 / 技能 1289 / **等级上限 3 / 效果类型名 31 / 马娘技能 1113 / 训练效果 3120 / 固有加成 405 / 固有文案 258 / 提示技能 2296**。

## 三条连接键（写错了会静默 0 行）

1. 马娘 → 技能：`card_data.available_skill_set_id` 必须 JOIN `available_skill_set.available_skill_set_id`；用 `available_skill_set.id` 匹配 0 行（那张表的 `id` 只是自增序号）。
2. 协助卡 → 提示技能：`single_mode_hint_gain.support_card_id = support_card_data.id`，且**只有 `hint_gain_type = 0`** 的行 `hint_value_1` 才是 `skill_id`（2296/2296 能 JOIN 到 `skill_data`）；其它取值是别的含义，不能混用。
3. 协助卡技能组（历史坑，本模块未用）：`support_card_data.skill_set_id / 1000` 才能对上 `skill_set.id`。

`need_rank` 是解锁所需星级：`0` 表示初始可用，`2`–`5` 表示要升到对应星级。详情里显示为「初始 / ★N 解锁」。

## 突破口径

用户真正关心的是「这张卡每突破一次、练到该档上限之后数值是多少」，所以详情不给「各等级档位」的原始表格，而是折算成 **0–4 突 × 数值** 的五列。

1. **等级上限**：`support_card_limit` 给出每个稀有度的上限——R `20/25/30/35/40`、SR `25/30/35/40/45`、SSR `30/35/40/45/50`（简中服 2026-10-22 快照，362 张卡无缺口）。导入进 `catalog_support_limits`；该表没有对应稀有度时（旧库还没重新导入）回落到 `labels.SUPPORT_LEVEL_CAPS` 的内置值，避免详情整块消失。
2. **训练效果**：`support_card_effect_table` 的 `init` 相当于 1 级，`limit_lv5..limit_lv50` 是**该等级**的值，`-1` 表示这一档不上涨（沿用上一档）。取「不超过该档等级上限的最近一档」即这一突破的数值。
3. **固有效果不进这张表**：它不随突破档位变化（只有解锁等级），单独一块展示，见下节。

抽样核对：SSR `30001` 得意率 0 突（30 级）= 20 → 3 突（45 级）= 35；R `10001` 友情加成 0 突（20 级）= 10 → 3 突（35 级）= 15。

## 固有效果与发动条件

**名称与条件是主数据自带的**：`text_data` category 155 是固有效果的成句文案，按 `unique_effect_id` 索引（258/258 命中），一句话涵盖同一行的两个槽位。同一个 id 的 `support_card_unique_effect` 里：

- **普通加成**（`type_N` 1–31）：`value_N` 就是数值。界面显示「文案」+「名称 +值」+「解锁条件」。
- **条件型加成**（`type_N` ≥ 100，快照里是 101–120）：**不是普通效果，而是「满足条件才生效」的加成**。`value_N` 是条件参数、`value_N_1..4` 是参数对/效果值，**含义随类型变化**——101 `value_0` = 友情槽阈值（80/100）、102 友情槽阈值且限「非擅长训练」、103 编成卡的类型种数、104 粉丝数、106 友情训练次数上限、108/114 体力上限/当前体力、118 友情槽 60 时可能出现在两处、120 友情槽 80 时按编成卡给属性……主数据没有逐类型的参数表，**因此一律不换算**，只显示 category 155 的原句（自带条件，例如「友情槽达到80时\n训练效果提升」）。
- **解锁条件**：`support_card_unique_effect.lv` 是解锁等级，换算成「等级 N · 第 X 突起可达到」（拿 `support_card_limit` 反查第一档够得着的上限）；练不到时明说「该稀有度练不到这个等级」。

抽样核对：SSR `30081` 的固有效果是 `type_0 = 101 / value_0 = 80`，category 155 原文「友情槽达到80时\n训练效果提升」，`lv = 40` → 显示「解锁条件：等级 40 · 第 2 突起可达到」。

**效果名称用主数据自带的字典**：`text_data` category 151 = 名称、154 = 说明，导入进 `catalog_support_effect_types`。官方简中服措辞与 BWiki 不同（151 的「初始友情槽提升 / 比赛加成 / 擅长率提升 / 技能点数加成」对应 BWiki 的「初期羁绊计量格提升 / 赛后加成 / 得意率提升 / 技能Pt加成」），所以**字典优先**；mdb 快照缺 151（老日服库）时才回落到 `labels.SUPPORT_EFFECT_LABELS`，两者都没有的取值显示「原始值 N」。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/catalog/stats` | 各表条数与数据版本 |
| GET | `/api/catalog/search?q=&kind=&limit=` | 名称检索（`character` / `support_card` / `skill`） |
| GET | `/api/catalog/items/{kind}/{id}` | 详情：基础属性 + 描述 + **kind 专属区块** + 数据版本 |

详情里新增的字段：

- `character.skills[]`：`skill_id` / `name` / `description` / `rarity_label` / `need_rank`
- `support_card.effects[]`：`effect_type` / `label`（主数据字典优先）/ `init` / `levels{等级: 值}` / `max`（原始等级档位，面板不再直接渲染）
- `support_card.breakthroughs`：`level_caps[]`（0–4 突的等级上限）+ `rows[]`（`label` / `values[]`，与 `level_caps` 对齐，`null` = 该档还没有）；**只含训练效果**；查不到上限时为 `null`
- `support_card.unique`：`text`（category 155 原文，条件型加成自带发动条件）/ `conditional` / `need_lv` / `unlock_breakthrough`（第几突起练得到，`null` = 练不到）/ `items[]`（普通槽位的 `label` + `value`，条件型为空）
- `support_card.unique_effects[]`：原始槽位 `slot` / `effect_type` / `label` / `value` / `need_lv` / `kind`（`plain` / `conditional`，后者 `label` 与 `value` 为 `null`）
- `support_card.skills[]`：提示技能（与技能表同构）
- `support_card.effect_source`：效果名称的来源说明

## 面板

侧栏「图鉴」：搜索结果 9 条三列、两行截断、可展开；点开详情为 880px 宽的弹窗：

- 马娘：属性 + **技能（N）** 列表，每条带稀有度、描述、解锁星级。
- 协助卡：属性 + **突破效果** 表（效果 × 0/1/2/3/4 突，表头标该档等级上限；与上一档不同的数值加粗）+ 口径说明 + **固有加成**（官方文案 + 普通槽位的「名称 +值」+ 解锁条件；条件型加成标注「只有条件与效果名」）+ **提示技能（N）**。
- 技能：属性 + 完整描述。

## 已知限制

- 条件型加成（`type_N` 101–120）**只显示主数据原文，不算数值**：`value_N` 的含义随类型变化（阈值 / 条数上限 / 效果值），主数据没有逐类型参数表，强行解读会给出错误数字。要补的话得逐类型建映射并逐条核对。
- 效果名优先用主数据 151；老 mdb 快照缺这张字典时回落到 BWiki（仅 1–31），31 之外的普通类型仍显示「原始值 N」。
- `support_card_unique_effect` 只有两个槽位；主数据若扩到 3 个槽位需要改导入与展示。
- 未做「技能升级前后的替换关系」查询（该能力在 `modules/skills`）。
- 不展示抽卡概率、碎片兑换与觉醒材料消耗。
