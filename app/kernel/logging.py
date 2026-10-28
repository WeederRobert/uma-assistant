"""日志初始化：控制台 + 按大小轮转的文件。

桌面应用没有终端，出问题时只能靠文件日志。日志统一写
``<data_dir>/logs/app.log``，单文件上限 1 MB，保留 3 份历史
（``app.log.1`` … ``app.log.3``），长期运行也不会写满磁盘。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def log_file_path(log_dir: Path) -> Path:
    return log_dir / "app.log"


def _resolve_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelNamesMapping().get(level.strip().upper())
    return resolved if resolved is not None else logging.INFO


def setup_logging(
    log_dir: Path,
    level: str | int = "INFO",
    *,
    console: bool = True,
) -> Path:
    """安装根日志处理器，返回日志文件路径。

    重复调用会先清掉旧的处理器，避免同一行日志写两遍。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_file_path(log_dir)
    level_value = _resolve_level(level)

    formatter = logging.Formatter(LOG_FORMAT)
    handlers: list[logging.Handler] = []

    file_handler = RotatingFileHandler(
        path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    # pythonw / 无控制台时 stdout 可能是 None，此时只写文件
    stream = sys.stderr if sys.stderr is not None else None
    if console and stream is not None:
        console_handler = logging.StreamHandler(stream)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    logging.basicConfig(level=level_value, handlers=handlers, force=True)
    return path
