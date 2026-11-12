# -*- coding: utf-8 -*-
"""程序入口：python -m mini60pilot"""
from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
from pathlib import Path


def _enable_dpi_awareness() -> None:
    try:
        if sys.platform == "win32":
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass


def _hide_console() -> None:
    """打包成 console exe 时隐藏控制台窗口（保留 stdout，便于自检）。

    从 cmd/PowerShell 启动时控制台里还有父进程，不能把父窗口一起隐藏。
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    if os.environ.get("MINI60_CONSOLE"):
        return
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        processes = (ctypes.c_ulong * 4)()
        count = kernel32.GetConsoleProcessList(processes, 4)
        if count <= 1:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # type: ignore[attr-defined]
    except Exception:
        pass


def _selftest() -> int:
    """无界面自检：给打包后的 exe 验证依赖是否完整。"""
    from . import codec
    from .config import AppConfig, Profile, Settings, new_id
    from .device.simulator import SimulatorDevice
    from .engine import Engine

    sim = SimulatorDevice()
    config = AppConfig(settings=Settings(simulator=True, pages=[1, 2, 3], page2_length=512))
    default = Profile(id=new_id(), name="默认", pages=sim.snapshot(), source="capture")
    config.add_profile(default)
    engine = Engine(config)
    engine._device = sim
    engine._device_info = sim.info
    pages = engine._capture_locked()
    assert set(pages) == {1, 2, 3}, pages.keys()
    catalog = codec.Catalog(pages[2])
    ok, message = catalog.validate()
    assert ok, message
    print("SELFTEST OK", message)
    return 0


_MUTEX_HANDLE = None


def _single_instance() -> bool:
    """同名互斥量；已有实例时让托盘窗口弹出来，然后本进程退出。"""
    global _MUTEX_HANDLE
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    ctypes.set_last_error(0)
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\Mini60Pilot")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return True
    try:
        from .tray import TRAY_CLASS, WM_SHOW

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
        hwnd = user32.FindWindowW(TRAY_CLASS, None)
        if hwnd:
            user32.PostMessageW(hwnd, WM_SHOW, 0, 0)
    except Exception:
        pass
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="狼蛛 MINI60 HE 场景切换器")
    parser.add_argument("--config", help="指定 config.json 路径")
    parser.add_argument("--simulator", action="store_true", help="强制使用模拟器")
    parser.add_argument("--silent", action="store_true", help="启动后直接进托盘，不显示主窗口")
    parser.add_argument("--selftest", action="store_true", help="无界面自检后退出")
    parser.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version="%(prog)s 1.0.0")
    args = parser.parse_args(argv)

    _enable_dpi_awareness()
    _hide_console()

    from .config import DEFAULT_DATA_DIR

    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(DEFAULT_DATA_DIR / "app.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )

    if args.selftest:
        return _selftest()
    if not _single_instance():
        logging.info("已有实例在运行，已唤起托盘窗口")
        return 0

    try:
        from .ui import Mini60App
    except Exception as exc:  # noqa: BLE001
        print(f"界面初始化失败：{exc}", file=sys.stderr)
        return 1

    import tkinter as tk

    try:
        app = Mini60App(
            Path(args.config) if args.config else None,
            simulator=args.simulator,
            silent=args.silent,
        )
    except tk.TclError as exc:
        print(f"无法启动图形界面：{exc}", file=sys.stderr)
        return 1
    logging.info("应用启动")
    if args.smoke:
        app.after(2500, app._quit)
    app.mainloop()
    logging.info("应用退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
