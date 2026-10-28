# 图鉴模块（马娘 / 协助卡 / 技能）

`modules/catalog` 把 `master.mdb` 里的卡与技能搬进本地 SQLite，提供检索与**详情**。详情不只是「名字 + 描述」：马娘要看得到能学的技能组，协助卡要看得到训练效果数值、固有加成与提示技能。

## 数据表

| 表 | 内容 | 来源 |
| --- | --- | --- |
| `catalog_characters` | 马娘卡：名称、称号、稀有度、限定、五项天赋加成 | `card_data` + `text_data` 5/6 |
| `catalog_support_cards` | 协助卡：名称、系列名、稀有度、卡类型、技能组 id | `support_card_data` + `text_data` 6/76 |
| `catalog_skills` | 技能：名称、描述、稀有度、分类、标签 | `skill_data` + `text_data` 47/48 |
| `catalog_character_skills` | 马娘能学的技能 + 解锁星级 | `card_data` → `available_skill_set` |
| `catalog_support_effects` | 协助卡训练效果：初始值 + 各突破等级的值 | `support_card_effect_table` |
| `catalog_support_unique` | 协助卡固有加成（一行两个槽位） | `support_card_unique_effect` |
| `catalog_support_skills` | 协助卡提示技能 | `single_mode_hint_gain`（`hint_gain_type = 0`） |

当期简中服快照（`mdb-cn-20261022`）导入结果：马娘 161 / 协助卡 362 / 技能 1289 / **马娘技能 1113 / 训练效果 3120 / 固有加成 516 / 提示技能 2296**。

## 三条连接键（写错了会静默 0 行）

1. 马娘 → 技能：`card_data.available_skill_set_id` 必须 JOIN `available_skill_set.available_skill_set_id`；用 `available_skill_set.id` 匹配 0 行（那张表的 `id` 只是自增序号）。
2. 协助卡 → 提示技能：`single_mode_hint_gain.support_card_id = support_card_data.id`，且**只有 `hint_gain_type = 0`** 的行 `hint_value_1` 才是 `skill_id`（2296/2296 能 JOIN 到 `skill_data`）；其它取值是别的含义，不能混用。
3. 协助卡技能组（历史坑，本模块未用）：`support_card_data.skill_set_id / 1000` 才能对上 `skill_set.id`。

`need_rank` 是解锁所需星级：`0` 表示初始可用，`2`–`5` 表示要升到对应星级。详情里显示为「初始 / ★N 解锁」。

## 训练效果口径

`support_card_effect_table` 一行一个效果类型，`init` 是初始值，`limit_lv5..limit_lv50` 是各突破等级**达到的值**，`-1` 表示该档不提升（沿用上一档）。详情表里：

- 「初始」取 `init`，`-1` 显示 `—`；
- 「各突破等级」只列出有变化的档位（`5: +10 · 15: +15 …`）；
- 「上限」取所有档位的最大值。

**效果名称没有主数据字典**：`support_card_effect_table.type` 与 `support_card_unique_effect.type_N` 只是编号。名称取自 BWiki「支援卡效果一览」（revid 35946，2026-10-27 抓取，ID 1–31），写死在 `modules/catalog/labels.py` 的 `SUPPORT_EFFECT_LABELS`；**31 以外的取值只显示「原始值 N」**，不猜。详情页会标注这一来源。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/catalog/stats` | 各表条数与数据版本 |
| GET | `/api/catalog/search?q=&kind=&limit=` | 名称检索（`character` / `support_card` / `skill`） |
| GET | `/api/catalog/items/{kind}/{id}` | 详情：基础属性 + 描述 + **kind 专属区块** + 数据版本 |

详情里新增的字段：

- `character.skills[]`：`skill_id` / `name` / `description` / `rarity_label` / `need_rank`
- `support_card.effects[]`：`effect_type` / `label` / `init` / `levels{等级: 值}` / `max`
- `support_card.unique_effects[]`：`slot` / `effect_type` / `label` / `value` / `need_lv`
- `support_card.skills[]`：提示技能（与技能表同构）
- `support_card.effect_source`：效果名称的来源说明

## 面板

侧栏「图鉴」：搜索结果 9 条三列、两行截断、可展开；点开详情为 880px 宽的弹窗：

- 马娘：属性 + **技能（N）** 列表，每条带稀有度、描述、解锁星级。
- 协助卡：属性 + **训练效果（N）** 表格（效果 / 类型 / 初始 / 各突破等级 / 上限）+ 来源说明 + **固有加成** + **提示技能（N）**。
- 技能：属性 + 完整描述。

## 已知限制

- 效果名称来自 BWiki 而非主数据，BWiki 改表或新增 32+ 类型时需要更新 `SUPPORT_EFFECT_LABELS` 并补 revid。
- `support_card_unique_effect` 只有两个槽位；主数据若扩到 3 个槽位需要改导入与展示。
- 未做「技能升级前后的替换关系」查询（该能力在 `modules/skills`）。
- 不展示抽卡概率、碎片兑换与觉醒材料消耗。
