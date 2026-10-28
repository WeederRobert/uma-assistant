from __future__ import annotations

import contextlib
import sys


def enable_utf8_console() -> None:
    """Windows 控制台默认使用 GBK 代码页，中文日志会变成乱码。

    入口脚本（桌面启动、数据导入）调用一次即可；失败不影响主流程。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")

    if sys.platform != "win32":
        return
    with contextlib.suppress(AttributeError, OSError):
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
