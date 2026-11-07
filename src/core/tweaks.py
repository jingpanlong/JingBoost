"""系统调优：电源、性能、隐私、游戏模式等，全部可一键还原。"""
from __future__ import annotations

import os
import time

from . import native, reg, services, winapi

K = reg


def _power_active() -> str:
    rc, out = winapi.run_hidden(["powercfg", "/getactivescheme"], timeout=20)
    if rc == 0 and "GUID" in out:
        try:
            return out.split("GUID:")[1].split("(")[0].strip()
        except Exception:
            pass
    return ""


def power_plans() -> list[dict]:
    rc, out = winapi.run_hidden(["powercfg", "/list"], timeout=20)
    plans = []
    active = _power_active()
    for line in out.splitlines():
        if "GUID" not in line:
            continue
        try:
            guid = line.split("GUID:")[1].split()[0].strip()
            # 活动方案行末尾带 "*"，旧写法只 rstrip(")") 会留下「高性能) *」
            rest = line.split("GUID:")[1]
            name = (rest.split("(", 1)[1].split(")")[0].strip()
                    if "(" in rest else rest.strip())
            plans.append({"guid": guid, "name": name or guid, "active": guid == active})
        except Exception:
            continue
    return plans


def set_power_plan(kind: str) -> dict:
    mapping = {"high": "SCHEME_MIN", "balanced": "SCHEME_BALANCED",
               "saver": "SCHEME_MAX", "ultimate": "e9a42b02-d5df-448d-aa00-03f14749eb61"}
    target = mapping.get(kind, "SCHEME_BALANCED")
    if kind == "ultimate":
        winapi.run_hidden(["powercfg", "-duplicatescheme", target], timeout=25)
    rc, out = winapi.run_hidden(["powercfg", "/setactive", target], timeout=20)
    label = {"high": "高性能", "balanced": "平衡", "saver": "节能",
             "ultimate": "卓越性能"}.get(kind, kind)
    if rc != 0 and kind == "ultimate":
        rc, out = winapi.run_hidden(["powercfg", "/setactive", "SCHEME_MIN"], timeout=20)
        label = "高性能（卓越性能不可用，已回退）"
    return {"ok": rc == 0, "msg": f"电源计划已切换为 {label}" if rc == 0 else out}


def hibernate_off() -> dict:
    rc, out = winapi.run_hidden(["powercfg", "/h", "off"], timeout=25)
    return {"ok": rc == 0, "msg": "已关闭休眠，释放约等于内存大小的磁盘空间" if rc == 0 else out}


def hibernate_on() -> dict:
    rc, out = winapi.run_hidden(["powercfg", "/h", "on"], timeout=25)
    return {"ok": rc == 0, "msg": "已开启休眠" if rc == 0 else out}


# ---------------------------------------------------------------- 注册表调优项

VISUAL_EFFECTS = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"


def _apply_visual_perf() -> dict:
    K.set_value(VISUAL_EFFECTS, "VisualFXSetting", 2, K.TYPE_INT)
    K.set_value(r"HKCU\Control Panel\Desktop", "MenuShowDelay", "0", K.TYPE_STR)
    K.set_value(r"HKCU\Control Panel\Desktop\WindowMetrics", "MinAnimate", "0", K.TYPE_STR)
    K.set_value(r"HKCU\Control Panel\Desktop", "DragFullWindows", "1", K.TYPE_STR)
    K.set_value(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "TaskbarAnimations", 0, K.TYPE_INT)
    K.set_value(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "ListviewAlphaSelect", 0, K.TYPE_INT)
    K.set_value(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
                "ListviewShadow", 0, K.TYPE_INT)
    return {"ok": True, "msg": "已切换到性能优先的视觉效果，重启资源管理器后完全生效"}


def _visual_status() -> bool:
    ok, val, _ = K.get_value(VISUAL_EFFECTS, "VisualFXSetting")
    return ok and val == 2


