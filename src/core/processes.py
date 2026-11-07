"""进程管理：列表、结束、优先级、CPU/内存占用。"""
from __future__ import annotations

import os
import time

import psutil

_cpu_prev: dict[int, tuple[float, float]] = {}

CRITICAL = {
    "system", "system idle process", "registry", "memory compression",
    "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "smss.exe", "explorer.exe", "dwm.exe", "fontdrvhost.exe", "sihost.exe",
    "ctfmon.exe", "spoolsv.exe", "audiodg.exe",
}

PRIORITY_MAP = {
    "idle": psutil.IDLE_PRIORITY_CLASS,
    "below": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "normal": psutil.NORMAL_PRIORITY_CLASS,
    "above": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    "high": psutil.HIGH_PRIORITY_CLASS,
    "realtime": psutil.REALTIME_PRIORITY_CLASS,
}


def list_processes(sort: str = "mem", limit: int = 300) -> list[dict]:
    """返回进程列表。CPU 占用通过两次采样差值计算，首次调用为 0。"""
    now = time.time()
    cores = psutil.cpu_count() or 1
    rows = []
    for p in psutil.process_iter(
            ["pid", "name", "exe", "memory_info", "status", "username",
             "create_time"], ad_value=None):
        try:
            i = p.info
            pid = i["pid"]
            mem = getattr(i.get("memory_info"), "rss", 0) or 0
            pct = 0.0
            try:
                ct = p.cpu_times()
                total = ct.user + ct.system
                prev = _cpu_prev.get(pid)
                if prev and now - prev[0] > 0.05:
                    pct = max(0.0, (total - prev[1]) / (now - prev[0]) * 100 / cores)
                _cpu_prev[pid] = (now, total)
            except Exception:
                pass
            rows.append({
                "pid": pid,
                "name": i.get("name") or "未知",
                "cpu": round(pct, 1),
                "mem": mem,
                "mem_h": f"{mem / 1048576:.1f} MB",
                "status": i.get("status") or "",
                "exe": i.get("exe") or "",
                "user": (i.get("username") or "").split("\\")[-1],
                "critical": (i.get("name") or "").lower() in CRITICAL,
            })
        except Exception:
            continue
    if len(_cpu_prev) > 4000:
        _cpu_prev.clear()
    if sort == "cpu":
        rows.sort(key=lambda r: (-r["cpu"], -r["mem"]))
    elif sort == "name":
        rows.sort(key=lambda r: r["name"].lower())
    elif sort == "pid":
        rows.sort(key=lambda r: r["pid"])
    else:
        rows.sort(key=lambda r: -r["mem"])
    return rows[:limit]


def kill(pid: int, force: bool = True) -> dict:
    try:
        p = psutil.Process(pid)
        name = p.name().lower()
        if name in CRITICAL:
            return {"ok": False, "msg": f"{name} 是系统关键进程，已阻止结束"}
        if pid == os.getpid():
            return {"ok": False, "msg": "不能结束后台服务自身"}
        if force:
            p.kill()
        else:
            p.terminate()
        return {"ok": True, "msg": f"已结束 {name} ({pid})"}
    except psutil.NoSuchProcess:
        return {"ok": False, "msg": "进程已不存在"}
    except psutil.AccessDenied:
        return {"ok": False, "msg": "权限不足，请以管理员身份运行"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc)}


def kill_many(pids: list[int]) -> dict:
    ok, fail = 0, 0
    msgs = []
    for pid in pids:
        r = kill(int(pid))
        if r["ok"]:
            ok += 1
        else:
            fail += 1
            msgs.append(r["msg"])
    return {"ok": True, "killed": ok, "failed": fail,
            "msg": f"已结束 {ok} 个进程，失败 {fail} 个", "details": msgs[:5]}


def set_priority(pid: int, level: str) -> dict:
    try:
        p = psutil.Process(pid)
        p.nice(PRIORITY_MAP.get(level, psutil.NORMAL_PRIORITY_CLASS))
        return {"ok": True, "msg": f"已将 {p.name()} 优先级设为 {level}"}
    except psutil.AccessDenied:
        return {"ok": False, "msg": "权限不足"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc)}


def heavy_consumers(threshold_mb: int = 500) -> list[dict]:
    """找出内存占用过高的进程，用于一键加速提示。"""
    out = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            mem = p.info["memory_info"].rss
            if mem >= threshold_mb * 1048576:
                out.append({"pid": p.info["pid"], "name": p.info["name"],
                            "mem": mem, "mem_h": f"{mem / 1048576:.0f} MB"})
        except Exception:
            continue
    return out
