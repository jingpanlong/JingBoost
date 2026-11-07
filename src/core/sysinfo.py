"""系统信息采集与实时状态监控。

采集分三层，任一层失败都能优雅降级，避免出现大片「未知」：
  1. PowerShell / CIM（最全）
  2. 注册表 SMBIOS / 硬件描述（无需外部进程）
  3. psutil / ctypes（CPU 核心、内存、网卡、分辨率）
"""
from __future__ import annotations

import ctypes
import datetime
import os
import platform
import socket
import threading
import time

import psutil

from . import winapi

_cache: dict = {}

CPU_REG = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
BIOS_REG = r"HARDWARE\DESCRIPTION\System\BIOS"
GPU_CLASS_REG = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

# 主板厂商没写 SMBIOS 时常见的占位字符串
_PLACEHOLDERS = {
    "", "to be filled by o.e.m.", "default string", "system product name",
    "system manufacturer", "not applicable", "not specified", "unknown",
    "n/a", "none", "null", "o.e.m.", "oem", "0", "未知",
}
# 远程控制 / 虚拟显示器，不该混进「显卡」里
_VIRTUAL_GPU = (
    "idd", "oray", "virtual", "vmware", "vbox", "virtualbox", "hyper-v",
    "miracast", "remote", "basic display", "基本显示", "microsoft basic",
    "parsec", "sunlogin", "todesk",
)
_VIRTUAL_NET = (
    "loopback", "pseudo", "*", "virtual", "tap", "wsl", "vpn", "vswitch",
    "hyper-v", "docker", "vmware", "virtualbox", "bluetooth", "teredo",
)


def _clean(value) -> str:
    """去掉 SMBIOS 占位值，并把 REG_MULTI_SZ 列表拼成可读文本。"""
    if isinstance(value, (list, tuple)):
        value = " / ".join(str(x).strip() for x in value if str(x).strip())
    s = str(value if value is not None else "").strip()
    return "" if s.lower() in _PLACEHOLDERS else s


def _reg_val(path: str, name: str):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            v, _ = winreg.QueryValueEx(k, name)
            if isinstance(v, (list, tuple)):
                return " / ".join(str(x).strip() for x in v if str(x).strip())
            return v
    except Exception:
        return ""


def _reg_str(path: str, name: str) -> str:
    return _clean(_reg_val(path, name))


# ---------------------------------------------------------------- 各层采集
def _fast_block() -> dict:
    """纯注册表 + psutil，不调用 PowerShell（首屏 / 兜底）。"""
    mhz = _reg_val(CPU_REG, "~MHz")
    try:
        mhz = int(mhz)
    except (TypeError, ValueError):
        mhz = 0
    return {
        "CPUName": _reg_str(CPU_REG, "ProcessorNameString")
                   or _clean(platform.processor()) or "未知",
        "CPUCores": psutil.cpu_count(logical=False),
        "CPUThreads": psutil.cpu_count(logical=True),
        "OSName": f"{platform.system()} {platform.release()}",
        "OSBuild": platform.version(),
        "OSArch": platform.machine(),
        "CPUMaxClock": mhz,
    }


