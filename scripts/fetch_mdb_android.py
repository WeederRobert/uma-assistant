"""从 Android 设备 / 模拟器提取 master.mdb（命令行薄封装，逻辑在 ``modules.sync``）。

游戏每次更新资源都会重写 ``/data/data/<包名>/files/master/master.mdb``，
所以「最新」的唯一可靠来源是你本机已安装的客户端。应用内也有同样能力
（侧栏「数据同步」）。

示例：
    uv run python scripts/fetch_mdb_android.py
    uv run python scripts/fetch_mdb_android.py --check          # 只比对，不写文件
    uv run python scripts/fetch_mdb_android.py --out "D:\\tmp\\master.mdb"
    uv run python scripts/fetch_mdb_android.py --serial 127.0.0.1:16384 --yes

读取 /data/data 需要 root（模拟器一般自带）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.kernel.config import get_settings  # noqa: E402
from app.kernel.console import enable_utf8_console  # noqa: E402
from modules.sync.android import DEFAULT_PACKAGE, FetchError, extract  # noqa: E402
from modules.sync.service import installed_sha, suggested_version  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    enable_utf8_console()
    parser = argparse.ArgumentParser(
        description="通过 adb 从已安装的游戏客户端提取 master.mdb",
    )
    parser.add_argument("--adb", help="adb 可执行文件路径，默认自动探测")
    parser.add_argument("--serial", help="设备序列号，例如 127.0.0.1:16384")
    parser.add_argument(
        "--package", default=DEFAULT_PACKAGE, help=f"游戏包名，默认 {DEFAULT_PACKAGE}"
    )
    parser.add_argument("--out", type=Path, help="输出路径，默认 data/mdb/master.mdb")
    parser.add_argument("--check", action="store_true", help="只检查是否比本地新，不写文件")
    args = parser.parse_args(argv)

    settings = get_settings()
    out = args.out or settings.mdb_path
    staging = settings.tmp_dir / "fetch"
    shutil.rmtree(staging, ignore_errors=True)

    try:
        extraction = extract(
            staging_dir=staging,
            adb_path=args.adb,
            serial=args.serial,
            package=args.package,
        )
    except FetchError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"提取失败：{exc}", file=sys.stderr)
        return 2

    try:
        print(f"设备 {extraction.serial} · 游戏版本 {extraction.game_version}")
        print(f"远端 {extraction.remote.path}")
        print(
            f"     {extraction.remote.size / 1024 / 1024:.1f} MB，"
            f"修改于 {extraction.remote.mtime:%Y-%m-%d %H:%M}"
        )
        print(f"sha256 {extraction.sha256[:12]}")
        print(
            f"体检 表 {extraction.probe.tables} · 马娘卡 {extraction.probe.characters} "
            f"· 协助卡 {extraction.probe.support_cards} · 技能 {extraction.probe.skills} "
            f"· 文本 {extraction.probe.text_entries}"
        )

        local = installed_sha(settings.db_path)
        if local and extraction.sha256.startswith(local):
            print("本地库已是这一版，无需重新导入。")
        elif local:
            print(f"本地库是旧版（sha256:{local}），可以重新导入。")
        else:
            print("本地库还没有数据，导入后即可在图鉴里查询。")

        if args.check:
            print("--check 模式：未写入文件。")
            return 0

        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extraction.path), out)
    finally:
        shutil.rmtree(extraction.path.parent, ignore_errors=True)

    version = suggested_version(extraction)
    print(f"已保存到 {out}")
    print(
        "下一步：\n"
        f'  uv run python scripts/import_master_mdb.py "{out}" '
        f"--game-version {extraction.game_version} --data-version {version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
