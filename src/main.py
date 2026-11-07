"""景同志·电脑加速 —— 程序入口。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

# PyInstaller --windowed 模式下 stdout/stderr 为 None，会导致部分库 print 报错
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    try:
        sys.stderr = open(os.path.join(os.environ.get("TEMP", "."), "jingboost.log"),
                          "a", encoding="utf-8")
    except Exception:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _log_exc(exc_type, exc, tb) -> None:
    """崩溃日志写到 %LOCALAPPDATA%\\JingBoost\\logs。

    别放 %TEMP%：本工具的「垃圾清理」会清空临时目录，等于把自己的
    诊断现场一起删掉，出了事什么线索都留不下。
    """
    import traceback
    try:
        base = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", "."),
                            "JingBoost", "logs")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "jingboost.log"), "a", encoding="utf-8") as f:
            f.write(f"\n==== 崩溃 {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass


sys.excepthook = _log_exc

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import webview  # noqa: E402

from api import APP_NAME, APP_VERSION, Api, _load_settings  # noqa: E402
from core import autostart, native, winapi  # noqa: E402
from ui import WIDGET_H, WIDGET_TITLE, WIDGET_W, DesktopUI  # noqa: E402

WEB_DIR = os.path.join(BASE, "web")
INDEX = os.path.join(WEB_DIR, "index.html")
MAIN_TITLE = f"{APP_NAME}  v{APP_VERSION}"

MUTEX_NAME = "Local\\JingZhiTongSpeedUp_SingleInstance"
_mutex = 0

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
_kernel32.CreateMutexW.restype = wt.HANDLE
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.restype = wt.BOOL


# ------------------------------------------------------------------ 单实例
def _acquire_mutex(timeout: float = 0.0, force: bool = False) -> bool:
    """抢占单实例互斥体。

    timeout > 0 时会在「旧实例仍在运行」期间重试等待（提权交接用）。
    force=True 表示等待超时后强制接管，避免提权子进程永远起不来。

    原来用 `Global\\` 前缀：普通用户进程没有 SeCreateGlobalPrivilege，
    CreateMutexW 直接返回 NULL + ERROR_ACCESS_DENIED(5)，旧代码把「错误码不是
    183」一律当成抢到了，于是锁形同虚设 —— 连点两次会同时跑起两个实例，
    onefile 各自往 %TEMP% 解包，正是启动失败的温床。改成会话内的 Local\\，
    并且严格区分「我创建的」和「已存在」。
    """
    global _mutex
    deadline = time.time() + timeout
    while True:
        ctypes.set_last_error(0)
        h = _kernel32.CreateMutexW(None, True, MUTEX_NAME)
        already = ctypes.get_last_error() == 183   # ERROR_ALREADY_EXISTS
        if h and not already:
            _mutex = h
            return True
        if h:
            _kernel32.CloseHandle(h)               # 别泄漏自己那份句柄
        if time.time() >= deadline:
            break
        time.sleep(0.15)
    if force:
        _mutex = _kernel32.CreateMutexW(None, True, MUTEX_NAME) or 0
        return True
    return False


class _InstanceGuard:
    """暴露给 Api.elevate()，让提权前主动交出互斥体。"""

    @staticmethod
    def release_instance() -> None:
        global _mutex
        if _mutex:
            _kernel32.CloseHandle(_mutex)
            _mutex = 0

    @staticmethod
    def acquire_instance() -> bool:
        return _acquire_mutex()


def _wake_existing() -> bool:
    """已有实例就把它唤醒到前台。

    返回 False 表示「锁还在、窗口没了」——那是崩溃残留的僵尸锁，
    调用方应该强制接管，而不是弹一句「程序已经在运行了」把用户堵在门外。
    """
    hwnd = native.find_window_prefix(APP_NAME)
    if not hwnd:
        return False
    native.show_window(hwnd, True)
    native.user32.ShowWindow(hwnd, 9)         # SW_RESTORE
    native.user32.SetForegroundWindow(hwnd)
    return True


# ------------------------------------------------------------------ 卸载
def _uninstall_flow() -> None:
    """--uninstall 时执行卸载：删除快捷方式、自启项、注册表、程序目录。"""
    import shutil  # noqa: F401

    import winreg

    exe = sys.executable
    appdir = os.path.dirname(exe)

    try:
        autostart.set_enabled(False)
    except Exception:
        pass

    folders = [
        os.path.join(os.environ.get("APPDATA", ""),
                     r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(os.path.expanduser("~"), "Desktop"),
        os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop"),
    ]
    for folder in folders:
        try:
            for f in os.listdir(folder):
                if f.endswith(".lnk") and "电脑加速" in f:
                    try:
                        os.remove(os.path.join(folder, f))
                    except Exception:
                        pass
        except Exception:
            pass

    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\JingBoost"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\JingBoost"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\JingBoost"),
    ):
        try:
            winreg.DeleteKey(root, key)
        except Exception:
            pass

    batch = os.path.join(os.environ.get("TEMP", "."), "_jz_uninstall.bat")
    try:
        with open(batch, "w", encoding="gbk") as f:
            f.write("@echo off\r\n")
            f.write("timeout /t 2 /nobreak >nul\r\n")
            f.write(f'rmdir /s /q "{appdir}"\r\n')
            f.write(f'del /f /q "{batch}"\r\n')
        os.startfile(batch)
    except Exception:
        pass


# ------------------------------------------------------------------ 主流程
def main() -> None:
    if "--uninstall" in sys.argv:
        _uninstall_flow()
        return

    elevated_child = winapi.ELEVATE_FLAG in sys.argv
    if elevated_child:
        # 提权交接：等旧进程退出再接管；最多 8 秒，超时也强制接管
        _acquire_mutex(timeout=8.0, force=True)
    elif not _acquire_mutex():
        if _wake_existing():
            return
        # 锁被占着但根本找不到窗口（上次崩溃/被强杀留下的僵尸锁）：
        # 强制接管，别让用户对着"程序已经在运行"却什么都看不到
        _acquire_mutex(force=True)

    settings = _load_settings()
    api = Api(lifecycle=_InstanceGuard())

    main_window = webview.create_window(
        MAIN_TITLE,
        url=INDEX,
        js_api=api,
        width=1200,
        height=790,
        min_size=(1040, 700),
        background_color="#f5f7fc",
        text_select=False,
        confirm_close=False,
    )

    # 悬浮胶囊：无边框 + 置顶
    # 不用 transparent=True —— 本机 WebView2 上它不生效，窗口底下仍是
    # Form 的默认灰底，CSS 圆角外会露出一圈"透明边"。改为窗口圆角裁剪
    # （native.set_round_region）+ 内容铺满，窗口底色只在动画首帧可见。
    widget_bg = "#111828" if settings.get("theme") == "dark" else "#ffffff"
    wl, wt_, wr, wb = native.work_area()
    wx = settings.get("widget_x")
    wy = settings.get("widget_y")
    if not isinstance(wx, int) or not isinstance(wy, int):
        wx, wy = wr - WIDGET_W - 28, wb - WIDGET_H - 28
    widget_window = webview.create_window(
        WIDGET_TITLE,
        url=os.path.join(WEB_DIR, "widget.html"),
        js_api=api,
        width=WIDGET_W,
        height=WIDGET_H,
        min_size=(200, 40),
        x=wx,
        y=wy,
        resizable=False,
        frameless=True,
        easy_drag=False,
        on_top=True,
        transparent=False,
        shadow=False,
        focus=False,
        background_color=widget_bg,
        text_select=False,
        confirm_close=False,
    )

    ui = DesktopUI(api, main_window, widget_window, MAIN_TITLE, settings,
                   elevated=elevated_child)
    api.attach_ui(ui)
    main_window.events.closing += lambda: ui.on_closing()
    main_window.events.closed += lambda: ui.shutdown()
    widget_window.events.closing += lambda: ui.on_widget_closing()

    webview.start(ui.boot, debug="--debug" in sys.argv, private_mode=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        _log_exc(*sys.exc_info())
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "启动失败，日志已写入 %TEMP%\\jingboost.log\n\n"
                      + traceback.format_exc()[-600:], APP_NAME, 0x10)
        except Exception:
            pass
        raise
