# -*- coding: utf-8 -*-
"""运行时资源生成：应用图标与番茄钟提示音。

思路：不把二进制资源塞进仓库/安装包，而是在首次运行时用纯 Python 生成，
      既减小打包体积，也避免路径丢失（打包后的 _MEIPASS 问题）。
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Tuple

from .config import assets_dir

ICON_PNG = "icon.png"
ICON_ICO = "icon.ico"
CHIME_WAV = "chime.wav"

APP_BG = (250, 247, 242, 255)      # #FAF7F2
SUN_CORE = (232, 184, 74, 255)     # #E8B84A
SUN_GLOW = (246, 222, 160, 255)    # 光晕
RAY = (232, 154, 74, 255)          # #E89A4A


# --------------------------------------------------------------------------
# 图标
# --------------------------------------------------------------------------
def _draw_icon(size: int):
    """画一个"暖阳"图标：柔和底色 + 中心太阳 + 八道光芒。"""
    from PIL import Image, ImageDraw

    scale = 4  # 超采样再缩小，得到平滑边缘
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.ellipse((0, 0, s - 1, s - 1), fill=APP_BG)

    cx = cy = s / 2
    halo_r = s * 0.30
    core_r = s * 0.195

    # 光晕（两层，模拟柔光）
    draw.ellipse(
        (cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r),
        fill=(246, 230, 190, 255),
    )
    draw.ellipse(
        (cx - core_r * 1.32, cy - core_r * 1.32, cx + core_r * 1.32, cy + core_r * 1.32),
        fill=SUN_GLOW,
    )
    # 太阳本体
    draw.ellipse((cx - core_r, cy - core_r, cx + core_r, cy + core_r), fill=SUN_CORE)

    # 八道光芒
    ray_inner = core_r * 1.62
    ray_outer = s * 0.44
    width = max(2, int(s * 0.035))
    for i in range(8):
        ang = math.radians(i * 45 + 22.5)
        x1 = cx + math.cos(ang) * ray_inner
        y1 = cy + math.sin(ang) * ray_inner
        x2 = cx + math.cos(ang) * ray_outer
        y2 = cy + math.sin(ang) * ray_outer
        draw.line((x1, y1, x2, y2), fill=RAY, width=width)

    return img.resize((size, size), Image.LANCZOS)


def ensure_icon() -> Tuple[Path, Path]:
    """生成/复用 PNG 与 ICO 图标，返回 (png, ico)。"""
    folder = assets_dir()
    png = folder / ICON_PNG
    ico = folder / ICON_ICO
    if png.exists() and ico.exists():
        return png, ico
    try:
        img = _draw_icon(256)
        img.save(png, "PNG")
        img.resize((256, 256)).save(
            ico, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        )
    except Exception:  # noqa: BLE001 - 没有 Pillow 也不该阻断启动
        pass
    return png, ico


# --------------------------------------------------------------------------
# 提示音
# --------------------------------------------------------------------------
def _soft_tone(freq: float, seconds: float, rate: int, amplitude: float = 0.22,
               harmonics: Tuple[float, ...] = (1.0, 0.28, 0.10)) -> bytes:
    """生成一段带指数衰减包络的柔和音（避免刺耳的方波/突兀起音）。"""
    total = int(rate * seconds)
    frames = bytearray()
    attack = max(1, int(rate * 0.012))     # 12ms 淡入，消除爆音
    for n in range(total):
        t = n / rate
        env = math.exp(-2.6 * t)
        if n < attack:
            env *= n / attack
        elif n > total - attack:
            env *= max(0.0, (total - n) / attack)
        value = 0.0
        for k, amp in enumerate(harmonics, start=1):
            value += amp * math.sin(2 * math.pi * freq * k * t)
        sample = int(max(-1.0, min(1.0, value * amplitude * env)) * 32767)
        frames += struct.pack("<h", sample)
    return bytes(frames)


def ensure_chime() -> Path:
    """生成轻柔的"叮——咚"两声提示音（纯 Python 合成，无外部音频文件）。"""
    path = assets_dir() / CHIME_WAV
    if path.exists():
        return path
    try:
        rate = 22050
        parts = [
            _soft_tone(587.33, 0.75, rate),   # D5
            _soft_tone(880.00, 0.95, rate),   # A5
        ]
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"".join(parts))
    except Exception:  # noqa: BLE001
        pass
    return path
