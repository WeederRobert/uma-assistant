---
name: uma-verify
description: '当在 uma-assistant 里完成或检查任何改动时使用：跑 ruff / pytest、重启 8742 端口服务、浏览器验收面板、确认模块列表与接口返回、同步 README / docs / ROADMAP。汇报「完成」之前也要先走一遍。'
argument-hint: "要验证的改动，例如：日程页合并后的面板验收"
---

# 交付前验证（uma-assistant）

## 1. 代码层

```powershell
uv run ruff format .          # 先格式化，再查
uv run ruff check .
uv run ruff format --check .
uv run pytest                 # 全量；只改一个模块时先 -q tests/test_<id>.py
```

三条全绿才算过。纯 Markdown 改动跳过。

## 2. 服务层（面板改动必做）

```powershell
# 关掉旧进程（不要用 Stop-Process -Name）
$conn = Get-NetTCPConnection -LocalPort 8742 -State Listen -ErrorAction SilentlyContinue
if ($conn) { $conn | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; Start-Sleep -Seconds 2 }

# 后台起服务，日志落 data/logs/serve.out
Start-Process cmd.exe -ArgumentList "/c","cd /d d:\Workspace\Projects\uma-assistant && uv run python -m app.desktop --no-window --port 8742 > data\logs\serve.out 2>&1" -WindowStyle Hidden
Start-Sleep -Seconds 14
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8742/api/system/info).StatusCode   # 期望 200
```

接口自查（比看面板快）：

```powershell
$info = Invoke-RestMethod http://127.0.0.1:8742/api/system/info
$info.modules | ForEach-Object { "$($_.id): $($_.status) ui=$([bool]$_.ui.panel)" }
```

## 3. 浏览器层

- 面板有模块缓存，必须：CDP `Network.setCacheDisabled` → `Network.clearBrowserCache` → `goto` 带 `?v=<Date.now()>`。
- 用 `page.evaluate` 直接点 `click()`（窄元素 `click` 会超时），等待用 `waitForTimeout` + 检查 DOM 文本。
- 验收项写清「看哪个元素、期望什么文字」，例如：侧栏 7 项无「卡池」、大赛默认 2 届、取消勾选变 29 届。
- `readPage` 输出很大，改用 `page.evaluate(() => el.innerText.slice(...))` 取片段。

## 4. 一致性检查

| 检查 | 命令 / 位置 |
| --- | --- |
| 模块与侧栏一致 | `/api/system/info` 的 `modules[].ui.panel` |
| 文档与实现一致 | `README.md` 模块一览 / 状态行、`docs/<module>.md`、`docs/architecture.md`、`ROADMAP.md`、`docs/kb/README.md` |
| 表前缀 | `sqlite_master` 里 `<id>_*` |
| 无临时文件 | `data/.tmp` 清空 |
| 无 BOM | `([System.IO.File]::ReadAllBytes($p))[0] -ne 0xEF` |

## 5. 汇报格式

给用户的总结固定包含：**改了什么（文件级）→ 实测数据（条数 / 状态 / 浏览器看到什么）→ 验证结果（测试数、lint）→ 仍存在的限制**。不写「应该可以」。

## 常见失败与处置

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| 模块从侧栏消失、`tomllib` 报第 1 行 | manifest 被写成 UTF-8 BOM | 用 `UTF8Encoding($false)` 重写 |
| 面板 `import()` 失败 | panel.js 带 BOM 或语法错 | 同上；或 `node --check` 不适用时用浏览器 console |
| 改了面板但页面没变 | 模块缓存 | 清缓存 + `?v=` |
| `pytest` 里时间相关断言失败 | 写死了会过期的日期 | 改成 `datetime.now(UTC) ± timedelta` |
| 接口 422 | importer 抛 `*SourceError` | 看 `data/logs/serve.out` 里的中文原因 |
