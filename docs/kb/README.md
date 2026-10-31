# 知识库（KB）：来源、纳入口径与缺口

本目录存放助手可直接导入 `knowledge` 模块的机制类知识文档。它解决的是「在没有 `master.mdb` 时，助手如何仍能回答正确的机制问题」，因此**每一条事实都必须能追溯到一份可公开访问的来源**。

> 与 `docs/research.md` 的分工：research.md 记录「用什么数据源、能不能用」的调研结论；本目录记录「已经核实、可以对外回答」的事实本体。

## 1. 纳入口径（什么算「足够正确」）

一条内容要写进本目录，必须满足下列之一：

| 判据 | 说明 | 标注级别 |
| --- | --- | --- |
| 单一权威源，且与游戏内表现不冲突 | 官方页面或该领域唯一的权威二手源 | B |
| 两个及以上互相独立的来源一致 | 例如官方页 + 维基 + 社区 wiki 描述同一机制 | B |
| 官方公告 / 官方站原文直接陈述 | 官方运营、版本更新公告、官方系统介绍页 | A |

以下情况**一律不得写入**，只能登记到「已知缺口」：

- 仅凭记忆、社区口口相传、无法给出 URL 的数值或机制。
- 来源之间互相矛盾且无法判定谁更新（矛盾本身可以写，但要写明矛盾和各自出处）。
- 涉及简中服特有时点、卡池进度的内容——除非有官方简中公告或可核对的简中服数据；译名可以用 S7 文本表或 S8 BWiki 页名补充，但必须标注可信级别，且官方文本优先。
- 具体数值公式（系数、上限、概率）在来源中未明确给出时，不得自行推导或估算。

每条事实的写法约定：

1. **不复制来源原文表达**，用自己的话概述事实（来源多为 CC BY-SA 或社区版权内容，概述 + 署名是安全做法）。
2. 文件头必须写明来源 URL、来源版本号、抓取时间、可信级别、已知缺口。
3. 版本敏感的数字（例如某剧本的实装日期）必须带「截至某来源版本」限定语。

## 2. 可信级别定义

| 级别 | 含义 | 使用方式 |
| --- | --- | --- |
| A | 官方（Cygames / 简中服运营方）原文 | 可直接断言 |
| B | 权威二手源（维基百科、成熟社区 wiki 的机制页） | 可断言，但涉及数值时提示「以游戏内为准」 |
| C | 社区整理、可能已归档的内容 | 只能作为「社区普遍理解」呈现，必须带时效提示 |

本目录当前**没有 A 级来源能覆盖机制细节**：官方站只有宣传口径的系统介绍页，没有数值/规则文档。因此机制细节以 B 级为主。

## 3. 来源登记

抓取时间：非 BWiki 来源统一为 **2026-10-25**；BWiki 为 **2026-10-26**（本地时区 UTC+8）。

