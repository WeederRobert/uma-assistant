from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.kernel.config import Settings
from app.kernel.logging import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    log_file_path,
    setup_logging,
)


def _flush() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_writes_log_file_under_data_dir(settings: Settings) -> None:
    path = setup_logging(settings.log_dir, "INFO")

    logging.getLogger("test.file").info("写入文件日志")
    _flush()

    assert path == settings.log_dir / "app.log"
    assert path.exists()
    assert "写入文件日志" in path.read_text(encoding="utf-8")


def test_rotation_settings(settings: Settings) -> None:
    """按大小轮转并保留 3 份历史，避免长期运行写满磁盘。"""
    setup_logging(settings.log_dir)

    rotating = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]

    assert len(rotating) == 1
    assert rotating[0].maxBytes == LOG_MAX_BYTES == 1024 * 1024
    assert rotating[0].backupCount == LOG_BACKUP_COUNT == 3


def test_repeated_setup_does_not_duplicate_handlers(settings: Settings) -> None:
    setup_logging(settings.log_dir)
    first = len(logging.getLogger().handlers)

    setup_logging(settings.log_dir)

    assert len(logging.getLogger().handlers) == first


def test_log_dir_created_on_demand(tmp_path: Path) -> None:
    path = setup_logging(tmp_path / "nested" / "logs")

    assert path.parent.is_dir()


def test_level_accepts_name_and_unknown_falls_back_to_info(settings: Settings) -> None:
    setup_logging(settings.log_dir, "debug")
    assert logging.getLogger().level == logging.DEBUG

    setup_logging(settings.log_dir, "nonsense-level")
    assert logging.getLogger().level == logging.INFO


def test_log_file_path_is_stable(settings: Settings) -> None:
    assert log_file_path(settings.log_dir).name == "app.log"
