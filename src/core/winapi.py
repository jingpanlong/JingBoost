"""Windows 底层能力封装（纯 ctypes，无第三方依赖）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# ---------------------------------------------------------------- 权限 / 提权

def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


ELEVATE_FLAG = "--elevated-child"

# 必须声明 restype：64 位下 HINSTANCE 会被默认截成 c_int，导致误判「提权失败」
shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR,
                                  wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = wt.HINSTANCE

SEE_MASK_NOCLOSEPROCESS = 0x00000040


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("fMask", wt.ULONG), ("hwnd", wt.HWND),
        ("lpVerb", wt.LPCWSTR), ("lpFile", wt.LPCWSTR),
        ("lpParameters", wt.LPCWSTR), ("lpDirectory", wt.LPCWSTR),
        ("nShow", ctypes.c_int), ("hInstApp", wt.HINSTANCE),
        ("lpIDList", ctypes.c_void_p), ("lpClass", wt.LPCWSTR),
        ("hkeyClass", wt.HKEY), ("dwHotKey", wt.DWORD),
        ("hIcon", wt.HANDLE), ("hProcess", wt.HANDLE),
    ]


shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL


def run_as_admin(extra_args: str = "") -> bool:
    """以管理员身份重新启动自身。返回是否**确实**启动了管理员进程。

    用 ShellExecuteExW 而非 ShellExecuteW：后者只要成功弹出 UAC 就返回成功，
    用户点「否」时程序却以为提权成功而自行退出，只剩一个空桌面。
    ShellExecuteExW 会在用户拒绝时返回 FALSE（ERROR_CANCELLED=1223）。
    extra_args 用于给子进程打「提权交接」标记。
    """
    exe = sys.executable
    params = (extra_args if getattr(sys, "frozen", False)
              else f'"{os.path.abspath(sys.argv[0])}" {extra_args}')
    try:
        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = exe
        sei.lpParameters = params
        sei.lpDirectory = None
        sei.nShow = 1
        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            return False
        if sei.hProcess:
            kernel32.CloseHandle(sei.hProcess)
        return True
    except Exception:
        try:
            rc = shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
            return int(rc or 0) > 32
        except Exception:
            return False


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wt.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x0002


def enable_privilege(name: str) -> bool:
    """启用当前进程的一个特权（如 SeProfileSingleProcessPrivilege）。"""
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                     ctypes.byref(token)):
        return False
    try:
        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = _TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        return ctypes.get_last_error() == 0
    finally:
        kernel32.CloseHandle(token)


# ---------------------------------------------------------------- 内存优化

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100

psapi.EmptyWorkingSet.argtypes = [wt.HANDLE]
psapi.EmptyWorkingSet.restype = wt.BOOL
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE

# 不需要也无法处理的进程
_SKIP_NAMES = {
    "system", "system idle process", "registry", "memory compression",
    "secure system", "idle",
}


def empty_working_set_all() -> dict:
    """清空所有可访问进程的工作集，返回统计信息。"""
    import psutil

    done = 0
    skipped = 0
    freed_hint = 0
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            pid = info["pid"]
            if pid <= 4 or name in _SKIP_NAMES:
                skipped += 1
                continue
            mem = info.get("memory_info")
            rss = getattr(mem, "rss", 0) or 0
            if rss < 4 * 1024 * 1024:      # 小于 4MB 的没必要动
                skipped += 1
                continue
            h = kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
            if not h:
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                skipped += 1
                continue
            try:
                if psapi.EmptyWorkingSet(h):
                    done += 1
                    freed_hint += min(rss, 64 * 1024 * 1024)
                else:
                    skipped += 1
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            skipped += 1
    return {"trimmed": done, "skipped": skipped, "theoretical": freed_hint}


def purge_standby_list() -> bool:
    """清空系统备用内存列表（需要 SeProfileSingleProcessPrivilege）。"""
    SystemMemoryListInformation = 80
    MemoryPurgeStandbyList = 4
    if not enable_privilege("SeProfileSingleProcessPrivilege"):
        # 普通权限下尝试一次，失败也不报错
        pass
    cmd = ctypes.c_int(MemoryPurgeStandbyList)
    status = ntdll.NtSetSystemInformation(
        SystemMemoryListInformation, ctypes.byref(cmd), ctypes.sizeof(cmd))
    return status == 0


def flush_file_cache() -> bool:
    """清空系统文件缓存。"""
    try:
        kernel32.SetSystemFileCacheSize(
            ctypes.c_size_t(-1).value, ctypes.c_size_t(-1).value, 0)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 回收站

SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD),
                ("i64Size", ctypes.c_longlong),
                ("i64NumItems", ctypes.c_longlong)]


shell32.SHEmptyRecycleBinW.argtypes = [wt.HWND, wt.LPCWSTR, wt.DWORD]
shell32.SHEmptyRecycleBinW.restype = ctypes.c_long
shell32.SHQueryRecycleBinW.argtypes = [wt.LPCWSTR, ctypes.POINTER(SHQUERYRBINFO)]
shell32.SHQueryRecycleBinW.restype = ctypes.c_long

TOKEN_QUERY = 0x0008
TOKEN_USER = 1
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD,
                                      ctypes.POINTER(wt.HANDLE)]
kernel32.OpenProcessToken.restype = wt.BOOL
kernel32.GetCurrentProcess.restype = wt.HANDLE
advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wt.DWORD, ctypes.POINTER(wt.DWORD)]
advapi32.GetTokenInformation.restype = wt.BOOL
advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(wt.LPWSTR)]
advapi32.ConvertSidToStringSidW.restype = wt.BOOL

_SID_CACHE = {"value": ""}


def current_user_sid() -> str:
    """当前登录用户的 SID（回收站按 SID 分目录存放，必须精确匹配）。"""
    if _SID_CACHE["value"]:
        return _SID_CACHE["value"]
    sid = ""
    token = wt.HANDLE()
    try:
        if kernel32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY,
                                     ctypes.byref(token)):
            need = wt.DWORD()
            advapi32.GetTokenInformation(token, TOKEN_USER, None, 0,
                                         ctypes.byref(need))
            if need.value:
                buf = ctypes.create_string_buffer(need.value)
                if advapi32.GetTokenInformation(token, TOKEN_USER, buf, need.value,
                                                ctypes.byref(need)):
                    psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                    out = wt.LPWSTR()
                    if advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
                        sid = out.value or ""
    except Exception:
        sid = ""
    finally:
        if token:
            kernel32.CloseHandle(token)
    _SID_CACHE["value"] = sid
    return sid


def recycle_bin_info() -> dict:
    """回收站的**权威**占用：Windows 自己报的数（= 资源管理器里看到的）。

    返回 {size, items, ok}
    """
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    hr = int(shell32.SHQueryRecycleBinW(None, ctypes.byref(info)))
    if hr != 0:
        return {"size": 0, "items": 0, "ok": False}
    return {"size": max(0, int(info.i64Size)),
            "items": max(0, int(info.i64NumItems)), "ok": True}


def recycle_bin_dir() -> list[str]:
    """各卷下属于**当前用户**的回收站目录。"""
    sid = current_user_sid()
    out = []
    if not sid:
        return out
    for vol in _volumes():
        d = os.path.join(vol, "$Recycle.Bin", sid)
        if os.path.isdir(d):
            out.append(d)
    return out


_RB_SYSTEM_FILES = {"desktop.ini", "desktop.ini.nfs"}


def _rb_pairs(dir_path: str):
    """扫一个回收站目录，返回 (孤儿路径列表, 失效索引列表, 孤儿字节数)。

    规则（Windows 回收站是 $I 索引 + $R 数据 的一对一结构）：
    * 名字以 $I 开头 → 索引，记录后缀；
    * 名字以 $R 开头且后缀在索引集合里 → **正常项目**，不动；
    * 名字以 $R 开头但后缀没有索引 → 孤儿数据，回收站里看不到，纯占盘；
    * 其他名字（历史遗留的乱码名、被中断的删除残留）→ 也算孤儿数据；
    * desktop.ini 是资源管理器的目录属性文件，跳过；
    * 索引存在但对应 $R 已经没了 → 失效索引，只占几十字节，顺手清掉。
    """
    try:
        ents = list(os.scandir(dir_path))
    except OSError:
        return [], [], 0

    idx: set[str] = set()
    data: list[os.DirEntry] = []
    for e in ents:
        n = e.name
        up = n.upper()
        if n.lower() in _RB_SYSTEM_FILES:
            continue
        if up.startswith("$I"):
            idx.add(n[2:].lower())
        else:
            data.append(e)

    orphans: list[str] = []
    size = 0
    live_suffix: set[str] = set()
    for e in data:
        n = e.name
        if n.upper().startswith("$R"):
            suffix = n[2:].lower()
            if suffix in idx:
                live_suffix.add(suffix)
                continue
        orphans.append(e.path)
        try:
            if e.is_dir(follow_symlinks=False):
                size += dir_size(e.path)
            else:
                size += e.stat(follow_symlinks=False).st_size
        except OSError:
            pass

    # 有索引但数据已经不在的 → 失效索引
    stale: list[str] = []
    for e in ents:
        n = e.name
        if n.upper().startswith("$I") and n[2:].lower() not in live_suffix:
            stale.append(e.path)
    return orphans, stale, size


def recycle_bin_orphans() -> dict:
    """当前用户回收站目录里的「孤儿残留」：资源管理器看不到、但一直占着盘。

    旧版直接遍历整个 $Recycle.Bin（含其他用户 SID 目录）当回收站大小，
    于是出现「资源管理器里回收站是空的，程序却报 89.7 GB」；
    而清空走 SHEmptyRecycleBin 只按索引删，这些孤儿一个都不会动，
    所以点「立即清理」后数字纹丝不动。
    """
    size = 0
    files = 0
    stale = 0
    for d in recycle_bin_dir():
        orphans, stale_idxs, sz = _rb_pairs(d)
        size += sz
        files += len(orphans)
        stale += len(stale_idxs)
    return {"size": size, "files": files, "stale": stale}


def purge_recycle_orphans() -> dict:
    """删掉当前用户回收站目录下的孤儿残留 + 失效索引（只动本账户目录）。"""
    before = recycle_bin_orphans()["size"]
    removed = 0
    failed = 0
    for d in recycle_bin_dir():
        orphans, stale, _sz = _rb_pairs(d)
        for p in orphans:
            f, _s = delete_path(p)
            if f:
                removed += f
            else:
                failed += 1
        for p in stale:
            try:
                os.remove(p)
                removed += 1
            except OSError:
                failed += 1
    after = recycle_bin_orphans()["size"]
    return {"removed": removed, "failed": failed,
            "size": max(0, before - after), "before": before, "after": after}


def empty_recycle_bin(drive: str | None = None) -> dict:
    """清空回收站，返回**实测**释放量（不是估算值）。"""
    before = recycle_bin_info()["size"]
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    res = int(shell32.SHEmptyRecycleBinW(None, drive, flags))
    # 0 = 成功；0x8000FFFF (E_UNEXPECTED) 表示回收站本来就空
    ok = res == 0 or (res & 0xFFFFFFFF) == 0x8000FFFF
    after = recycle_bin_info()["size"]
    return {"ok": ok, "code": res, "freed": max(0, before - after),
            "before": before, "after": after}


def recycle_bin_size() -> int:
    """回收站占用的真实合计：可见项目 + 孤儿残留。"""
    return recycle_bin_info()["size"] + recycle_bin_orphans()["size"]


def _volumes() -> list[str]:
    import psutil
    out = []
    for p in psutil.disk_partitions(all=False):
        mp = p.mountpoint
        if mp and len(mp) >= 2 and mp[1] == ":":
            out.append(mp[:2] + "\\")
    return out


# ---------------------------------------------------------------- 文件/目录工具

def dir_size(path: str, limit_files: int = 500000) -> int:
    """快速统计目录大小，出错静默跳过。"""
    total = 0
    count = 0
    stack = [path]
    while stack and count < limit_files:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def delete_path(path: str) -> tuple[int, int]:
    """删除文件或目录树，返回 (删除文件数, 释放字节数)。不抛异常。"""
    files = 0
    size = 0
    if not path or not os.path.exists(path):
        return 0, 0
    if os.path.isfile(path):
        try:
            sz = os.path.getsize(path)
            os.remove(path)
            return 1, sz
        except OSError:
            return 0, 0
    # 目录：深度优先删除
    dirs: list[str] = []
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                            dirs.append(e.path)
                        else:
                            sz = e.stat(follow_symlinks=False).st_size
                            os.remove(e.path)
                            files += 1
                            size += sz
                    except OSError:
                        continue
        except OSError:
            continue
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass
    return files, size


def human(n: float) -> str:
    """容量格式化（1024 进位，和资源管理器一致）。"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def human_rate(n: float) -> str:
    """速率格式化（1000 进位）。

    网速习惯用 1000 进制：1008 B/s 应该读作 1.0 KB/s，而不是"1008 B/s"。
    """
    n = max(0.0, float(n or 0))
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"


