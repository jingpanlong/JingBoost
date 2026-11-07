"""启动项管理：注册表 Run 键 + 启动文件夹，支持启用/禁用/删除。"""
from __future__ import annotations

import json
import os
import shutil

from . import reg

RUN_KEYS = [
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
]

STARTUP_DIRS = [
    (os.path.join(os.environ.get("APPDATA", ""),
                  r"Microsoft\Windows\Start Menu\Programs\Startup"), "当前用户"),
    (os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                  r"Microsoft\Windows\Start Menu\Programs\Startup"), "所有用户"),
]

DISABLED_ROOT = r"SOFTWARE\JingBoost\DisabledStartup"
DISABLED_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "JingBoost", "disabled_startup")


def exe_from_cmd(cmd: str) -> str:
    """从启动命令行里解析出可执行文件路径，用于取图标。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        path = cmd[1:end] if end > 0 else cmd.strip('"')
    else:
        low = cmd.lower()
        idx = low.find(".exe")
        path = cmd[:idx + 4] if idx > 0 else cmd.split(" ")[0]
    path = os.path.expandvars(path.strip())
    if os.path.exists(path):
        return path
    # 只有文件名的情况下尝试在 System32 里找
    base = os.path.basename(path)
    if base and not os.path.dirname(path):
        for root in (os.environ.get("SystemRoot", r"C:\Windows") + r"\System32",
                     os.environ.get("SystemRoot", r"C:\Windows")):
            cand = os.path.join(root, base)
            if os.path.exists(cand):
                return cand
    return path if path.lower().endswith((".exe", ".lnk", ".bat", ".cmd")) else ""


def _is_approved_off(hive: str, name: str) -> bool:
    """读取 StartupApproved，判断系统层面是否已禁用。"""
    path = ("HKCU" if hive == "HKCU" else "HKLM") + \
        r"\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    exists, val, _ = reg.get_value(path, name)
    if not exists or not isinstance(val, (bytes, bytearray)) or len(val) < 1:
        return False
    return val[0] in (2, 3, 6)


def list_items() -> list[dict]:
    items: list[dict] = []
    for hive, sub in RUN_KEYS:
        for v in reg.list_values(f"{hive}\\{sub}"):
            name = v["name"]
            cmd = v["value"] if isinstance(v["value"], str) else str(v["value"])
            items.append({
                "id": f"reg|{hive}|{sub}|{name}",
                "name": name, "cmd": cmd, "source": f"{hive} Run",
                "type": "registry", "enabled": not _is_approved_off(hive, name),
                "hive": hive, "key": sub, "exe": exe_from_cmd(cmd),
            })
    for path, label in STARTUP_DIRS:
        if not os.path.isdir(path):
            continue
        for f in os.listdir(path):
            full = os.path.join(path, f)
            if f.lower() == "desktop.ini":
                continue
            items.append({
                "id": f"file|{full}",
                "name": os.path.splitext(f)[0], "cmd": full,
                "source": f"启动文件夹（{label}）", "type": "file",
                "enabled": True, "path": full, "exe": full,
            })
    # 已禁用的项
    for v in reg.list_values(f"HKCU\\{DISABLED_ROOT}"):
        try:
            meta = json.loads(v["value"])
        except Exception:
            continue
        meta["id"] = f"disabled|{meta['hive']}|{meta['key']}|{meta['name']}"
        meta["enabled"] = False
        meta["from"] = "registry"
        meta["exe"] = exe_from_cmd(meta.get("cmd", ""))
        items.append(meta)
    if os.path.isdir(DISABLED_DIR):
        for f in os.listdir(DISABLED_DIR):
            items.append({
                "id": f"diskfile|{f}", "name": os.path.splitext(f)[0],
                "cmd": os.path.join(DISABLED_DIR, f),
                "source": "启动文件夹（已禁用）", "type": "file",
                "enabled": False, "path": os.path.join(DISABLED_DIR, f),
                "exe": os.path.join(DISABLED_DIR, f),
            })
    items.sort(key=lambda x: (not x["enabled"], x["name"].lower()))
    return items


def disable(item_id: str) -> dict:
    if item_id.startswith("reg|"):
        _, hive, sub, name = item_id.split("|", 3)
        exists, val, vtype = reg.get_value(f"{hive}\\{sub}", name)
        if not exists:
            return {"ok": False, "msg": "启动项不存在"}
        meta = {"name": name, "cmd": val, "hive": hive, "key": sub,
                "vtype": vtype,
                "source": f"{hive} Run（已禁用）", "type": "registry"}
        store_id = f"disabled|{hive}|{sub}|{name}"
        if not reg.set_value(f"HKCU\\{DISABLED_ROOT}", store_id,
                             json.dumps(meta, ensure_ascii=False), reg.TYPE_STR,
                             backup=False):
            return {"ok": False, "msg": "写入备份记录失败"}
        if not reg.delete_value(f"{hive}\\{sub}", name):
            # 备份写了但原值没删掉（多半是 HKLM 需要管理员权限），
            # 旧代码这里照样返回成功，用户重启后发现项还在
            reg.delete_value(f"HKCU\\{DISABLED_ROOT}", store_id, backup=False)
            return {"ok": False, "msg": "删除启动项失败，请以管理员身份运行"}
        return {"ok": True}
    if item_id.startswith("file|"):
        path = item_id.split("|", 1)[1]
        if not os.path.exists(path):
            return {"ok": False, "msg": "文件不存在"}
        os.makedirs(DISABLED_DIR, exist_ok=True)
        dst = os.path.join(DISABLED_DIR, os.path.basename(path))
        try:
            shutil.move(path, dst)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}
    return {"ok": False, "msg": "该启动项无需禁用"}


def enable(item_id: str) -> dict:
    if item_id.startswith("disabled|"):
        _, hive, sub, name = item_id.split("|", 3)
        exists, raw, _ = reg.get_value(f"HKCU\\{DISABLED_ROOT}", item_id)
        if not exists:
            return {"ok": False, "msg": "记录已丢失"}
        # 备份里存的是 json 元数据，真正要写回 Run 的是 meta["cmd"]。
        # 旧实现直接把整段 json 当成命令行写回，Run 键里就变成一坨 JSON。
        try:
            meta = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            meta = {}
        cmd = meta.get("cmd") or ""
        if not cmd:
            return {"ok": False, "msg": "备份记录已损坏，无法恢复"}
        vtype = meta.get("vtype")
        if not isinstance(vtype, int) or vtype not in (
                reg.TYPE_STR, reg.TYPE_EXPAND, reg.TYPE_BIN):
            vtype = reg.TYPE_EXPAND if "%" in str(cmd) else reg.TYPE_STR
        if not reg.set_value(f"{hive}\\{sub}", name, cmd, vtype, backup=False):
            return {"ok": False, "msg": "恢复失败，请以管理员身份运行"}
        reg.delete_value(f"HKCU\\{DISABLED_ROOT}", item_id, backup=False)
        return {"ok": True}
    if item_id.startswith("diskfile|"):
        fname = item_id.split("|", 1)[1]
        src = os.path.join(DISABLED_DIR, fname)
        target_dir = STARTUP_DIRS[0][0]
        os.makedirs(target_dir, exist_ok=True)
        try:
            shutil.move(src, os.path.join(target_dir, fname))
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}
    return {"ok": False, "msg": "该启动项已启用"}


def remove(item_id: str) -> dict:
    if item_id.startswith("reg|"):
        _, hive, sub, name = item_id.split("|", 3)
        ok = reg.delete_value(f"{hive}\\{sub}", name)
        return {"ok": ok}
    if item_id.startswith(("file|", "diskfile|")):
        path = item_id.split("|", 1)[1]
        if not os.path.isabs(path):
            path = os.path.join(DISABLED_DIR, path)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.exists(path):
                os.remove(path)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "msg": str(exc)}
    if item_id.startswith("disabled|"):
        reg.delete_value(f"HKCU\\{DISABLED_ROOT}", item_id, backup=False)
        return {"ok": True}
    return {"ok": False, "msg": "不支持的启动项"}


def open_manager() -> None:
    os.startfile("taskmgr.exe")
