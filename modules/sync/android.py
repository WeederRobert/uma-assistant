"""从 Android 设备 / 模拟器提取游戏自带的 ``master.mdb``。

游戏每次更新资源时都会重新下发这份数据库，所以「最新」的唯一可靠来源是
**你自己已安装的游戏客户端**：模拟器里就是
``/data/data/<包名>/files/master/master.mdb``。这里通过 adb 把它拷出来，
并做一次结构体检，避免拿到半截文件或错误的文件。

本模块只负责「拿到文件并验证」，导入与版本记录在 :mod:`.service`。
需要设备已开启 USB 调试（模拟器一般默认开启）；读取 ``/data/data`` 需要 root，
模拟器通常自带 root，会走 ``su``。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PACKAGE = "com.bilibili.umamusu"

# 设备上可能放 mdb 的位置，按优先级排列
REMOTE_CANDIDATES = (
    "/data/data/{pkg}/files/master/master.mdb",
    "/data/data/{pkg}/files/master.mdb",
    "/sdcard/Android/data/{pkg}/files/master/master.mdb",
    "/storage/emulated/0/Android/data/{pkg}/files/master/master.mdb",
)

# 模拟器 adb 端口通常从 16384 起（MuMu / 夜神等各有约定），加 5555 兜底
CONNECT_PORTS = (16384, 16416, 16448, 7555, 62001, 5555)

REQUIRED_TABLES = ("text_data", "card_data", "support_card_data", "skill_data")


class FetchError(RuntimeError):
    """提取失败；message 是可直接展示给用户的中文。"""


@dataclass(slots=True)
class RemoteFile:
    path: str
    size: int
    mtime: datetime


@dataclass(slots=True)
class ProbeResult:
    tables: int
    text_entries: int
    characters: int
    support_cards: int
    skills: int

    def as_dict(self) -> dict:
        return {
            "tables": self.tables,
            "text_entries": self.text_entries,
            "characters": self.characters,
            "support_cards": self.support_cards,
            "skills": self.skills,
        }


@dataclass(slots=True)
class Extraction:
    """一次成功提取的结果。"""

    path: Path
    sha256: str
    game_version: str
    serial: str
    package: str
    remote: RemoteFile
    probe: ProbeResult

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "game_version": self.game_version,
            "serial": self.serial,
            "package": self.package,
            "remote_path": self.remote.path,
            "remote_size": self.remote.size,
            "remote_mtime": self.remote.mtime.isoformat(timespec="minutes"),
            **self.probe.as_dict(),
        }


def run(args: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - 参数是列表，不经过 shell
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FetchError(f"找不到 adb：{args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"命令超时（{timeout:.0f}s）：{' '.join(args)}") from exc


def find_adb(explicit: str | None = None) -> str:
    """按 PATH → 模拟器自带 → 注册表登记的安装目录 的顺序找 adb。"""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FetchError(f"指定的 adb 文件不存在：{path}")
        return str(path)

    found = shutil.which("adb")
    if found:
        return found

    # 模拟器一般自带 adb，且不一定在 PATH 里
    for root in emulator_roots():
        for pattern in ("**/adb.exe", "**/adb"):
            for candidate in root.glob(pattern):
                if candidate.is_file():
                    return str(candidate)

    raise FetchError(
        "找不到 adb。请指定 adb 可执行文件路径，"
        "或安装 Android Platform Tools（platform-tools/adb.exe）并加入 PATH。"
    )


def emulator_roots() -> list[Path]:
    """从注册表与常见路径猜出模拟器安装目录。"""
    roots: list[Path] = []
    candidates: list[Path] = []

    if sys.platform == "win32":
        candidates.extend(registry_install_locations())

    candidates.extend(
        [
            Path("C:/Program Files/Netease"),
            Path("C:/Program Files/MuMuVMMVbox"),
            Path("D:/GAME"),
            Path("D:/Program Files/Netease"),
        ]
    )
    for base in candidates:
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and re.search(r"MuMu|Netease|LDPlayer|夜神|BlueStacks", child.name):
                roots.append(child)
    return roots


def registry_install_locations() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []

    locations: list[Path] = []
    keys = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in keys:
            try:
                with winreg.OpenKey(hive, key) as root:
                    count = winreg.QueryInfoKey(root)[0]
                    for index in range(count):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                                name = reg_value(entry, "DisplayName")
                                if not name or not re.search(r"MuMu|Netease|模拟器", name):
                                    continue
                                location = reg_value(entry, "InstallLocation")
                                if location:
                                    locations.append(Path(location))
                        except OSError:
                            continue
            except OSError:
                continue
    return [path for path in locations if path.is_dir()]


def reg_value(key, name: str) -> str | None:
    import winreg

    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value else None


def online_serials(adb: str) -> list[str]:
    result = run([adb, "devices"])
    serials = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def try_connect(adb: str) -> list[str]:
    """设备未就绪时依次尝试常见模拟器端口。"""
    for port in CONNECT_PORTS:
        target = f"127.0.0.1:{port}"
        result = run([adb, "connect", target], timeout=10)
        output = (result.stdout + result.stderr).lower()
        if "connected" in output and "cannot" not in output and "failed" not in output:
            serials = online_serials(adb)
            if serials:
                return serials
    return []


def device_fingerprint(adb: str, serial: str) -> str:
    """同一台设备可能同时以 emulator-5554 和 127.0.0.1:16384 出现，用指纹去重。"""
    output = shell(adb, serial, "getprop ro.build.fingerprint")
    return output.strip() or serial


def resolve_serial(adb: str, explicit: str | None = None) -> str:
    if explicit:
        run([adb, "connect", explicit], timeout=10)
        return explicit

    serials = online_serials(adb)
    if not serials:
        serials = try_connect(adb)
    if not serials:
        raise FetchError(
            "没有在线的 adb 设备。请先启动模拟器或开启手机 USB 调试，"
            "然后用 `adb devices` 确认设备状态是 device。"
        )

    unique: dict[str, str] = {}
    for serial in serials:
        unique.setdefault(device_fingerprint(adb, serial), serial)

    if len(unique) > 1:
        listing = "、".join(unique.values())
        raise FetchError(f"检测到多个设备（{listing}），请指定要使用哪一个。")
    return next(iter(unique.values()))


def shell(adb: str, serial: str, command: str, timeout: float = 60.0) -> str:
    return run([adb, "-s", serial, "shell", command], timeout=timeout).stdout


def has_root(adb: str, serial: str) -> bool:
    return "uid=0" in shell(adb, serial, "su -c id")


def package_version(adb: str, serial: str, package: str) -> str | None:
    output = shell(adb, serial, f"dumpsys package {package} | grep versionName")
    match = re.search(r"versionName=(\S+)", output)
    return match.group(1) if match else None


def ensure_package(adb: str, serial: str, package: str) -> str:
    version = package_version(adb, serial, package)
    if version is None:
        raise FetchError(
            f"设备上找不到 {package}。请确认游戏已安装、并已至少启动过一次"
            "（首次启动后才会把数据解压到 files/master）。"
        )
    return version


def locate_remote(adb: str, serial: str, package: str, use_root: bool) -> RemoteFile:
    for template in REMOTE_CANDIDATES:
        remote = template.format(pkg=package)
        stat_cmd = f'stat -c "%s %Y" "{remote}"'
        output = shell(adb, serial, f"su -c '{stat_cmd}'" if use_root else stat_cmd).strip()
        parts = output.split()
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        size = int(parts[0])
        if size <= 0:
            continue
        return RemoteFile(path=remote, size=size, mtime=datetime.fromtimestamp(int(parts[1])))
    raise FetchError(
        f"在设备上没找到 {package} 的 master.mdb。请先在游戏里完成一次资源下载，再重试。"
    )


def pull(adb: str, serial: str, remote: RemoteFile, target: Path, use_root: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = "/sdcard/Download/.uma-master-fetch.mdb"
    copy_cmd = f'cp "{remote.path}" "{staged}" && chmod 666 "{staged}"'
    result = run(
        [adb, "-s", serial, "shell", f"su -c '{copy_cmd}'" if use_root else copy_cmd],
        timeout=120,
    )
    if result.returncode != 0:
        raise FetchError(f"设备上复制文件失败：{(result.stderr or result.stdout).strip()}")

    try:
        pulled = run([adb, "-s", serial, "pull", staged, str(target)], timeout=600)
        if pulled.returncode != 0:
            raise FetchError(f"adbd 拉取失败：{(pulled.stderr or pulled.stdout).strip()}")
    finally:
        run([adb, "-s", serial, "shell", f'rm -f "{staged}"'], timeout=30)

    actual = target.stat().st_size
    if actual != remote.size:
        raise FetchError(
            f"文件大小不一致：设备 {remote.size} 字节，本地 {actual} 字节，可能传输中断。"
        )


def probe(path: Path) -> ProbeResult:
    """确认拿到的是真正的 master.mdb，而不是半截文件或别的东西。"""
    with path.open("rb") as handle:
        header = handle.read(16)
    if not header.startswith(b"SQLite format 3"):
        raise FetchError(f"{path.name} 不是 SQLite 数据库（文件头 {header!r}）。")

    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        missing = [name for name in REQUIRED_TABLES if name not in tables]
        if missing:
            raise FetchError(f"缺少表：{'、'.join(missing)}，这不是有效的 master.mdb。")

        def count(table: str) -> int:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        return ProbeResult(
            tables=len(tables),
            text_entries=count("text_data"),
            characters=count("card_data"),
            support_cards=count("support_card_data"),
            skills=count("skill_data"),
        )
    finally:
        conn.close()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(
    *,
    staging_dir: Path,
    adb_path: str | None = None,
    serial: str | None = None,
    package: str = DEFAULT_PACKAGE,
) -> Extraction:
    """把设备上的 master.mdb 拉到 ``staging_dir`` 并体检。

    中转目录由调用方指定（一律在工作区内），调用方负责清理。
    """
    adb = find_adb(adb_path)
    target_serial = resolve_serial(adb, serial)
    version = ensure_package(adb, target_serial, package)
    if not has_root(adb, target_serial):
        raise FetchError(
            "设备没有 root 权限，无法读取 /data/data 下的游戏数据。"
            "模拟器请在设置里开启 root；真机需要 root 或改用其他方式提取。"
        )

    remote = locate_remote(adb, target_serial, package, use_root=True)
    staged = staging_dir / "master.mdb"
    pull(adb, target_serial, remote, staged, use_root=True)

    return Extraction(
        path=staged,
        sha256=sha256_file(staged),
        game_version=version,
        serial=target_serial,
        package=package,
        remote=remote,
        probe=probe(staged),
    )