def _wmic_block() -> dict:
    """CIM/WMI 一次性拿全硬件信息。

    注意：脚本结尾必须紧贴 `}`，ps_json 会在后面追加 ` | ConvertTo-Json`。
    若末尾带换行，管道会变成一条新语句，PowerShell 直接语法错误并返回空。
    """
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$cs = Get-CimInstance Win32_ComputerSystem
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$bb = Get-CimInstance Win32_BaseBoard | Select-Object -First 1
$bios = Get-CimInstance Win32_BIOS | Select-Object -First 1
$gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })
$disk = @(Get-CimInstance Win32_DiskDrive | ForEach-Object { $_.Model })
$net = @(Get-NetAdapter -Physical | Where-Object {$_.Status -eq 'Up'} | ForEach-Object { $_.Name })
[pscustomobject]@{
  Manufacturer=$cs.Manufacturer; Model=$cs.Model;
  OSName=$os.Caption; OSBuild=$os.BuildNumber; OSArch=$os.OSArchitecture;
  InstallDate=$os.InstallDate; LastBoot=$os.LastBootUpTime;
  CPUName=$cpu.Name; CPUCores=$cpu.NumberOfCores; CPUThreads=$cpu.NumberOfLogicalProcessors;
  CPUMaxClock=$cpu.MaxClockSpeed;
  Board=$bb.Manufacturer + ' ' + $bb.Product;
  BIOS=$bios.SMBIOSBIOSVersion;
  GPU=$gpu; Disks=$disk; NetAdapters=$net
}""".strip()
    data = winapi.ps_json(script, timeout=40)
    return data if isinstance(data, dict) else {}


def _bios_block() -> dict:
    """SMBIOS 注册表兜底（PowerShell 被策略禁用时仍可用）。"""
    man = _reg_str(BIOS_REG, "SystemManufacturer")
    model = _reg_str(BIOS_REG, "SystemProductName")
    bman = _reg_str(BIOS_REG, "BaseBoardManufacturer")
    bprod = _reg_str(BIOS_REG, "BaseBoardProduct")
    return {
        "Manufacturer": man,
        "Model": model,
        "Board": " ".join(x for x in (bman, bprod) if x),
        "BIOS": _reg_str(BIOS_REG, "BIOSVersion"),
        "BIOSDate": _reg_str(BIOS_REG, "BIOSReleaseDate"),
    }


def _gpu_registry() -> list[str]:
    """从显示适配器类注册表枚举显卡名。"""
    import winreg
    out: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GPU_CLASS_REG) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                desc = _reg_str(GPU_CLASS_REG + "\\" + sub, "DriverDesc")
                if desc and desc not in out:
                    out.append(desc)
    except Exception:
        pass
    return out


def _net_fallback() -> list[str]:
    try:
        stats = psutil.net_if_stats()
        out = []
        for name, st in stats.items():
            low = name.lower()
            if not st.isup or any(k in low for k in _VIRTUAL_NET):
                continue
            out.append(name)
        return out
    except Exception:
        return []


def _disk_fallback() -> list[str]:
    """CIM 失败时再单独试一次 Win32_DiskDrive，命令更短更容易成功。"""
    out = winapi.run_ps(
        "Get-CimInstance Win32_DiskDrive | ForEach-Object { $_.Model }",
        timeout=20)
    return [_clean(x) for x in out.splitlines() if _clean(x)]


def _vendor_text(man: str, model: str, board: str) -> str:
    """设备型号：SMBIOS 常是 "To Be Filled By O.E.M."，此时用主板型号标注为组装机。"""
    v = " ".join(x for x in (man, model) if x)
    if v:
        return v
    return f"{board} · 组装机" if board else "自定义组装机"


def _quick_board_block() -> dict:
    """注册表版主板/显卡信息，纯内存读取，几毫秒返回（首屏用，不起 PowerShell）。

    之前 fast 模式把这些字段硬写成「读取中…」「未知」，用户一眼就看到一堆未知；
    其实 SMBIOS 与显示适配器类注册表里都有现成的值。
    """
    reg = _bios_block()
    gpus = [g for g in _gpu_registry()
            if not any(k in g.lower() for k in _VIRTUAL_GPU)] or _gpu_registry()
    return {
        "Vendor": _vendor_text(reg["Manufacturer"], reg["Model"], reg["Board"]),
        "Board": reg["Board"],
        "BIOS": reg["BIOS"],
        "GPU": gpus,
    }


def _hardware_block(fast: bool = False) -> dict:
    base = _fast_block()
    if fast:
        base.update(_quick_board_block())
        return base

    wmi = _wmic_block()
    reg = _bios_block()
    out = dict(base)
    for k, v in wmi.items():
        if v not in (None, "", [], {}, 0):
            out[k] = v

    # 设备型号：SMBIOS 常是 "To Be Filled By O.E.M."，此时用主板型号标注为组装机
    man = _clean(out.get("Manufacturer")) or reg["Manufacturer"]
    model = _clean(out.get("Model")) or reg["Model"]
    board = _clean(out.get("Board")) or reg["Board"]
    out["Vendor"] = _vendor_text(man, model, board)
    out["Board"] = board or "未知"

    bios = _clean(out.get("BIOS")) or reg["BIOS"]
    if reg.get("BIOSDate"):
        bios = f"{bios}（{reg['BIOSDate']}）" if bios else reg["BIOSDate"]
    out["BIOS"] = bios or "未知"

    gpus = [_clean(x) for x in (out.get("GPU") or []) if _clean(x)]
    if not gpus:
        gpus = _gpu_registry()
    real = [g for g in gpus if not any(k in g.lower() for k in _VIRTUAL_GPU)]
    out["GPU"] = real or gpus or ["未知"]

    disks = [_clean(x) for x in (out.get("Disks") or []) if _clean(x)]
    out["Disks"] = disks or _disk_fallback()

    nets = [_clean(x) for x in (out.get("NetAdapters") or []) if _clean(x)]
    out["NetAdapters"] = nets or _net_fallback()

    out["CPUName"] = _clean(out.get("CPUName")) or base["CPUName"]
    try:
        out["CPUMaxClock"] = int(out.get("CPUMaxClock") or base["CPUMaxClock"] or 0)
    except (TypeError, ValueError):
        out["CPUMaxClock"] = base["CPUMaxClock"]
    return out


def _fmt_clock(mhz) -> str:
    try:
        v = float(mhz)
    except (TypeError, ValueError):
        return "未知"
    if v <= 0:
        return "未知"
    return f"{v / 1000:.2f} GHz" if v >= 1000 else f"{int(v)} MHz"


# ---------------------------------------------------------------- 磁盘与激活
def _disk_media() -> dict:
    """返回 {盘符: MediaType}，一次 PowerShell 查询搞定。"""
    script = (
        "Get-PhysicalDisk | ForEach-Object { $d=$_; "
        "Get-Partition -DiskNumber $d.DeviceId -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DriveLetter } | ForEach-Object { "
        "\"$($_.DriveLetter)|$($d.MediaType)\" } }"
    )
    out = winapi.run_ps(script, timeout=20)
    result = {}
    for line in out.splitlines():
        line = line.strip()
        if "|" in line:
            k, _, v = line.partition("|")
            result[k.strip().upper()] = v.strip()
    return result


def _activation() -> str:
    script = (
        "$p = Get-CimInstance SoftwareLicensingProduct -Filter "
        "\"PartialProductKey IS NOT NULL AND ApplicationId='55c92734-d682-4d71-983e-d6ec3f16059f'\" "
        "| Select-Object -First 1; if ($p) { $p.LicenseStatus } else { 'x' }"
    )
    out = winapi.run_ps(script, timeout=25).strip()
    return {"1": "已激活", "0": "未激活"}.get(out, "未知")


# ---------------------------------------------------------------- 对外接口
_RAM_CACHE = {"data": None, "time": 0.0}
_TEMP_CACHE = {"data": None, "time": 0.0}

_SMBIOS_MEM = {0: "", 20: "DDR", 21: "DDR2", 22: "DDR2", 24: "DDR3",
               26: "DDR4", 27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3",
               30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}


def ram_modules(force: bool = False) -> dict:
    """内存条数量 / 插槽 / 类型 / 频率（WMI Win32_PhysicalMemory，缓存 5 分钟）。"""
    now = time.time()
    if not force and _RAM_CACHE["data"] and now - _RAM_CACHE["time"] < 300:
        return _RAM_CACHE["data"]
    out = {"count": 0, "slots": 0, "type": "", "speed": ""}
    data = winapi.ps_json(r"""