def _apply_privacy() -> dict:
    items = [
        (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 0, K.TYPE_INT),
        (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", 0, K.TYPE_INT),
        (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System", "EnableActivityFeed", 0, K.TYPE_INT),
        (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System", "PublishUserActivities", 0, K.TYPE_INT),
        (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\System", "UploadUserActivities", 0, K.TYPE_INT),
        (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", 0, K.TYPE_INT),
        (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search", "AllowCortana", 0, K.TYPE_INT),
        (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
         "SubscribedContent-338388Enabled", 0, K.TYPE_INT),
        (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
         "SubscribedContent-338389Enabled", 0, K.TYPE_INT),
        (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
         "SystemPaneSuggestionsEnabled", 0, K.TYPE_INT),
        (r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
         "SoftLandingEnabled", 0, K.TYPE_INT),
        (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\CloudContent", "DisableWindowsConsumerFeatures", 1, K.TYPE_INT),
    ]
    for path, name, val, t in items:
        K.set_value(path, name, val, t)
    return {"ok": True, "msg": "已关闭遥测、广告 ID、活动历史与推荐内容"}


def _privacy_status() -> bool:
    ok, val, _ = K.get_value(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry")
    return ok and val == 0


def _apply_game() -> dict:
    K.set_value(r"HKCU\System\GameConfigStore", "GameDVR_Enabled", 0, K.TYPE_INT)
    K.set_value(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR", 0, K.TYPE_INT)
    K.set_value(r"HKCU\Software\Microsoft\GameBar", "AllowAutoGameMode", 1, K.TYPE_INT)
    K.set_value(r"HKCU\Software\Microsoft\GameBar", "AutoGameModeEnabled", 1, K.TYPE_INT)
    K.set_value(r"HKCU\System\GameConfigStore", "GameDVR_FSEBehaviorMode", 2, K.TYPE_INT)
    K.set_value(r"HKCU\System\GameConfigStore", "GameDVR_HonorUserFSEBehaviorMode", 1, K.TYPE_INT)
    return {"ok": True, "msg": "已关闭后台录制，开启游戏模式与独占全屏优化"}


def _game_status() -> bool:
    ok, val, _ = K.get_value(r"HKCU\System\GameConfigStore", "GameDVR_Enabled")
    return ok and val == 0


def _apply_startup_delay() -> dict:
    K.set_value(r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Serialize",
                "StartupDelayInMSec", 0, K.TYPE_INT)
    return {"ok": True, "msg": "已取消开机启动项延迟加载"}


def _startup_delay_status() -> bool:
    ok, val, _ = K.get_value(
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Serialize",
        "StartupDelayInMSec")
    return ok and val == 0


def _apply_long_path() -> dict:
    K.set_value(r"HKLM\SYSTEM\CurrentControlSet\Control\FileSystem",
                "LongPathsEnabled", 1, K.TYPE_INT)
    return {"ok": True, "msg": "已启用 260 字符以上的长路径支持"}


def _long_path_status() -> bool:
    ok, val, _ = K.get_value(r"HKLM\SYSTEM\CurrentControlSet\Control\FileSystem",
                            "LongPathsEnabled")
    return ok and val == 1


def _restart_shell() -> None:
    """重启资源管理器，并确认任务栏真的回来了。

    旧实现只 taskkill 不重启（依赖 Windows 自动拉起），个别系统上 explorer
    不会自动回来，用户会看到任务栏和桌面全没了。
    """
    winapi.run_hidden(["taskkill", "/f", "/im", "explorer.exe"], timeout=20)
    for _ in range(20):                       # 最多等 4 秒自动重启
        time.sleep(0.2)
        if native.shell_present():
            return
    winapi.run_hidden(["cmd", "/c", "start", "", "explorer.exe"], timeout=15)


def _apply_old_menu() -> dict:
    path = r"HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
    try:
        hive, sub = reg.parse_path(path)
        import winreg
        with winreg.CreateKeyEx(hive, sub, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc)}
    _restart_shell()
    return {"ok": True, "msg": "已恢复 Win10 经典右键菜单（资源管理器已重启）"}


def _old_menu_status() -> bool:
    ok, val, _ = K.get_value(
        r"HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32", "")
    return ok


def _apply_index_off() -> dict:
    r = services.control("WSearch", "stop")
    r2 = services.set_start_type("WSearch", "manual")
    if r["ok"] or r2["ok"]:
        return {"ok": True, "msg": "已停止 Windows 搜索索引服务（文件搜索会变慢，可在服务页恢复）"}
    return {"ok": False, "msg": "操作失败，可能需要管理员权限"}


def _index_status() -> bool:
    for s in services.list_services():
        if s["name"].lower() == "wsearch":
            return s["status"] != "running"
    return False


def _apply_sysmain() -> dict:
    r = services.control("SysMain", "stop")
    r2 = services.set_start_type("SysMain", "manual")
    if r["ok"] or r2["ok"]:
        return {"ok": True, "msg": "已停止 SysMain 预读取服务（机械硬盘建议保留）"}
    return {"ok": False, "msg": "操作失败，可能需要管理员权限"}


def _sysmain_status() -> bool:
    for s in services.list_services():
        if s["name"].lower() == "sysmain":
            return s["status"] != "running"
    return False


TWEAKS = [
    {"id": "visual_perf", "name": "性能优先视觉特效", "group": "性能",
     "desc": "关闭窗口动画、任务栏动画、菜单渐隐，界面响应更快",
     "action": _apply_visual_perf, "check": _visual_status},
    {"id": "startup_delay", "name": "取消开机启动延迟", "group": "性能",
     "desc": "开机后立即加载启动项，节省 10 秒左右等待",
     "action": _apply_startup_delay, "check": _startup_delay_status},
    {"id": "game_mode", "name": "游戏模式与关闭后台录制", "group": "游戏",
     "desc": "关闭 GameDVR 录制、开启游戏模式与独占全屏，减少掉帧",
     "action": _apply_game, "check": _game_status},
    {"id": "privacy", "name": "关闭遥测与广告推送", "group": "隐私",
     "desc": "关闭系统遥测、广告 ID、活动历史、推荐内容与 Bing 搜索建议",
     "action": _apply_privacy, "check": _privacy_status},
    {"id": "long_path", "name": "启用超长路径支持", "group": "系统",
     "desc": "解除 260 字符路径限制，解决部分软件安装/解压失败",
     "action": _apply_long_path, "check": _long_path_status},
    {"id": "index_off", "name": "关闭搜索索引服务", "group": "服务",
     "desc": "索引服务长期占用磁盘与 CPU，关掉后文件管理器搜索会变慢",
     "action": _apply_index_off, "check": _index_status},
    {"id": "sysmain_off", "name": "关闭 SysMain 预读服务", "group": "服务",
     "desc": "SSD 用户关掉可减少后台磁盘读写；机械硬盘不建议关闭",
     "action": _apply_sysmain, "check": _sysmain_status},
    {"id": "old_menu", "name": "恢复经典右键菜单", "group": "系统",
     "desc": "Win11 恢复 Win10 的完整右键菜单，少点一次“显示更多选项”",
     "action": _apply_old_menu, "check": _old_menu_status},
]

_BY_ID = {t["id"]: t for t in TWEAKS}


def tweak_list() -> list[dict]:
    out = []
    for t in TWEAKS:
        try:
            applied = bool(t["check"]())
        except Exception:
            applied = False
        out.append({"id": t["id"], "name": t["name"], "desc": t["desc"],
                    "group": t["group"], "applied": applied})
    return out


def apply_tweak(tid: str) -> dict:
    t = _BY_ID.get(tid)
    if not t:
        return {"ok": False, "msg": "未知调优项"}
    try:
        r = t["action"]()
        r["id"] = tid
        return r
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": f"{type(exc).__name__}: {exc}", "id": tid}


def apply_all() -> dict:
    results = []
    ok = 0
    for t in TWEAKS:
        r = apply_tweak(t["id"])
        results.append({"name": t["name"], "ok": r.get("ok"), "msg": r.get("msg", "")})
        if r.get("ok"):
            ok += 1
    return {"ok": True, "count": ok, "total": len(TWEAKS), "details": results,
            "msg": f"已完成 {ok}/{len(TWEAKS)} 项系统调优"}


def restart_explorer() -> dict:
    _restart_shell()
    return {"ok": True, "msg": "资源管理器已重启"}