def _oem_codec() -> str:
    """控制台程序（powercfg / netsh / sc / chkdsk）用的是 OEM 代码页。"""
    try:
        return f"cp{int(kernel32.GetOEMCP())}"
    except Exception:
        return "mbcs"


def _try_utf16le(data: bytes) -> "str | None":
    """尝试按 UTF-16LE 解码，并校验结果像不像正常文本。

    SFC / DISM 的控制台输出是 UTF-16LE（ASCII 字符也占两字节，每个后面跟
    一个 0x00）。这种字节流几乎总能被 GBK「成功」解码——解出来是一堆
    生僻汉字（「嚯璒qb碾」这种），而且不抛异常，导致按 OEM 兜底的旧逻辑
    永远轮不到正确答案，「未发现完整性冲突」之类的结果判断也全部失效。
    所以这里解完后还要看内容：NUL 占比过高或不可打印字符过多就放弃。
    """
    if len(data) % 2:
        data = data[:-1]
    if len(data) < 4:
        return None
    try:
        text = data.decode("utf-16-le")
    except UnicodeDecodeError:
        return None
    if text.count("\x00") > len(text) // 2:
        return None
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\t\r\n")
    if len(text) and printable / len(text) < 0.7:
        return None
    return text.replace("\x00", "")


def _decode_text(data: bytes) -> str:
    """先把子进程输出正确解码。

    之前写死 encoding="utf-8" + errors="ignore"：PowerShell 我们显式设了
    UTF-8 输出所以没问题，但 powercfg / netsh / sc 按 OEM 代码页（简中 = 936）
    输出，用 UTF-8 解就会变成乱码（电源计划名显示成「ƽ」这种），
    而且 errors="ignore" 会把解不出来的中文直接吞掉。

    v1.4.3 补充：SFC / DISM 输出是 UTF-16LE，必须先探测（BOM / NUL 占比），
    否则会被 OEM 解成生僻汉字乱码。
    """
    if not data:
        return ""
    if data.startswith(b"\xff\xfe"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    # NUL 字节多 → 大概率是 UTF-16LE；GBK 文本几乎不含 0x00，不会误伤
    if len(data) >= 8 and data.count(0) * 3 >= len(data):
        text = _try_utf16le(data)
        if text is not None:
            return text
    for enc in ("utf-8", _oem_codec(), "mbcs"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\x00" in text and len(data) >= 8:
            # 纯 ASCII 的 UTF-16LE 能被 UTF-8「成功」解码出夹心 NUL，
            # 同样回退到 UTF-16LE 再试一次
            text16 = _try_utf16le(data)
            if text16 is not None:
                return text16
        return text
    text = _try_utf16le(data)
    if text is not None:
        return text
    return data.decode("utf-8", "replace")


def run_hidden(cmd: list[str] | str, timeout: int = 120, shell: bool = False) -> tuple[int, str]:
    """静默执行命令，返回 (returncode, 输出)。"""
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout, shell=shell,
            creationflags=flags,
        )
        out = _decode_text(p.stdout or "") + _decode_text(p.stderr or "")
        return p.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return -1, "命令执行超时"
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


def run_ps(script: str, timeout: int = 60) -> str:
    """执行 PowerShell 脚本，返回 UTF-8 文本。"""
    ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                      "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(ps):
        ps = "powershell"
    cmd = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script]
    rc, out = run_hidden(cmd, timeout=timeout)
    return out


def ps_json(script: str, timeout: int = 60):
    """执行 PowerShell 并解析 ConvertTo-Json 输出。

    script 必须以表达式结尾（不能带尾随换行），管道才能正确拼接。
    这里额外容忍 PowerShell 输出的前后噪声行。
    """
    import json
    out = run_ps(script.strip() + " | ConvertTo-Json -Depth 4 -Compress",
                 timeout=timeout).strip()
    if not out:
        return None
    for candidate in (out, out[out.find("{"):out.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None
