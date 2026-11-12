# -*- coding: utf-8 -*-
"""前台窗口 / 进程监听（User32 + Toolhelp32，无第三方依赖）。"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    MAX_PATH = 260
    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT = 0x0000
    PM_REMOVE = 0x0001
    _WINEVENTPROC = ctypes.WINFUNCTYPE(
        None, w.HANDLE, w.DWORD, w.HWND, w.LONG, w.LONG, w.DWORD, w.DWORD
    )
    _user32.SetWinEventHook.restype = w.HANDLE
    _user32.SetWinEventHook.argtypes = [
        w.DWORD, w.DWORD, w.HANDLE, _WINEVENTPROC, w.DWORD, w.DWORD, w.DWORD
    ]
    _user32.UnhookWinEvent.argtypes = [w.HANDLE]
    _user32.PeekMessageW.argtypes = [
        ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT
    ]
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", w.DWORD),
            ("cntUsage", w.DWORD),
            ("th32ProcessID", w.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", w.DWORD),
            ("cntThreads", w.DWORD),
            ("th32ParentProcessID", w.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", w.DWORD),
            ("szExeFile", w.WCHAR * MAX_PATH),
        ]

    _kernel32.CreateToolhelp32Snapshot.restype = w.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    _kernel32.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _kernel32.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    _kernel32.CloseHandle.argtypes = [w.HANDLE]


def list_process_names() -> set[str]:
    """返回当前运行进程的可执行文件名（小写，含 .exe）。"""
    if not IS_WINDOWS:
        return set()
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return set()
    names: set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return names
        while True:
            if entry.szExeFile:
                names.add(entry.szExeFile.lower())
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snapshot)
    return names


def foreground_process() -> tuple[str, str, int] | None:
    """返回当前前台窗口的 (exe 文件名, 窗口标题, pid)；失败返回 None。"""
    if not IS_WINDOWS:
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = w.HWND
    user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    _kernel32.OpenProcess.restype = w.HANDLE
    _kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        w.HANDLE,
        w.DWORD,
        w.LPWSTR,
        ctypes.POINTER(w.DWORD),
    ]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, len(title))

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        path = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(len(path))
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return None
        exe = Path(path.value).name
        return (exe, title.value, int(pid.value))
    finally:
        _kernel32.CloseHandle(handle)


def active_process_exe() -> str:
    """返回当前前台（激活）窗口的 exe 文件名（小写）；读不到返回空串。"""
    info = foreground_process()
    if not info:
        return ""
    return (info[0] or "").lower()


class ActivationWatcher:
    """前台窗口监听：WinEvent 事件立即唤醒，定时轮询兜底。

    事件线程只负责 SetWinEventHook + 消息泵，收到前景变化就 `_wake`；
    真正读取前台进程名只在轮询线程里做，避免两个线程读到新旧不一致的状态。
    """

    def __init__(self, on_change, interval: float = 0.8, provider=None, on_tick=None):
        self.on_change = on_change
        self.on_tick = on_tick
        self.interval = max(0.3, float(interval))
        self.provider = provider or active_process_exe
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._hook_thread: threading.Thread | None = None
        self._last: str = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last = ""
        self._thread = threading.Thread(target=self._run, name="mini60-activation", daemon=True)
        self._thread.start()
        self._start_hook()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        for thread in (self._thread, self._hook_thread):
            if thread:
                thread.join(timeout)

    def update_interval(self, interval: float) -> None:
        self.interval = max(0.3, float(interval))
        self._wake.set()

    def poll_once(self) -> str:
        exe = str(self.provider() or "").lower()
        if exe != self._last:
            self._last = exe
            try:
                self.on_change(exe)
            except Exception:
                pass
        return exe

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.poll_once()
            except Exception:
                pass
            if self.on_tick:
                try:
                    self.on_tick()
                except Exception:
                    pass
            elapsed = time.monotonic() - started
            self._wake.wait(max(0.02, self.interval - elapsed))
            self._wake.clear()

    # ------------------------------------------------------------ WinEvent
    def _start_hook(self) -> None:
        if not IS_WINDOWS:
            return
        self._hook_thread = threading.Thread(
            target=self._run_hook, name="mini60-activation-hook", daemon=True
        )
        self._hook_thread.start()

    def _run_hook(self) -> None:
        callback = _WINEVENTPROC(lambda *args: self._wake.set())
        hook = _user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )
        if not hook:
            return
        msg = w.MSG()
        try:
            while not self._stop.is_set():
                while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.01)
        finally:
            try:
                _user32.UnhookWinEvent(hook)
            except Exception:
                pass


class ProcessWatcher:
    """定时轮询进程列表，有变化时回调。"""

    def __init__(self, on_change, interval: float = 0.8, provider=None, on_tick=None):
        self.on_change = on_change
        self.on_tick = on_tick
        self.interval = max(0.3, float(interval))
        self.provider = provider or list_process_names
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: set[str] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mini60-watcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def update_interval(self, interval: float) -> None:
        self.interval = max(0.3, float(interval))

    def poll_once(self) -> set[str]:
        names = set(self.provider() or set())
        if names != self._last:
            self._last = names
            try:
                self.on_change(names)
            except Exception:
                pass
        return names

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.poll_once()
            if self.on_tick:
                try:
                    self.on_tick()
                except Exception:
                    pass
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.1, self.interval - elapsed))
