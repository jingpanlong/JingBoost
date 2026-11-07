"""前后端桥接层：暴露给 WebView 的所有接口。"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid

from core import (autostart, cleaner, icons, network, processes, reg, repair,
                  scanner, services, startup, sysinfo, tweaks, winapi)

APP_NAME = "景同志·电脑加速"
APP_VERSION = "1.4.4"
AUTHOR = {
    "wechat": "景同志",
    "xhs": "景同学",
    "slogan": "让每一台电脑都跑得更顺一点",
}

SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                             "JingBoost", "settings.json")
DEFAULT_SETTINGS = {
    "theme": "light",
    "widget": True,               # 桌面悬浮胶囊
    "widget_edge_hide": True,     # 贴边自动隐藏
    "close_to_tray": True,        # 关闭主窗口时收进托盘
    "widget_x": None,
    "widget_y": None,
    # --- 悬浮胶囊外观 ---
    "widget_opacity": 100,                       # 30-100（%），整窗 alpha
    "widget_accent": "blue",                     # 预设配色 key 或 custom
    "widget_color": "#2f6bff",                   # 自定义主色（accent=custom 时生效）
    "widget_items": ["score", "cpu", "ram", "net"],  # 胶囊上显示哪些信息
}

# 胶囊配色预设：key -> (浅色主题主色, 浅色主题辅色)
WIDGET_ACCENTS = {
    "blue":   ("#2f6bff", "#5b4bff"),
    "cyan":   ("#0891b2", "#0ea5e9"),
    "green":  ("#10b981", "#22c55e"),
    "violet": ("#8b5cf6", "#a855f7"),
    "amber":  ("#f59e0b", "#f97316"),
    "pink":   ("#ec4899", "#f472b6"),
    "slate":  ("#334155", "#475569"),
}
WIDGET_ITEMS = ("score", "cpu", "ram", "net")


def _hex_to_rgb(c: str) -> tuple[int, int, int] | None:
    c = str(c or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return None
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return None


def _shift_color(c: str, delta: int) -> str:
    """把颜色整体提亮/压暗 delta（-255..255），用来算渐变的第二个色。"""
    rgb = _hex_to_rgb(c)
    if not rgb:
        return c
    out = [max(0, min(255, v + delta)) for v in rgb]
    return "#%02x%02x%02x" % tuple(out)


def widget_accent_colors(settings: dict) -> tuple[str, str]:
    """返回胶囊渐变用的 (主色, 辅色)。自定义色自动推一个辅色。"""
    key = str(settings.get("widget_accent") or "blue")
    if key in WIDGET_ACCENTS:
        return WIDGET_ACCENTS[key]
    custom = str(settings.get("widget_color") or "")
    if _hex_to_rgb(custom):
        if not custom.startswith("#"):
            custom = "#" + custom
        return custom, _shift_color(custom, -28)
    return WIDGET_ACCENTS["blue"]


def _sanitize_widget_items(v) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return list(WIDGET_ITEMS)
    keep = [str(x) for x in v if str(x) in WIDGET_ITEMS]
    return keep or []          # 允许全关（只留加速按钮）


def _load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data.update(json.load(f) or {})
    except Exception:
        pass
    return data


def _save_settings(data: dict) -> dict:
    cur = _load_settings()
    cur.update(data or {})
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        return {"ok": True, "settings": cur}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc), "settings": cur}


# ------------------------------------------------------------------ 任务系统

class Job:
    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.status = "running"      # running / done / error / cancelled
        self.progress = 0
        self.message = "准备中…"
        self.logs: list[str] = []
        self.result: dict | None = None
        self.error = ""
        self.started = time.time()

    def push(self, msg: str) -> None:
        self.message = msg
        self.logs.append(msg)
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title,
            "status": self.status, "progress": self.progress,
            "message": self.message, "logs": self.logs[-40:],
            "result": self.result, "error": self.error,
            "elapsed": round(time.time() - self.started, 1),
        }


_jobs: dict[str, Job] = {}
_job_lock = threading.Lock()


def _start_job(kind: str, title: str, fn) -> str:
    job = Job(kind, title)
    with _job_lock:
        _jobs[job.id] = job
        # 只保留最近 12 个任务
        if len(_jobs) > 12:
            for k in list(_jobs)[:-12]:
                _jobs.pop(k, None)

    def runner():
        try:
            res = fn(job)
            job.result = res if isinstance(res, dict) else {"ok": True}
            job.status = "done"
            job.progress = 100
            job.message = job.result.get("msg") or "已完成"
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "执行失败"
            job.logs.append(traceback.format_exc()[-1200:])

    threading.Thread(target=runner, daemon=True).start()
    return job.id


# ------------------------------------------------------------------ API 类

_WIDGET_SCORE = {"value": None, "time": 0.0}


class Api:
    def __init__(self, lifecycle=None):
        # lifecycle: main.py 提供的单实例锁管理器（提权交接用）
        self._lifecycle = lifecycle
        self._ui = None

    def attach_ui(self, ui) -> None:
        self._ui = ui
        # 后台预热：健康评分（首次体检 1~3 秒）+ 内存条/温度/完整硬件信息。
        # 串行跑，避免同时拉起多个 PowerShell 进程拖慢首屏。
        threading.Thread(target=self._warm_all, name="JingBoostWarm",
                         daemon=True).start()

    @staticmethod
    def _warm_all() -> None:
        try:
            sysinfo.warm()
        except Exception:
            pass
        try:
            _WIDGET_SCORE["value"] = repair.system_health_summary().get("score")
            _WIDGET_SCORE["time"] = time.time()
        except Exception:
            pass

    # ------------------------------------------------ 基础信息
    def get_env(self) -> dict:
        import platform
        return {
            "app": APP_NAME, "version": APP_VERSION, "author": AUTHOR,
            "admin": winapi.is_admin(),
            "hostname": os.environ.get("COMPUTERNAME", ""),
            "user": os.environ.get("USERNAME", ""),
            "os": f"{platform.system()} {platform.release()}",
            "backup": reg.backup_info(),
            "frozen": bool(getattr(__import__("sys"), "frozen", False)),
        }

    def elevate(self) -> dict:
        """提权重启。

        顺序很关键：
          1. 先交出单实例互斥体（否则管理员子进程会撞上旧锁自杀）
          2. 弹 UAC —— 这一步是阻塞的，界面保持原样，用户点「否」也不会丢界面
          3. UAC 通过后，才撤托盘 / 主窗口 / 胶囊，然后立刻退出
          4. 子进程（--elevated-child）重新接管并显示自己的主窗口与胶囊

        旧版把第 3 步放在第 2 步之前，UAC 点「否」时界面已经被撤掉又不会恢复，
        用户会看到「程序消失了但进程还在」。
        """
        guard = self._lifecycle
        if guard:
            guard.release_instance()
        ok = winapi.run_as_admin(winapi.ELEVATE_FLAG)
        if ok:
            # 管理员进程已经起来了，马上把旧界面全部撤掉再退出，
            # 让用户明确看到「旧程序退了 → 新程序接管」
            if self._ui:
                try:
                    self._ui.prepare_elevation()
                except Exception:
                    pass
            threading.Timer(0.2, lambda: os._exit(0)).start()
            return {"ok": True, "msg": "正在以管理员身份重新启动…"}
        # 用户在 UAC 弹窗点了「否」：收回锁，程序继续正常运行
        if guard:
            guard.acquire_instance()
        return {"ok": False, "msg": "提权已取消，程序保持运行"}

    # ------------------------------------------------ 设置 / 图标
    def get_settings(self) -> dict:
        return {"ok": True, "settings": _load_settings()}

    def save_settings(self, data: dict) -> dict:
        r = _save_settings(data or {})
        if self._ui and data and "theme" in data:
            self._ui.set_theme(data["theme"])
        return r

    def get_icons(self, paths: list) -> dict:
        try:
            return {"ok": True, "icons": icons.icons_for(list(paths or [])[:200])}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc), "icons": {}}

    # ------------------------------------------------ 托盘 / 自启 / 悬浮胶囊
    def get_ui_settings(self) -> dict:
        return {
            "ok": True,
            "settings": _load_settings(),
            "autostart": autostart.is_enabled(),
            "widget_state": self._ui.widget_state() if self._ui else None,
        }

    def set_ui_option(self, key: str, value) -> dict:
        key = str(key or "")
        try:
            if key == "autostart":
                r = autostart.set_enabled(bool(value))
            elif key == "widget":
                r = self._ui.set_widget(bool(value)) if self._ui else _save_settings({"widget": bool(value)})
            elif key == "widget_edge_hide":
                if self._ui:
                    self._ui.set_edge_hide(bool(value))
                r = _save_settings({"widget_edge_hide": bool(value)})
            elif key == "close_to_tray":
                if self._ui:
                    self._ui.set_close_to_tray(bool(value))
                r = _save_settings({"close_to_tray": bool(value)})
            elif key == "theme":
                if self._ui:
                    self._ui.set_theme(str(value))
                r = _save_settings({"theme": str(value)})
            elif key == "widget_opacity":
                try:
                    pct = max(30, min(100, int(round(float(value)))))
                except Exception:  # noqa: BLE001
                    return {"ok": False, "msg": "透明度取值无效"}
                if self._ui:
                    self._ui.set_widget_opacity(pct)
                r = _save_settings({"widget_opacity": pct})
            elif key == "widget_accent":
                key_ = str(value or "blue")
                if key_ != "custom" and key_ not in WIDGET_ACCENTS:
                    return {"ok": False, "msg": f"未知配色：{key_}"}
                if self._ui:
                    self._ui.set_widget_accent(key_, _load_settings().get("widget_color"))
                r = _save_settings({"widget_accent": key_})
            elif key == "widget_color":
                raw = str(value or "").strip()
                rgb = _hex_to_rgb(raw)
                if not rgb:
                    return {"ok": False, "msg": "颜色格式应为 #RRGGBB"}
                hexv = "#%02x%02x%02x" % rgb
                if self._ui:
                    self._ui.set_widget_accent("custom", hexv)
                r = _save_settings({"widget_color": hexv, "widget_accent": "custom"})
            elif key == "widget_items":
                items = _sanitize_widget_items(value)
                if self._ui:
                    self._ui.set_widget_items(items)
                r = _save_settings({"widget_items": items})
            else:
                return {"ok": False, "msg": f"未知设置项：{key}"}
            return {"ok": r.get("ok", True), "msg": r.get("msg", ""),
                    "settings": _load_settings(), "autostart": autostart.is_enabled()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}

    def get_autostart(self) -> dict:
        return {"ok": True, "enabled": autostart.is_enabled()}

    def set_autostart(self, enabled: bool) -> dict:
        return autostart.set_enabled(bool(enabled))

    def minimize_to_tray(self) -> dict:
        if not self._ui:
            return {"ok": False, "msg": "界面未就绪"}
        self._ui.minimize_to_tray()
        return {"ok": True}

    def app_quit(self) -> dict:
        if not self._ui:
            return {"ok": False, "msg": "界面未就绪"}
        threading.Timer(0.25, self._ui.quit).start()
        return {"ok": True}

    def widget_state(self) -> dict:
        return self._ui.widget_state() if self._ui else {"enabled": False}

    def widget_set(self, enabled: bool) -> dict:
        return (self._ui.set_widget(bool(enabled)) if self._ui
                else {"ok": False, "msg": "界面未就绪"})

    def widget_toggle(self) -> dict:
        return (self._ui.toggle_widget() if self._ui
                else {"ok": False, "msg": "界面未就绪"})

    def widget_drag_start(self) -> dict:
        return (self._ui.widget_drag_start() if self._ui
                else {"ok": False, "msg": "界面未就绪"})

    def widget_resize(self, width) -> dict:
        """胶囊自己量出内容宽度后回传，后端按内容改窗口大小。

        不用 Python 里按公式估宽度：字体、DPI 缩放一变就对不上，
        界面里量 `max-content` 才是准的。
        """
        if not self._ui:
            return {"ok": False}
        try:
            w = int(round(float(width)))
        except Exception:  # noqa: BLE001
            return {"ok": False, "msg": "宽度无效"}
        return self._ui.resize_widget(w)

    def widget_data(self) -> dict:
        """悬浮胶囊的实时仪表数据（评分 30 秒缓存，避免频繁体检）。"""
        m = sysinfo.metrics()
        now = time.time()
        if now - _WIDGET_SCORE["time"] > 30:
            try:
                _WIDGET_SCORE["value"] = repair.system_health_summary().get("score")
            except Exception:
                pass
            _WIDGET_SCORE["time"] = now
        return {
            "cpu": m.get("cpu", 0),
            "ram": m.get("ram_percent", 0),
            "score": _WIDGET_SCORE["value"],
            "net": m.get("net_down", ""),
            "net_down": m.get("net_down", ""),
            "net_up": m.get("net_up", ""),
        }

    def widget_boost(self) -> dict:
        """悬浮胶囊的一键加速：立刻返回，后台线程执行，不阻塞胶囊界面。"""
        if not self._ui:
            return {"ok": False, "msg": "界面未就绪"}
        self._ui.quick_boost()
        return {"ok": True}

    def get_overview(self) -> dict:
        m = sysinfo.metrics()
        health = repair.system_health_summary()
        info = sysinfo.sysinfo(fast=True)
        return {
            "metrics": m,
            "health": health,
            "sys": {
                "os": info["os"], "cpu": info["cpu"], "ram": info["ram_total_h"],
                "uptime": info["uptime"], "boot": info["boot_time"],
                "disks": info["disks"], "admin": info["admin"],
                "activated": info["activated"], "vendor": info["vendor"],
                "gpu": info["gpu"], "board": info["board"],
                # 仪表盘副标题要用；之前没透传，前端只能显示「-- 核 -- 线程」
                "cpu_cores": info.get("cpu_cores"),
                "cpu_threads": info.get("cpu_threads"),
                "cpu_clock": info.get("cpu_clock"),
                "cpu_temp": info.get("cpu_temp"),
                "ram_sticks": info.get("ram_sticks"),
                "ram_type": info.get("ram_type"),
                "ram_speed": info.get("ram_speed"),
                "disk_count": info.get("disk_count"),
                "phys_disks": info.get("phys_disks") or [],
                # 关键：前端要靠它判断「这份是不是轻量数据」，决定要不要补刷。
                # 之前漏透传，s.partial 恒为 undefined，补刷逻辑从未生效。
                "partial": bool(info.get("partial")),
            },
            "top": sysinfo.top_processes(6),
            "admin": winapi.is_admin(),
            "backup": reg.backup_info(),
        }

    def get_metrics(self) -> dict:
        return sysinfo.metrics()

    def get_sysinfo(self) -> dict:
        return sysinfo.sysinfo(force=True)

    # ------------------------------------------------ 一键加速
    def boost(self, options: dict | None = None) -> dict:
        options = options or {}
        steps: list[dict] = []
        t0 = time.time()
        mem_before = sysinfo.metrics()

        jobs = []
        if options.get("clean", True):
            jobs.append(("clean", self._boost_clean))
        if options.get("memory", True):
            jobs.append(("memory", self._boost_memory))
        if options.get("power", True):
            jobs.append(("power", lambda: tweaks.set_power_plan("high")))
        if options.get("network", True):
            jobs.append(("network", network.flush_dns))
        if options.get("service", False):
            jobs.append(("service", services.disable_recommended))
        if options.get("tweak", False):
            jobs.append(("tweak", tweaks.apply_all))

        for name, fn in jobs:
            try:
                r = fn()
                steps.append({"step": name, "ok": bool(r.get("ok", True)),
                              "msg": r.get("msg") or r.get("freed_h") or ""})
            except Exception as exc:  # noqa: BLE001
                steps.append({"step": name, "ok": False, "msg": str(exc)})

        mem_after = sysinfo.metrics()
        freed_mem = max(0, mem_before["ram_used"] - mem_after["ram_used"])
        return {
            "ok": True,
            "elapsed": round(time.time() - t0, 1),
            "steps": steps,
            "mem_before": mem_before["ram_percent"],
            "mem_after": mem_after["ram_percent"],
            "mem_freed_h": winapi.human(freed_mem),
            "msg": f"加速完成，释放内存 {winapi.human(freed_mem)}，耗时 {round(time.time() - t0, 1)} 秒",
        }

    def _boost_clean(self) -> dict:
        ids = [t["id"] for t in cleaner.TARGETS
               if t["risk"] == "safe" and t["id"] not in ("dev_cache", "browser_cache")]
        r = cleaner.clean(ids)
        return {"ok": True, "freed": r["freed"],
                "msg": f"清理垃圾 {r['freed_h']}（{r['files']} 个文件）"}

    def _boost_memory(self) -> dict:
        before = sysinfo.metrics()["ram_used"]
        r = winapi.empty_working_set_all()
        purged = winapi.purge_standby_list()
        time.sleep(0.6)
        after = sysinfo.metrics()["ram_used"]
        freed = max(0, before - after)
        return {"ok": True,
                "msg": f"释放内存 {winapi.human(freed)}（{r['trimmed']} 个进程已整理{'，备用内存已清空' if purged else ''}）",
                "freed": freed, "trimmed": r["trimmed"]}

    def memory_boost(self) -> dict:
        return self._boost_memory()

    # ------------------------------------------------ 垃圾清理
    def clean_scan(self) -> dict:
        """同步扫描（保留给冒烟测试与内部调用）。"""
        return {"ok": True, "items": cleaner.scan()}

    def clean_scan_job(self) -> dict:
        """异步扫描：前端可显示逐项进度。"""
        def run(job):
            def cb(done: int, total: int, name: str) -> None:
                job.progress = int(done / max(1, total) * 100)
                if done < total:
                    job.push(f"扫描 {name}")
            items = cleaner.scan(progress=cb)
            total = sum(x["size"] for x in items)
            return {"ok": True, "items": items,
                    "msg": f"扫描完成，可清理 {winapi.human(total)}"}

        return {"ok": True, "job_id": _start_job("clean_scan", "扫描垃圾文件", run)}

    def clean_run(self, ids: list[str]) -> dict:
        """异步清理：返回 job_id，前端轮询 job_status 拿真实进度与释放量。"""
        def run(job):
            def cb(done: int, total: int, name: str) -> None:
                job.progress = int(done / max(1, total) * 100)
                if done < total:
                    job.push(f"清理 {name}")
            r = cleaner.clean(ids, progress=cb)
            if r["freed"] > 0:
                r["msg"] = f"已释放 {r['freed_h']}（{r['files']} 个文件）"
            else:
                r["msg"] = "清理完成，这部分垃圾已经空了"
            return r

        return {"ok": True, "job_id": _start_job("clean", "垃圾清理", run)}

    def clean_default(self) -> dict:
        return cleaner.clean_all_safe()

    # ------------------------------------------------ 启动项
    def startup_list(self) -> dict:
        return {"ok": True, "items": startup.list_items()}

    def startup_toggle(self, item_id: str, enabled: bool) -> dict:
        return startup.enable(item_id) if enabled else startup.disable(item_id)

    def startup_remove(self, item_id: str) -> dict:
        return startup.remove(item_id)

    # ------------------------------------------------ 进程
    def proc_list(self, sort: str = "mem") -> dict:
        return {"ok": True, "items": processes.list_processes(sort)}

    def proc_kill(self, pid: int) -> dict:
        return processes.kill(int(pid))

    def proc_kill_many(self, pids: list) -> dict:
        return processes.kill_many([int(p) for p in pids])

    def proc_priority(self, pid: int, level: str) -> dict:
        return processes.set_priority(int(pid), level)

    # ------------------------------------------------ 服务
    def svc_list(self, force: bool = False) -> dict:
        return {"ok": True, "items": services.list_services(force)}

    def svc_control(self, name: str, action: str) -> dict:
        return services.control(name, action)

    def svc_start_type(self, name: str, start_type: str) -> dict:
        return services.set_start_type(name, start_type)

    def svc_disable_recommended(self) -> dict:
        return services.disable_recommended()

    # ------------------------------------------------ 网络
    def net_flush(self) -> dict:
        return network.flush_dns()

    def net_winsock(self) -> dict:
        return network.reset_winsock()

    def net_tcpip(self) -> dict:
        return network.reset_tcpip()

    def net_optimize(self) -> dict:
        return network.optimize_tcp()

    def net_ping(self) -> dict:
        return {"ok": True, "items": network.ping_latency()}

    def net_adapters(self) -> dict:
        return {"ok": True, "items": network.adapters(),
                "dns": network.dns_servers(),
                "conns": network.connections_count()}

    def net_set_dns(self, primary: str, secondary: str = "") -> dict:
        return network.set_dns(primary, secondary)

    def net_reset_dns(self) -> dict:
        return network.reset_dns_dhcp()

    def net_hosts(self) -> dict:
        return network.hosts_info()

    def net_hosts_clean(self) -> dict:
        return network.clean_hosts()

    def net_hosts_restore(self) -> dict:
        return network.restore_hosts()

    # ------------------------------------------------ 系统调优
    def tweak_list(self) -> dict:
        return {"ok": True, "items": tweaks.tweak_list()}

    def tweak_apply(self, tid: str) -> dict:
        return tweaks.apply_tweak(tid)

    def tweak_all(self) -> dict:
        return tweaks.apply_all()

    def power_plans(self) -> dict:
        return {"ok": True, "items": tweaks.power_plans()}

    def power_set(self, kind: str) -> dict:
        return tweaks.set_power_plan(kind)

    def hibernate(self, enable: bool) -> dict:
        return tweaks.hibernate_on() if enable else tweaks.hibernate_off()

    def restore_registry(self) -> dict:
        return reg.restore_all()

    def restart_explorer(self) -> dict:
        return tweaks.restart_explorer()

    # ------------------------------------------------ 系统修复（异步）
    def job_status(self, job_id: str) -> dict:
        job = _jobs.get(job_id)
        if not job:
            return {"ok": False, "msg": "任务不存在"}
        return {"ok": True, "job": job.to_dict()}

    def job_cancel(self) -> dict:
        scanner.cancel()
        return {"ok": True, "msg": "已请求取消当前扫描"}

    def repair_start(self, kind: str) -> dict:
        def run(job: Job) -> dict:
            job.push("开始执行…")
            if kind == "sfc":
                return repair.sfc_scan(cb=job.push)
            if kind == "dism":
                return repair.dism_repair(cb=job.push)
            if kind == "chkdsk":
                return repair.chkdsk_scan("C:")
            if kind == "optimize":
                return repair.optimize_drive("C:", cb=job.push)
            if kind == "restore_point":
                return repair.create_restore_point()
            raise ValueError("未知修复类型")

        titles = {"sfc": "系统文件检查", "dism": "系统映像修复",
                  "chkdsk": "磁盘错误检查", "optimize": "磁盘优化",
                  "restore_point": "创建还原点"}
        return {"ok": True, "job_id": _start_job(kind, titles.get(kind, "系统任务"), run)}

    def repair_overview(self) -> dict:
        return {
            "ok": True,
            "disks": repair.disk_health(),
            "smart": repair.smart_predict(),
            "points": repair.restore_points(),
            "health": repair.system_health_summary(),
        }

    # ------------------------------------------------ 磁盘分析（异步）
    def scan_start(self, kind: str, params: dict | None = None) -> dict:
        params = params or {}

        def run(job: Job) -> dict:
            if kind == "big":
                min_mb = int(params.get("min_mb", 100))
                return scanner.big_files(min_mb, cb=lambda n, c, msg="": _progress(job, n, msg))
            if kind == "dup":
                min_kb = int(params.get("min_kb", 512))
                return scanner.dup_files(min_kb, cb=lambda n, c, msg="": _progress(job, n, msg))
            if kind == "empty":
                return scanner.empty_dirs()
            raise ValueError("未知扫描类型")

        titles = {"big": "大文件扫描", "dup": "重复文件扫描", "empty": "空目录扫描"}
        return {"ok": True, "job_id": _start_job(kind, titles.get(kind, "扫描"), run)}

    def delete_files(self, paths: list[str]) -> dict:
        return scanner.delete_files(paths)

    def open_path(self, path: str) -> dict:
        try:
            scanner.open_in_explorer(path)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}

    # ------------------------------------------------ 其他
    def open_url(self, url: str) -> dict:
        import webbrowser
        try:
            webbrowser.open(url)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}

    def export_report(self, directory: str | None = None) -> dict:
        """把体检结果导出为文本报告。

        默认写到桌面；directory 仅供内部/测试指定落点，
        避免自动化用例在用户桌面上堆一堆报告文件。
        """
        try:
            info = sysinfo.sysinfo()
            health = repair.system_health_summary()
            m = sysinfo.metrics()
            lines = [
                f"{APP_NAME} v{APP_VERSION} 系统体检报告",
                f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                "-" * 46,
                f"系统：{info['os']}",
                f"设备：{info['vendor']}",
                f"CPU：{info['cpu']}（{info['cpu_cores']} 核 {info['cpu_threads']} 线程）",
                f"内存：{info['ram_total_h']}  当前占用 {m['ram_percent']}%",
                f"显卡：{', '.join(info['gpu'])}",
                f"开机时间：{info['boot_time']}  已运行 {info['uptime']}",
                f"激活状态：{info['activated']}",
                "-" * 46,
                "磁盘：",
            ]
            for d in info["disks"]:
                lines.append(f"  {d['device']} {d['media']} 总 {d['total_h']} / 可用 {d['free_h']}（{d['percent']}% 已用）")
            lines.append("-" * 46)
            lines.append("体检结论：")
            for i in health["issues"]:
                lines.append(f"  [{i['level']}] {i['text']}")
            lines.append(f"健康评分：{health['score']} / 100")
            lines.append("-" * 46)
            lines.append(f"作者公众号：{AUTHOR['wechat']}    小红书：{AUTHOR['xhs']}")
            target_dir = directory or os.path.join(os.path.expanduser("~"), "Desktop")
            os.makedirs(target_dir, exist_ok=True)
            # 带秒：同一分钟内连点两次不会互相覆盖
            name = f"电脑体检报告_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            path = os.path.join(target_dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return {"ok": True,
                    "msg": (f"报告已保存到桌面：{name}" if not directory
                            else f"报告已生成：{name}"),
                    "path": path}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}


def _progress(job: Job, n: int, msg: str) -> None:
    job.message = msg or f"已扫描 {n} 个文件"
    # 用文件数估算进度，最多到 95%
    job.progress = min(95, 5 + n // 4000)
