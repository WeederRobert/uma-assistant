---
name: uma-data-probe
description: "当要在动手前核实某条事实到底在哪、能不能用时使用：探查 master.mdb 的表（gacha_data、champions_*、skill_data、race_condition、support_card_*）、检查 BWiki SMW 返回、把 mdb 与 BWiki 交叉验证、判断某个字段能不能信。产出可复现的证据与口径记录，而不是猜。"
argument-hint: "要查证的事实，例如：卡池排期有没有未来数据"
---

# 数据源核实（uma-assistant）

前提：`data/mdb/master.mdb`（简中服最新快照，只读，2026.10.27使用2.0.2版本）与 `data/app.db`（各模块落库结果）。
**没核实过的结构不写进代码**：JOIN 键写错会静默返回 0 行，枚举含义写错会误导整个面板。

## 探查脚本模板

写 `data/.tmp/probe.py`，输出重定向到文件再用 `view` 读（PowerShell 控制台会把中文压成乱码）：

```python
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect(Path("data/mdb/master.mdb").resolve())
conn.row_factory = sqlite3.Row

# 1) 找表：按关键词扫 sqlite_master
for row in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%gacha%'"
):
    print(row["name"])

# 2) 看列
print([r["name"] for r in conn.execute("PRAGMA table_info(gacha_data)")])

# 3) 数连接命中率：先看能连上多少，再决定用哪一列
print(
    conn.execute(
        "SELECT COUNT(*) FROM card_data c JOIN available_skill_set a "
        "ON a.available_skill_set_id = c.available_skill_set_id"
    ).fetchone()[0]
)
```

```powershell
uv run python data\.tmp\probe.py 2>&1 | Out-File data\.tmp\probe.out -Encoding utf8
```

完成后删掉 `data/.tmp/*`。

## 核实清单

| 要确认的事 | 怎么确认 |
| --- | --- |
| 表存在、行数 | `sqlite_master` + `COUNT(*)` |
| 列名与类型 | `PRAGMA table_info(<table>)` |
| 连接键 | 两种写法各跑一次 `COUNT(*)`，命中率 100%（或说明缺口）才算验证过 |
| 时间字段语义 | 抽样打印 + 转本地时间；注意哨兵值（`2050-01-01` = 不结束、`2049+` = 未定档） |
| 枚举取值 | `GROUP BY <col>` 看分布；**同时查有没有字典表**（`%text%` / `%name%` / `text_data` 的 category 全集） |
| 名称文本 | `text_data`：卡池名 26、卡池说明 13、马娘名 6、卡称号 5、协助卡称号 76、赛事名 32、技能名 47 / 描述 48 |
| 与外部源一致 | 取 mdb 里已发生的当期数据，和 BWiki 页面逐字对比（例：`gacha_data.id=30204` ↔ 「3★跳舞城 优俊少女招募」） |

## BWiki 侧

- 端点：`api.php?action=ask&query=[[分类:简中赛马娘卡池]]|?开始描述|...`，返回 `query.results[标题].printouts`。
- 只跟简中服；`繁中*` / `赛马娘卡池` / `活动` 分类已下线，不要再用。
- BWiki 不回 `ETag`，只能靠 TTL 缓存 + 强制刷新；探测时加 `force=true` 或直接 `uv run python` 调 `modules/events/fetcher.py`。

## 结论怎么写

在 `docs/<module>.md` 里固定四段：

1. **数据来源表**：内容 → 表 → 可信级别（A 客户端原始 / B 权威二手 / C 社区整理 / D 占位）。
2. **连接键**：正确写法 + 错误写法会怎样（0 行 / 串味），这是最容易复发的坑。
3. **字段口径**：枚举「有字典 / 无字典」、哨兵值、单位未确认的都写出来。
4. **已知限制**：解析不了的、退化的链路（如 `skill_set_id / 1000` 只落到 2 个组）明确写「不做」。

同时更新 `ROADMAP.md` 的「数据依据」表与记录，以及 `docs/kb/README.md` 缺口表。