$m = @(Get-CimInstance Win32_PhysicalMemory -ErrorAction SilentlyContinue)
$arr = Get-CimInstance Win32_PhysicalMemoryArray -ErrorAction SilentlyContinue
[pscustomobject]@{
  Count = $m.Count
  Type  = if ($m.Count) { $m[0].SMBIOSMemoryType } else { 0 }
  Speed = if ($m.Count) { $m[0].Speed } else { 0 }
  Slots = if ($arr) { $arr.MemoryDevices } else { 0 }
}
""", timeout=20)
    if isinstance(data, dict):
        def _int(v):
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0
        out["count"] = _int(data.get("Count"))
        out["slots"] = _int(data.get("Slots"))
        out["type"] = _SMBIOS_MEM.get(_int(data.get("Type")), "")
        speed = _int(data.get("Speed"))
        out["speed"] = f"{speed} MHz" if speed else ""
    _RAM_CACHE.update(data=out, time=now)
    return out


def cpu_temp(force: bool = False) -> float | None:
    """CPU 温度（摄氏度）。多数消费级主板不暴露 ACPI 热区，拿不到就返回 None。

    依次尝试 LibreHardwareMonitor / OpenHardwareMonitor 的 WMI 传感器，
    最后才是 ACPI 热区；全部失败说明这台机器没有可读的传感器，
    上层应显示「不支持」而不是「未知」，别让用户以为采集出错。
    """
    now = time.time()
    if not force and now - _TEMP_CACHE["time"] < 20:
        return _TEMP_CACHE["data"]
    val = None
    for ns in ("root/LibreHardwareMonitor", "root/OpenHardwareMonitor"):
        data = winapi.ps_json(r"""
