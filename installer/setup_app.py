"""景同志·电脑加速 —— 图形化安装程序（源码）。"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import winreg

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    try:
        sys.stderr = open(os.path.join(os.environ.get("TEMP", "."), "jingboost_setup.log"),
                          "a", encoding="utf-8")
    except Exception:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import webview  # noqa: E402

APP_NAME = "景同志电脑加速"
APP_DISPLAY = "景同志·电脑加速"
VERSION = "1.4.2"
PUBLISHER = "景同志"
EXE_NAME = "景同志电脑加速.exe"
DEFAULT_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "Programs", "JingBoost")

WINDOW = None


def appdata_payload() -> str:
    for p in (
        os.path.join(BASE, "payload", EXE_NAME),
        os.path.join(BASE, "payload", "app.exe"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", EXE_NAME),
    ):
        if os.path.exists(p):
            return p
    return ""


class SetupApi:
    def default_dir(self) -> dict:
        free = 0
        try:
            free = shutil.disk_usage(DEFAULT_DIR[:3]).free
        except Exception:
            pass
        return {"path": DEFAULT_DIR, "free": free, "has_payload": bool(appdata_payload()),
                "app": APP_DISPLAY, "version": VERSION, "publisher": PUBLISHER}

    def browse(self) -> dict:
        try:
            res = WINDOW.create_file_dialog(webview.FOLDER_DIALOG)
            if res:
                return {"ok": True, "path": res[0]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}
        return {"ok": False}

    def install(self, target: str, desktop: bool = True, launch: bool = True) -> dict:
        src = appdata_payload()
        if not src:
            return {"ok": False, "msg": "安装包损坏：未找到主程序"}
        try:
            os.makedirs(target, exist_ok=True)
            dst = os.path.join(target, EXE_NAME)
            if os.path.abspath(dst) != os.path.abspath(src):
                shutil.copy2(src, dst)
            self._write_uninstall_key(target, dst)
            links = []
            if desktop:
                if self._shortcut(dst, os.path.join(
                        os.path.expanduser("~"), "Desktop", APP_DISPLAY + ".lnk")):
                    links.append("桌面")
            if self._shortcut(dst, os.path.join(
                    os.environ.get("APPDATA", ""),
                    r"Microsoft\Windows\Start Menu\Programs",
                    APP_DISPLAY + ".lnk")):
                links.append("开始菜单")
            if launch:
                subprocess.Popen([dst], close_fds=True)
            return {"ok": True, "msg": f"安装完成，已创建快捷方式：{'、'.join(links) or '无'}",
                    "path": dst}
        except PermissionError:
            return {"ok": False, "msg": "没有写入权限，请以管理员身份运行安装程序"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": f"{type(exc).__name__}: {exc}"}

    def _shortcut(self, target: str, link: str) -> bool:
        try:
            os.makedirs(os.path.dirname(link), exist_ok=True)
            ps = (
                "$W=New-Object -ComObject WScript.Shell;"
                f"$s=$W.CreateShortcut('{link}');"
                f"$s.TargetPath='{target}';"
                f"$s.WorkingDirectory='{os.path.dirname(target)}';"
                f"$s.IconLocation='{target},0';"
                f"$s.Description='{APP_DISPLAY}';"
                "$s.Save()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, timeout=40,
                creationflags=subprocess.CREATE_NO_WINDOW)
            return os.path.exists(link)
        except Exception:
            return False

    def _write_uninstall_key(self, target: str, exe: str) -> None:
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\JingBoost"
        try:
            size_kb = int(os.path.getsize(exe) / 1024)
        except Exception:
            size_kb = 30000
        values = {
            "DisplayName": (APP_DISPLAY, winreg.REG_SZ),
            "DisplayVersion": (VERSION, winreg.REG_SZ),
            "Publisher": (PUBLISHER, winreg.REG_SZ),
            "DisplayIcon": (f"{exe},0", winreg.REG_SZ),
            "InstallLocation": (target + "\\", winreg.REG_SZ),
            "UninstallString": (f'"{exe}" --uninstall', winreg.REG_SZ),
            "QuietUninstallString": (f'"{exe}" --uninstall', winreg.REG_SZ),
            "EstimatedSize": (size_kb, winreg.REG_DWORD),
            "NoModify": (1, winreg.REG_DWORD),
            "NoRepair": (1, winreg.REG_DWORD),
        }
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.CreateKeyEx(root, key_path, 0, winreg.KEY_WRITE) as k:
                    for name, (val, t) in values.items():
                        winreg.SetValueEx(k, name, 0, t, val)
            except Exception:
                continue

    def open_dir(self, path: str) -> dict:
        try:
            os.startfile(path)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}

    def finish(self) -> dict:
        if WINDOW:
            WINDOW.destroy()
        return {"ok": True}


def main() -> None:
    global WINDOW
    html = os.path.join(BASE, "installer", "index.html")
    if not os.path.exists(html):
        html = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    WINDOW = webview.create_window(
        f"{APP_DISPLAY} 安装程序", url=html, js_api=SetupApi(),
        width=720, height=560, resizable=False, background_color="#0a0e17",
    )
    webview.start(private_mode=True)


if __name__ == "__main__":
    main()
