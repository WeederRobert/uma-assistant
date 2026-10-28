# 模块约定

一个目录 = 一个模块。内核负责发现、排序、加载与生命周期，模块只关心自己的业务。

## 最小结构

```
modules/<id>/
├─ manifest.toml      # 必需：元信息与挂载点
├─ module.py          # 必需：setup(ctx) -> Module
├─ api.py             # 建议：路由
├─ service.py         # 建议：业务逻辑（可单测，不依赖 FastAPI）
├─ schema.py          # 建议：数据表 DDL
└─ ui/
   └─ panel.js        # 可选：前端面板
```

复制 `modules/_template/` 起手。以 `_` 或 `.` 开头的目录不会被加载，所以 `_template` 常驻也不会污染运行时。

## manifest.toml

```toml
[module]
id = "catalog"            # 必填，必须等于目录名，且是合法 Python 标识符
name = "图鉴"             # 必填，界面显示用
version = "0.1.0"         # 必填，模块自身版本
description = "..."       # 可选
order = 10                # 可选，默认 100，小的先加载
requires = ["knowledge"]  # 可选，依赖的模块 id，用于拓扑排序

[api]
prefix = "/api/catalog"   # 可选，默认 /api/<id>

[ui]
label = "图鉴"            # 侧栏文字，默认用 name
panel = "panel.js"        # 相对于 ui/ 的入口文件
```

校验规则：`id` 非法或与目录名不一致、缺 `name`/`version` → 该模块被跳过并记 error 日志。`requires` 指向不存在的模块或形成环 → 启动直接失败（这是配置错误，不是运行时故障，早失败更好）。

## module.py

```python
def setup(ctx: ModuleContext) -> Module:
    service = CatalogService(ctx.db)
    return Module(
        router=build_router(service),
        on_startup=None,  # 可选：加载模型、预热缓存
        on_shutdown=None,  # 可选：释放资源
        status=service.stats,  # 可选：返回 dict，展示在 /api/system/modules
    )
```

- `setup` 抛异常 → 该模块标记 `status: "error"`，应用继续启动。
- 路由自己声明 `tags`，不必再写 prefix（内核按 manifest 挂载）。
- `status` 会在每次 `/api/system/modules` 时调用，必须轻量（只查计数，别全表扫描）。

## ModuleContext

| 成员 | 说明 |
| --- | --- |
| `ctx.settings` | 内核 `Settings`；模块自己的配置应另建 `BaseSettings` 子类 |
| `ctx.db` | 共享 `Database`；`query` / `query_one` / `execute` / `executemany` / `transaction()` |
| `ctx.module_id` | 模块 id |
| `ctx.logger` | 已带模块前缀的 logger |
| `ctx.data_dir` | 模块私有目录 `data/modules/<id>/`，已创建 |
| `ctx.settings.snapshots_dir` | 数据快照留档目录 `data/snapshots/`（每个数据版本一个 JSON） |
| `ctx.settings.tmp_dir` | 中转目录 `data/.tmp/`，大文件临时落点，用完即删；不要用系统临时目录 |
| `ctx.data_version()` | 当前数据版本 `DataVersion` |

禁止：跨模块 import、直接操作别的模块的表、读写别的模块的 `data_dir`。

## 数据表命名

表名以模块 id 加下划线前缀（`catalog_characters`），与内核 `meta` 表、其它模块互不冲突。建表用 `CREATE TABLE IF NOT EXISTS`，迁移用 `PRAGMA user_version` 或自查列。

模块产出的大文件与历史记录放内核提供的固定位置，便于备份与排查：

| 位置 | 用途 |
| --- | --- |
| `data/modules/<id>/` | 模块私有持久化（`ctx.data_dir`） |
| `data/snapshots/<版本>.json` | 数据快照留档（`settings.snapshots_dir`），见 `modules/sync/snapshots.py` |
| `data/.tmp/` | 中转目录（`settings.tmp_dir`），提取/上传的中间文件放这里，随手清理 |
| `data/modules/<id>/cache/` | 外部抓取的原始响应缓存（`modules/events`、`modules/races` 这样用），可随时删除重建 |

## 前端面板

`ui/panel.js` 由外壳动态 `import()`，默认导出：

```js
export default {
  async mount(root, ctx) {
    // root: 主视图容器（已清空）
    // ctx: { api, escapeHtml, toast, state, refreshInfo }
    root.innerHTML = `<h1 class="page-title">标题</h1><section class="card">…</section>`;
    const data = await ctx.api("/api/catalog/stats");
    // …
    return () => {
      /* 离开页面时清理定时器 / 监听 */
    };
  },
};
```

- `ctx.api(path, options)`：自动处理 JSON 与 `FormData`，非 2xx 抛错（带后端 `detail`）。
- `ctx.escapeHtml(text)`：插入任何外部文本前必须调用。
- `ctx.toast(message, type)`：轻提示，`type` 为 `info` / `error`。
- `ctx.state`：跨面板只读的全局状态（`app` / `data` / `modules`）。
- `ctx.refreshInfo()`：重新拉取 `/api/system/info`，刷新侧栏版本徽标与 `ctx.state`。面板改了数据版本（如导入 mdb）后必须调用，否则徽标会停在旧值。
- 面板样式复用 `static/styles.css` 的类与变量；不要引入构建工具或前端框架。
- 不要假设面板只被挂载一次：同一页面可能被反复进入，状态要放在闭包里而非模块顶层。
- `/static/` 与 `/module-assets/` 带 `Cache-Control: no-store`，改完面板刷新页面即可生效，不需要清浏览器缓存。

## 生命周期顺序

按 `(order, id)` 排序后再做拓扑排序，`requires` 优先。`on_startup` 正序执行，`on_shutdown` 逆序执行。加载失败不影响其它模块，但依赖它的模块会拿到一个未启动的环境——所以 `requires` 只用于真正的前置条件。

## 检查清单

新增模块后：

```powershell
uv run ruff check .
uv run pytest
uv run python -m app.desktop --no-window
# 另开终端
curl http://127.0.0.1:8737/api/system/modules
```

确认三件事：模块状态是 `ok`、`api` 前缀可达、`ui.panel` 能在侧栏打开。

测试建议：`service` 用临时 SQLite 单测（见 `tests/test_catalog.py`），路由用 `fastapi.testclient.TestClient(create_app(settings))` 冒烟。测试会话的数据目录固定在 `data/.tmp/tests/`（`tests/conftest.py` 设置 `UMA_DATA_DIR`），不写系统临时目录，也不碰项目真正的 `data/app.db`。