$s = Get-CimInstance -Namespace %s -ClassName Sensor -ErrorAction SilentlyContinue |
     Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU|Package|Core|Tctl' } |
     Sort-Object Value -Descending | Select-Object -First 1
if ($s) { [pscustomobject]@{ C = [math]::Round($s.Value, 1) } }
""" % ns, timeout=15)
        if isinstance(data, dict) and data.get("C") is not None:
            try:
                val = float(data["C"])
                break
            except (TypeError, ValueError):
                pass
    if val is None:
        data = winapi.ps_json(r"""
$z = @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue)
if ($z.Count) {
  $c = ($z | Measure-Object CurrentTemperature -Average).Average / 10 - 273.15
  if ($c -gt 0 -and $c -lt 125) { [pscustomobject]@{ C = [math]::Round($c, 1) } }
}
""", timeout=15)
        if isinstance(data, dict) and data.get("C") is not None:
            try:
                val = float(data["C"])
            except (TypeError, ValueError):
                val = None
    _TEMP_CACHE.update(data=val, time=now)
    return val


def warm() -> None:
    """后台预热，顺序按「对首屏的有用程度」排：

    1. 内存条（0.5 秒）—— 仪表盘副标题要用；
    2. 完整硬件（物理磁盘数、主板、激活状态）—— 决定 partial 何时变 False；
    3. 温度放最后（2~4 秒，而且多数消费级主板根本读不到）。
    """
    try:
        ram_modules(force=True)
    except Exception:
        pass
    try:
        sysinfo(force=True)
    except Exception:
        pass
    try:
        cpu_temp(force=True)
    except Exception:
        pass


_refreshing = threading.Lock()


def _parallel(*fns):
    """并发执行若干个采集函数，返回按入参顺序排列的结果（失败的置 None）。

    这里的每一步都是一次独立的 PowerShell 调用，启动开销（1~2 秒）远大于
    实际计算，串行累加非常浪费。并发后总耗时 ≈ 最慢的那一个。
    """
    out: list = [None] * len(fns)

    def wrap(i: int, fn):
        try:
            out[i] = fn()
        except Exception:
            out[i] = None

    threads = [threading.Thread(target=wrap, args=(i, fn), daemon=True)
               for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


def _refresh_full_async() -> None:
    """硬件信息过期时后台刷新，别让界面等 PowerShell。"""
    if not _refreshing.acquire(blocking=False):
        return

    def run():
        try:
            sysinfo(force=True)
        except Exception:
            pass
        finally:
            _refreshing.release()

    threading.Thread(target=run, name="JingBoostSysinfoRefresh", daemon=True).start()


def sysinfo(force: bool = False, fast: bool = False) -> dict:
    """fast=True 时不调用 PowerShell，用于界面首屏快速返回。"""
    now = time.time()
    # 完整数据只要存在就优先用（硬件信息几乎不变）：之前用 120 秒过期，
    # 过期后 fast 模式会退回残缺版本，仪表盘上的内存条数 / 硬盘数量就变回 0，
    # 而预热线程只在启动时跑一次，永远补不回来。过期了改成后台刷新。
    if fast and not force:
        full = _cache.get("full")
        if full:
            if now - full["time"] > 600:
                _refresh_full_async()
            return full["data"]
    key = "fast" if fast else "full"
    cached = _cache.get(key)
    # 轻量快照只用于「首屏第一帧」，5 秒后就不再复用，避免用户长时间
    # 停在残缺数据上（完整数据一到就会被上面的分支截走）
    ttl = 5 if fast else 120
    if not force and cached and now - cached["time"] < ttl:
        return cached["data"]

    virt = psutil.virtual_memory()
    if fast:
        w = _hardware_block(fast=True)
        media_map = {}
        act = "读取中"
        ram = _RAM_CACHE["data"] or {"count": 0, "slots": 0, "type": "", "speed": ""}
    else:
        # 这几步互不依赖，串行要 6 秒以上（每次 PowerShell 启动就要 1~2 秒），
        # 并发跑取最慢的那一个，实测完整信息从 ~6s 降到 ~3.5s。
        w, media_map, act, ram = _parallel(
            lambda: _hardware_block(fast=False),
            _disk_media,
            _activation,
            ram_modules,
        )
        w = w or _fast_block()
        media_map = media_map or {}
        act = act or "未知"
        ram = ram or {"count": 0, "slots": 0, "type": "", "speed": ""}

    disks = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
        except Exception:
            continue
        letter = p.mountpoint[0].upper() if p.mountpoint else ""
        raw = media_map.get(letter, "")
        media = "SSD 固态" if "SSD" in raw else ("HDD 机械" if "HDD" in raw else "未知")
        disks.append({
            "device": p.device, "fstype": p.fstype, "media": media,
            "total": u.total, "used": u.used, "free": u.free,
            "percent": round(u.used / u.total * 100, 1) if u.total else 0,
            "total_h": winapi.human(u.total), "free_h": winapi.human(u.free),
            "used_h": winapi.human(u.used),
        })

    phys = w.get("Disks") or []
    # 温度一律只读缓存：WMI 传感器查询要 2~4 秒，是整条链上最慢的一步，
    # 不该拖住内存条数 / 物理磁盘数到位。cpu_temp() 由 warm() 单独预热，
    # 拿到后仪表盘会通过 get_metrics 的 temp 字段补上。
    temp = _TEMP_CACHE["data"] if _TEMP_CACHE["time"] else None
    boot = psutil.boot_time()
    up = now - boot

    data = {
        "hostname": socket.gethostname(),
        "user": os.environ.get("USERNAME", ""),
        "os": f"{_clean(w.get('OSName')) or platform.system()} "
              f"(Build {_clean(w.get('OSBuild')) or platform.version()})",
        "os_arch": _clean(w.get("OSArch")) or platform.machine(),
        "python": platform.python_version(),
        "vendor": w.get("Vendor") or "未知",
        "board": w.get("Board") or "未知",
        "bios": w.get("BIOS") or "未知",
        "cpu": w.get("CPUName") or "未知",
        "cpu_cores": w.get("CPUCores") or psutil.cpu_count(logical=False),
        "cpu_threads": w.get("CPUThreads") or psutil.cpu_count(logical=True),
        "cpu_clock": _fmt_clock(w.get("CPUMaxClock")),
        "gpu": w.get("GPU") or ["未知"],
        "phys_disks": phys,
        "disk_count": len(phys),
        "net_adapters": w.get("NetAdapters") or [],
        "ram_total": virt.total,
        "ram_total_h": winapi.human(virt.total),
        "ram_used": virt.used,
        "ram_percent": virt.percent,
        "ram_sticks": ram["count"],
        "ram_slots": ram["slots"],
        "ram_type": ram["type"],
        "ram_speed": ram["speed"],
        "cpu_temp": temp,           # None = 没读到（可能只是还没探测）
        "cpu_temp_known": bool(_TEMP_CACHE["time"]),   # 是否已经探测过
        "boot_time": datetime.datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M"),
        "uptime": _fmt_uptime(up),
        "disks": disks,
        "admin": winapi.is_admin(),
        "activated": act,
        "res": _resolution(),
        "partial": fast,
    }
    _cache[key] = {"data": data, "time": now}
    return data


def _fmt_uptime(sec: float) -> str:
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} 天 {h} 小时 {m} 分"
    if h:
        return f"{h} 小时 {m} 分"
    return f"{m} 分钟"


def _resolution() -> str:
    try:
        user32 = ctypes.WinDLL("user32")
        return f"{user32.GetSystemMetrics(0)} x {user32.GetSystemMetrics(1)}"
    except Exception:
        return "未知"


_prev_io = None
_primed = False

# 预热一次，避免首屏 CPU 读数恒为 0
try:
    psutil.cpu_percent(interval=None)
except Exception:
    pass


def metrics() -> dict:
    """轻量实时指标，被前端每秒轮询。"""
    global _prev_io, _primed
    virt = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None if _primed else 0.12)
    _primed = True
    io = psutil.disk_io_counters()
    net = psutil.net_io_counters()
    now = time.time()
    dr = dw = nr = nw = 0.0
    if _prev_io:
        dt = max(now - _prev_io[0], 0.001)
        dr = (io.read_bytes - _prev_io[1]) / dt
        dw = (io.write_bytes - _prev_io[2]) / dt
        nr = (net.bytes_recv - _prev_io[3]) / dt
        nw = (net.bytes_sent - _prev_io[4]) / dt
    _prev_io = (now, io.read_bytes, io.write_bytes, net.bytes_recv, net.bytes_sent)

    # psutil 的温度接口在 Windows 上并不工作，直接用 cpu_temp() 的缓存值，
    # 避免每 1.6 秒轮询都去跑一次 WMI
    temp = _TEMP_CACHE.get("data")

    return {
        "cpu": round(cpu, 1),
        "ram_percent": virt.percent,
        "ram_used": virt.used,
        "ram_total": virt.total,
        "ram_used_h": winapi.human(virt.used),
        "ram_total_h": winapi.human(virt.total),
        "disk_read": winapi.human_rate(dr) + "/s",
        "disk_write": winapi.human_rate(dw) + "/s",
        "net_up": winapi.human_rate(nw) + "/s",
        "net_down": winapi.human_rate(nr) + "/s",
        "net_up_raw": nw, "net_down_raw": nr,
        "temp": temp,
        "proc_count": len(psutil.pids()),
        "ts": now,
    }


def top_processes(n: int = 8) -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            procs.append({
                "pid": p.info["pid"],
                "name": p.info["name"],
                "mem": p.info["memory_info"].rss if p.info["memory_info"] else 0,
            })
        except Exception:
            continue
    procs.sort(key=lambda x: x["mem"], reverse=True)
    out = []
    for p in procs[:n]:
        out.append({**p, "mem_h": winapi.human(p["mem"])})
    return out
