"""开机自启动（HKCU Run 键，无需管理员权限）。

纯标准库实现；同时兼容 PyInstaller 冻结后的 exe 与源码运行。
"""
from __future__ import annotations

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "JingBoost"


def command() -> str:
    """返回写进 Run 键的命令行（带引号，兼容带空格的路径）。"""
    exe = sys.executable
    if getattr(sys, "frozen", False):
        return f'"{exe}"'
    script = os.path.abspath(sys.argv[0])
    return f'"{exe}" "{script}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(enabled: bool) -> dict:
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return {"ok": True, "enabled": is_enabled()}
    except OSError as exc:
        return {"ok": False, "msg": str(exc), "enabled": is_enabled()}
