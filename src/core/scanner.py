"""磁盘空间分析：大文件、重复文件、空目录。"""
from __future__ import annotations

import hashlib
import os
import threading

import psutil

from . import winapi

SKIP = {
    "windows", "$recycle.bin", "system volume information", "$windows.~bt",
    "$windows.~ws", "config.msi", "recovery", "perflogs", "windows.old",
    "appdata", "programdata", "node_modules", ".git", ".svn", "__pycache__",
}

_cancel = threading.Event()


def cancel() -> None:
    _cancel.set()


def reset() -> None:
    _cancel.clear()


def _roots() -> list[str]:
    out = []
    for p in psutil.disk_partitions(all=False):
        if "cdrom" in p.opts or not p.fstype:
            continue
        out.append(p.mountpoint)
    return out


def _walk(root: str, cb=None, max_files: int = 800000,
          min_size: int = 0) -> list[tuple[str, int]]:
    """返回 [(path, size)]，只收体积不小于 min_size 的文件。"""
    files: list[tuple[str, int]] = []
    stack = [root]
    n = 0
    while stack:
        if _cancel.is_set():
            break
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name.lower() in SKIP:
                                continue
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            n += 1
                            if st.st_size >= min_size:
                                files.append((e.path, st.st_size))
                            if n % 2000 == 0 and cb:
                                cb(n, len(files))
                            if n >= max_files:
                                return files
                    except OSError:
                        continue
        except OSError:
            continue
    return files


def big_files(min_mb: int = 100, limit: int = 120, cb=None) -> dict:
    reset()
    threshold = min_mb * 1024 * 1024
    found: list[tuple[str, int]] = []
    scanned = 0
    for root in _roots():
        if _cancel.is_set():
            break
        label = root
        if cb:
            cb(scanned, 0, f"正在扫描 {label}")
        files = _walk(root, cb=lambda n, t: cb and cb(scanned + n, len(found), f"正在扫描 {label}"),
                      min_size=threshold)
        scanned += len(files)
        for path, size in files:
            if size >= threshold:
                found.append((path, size))
        del files
    found.sort(key=lambda x: -x[1])
    rows = []
    for path, size in found[:limit]:
        try:
            import datetime
            mt = os.path.getmtime(path)
            date = datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d")
        except Exception:
            date = "-"
        rows.append({"path": path, "size": size, "size_h": winapi.human(size),
                     "name": os.path.basename(path),
                     "dir": os.path.dirname(path), "date": date})
    total = sum(s for _, s in found)
    return {"ok": True, "items": rows, "count": len(found),
            "total": total, "total_h": winapi.human(total),
            "scanned": scanned, "cancelled": _cancel.is_set()}


def _quick_hash(path: str, chunk: int = 65536) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            h.update(f.read(chunk))
    except OSError:
        return ""
    return h.hexdigest()


def _full_hash(path: str, block: int = 1048576) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                if _cancel.is_set():
                    return ""
                b = f.read(block)
                if not b:
                    break
                h.update(b)
    except OSError:
        return ""
    return h.hexdigest()


def dup_files(min_kb: int = 512, limit_groups: int = 60, cb=None) -> dict:
    reset()
    threshold = min_kb * 1024
    by_size: dict[int, list[str]] = {}
    scanned = 0
    for root in _roots():
        if _cancel.is_set():
            break
        files = _walk(root, cb=lambda n, t: cb and cb(scanned + n, len(by_size), "正在扫描磁盘"),
                      min_size=threshold)
        scanned += len(files)
        for path, size in files:
            by_size.setdefault(size, []).append(path)
        del files

    candidates = {s: p for s, p in by_size.items() if len(p) > 1}
    groups = []
    total_waste = 0
    idx = 0
    for size, paths in candidates.items():
        if _cancel.is_set():
            break
        idx += 1
        if cb:
            cb(scanned, idx, f"正在比对第 {idx}/{len(candidates)} 组相同大小文件")
        # 先按头部哈希分组
        by_head: dict[str, list[str]] = {}
        for p in paths:
            by_head.setdefault(_quick_hash(p), []).append(p)
        for head, group in by_head.items():
            if len(group) < 2:
                continue
            by_full: dict[str, list[str]] = {}
            for p in group:
                by_full.setdefault(_full_hash(p), []).append(p)
            for fh, same in by_full.items():
                if len(same) < 2:
                    continue
                waste = size * (len(same) - 1)
                total_waste += waste
                groups.append({
                    "hash": fh[:10], "size": size, "size_h": winapi.human(size),
                    "count": len(same), "waste": waste,
                    "waste_h": winapi.human(waste),
                    "files": [{"path": p, "name": os.path.basename(p)} for p in same],
                })
    groups.sort(key=lambda g: -g["waste"])
    return {"ok": True, "groups": groups[:limit_groups], "total_groups": len(groups),
            "waste": total_waste, "waste_h": winapi.human(total_waste),
            "scanned": scanned, "cancelled": _cancel.is_set()}


def delete_files(paths: list[str]) -> dict:
    ok, fail, size = 0, 0, 0
    for p in paths:
        try:
            sz = os.path.getsize(p)
            os.remove(p)
            ok += 1
            size += sz
        except Exception:
            fail += 1
    return {"ok": True, "deleted": ok, "failed": fail,
            "freed_h": winapi.human(size),
            "msg": f"已删除 {ok} 个文件，释放 {winapi.human(size)}"}


def empty_dirs(roots: list[str] | None = None, limit: int = 200) -> dict:
    roots = roots or _roots()
    empties: list[str] = []
    for root in roots:
        stack = [root]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    subs = []
                    has_file = False
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            if e.name.lower() in SKIP:
                                continue
                            subs.append(e.path)
                        else:
                            has_file = True
                    if not subs and not has_file and cur.lower() != root.lower():
                        empties.append(cur)
                    stack.extend(subs)
            except OSError:
                continue
    return {"ok": True, "dirs": empties[:limit], "count": len(empties)}


def open_in_explorer(path: str) -> None:
    target = path if os.path.isdir(path) else os.path.dirname(path)
    if os.path.exists(target):
        os.startfile(target)
