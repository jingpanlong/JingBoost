"""端到端界面自检：启动窗口，用 JS 检查渲染与数据流，然后自动关闭。"""
import os
import sys
import threading
import time

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, BASE)

import webview  # noqa: E402

from api import Api  # noqa: E402

RESULT = []


def probe(window):
    time.sleep(6)
    checks = [
        ("heroDesc", "document.querySelector('#heroDesc').textContent.slice(0,60)"),
        ("score", "document.querySelector('#scoreVal').textContent"),
        ("issues", "document.querySelectorAll('.issue').length"),
        ("toprows", "document.querySelectorAll('.top-row').length"),
        ("cpuTop", "document.querySelector('#topCpu').textContent"),
        ("adminPill", "document.querySelector('#adminPill').textContent"),
        ("api_exists", "typeof window.pywebview.api.get_overview"),
        ("js_errors", "JSON.stringify(window.__errs || [])"),
        ("theme", "document.body.classList.contains('theme-dark') ? 'dark' : 'light'"),
        ("theme_btn", "!!document.querySelector('#btnTheme')"),
    ]
    for name, js in checks:
        try:
            r = window.evaluate_js(js)
            RESULT.append(f"{name}: {r}")
        except Exception as exc:  # noqa: BLE001
            RESULT.append(f"{name}: ERROR {exc}")
    # 切到清理页并触发扫描
    try:
        window.evaluate_js("go('clean')")
        time.sleep(1)
        RESULT.append("clean_page: " + str(window.evaluate_js(
            "document.querySelector('#page-clean').classList.contains('active')")))
    except Exception as exc:  # noqa: BLE001
        RESULT.append("clean_page ERROR " + str(exc))
    # 实测主题切换按钮（含设置持久化桥接），随后切回浅色
    try:
        window.evaluate_js("document.querySelector('#btnTheme').click()")
        time.sleep(1.2)
        RESULT.append("theme_to_dark: " + str(window.evaluate_js(
            "document.body.classList.contains('theme-dark')")))
        window.evaluate_js("document.querySelector('#btnTheme').click()")
        time.sleep(1.2)
        RESULT.append("theme_back_light: " + str(window.evaluate_js(
            "!document.body.classList.contains('theme-dark')")))
    except Exception as exc:  # noqa: BLE001
        RESULT.append("theme_toggle ERROR " + str(exc))
    # 切到进程页，等图标异步加载后统计真实渲染出来的图标数量
    try:
        window.evaluate_js("go('process')")
        time.sleep(9)
        RESULT.append("proc_icons: " + str(window.evaluate_js(
            "document.querySelectorAll('#procTable img.app-ico').length")))
        RESULT.append("proc_rows: " + str(window.evaluate_js(
            "document.querySelectorAll('#procTable tbody tr').length")))
        window.evaluate_js("go('startup')")
        time.sleep(4)
        RESULT.append("startup_icon_n: " + str(window.evaluate_js(
            "document.querySelectorAll('#startupTable img.app-ico').length")))
    except Exception as exc:  # noqa: BLE001
        RESULT.append("proc_icons ERROR " + str(exc))
    time.sleep(1)
    window.destroy()


if __name__ == "__main__":
    win = webview.create_window(
        "selftest", url=os.path.join(BASE, "web", "index.html"),
        js_api=Api(), width=1200, height=790, background_color="#f5f7fc",
    )
    threading.Thread(target=probe, args=(win,), daemon=True).start()
    webview.start(private_mode=True)
    print("\n".join(RESULT))
