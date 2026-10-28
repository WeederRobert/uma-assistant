"""把 master.mdb 导入本地图鉴库（命令行薄封装，逻辑在 ``modules.sync``）。

示例：
    uv run python scripts/import_master_mdb.py "C:\\path\\master.mdb" --game-version 2.0.2
    uv run python scripts/import_master_mdb.py data\\mdb\\master.mdb --data-version mdb-cn-20261022

只读取源文件，不修改游戏数据；导入后会写一条快照到 data/snapshots/，
/api/system/info 的数据版本随之变化。同一份文件重复导入会按 sha256 跳过。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.kernel.config import get_settings  # noqa: E402
from app.kernel.console import enable_utf8_console  # noqa: E402
from app.kernel.database import Database  # noqa: E402
from app.kernel.logging import setup_logging  # noqa: E402
from modules.catalog.importer import MasterMdbError  # noqa: E402
from modules.sync.service import SyncService  # noqa: E402
from modules.sync.settings import SyncSettings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    enable_utf8_console()
    parser = argparse.ArgumentParser(description="导入 master.mdb 到本地图鉴库")
    parser.add_argument("mdb", type=Path, help="master.mdb 路径")
    parser.add_argument("--game-version", help="国服游戏版本，例如 2.0.2")
    parser.add_argument("--data-version", help="自定义数据版本号，默认 mdb-cn-<今天>")
    parser.add_argument("--force", action="store_true", help="即使已导入过同一文件也重新导入")
    args = parser.parse_args(argv)

    settings = get_settings()
    settings.ensure_dirs()
    log_path = setup_logging(settings.log_dir, settings.log_level)
    db = Database(settings.db_path)
    try:
        result = SyncService(db, settings, SyncSettings()).apply_path(
            args.mdb,
            game_version=args.game_version,
            data_version=args.data_version,
            via="cli",
            force=args.force,
        )
    except MasterMdbError as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        print(f"详细日志：{log_path}", file=sys.stderr)
        return 2
    finally:
        db.close()

    if not result["imported"]:
        print(f"{result['reason']}（sha256:{result['sha256'][:12]}）")
        return 0

    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"已记录快照：{result['snapshot']['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