| ID | 来源 | URL | 版本信息 | 级别 | 许可 / 可用性 |
| --- | --- | --- | --- | --- | --- |
| S1 | 日文维基百科「ウマ娘 プリティーダービー」 | https://ja.wikipedia.org/wiki/ウマ娘_プリティーダービー | revid 111448118，时间戳 2026-10-20T05:06:50Z | B | CC BY-SA 4.0，需署名与同协议共享 |
| S2 | 官方站「ゲームシステム」 | https://umamusume.jp/contents/game/system/ | 页面无版本号，抓取时内容口径覆盖至 2026 年剧本 | A（但只到宣传级粒度） | robots.txt 允许抓取 |
| S3 | uma.guide「Career Mechanics」 | https://uma.guide/guides/career-mechanics | 无版本号，面向 2025 年国际服 | C | robots.txt `Allow: /`；内容为社区贡献（页面署名 Contributors） |
| S4 | uma.guide「Race Mechanics - Stats」 | https://uma.guide/guides/race-stats | 页面自带「archived」标记 | C | 同上 |
| S5 | uma.guide「Beginner's Guide」 | https://uma.guide/guides/beginners | 面向 2025 年国际服 | C | 同上 |
| S6 | umapyoi.net API | https://umapyoi.net/api/v1/character 、/api/v1/support | 无显式版本号 | C | robots.txt 仅禁 `/api/v1/mun` 等少数路径 |
| S7 | 简中服 `master.mdb`（社区存档，历史参照） | https://github.com/ChihyuenWang/Umamusume-MasterMDB/tree/main/zh-CN | 提交 `zh-CN 2023-10-3`（2023-10-03）；47 张育成卡 / 446 个技能。该仓库 `zh-CN` 目录此后未再更新（`zh-TW` 止于 2024-06-27、`ja-JP` 止于 2024-11-28），仓库已于 2025-04 归档 | **A**（但只是 2023-10 快照，**不能代表当前简中服**） | 游戏本体资源，仅本地读取，不随仓库分发。2026-10-26 起本地库已改用 S10 的当期数据，S7 仅作历史对照 |
| S8 | BWiki 赛马娘WIKI（中文页面） | https://wiki.biligame.com/umamusume/ | 逐页 revid 见 3.2；页面发布于 2022-06 至 2026-05 | B（页面直接列出的机制与名称）/ C（具体数值与社区算法） | 用户贡献内容，版权归原作者与站点；仅读取公开页面、概述事实并署名，不批量转载。robots.txt 与访问限制见 3.3 |
| S9 | BWiki `Data:Course/jian data.json`（简中赛道几何） | https://wiki.biligame.com/umamusume/Data:Course/jian%20data.json | revid 142169，2026-05-13T10:32:38Z | C | 社区维护的数据页；完整数据由 `races` 模块结构化导入，本目录只保留 6 条示例 |
| S10 | 本机客户端 `master.mdb`（简中服 2.0.2） | 模拟器 `com.bilibili.umamusu` → `/data/data/com.bilibili.umamusu/files/master/master.mdb` | 2026-10-22 19:37，22.8 MB，sha256 `e0da18e6b9d1`；161 育成卡 / 362 协助卡 / 1289 技能 / 61801 文本 | **A**（与当前客户端一致） | 用户本机游戏数据，仅本地读取；提取方式见 3.4，不随仓库分发 |

> S3–S5 是**国际服（Global，2025 年上线）**视角，术语为英文（Carats / Team Trials / Wit 等），与简中服译名不同；机制骨架与日服一致，但**剧本实装顺序、卡池、活动节奏完全不同**。引用机制结论时不要引用其进度类内容。

> S8 是社区 wiki，不是官方来源。同一 wiki 同时维护日服、繁中服与简中服页面：带「简中」前缀的页面才是简中服条目，其余页面多为日服内容的中文翻译。引用时必须写明页面级服务器口径（见 3.2），不能把日服页面的数值直接说成简中服现状。

### 3.1 明确排除的来源

| 来源 | 排除原因 |
| --- | --- |
| Game8（game8.jp/umamusume） | `robots.txt` 明确 `Disallow` GPTBot / ClaudeBot / DeepSeekBot / CCBot / anthropic-ai 等 AI 抓取方。**不再访问**，已获取的临时缓存已删除。 |
| 萌娘百科（zh.moegirl.org.cn） | `robots.txt` 的 `User-agent: *` 段 `Disallow: /zh` 且 `Disallow: /*action=`，即正文与 API 均不对通用爬虫开放。仅作为人工查阅入口，不做自动采集。 |
| altema.jp | 返回 403，不可用。 |
| 简中服官方站（`*.biligame.com`、`game.bilibili.com/umamusume`） | 本机 DNS 无法解析，未能取得任何简中官方原文。 |

### 3.2 S8 页面级版本与服务器口径

