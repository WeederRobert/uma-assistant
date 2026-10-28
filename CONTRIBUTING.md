# 贡献指南

面向两类人：想加功能的自己，以及外部开发者。约定尽量少，但都要执行。

## 1. 开发环境

```powershell
uv sync
Copy-Item .env.example .env
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m app.desktop --no-window   # 只跑服务，浏览器访问 http://127.0.0.1:8737
uv run pyinstaller packaging/uma-assistant.spec --noconfirm   # 打包（精简版），产物在 dist/
.\packaging\smoke.ps1 -Exe .\dist\uma-assistant\uma-assistant.exe   # 打包产物冒烟
```

- 依赖只用 `uv add` / `uv add --dev` 增删，不要手改 `uv.lock`。
- Python 下限见 `pyproject.toml`；不要使用低于该版本的语法。
- 本地数据放在 `data/`，不入库（`.gitignore` 已忽略 `data/`、`*.mdb`、`.env`）。

## 2. 代码规范

### Python

- 格式化与静态检查统一 `ruff`（`line-length = 100`，规则集 `E/F/I/UP/B/SIM`）。提交前必须 `uv run ruff check .` 与 `uv run ruff format --check .` 通过。
- 每个模块文件首行加 `from __future__ import annotations`；泛型用内置写法（`list[str]`、`dict[str, int]`）。
- 公共函数、服务类方法写类型注解；一次性的内部小函数可以不写。
- 路径一律 `pathlib.Path`，不拼字符串路径。
- 配置只从 `Settings` / 模块自己的 settings 读，不在业务代码里散落 `os.getenv`。
- 异常名以 `Error` 结尾；能恢复的降级要记 `logger.warning`，不要静默 `pass`。
- 注释与文档字符串用中文，只解释"为什么"，不复述代码。
- 不引入新依赖来表达已有能力；新增依赖需在 PR 说明理由与体积影响。

### 模块边界（硬约束）

- 模块之间**不得**互相 import。需要共享的能力，先提到 `app/kernel/` 或新建独立模块，用 `requires` 声明依赖。
- 模块只能通过 `ModuleContext`（`settings` / `db` / `logger` / `data_dir` / `data_version()`）访问内核能力。
- 任何模块都不许写入别的模块的 `data_dir` 或表；表名自带模块前缀（如 `catalog_`）。
- 结构化数据的**列名、接口路径、manifest 字段**都是对外契约，改动需在 PR 描述里写明迁移方式。

### 前端

- 原生 ES Module，无构建步骤；`static/app.js` 是外壳，模块面板放各自的 `ui/panel.js`。
- 面板契约：`export default { async mount(root, ctx) }`，`ctx` 提供 `api` / `escapeHtml` / `toast` / `state`；返回的函数会在离开页面时调用，用来清理定时器与监听。
- 所有插入 DOM 的外部文本必须经过 `escapeHtml`；禁止直接拼未转义的接口数据。
- 样式优先复用 `static/styles.css` 的 CSS 变量与类，不写内联颜色。

### 测试

- `tests/` 下按模块分文件；涉及数据库的用例用 `tmp_path`，不要污染真实 `data/`。
- 新增模块至少覆盖：正常路径、空数据/未导入、输入非法。
- 修 bug 先补一个能复现的用例，再改代码。

## 3. 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/)，中文描述可以，但 type/scope 用英文。

```
<type>(<scope>): <一句话说明>

<可选：为什么这么改、取舍、影响面>
```

- `type`：`feat` `fix` `refactor` `perf` `test` `docs` `build` `chore` `style`
- `scope`：模块 id 或目录名，如 `catalog`、`knowledge`、`kernel`、`ui`、`docs`；跨模块改动可省略
- 一次提交只做一件事；格式化、重命名不要混进功能提交
- 不要在提交信息里写"update""fix bug"这类无信息量描述
- 破坏性改动：type 后加 `!`，正文写 `BREAKING CHANGE: ...` 与迁移步骤

示例：

```
feat(catalog): 支持按技能 tag 过滤
fix(kernel): 模块依赖缺失时降级为 error 而不是中断启动
docs(modules): 补充面板 ctx 的字段说明
```

分支：`feat/<scope>-<简述>`、`fix/<scope>-<简述>`；默认分支 `main` 保持可运行（`ruff` + `pytest` 必须绿）。

PR 检查清单：

- [ ] `uv run ruff check .`、`uv run ruff format --check .` 与 `uv run pytest` 通过
- [ ] 新增/改动接口、manifest 字段、数据结构，同步更新 `README.md` 或 `docs/`
- [ ] 没有提交 `data/`、`.env`、`*.mdb` 或游戏资源
- [ ] 改动涉及数据展示时，界面仍能显示数据版本
- [ ] 改动涉及打包（`packaging/`、依赖增减）时，`packaging/smoke.ps1` 跑通
- [ ] 遵守第 5 节的合规边界

## 4. 新增模块清单

1. 复制 `modules/_template/` 为 `modules/<id>/`，目录名必须等于 `manifest.toml` 里的 `[module].id`（合法 Python 标识符，且不以 `_` 开头）。
2. 在 `manifest.toml` 填 `name` / `version` / `description` / `order`；需要别的模块先就绪时写 `requires`（不要复制别人的代码）。
3. 在 `module.py` 实现 `setup(ctx) -> Module`；把业务逻辑放 `service.py`，路由放 `api.py`，表结构放 `schema.py`。
4. 需要前端时写 `ui/panel.js` 并在 manifest 的 `[ui]` 声明；数据表名加 `<id>_` 前缀。
5. 需要持久化文件时用 `ctx.data_dir`；需要展示数据来源时用 `ctx.data_version()`。
6. 补 `tests/test_<id>.py`，然后跑 `uv run ruff check .`、`uv run ruff format --check .` 与 `uv run pytest`。
7. 在 `README.md` 的模块一览表加一行，并在 `docs/modules.md` 需要时补充说明。

详细契约见 [docs/modules.md](docs/modules.md)。

## 5. 合规边界

不接受以下方向的贡献：

- 注入 / Hook / 修改游戏客户端，读进程内存、抓包、解密或分发游戏资源
- 自动化游戏操作（挂机、自动育成、模拟点击）
- 把 `master.mdb`、卡面、语音等游戏资源提交到仓库

允许：解析用户本机已有的数据文件、抓取公开 Wiki 与官方公告（限速、缓存、说明来源）。

## 6. 数据版本

任何改变结构化数据内容的操作都必须写入数据版本（`app/kernel/dataversion.py`），UI 与 LLM 提示词都会引用它。数据来源、抓取时间、游戏版本三者缺一不可。

## 7. 许可

提交即表示同意以 [Apache-2.0](LICENSE) 授权你的贡献。引用第三方代码前先确认许可兼容（GPL 项目只可参考思路，不可复制代码，详见 `docs/research.md`）。
