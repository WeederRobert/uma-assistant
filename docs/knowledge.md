# 知识库模块（文档检索 + 结构化数据优先）

`modules/knowledge` 负责「问一句 → 给出带依据的回答」。2026-10-28 起改成**先查库、再查文档**：

```
问题 ──► 话题识别（大赛/赛道/技能/卡池/图鉴）
          │
          ├─ 命中 → 调对应模块的接口 → 结构化事实块（含端点 / 表 / 数据版本）
          │        └─► 与文档片段一起交给 LLM；LLM 不可用时直接展示事实块
          │
          └─ 未命中 → 只走向量检索 → 回答标「来自文档」（mode=documents_only）
```

这样「这届大赛终直多长」这类问题拿到的是当前 `master.mdb` 的数值，而不是攻略文档里可能过期的说法。

## 话题与实体识别

| 话题 | 触发关键词（节选） | 查什么 |
| --- | --- | --- |
| `champions` | 大赛 / 冠军大会 / 群英联赛 / CM / 杯 | `/api/races/champions`（未来优先） |
| `course` | 赛道 / 终直 / 坡道 / 分段 / 弯道 / 直线 | `/api/races/courses[/{id}]` |
| `skill` | 技能 / 固有 / 觉醒 / 发动条件 | `/api/skills/skills?q=` + 详情 |
| `gacha` | 卡池 / 招募 / 池子 / 抽卡 | `/api/gacha/banners` |
| `catalog` | 属性加成 / 天赋 / 协助卡 / 支援卡 / 马娘 | `/api/catalog/search` + 详情 |

**话题顺序有意义**：`champions` 排在 `course` 前面，因为「这届大赛的赛道」要先用大赛记录里的 `course_id` 定到具体赛道。

**实体识别不靠分词**：中文问句没有空格，所以

1. 赛道先用库里的赛场词表（`/api/races/status` 的 `venues`）在问句里找最长匹配，问句里带 `1200`/`2500` 这类数字时按距离挑；
2. 技能、图鉴用 8→2 字的滑动窗口去查搜索接口，命中即停（单次是本地 HTTP，整体上限 `max_probes=120`）。

## 三种回答模式（`ChatResponse.mode`）

| mode | 含义 |
| --- | --- |
| `ok` | 结构化命中 + LLM 组织语言；提示词里第一条就是结构化事实块 |
| `structured_only` | 结构化命中但没配 `DEEPSEEK_API_KEY`：直接返回事实清单，并在开头写明「未配置模型」 |
| `documents_only` | 结构化没命中，只走文档检索；回答要标注「来自文档，可能不是当前数据」 |

结构化事实块形如：

```
[结构化] 赛道几何（来源 /api/races/courses/10506 · 表 races_courses / races_segments / races_slopes · 数据版本 mdb-cn-20261022）
- 中山 草地 2500m（10506）：中山 · 草地 · 2500m · 方向 右
- 终直 300m；直线 4 段、合计 1104m（含开场切线 100m）
```

## 冲突处理

提示词里明确要求：**结构化数据优先于文档**，两者冲突时以库内数据为准并说明冲突。这样「文档说终直 500m、库里 300m」时不会给出模糊答案。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/knowledge/chat` | 返回 `answer` / `sources` / `mode` / `structured`（`tried` / `facts` / `misses` / `errors`） / `data_version` |
| POST | `/api/knowledge/search` | 只做向量检索（不触发结构化查询，也不调 LLM） |
| GET | `/api/knowledge/stats` | 文档数、块数、模型、`llm_configured`、数据版本 |

## 面板

侧栏「知识库」的提问区下方多一块**依据**：列出命中的结构化事实（标签、来源接口、涉及表、数据版本、前几条内容）与 `mode` 标签；没命中时显示「结构化数据没命中，下面的回答来自文档」，`misses` / 接口错误也会逐条列出。

未配置 `DEEPSEEK_API_KEY` 时提问按钮不再禁用，只提示「只会给出结构化查询结果」。

## 配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `UMA_KNOWLEDGE_STRUCTURED_ENABLED` | `true` | 关掉就回到纯 RAG |
| `UMA_KNOWLEDGE_STRUCTURED_BASE_URL` | 空（用内核 `host:port`） | 模块自调用的地址；单独部署时可指向别处 |
| `UMA_KNOWLEDGE_STRUCTURED_TIMEOUT` | `6.0` | 单次自调用超时（秒） |

## 已知限制

- **只覆盖 5 个话题**：相性（需要两个角色）、BD 剧情、活动这类问题仍只走文档。
- **实体识别是启发式**：问句里没有库内的名字时会 miss（例如「那个很长的直线赛道」），`misses` 会如实说明。
- **自调用依赖端口**：`app.desktop` 会把实际监听端口写回 settings；如果绕过 `run_server` 直接 `uvicorn app.main:app`，需要显式设 `UMA_KNOWLEDGE_STRUCTURED_BASE_URL`。
- **冲突只做提示不做裁决**：冲突时让 LLM 说明取舍，不自动丢弃文档片段。

## 可选组件与精简安装包

`chromadb`（向量库）、`sentence-transformers` + `torch`（向量化）与 `numpy` 都是**延迟导入**的：

- 模块加载时**不**导入它们，所以精简安装包（`packaging/uma-assistant.spec` 默认产物，约 45 MB）里 `knowledge` 仍能正常加载；
- 缺组件时只影响「上传文档 / 检索片段」，调用时抛 `RuntimeError`，提示改用手感完整版或 `uv sync`；
- 「结构化数据优先」这条路径只走本机 HTTP 接口，不依赖上述组件，因此在精简包里照常可用。

完整版（`UMA_BUNDLE_ML=1`）才带文档 RAG，体积见 [packaging.md](packaging.md)。