| 页面 | URL | revid / 时间戳 | 服务器口径 | 本目录用途 |
| --- | --- | --- | --- | --- |
| 育成状态 | https://wiki.biligame.com/umamusume/育成状态 | 121529 / 2024-12-09T12:03:25Z | 日服主页面，含简中文本列 | [09-status-effects.md](./09-status-effects.md) |
| 英雄联赛 | https://wiki.biligame.com/umamusume/英雄联赛 | 89566 / 2023-07-13T05:01:30Z | 日服教程中译；页面早于简中服上线 | [04-races.md](./04-races.md) 名称对照 |
| 群英月赛 | https://wiki.biligame.com/umamusume/群英月赛 | 105787 / 2023-11-30T14:18:31Z | 中文页，CM 历史与译名 | [04-races.md](./04-races.md) 名称对照 |
| 传奇竞赛 | https://wiki.biligame.com/umamusume/传奇竞赛 | 119640 / 2024-09-20T08:26:37Z | 中文页，未标注服务器；标题与门票名称混用 | [04-races.md](./04-races.md) |
| 日常竞赛 | https://wiki.biligame.com/umamusume/日常竞赛 | 43433 / 2022-06-25T08:12:57Z | 日服口径中译；页面早于简中服上线 | [04-races.md](./04-races.md) |
| 相性计算器计算方法 | https://wiki.biligame.com/umamusume/相性计算器计算方法 | 140988 / 2026-04-18T00:23:44Z | 通用社区算法（非官方公式） | [06-inheritance-and-factors.md](./06-inheritance-and-factors.md) |
| 简中比赛 | https://wiki.biligame.com/umamusume/简中比赛 | 114667 / 2024-05-13T09:53:36Z | **简中服**赛事列表 | 未直接引用；P3-1 结构化导入参考 |
| 简/小心波澜炮！ | https://wiki.biligame.com/umamusume/简/小心波澜炮！ | 96681 / 2023-09-05T12:43:55Z | **简中服**技能页 | [05-skills.md](./05-skills.md) 触发条件示例 |
| 简/热血进化的骑乘！ | https://wiki.biligame.com/umamusume/简/热血进化的骑乘！ | 142819 / 2026-05-17T11:50:47Z | **简中服**技能页 | [05-skills.md](./05-skills.md) 触发条件示例 |
| Data:Course/jian data.json | https://wiki.biligame.com/umamusume/Data:Course/jian%20data.json | 142169 / 2026-05-13T10:32:38Z | **简中服**赛道几何 | [10-course-geometry.md](./10-course-geometry.md) |
| 简中卡池 | https://wiki.biligame.com/umamusume/简中卡池 | 145361 / 2026-08-23T13:49:24Z | **简中服**卡池（含预测） | 不入库；`events` 模块唯一抓取的卡池来源（繁中 / 日服来源已下线）；未开始的卡池以 `gacha` 模块读 mdb 为准 |

### 3.3 BWiki 访问方式与限制

- `robots.txt` 于 2026-10-26 在浏览器上下文读取成功，`User-agent: *` 段允许文章页与 `api.php`；禁止 `index.php?`、`File:`、`User`、`MediaWiki:`、`模板:`、`特殊:`、`diff`、`oldid` 等路径。
- 普通 HTTP 客户端（curl / httpx）直接请求会得到 **HTTP 567**（EdgeOne 反爬）。本次通过真实浏览器上下文按需读取，未尝试绕过验证。
- 读取方式：逐页串行、间隔 ≥1 秒、只读公开页面；不并发、不批量镜像、不下载图片/语音等资源。
- 引用方式：概述事实 + 标注页面 revid 与抓取时间；不整段复制页面正文。页面版权归原作者与站点。
- 时效数据（卡池、活动、维护公告）**不写入知识库**：它们会立刻过期，应由 ROADMAP P2 的 `events` 模块抓取并带 `fetched_at` 展示。

### 3.4 当前数据版本与更新流程

判断「我现在用的是哪一版」只看应用暴露的元信息，不看文件名，也不看 KB 文件里的日期：

| 字段 | 位置 | 说明 |
| --- | --- | --- |
| `data.version` | `GET /api/catalog/stats` → `data_version.version`；`GET /api/knowledge/stats` → `data_version` | 导入时写入的版本串 |
| `data.game_version` | 同上 `.game_version` | 游戏版本号；只有显式传入才存在 |
| `data.updated_at` | 同上 `.updated_at` | 导入时间（UTC） |
| `data.source` | 同上 `.source` | `<文件名> (sha256:<12>)` |

**`mdb-<日期>` 是导入日期，不是数据快照日期。** 导入第三方存档时必须显式给版本号，否则 2023 年的快照会被标成导入当天的版本。

> 截至 2026-10-26，本地库版本为 `mdb-cn-20261022`（`game_version=2.0.2`），`source` 是 `master.mdb (sha256:e0da18e6b9d1)`，数据集 161 育成卡 / 362 协助卡 / 1289 技能 / 61801 文本。数据来自用户本机 MuMu 模拟器里 `com.bilibili.umamusu` 客户端的
> `/data/data/com.bilibili.umamusu/files/master/master.mdb`（22.8 MB，文件时间 2026-10-22 19:37），不再是 2023-10 的社区存档。
>
> 版本演进留痕：2023-10 社区存档（`mdb-cn-20231003`，47/154/446/22425）→ 当前本机客户端（`mdb-cn-20261022`，161/362/1289/61801）。旧库备份为 `data/app.db.bak-20231003`。

