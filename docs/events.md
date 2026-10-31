# 日程模块（简中服卡池 / 卡池与大赛排期汇总）

`modules/events` 是**日程总页**：BWiki 抓来的简中服卡池、`master.mdb` 的卡池预售排期、大赛排期都在这一页。本文件讲 BWiki 抓取这部分（`events_*` 表与 `/api/events` 接口）；卡池排期见 [gacha.md](gacha.md)，大赛排期见 [races.md](races.md)，两者以 HTTP 接口的形式被这一页引用，不跨模块 import。

「已经开始的卡池」只能从 wiki 拿：`master.mdb` 虽然内置了卡池排期（见 gacha.md），但「这一期什么时候开、BWiki 怎么记」仍以抓取结果为准。`modules/events` 从 BWiki 抓结构化字段后入本地库，界面按时间轴展示，并记录「哪一版变了什么」。**只跟简中服**，不跟踪繁中服与日服。

## 数据来源

单一来源：BWiki 的 MediaWiki API。

```
POST https://wiki.biligame.com/umamusume/api.php
  action=ask
  query=[[分类:简中赛马娘卡池]]|?开始描述|?结束描述|?开始时间|?结束时间|?角色ID|sort=开始时间|order=desc|limit=500
```

`action=ask` 是 Semantic MediaWiki 的查询接口，返回 `query.results[标题].printouts`，比解析 HTML 稳。时间字段优先取文本型 `开始描述` / `结束描述`（形如 `2026/10/24 12:00`），`开始时间` / `结束时间` 是 SMW 日期属性、部分页面为空，只作回退。

### 只跟简中服

**同一个 wiki 同时挂着日服、繁中服、简中服的页面，页面正文里没有服务器标记，服务器只由分类决定。**本项目只跟踪简中服：

| 分类 | 内容 | 服 | 可信级别 |
| --- | --- | --- | --- |
| `简中赛马娘卡池` | 育成（马娘）卡池 | 简中服 | B |
| `简中支援卡卡池` | 协助卡卡池 | 简中服 | B |

繁中服（`繁中赛马娘卡池` / `繁中支援卡卡池`）与日服（`赛马娘卡池`、日服节奏的 `活动`）的来源已于 2026-10-27 **按要求下线**并删除代码：跨服进度与简中服不同步，留着只会让人误以为是简中服的日程。库里残留的旧来源行（含审计）会在下一次刷新时由 `store.purge_sources()` 一并清掉。要恢复只需往 `SOURCES` 里加回条目。

来源里混着占位条目，形如 `无马娘卡池，占位用 20261020`。识别到 `占位` / `待补充` / `敬请期待` 就降为 D 级，默认不在时间轴显示（见「占位行处理」）。

可配置的来源清单在 `modules/events/sources.py` 的 `SOURCES`，改来源只动这一处。

### 可信级别

沿用 `docs/kb/README.md` 的定义：

| 级别 | 含义 |
| --- | --- |
| A | 官方原文 |
| B | 权威二手（分类页的结构化字段） |
| C | 社区整理，可能已过期 |
| D | 占位或来源不明 |

界面上 B 级及以下都在每行右侧显示角标，鼠标悬停可看原文链接与抓取时间。

## 抓取策略

BWiki 对请求频率敏感，且**不返回 `ETag` / `Last-Modified`**（响应头只有 `cache-control: private, must-revalidate`），条件请求拿不到 304。因此做两层保护：

1. **限速**：串行请求，两次之间至少 `UMA_EVENTS_INTERVAL_SECONDS`（默认 1.5 s）。请求过快会返回 HTML 反爬页，识别到非 JSON 直接报 `FetchError`。
2. **TTL 缓存**：响应原文写 `data/modules/events/cache/<来源>.json`，`UMA_EVENTS_CACHE_HOURS`（默认 6）内复用；命中缓存时 `from_cache=true`。面板上的「刷新」按钮显式传 `force=true` 绕过缓存。

失败处理：

- 429 / 500 / 502 / 503 / 504 / 567 按 `UMA_EVENTS_BACKOFF_SECONDS` 指数退避重试，最多 `UMA_EVENTS_RETRIES` 次。
- 重试仍失败时，若有旧缓存则用它继续，响应标 `stale=true` 并在界面提示「素材可能过期」；没有任何缓存才让该来源整体失败。
- 单个来源失败不影响其余来源（每个来源独立 `events_fetches` 审计行）。

## 数据表

三张表，前缀 `events_`：

