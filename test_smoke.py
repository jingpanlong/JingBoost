"""后端接口冒烟测试（不启动 GUI）。"""
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from api import Api  # noqa: E402

api = Api()
# 导出报告这条必须落到临时目录：默认落点是用户桌面，
# 跑一次测试就在人家桌面上留一个「电脑体检报告_xxx.txt」，用户会以为软件自己乱写文件。
REPORT_DIR = tempfile.mkdtemp(prefix="jb_smoke_report_")
CASES = [
    ("get_env", lambda: api.get_env()),
    ("get_settings", lambda: api.get_settings()),
    ("get_icons", lambda: api.get_icons(["C:\\Windows\\notepad.exe", "C:\\Windows\\explorer.exe"])),
    ("get_ui_settings", lambda: api.get_ui_settings()),
    ("get_autostart", lambda: api.get_autostart()),
    ("widget_state", lambda: api.widget_state()),
    ("widget_data", lambda: api.widget_data()),
    ("get_metrics", lambda: api.get_metrics()),
    ("proc_list", lambda: api.proc_list("mem")),
    ("svc_list", lambda: api.svc_list()),
    ("clean_scan", lambda: api.clean_scan()),
    ("startup_list", lambda: api.startup_list()),
    ("net_adapters", lambda: api.net_adapters()),
    ("get_sysinfo", lambda: api.get_sysinfo()),
    ("get_overview", lambda: api.get_overview()),
    ("tweak_list", lambda: api.tweak_list()),
    ("repair_overview", lambda: api.repair_overview()),
    ("net_ping", lambda: api.net_ping()),
    ("net_hosts", lambda: api.net_hosts()),
    ("power_plans", lambda: api.power_plans()),
    ("health", lambda: api.export_report(REPORT_DIR)),
]

ok = fail = 0
for name, fn in CASES:
    t0 = time.time()
    try:
        r = fn()
        dt = time.time() - t0
        brief = ""
        if isinstance(r, dict):
            keys = [k for k in r.keys()][:6]
            brief = str(keys)
            if "items" in r and isinstance(r["items"], list):
                brief += f" items={len(r['items'])}"
            if "msg" in r:
                brief += f" msg={r['msg']}"
        print(f"[OK] {name:16s} {dt:5.1f}s  {brief[:170]}")
        ok += 1
    except Exception:
        fail += 1
        print(f"[FAIL] {name}")
        traceback.print_exc()

print(f"\n=== {ok} passed, {fail} failed ===")
