"""网络优化与网络诊断。"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import time

import psutil

from . import winapi

WIN = os.environ.get("SystemRoot", r"C:\Windows")
HOSTS = os.path.join(WIN, r"System32\drivers\etc\hosts")
HOSTS_BAK = HOSTS + ".jingboost.bak"


def flush_dns() -> dict:
    rc, out = winapi.run_hidden(["ipconfig", "/flushdns"], timeout=25)
    return {"ok": rc == 0, "msg": "DNS 缓存已刷新" if rc == 0 else out}


def reset_winsock() -> dict:
    rc, out = winapi.run_hidden(["netsh", "winsock", "reset"], timeout=40)
    return {"ok": rc == 0,
            "msg": "Winsock 已重置，重启电脑后生效" if rc == 0 else out}


def reset_tcpip() -> dict:
    rc, out = winapi.run_hidden(["netsh", "int", "ip", "reset"], timeout=60)
    return {"ok": rc == 0,
            "msg": "TCP/IP 协议栈已重置，建议重启电脑" if rc == 0 else out}


def optimize_tcp() -> dict:
    steps = [
        (["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"], "接收窗口自动调优"),
        (["netsh", "int", "tcp", "set", "global", "rss=enabled"], "RSS 多核处理"),
        (["netsh", "int", "tcp", "set", "global", "ecn=default"], "ECN 拥塞控制"),
        (["netsh", "int", "tcp", "set", "global", "timestamps=default"], "时间戳"),
    ]
    done = []
    fail = []
    for cmd, label in steps:
        rc, _ = winapi.run_hidden(cmd, timeout=25)
        (done if rc == 0 else fail).append(label)
    return {"ok": True, "done": done, "failed": fail,
            "msg": f"已应用 {len(done)} 项 TCP 优化"}


DEFAULT_PING_TARGETS = [
    ("百度", "www.baidu.com", 443),
    ("腾讯", "www.qq.com", 443),
    ("阿里 DNS", "223.5.5.5", 53),
    ("114 DNS", "114.114.114.114", 53),
    ("GitHub", "github.com", 443),
    ("Google", "www.google.com", 443),
    ("Google DNS", "8.8.8.8", 53),
]


def ping_latency(hosts=None) -> list[dict]:
    """TCP 握手测延迟，比 ICMP 更能反映真实可用性。

    各节点并发探测：新增 Google / Google DNS 后，即使这两个节点不可达，
    整轮测速也只取决于最慢的那个节点，而不是所有超时时间相加。
    """
    from concurrent.futures import ThreadPoolExecutor

    if hosts:
        targets = [h if isinstance(h, (tuple, list)) and len(h) == 3
                   else (str(h), str(h), 443) for h in hosts]
    else:
        targets = DEFAULT_PING_TARGETS

    def probe(target) -> dict:
        label, host, port = target
        times: list[float] = []
        err = ""
        try:
            ip = socket.gethostbyname(host)
        except Exception as exc:  # noqa: BLE001
            ip, err = host, str(exc)
        for _ in range(3):
            try:
                t0 = time.perf_counter()
                s = socket.create_connection((ip, port), timeout=1.6)
                s.close()
                times.append((time.perf_counter() - t0) * 1000)
                err = ""
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
            time.sleep(0.05)
        if times:
            return {"label": label, "host": host,
                    "ms": round(sum(times) / len(times), 1),
                    "min": round(min(times), 1), "ok": True, "err": ""}
        return {"label": label, "host": host, "ms": None, "min": None,
                "ok": False, "err": err}

    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
        return list(pool.map(probe, targets))


def adapters() -> list[dict]:
    stats = psutil.net_if_stats()
    io = psutil.net_io_counters(pernic=True)
    addrs = psutil.net_if_addrs()
    rows = []
    for name, st in stats.items():
        if name.lower().startswith(("loopback", "lo")):
            continue
        i = io.get(name)
        ip = ""
        for a in addrs.get(name, []):
            if a.family == socket.AF_INET:
                ip = a.address
                break
        rows.append({
            "name": name,
            "up": st.isup,
            # 未连接的虚拟网卡本来就没有速率概念，用「—」而不是「未知」，
            # 避免被误读成"采集失败"。
            "speed": f"{st.speed} Mbps" if st.speed else "—",
            "ip": ip,
            "recv": winapi.human(i.bytes_recv) if i else "-",
            "sent": winapi.human(i.bytes_sent) if i else "-",
        })
    rows.sort(key=lambda r: not r["up"])
    return rows


def dns_servers() -> list[str]:
    """当前生效的 DNS 服务器。

    优先读注册表（TCPIP\\Parameters\\Interfaces\\{GUID}\\NameServer），
    数值精确、零开销；解析 ipconfig 文本会把子网掩码 / 默认网关一起抓进来。
    """
    import winreg

    base = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    uniq: list[str] = []

    def _add(raw: str) -> None:
        for piece in re.split(r"[,;\s]+", str(raw or "")):
            piece = piece.strip()
            if not piece:
                continue
            try:
                ipaddress.ip_address(piece)
            except ValueError:
                continue
            if piece.startswith(("127.", "0.")) or piece in uniq:
                continue
            uniq.append(piece)

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        got = False
                        for name in ("NameServer", "DhcpNameServer"):
                            try:
                                v, _ = winreg.QueryValueEx(k, name)
                            except OSError:
                                continue
                            if str(v or "").strip():
                                _add(v)
                                got = True
                        # 只看实际启用（有 IP）的接口，避免把断开的虚拟网卡算进来
                        if not got:
                            continue
                except OSError:
                    continue
    except OSError:
        pass
    return uniq[:8]


def set_dns(primary: str, secondary: str = "") -> dict:
    """给所有已连接的有线/无线网卡设置 DNS。"""
    ok, fail = 0, []
    for a in adapters():
        if not a["up"]:
            continue
        rc, out = winapi.run_hidden(["netsh", "interface", "ip", "set", "dns",
                                     f'name={a["name"]}', "static", primary],
                                    timeout=25)
        if rc != 0:
            # 之前不看返回码，netsh 失败也照样报「已为 N 个网卡设置」，
            # 用户会以为改成功了其实没变（多半是没管理员权限）
            fail.append(a["name"])
            continue
        if secondary:
            winapi.run_hidden(["netsh", "interface", "ip", "add", "dns",
                               f'name={a["name"]}', secondary, "index=2"], timeout=25)
        ok += 1
    if ok:
        winapi.run_hidden(["ipconfig", "/flushdns"], timeout=20)
    if not ok and fail:
        return {"ok": False,
                "msg": f"设置失败（{len(fail)} 个网卡被拒绝），请以管理员身份运行"}
    return {"ok": ok > 0, "msg": f"已为 {ok} 个网卡设置 DNS 为 {primary}"}


def reset_dns_dhcp() -> dict:
    ok = 0
    fail = 0
    for a in adapters():
        if not a["up"]:
            continue
        rc, _ = winapi.run_hidden(["netsh", "interface", "ip", "set", "dns",
                                   f'name={a["name"]}', "dhcp"], timeout=25)
        if rc == 0:
            ok += 1
        else:
            fail += 1
    if ok:
        winapi.run_hidden(["ipconfig", "/flushdns"], timeout=20)
    if not ok:
        return {"ok": False,
                "msg": "恢复失败，请以管理员身份运行" if fail else "没有处于连接状态的网卡"}
    return {"ok": True, "msg": f"已恢复 {ok} 个网卡为自动获取 DNS"}


def connections_count() -> int:
    try:
        return len(psutil.net_connections(kind="inet"))
    except Exception:
        return 0


def hosts_info() -> dict:
    try:
        with open(HOSTS, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc), "content": "", "active": 0}
    lines = content.splitlines()
    active = [l for l in lines if l.strip() and not l.strip().startswith("#")]
    return {"ok": True, "content": content, "active": len(active),
            "backup": os.path.exists(HOSTS_BAK)}


def clean_hosts() -> dict:
    info = hosts_info()
    if not info["ok"]:
        return {"ok": False, "msg": info["msg"]}
    try:
        if not os.path.exists(HOSTS_BAK):
            with open(HOSTS_BAK, "w", encoding="utf-8") as f:
                f.write(info["content"])
        default = (
            "# Copyright (c) 1993-2009 Microsoft Corp.\n"
            "#\n"
            "# This is a sample HOSTS file used by Microsoft TCP/IP for Windows.\n"
            "#\n"
            "# 由 景同志·电脑加速 重置\n"
            "127.0.0.1 localhost\n"
            "::1 localhost\n"
        )
        with open(HOSTS, "w", encoding="utf-8") as f:
            f.write(default)
        winapi.run_hidden(["ipconfig", "/flushdns"], timeout=20)
        return {"ok": True, "msg": "hosts 已重置为默认（原文件已备份）"}
    except PermissionError:
        return {"ok": False, "msg": "权限不足，请以管理员身份运行"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc)}


def restore_hosts() -> dict:
    if not os.path.exists(HOSTS_BAK):
        return {"ok": False, "msg": "没有找到备份文件"}
    try:
        with open(HOSTS_BAK, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        with open(HOSTS, "w", encoding="utf-8") as f:
            f.write(data)
        return {"ok": True, "msg": "hosts 已恢复备份"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "msg": str(exc)}
