"""系统托盘（纯 ctypes 实现 Shell_NotifyIconW，无第三方依赖）。

pywebview 的 webview.start() 会占用主线程跑 WinForms 消息循环，
因此托盘的消息循环放在独立线程里。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading
from typing import Callable

from . import icons, native

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# ---- 常量 ----
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_APP_QUIT = WM_USER + 21
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_NULL = 0x0000
WM_DESTROY = 0x0002
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_LEFTBUTTON, TPM_RIGHTALIGN, TPM_BOTTOMALIGN = 0x0, 0x8, 0x20
IDI_APPLICATION = 32512

_LRESULT = ctypes.c_ssize_t
_WPARAM = ctypes.c_size_t
_LPARAM = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wt.HWND, wt.UINT, _WPARAM, _LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND), ("message", wt.UINT),
        ("wParam", _WPARAM), ("lParam", _LPARAM),
        ("time", wt.DWORD), ("pt", native.POINT), ("lPrivate", wt.DWORD),
    ]


class _NotifyUnion(ctypes.Union):
    _fields_ = [("uTimeout", wt.UINT), ("uVersion", wt.UINT)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("_u", _NotifyUnion),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON),
    ]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


# ---- 函数签名 ----
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.DestroyWindow.argtypes = [wt.HWND]
user32.DestroyWindow.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wt.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = _LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, _WPARAM, _LPARAM]
user32.DefWindowProcW.restype = _LRESULT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, _WPARAM, _LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, _WPARAM, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL
user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
user32.LoadIconW.restype = wt.HICON
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE


def _app_hicon() -> int:
    """取程序自身图标作为托盘图标。"""
    try:
        h = icons._extract_hicon(sys.executable)
        if h:
            return h
    except Exception:
        pass
    try:
        return int(user32.LoadIconW(
            None, ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), wt.LPCWSTR)) or 0)
    except Exception:
        return 0


class Tray:
    """托盘图标。commands: {菜单ID: 无参回调}；autostart_getter 返回是否勾选自启。"""

    CLASS_NAME = "JingBoostTrayHostClass"
    WINDOW_NAME = "JingBoostTrayHost"

    def __init__(self, tip: str, commands: dict[int, Callable[[], None]],
                 autostart_getter: Callable[[], bool] | None = None):
        self.tip = tip[:127]
        self.commands = commands
        self.autostart_getter = autostart_getter or (lambda: False)
        self.hwnd = 0
        self.hicon = 0
        self._ready = threading.Event()
        self._wndproc_ref = None
        self._taskbar_msg = 0
        self._added = False

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        threading.Thread(target=self._loop, name="JingBoostTray", daemon=True).start()
        self._ready.wait(3.0)

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_QUIT, 0, 0)

    def balloon(self, title: str, text: str) -> None:
        if not self.hwnd or not self._added:
            return
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.tip
        return nid

    def _add_icon(self) -> bool:
        self._added = bool(shell32.Shell_NotifyIconW(
            NIM_ADD, ctypes.byref(self._nid())))
        return self._added

    # ------------------------------------------------------------ 菜单
    def _popup_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        labels = [
            (1, "显示主界面"),
            (2, "显示 / 隐藏 悬浮胶囊"),
            (3, "一键加速"),
            (None, None),
            (4, "开机自动启动"),
            (None, None),
            (9, "退出程序"),
        ]
        for mid, label in labels:
            if mid is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            flags = MF_STRING
            if mid == 4 and self.autostart_getter():
                flags |= MF_CHECKED
            user32.AppendMenuW(menu, flags, mid, label)
        x, y = native.cursor_pos()
        user32.SetForegroundWindow(self.hwnd)
        user32.TrackPopupMenu(menu, TPM_LEFTBUTTON | TPM_RIGHTALIGN | TPM_BOTTOMALIGN,
                              x, y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

    # ------------------------------------------------------------ 消息循环
    def _loop(self) -> None:
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self.hicon = _app_hicon()
            self._taskbar_msg = user32.RegisterWindowMessageW("TaskbarCreated")

            def wndproc(hwnd, msg, wparam, lparam):
                if msg == WM_TRAY:
                    if lparam == WM_RBUTTONUP:
                        self._popup_menu()
                    elif lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                        cb = self.commands.get(1)
                        cb and cb()
                    return 0
                if msg == WM_COMMAND:
                    mid = wparam & 0xFFFF
                    cb = self.commands.get(mid)
                    if cb:
                        threading.Thread(target=cb, daemon=True).start()
                    return 0
                if msg == self._taskbar_msg and self._taskbar_msg:
                    self._add_icon()
                    return 0
                if msg == WM_APP_QUIT:
                    if self._added:
                        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))
                    user32.DestroyWindow(hwnd)
                    user32.PostQuitMessage(0)
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            proc = WNDPROC(wndproc)
            self._wndproc_ref = proc          # 防止被 GC
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = proc
            wc.hInstance = hinst
            wc.lpszClassName = self.CLASS_NAME
            wc.hCursor = None
            wc.hIcon = None
            wc.hbrBackground = None
            user32.RegisterClassW(ctypes.byref(wc))

            self.hwnd = user32.CreateWindowExW(
                0, self.CLASS_NAME, self.WINDOW_NAME, 0,
                0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                self._ready.set()
                return
            self._add_icon()
            self._ready.set()

            m = MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(m), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                user32.TranslateMessage(ctypes.byref(m))
                user32.DispatchMessageW(ctypes.byref(m))
        except Exception:
            self._ready.set()
