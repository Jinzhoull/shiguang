"""Render Tk label text into a bitmap with the same Windows GDI rasterizer.

Pillow uses grayscale antialiasing for text, while Tk labels on Windows use
ClearType. Drawing the preview with GDI prevents glyphs from changing when
the resize overlay is removed.
"""

from __future__ import annotations

import ctypes
from functools import lru_cache
import sys

from PIL import Image


if sys.platform.startswith("win"):
    class _BitmapHeader(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("width", ctypes.c_int32),
                    ("height", ctypes.c_int32), ("planes", ctypes.c_uint16),
                    ("bpp", ctypes.c_uint16), ("compression", ctypes.c_uint32),
                    ("sizeimage", ctypes.c_uint32), ("xppm", ctypes.c_int32),
                    ("yppm", ctypes.c_int32), ("used", ctypes.c_uint32),
                    ("important", ctypes.c_uint32)]

    class _BitmapInfo(ctypes.Structure):
        _fields_ = [("header", _BitmapHeader), ("colors", ctypes.c_uint32 * 3)]

    _gdi = ctypes.windll.gdi32
    _gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    _gdi.CreateCompatibleDC.restype = ctypes.c_void_p
    _gdi.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.c_void_p, ctypes.c_uint]
    _gdi.CreateDIBSection.restype = ctypes.c_void_p
    _gdi.CreateFontW.argtypes = ([ctypes.c_int] * 5
                                 + [ctypes.c_uint] * 8 + [ctypes.c_wchar_p])
    _gdi.CreateFontW.restype = ctypes.c_void_p
    _gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _gdi.SelectObject.restype = ctypes.c_void_p
    _gdi.DeleteObject.argtypes = [ctypes.c_void_p]
    _gdi.DeleteDC.argtypes = [ctypes.c_void_p]
    _gdi.SetTextColor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    _gdi.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _gdi.TextOutW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_wchar_p, ctypes.c_int]
    _gdi.TextOutW.restype = ctypes.c_int


@lru_cache(maxsize=512)
def label_text_image(text: str, width: int, height: int, family: str,
                     font_size: int, weight: str, slant: str, strike: bool,
                     foreground: str, background: str) -> Image.Image | None:
    """Return an exact Tk-style label image, or None outside Windows."""
    if not sys.platform.startswith("win") or not text or width < 1 or height < 1:
        return None
    try:
        fr, fg, fb = bytes.fromhex(foreground.lstrip("#"))
        br, bg, bb = bytes.fromhex(background.lstrip("#"))
    except ValueError:
        return None
    dc = _gdi.CreateCompatibleDC(None)
    if not dc:
        return None
    info = _BitmapInfo(_BitmapHeader(ctypes.sizeof(_BitmapHeader), width,
                                     -height, 1, 32, 0, width * height * 4,
                                     0, 0, 0, 0))
    bits = ctypes.c_void_p()
    bitmap = _gdi.CreateDIBSection(dc, ctypes.byref(info), 0,
                                    ctypes.byref(bits), None, 0)
    if not bitmap or not bits.value:
        _gdi.DeleteDC(dc)
        return None
    old_bitmap = _gdi.SelectObject(dc, bitmap)
    font = None
    old_font = None
    try:
        background_pixel = bytes((bb, bg, br, 255))
        payload = background_pixel * (width * height)
        ctypes.memmove(bits, payload, len(payload))
        font = _gdi.CreateFontW(
            -abs(int(font_size)), 0, 0, 0, 700 if weight == "bold" else 400,
            1 if slant == "italic" else 0, 0, int(strike), 1, 0, 0, 5, 0,
            family)
        if not font:
            return None
        old_font = _gdi.SelectObject(dc, font)
        _gdi.SetTextColor(dc, fr | (fg << 8) | (fb << 16))
        _gdi.SetBkMode(dc, 1)
        if not _gdi.TextOutW(dc, 0, 0, text, len(text)):
            return None
        return Image.frombytes("RGB", (width, height),
                               ctypes.string_at(bits, width * height * 4),
                               "raw", "BGRX")
    finally:
        if old_font:
            _gdi.SelectObject(dc, old_font)
        if font:
            _gdi.DeleteObject(font)
        _gdi.SelectObject(dc, old_bitmap)
        _gdi.DeleteObject(bitmap)
        _gdi.DeleteDC(dc)
