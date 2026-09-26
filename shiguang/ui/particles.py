# -*- coding: utf-8 -*-
"""粒子动画：任务全部完成时的暖金粒子迸发。

为什么用"透明色键 Toplevel"而不是盖在窗口上的 Canvas（这是第一版踩的坑）
--------------------------------------------------------------------
tkinter 的控件**没有 alpha 通道**。第一版在主窗口上 ``place`` 了一个铺满的 Canvas，
它的不透明底色 ``#FAF7F2`` 直接把整个界面糊住了 —— 动画的 1.5 秒里用户完全看不到
任务卡片，截图回来只有一片底色加几个小点。这个方案从根上不成立。

Windows 支持给窗口设**透明色键**（``-transparentcolor``）：把一个魔法色声明为
全透明。于是做法变成：无边框置顶小窗口 + 魔法色底 + 上面画粒子，
粒子浮在界面之上，其余区域完全透明。这才是"浮层粒子"在 tkinter 里唯一干净的实现。

其余三个关键设计
----------------
1. **对象池**：一次性建好 ``MAX_PARTICLES`` 个 oval item，之后每次播放只改
   ``coords`` / ``fill``，绝不 ``create_oval`` / ``delete``。连点十几次也不会掉帧。
2. **时间驱动而非帧计数**：每帧按真实经过的毫秒算位置，某帧迟到也不会让轨迹跳变。
3. **淡出用"收缩 + 降色温"**：没有 alpha 就不能向背景插值（背景是透明的，
   插值会露出色块），改成半径收缩到 0 + 颜色向浅金靠拢，观感是火花自然熄灭。

窗口不可见（最小化到托盘）时动画自行中止 —— 在看不见的窗口上跑 90 帧纯属白烧 CPU。
"""

from __future__ import annotations

import logging
import math
import random
import time
import tkinter as tk
from typing import List, Optional

from .. import theme

log = logging.getLogger("shiguang.particles")

# 品牌暖色系：金色 / 柔橘 / 暖黄 / 浅金
PARTICLE_COLORS = ["#E8B84A", "#E89A4A", "#F5D76E", "#FCEABB"]

MAX_PARTICLES = 30
FRAME_MS = 16                  # ≈60fps，一帧工作量远低于 16ms 的预算
DURATION_MS = 1500             # 粒子总时长，配合文案控制在 2 秒内


class _Particle:
    """一个粒子的运动状态（纯数据，便于复用）。"""

    __slots__ = ("x", "y", "vx", "vy", "color", "radius", "alive", "delay")

    def __init__(self) -> None:
        self.x = self.y = 0.0
        self.vx = self.vy = 0.0
        self.color = PARTICLE_COLORS[0]
        self.radius = 2.0
        self.alive = False
        self.delay = 0.0       # 错峰起飞，避免所有粒子糊成一团