#### 三条刷新路径

| 路径 | 能刷新什么 | 操作 |
| --- | --- | --- |
| 本机客户端 `master.mdb`（A，唯一与当前版本一致） | 马娘 / 协助卡 / 技能 / 全部文本术语 | 模拟器 / 真机：`uv run python scripts/fetch_mdb_android.py` 自动提取，再 `uv run python scripts/import_master_mdb.py "data/mdb/master.mdb" --game-version <国服版本> --data-version mdb-cn-YYYYMMDD`；已手工拿到文件时直接跑后者 |
| BWiki 中文页（B / C） | 状态、赛事名、技能触发条件、赛道几何、卡池 | 按 3.2 逐页取最新 revid，改对应 KB 文件后重跑 `uv run python scripts/import_kb.py` |
| 官方公告（A，但结构不稳定） | 版本号、卡池起止、维护时间 | 归 P2 `events` 模块，带 `fetched_at`；**不写入 KB** |

公开的 mdb 存档当前都已过时：`ChihyuenWang/Umamusume-MasterMDB` 的 `zh-CN` 止于 2023-10-03、`ja-JP` 止于 2024-11-28（仓库已归档），`SimpleSandman/*` 系列全部归档。要拿到当期简中数据，只能从**本机已安装的客户端**提取。

#### 从本机客户端取最新 mdb

游戏每次更新资源都会重写这份数据库，所以「最新」只取决于本机客户端是否已更新到当期。模拟器（MuMu / 夜神等）路径：

```powershell
uv run python scripts/fetch_mdb_android.py --check    # 只看有没有新版本，不写文件
uv run python scripts/fetch_mdb_android.py            # 提取到 data/mdb/master.mdb
```

脚本会：自动找 adb（含模拟器自带）→ 连接设备 → 校验包名 → 用 `su` 把 `/data/data/com.bilibili.umamusu/files/master/master.mdb` 复制到共享存储再 `adb pull` → 校验 SQLite 头与 4 张必需表 → 打印 sha256 与行数 → 与本地库 `data.source` 比对，直接告诉你「已是最新」还是「本地是旧版」。

前置条件与限制：

- 模拟器需开启 root（MuMu 默认开启）；真机无 root 时无法读取 `/data/data`，脚本会明确报错而不是静默失败。
- 若报「找不到 master.mdb」，先在游戏里完成一次资源下载/更新再重试。
- 提取的是游戏数据，**不随仓库分发**：`data/` 与 `*.mdb` 已在 `.gitignore` 中忽略。

#### 导入新 mdb 后必须复核的 KB 位置

| 位置 | 复核什么 |
| --- | --- |
| [08-glossary.md](./08-glossary.md) | 新增系统与新术语的简中官方名；证据列写文本 id |
| [09-status-effects.md](./09-status-effects.md) | 状态名称、效果与数值 |
| [05-skills.md](./05-skills.md) | 技能 ID、触发条件、新增进化技能 |
| [04-races.md](./04-races.md) | 赛事名与规则 |
| [10-course-geometry.md](./10-course-geometry.md) | 新增赛道、赛道几何改动 |
| 各篇文件头 | 来源版本 / 抓取时间 / 已知缺口三行 |

#### 记录规范

1. 抓了 BWiki 就更新 3.2 表的 revid 与时间戳；导了 mdb 就更新本节的本地版本说明。
2. KB 文件头的「来源版本」「抓取时间」必须与本次实际读取的版本一致，不写「最新」这类无法复核的措辞。
3. 同步更新 `ROADMAP.md` 的「已交付」与「已知缺口」。
4. 不把 mdb 本体与整表游戏文本写进仓库；只保留派生事实、校验和与快照元信息。

## 4. 已知缺口总表