| 表 | 用途 |
| --- | --- |
| `events_items` | 当前状态；`uid` = `<来源 key>:<页面标题>`，来源里消失的条目走软删除（`removed_at`） |
| `events_fetches` | 每次抓取一行：开始 / 结束时间、是否成功、条数、新增 / 变更 / 移除、是否用缓存、是否 `stale`、错误原因 |
| `events_changes` | **只记真正变化的字段**（`title` / `starts_at` / `ends_at` / `category` / `payload` / `source_level` / `permanent`），一行一个字段，用于回答「这版改了什么」 |

「只对变化写审计」由 `service.diff_items()` 保证：二次刷新时 `added / changed / removed` 全为 0，`events_changes` 不增长。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/events/status` | 来源清单（现在只有 2 个简中服来源）、上次抓取时间、各来源条数与最近一次抓取结果；`servers` 只有 `cn` |
| GET | `/api/events/schedule?servers=cn&kinds=pool&days_before=7&days_after=30&include_placeholders=false` | 时间轴；每条带 `state` / `starts_local` / `ends_local` / `zone` / `same_zone` / `days_left` / `starts_today` / `this_week` / `placeholder` / 等级标签；另有 `near_term` 汇总（见下）。`include_placeholders` 默认 `false`，来源里的占位行不进时间轴，条数记在 `counts.placeholders` |
| GET | `/api/events/history?limit=50` | 最近变更（新增 / 变更 / 移除） |
| POST | `/api/events/refresh` | 抓取；body `{"sources": ["cn-character-pool"], "force": true}`，两者都可省略 |

`state` 取值：`active`（进行中）、`permanent`（来源写「常驻」）、`upcoming`（未开始）、`ended`（已结束）、`unknown`（来源没给时间）。时间轴按 `active/permanent` → `upcoming` → `unknown` → `ended` 分组，进行中按剩余天数升序。

### 近期提醒口径

`schedule` 额外返回 `near_term`，专门回答「有什么要开始 / 什么快结束」：

| 字段 | 含义 |
| --- | --- |
| `horizon_days` | 「即将结束」的提前量，默认 7 天（`UMA_EVENTS_NEAR_DAYS`） |
| `upcoming` / `upcoming_total` | `state = upcoming` 的条目，按开始时间升序，最多列 5 条 |
| `ending_soon` / `ending_soon_total` | `state = active` 且剩余时间 ≤ `horizon_days` 的条目，按结束时间升序，最多列 5 条 |

每条只带面板要显示的字段（`title` / `server_label` / `kind_label` / `starts_local` / `ends_local` / `days_to_start` / `days_left` / `zone`），不带内部 datetime。

**`upcoming` 经常是空的，这不是 bug**：BWiki 的卡池分类页只收录**已经开始**的条目，下一期通常在当前池结束前一两天才出现在页面上。2026-10-27 的库内实测 202 条简中卡池里最晚的 `starts_at` 就是当天，没有任何未来条目，所以「即将开始」为 0 —— 这时「即将结束」才是接下来会变的东西。要拿到新条目，只能在页面更新后重新抓取（见「抓取策略」）。**未来卡池在同一页的「卡池」卡里**（读 `master.mdb` 的内置排期，见 [gacha.md](gacha.md)）。

### 占位行处理

来源页面还没填内容时会留一条 `无马娘卡池，占位用 20261020` 这类占位行（标题含「占位 / 待补充 / 敬请期待」，或既无开始也无结束时间且不是常驻）。它们：

- 仍按 D 级入库，**数据不删**——删了就没法核对来源到底有没有内容，而且下次抓取会重新写回；
- 默认**不进时间轴与近期提醒**（`include_placeholders=false`），隐藏条数记在 `counts.placeholders`，面板显示「已隐藏 N 条来源占位行」；
- 想看时可以勾「显示占位条目」，`include_placeholders=true` 把它们放回时间轴，行上带「占位」标记、等级角标为 D。

### 时区

**所有时间一律按运行环境的系统时区展示**：

- `starts_local` / `ends_local` / `zone` 是系统时区下的文本与偏移标签（如 `2026-10-01 11:00` / `UTC+08:00`）；
- `starts_today` / `this_week` 也按系统时区判定，保证面板上的「今天 / 本周」与用户看到的日期一致；
- `starts_source` / `ends_source` / `source_zone` 保留来源服时区（简中服是 `CST` = UTC+8）下的原始时间；`same_zone` 为真时说明来源时区与系统时区偏移一致（国内机器就是这样），面板不再重复提示，只在偏移不同时补一行「来源时区 CST 12:00」；
- 抓取时间、变更时间在库里以 UTC 存储，前端用 `Date` 解析后按系统时区格式化。**不要直接对 ISO 字符串做 `slice`**，那会把 UTC 时钟当成当地时间（在中国时区差 8 小时）。

## 面板

侧栏「日程」是**时间相关内容的汇总页**，六张卡从上到下：

| 卡 | 数据来源 | 说明 |
| --- | --- | --- |
| 数据来源 | BWiki | 2 条简中服来源的标签、条目数、上次抓取时间；「刷新 BWiki」走 `force=true` 并回报新增 / 变更 / 移除条数 |
| 近期提醒 | BWiki | 「即将开始（N）」按开始时间列出未来条目并标「还有 N 天开始」，为空时说明「BWiki 分类页只收录已经开始的卡池」；「7 天内结束（N）」列出剩余天数不超过阈值的进行中条目。天数按系统时区算 |
| 卡池（master.mdb 排期） | `modules/gacha` | 即将开始 / 进行中 / 未定档 / 最近已结束，带精选卡片与 `type` 原始值；精选默认只列 4 张，剩下的折成可点的 `+N`（点开看全部、再点「收起」）；「刷新卡池排期」调 `POST /api/gacha/refresh`。详见 [gacha.md](gacha.md) |
| 大赛（群英联赛） | `modules/races` | 排期列表 + 轮次窗口 + 晋级门槛；**「只看未结束」默认勾选**，取消后显示全部 29 届。详见 [races.md](races.md) |
| 时间轴（BWiki 简中服卡池） | BWiki | 每行左边是系统时区下的时间（下方标 `UTC+08:00` 这类偏移），右边是标题、剩余天数、等级 / 类型 / ID 数角标；今天与本周加色条。**「即将开始」组即使为 0 也会显示** |
| 最近变更 | BWiki 审计 | 最近 20 条审计行 |

跨模块的两个卡（卡池 / 大赛）只通过 HTTP 调各自的接口，**不 import 别的模块**：`manifest.toml` 的 `requires = ["sync", "gacha", "races"]` 只用于保证加载顺序；任一接口失败时该卡降级成一行错误提示，其余内容照常显示。

时间轴的两个细节：

- **占位条目**默认隐藏，工具条右侧写明「已隐藏 N 条来源占位行」；勾「显示占位条目」可把它们放回时间轴核对来源。
- 服务器筛选按钮已随繁中 / 日服来源一起移除：只剩简中服，没有可切换的对象。

## 环境变量

`UMA_EVENTS_` 前缀，全部有默认值：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `UMA_EVENTS_API_URL` | `https://wiki.biligame.com/umamusume/api.php` | 抓取端点 |
| `UMA_EVENTS_USER_AGENT` | 带项目标识的 UA | 请求头 |
| `UMA_EVENTS_REFERER` | wiki 首页 | 请求头 |
| `UMA_EVENTS_INTERVAL_SECONDS` | `1.5` | 请求最小间隔 |
| `UMA_EVENTS_TIMEOUT_SECONDS` | `30` | 单次请求超时 |
| `UMA_EVENTS_RETRIES` | `3` | 退避重试次数 |
| `UMA_EVENTS_BACKOFF_SECONDS` | `3` | 退避基数 |
| `UMA_EVENTS_MAX_RESULTS` | `500` | SMW `limit` |
| `UMA_EVENTS_CACHE_HOURS` | `6` | 缓存有效期，`0` 表示每次都请求 |
| `UMA_EVENTS_SERVERS` | `cn` | 启用的服务器，逗号分隔（目前只有 `cn`） |
| `UMA_EVENTS_DAYS_BEFORE` | `7` | 时间轴向前窗口 |
| `UMA_EVENTS_DAYS_AFTER` | `30` | 时间轴向后窗口 |
| `UMA_EVENTS_NEAR_DAYS` | `7` | 「近期提醒」的提前量：剩余天数不超过它就算「即将结束」 |

## 已知限制

- **拿不到 304**：BWiki 不回 `ETag` / `Last-Modified`，只能靠 TTL 缓存 + 强制刷新；间隔内的数据可能滞后最多 6 小时。
- **不跟踪活动**：日服节奏的 `活动` 分类随日服来源一起下线，现在只剩简中服卡池；要看活动只能自己翻 wiki。
- **占位条目默认不出现在时间轴**：来源页面留的 `占位` 行按 D 级入库但不显示（勾「显示占位条目」可见）。数据不删，否则没法核对来源确实没有内容，而且下次抓取会重新写回。
- **分类改名会静默失效**：来源靠分类名匹配，BWiki 改分类名后该来源会返回 0 条而不会报错。`/api/events/status` 的条数归零是唯一信号。
- **没有「未公布」的未来排期**：BWiki 分类页只收录已开始的卡池，所以本模块的「即将开始」通常是 0；**未来卡池看同一页的「卡池」卡**（读 `master.mdb` 的 `gacha_data`，当期快照排到 2027-01-18），大赛排期看同一页的「大赛」卡。本模块不推测未公布的日期。
