"""桌面入口：本地服务 + 原生窗口。

用法：
    uv run python -m app.desktop            # 启动桌面窗口
    uv run python -m app.desktop --no-window  # 只跑服务（浏览器访问）
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time

import httpx
import uvicorn

from .kernel.config import Settings, get_settings
from .kernel.console import enable_utf8_console
from .kernel.logging import setup_logging
from .main import create_app

logger = logging.getLogger(__name__)


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, preferred: int) -> int:
    for candidate in range(preferred, preferred + 20):
        if _port_available(host, candidate):
            return candidate
    raise RuntimeError(f"{host}:{preferred} 起 20 个端口均被占用，请修改 UMA_PORT")


def _wait_until_ready(url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def run_server(settings: Settings, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    # 端口可能被占用后改过，模块里要用真实端口做本地自调用，所以先写回 settings
    settings.port = port
    application = create_app(settings)
    config = uvicorn.Config(
        application,
        host=settings.host,
        port=port,
        log_level=settings.log_level.lower(),
        access_log=False,
        # 用根日志配置，让 uvicorn 的日志也进同一个文件
        log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return server, thread


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="优俊少女助手（桌面版）")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认取 UMA_PORT")
    parser.add_argument("--no-window", action="store_true", help="只启动本地服务")
    args = parser.parse_args(argv)

    enable_utf8_console()
    settings = get_settings()
    log_path = setup_logging(settings.log_dir, settings.log_level)

    port = args.port or settings.port
    if not _port_available(settings.host, port):
        port = _pick_port(settings.host, port)
        logger.warning("端口被占用，改用 %d", port)

    server, thread = run_server(settings, port)
    base_url = f"http://{settings.host}:{port}"

    if not _wait_until_ready(f"{base_url}/api/health"):
        logger.error("本地服务启动失败，请查看上方日志")
        server.should_exit = True
        return 1

    logger.info("服务已就绪: %s", base_url)
    logger.info("日志文件: %s", log_path)

    if args.no_window:
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
            thread.join(5)
        return 0

    import webview

    window = webview.create_window(
        settings.app_name,
        base_url,
        width=settings.window_width,
        height=settings.window_height,
        min_size=(960, 640),
        text_select=True,
    )
    webview.start()
    del window
    server.should_exit = True
    thread.join(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
