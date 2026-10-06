# -*- coding: utf-8 -*-
"""暖金粒子动画：原生窗口区域只包含活跃粒子的圆形。

每帧复用 Canvas 的粒子对象，并把窗口裁切为这些圆形的并集。
不使用颜色键、投影或整块不透明覆盖层；切走应用即停止动画。
"""

from __future__ import annotations

import logging
import math
import random
import time
import tkinter as tk
from typing import List, Optional

from .. import theme
from .window_shape import apply_particle_region, attach_native_owner

log = logging.getLogger("shiguang.particles")

# 品牌暖色系：金色 / 柔橘 / 暖黄 / 浅金
PARTICLE_COLOR_KEYS = ("accent", "orange", "particle_spark", "particle_fade")

MAX_PARTICLES = theme.PARTICLE_MAX
FRAME_MS = theme.PARTICLE_FRAME_MS                  # ≈60fps，一帧工作量远低于 16ms 的预算
DURATION_MS = theme.PARTICLE_DURATION_MS             # 粒子总时长，配合文案控制在 2 秒内


class _Particle:
    """一个粒子的运动状态（纯数据，便于复用）。"""

    __slots__ = ("x", "y", "vx", "vy", "color", "radius", "alive", "delay")

    def __init__(self) -> None:
        self.x = self.y = 0.0
        self.vx = self.vy = 0.0
        self.color = theme.c(PARTICLE_COLOR_KEYS[0])
        self.radius = 2.0
        self.alive = False
        self.delay = 0.0       # 错峰起飞，避免所有粒子糊成一团


class ParticleLayer:
    """粒子迸发层（透明置顶浮窗，可反复播放）。"""

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
            win.configure(bg=theme.c("accent"))
            win.transient(self.master.winfo_toplevel())
            if not apply_particle_region(win, []):
                win.destroy()
                self._supported = False
                return False
            canvas = tk.Canvas(win, bg=theme.c("accent"),
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
            apply_particle_region(self._win, [])
            self._win.deiconify()
            self._win.update_idletasks()
            attach_native_owner(self._win, master)
            self._win.attributes("-topmost", bool(getattr(master, "store", None) and
                                 master.store.settings.get("always_on_top", False)))
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
            p.color = theme.c(random.choice(PARTICLE_COLOR_KEYS))
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
            if (not self.master.winfo_viewable()
                    or not getattr(self.master, "is_foreground", lambda: True)()):
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

        bounds = []
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
            color = theme.mix(p.color, theme.c("particle_fade"), min(1.0, life * 0.7))

            try:
                if r < 0.25:
                    self._canvas.itemconfigure(item, fill="")
                else:
                    bounds.append((x - r, y - r, x + r, y + r))
                    self._canvas.coords(item, x - r, y - r, x + r, y + r)
                    self._canvas.itemconfigure(item, fill=color, outline="")
            except Exception:  # noqa: BLE001
                p.alive = False

        if not apply_particle_region(self._win, bounds):
            self.stop()
            return
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
