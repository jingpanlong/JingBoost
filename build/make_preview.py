"""生成带真实数据的界面预览页，用于渲染检查。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from api import Api, _load_settings  # noqa: E402

api = Api()
# 先把完整硬件信息跑出来（内存条 / 物理磁盘 / 温度），否则预览页拿到的是
# 首屏那份 partial 快照，卡片上「2 条 DDR4」「3 块硬盘」都会是空的。
api.get_sysinfo()

_proc = api.proc_list("mem")
_startup = api.startup_list()
_icon_src = []
for _it in (_proc.get("items") or [])[:40] + (_startup.get("items") or [])[:40]:
    _p = _it.get("exe")
    if _p:
        _icon_src.append(_p)

data = {
    "get_env": api.get_env(),
    "get_settings": {"ok": True, "settings": _load_settings()},
    "get_icons": api.get_icons(_icon_src),
    "get_ui_settings": {"ok": True, "settings": _load_settings(),
                        "autostart": False,
                        "widget_state": {"enabled": True, "peek": None,
                                        "theme": "light", "boosting": False}},
    "widget_state": {"enabled": True, "peek": None, "theme": "light",
                     "autostart": False, "close_to_tray": True,
                     "edge_hide": True, "boosting": False, "boost_msg": ""},
    "widget_data": {"cpu": 12, "ram": 56, "score": 90, "net": "1.2 MB/s"},
    "get_autostart": {"ok": True, "enabled": False},
    "get_overview": api.get_overview(),
    "get_sysinfo": api.get_sysinfo(),
    "clean_scan": api.clean_scan(),
    "proc_list": _proc,
    "svc_list": api.svc_list(),
    "startup_list": _startup,
    "tweak_list": api.tweak_list(),
    "repair_overview": api.repair_overview(),
    "net_adapters": api.net_adapters(),
    "net_ping": api.net_ping(),
    "power_plans": api.power_plans(),
    "net_hosts": api.net_hosts(),
    "get_metrics": api.get_metrics(),
}

mock = """
<style>*{animation:none !important;transition:none !important}</style>
<script>
window.__MOCK__ = %s;
window.pywebview = { api: {} };
Object.keys(window.__MOCK__).forEach(function (k) {
  var v = window.__MOCK__[k];
  window.pywebview.api[k] = function () { return Promise.resolve(v); };
});
window.pywebview.api.export_report = function(){ return Promise.resolve({ok:true,msg:'ok'}); };
setTimeout(function () {
  var p = new URLSearchParams(location.search).get('page');
  if (p && window.go) window.go(p);
  if (p === 'clean' && window.renderClean) {
    window.renderClean(window.__MOCK__.clean_scan.items);
  }
  if (p === 'network') {
    var b = document.querySelector('#btnPing');
    if (b) b.click();
  }
}, 900);
</script>
""" % json.dumps(data, ensure_ascii=False, default=str)

src = os.path.join(ROOT, "src", "web", "index.html")
dst = os.path.join(ROOT, "src", "web", "_preview.html")
with open(src, "r", encoding="utf-8") as f:
    html = f.read()
html = html.replace('<script src="app.js"></script>', mock + '<script src="app.js"></script>')
with open(dst, "w", encoding="utf-8") as f:
    f.write(html)
print("preview written:", dst)
