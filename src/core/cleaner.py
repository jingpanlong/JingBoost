"""垃圾清理引擎：14 类可清理目标，支持先扫描后清理。"""
from __future__ import annotations

import glob
import os
import shutil
import sys

from . import winapi

LOCAL = os.environ.get("LOCALAPPDATA", "")
ROAMING = os.environ.get("APPDATA", "")
WIN = os.environ.get("SystemRoot", r"C:\Windows")
PROGRAMDATA = os.environ.get("ProgramData", r"C:\ProgramData")
USERPROFILE = os.environ.get("USERPROFILE", "")


def _browser_cache():
    paths = []
    for base in (
        os.path.join(LOCAL, r"Microsoft\Edge\User Data"),
        os.path.join(LOCAL, r"Google\Chrome\User Data"),
        os.path.join(LOCAL, r"BraveSoftware\Brave-Browser\User Data"),
        os.path.join(LOCAL, r"360Chrome\Chrome\User Data"),
    ):
        if not os.path.isdir(base):
            continue
        for prof in ("Default", "Profile *", "Guest Profile"):
            pp = os.path.join(base, prof)
            for sub in (r"Cache\Cache_Data", r"Code Cache", r"GPUCache",
                        r"Service Worker\CacheStorage", r"Cache", r"Media Cache"):
                paths.append(os.path.join(pp, sub))
    return paths


TARGETS: list[dict] = [
    {
        "id": "user_temp", "name": "用户临时文件", "risk": "safe", "admin": False,
        "desc": "各类软件运行中产生的临时文件，可安全删除",
        "paths": [os.environ.get("TEMP", os.path.join(LOCAL, "Temp")),
                  os.path.join(LOCAL, "Temp")],
    },
    {
        "id": "win_temp", "name": "系统临时文件", "risk": "safe", "admin": True,
        "desc": "Windows\\Temp 下的残留文件",
        "paths": [os.path.join(WIN, "Temp")],
    },
    {
        "id": "recycle_bin", "name": "回收站", "risk": "caution", "admin": False,
        # 口径改成 Windows 自己报的数（SHQueryRecycleBin），也就是资源管理器
        # 里看到的那些。以前直接遍历 $Recycle.Bin 目录，会把索引已丢失的孤儿
        # 文件也算进来，于是出现「回收站明明是空的却显示 89.7 GB」。
        "desc": "资源管理器回收站中可见的文件，清空后无法恢复",
        "kind": "recycle", "paths": [],
    },
    {
        # 孤儿残留：索引（$I）没了、回收站里看不到，但 $R 文件还占着盘。
        # SHEmptyRecycleBin 按索引删，永远清不掉这些，必须单独一项。
        "id": "recycle_orphan", "name": "回收站残留文件", "risk": "caution",
        "admin": False, "default_on": True,
        "desc": "回收站索引已丢失的残留文件（回收站里看不到，但一直占磁盘）",
        "kind": "recycle_orphan", "paths": [],
    },
    {
        "id": "prefetch", "name": "预读取缓存", "risk": "safe", "admin": True,
        "desc": "Windows 预读文件，删除后首次启动程序略慢，会重建",
        "paths": [os.path.join(WIN, "Prefetch")],
        "keep": ["LAYOUT.INI"],
    },
    {
        "id": "thumbcache", "name": "缩略图与图标缓存", "risk": "safe", "admin": False,
        "desc": "图片缩略图数据库，删除后会自动重建",
        "paths": [os.path.join(LOCAL, r"Microsoft\Windows\Explorer\thumbcache_*.db"),
                  os.path.join(LOCAL, r"Microsoft\Windows\Explorer\iconcache_*.db")],
    },
    {
        "id": "wu_cache", "name": "Windows 更新缓存", "risk": "caution", "admin": True,
        "desc": "已下载的更新安装包，删除不影响已装好的更新",
        "paths": [os.path.join(WIN, r"SoftwareDistribution\Download")],
    },
    {
        "id": "delivery_opt", "name": "传递优化缓存", "risk": "safe", "admin": True,
        "desc": "更新分发缓存文件",
        "paths": [os.path.join(WIN, r"SoftwareDistribution\DeliveryOptimization"),
                  os.path.join(WIN, r"ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization")],
    },
    {
        "id": "wer", "name": "错误报告与崩溃转储", "risk": "safe", "admin": True,
        "desc": "程序崩溃时生成的报告文件",
        "paths": [os.path.join(LOCAL, r"Microsoft\Windows\WER"),
                  os.path.join(PROGRAMDATA, r"Microsoft\Windows\WER"),
                  os.path.join(LOCAL, "CrashDumps"),
                  os.path.join(WIN, "Minidump")],
    },
    {
        "id": "browser_cache", "name": "浏览器缓存", "risk": "safe", "admin": False,
        "desc": "Edge / Chrome / 360 等浏览器网页缓存，关闭浏览器后清理更彻底",
        "paths": _browser_cache(),
    },
    {
        "id": "system_logs", "name": "系统日志与安装残留", "risk": "safe", "admin": True,
        "desc": "CBS 日志、Windows 升级日志等文本型记录",
        # 注意：这里只能列目录，不能再额外挂 CbsPersist*.log 之类的 glob，
        # 否则同一批文件会被算两遍，扫描结果虚高。
        "paths": [os.path.join(WIN, r"Logs\CBS"),
                  os.path.join(WIN, r"Logs\DISM"),
                  os.path.join(WIN, "Panther"),
                  os.path.join(WIN, r"System32\LogFiles")],
    },
    {
        "id": "dev_cache", "name": "开发工具缓存", "risk": "safe", "admin": False,
        "desc": "pip / npm / yarn / NuGet 下载缓存，重新安装依赖时会重新下载",
        # 不再包含 %TEMP%\pip-*：那已经在「用户临时文件」里算过一遍了。
        "paths": [os.path.join(LOCAL, "pip", "Cache"),
                  os.path.join(LOCAL, "npm-cache"),
                  os.path.join(LOCAL, r"Yarn\Cache"),
                  os.path.join(LOCAL, r"NuGet\v3-cache")],
    },
    {
        "id": "dns_cache", "name": "DNS 解析缓存", "risk": "safe", "admin": False,
        "desc": "刷新域名解析缓存，可解决部分网站打不开的问题",
        "kind": "dns", "paths": [],
    },
    {
        "id": "old_logs", "name": "临时更新与安装包", "risk": "safe", "admin": True,
        "desc": "升级遗留目录 $Windows.~BT / ~WS 与 MSI 缓存",
        "paths": [r"C:\$Windows.~BT", r"C:\$Windows.~WS",
                  os.path.join(WIN, "Installer", "$PatchCache$"),
                  os.path.join(WIN, r"SoftwareDistribution\PostRebootEventCache.V2")],
    },
]

