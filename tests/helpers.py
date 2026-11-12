# -*- coding: utf-8 -*-
"""测试辅助：临时目录放在项目内，避免系统临时目录的权限限制。"""
from __future__ import annotations

import contextlib
import shutil
import uuid
from pathlib import Path

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"


def make_temp_dir() -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TMP_ROOT / uuid.uuid4().hex[:8]
    path.mkdir()
    return path


def remove_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def temp_dir():
    path = make_temp_dir()
    try:
        yield path
    finally:
        remove_temp_dir(path)
