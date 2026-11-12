# -*- coding: utf-8 -*-
"""Windows 系统托盘（Shell_NotifyIconW，纯 ctypes，无第三方依赖）。

托盘线程自己创建隐藏窗口并跑消息循环；菜单/双击动作通过回调交给主线程处理。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes as w
from pathlib import Path

TRAY_CLASS = "Mini60PilotTrayWnd"
TRAY_TITLE = "Mini60PilotTray"

WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_SHOW = WM_APP + 2

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007B
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002

ID_SHOW = 1
ID_EXIT = 2

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", w.DWORD),
        ("Data2", w.WORD),
        ("Data3", w.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", w.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
LRESULT = ctypes.c_ssize_t

_user32.DefWindowProcW.restype = LRESULT
_user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
_user32.RegisterClassW.restype = w.ATOM
_user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
_user32.CreateWindowExW.restype = w.HWND
_user32.CreateWindowExW.argtypes = [
    w.DWORD,
    w.LPCWSTR,
    w.LPCWSTR,
    w.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    w.HWND,
    w.HMENU,
    w.HINSTANCE,
    ctypes.c_void_p,
]
_user32.DestroyWindow.argtypes = [w.HWND]
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
_user32.FindWindowW.restype = w.HWND
_user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
_user32.LoadIconW.restype = w.HICON
_user32.LoadIconW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
_user32.LoadImageW.restype = w.HANDLE
_user32.LoadImageW.argtypes = [
    w.HINSTANCE,
    w.LPCWSTR,
    w.UINT,
    ctypes.c_int,
    ctypes.c_int,
    w.UINT,
]
_user32.CreatePopupMenu.restype = w.HMENU
_user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
_user32.TrackPopupMenu.restype = w.UINT
_user32.TrackPopupMenu.argtypes = [
    w.HMENU,
    w.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    w.HWND,
    ctypes.c_void_p,
]
_user32.DestroyMenu.argtypes = [w.HMENU]
_user32.SetForegroundWindow.argtypes = [w.HWND]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]

_shell32.Shell_NotifyIconW.restype = w.BOOL
_shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

_kernel32.GetModuleHandleW.restype = w.HMODULE
_kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]


class TrayIcon:
    def __init__(self, on_action, tooltip: str = "Mini60 场景切换器", icon_path: str | Path | None = None):
        self._on_action = on_action
        self._tooltip = tooltip
        self._icon_path = Path(icon_path) if icon_path else None
        self._hwnd: int | None = None
        self._hicon = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._nid: NOTIFYICONDATAW | None = None
        self._wndproc_cb = WNDPROC(self._wndproc)
        self._added = False

    # ------------------------------------------------------------ 生命周期
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return self._added
        self._thread = threading.Thread(target=self._run, name="mini60-tray", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self._added

    def stop(self) -> None:
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------ 线程内
    def _run(self) -> None:
        instance = _kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc_cb, ctypes.c_void_p)
        wc.hInstance = instance
        wc.lpszClassName = TRAY_CLASS
        try:
            _user32.RegisterClassW(ctypes.byref(wc))
        except Exception:
            pass

        hwnd = _user32.CreateWindowExW(
            0, TRAY_CLASS, TRAY_TITLE, 0, 0, 0, 0, 0, None, None, instance, None
        )
        if not hwnd:
            self._ready.set()
            return
        self._hwnd = hwnd
        self._hicon = self._load_icon()

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self._tooltip
        self._nid = nid
        self._added = bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        self._ready.set()

        msg = w.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        try:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass

    def _load_icon(self):
        if self._icon_path and self._icon_path.exists():
            handle = _user32.LoadImageW(
                None,
                str(self._icon_path),
                IMAGE_ICON,
                32,
                32,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if handle:
                return handle
        return _user32.LoadIconW(None, ctypes.c_void_p(32512))  # IDI_APPLICATION

    def _notify(self, action: str) -> None:
        try:
            self._on_action(action)
        except Exception:
            pass

    def _show_menu(self) -> None:
        if not self._hwnd:
            return
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "打开主界面")
        _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "退出 Mini60 场景切换器")
        point = w.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        _user32.SetForegroundWindow(self._hwnd)
        command = _user32.TrackPopupMenu(
            menu,
            TPM_RETURNCMD | TPM_RIGHTBUTTON,
            point.x,
            point.y,
            0,
            self._hwnd,
            None,
        )
        _user32.DestroyMenu(menu)
        _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        if command == ID_SHOW:
            self._notify("show")
        elif command == ID_EXIT:
            self._notify("exit")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONDBLCLK:
                    self._notify("show")
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                return 0
            if msg == WM_COMMAND:
                command = wparam & 0xFFFF
                if command == ID_SHOW:
                    self._notify("show")
                elif command == ID_EXIT:
                    self._notify("exit")
                return 0
            if msg == WM_SHOW:
                self._notify("show")
                return 0
            if msg == WM_CLOSE:
                _user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------ 气泡
    def notify(self, title: str, message: str) -> None:
        if not self._nid:
            return
        try:
            nid = self._nid
            nid.uFlags = NIF_INFO
            nid.szInfoTitle = title[:63]
            nid.szInfo = message[:255]
            nid.dwInfoFlags = NIIF_INFO
            nid.uVersion = 5000
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass
