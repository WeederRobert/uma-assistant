# 打包与发布

本文说明为什么用 PyInstaller、体积是怎么来的、默认产物裁掉了什么，以及运行时文件落在哪里。
打包配置在 [`packaging/uma-assistant.spec`](../packaging/uma-assistant.spec)，冒烟脚本是
[`packaging/smoke.ps1`](../packaging/smoke.ps1)。

## 1. 结论

| 产物 | 体积（实测） | 含文档 RAG | 用途 |
| --- | --- | --- | --- |
| **精简版**（默认） | **45.3 MB**（770 个文件，onedir） | 否（调用时提示缺可选组件） | 发布给玩家 |
| 完整版（`UMA_BUNDLE_ML=1`） | **691.1 MB**（6364 个文件，onedir） | 是 | 自己用 / 需要文档问答 |

两个版本都是 `onedir`（exe + `_internal/`），启动到 `/api/health` 就绪实测 **约 2.6 s**
（含 PowerShell 启动与 0.5 s 轮询，冷启动、数据目录为空）。需要单文件时设 `UMA_ONEFILE=1`。

## 2. 体积是怎么来的

依赖体积实测自 `.venv/Lib/site-packages`（`uv sync` 后的开发环境）：**1061 MB / 35263 个文件**。

| 包 | 体积 | 精简版是否携带 | 说明 |
| --- | --- | --- | --- |
| `torch` | 475 MB | ❌ | 仅 `sentence-transformers` 推理需要 |
| `scipy` | 93 MB | ❌ | `torch` / `chromadb` 的传递依赖 |
| `kubernetes` | 70 MB | ❌ | `chromadb` 的传递依赖（本地持久化用不到） |
| `chromadb_rust_bindings` | 61 MB | ❌ | 向量库 |
| `transformers` | 55 MB | ❌ | 仅向量化需要 |
| `onnxruntime` | 42 MB | ❌ | 传递依赖，本项目未直接使用 |
| `sympy` | 37 MB | ❌ | `torch` 的传递依赖 |
| `sklearn` | 28 MB | ❌ | `chromadb` 的传递依赖 |
| `numpy` | 43 MB（23 + 20 libs） | ❌ | 只在向量化时用到，已改为延迟导入 |
| `fastapi` / `starlette` / `uvicorn` / `pydantic` / `httpx` / `openai` / `pypdf` / `pywebview` 等 | ≈ 60 MB | ✅ | 内核、接口、LLM 客户端、窗口 |

也就是说：**体积的 95% 是文档向量化能力**，而它只服务于「上传文档 + 片段检索」一条路径。

## 3. 裁剪口径

1. **依赖延迟导入**：`chromadb`、`sentence-transformers`、`numpy` 都不在模块加载期导入
   （`vector_store._ensure_collection()`、`embeddings._load_model()`、`embeddings._numpy()`）。
   好处是缺组件时 `knowledge` 模块仍能加载，「结构化数据优先」的问答路径不受影响；
   真正用到文档 RAG 时才报一句中文的「缺可选组件」。
2. **spec 里排除**：`torch` `torchvision` `transformers` `sentence_transformers` `chromadb`
   `numpy` `scipy` `sklearn` `sympy` `kubernetes` `grpc` `posthog` `onnxruntime` 等。
3. **不能排除的**：`fastapi` 0.162 的 `telemetry` 子模块会**无条件**导入 `opentelemetry`，
   所以 `opentelemetry` 必须保留（早期把它一起排掉，打包产物启动即 `ModuleNotFoundError`）。

### 评估过但没采用的方案

| 方案 | 预期体积 | 为什么不采用 |
| --- | --- | --- |
| 保持 torch，直接打全量包 | 691 MB（实测完整版） | 传播成本太高，多数用户用不到文档 RAG |
| 把 bge-small-zh 转 ONNX + `onnxruntime` 替代 torch / transformers | ≈ 150 MB（含模型） | 要重写 `embeddings.py`、处理模型分发与缓存，收益不如「可选组件」直接；留作后续 |
| 完全删除知识库模块 | 最小 | 会让「结构化数据优先」的问答一起消失，那是模块里最有价值的部分 |

## 4. 构建