| 缺口 | 影响 | 当前结论 | 后续动作 |
| --- | --- | --- | --- |
| 结构化数据随客户端更新而变 | 客户端没更新时，本地库就是上一版；无法「实时」反映游戏内容 | **已解决**：`scripts/fetch_mdb_android.py` 可从本机客户端提取当期 mdb 并自动比对 sha256（见 3.4）。当前库 `mdb-cn-20261022` 与客户端 2.0.2 一致 | 每次游戏大版本更新后重跑提取 + 导入，并复核 5 个 KB 位置 |
| 具体数值公式（最高速度、加速度、耐力消耗、坡道与天气修正系数） | 助手无法做精确的赛道/育成收益计算 | **未覆盖**：本次未在 BWiki 找到公式；本目录不自行推导。天气 / 马场只有**出现率**（`race_condition`，已由 `modules/races` 的「场地状态出现率」提供），没有修正系数 | 需要带版本的社区公式库；出现率已可用 |
| 训练菜单在各剧本下的完整数值表 | 只能定性说明「哪种训练涨什么」 | **未覆盖，且不再排期**：育成规划已从 ROADMAP 移除（缺训练收益与事件结果两类表，结论无法验证） | 不做；原始表仍留在 mdb，需要时可直接查 |
| 技能完整清单、效果数值、发动条件 | 不能查具体技能的效果数值 | **已解决**：`skills` 模块（P3-3）把 1289 个技能的条件表达式、效果参数、持续时间与冷却结构化，并反查马娘 / 协助卡来源与进化关系（见 [../skills.md](../skills.md)）；`ability_type` / `target_type` / `skill_category` 没有字典表，只回显原始编号 | 编号到中文效果名的映射需外部资料确认；SP 学习成本与提示等级仍无来源 |
| 协助卡各突破等级的训练效果数值 | 「这张卡突破到 3 突有多少友情加成」只能看攻略 | **已解决**：图鉴详情把 `support_card_effect_table` 的等级档位按 `support_card_limit` 折算成 0–4 突五列（每列都是练到该档等级上限后的值）；效果名称改用主数据自带的 `text_data` 151/154；固有效果另起一块，用 `text_data` 155 的成句文案说明，条件型加成（101–120）的发动条件就写在那句话里（见 [../catalog.md](../catalog.md)） | 条件型加成的 `value_N` 是各类型自定义参数，主数据无逐类型说明，因此只显示条件与效果名、不算数值 |
| 状态（コンディション）效果与数值 | 只能泛泛说「有负面状态」 | **已补**：[09-status-effects.md](./09-status-effects.md) 收录 BWiki「育成状态」页的名称、效果与页面数值；获取概率与简中服当前值未覆盖 | 用更新的简中服 `master.mdb` 文本表逐条核对状态名 |
| 赛道几何（分段、终直、坡道） | 不能回答「这条赛道几段直线 / 坡道」 | **已解决**：`races` 模块（P3-1）已结构化全部 107 条赛道的分段、坡道与终直，[10-course-geometry.md](./10-course-geometry.md) 保留 6 条示例作说明；14 条地方 / 海外场地无几何 | 后续赛道更新只需在「赛道」页刷新 BWiki 数据页 |
| 简中服译名与术语 | 少量新系统缺官方文本 | **已改善**：本地库已换用 S10（2026-10-22，2.0.2）当期文本；新增「觉醒」「剧本」技能分类来自 `skill_upgrade_description` / `skill_upgrade_speciality` 参照表。术语表本身尚未按新数据重跑 | 用 S10 重跑术语提取，更新 [08-glossary.md](./08-glossary.md) |
| 简中服版本进度、卡池与活动日程 | 助手不能回答「简中服现在到哪个池」 | **已由模块覆盖，不入库**：`events` 模块抓 BWiki 的**简中服**卡池（不跟踪繁中 / 日服，也不跟踪活动），`gacha` 模块读 `master.mdb` 的 `gacha_data`（含未来卡池，可提前预告）；时效数据仍刻意不写入知识库 | 时效数据会过期，只通过模块 + `fetched_at` 展示 |
| 大赛（CM）当期赛道与规则 | 只能解释赛制，不能给当期作业 | **数据侧已具备**：`champions_schedule`(29) / `champions_round_schedule`(174) / `champions_race_condition`(203) 在 mdb 里，含起止时间、轮次与每轮赛道，已由 `modules/races` 的「大赛（群英联赛）」面板提供。**对手阵容与当届环境使用率仍无来源** | 环境数据需外部统计，不自行编造 |

