# 优俊少女助手（uma-assistant）

《闪耀！优俊少女》（赛马娘简中服）的桌面助手：本地运行的模块化工具集，用一个可增量扩展的内核承载「图鉴查询」「知识库问答」等独立功能模块。

- 形态：Windows 桌面应用（pywebview 窗口 + 本地 FastAPI 内核，仅监听 `127.0.0.1`）
- 原则：事实查库、建议交给 LLM；每条结论都能追溯到一个**数据版本**
- 许可：Apache-2.0

> 当前状态：MVP 骨架可运行。内核、模块系统、数据版本、图鉴模块（master.mdb 导入 + 查询，含马娘技能组与协助卡各突破等级数值）、知识库模块（RAG + DeepSeek）、数据同步（客户端提取 / 上传）、日程（BWiki 简中服卡池 + mdb 卡池预告 + 大赛排期，汇总一页）、赛道（赛事目录 + 赛道几何 + 场地状态出现率）、技能（条件 / 效果参数 / 来源反查 / 进化关系）、相性（两人关系组点数 + 六人继承布局）已打通；内置 [机制知识库素材](docs/kb/README.md) 让助手在没有 `master.mdb` 时也能回答机制问题。

## 快速开始

前置：Python 3.13、[uv](https://docs.astral.sh/uv/)。**只想用应用的话，双击根目录的 `start.cmd` 就够了**，下面的手动步骤是给开发者看的。

### 一键启动（推荐）

双击 `start.cmd`，或在终端运行：

```powershell
.\start.cmd                 # 弹桌面窗口
.\start.cmd -NoWindow       # 只启动本地服务，用浏览器访问
.\start.cmd -Reinstall      # 强制重装依赖后再启动
```

启动器（[scripts/launch.ps1](scripts/launch.ps1)）会自己完成这些事：

1. 定位 `uv`：PATH → `%USERPROFILE%\.local\bin` → WinGet Links → WinGet Packages → 都找不到就用项目内 `.venv`；
2. 首次运行或 `pyproject.toml` / `uv.lock` 变过时执行 `uv sync`，之后直接启动（不联网）；
3. `.env` 不存在时按 `.env.example` 生成一份；
4. 前面任一步失败时打印明确原因并停住窗口，不会一闪而过。

| 退出码 | 含义 |
| --- | --- |
| 0 | 正常退出 |
| 2 | 虚拟环境不可用，且未找到 `uv` |
| 3 | 依赖安装失败 |

### 手动启动

```powershell
cd uma-assistant
uv sync
Copy-Item .env.example .env      # 可选：不填也能启动，只是知识库问答不可用
uv run python -m app.desktop
```

导入游戏数据（可选，但图鉴模块需要）：

```powershell
uv run python scripts/import_master_mdb.py "D:\path\to\master.mdb" --game-version 2.0.2
```

`master.mdb` 是游戏本体的数据表，需从**你自己已安装的客户端**目录中取得（项目不提供、不下载、不再分发该文件）。导入结果会打印条目计数，`skipped` 与 `missing_columns` 为空即表示字段映射完整；每次导入会在 `data/snapshots/<版本>.json` 留一条记录，同一份文件重复导入会按 sha256 直接跳过（要强制重导加 `--force`）。

也可以完全不开终端：启动后在侧栏「数据同步」里点「检查客户端」→「导入这一版」，或直接选一个 `master.mdb` 上传。命令行与界面走的是同一条链路（`modules/sync`），`scripts/*.py` 只是薄封装。

用的是模拟器（MuMu / 夜神等）时不必手工翻目录，直接从设备取当期数据：

```powershell
uv run python scripts/fetch_mdb_android.py --check   # 只比对版本，不写文件
uv run python scripts/fetch_mdb_android.py           # 提取到 data/mdb/master.mdb
```

脚本会自动找 adb（含模拟器自带的）、连接设备、校验包名与 SQLite 结构，并告诉你是「已是最新」还是「本地是旧版」。需要模拟器开启 root；真机无 root 时它会明确报错。取到文件后按上一条命令导入，`--data-version` 可写成 `mdb-cn-<快照日期>`。

路径写错、选到别的文件或文件损坏时，命令只输出一行中文原因并以退出码 `2` 结束，不打印堆栈：

```
导入失败：不是有效的 SQLite 数据库：fake.mdb（file is not a database）。常见原因是文件下载/解压不完整，或者选错了文件——master.mdb 应在游戏目录的 master 子目录下。
详细日志：D:\...\data\logs\app.log
```

导入内置知识库素材（可选，没有 `master.mdb` 时也能回答机制问题）：

```powershell
uv run python scripts/import_kb.py          # 需要应用先跑起来，默认导入 docs/kb/*.md
```

同名素材内容改动后重跑该脚本即可：脚本会先删掉旧版本的文档再导入，避免旧切块继续参与检索（要保留历史版本时加 `--keep-stale`）。

运行参数：

- `--no-window`：只启动本地服务，用浏览器访问（便于调试）
- `--port 8800`：指定端口，默认 8737，被占用时自动顺延
- `-SkipUv`（仅启动器）：不经过 `uv`，直接用 `.venv` 里的解释器启动

开发模式（热重载 + 浏览器）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
```

日志：桌面版没有终端，运行日志统一写 `data/logs/app.log`（含 uvicorn 与模块日志），单文件超过 1 MB 自动轮转，保留 `app.log.1` … `app.log.3`。启动日志末尾会打印日志文件的绝对路径。

检查：

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## 目录结构

```
uma-assistant/
├─ start.cmd             # 一键启动入口（双击即可）
├─ app/                  # 内核：与业务无关的装配层
│  ├─ main.py            # create_app()：内核 + 模块 + 静态前端
│  ├─ desktop.py         # 桌面入口：uvicorn 后台线程 + pywebview 窗口
│  └─ kernel/
│     ├─ config.py       # Settings（UMA_* 环境变量、data/ 目录）
│     ├─ database.py     # 共享 SQLite（WAL、meta 表、事务）
│     ├─ dataversion.py  # 数据版本的读写与展示
│     ├─ logging.py      # 日志：控制台 + data/logs/app.log（1 MB × 3 份轮转）
│     ├─ modules.py      # 模块发现 / 依赖排序 / 加载 / 生命周期
│     └─ api.py          # /api/system/* 系统信息
├─ modules/              # 一个目录 = 一个模块，可独立开发
│  ├─ catalog/           # 图鉴：master.mdb → SQLite，检索 + 技能组 / 协助卡数值详情
│  ├─ knowledge/         # 知识库：文档 RAG + DeepSeek 问答
│  ├─ sync/              # 数据同步：设备提取 / 上传 / 幂等 / 版本快照
│  ├─ events/            # 日程：BWiki SMW 抓简中服卡池（不跟踪繁中 / 日服）+ 汇总卡池与大赛排期
│  ├─ races/             # 赛道：master.mdb 赛事目录 / 大赛排期 / 场地状态 + BWiki 赛道几何
│  ├─ skills/            # 技能：条件表达式 / 效果参数 / 马娘与协助卡反查 / 进化关系
│  ├─ succession/        # 相性：两人关系组点数 + 六人继承布局分数与等级
│  ├─ gacha/             # 卡池：master.mdb 排期（含未来卡池）与精选卡片，面板并入「日程」页
│  └─ _template/         # 新模块脚手架（下划线开头不会被加载）
├─ static/               # 前端壳：侧栏、路由、数据版本徽标
├─ scripts/              # 一键启动、数据导入与开发脚本
│  ├─ launch.ps1         # start.cmd 的实际实现：定位 uv + 装依赖 + 启动
│  ├─ dev.ps1            # 开发服务器（热重载）
│  ├─ import_master_mdb.py  # 导入 mdb（薄封装，逻辑在 modules/sync）
│  ├─ fetch_mdb_android.py  # 从模拟器/真机提取当期 master.mdb（同上）
│  └─ import_kb.py
├─ data/                 # 运行时生成：app.db / mdb / snapshots / .tmp / logs（都在工作区内）
├─ tests/                # pytest
└─ docs/                 # 调研、架构、模块约定、数据同步设计、知识库素材
   └─ kb/                # 机制知识库（带来源与可信级别，可直接导入 knowledge 模块）
```

## 设计要点

| 主题 | 做法 | 文档 |
| --- | --- | --- |
| 开发计划 | 阶段划分、模块路线、技术债与风险；P0–P2 完成，P3 进行中（赛道 / 大赛 / 场地 / 技能已交付） | [ROADMAP.md](ROADMAP.md) |
| Agent 定制 | `.github/instructions/` 存项目约定（模块七件套、数据口径、BOM 陷阱、验证流程），`.github/skills/` 放 4 个按需加载的 skill（需求拆解 / 新模块 / 数据源核实 / 交付验证） | [.github/](.github/) |
| 界面约定 | 内容限宽 1180px、条目列表用 `.list.grid` 两到三列、长列表首屏 6–9 条 + 「展开其余 N 条」、长描述两行截断 | [docs/architecture.md](docs/architecture.md) |
| 模块化 | 目录即模块，`manifest.toml` + `module.py: setup(ctx)`；单模块加载失败被隔离，不影响其它模块 | [docs/modules.md](docs/modules.md) |
| 架构分层 | 内核（装配/存储/版本）与模块（业务）分离，模块之间不互相 import | [docs/architecture.md](docs/architecture.md) |
| 数据版本 | `meta` 表存 `data.version`，侧栏徽标、`/api/system/info`、LLM 提示词都会带上 | [docs/architecture.md](docs/architecture.md) |
| 数据同步 | 客户端提取 / 文件上传 / sha256 幂等 / 每次导入写 `data/snapshots/<版本>.json` | [docs/data-sync.md](docs/data-sync.md) |
| 日程（简中服卡池 / 卡池与大赛排期） | BWiki SMW 只抓简中服两个卡池分类（服务器由分类决定），带来源等级与抓取时间；6h 缓存 + 强制刷新。卡池预告与大赛排期取 `master.mdb`，同页展示 | [docs/events.md](docs/events.md) |
| 赛道 / 大赛 / 场地状态 | 赛事目录、大赛排期与场地状态出现率取 `master.mdb`（A 级），赛道几何取 BWiki 数据页（C 级）；几何统计口径、出现率口径与源数据缺陷单独说明 | [docs/races.md](docs/races.md) |
| 技能 | 条件表达式、效果参数、马娘与协助卡反查、进化关系全部来自 `master.mdb`；没有字典表的枚举只回显原始编号，`skill_set` 这条退化的反查链路明确弃用 | [docs/skills.md](docs/skills.md) |
| 图鉴详情 | `master.mdb` 的 `available_skill_set`（马娘技能组 + 解锁星级）、`support_card_effect_table`（各突破等级数值）、`support_card_unique_effect`（固有加成）、`single_mode_hint_gain`（提示技能）；效果名称取自 BWiki revid 35946 | [docs/catalog.md](docs/catalog.md) |
| 相性 | `master.mdb` 的 `succession_relation` / `_member` / `_rank`（2133 组 / 4527 成员 / 3 档），两人点数与六人布局分数；明确不含适性（主数据无适性表） | [docs/succession.md](docs/succession.md) |
| 卡池预告 | `master.mdb` 的 `gacha_data`／`text_data`／`gacha_available` 给出卡池排期与精选，含尚未开放的未来卡池（当期快照排到 2027-01-18），已用 BWiki 当期卡池交叉验证；面板并入「日程」页 | [docs/gacha.md](docs/gacha.md) |
| 日志 | 控制台 + `data/logs/app.log`，`RotatingFileHandler` 1 MB × 3 份；uvicorn 也走同一份配置 | — |
| 调研结论 | 各服差异、数据源、开源项目与许可、合规红线 | [docs/research.md](docs/research.md) |
| 知识库素材 | 机制类事实的来源登记、可信分级（A/B/C）与缺口清单，可直接导入 | [docs/kb/README.md](docs/kb/README.md) |

## 模块一览

| 模块 | 作用 | API 前缀 |
| --- | --- | --- |
| `catalog` | 马娘 / 协助卡 / 技能的结构化查询，数据来自本地 `master.mdb`；详情带马娘的技能组（含解锁星级）、协助卡的训练效果数值 / 固有加成 / 提示技能 | `/api/catalog` |
| `knowledge` | 导入 Markdown/PDF/TXT 建立向量库；回答时**先用本地模块接口查结构化数据**（赛道 / 大赛 / 技能 / 卡池 / 图鉴），命中就以查库结果为准并标注来源接口与数据版本，没命中才回落文档检索 | `/api/knowledge` |
| `sync` | 从本机客户端或选定的 `master.mdb` 更新数据，按 sha256 幂等，并记录版本快照 | `/api/sync` |
| `events` | 从 BWiki 抓取**简中服**卡池（只看简中，不跟踪繁中 / 日服），看日程与变更审计；**日程总页**同时展示 `gacha` 的卡池排期与 `races` 的大赛排期 | `/api/events` |
| `races` | 赛事目录 / 大赛排期 / 场地状态（`master.mdb`）+ 赛道几何（BWiki），查分段、坡道、终直、轮次窗口与晋级门槛 | `/api/races` |
| `skills` | 技能条件、效果参数、来源反查（哪些马娘 / 哪些协助卡给）与进化关系，全部来自 `master.mdb`，解析不了的字段回显原始值 | `/api/skills` |
| `succession` | 相性：两只马娘的共享关系组与点数、六人继承布局总分与等级，数据来自 `master.mdb` 的 `succession_relation*`；不含适性 | `/api/succession` |
| `gacha` | 卡池排期与精选卡片，来自 `master.mdb`（含未来卡池，可提前预告下一期）；**面板并入「日程」页**，本模块只提供接口 | `/api/gacha` |

## 合规边界

本项目**不做**以下事情，也不接受相关贡献：

- 不注入、Hook 或修改游戏客户端；不读进程内存、不抓包、不解密游戏资源
- 不自动化游戏操作（脚本挂机、自动育成）
- 不分发游戏本体资源（`master.mdb`、卡面、语音等）；只读取用户本机已有的文件，且不随代码仓库分发

数据来源限于：用户本机的 `master.mdb`、公开 Wiki（如 BWiki）与官方公告。

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)：代码规范、提交规范（Conventional Commits）、分支与 PR 检查清单、新增模块清单。

## 许可

[Apache-2.0](LICENSE)。第三方项目与数据的许可说明见 [docs/research.md](docs/research.md)。
