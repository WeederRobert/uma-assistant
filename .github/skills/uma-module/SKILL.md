---
name: uma-module
description: "当要给 uma-assistant 新增模块或改造已有模块时使用（schema / store / importer / service / api / ui/panel / manifest），例如「做一个相性查询模块」「把卡池模块的面板并到日程」「给赛道模块加一个接口」。覆盖模块七件套、表名前缀、跨模块 HTTP 调用，以及必须同步的文档清单。"
argument-hint: "要新增或改造的模块，例如：新增 succession 相性查询模块"
---

# 新增 / 改造模块（uma-assistant）

目录即模块：`modules/<id>/`，`manifest.toml` 的 `id` 必须等于目录名。新建先复制 `modules/_template/`。

## 七件套与职责

| 文件 | 职责 | 关键点 |
| --- | --- | --- |
| `manifest.toml` | 声明 id / name / order / requires / api 前缀 / ui | `requires` 只影响加载顺序；**不写 `[ui]` 就是只提供接口**（如 `gacha`） |
| `module.py` | `setup(ctx) -> Module(router=..., status=..., on_shutdown=...)` | 启动时做一次 `ensure_*()` 补导入；`status` 给侧栏与 `/api/system/info` |
| `schema.py` | `SCHEMA` + `ensure_schema(db)` | 表名 `<id>_*`；老库加列要写 `PRAGMA table_info` + `ALTER TABLE` 迁移（参考 `modules/races/schema.py`） |
| `store.py` | 纯 SQL 读写 + `meta` 来源信息 | 写导入元信息：路径、sha256 前缀、`mdb_mtime`、导入时间、条数 |
| `importer.py` | 打开 `master.mdb`（`mode=ro`）→ 读表 → 落库 | 缺表要报中文错误或降级进 `skipped`；JOIN 键必须先验证（会静默 0 行） |
| `service.py` | 口径、分组、状态、时区 | 用 `app/kernel/times.py`；返回给面板的字段要做完换算，面板不再算 |
| `api.py` | 路由 | `/status`、列表、`/{id}`（404）、`POST /refresh`（`GachaSourceError` 之类转 422） |
| `ui/panel.js` | `export default { async mount(root, ctx) }` | 用 `ctx.api/escapeHtml/toast`；跨模块只调 HTTP 并降级 |

## 流程

1. **确认数据**：先用 `uma-data-probe` 核实表、列、行数与连接键；把「能解析 / 不能解析」分清。
2. **定表**：只存你要展示与反查的列；一行一实体，主键用业务 id；口径列（`rate` / `opening_straight` / `sentinel`）单独成列并写注释。
3. **写 importer**：`_read_xxx()` 返回值是 `list[tuple]`，`CatalogSummary` 带 `skipped` / `missing_columns`；末尾写 `meta` 与 `data_version`。
4. **写 service**：状态判定、分组、天数、百分比都在这层；返回 `notes` 字段向面板解释限制。
5. **写 api + panel**：先 `GET /status` 再列表；面板首屏只渲染一次，失败段落降级。
6. **`requires`**：只依赖表前缀之外的模块时，宁可写 `requires = []`；需要顺序稳定再加。
7. **测试**：`tests/test_<id>.py`，`build_mdb()` 造最小库，断言导入条数 / 分组 / 换算 / 缺表 / 404。
8. **文档**：`docs/<id>.md`（数据来源表、字段口径表、接口表、面板说明、环境变量、已知限制）+ `README.md` 模块一览 + `docs/architecture.md` 架构图与存储说明 + `ROADMAP.md` 状态与记录 + `docs/kb/README.md` 缺口表。

## 改造既有模块时

- **搬面板**：只搬 UI 时，服务端接口不动；被搬走的一方删干净（引用、监听器、`openXxx` 函数），别留死代码。
- **删来源 / 删功能**：代码删掉的同时处理库内旧数据（参考 `store.purge_sources()`），并在刷新路径里调用；文档写清「为什么下线、怎么恢复」。
- **加字段**：`schema.py` 加列 + 迁移 → `importer` 写值 → `store` 读写 → `service` 输出 → 面板展示 → 文档口径表补一行；旧数据要么重导入，要么给默认值。

## 反模式

- 在 `api.py` 或面板里做业务换算（百分比、天数、时区）。
- 模块之间 `from modules.xxx import ...`。
- 表名不带前缀，或 `manifest.id` 与目录名不一致。
- 没有字典表的枚举自己编中文名；把「未列出 / 未定档 / 推断」当真实数据展示。
- 面板里 `innerHTML` 拼接未经 `escapeHtml` 的文本。
