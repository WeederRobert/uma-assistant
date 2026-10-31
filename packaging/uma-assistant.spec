# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    uv run pyinstaller packaging/uma-assistant.spec --noconfirm

默认出**精简版**：剔除 torch / transformers / sentence-transformers / chromadb / numpy，
体积可控，知识库的文档向量化降级为「缺可选组件」提示，其余模块与结构化问答照常。
需要带文档 RAG 的完整版时设 ``UMA_BUNDLE_ML=1``；需要单文件时设 ``UMA_ONEFILE=1``。
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH 由 PyInstaller 注入
BUNDLE_ML = os.environ.get("UMA_BUNDLE_ML") == "1"
ONEFILE = os.environ.get("UMA_ONEFILE") == "1"
# 排查打包问题时设 UMA_CONSOLE=1，保留控制台以便看到启动期异常
CONSOLE = os.environ.get("UMA_CONSOLE") == "1"

# 应用图标由 packaging/make-icon.ps1 从 packaging/Logo.png 生成（exe 图标 + favicon + 侧栏品牌图）
ICON = ROOT / "packaging" / "icon.ico"
icon_path = str(ICON) if ICON.is_file() and sys.platform == "win32" else None

# 模块只在运行期按目录发现，PyInstaller 静态分析看不到，数据和 py 模块都要显式列出来
DATA_SUFFIXES = {".toml", ".js", ".css", ".html"}
module_dirs = sorted(
    path
    for path in (ROOT / "modules").iterdir()
    if path.is_dir() and not path.name.startswith(("_", "."))
)

datas = [(str(ROOT / "static"), "static")]
datas += collect_data_files("webview")
datas += [
    (str(file), str(file.parent.relative_to(ROOT)))
    for module_dir in module_dirs
    for file in sorted(module_dir.rglob("*"))
    if file.is_file() and file.suffix in DATA_SUFFIXES
]

hiddenimports = [f"modules.{path.name}.module" for path in module_dirs]
hiddenimports += [
    # uvicorn 的协议实现与事件循环是运行时挑的
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # pywebview 在 Windows 上走 .NET / WebView2，同样是动态导入
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]

excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "pytest",
    "_pytest",
    "IPython",
]

if not BUNDLE_ML:
    excludes += [
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "sentence_transformers",
        "tokenizers",
        "huggingface_hub",
        "safetensors",
        "chromadb",
        "onnxruntime",
        "numpy",
        "scipy",
        "sklearn",
        "sympy",
        "kubernetes",
        "grpc",
        "posthog",
        "pandas",
        "matplotlib",
    ]

analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries if ONEFILE else [],
    analysis.datas if ONEFILE else [],
    [],
    name="uma-assistant",
    icon=icon_path,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    exclude_binaries=not ONEFILE,
)

if not ONEFILE:
    COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name="uma-assistant",
    )