```powershell
uv run pyinstaller packaging/uma-assistant.spec --noconfirm          # 精简版 → dist/uma-assistant/
$env:UMA_BUNDLE_ML='1'; uv run pyinstaller packaging/uma-assistant.spec --noconfirm   # 完整版
$env:UMA_ONEFILE='1';   uv run pyinstaller packaging/uma-assistant.spec --noconfirm   # 单文件
$env:UMA_CONSOLE='1';   uv run pyinstaller packaging/uma-assistant.spec --noconfirm   # 保留控制台，排查启动异常
.\packaging\smoke.ps1 -Exe .\dist\uma-assistant\uma-assistant.exe   # 冒烟：启动 → 健康检查 → 报告 frozen/数据目录
```

冒烟脚本会断言三件事：服务在 60 s 内就绪、`app.frozen` 为 `true`、全新的数据目录处于首次使用状态。
CI 的 `package` 任务（[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)）在 `windows-latest` 上
跑同一套命令，并把 `uma-assistant-windows.zip` 作为 artifact 上传（保留 14 天）。

## 5. 图标

图标只有一份源图 `packaging/Logo.png`，其余都是生成物：

```powershell
.\packaging\make-icon.ps1        # 改了 Logo.png 之后跑一次
```

| 产物 | 用途 | 规格 |
| --- | --- | --- |
| `packaging/icon.ico` | spec 里 `EXE(icon=…)` → exe 图标（资源管理器 / 开始菜单 / 任务栏 / 窗口标题栏都跟着它） | 7 帧：16/24/32/48/64/128 用 32bpp BMP 帧，256 用 PNG 帧 |
| `static/favicon.ico` | 浏览器标签页（`index.html` 的 `rel="icon"`） | 3 帧：16/32/48 BMP |
| `static/icon.png` | 侧栏品牌图（`.brand-mark`，34px 圆角裁切）与 `apple-touch-icon` | 256×256 PNG |

两点说明：

- **生成脚本用 `System.Drawing`（GDI+）**：不引入第三方依赖，`uv sync` 不用为了一图标多装一个包；脚本自己拼 ICO 容器（BMP 帧 + 256 的 PNG 帧），无外部工具链。
- **源码模式跑出来的窗口图标仍然是 Python 的**：pywebview 没有暴露设置窗口图标的接口，任务栏图标取自 exe。要看到成品图标就用打包产物（`dist/uma-assistant/uma-assistant.exe`），或在浏览器里看 `favicon.ico` 与侧栏品牌图。

验证：`.\packaging\smoke.ps1 -Exe .\dist\uma-assistant\uma-assistant.exe` 跑通后，用 `[System.Drawing.Icon]::ExtractAssociatedIcon($exe)` 取出的图标应与 `Logo.png` 一致（实测 32×32 提取正常，exe 里能找到 256 帧的 PNG 签名）。

## 6. 运行时文件落点（P4-2b 定稿）

| 内容 | 源码运行 | 打包运行 | 原因 |
| --- | --- | --- | --- |
| `data_dir`（`app.db` / `mdb` / `logs` / `snapshots` / `.tmp`） | `<项目根>/data/` | `%LOCALAPPDATA%\uma-assistant\data\` | 安装目录可能在 `Program Files`（只读）、且多用户共享，不能存可变数据 |
| `.env` | `<项目根>/.env` | exe 同级目录 | 配置跟着程序走，卸载即清 |
| `modules/`、`static/`（只读） | `<项目根>/` | `sys._MEIPASS` | PyInstaller 的 datas 解包目录 |
| 中转文件 `.tmp` | `<data_dir>/.tmp` | `%LOCALAPPDATA%\uma-assistant\data\.tmp` | 不写系统临时目录，避免被清理；mdb 二十几 MB |

实现只集中在 [`app/kernel/config.py`](../app/kernel/config.py) 的 `is_frozen()` / `resource_root()` /
`app_root()` / `default_data_dir()`，业务代码继续只用 `Settings.data_dir` 与 `Settings.tmp_dir`，
另有 `tests/test_config.py` 覆盖两种形态（含 `%LOCALAPPDATA%` 缺失时回退到 `~/AppData/Local`）。

## 7. 已知限制

- **未签名**：首次运行会被 SmartScreen 拦一下，需要「更多信息 → 仍要运行」。
- **依赖 WebView2 Runtime**：Windows 10/11 一般自带；缺失时窗口起不来（可用 `--no-window` 后用浏览器打开）。
- **完整版没有进 CI**：691 MB 的 artifact 没必要每次 PR 都传，只保证本地可构建（已在本地实测启动通过）。
- **精简版的知识库只有结构化问答**：上传文档 / 文档检索会提示缺可选组件。
- **单文件模式启动更慢**：每次运行都要把整包解到临时目录，默认不用。
