"""Win32 窗口 / 屏幕 / 鼠标原语（纯 ctypes，供托盘与悬浮胶囊复用）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                ("right", wt.LONG), ("bottom", wt.LONG)]

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


# ---- 函数签名（64 位下必须显式声明，否则句柄会被截断） ----
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
user32.SystemParametersInfoW.restype = wt.BOOL
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wt.LONG
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
user32.SetWindowLongW.restype = wt.LONG
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM),
                               wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcessId.argtypes = []
_kernel32.GetCurrentProcessId.restype = wt.DWORD

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SPI_GETWORKAREA = 0x0030
VK_LBUTTON = 0x01
SW_HIDE = 0
SW_SHOW = 5
GW_OWNER = 4


def shell_present() -> bool:
    """任务栏窗口是否还在（explorer.exe 是否活着）。"""
    return bool(user32.FindWindowW("Shell_TrayWnd", None))


def find_window(title: str) -> int:
    """按精确标题找顶层窗口句柄，找不到返回 0。"""
    return int(user32.FindWindowW(None, title) or 0)


def find_window_prefix(prefix: str) -> int:
    """按标题前缀找可见顶层窗口。

    标题里带版本号（`景同志·电脑加速  v1.4.1`），用精确匹配的话
    新旧版本互相看不见，单实例判定就会误判成「没有旧实例」。
    """
    found = 0

    def _cb(hwnd, _):
        nonlocal found
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.startswith(prefix):
            found = int(hwnd)
            return False
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb), 0)
    return found


def own_windows() -> list[tuple[int, str]]:
    """枚举**当前进程**的全部顶层窗口 (hwnd, title)。"""
    out: list[tuple[int, str]] = []
    mypid = _kernel32.GetCurrentProcessId()

    def _enum_cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != mypid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        out.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_enum_cb), 0)
    return out


def find_own_window(title: str, timeout: float = 15.0) -> int:
    """找到属于**本进程**的指定标题窗口（找不到就等，超时返回 0）。

    提权交接时旧进程的窗口标题与新进程完全一样，直接用 FindWindowW 会认到
    旧窗口的句柄：于是新胶囊没被设成工具窗口（任务栏留下按钮）、main_hwnd
    指向马上要销毁的旧句柄（主窗口不会被带回前台）。必须按 PID 过滤。
    """
    import time as _time
    deadline = _time.time() + max(0.0, timeout)
    while True:
        for hwnd, text in own_windows():
            if text == title:
                return hwnd
        if _time.time() >= deadline:
            return 0
        _time.sleep(0.1)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.as_tuple()


def move_window(hwnd: int, x: int, y: int, w: int = 0, h: int = 0,
                flags: int = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE) -> bool:
    return bool(user32.SetWindowPos(hwnd, None, int(x), int(y), int(w), int(h), flags))


def cursor_pos() -> tuple[int, int]:
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def left_button_down() -> bool:
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def work_area() -> tuple[int, int, int, int]:
    """返回屏幕工作区（不含任务栏）。"""
    r = RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.as_tuple()
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def taskbar_eligible(hwnd: int) -> bool:
    """判断窗口是否会被 Windows 分配任务栏按钮（用于自检）。

    规则：有 WS_EX_APPWINDOW 一定显示；有 WS_EX_TOOLWINDOW 一定不显示；
    其余情况下，没有 owner 的可见顶层窗口才显示。
    """
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if style & WS_EX_APPWINDOW:
            return True
        if style & WS_EX_TOOLWINDOW:
            return False
        return not user32.GetWindow(hwnd, GW_OWNER)
    except Exception:
        return False


def make_tool_window(hwnd: int) -> None:
    """从任务栏 / Alt-Tab 里摘掉窗口（悬浮胶囊用）。

    三层保障，缺一不可：
    1) WS_EX_TOOLWINDOW + 去掉 WS_EX_APPWINDOW；
    2) SWP_FRAMECHANGED 让外壳重新计算窗口归属（pywebview/WinForms
       默认 ShowInTaskbar=True，创建时已经给自己挂了任务栏按钮）；
    3) ITaskbarList::DeleteTab 显式把已生成的按钮删除，Win11 上最可靠。

    样式已经是目标值时跳过 hide/show，避免重复调用造成闪烁。
    """
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        target = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        changed = target != style
        if changed:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, target)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                                | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            user32.ShowWindow(hwnd, SW_HIDE)
            user32.ShowWindow(hwnd, SW_SHOW)
        taskbar_delete_tab(hwnd)
    except Exception:
        pass


def show_window(hwnd: int, show: bool) -> None:
    user32.ShowWindow(hwnd, SW_SHOW if show else SW_HIDE)


# ---- 整窗恒定透明度：WebView2 底下是 WinForms 的灰底，只靠 CSS 调 alpha
# 会变成「灰底上盖一层半透明卡片」，正是之前那圈脏边的成因。
# 交给 DWM 做整窗 alpha，边缘和阴影才干净。
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x00000002
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, wt.BYTE, wt.DWORD]
user32.SetLayeredWindowAttributes.restype = wt.BOOL
user32.GetLayeredWindowAttributes.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD),
                                              ctypes.POINTER(wt.BYTE),
                                              ctypes.POINTER(wt.DWORD)]
user32.GetLayeredWindowAttributes.restype = wt.BOOL


def set_window_alpha(hwnd: int, alpha: int) -> bool:
    """整窗透明度，alpha 取 0-255（255 = 完全不透明）。

    注意：**必须常驻 WS_EX_LAYERED**，只在 255 时把 alpha 设回 255，不能
    反复摘掉这个样式位。WebView2 走 DirectComposition，样式位来回增删后
    合成层不会重新接受恒定 alpha —— 表现就是 GetLayeredWindowAttributes
    读回来是对的（128/204），屏幕上却一点也没透。
    """
    if not hwnd:
        return False
    try:
        alpha = max(0, min(255, int(alpha)))
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_LAYERED:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                                | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        return bool(user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA))
    except Exception:
        return False


def get_window_alpha(hwnd: int) -> int:
    """读回当前透明度（0-255），用于验证；未启用恒定 alpha 时返回 255。"""
    try:
        flags = wt.DWORD()
        alpha = wt.BYTE()
        if user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha),
                                             ctypes.byref(flags)):
            if not (flags.value & LWA_ALPHA):
                return 255
            return int(alpha.value)
        return 255
    except Exception:
        return 255


# ---- 圆角窗口裁剪：WebView2 的 transparent 在本机不生效，窗口底下是
# Form 默认灰底（#E4E6E8），只靠 CSS 圆角就会露出一圈"透明边"。
# 直接把窗口区域裁成圆角，内容再铺满，边缘就彻底干净了。
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.CreateRoundRectRgn.restype = wt.HANDLE
user32.SetWindowRgn.argtypes = [wt.HWND, wt.HANDLE, wt.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int


def set_round_region(hwnd: int, w: int, h: int, radius: int = 15) -> bool:
    """把窗口裁成圆角矩形。rgn 交给系统托管，不要再 DeleteObject。"""
    if not hwnd or w <= 0 or h <= 0:
        return False
    try:
        rgn = gdi32.CreateRoundRectRgn(0, 0, int(w) + 1, int(h) + 1,
                                       int(radius) * 2, int(radius) * 2)
        if not rgn:
            return False
        if not user32.SetWindowRgn(hwnd, rgn, True):
            gdi32.DeleteObject(rgn)
            return False
        return True
    except Exception:
        return False


# ---- ITaskbarList::DeleteTab：显式把窗口从任务栏摘掉（Win11 上最可靠） ----
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(d1, d2, d3, tail) -> _GUID:
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*tail))


_CLSID_TASKBARLIST = _guid(0x56FDF344, 0xFD6D, 0x11D0,
                           [0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90])
_IID_ITASKBARLIST = _guid(0x56FDF342, 0xFD6D, 0x11D0,
                          [0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90])

_ole32 = ctypes.WinDLL("ole32")
_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_ole32.CoCreateInstance.restype = ctypes.c_long
_CLSCTX_INPROC_SERVER = 1
_COINIT_APARTMENTTHREADED = 0x2
_HRESULT = ctypes.c_long


def taskbar_delete_tab(hwnd: int) -> bool:
    """调用 ITaskbarList::DeleteTab，返回是否成功。可重复、跨线程调用。

    WinForms 窗口创建时外壳已经登记了任务栏按钮，光改窗口样式来不及，
    必须显式调用 DeleteTab 把按钮摘掉；explorer 重启后按钮会回来，
    所以调用方需要周期性补刷。
    """
    if not hwnd:
        return False
    hr_init = -1
    obj = ctypes.c_void_p()
    try:
        hr_init = _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        # 0=S_OK，1=S_FALSE(已初始化)；两者都需要配对 CoUninitialize。
        # RPC_E_CHANGED_MODE 等失败码不能再配对了，但仍可尝试复用现有单元。
        hr = _ole32.CoCreateInstance(ctypes.byref(_CLSID_TASKBARLIST), None,
                                     _CLSCTX_INPROC_SERVER,
                                     ctypes.byref(_IID_ITASKBARLIST),
                                     ctypes.byref(obj))
        if hr != 0 or not obj:
            return False
        vtable = ctypes.cast(
            obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        # IUnknown: 0=QI 1=AddRef 2=Release
        # ITaskbarList: 3=HrInit 4=AddTab 5=DeleteTab 6=ActivateTab 7=SetActiveAlt
        _fn = ctypes.WINFUNCTYPE
        hr_init_fn = _fn(_HRESULT, ctypes.c_void_p)(vtable[3])
        delete_tab = _fn(_HRESULT, ctypes.c_void_p, wt.HWND)(vtable[5])
        release = _fn(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        hr_init_fn(obj)
        hr = delete_tab(obj, hwnd)
        release(obj)
        obj = ctypes.c_void_p()
        return hr == 0
    except Exception:
        if obj:
            try:
                vtable = ctypes.cast(
                    obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)
            except Exception:
                pass
        return False
    finally:
        if hr_init in (0, 1):
            _ole32.CoUninitialize()
