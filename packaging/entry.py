"""PyInstaller 入口。

``app/desktop.py`` 用的是包内相对导入，不能直接当 ``__main__`` 脚本；这里做一层转发。
"""

from __future__ import annotations

from app.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
