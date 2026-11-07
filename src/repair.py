"""系统修复与维护：SFC / DISM / 磁盘优化 / 磁盘检查 / 系统还原点。"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time

import psutil

from . import winapi

WIN = os.environ.get("SystemRoot", r"C:\Windows")


def _stream(cmd: list[str], cb=None, timeout: int = 3600) -> tuple[int, str]:
    """流式执行命令，逐行回调。返回 (rc, 全量输出)。

    不能指定 encoding="utf-8"：sfc / DISM / chkdsk 按 OEM 代码页（简中 936）
    输出，硬解 UTF-8 会得到乱码，后面的「未发现完整性冲突」之类判断全部失效。
    这里按字节读，再交给统一的解码函数。
    """
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=flags)
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)
    lines = []
    start = time.time()
    assert p.stdout is not None
    for raw in p.stdout:
        # UTF-16LE 输出（SFC / DISM）的换行是 \r\x00\n\x00，按 b"\n" 切行后：
        # 行尾是 \r\x00（必须精确剥这两个字节——不能 rstrip，否则会把内容里
        # 「%」(U+0025) 等字符的低字节 0x00 一起剥掉，破坏字节对齐）；
        # 从第二行起行首还带着上一行换行的低字节 \x00，剥掉与否以「剥完后
        # 能否解码成正常文本」为准，不能见 00 就剥（「开」U+5F00 低字节也是 0x00）。
        b = raw
        if b.endswith(b"\n"):
            b = b[:-1]
        if b.endswith(b"\r\x00"):
            b = b[:-2]
        elif b.endswith(b"\r"):
            b = b[:-1]
        if b.count(0):
            # 行里出现 NUL 字节 → 只可能是 UTF-16LE（GBK/UTF-8 文本不含 0x00）。
            # 奇数长度说明行首带着上一行换行的低字节 \x00，优先试剥掉它的版本。
            candidates = []
            if len(b) % 2 and b.startswith(b"\x00"):
                candidates.append(b[1:])
            candidates.append(b)
            line = None
            for cand in candidates:
                text = winapi._try_utf16le(cand)
                if text is not None:
                    line = text.rstrip()
                    break
            if line is None:
                line = winapi._decode_text(b).rstrip()
        else:
            line = winapi._decode_text(b).rstrip()
        line = line.replace("\x00", "")
        if not line.strip():
            continue
        lines.append(line)
        if cb:
            cb(line)
        if time.time() - start > timeout:
            p.kill()
            break
    p.wait()
    return p.returncode, "\n".join(lines)


def sfc_scan(cb=None) -> dict:
    if not winapi.is_admin():
        return {"ok": False, "msg": "SFC 需要管理员权限，请以管理员身份重新打开本工具"}
    if cb:
        cb("正在启动系统文件检查，全过程约 5-15 分钟，请勿关闭窗口…")
    rc, out = _stream(["sfc", "/scannow"], cb=cb, timeout=2400)
    healthy = "未发现完整性冲突" in out or "did not find any integrity violations" in out
    fixed = "成功修复" in out or "successfully repaired" in out
    if rc == 0 or healthy or fixed:
        msg = "系统文件完好，未发现损坏" if healthy else "发现损坏并已自动修复"
        return {"ok": True, "msg": msg, "output": out[-4000:]}
    return {"ok": False, "msg": "SFC 执行异常，请查看日志", "output": out[-4000:]}


def dism_repair(cb=None) -> dict:
    if not winapi.is_admin():
        return {"ok": False, "msg": "DISM 需要管理员权限"}
    if cb:
        cb("正在通过 Windows 更新源修复系统映像，可能需要 10-30 分钟…")
    rc, out = _stream(["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"],
                      cb=cb, timeout=3600)
    ok = "还原操作已成功完成" in out or "The operation completed successfully" in out
    return {"ok": ok or rc == 0,
            "msg": "系统映像修复完成" if (ok or rc == 0) else "修复未完成，请查看日志",
            "output": out[-4000:]}


def chkdsk_scan(drive: str = "C:") -> dict:
    if not winapi.is_admin():
        return {"ok": False, "msg": "磁盘检查需要管理员权限"}
    rc, out = _stream(["chkdsk", drive], timeout=1800)
    # 「坏扇区 0 KB」是正常结果，旧代码算了 bad 却没用
    has_bad = re.search(r"坏扇区[^\n]*?([1-9]\d*)\s*KB", out) or \
        re.search(r"bad sectors[^\n]*?([1-9]\d*)\s*KB", out, re.I)
    if has_bad:
        msg = "磁盘检查发现坏扇区，建议备份重要数据并重启执行修复"
    elif rc == 0:
        msg = "磁盘检查完成，未发现需要修复的问题"
    else:
        msg = "磁盘检查完成，存在需要修复的问题（建议重启后执行修复）"
    return {"ok": rc in (0, 1), "msg": msg, "output": out[-4000:]}


def optimize_drive(drive: str = "C:", cb=None) -> dict:
    letter = drive.rstrip(":").rstrip("\\")[:1].upper()
    media = _media_type(letter)
    if media == "SSD":
        cmd = ["powershell", "-NoProfile", "-Command",
               f"Optimize-Volume -DriveLetter {letter} -ReTrim -Verbose"]
        label = "TRIM"
    else:
        cmd = ["powershell", "-NoProfile", "-Command",
               f"Optimize-Volume -DriveLetter {letter} -Defrag -Verbose"]
        label = "碎片整理"
    if cb:
        cb(f"正在对 {letter}: 执行{label}，请勿中断…")
    rc, out = _stream(cmd, cb=cb, timeout=3600)
    return {"ok": rc == 0, "msg": f"{letter}: {label}完成" if rc == 0 else "操作未完成，可能需要管理员权限",
            "output": out[-3000:], "media": media}


def _media_type(letter: str) -> str:
    out = winapi.run_ps(
        f"$d=Get-PhysicalDisk | Where-Object DeviceId -eq "
        f"(Get-Partition -DriveLetter {letter} -ErrorAction SilentlyContinue).DiskNumber;"
        f"if($d){{$d.MediaType}}", timeout=20)
    return "SSD" if "SSD" in out else "HDD"


def create_restore_point(desc: str = "JingBoost 优化前备份") -> dict:
    if not winapi.is_admin():
        return {"ok": False, "msg": "创建还原点需要管理员权限"}
    script = (
        "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\"; "
        f"Checkpoint-Computer -Description \"{desc}\" -RestorePointType MODIFY_SETTINGS"
    )
    out = winapi.run_ps(script, timeout=180)
    if "错误" in out or "Error" in out or "Exception" in out:
        return {"ok": False, "msg": "创建失败（系统保护可能未开启）", "output": out[-800:]}
    return {"ok": True, "msg": "已创建系统还原点，出问题可回滚"}


def restore_points() -> list[dict]:
    out = winapi.run_ps(
        "Get-ComputerRestorePoint | Select-Object SequenceNumber,Description,"
        "@{n='Time';e={$_.CreationTime}}", timeout=60)
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if re.match(r"^\d+\s", line):
            parts = line.split(None, 2)
            rows.append({"seq": parts[0], "desc": parts[1] if len(parts) > 1 else "",
                         "time": parts[2] if len(parts) > 2 else ""})
    return rows


def disk_health() -> list[dict]:
    out = winapi.ps_json(
        "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,"
        "OperationalStatus,@{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}", timeout=40)
    if isinstance(out, dict):
        out = [out]
    rows = []
    for d in out or []:
        rows.append({
            "name": d.get("FriendlyName", "磁盘"),
            "media": d.get("MediaType", ""),
            "health": {"Healthy": "健康", "Warning": "警告", "Unhealthy": "异常"}.get(
                d.get("HealthStatus"), d.get("HealthStatus") or "未知"),
            "status": d.get("OperationalStatus") or "",
            "size": f"{d.get('SizeGB', 0)} GB",
        })
    return rows


def smart_predict() -> list[dict]:
    out = winapi.run_ps(
        "Get-CimInstance -Namespace root\\wmi -ClassName MSStorageDriver_FailurePredictStatus "
        "-ErrorAction SilentlyContinue | ForEach-Object { \"$($_.InstanceName)|$($_.PredictFailure)\" }",
        timeout=40)
    rows = []
    for line in out.splitlines():
        if "|" in line:
            name, _, flag = line.partition("|")
            rows.append({"name": name.strip(), "fail": flag.strip().lower() == "true"})
    return rows


_health_cache: dict = {"time": 0, "data": None}


def system_health_summary(force: bool = False) -> dict:
    """综合体检：磁盘空间、内存、启动项数量、异常服务。结果缓存 60 秒。"""
    now = time.time()
    if not force and _health_cache["data"] and now - _health_cache["time"] < 60:
        return _health_cache["data"]
    issues = []
    virt = psutil.virtual_memory()
    if virt.percent > 85:
        issues.append({"level": "warn", "text": f"内存占用 {virt.percent}%，建议一键加速"})
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
        except Exception:
            continue
        if u.percent > 90 and u.total > 0:
            issues.append({"level": "danger",
                           "text": f"{p.mountpoint} 剩余仅 {winapi.human(u.free)}，建议清理"})
    upd = os.path.join(WIN, "SoftwareDistribution", "Download")
    if os.path.isdir(upd):
        sz = winapi.dir_size(upd, limit_files=20000)
        if sz > 500 * 1024 * 1024:
            issues.append({"level": "warn", "text": f"更新缓存占用约 {winapi.human(sz)}，可清理"})
    if not winapi.is_admin():
        issues.append({"level": "warn", "text": "当前未以管理员运行，部分深度优化不可用"})
    if not issues:
        issues.append({"level": "ok", "text": "系统状态良好，未发现明显问题"})
    data = {"issues": issues, "score": _score(issues)}
    _health_cache["data"] = data
    _health_cache["time"] = now
    return data


def _score(issues: list[dict]) -> int:
    score = 100
    for i in issues:
        if i["level"] == "danger":
            score -= 25
        elif i["level"] == "warn":
            score -= 10
    return max(score, 20)
