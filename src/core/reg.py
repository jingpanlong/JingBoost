"""注册表读写与改动备份（纯 winreg 实现，便于一键还原）。"""
from __future__ import annotations

import json
import os
import time
import winreg

APP_NAME = "JingBoost"
BACKUP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                          APP_NAME, "backups")

HIVES = {
    "HKCU": winreg.HKEY_CURRENT_USER,
    "HKLM": winreg.HKEY_LOCAL_MACHINE,
}

TYPE_INT = winreg.REG_DWORD
TYPE_STR = winreg.REG_SZ
TYPE_EXPAND = winreg.REG_EXPAND_SZ
TYPE_BIN = winreg.REG_BINARY


def parse_path(full: str) -> tuple[int, str]:
    hive, _, sub = full.partition("\\")
    return HIVES[hive.upper()], sub


def get_value(full: str, name: str):
    """返回 (存在, 值, 类型)。不存在返回 (False, None, None)。"""
    try:
        hive, sub = parse_path(full)
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ) as k:
            v, t = winreg.QueryValueEx(k, name)
            return True, v, t
    except OSError:
        return False, None, None


def set_value(full: str, name: str, value, vtype=TYPE_INT, backup: bool = True) -> bool:
    try:
        hive, sub = parse_path(full)
        if backup:
            exists, old, old_type = get_value(full, name)
            remember(full, name, exists, old, old_type)
        with winreg.CreateKeyEx(hive, sub, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, vtype, value)
        return True
    except OSError:
        return False


def delete_value(full: str, name: str, backup: bool = True) -> bool:
    try:
        hive, sub = parse_path(full)
        if backup:
            exists, old, old_type = get_value(full, name)
            remember(full, name, exists, old, old_type)
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
        return True
    except OSError:
        return False


def delete_tree(full: str) -> bool:
    """递归删除键树。"""
    hive, sub = parse_path(full)
    try:
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ) as k:
            children = []
            i = 0
            while True:
                try:
                    children.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
        for c in children:
            delete_tree(full + "\\" + c)
        winreg.DeleteKey(hive, sub)
        return True
    except OSError:
        return False


def list_values(full: str) -> list[dict]:
    out = []
    try:
        hive, sub = parse_path(full)
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    name, val, vtype = winreg.EnumValue(k, i)
                    out.append({"name": name, "value": val, "type": vtype, "key": full})
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def list_subkeys(full: str) -> list[str]:
    out = []
    try:
        hive, sub = parse_path(full)
        with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ 备份机制

def _jsonable(v):
    if isinstance(v, (bytes, bytearray)):
        return {"__b64__": bytes(v).hex()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def _unjsonable(v):
    if isinstance(v, dict) and "__b64__" in v:
        return bytes.fromhex(v["__b64__"])
    if isinstance(v, list):
        return [_unjsonable(x) for x in v]
    return v


def remember(key: str, name: str, existed: bool, value, vtype) -> None:
    """记录一次改动前的原始状态，供还原使用。"""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        path = os.path.join(BACKUP_DIR, "registry.json")
        data = {"entries": []}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"entries": []}
        entries = data.setdefault("entries", [])
        # 同一键值只记录第一次（原始状态）
        for e in entries:
            if e["key"].lower() == key.lower() and e["name"].lower() == name.lower():
                return
        entries.append({
            "key": key, "name": name, "existed": existed,
            "value": _jsonable(value), "type": vtype,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def backup_info() -> dict:
    path = os.path.join(BACKUP_DIR, "registry.json")
    if not os.path.exists(path):
        return {"count": 0, "time": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("entries", [])
        return {"count": len(entries), "time": entries[-1]["time"] if entries else None}
    except Exception:
        return {"count": 0, "time": None}


def restore_all() -> dict:
    """把记录过的注册表改动全部还原。"""
    path = os.path.join(BACKUP_DIR, "registry.json")
    if not os.path.exists(path):
        return {"restored": 0, "failed": 0, "msg": "没有可还原的记录"}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", [])
    ok = 0
    fail = 0
    for e in reversed(entries):
        try:
            hive, sub = parse_path(e["key"])
            if e["existed"]:
                val = _unjsonable(e["value"])
                with winreg.CreateKeyEx(hive, sub, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, e["name"], 0, e["type"], val)
                ok += 1
            else:
                try:
                    with winreg.OpenKey(hive, sub, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.DeleteValue(k, e["name"])
                    ok += 1
                except OSError:
                    ok += 1
        except Exception:
            fail += 1
    try:
        os.remove(path)
    except Exception:
        pass
    return {"restored": ok, "failed": fail}
