"""数据同步的业务逻辑：取文件 → 幂等判断 → 导入 → 留档。

三条入口共用同一条主链路 :meth:`SyncService.apply_path`：

- 本机客户端（:meth:`apply_device`）：adb 提取到 ``data/mdb/master.mdb`` 后导入
- 面板上传（:meth:`apply_upload`）：浏览器选文件
- 命令行 / 手动路径（:meth:`apply_path`）：直接给路径

导入本身由 ``modules.catalog.importer`` 负责（本模块 ``requires = ["catalog"]``），
这里不重复任何字段映射逻辑。
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.kernel.config import Settings
from app.kernel.database import Database
from app.kernel.dataversion import META_SOURCE, read_data_version
from modules.catalog.importer import MasterMdbError, import_master_mdb

from . import android, snapshots
from .settings import SyncSettings

_SHA_IN_SOURCE = re.compile(r"sha256:([0-9a-f]+)")


class SyncService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        options: SyncSettings | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.options = options or SyncSettings()
        self.logger = logger or logging.getLogger(__name__)

    # ------------------------------------------------------------------ paths
    @property
    def snapshot_dir(self) -> Path:
        return self.settings.snapshots_dir

    @property
    def mdb_dir(self) -> Path:
        return self.settings.mdb_dir

    @property
    def kept_mdb(self) -> Path:
        return self.settings.mdb_path

    @property
    def staging_root(self) -> Path:
        """中转文件根目录，位于工作区内（见 ``Settings.tmp_dir``）。"""
        path = self.settings.tmp_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ----------------------------------------------------------------- status
    def current_sha(self) -> str | None:
        """本地库当前数据的指纹（``data.source`` 里记录的 sha256 前缀）。"""
        match = _SHA_IN_SOURCE.search(self.db.get_meta(META_SOURCE) or "")
        return match.group(1) if match else None

    def is_current(self, sha256: str) -> bool:
        current = self.current_sha()
        return bool(current) and sha256.startswith(current)

    def status(self) -> dict[str, Any]:
        latest = snapshots.latest_snapshot(self.snapshot_dir)
        kept = None
        if self.kept_mdb.is_file():
            stat = self.kept_mdb.stat()
            kept = {
                "path": str(self.kept_mdb),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
            }
        return {
            "data_version": read_data_version(self.db).as_dict(),
            "sha256": self.current_sha(),
            "latest_snapshot": latest,
            "snapshot_count": snapshots.count_snapshots(self.snapshot_dir),
            "snapshot_dir": str(self.snapshot_dir),
            "kept_mdb": kept,
            "package": self.options.package,
        }

    def history(self, limit: int | None = None) -> list[dict[str, Any]]:
        return snapshots.list_snapshots(self.snapshot_dir, limit or self.options.history_limit)

    def _backfill_snapshot(
        self, *, via: str, sha256: str | None, extra: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """当前版本还没有快照时补一条，避免历史里凭空少掉已有数据。"""
        version = read_data_version(self.db)
        if not version.known:
            return None
        if snapshots.snapshot_path(self.snapshot_dir, version.version).is_file():
            return None
        record = {
            "version": version.version,
            "game_version": version.game_version,
            "sha256": sha256,
            "source": version.source,
            "via": via,
            "datasets": version.datasets,
            "imported_at": version.updated_at or datetime.now(UTC).isoformat(timespec="seconds"),
            "backfilled": True,
            "note": "根据本地库 meta 补记录：该版本导入时同步模块尚未接入",
            **(extra or {}),
        }
        return snapshots.write_snapshot(self.snapshot_dir, record)

    # ----------------------------------------------------------------- device
    def device_ready(self) -> dict[str, Any]:
        """只探测 adb 是否存在，不连接设备（供面板初始化时快速反馈）。"""
        try:
            adb = android.find_adb(self.options.adb_path)
        except android.FetchError as exc:
            return {"available": False, "adb": None, "message": str(exc)}
        return {"available": True, "adb": adb, "message": "", "package": self.options.package}

    def check_device(self, serial: str | None = None, package: str | None = None) -> dict[str, Any]:
        """提取一次并体检，但不写库。用于「有没有新版本」的判断。"""
        with tempfile.TemporaryDirectory(
            prefix="uma-sync-check-", dir=self.staging_root
        ) as staging:
            extraction = android.extract(
                adb_path=self.options.adb_path,
                serial=serial or self.options.serial,
                package=package or self.options.package,
                staging_dir=Path(staging),
            )
            return {
                **extraction.as_dict(),
                "is_current": self.is_current(extraction.sha256),
                "current_sha256": self.current_sha(),
                "suggested_version": suggested_version(extraction),
            }

    def apply_device(
        self,
        serial: str | None = None,
        package: str | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        self.mdb_dir.mkdir(parents=True, exist_ok=True)
        staging = self.mdb_dir / ".staging"
        try:
            extraction = android.extract(
                adb_path=self.options.adb_path,
                serial=serial or self.options.serial,
                package=package or self.options.package,
                staging_dir=staging,
            )
            source_path = extraction.path
            if self.options.keep_mdb:
                kept = self.mdb_dir / "master.mdb"
                shutil.move(str(extraction.path), kept)
                source_path = kept

            result = self.apply_path(
                source_path,
                game_version=extraction.game_version,
                data_version=suggested_version(extraction),
                via="android",
                force=force,
                extra={
                    "device": {
                        "serial": extraction.serial,
                        "package": extraction.package,
                        "remote_path": extraction.remote.path,
                        "remote_size": extraction.remote.size,
                        "remote_mtime": extraction.remote.mtime.isoformat(timespec="minutes"),
                    }
                },
            )
            result["extraction"] = extraction.as_dict()
            return result
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # ------------------------------------------------------------------- import
    def apply_path(
        self,
        path: Path | str,
        *,
        game_version: str | None = None,
        data_version: str | None = None,
        via: str = "path",
        force: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = Path(path).expanduser()
        sha256: str | None = None
        if source.is_file():
            sha256 = android.sha256_file(source)
            if not force and self.is_current(sha256):
                return {
                    "imported": False,
                    "reason": "本地库已是这一版数据，未重复导入",
                    "sha256": sha256,
                    "data_version": read_data_version(self.db).as_dict(),
                    # 首次通过同步模块运行时会顺手补一条历史记录
                    "snapshot": self._backfill_snapshot(via=via, sha256=sha256, extra=extra),
                }

        # 文件不存在 / 不是 SQLite / 缺 text_data 都由导入器给出中文原因
        summary = import_master_mdb(
            self.db,
            source.resolve(),
            game_version=game_version,
            data_version=data_version or _default_version(),
        )
        record = {
            "version": summary.data_version,
            "game_version": summary.game_version,
            "sha256": sha256,
            "source": summary.source,
            "via": via,
            "datasets": {
                "characters": summary.characters,
                "support_cards": summary.support_cards,
                "skills": summary.skills,
                "text_entries": summary.text_entries,
            },
            "missing_columns": summary.missing_columns,
            "skipped": summary.skipped,
            **(extra or {}),
        }
        snapshot = snapshots.write_snapshot(self.snapshot_dir, record)
        return {
            "imported": True,
            "sha256": sha256,
            "summary": summary.as_dict(),
            "snapshot": snapshot,
            "data_version": read_data_version(self.db).as_dict(),
        }

    def apply_upload(
        self,
        data: bytes,
        filename: str,
        *,
        game_version: str | None = None,
        data_version: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        if not data:
            raise MasterMdbError(f"{filename} 是空文件，请重新选择。")
        with tempfile.TemporaryDirectory(
            prefix="uma-sync-upload-", dir=self.staging_root
        ) as staging:
            target = Path(staging) / (Path(filename).name or "master.mdb")
            target.write_bytes(data)
            return self.apply_path(
                target,
                game_version=game_version,
                data_version=data_version,
                via="upload",
                force=force,
                extra={"filename": filename},
            )


def _default_version() -> str:
    return f"mdb-cn-{datetime.now(UTC):%Y%m%d}"


def installed_sha(db_path: Path) -> str | None:
    """只读 ``data/app.db`` 的 ``data.source``，供不持有 ``Database`` 的脚本使用。"""
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (META_SOURCE,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    match = _SHA_IN_SOURCE.search((row[0] if row else "") or "")
    return match.group(1) if match else None


def suggested_version(extraction: android.Extraction) -> str:
    """客户端文件的修改时间≈资源版本日期，比"导入日期"更能代表数据快照。"""
    return f"mdb-cn-{extraction.remote.mtime:%Y%m%d}"