_BY_ID = {t["id"]: t for t in TARGETS}


def _expand(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        if not p:
            continue
        p = os.path.expandvars(p)
        if "*" in p or "?" in p:
            out.extend(glob.glob(p))
        else:
            out.append(p)
    # 去重 + 安全校验
    seen = set()
    safe = []
    for p in out:
        k = p.lower().rstrip("\\")
        if k in seen:
            continue
        seen.add(k)
        if len(p) < 8 or k.endswith(":") or k == os.path.abspath(os.sep).rstrip("\\").lower():
            continue
        if k.startswith(os.path.dirname(os.path.abspath(__file__)).lower()):
            continue
        safe.append(p)
    return safe


# PyInstaller 单文件程序运行时会把自身解包到 %TEMP%\_MEIxxxxx，
# python313.dll 这些就在里面。我们「用户临时文件」瞄的是整个 %TEMP%，
# 一旦把正在运行的解包目录删掉，程序当场就崩，下次启动还会报
# "Failed to load Python DLL ... 找不到指定的模块"（实测踩过）。
# 别人的 _MEI 目录同样不能碰 —— 那里面是另一个活着的程序。
_SELF_MEI = os.path.normcase(getattr(sys, "_MEIPASS", "") or "")


def _is_protected(name: str, full_path: str = "") -> bool:
    """运行中的程序目录：一律不统计、不删除。"""
    if name.upper().startswith("_MEI"):
        return True
    if _SELF_MEI and full_path:
        p = os.path.normcase(full_path)
        if p == _SELF_MEI or p.startswith(_SELF_MEI + os.sep):
            return True
    return False


def _dir_stat(path: str, keep: tuple[str, ...] = ()) -> tuple[int, int, bool]:
    """统计目录里的 (字节数, 文件数, 是否被权限挡住)。

    keep 里的文件不会被删，所以也不统计。
    第三个返回值很关键：非管理员账号下 C:\\Windows\\Prefetch 会直接拒绝访问，
    老代码把它当 0 B，于是界面显示「没有垃圾」，其实是没权限看到。
    """
    size = 0
    files = 0
    denied = False
    stack = [path]
    n = 0
    while stack and n < 300000:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if _is_protected(e.name, e.path):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            if keep and e.name.lower() in keep:
                                continue
                            size += e.stat(follow_symlinks=False).st_size
                            files += 1
                            n += 1
                    except OSError:
                        continue
        except PermissionError:
            denied = True
        except OSError:
            continue
    return size, files, denied


def _stat_target(t: dict) -> tuple[int, int, bool]:
    """单个清理目标的 (可释放字节, 文件数, 是否被权限挡住)。

    只算**真正会被删掉**的东西：keep 文件、无权限目录都不计入。
    """
    kind = t.get("kind", "files")
    if kind == "recycle":
        info = winapi.recycle_bin_info()
        return info["size"], info["items"], False
    if kind == "recycle_orphan":
        o = winapi.recycle_bin_orphans()
        # 失效索引也会被清掉，算进文件数，避免「扫描 3 个 / 清理 308 个」对不上
        return o["size"], o["files"] + o.get("stale", 0), False
    if kind == "dns":
        return 0, 0, False

    keep = tuple(k.lower() for k in t.get("keep", []))
    size = 0
    files = 0
    denied = False
    for p in _expand(t["paths"]):
        if os.path.isfile(p):
            if keep and os.path.basename(p).lower() in keep:
                continue
            try:
                size += os.path.getsize(p)
                files += 1
            except PermissionError:
                denied = True
            except OSError:
                pass
        elif os.path.isdir(p):
            s, f, d = _dir_stat(p, keep)
            size += s
            files += f
            denied = denied or d
    return size, files, denied


def scan(ids: list[str] | None = None, progress=None) -> list[dict]:
    """扫描各清理目标。「回收站」与「回收站残留」口径互不重叠。"""
    todo = [t for t in TARGETS if not ids or t["id"] in ids]
    total = len(todo)
    result = []
    for i, t in enumerate(todo):
        if progress:
            progress(i, total, t["name"])
        size, files, denied = _stat_target(t)
        result.append({
            "id": t["id"], "name": t["name"], "desc": t["desc"],
            "risk": t["risk"], "admin": t["admin"],
            "default_on": bool(t.get("default_on")),
            "size": size, "files": files,
            "denied": denied and size == 0,
            "size_h": "需管理员" if (denied and size == 0) else winapi.human(size),
        })
    if progress:
        progress(total, total, "完成")
    return result


def clean(ids: list[str], progress=None) -> dict:
    """执行清理。progress(done, total, name) 用于前端显示真实进度。

    返回的 freed 一律是**清理前后实测差值**，不是扫描时的估算值。
    """
    total_size = 0
    total_files = 0
    details = []
    todo = [t for t in (_BY_ID.get(i) for i in ids) if t]
    total = len(todo)
    for i, t in enumerate(todo):
        if progress:
            progress(i, total, t["name"])
        kind = t.get("kind", "files")
        s = 0
        f = 0
        if kind == "recycle":
            before = winapi.recycle_bin_info()
            r = winapi.empty_recycle_bin()
            after = winapi.recycle_bin_info()
            s = r.get("freed", 0) if r.get("ok") else 0
            f = max(0, before["items"] - after["items"])
        elif kind == "recycle_orphan":
            r = winapi.purge_recycle_orphans()
            s = r.get("size", 0)
            f = r.get("removed", 0)
        elif kind == "dns":
            winapi.run_hidden(["ipconfig", "/flushdns"], timeout=20)
        else:
            keep = tuple(k.lower() for k in t.get("keep", []))
            size_before = 0
            files_before = 0
            for p in _expand(t["paths"]):
                if os.path.isdir(p):
                    sz, cnt, _d = _dir_stat(p, keep)
                    size_before += sz
                    files_before += cnt
                elif os.path.isfile(p) and os.path.basename(p).lower() not in keep:
                    try:
                        size_before += os.path.getsize(p)
                        files_before += 1
                    except OSError:
                        pass
            for p in _expand(t["paths"]):
                if os.path.isdir(p):
                    _purge_dir(p, list(keep))
                elif os.path.isfile(p) and os.path.basename(p).lower() not in keep:
                    winapi.delete_path(p)
            size_after = 0
            for p in _expand(t["paths"]):
                if os.path.isdir(p):
                    size_after += _dir_stat(p, keep)[0]
            s = max(0, size_before - size_after)
            f = files_before
        total_size += s
        total_files += f
        details.append({"id": t["id"], "name": t["name"], "size": s,
                        "size_h": winapi.human(s), "files": f})
    if progress:
        progress(total, total, "完成")
    return {
        "ok": True,
        "freed": total_size, "freed_h": winapi.human(total_size),
        "files": total_files, "details": details,
    }


def _purge_dir(root: str, keep: list[str]) -> None:
    """删除目录内容但保留目录本身；跳过 keep 文件与正在占用的文件。"""
    if _is_protected(os.path.basename(root), root):
        return
    dirs: list[str] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if _is_protected(e.name, e.path):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                            dirs.append(e.path)
                        else:
                            if e.name.lower() in keep:
                                continue
                            os.remove(e.path)
                    except OSError:
                        continue
        except OSError:
            continue
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass


def big_estimate(ids: list[str]) -> int:
    return sum(x["size"] for x in scan(ids))


def clean_all_safe() -> dict:
    safe_ids = [t["id"] for t in TARGETS if t["risk"] == "safe"]
    return clean(safe_ids)