## 5. 与代码的关系

- 本目录的 `.md` 文件可通过 `knowledge` 模块的 `/api/knowledge/documents` 接口导入（支持 md/txt/pdf，按标题层级切块，标题会进入检索上下文）。
- 批量导入：应用运行时执行 `uv run python scripts/import_kb.py`，默认导入 `docs/kb/*.md`；重复导入会被识别为重复文档，不会产生副本。
- 文件头部的「来源 / 版本 / 抓取时间」会被一并切块，因此检索结果天然带出处，便于回答时引用。当前 11 篇共切分为 165 个块（2026-10-26 实测）。
- 素材改动后重跑 `scripts/import_kb.py` 即可：脚本会先删掉内容已变化的同名旧文档，再导入新版本，避免旧切块继续参与检索。
- **修改本目录的内容等同于修改知识库事实来源**，提交信息请使用 `docs(kb): ...`，并在 PR 描述里写明新增事实的来源 URL 与抓取时间。

## 6. 回答边界（按主题）

汇总各篇的「能答 / 不能答」范围，避免在每篇正文里重复。助手回答时不得越过此表。

| 主题 | 能回答 | 只能给方法论 | 不能回答 |
| --- | --- | --- | --- |
| 训练与属性 | 训练失败后果、干劲作用、体力机制、各菜单产出方向 | 「属性练到多少够用」 | 具体训练收益数值、当前版本数值表 |
| 状态与干劲 | 具体状态的效果方向与页面给出的数值（如训练失败率 ±2% / ±4%）、一次性抵消规则 | 「这个状态该不该留着」 | 状态获取概率、持续回合、简中服当前版本的精确值 |
| 比赛 | CM / LoH 赛制、出走门槛、连斗惩罚、适应性维度、简中名称对照 | 「这次 CM 怎么配队」 | 当期赛道数值、最佳配装、当届环境 |
| 赛道几何 | 本目录收录 6 条示例；`races` 模块可查全部 107 条赛道的分段、终直、坡道位置与长度 | 「这条赛道适合什么跑法」 | 速度/加速度/耐力公式与天气修正系数 |
| 技能 | 颜色分类、负面技能成因、固有与进化技能规则、2 个简中技能页的触发条件 | 「该不该学某技能」 | `ability_type` 等编号的中文含义、SP 学习成本；`skills` 模块可查全部 1289 个技能的条件与效果原始值 |
| 继承与因子 | 继承次数与参与者、相性影响因素与等级阈值、四色因子规则、重抽规则 | 「这只该不该继承」 | 具体马娘组合的相性分数 **已由 `succession` 模块提供**（两人点数 + 六人布局，不含适性）；因子出率仍无来源 |
| 强化与资源 | 觉醒/开花/解锁上限的作用与上限、扭蛋结构 | 「这张卡要不要满破」 | 各阶段消耗数量、卡池强度 |
## 7. 索引

| 文件 | 内容 |
| --- | --- |
| [01-game-overview.md](./01-game-overview.md) | 游戏形态、两类卡片、核心循环、名词体系 |
| [02-career.md](./02-career.md) | 育成流程、TP、回合结构、育成目标、剧本一览 |
| [03-training-and-stats.md](./03-training-and-stats.md) | 五项属性、训练机制、体力/干劲/状态、训练收益影响因子 |
| [04-races.md](./04-races.md) | 比赛结构、等级与粉丝数、适应性、跑法、赛场状态、主要赛事玩法 |
| [05-skills.md](./05-skills.md) | 技能分类、稀有/进化/固有/负面技能、提示等级 |
| [06-inheritance-and-factors.md](./06-inheritance-and-factors.md) | 继承机制、相性、因子种类与星级 |
| [07-progression-and-upgrade.md](./07-progression-and-upgrade.md) | 觉醒、开花、协助卡强化、扭蛋与资源 |
| [08-glossary.md](./08-glossary.md) | 日 / 英 / 简中术语对照（简中列标注核实状态） |
| [09-status-effects.md](./09-status-effects.md) | 育成状态的简中名称、效果与页面数值 |
| [10-course-geometry.md](./10-course-geometry.md) | 简中赛道几何：6 条示例的分段、直线段数与坡道 |

