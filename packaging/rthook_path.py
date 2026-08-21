"""确保冻结环境下能找到包内资源。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = Path(sys.executable).resolve().parent
    # one-dir 布局下 _internal 在同级
    internal = base / "_internal"
    if internal.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(base / "ms-playwright"))
        # 方便写输出
        os.chdir(base)


_bootstrap()
