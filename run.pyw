# -*- coding: utf-8 -*-
"""双击启动（无控制台窗口）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mini60pilot.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