class ParticleLayer:
    """粒子迸发层（透明置顶浮窗，可反复播放）。"""

    # 魔法色：粒子配色里绝不能出现它，否则那部分会被"抠掉"
    KEY_COLOR = "#FF00FF"

    def __init__(self, master: tk.Misc) -> None:
        self.master = master
        self._win: Optional[tk.Toplevel] = None
        self._canvas: Optional[tk.Canvas] = None
        self._items: List[int] = []
        self._pool: List[_Particle] = [_Particle() for _ in range(MAX_PARTICLES)]
        self._job: Optional[str] = None
        self._playing = False
        self._start = 0.0
        self._width = 0
        self._height = 0
        self._last_pos = (0, 0)
        self._supported: Optional[bool] = None   # None = 还没试过

    # ------------------------------------------------------------------
    # 浮窗
    # ------------------------------------------------------------------
    @property
    def supported(self) -> bool:
        """透明浮层是否可用（不支持的平台/系统上会主动放弃动画）。"""
        if self._supported is None:
            self._ensure_overlay()
        return bool(self._supported)

    def _ensure_overlay(self) -> bool:
        """按需创建透明浮窗；失败则永久降级（不再每次播放都重试）。"""
        if self._win is not None:
            return True
        if self._supported is False:
            return False
        try:
            win = tk.Toplevel(self.master)
            win.withdraw()                       # 先藏起来，避免创建时闪一下
            win.overrideredirect(True)           # 去掉标题栏/边框，也不进任务栏
            win.configure(bg=self.KEY_COLOR)
            win.attributes("-topmost", True)
            # 关键一步：把魔法色声明为全透明。非 Windows 平台会抛 TclError
            win.attributes("-transparentcolor", self.KEY_COLOR)
            canvas = tk.Canvas(win, bg=self.KEY_COLOR,
                               highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
            self._win, self._canvas = win, canvas
            # 对象池：item 只建一次，之后永远复用
            self._items = [
                canvas.create_oval(0, 0, 0, 0, fill="", outline="")
                for _ in range(MAX_PARTICLES)
            ]
            self._supported = True
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("透明粒子浮层不可用，已降级为不出动画：%s", exc)
            self._supported = False
            self._win = self._canvas = None
            return False

    # ------------------------------------------------------------------
    def stop(self) -> None:
        if self._job is not None:
            try:
                self.master.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None
        self._playing = False
        if self._canvas is not None:
            for item in self._items:
                try:
                    self._canvas.itemconfigure(item, fill="")
                except Exception:  # noqa: BLE001
                    pass
        if self._win is not None:
            try:
                self._win.withdraw()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def play(self, count: int = 26, origin: Optional[tuple] = None) -> bool:
        """播放一次粒子迸发，返回是否真的播起来了。

        ``count``：粒子数（分组完成时用一半左右，见需求 14）。
        ``origin``：相对窗口中心的偏移 ``(dx, dy)``。
        """
        self.stop()
        if not self._ensure_overlay():
            return False

        master = self.master
        try:
            root_x, root_y = master.winfo_rootx(), master.winfo_rooty()
            host_w, host_h = master.winfo_width(), master.winfo_height()
        except Exception:  # noqa: BLE001
            return False
        # 窗口还没完成布局时不要硬播（winfo_width 会是 1）
        if host_w <= 1 or host_h <= 1:
            return False

        try:
            # DPI 感知进程里 winfo_* 返回的就是物理像素，直接喂给 geometry 即可对齐
            self._win.geometry(f"{host_w}x{host_h}+{root_x}+{root_y}")
            self._win.deiconify()
            self._win.attributes("-topmost", True)
            self._win.update_idletasks()
        except Exception as exc:  # noqa: BLE001
            log.info("粒子浮层定位失败：%s", exc)
            return False

        self._width, self._height = host_w, host_h
        self._last_pos = (root_x, root_y)

        count = max(0, min(int(count), MAX_PARTICLES))
        if count == 0:
            self._win.withdraw()
            return False

        cx = self._width / 2 + (origin[0] if origin else 0)
        cy = self._height / 2 + (origin[1] if origin else 0)

        for i, p in enumerate(self._pool):
            if i >= count:
                p.alive = False
                self._canvas.itemconfigure(self._items[i], fill="")
                continue
            angle = random.uniform(0, math.tau)
            speed = random.uniform(150.0, 340.0)
            p.x, p.y = cx, cy
            p.vx = math.cos(angle) * speed
            p.vy = math.sin(angle) * speed - 60.0      # 略微向上，更像"迸发"
            p.color = random.choice(PARTICLE_COLORS)
            p.radius = random.uniform(1.8, 3.6)
            p.delay = random.uniform(0.0, 0.12)
            p.alive = True

        self._playing = True
        self._start = time.monotonic()
        self._step()
        return True

    # ------------------------------------------------------------------
    def _follow_master(self) -> None:
        """主窗口被拖动时让浮层跟上（1.5 秒里也可能发生）。"""
        try:
            pos = (self.master.winfo_rootx(), self.master.winfo_rooty())
            if pos != self._last_pos:
                self._last_pos = pos
                self._win.geometry(f"{self._width}x{self._height}+{pos[0]}+{pos[1]}")
        except Exception:  # noqa: BLE001
            pass

    def _step(self) -> None:
        if not self._playing:
            return
        try:
            if not self._win.winfo_exists():
                self.stop()
                return
            # 主窗口被藏起来（最小化到托盘）就放弃这一轮，不空转
            if not self.master.winfo_viewable():
                self.stop()
                return
        except Exception:  # noqa: BLE001
            self.stop()
            return

        self._follow_master()

        elapsed = time.monotonic() - self._start
        t = elapsed * 1000.0 / DURATION_MS
        if t >= 1.0:
            self.stop()
            return

        gravity = 460.0
        damping = 0.62

        for i, p in enumerate(self._pool):
            item = self._items[i]
            if not p.alive:
                continue
            local = elapsed - p.delay
            if local <= 0:
                self._canvas.itemconfigure(item, fill="")
                continue

            # 位置：匀加速 + 水平阻尼（近似即可，视觉上够自然）
            x = p.x + p.vx * local * (1.0 - damping * local * 0.5)
            y = p.y + p.vy * local + 0.5 * gravity * local * local

            # "淡出"：半径收缩 + 色温向浅金靠拢（没有 alpha 就用这两招模拟熄灭）
            life = max(0.0, min(1.0, (local * 1000.0) / (DURATION_MS * 0.85)))
            r = p.radius * max(0.0, 1.0 - life * life)
            color = theme.mix(p.color, "#FCEABB", min(1.0, life * 0.7))

            try:
                if r < 0.25:
                    self._canvas.itemconfigure(item, fill="")
                else:
                    self._canvas.coords(item, x - r, y - r, x + r, y + r)
                    self._canvas.itemconfigure(item, fill=color, outline="")
            except Exception:  # noqa: BLE001
                p.alive = False

        self._job = self.master.after(FRAME_MS, self._step)

    # ------------------------------------------------------------------
    def destroy(self) -> None:
        self.stop()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._win = self._canvas = None
