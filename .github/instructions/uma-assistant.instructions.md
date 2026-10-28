---
description: "当在 uma-assistant 项目（赛马娘简中服桌面助手）里工作时使用：新增或改造模块、修改 master.mdb 导入与 SQLite 表结构、改动 modules/*/ui/panel.js、BWiki 抓取、时区处理，或运行它的 lint / 测试 / 浏览器验收。涵盖模块七件套、数据口径纪律、BOM 陷阱与运行时数据规则。"
applyTo: "uma-assistant/**"
---
# uma-assistant 项目规则

使用中文与用户交互，Windows 桌面应用：pywebview 窗口 + 本地 FastAPI 内核（只监听 `127.0.0.1`），前端是无构建的原生 JS。
「是什么」看 `README.md`，「为什么」看 `docs/`，「接下来做什么」看 `ROADMAP.md`。此处只写**必须遵守**的约定。

## 命令（都在 `uma-assistant/` 内执行）

```powershell
uv run ruff check .                       # 静态检查
uv run ruff format --check .              # 格式检查（改动后先跑 uv run ruff format .）
uv run pytest                             # 全量测试
uv run pytest tests/test_races.py -q      # 只跑某个模块
uv run python -m app.desktop --no-window --port 8742   # 后台起服务（前端验收用）
.\start.cmd                               # 正式启动（窗口模式）
```

改动完成前必须：目标测试通过 → 全量 `ruff check` / `format --check` / `pytest` 通过。纯文档改动不用跑测试。

## 模块约定（七件套）

一个目录 = 一个模块，`modules/<id>/`：`manifest.toml` + `module.py`（`setup(ctx) -> Module`）+ `schema.py` + `store.py` + `importer.py` + `service.py` + `api.py` + `ui/panel.js`，新建时复制 `modules/_template/`。

- `manifest.toml` 的 `id` 必须等于目录名；表名一律 `<id>_*`。
- `requires` 只决定**加载顺序**，不传递 Python 依赖。**模块之间不互相 import**；跨模块展示只在面板里调对方 HTTP 接口，并降级处理失败（见 `modules/events/ui/panel.js` 的卡池 / 大赛卡）。
- 没有 `[ui]` 的模块只提供接口、不进侧栏（如 `modules/gacha`）；`static/app.js` 的 `renderSidebar()` 按 `ui.panel` 过滤。
- 口径（百分比、状态判定、时区换算、降级规则、告警）写在 service/importer 并回显在接口里，面板只做展示与格式化。

## 数据来源纪律

- **能核实的才写**：`master.mdb` 里没有字典表的枚举（`ability_type` / `target_type` / `skill_category` / `gacha_data.type` 等）只回显「原始值 N」或「类型 N」，不臆造中文含义。
- `precondition_1` / `condition_1` 是引擎表达式，原样展示，不翻译。
- 时间：库里存 unix 秒或带偏移的 ISO，展示一律走 `app/kernel/times.py`（`system_zone` / `zone_label` / `local_iso`）；前端必须 `new Date()` 解析，**禁止对 ISO 字符串 `slice`**。
- 已知哨兵值：`gacha_data` 的 `end_date = 2050-01-01` 表示「不结束」，`start_date` 落在 2049/2050 表示未定档（阈值见 `modules/gacha/importer.py` 的 `SENTINEL_TS`）。
- BWiki 只抓简中服（`简中赛马娘卡池` / `简中支援卡卡池`）；繁中 / 日服来源已删除，不要再加回。
- 每处新增口径都要在对应 `docs/<module>.md` 写清「怎么算的 + 为什么 + 已知限制」，并在 `docs/kb/README.md` 缺口表登记。

## 文件写入陷阱

PowerShell 5.1 的 `Set-Content -Encoding utf8` / `Out-File` 会写 **BOM**，导致 `tomllib` 报
`Invalid statement (at line 1, column 1)`（模块直接消失）或浏览器 `import()` 拒绝加载面板。改文件一律用 `edit` 工具；需用脚本时：

```powershell
$p = "d:\Workspace\Projects\uma-assistant\<file>"
$t = [System.IO.File]::ReadAllText($p)
[System.IO.File]::WriteAllText($p, $t, (New-Object System.Text.UTF8Encoding($false)))
```

自检：`([System.IO.File]::ReadAllBytes($p))[0] -ne 0xEF`。

## 前端约定

- 面板导出 `export default { async mount(root, ctx) }`，用 `ctx.api(path, options)` / `ctx.escapeHtml` / `ctx.toast` / `ctx.createLimiter`；`api()` 返回已解析的 JSON，非 2xx 抛错。
- 查询参数用 `URLSearchParams`；渲染前一律 `escapeHtml`（含标题、描述、卡片名）。
- **不要出现超长单列列表**：条目类列表用 `class="list grid"`，首屏只渲染 6–9 条，底部用 `ctx.createLimiter(n).slice(key, items)` + `.more(key, items.length)` 折叠，`[data-more]` 用根节点代理一次点击后重渲染该区块。
- 长描述加 `class="meta clamp"`（两行截断）；次要明细（来源清单等）放 `<details>` 默认收起。
- 改完面板要**重启服务 + 浏览器实测**：CDP `Network.setCacheDisabled` + `Network.clearBrowserCache`，URL 加 `?v=<时间戳>` 绕过模块缓存；并确认 `.main-scroll` 没有横向滚动条（`scrollWidth === clientWidth`）。
- 不引入打包工具与前端框架；样式复用 `static/styles.css` 里已有的 card / toolbar / list-row / pill / tag / seg-table / more-btn。

## 测试约定

- 每个模块一个 `tests/test_<id>.py`，用 `build_mdb(path)` 造最小 `master.mdb`（只建需要的表与列），断言「导入条数 / 分组状态 / 口径换算 / 缺表降级 / API 404」。
- 外部抓取一律注入 `FakeFetcher`，测试不得联网。
- 时间相关断言写相对时间（`datetime.now(UTC) ± timedelta`），不要写死会过期的日期。

## 运行时数据

- `data/` 是运行时生成（`app.db` / `mdb` / `logs` / `snapshots` / `.tmp`），不提交；临时脚本放 `data/.tmp/`，任务结束清空。
- 快照、日志、中转文件都留在工作区内，不写系统盘临时目录。
- 不修改、不删除 `master.mdb`，一律 `mode=ro` 打开。
