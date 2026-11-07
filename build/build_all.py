"""一键构建：图标 -> 单文件 exe -> 安装版 exe。

用法：python build/build_all.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON = os.path.join(ROOT, "build", "app.ico")
WEB = os.path.join(ROOT, "src", "web")
DIST = os.path.join(ROOT, "dist")
APP_NAME = "景同志电脑加速"
SETUP_NAME = "景同志电脑加速_安装版"


def sh(cmd: list[str]) -> None:
    print("\n>>>", " ".join(cmd[:6]), "...")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"构建失败，退出码 {r.returncode}")


def main() -> None:
    os.makedirs(DIST, exist_ok=True)
    # 1. 图标
    sh([sys.executable, os.path.join(ROOT, "build", "make_icon.py")])

    # 2. 主程序（单文件）
    # 只收录运行时需要的 4 个文件，别把 _preview.html（带大段 mock 数据）
    # 之类的调试产物塞进 exe。
    web_files = ["index.html", "style.css", "app.js", "widget.html"]
    web_args = []
    for f in web_files:
        web_args += ["--add-data", f"{os.path.join(WEB, f)};web"]

    sh([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", APP_NAME,
        "--icon", ICON] + web_args + [
        "--collect-all", "webview",
        "--hidden-import", "clr", "--hidden-import", "pythonnet",
        "--hidden-import", "psutil",
        "--distpath", DIST,
        "--workpath", os.path.join(ROOT, "build", "work"),
        "--specpath", os.path.join(ROOT, "build"),
        os.path.join(ROOT, "src", "main.py")])

    # 3. 准备安装器 payload
    payload_dir = os.path.join(ROOT, "build", "payload")
    os.makedirs(payload_dir, exist_ok=True)
    exe = os.path.join(DIST, APP_NAME + ".exe")
    if not os.path.exists(exe):
        sys.exit("未找到主程序 exe")
    shutil.copy2(exe, os.path.join(payload_dir, "app.exe"))

    # 4. 安装程序
    sh([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", SETUP_NAME,
        "--icon", ICON,
        "--add-data", f"{os.path.join(ROOT, 'installer', 'index.html')};installer",
        "--add-data", f"{os.path.join(payload_dir, 'app.exe')};payload",
        "--collect-all", "webview",
        "--hidden-import", "clr", "--hidden-import", "pythonnet",
        "--distpath", DIST,
        "--workpath", os.path.join(ROOT, "build", "work_setup"),
        "--specpath", os.path.join(ROOT, "build"),
        os.path.join(ROOT, "installer", "setup_app.py")])

    print("\n构建完成：")
    for f in (exe, os.path.join(DIST, SETUP_NAME + ".exe")):
        if os.path.exists(f):
            print(f"  {f}  ({os.path.getsize(f) / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
