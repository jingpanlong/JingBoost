"""Windows 服务管理：查看、启停、设置启动类型。"""
from __future__ import annotations

import time

import psutil

from . import winapi

_cache: dict = {"time": 0, "data": None}

# 常见可安全禁用的非必要服务（仅作推荐标记，不自动操作）
RECOMMEND_OFF = {
    "diagtrack", "dmwappushservice", "retaildemo", "mapsbroker",
    "wsearch", "sysmain", "fax", "remoteaccess", "termservice",
    "xblauthmanager", "xblgamesave", "xboxgipsvc", "xboxnetapisvc",
}


def list_services(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _cache["data"] and now - _cache["time"] < 30:
        return _cache["data"]
    rows = []
    try:
        for s in psutil.win_service_iter():
            try:
                info = s.as_dict()
            except Exception:
                continue
            name = (info.get("name") or "").lower()
            rows.append({
                "name": info.get("name"),
                "display": info.get("display_name") or info.get("name"),
                "status": info.get("status"),
                "start_type": info.get("start_type"),
                "pid": info.get("pid") or 0,
                "desc": (info.get("description") or "")[:180],
                "recommend_off": name in RECOMMEND_OFF,
            })
    except Exception:
        pass
    rows.sort(key=lambda r: (r["status"] != "running", (r["display"] or "").lower()))
    _cache["data"] = rows
    _cache["time"] = now
    return rows


def control(name: str, action: str) -> dict:
    try:
        s = psutil.win_service_get(name)
        if action == "start":
            s.start()
        elif action == "stop":
            s.stop()
        elif action == "restart":
            try:
                s.stop()
            except Exception:
                pass
            time.sleep(1.2)
            s.start()
        else:
            return {"ok": False, "msg": "未知操作"}
        _cache["time"] = 0
        return {"ok": True, "msg": f"服务 {name} 已执行 {action}"}
    except psutil.AccessDenied:
        return {"ok": False, "msg": "权限不足，请以管理员身份运行"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": f"{type(exc).__name__}: {exc}"}


START_TYPES = {"boot": "boot", "system": "system", "automatic": "auto",
               "manual": "demand", "disabled": "disabled"}


def set_start_type(name: str, start_type: str) -> dict:
    arg = START_TYPES.get(start_type, "demand")
    rc, out = winapi.run_hidden(["sc", "config", name, "start=", arg], timeout=25)
    _cache["time"] = 0
    if rc == 0:
        return {"ok": True, "msg": f"{name} 启动类型已设为 {start_type}"}
    return {"ok": False, "msg": out or "设置失败，可能需要管理员权限"}


def disable_recommended() -> dict:
    """禁用推荐关闭的非必要服务，返回结果。"""
    done, failed = [], []
    for s in list_services():
        if s["recommend_off"] and s["status"] == "running":
            if s["name"].lower() in ("wsearch", "sysmain"):
                r = control(s["name"], "stop")
                if r["ok"]:
                    done.append(s["display"])
                else:
                    failed.append(s["display"])
                continue
            r1 = control(s["name"], "stop")
            r2 = set_start_type(s["name"], "manual")
            if r1["ok"] or r2["ok"]:
                done.append(s["display"])
            else:
                failed.append(s["display"])
    return {"ok": True, "done": len(done), "failed": len(failed),
            "msg": f"已优化 {len(done)} 个非必要服务", "details": done}
