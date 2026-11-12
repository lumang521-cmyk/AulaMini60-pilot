# -*- coding: utf-8 -*-
"""开机自启（HKCU Run，无需管理员）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Mini60Pilot"


def _command_line() -> str:
    """开机启动命令：带 --silent，登录后不弹主窗口，直接进托盘。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --silent'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    executable = pythonw if pythonw.exists() else python
    script = Path(__file__).resolve().parent.parent / "run.pyw"
    return f'"{executable}" "{script}" --silent'


def refresh_if_enabled() -> None:
    """旧版自启命令没有 --silent，启动时静默升级一次。"""
    try:
        if is_enabled():
            set_enabled(True)
    except OSError:
        pass


def is_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    if os.name != "nt":
        raise OSError("仅支持 Windows")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command_line())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
