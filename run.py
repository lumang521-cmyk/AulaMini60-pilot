# -*- coding: utf-8 -*-
"""带控制台启动，便于看报错。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mini60pilot.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
