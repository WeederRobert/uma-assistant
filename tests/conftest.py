from __future__ import annotations

import gc
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# app.main 在导入时会用默认 Settings 建一次应用，测试期间把数据目录指到临时目录，
# 避免污染项目里的 data/。中转目录放在工作区内，不写系统盘（见 Settings.tmp_dir）。
_SCRATCH = PROJECT_ROOT / "data" / ".tmp"
_SCRATCH.mkdir(parents=True, exist_ok=True)
for stale in _SCRATCH.glob("tests*"):
    shutil.rmtree(stale, ignore_errors=True)
_SESSION_TMP = _SCRATCH / "tests"
_SESSION_TMP.mkdir(parents=True, exist_ok=True)
os.environ["UMA_DATA_DIR"] = str(_SESSION_TMP / "data")

from app.kernel.config import Settings  # noqa: E402


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    gc.collect()
    for _ in range(3):
        shutil.rmtree(_SESSION_TMP, ignore_errors=True)
        if not _SESSION_TMP.exists():
            break
        time.sleep(0.2)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    config = Settings(data_dir=tmp_path / "data", port=8799)
    config.ensure_dirs()
    return config
