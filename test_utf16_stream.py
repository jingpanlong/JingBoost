# -*- coding: utf-8 -*-
"""临时测试：验证 repair._stream 对 UTF-16LE 输出的逐行解码（修复 SFC 乱码）。"""
import sys, os

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
from core import repair, winapi  # noqa: E402

DEBUG = "--debug" in sys.argv

child = r'''
import sys, time
lines = [
    "开始执行",
    "____ 验证 15%",
    "____ 验证 100%",
    "Windows 资源保护未找到任何完整性冲突",
]
for l in lines:
    sys.stdout.buffer.write(l.encode("utf-16-le") + b"\x0d\x00\x0a\x00")
    sys.stdout.flush()
    time.sleep(0.05)
'''
child_path = os.path.join(ROOT, "_child_utf16.py")
with open(child_path, "w", encoding="utf-8") as f:
    f.write(child)


def debug_stream(cmd, timeout=30):
    """复刻 _stream 的行处理，打印每一步的字节。"""
    import subprocess, time
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    out = []
    for raw in p.stdout:
        b = raw
        if b.endswith(b"\n"):
            b = b[:-1]
        b = b.rstrip(b"\x00\r")
        print("RAW :", raw.hex(" "))
        print("STRIP:", b.hex(" "), "| len", len(b), "| odd", len(b) % 2,
              "| nul", b.count(0))
        if b.count(0) and len(b) % 2 and b.startswith(b"\x00"):
            b = b[1:]
            print(" ->剥前导00:", b.hex(" "))
        if b.count(0) * 3 >= max(len(b), 8):
            text = winapi._try_utf16le(b)
            print(" ->utf16:", None if text is None else text)
            if text is None:
                print(" ->fallback:", winapi._decode_text(b))
        else:
            print(" ->oem/utf8:", winapi._decode_text(b))
        out.append(raw)
        if len(out) > 5:
            break
    p.kill()


try:
    if DEBUG:
        debug_stream([sys.executable, child_path])
    else:
        got = []
        rc, full = repair._stream([sys.executable, child_path], cb=got.append, timeout=30)
        print("--- 逐行输出 ---")
        for l in got:
            print(" ", l)
        need = ["开始执行", "验证 100%", "未找到任何完整性冲突"]
        ok = all(any(n in l for l in got) for n in need)
        print("LINES:", len(got), "| STREAM:", "PASS" if ok else "FAIL")
finally:
    os.remove(child_path)
