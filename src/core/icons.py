"""程序图标提取：Shell API -> HICON -> PNG(dataURL)，带内存缓存。"""
from __future__ import annotations

import base64
import ctypes
import io
import os
from ctypes import wintypes

from PIL import Image

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001
SHGFI_USEFILEATTRIBUTES = 0x000000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
DIB_RGB_COLORS = 0


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", wintypes.LONG),
        ("bmWidth", wintypes.LONG),
        ("bmHeight", wintypes.LONG),
        ("bmWidthBytes", wintypes.LONG),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


shell32.SHGetFileInfoW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW),
    wintypes.UINT, wintypes.UINT,
]
shell32.SHGetFileInfoW.restype = ctypes.c_size_t
shell32.ExtractIconExW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
    ctypes.POINTER(wintypes.HICON), wintypes.UINT,
]
shell32.ExtractIconExW.restype = wintypes.UINT

user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

gdi32.GetObjectW.argtypes = [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

_cache: dict[str, str] = {}
_MAX = 1200


def _hicon_png(hicon: int, px: int = 32) -> str | None:
    """HICON -> PNG base64 dataURL。"""
    info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None
    hbm = info.hbmColor
    try:
        if not hbm:
            return None
        bm = BITMAP()
        if not gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
            return None
        w, h = int(bm.bmWidth), int(bm.bmHeight)
        if w <= 0 or h <= 0 or w > 512 or h > 512:
            return None
        hdc = user32.GetDC(None)
        memdc = gdi32.CreateCompatibleDC(hdc)
        old = gdi32.SelectObject(memdc, hbm)
        try:
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h           # 负数 = 自上而下
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0
            buf = ctypes.create_string_buffer(w * h * 4)
            got = gdi32.GetDIBits(memdc, hbm, 0, h, buf,
                                  ctypes.byref(bmi), DIB_RGB_COLORS)
            if not got:
                return None
            img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
        finally:
            if old:
                gdi32.SelectObject(memdc, old)
            gdi32.DeleteDC(memdc)
            user32.ReleaseDC(None, hdc)
        if px and (w != px or h != px):
            img = img.resize((px, px), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=False, compress_level=6)
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    except Exception:
        return None
    finally:
        for hbm_del in (info.hbmMask, info.hbmColor):
            if hbm_del:
                gdi32.DeleteObject(hbm_del)
        user32.DestroyIcon(hicon)


def _extract_hicon(path: str) -> int | None:
    """优先用 ExtractIconEx（对 exe/lnk 最可靠），失败再退回 SHGetFileInfo。"""
    large = wintypes.HICON()
    small = wintypes.HICON()
    try:
        n = shell32.ExtractIconExW(path, 0, ctypes.byref(large), ctypes.byref(small), 1)
        if n and large.value:
            if small.value:
                user32.DestroyIcon(small)
            return int(large.value)
        if small.value:
            return int(small.value)
    except Exception:
        pass
    # 文件类型通用图标（如系统目录下的特殊文件）
    try:
        info = SHFILEINFOW()
        shell32.SHGetFileInfoW(path, FILE_ATTRIBUTE_NORMAL, ctypes.byref(info),
                               ctypes.sizeof(info), SHGFI_ICON | SHGFI_LARGEICON)
        if info.hIcon:
            return int(info.hIcon)
    except Exception:
        pass
    return None


def icon_for(path: str, px: int = 32) -> str | None:
    """取文件图标（PNG dataURL），失败返回 None。"""
    if not path:
        return None
    key = f"{path.lower()}|{px}"
    if key in _cache:
        return _cache[key]
    result = None
    try:
        if os.path.exists(path):
            hicon = _extract_hicon(path)
            if hicon:
                result = _hicon_png(hicon, px)
    except Exception:
        result = None
    if len(_cache) > _MAX:
        _cache.clear()
    _cache[key] = result
    return result


def icons_for(paths: list[str], px: int = 32, limit: int = 140) -> dict:
    """批量取图标，返回 {小写路径: dataURL}。"""
    out: dict[str, str] = {}
    seen = set()
    for p in paths:
        if not p:
            continue
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        if len(seen) > limit:
            break
        icon = icon_for(p, px)
        if icon:
            out[k] = icon
    return out
