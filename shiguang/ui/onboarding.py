# -*- coding: utf-8 -*-
"""首次启动的三步引导（覆盖在主窗口之上的浮层）。"""

from __future__ import annotations

import math
import tkinter as tk
from typing import List

import customtkinter as ctk

from .. import icons, theme
from . import widgets

STEPS = [
    {
        "title": "欢迎来到拾光",
        "body": "把每天想做的事写下来，完成一件，\n就当作收藏了一缕阳光。\n\n不用追求全部做完，拾到多少都算数。",
        "button": "下一步",
    },
    {
        "title": "随时叫得出来",
        "body": "按下 Ctrl + Shift + T，\n拾光会立刻出现在你面前，再按一次就藏起来。\n\n点击关闭按钮不会退出，\n它会安静地待在右下角托盘里。",
        "button": "下一步",
    },
    {
        "title": "从第一缕光开始",
        "body": "写下一件今天想完成的小事吧，\n哪怕只是「读十页书」。",
        "focus_prefix": "想专注的时候，点任务右侧的",
        "focus_suffix": "，",
        "focus_followup": "会有 25 分钟安静的陪伴。",
        "button": "开始拾光",
    },
]


class OnboardingOverlay(ctk.CTkFrame):
    """全屏浮层引导。

    尺寸按**最小窗口（310×380）**倒推：插画 108 + 标题 + 四行正文 + 圆点 +
    主按钮 + 跳过，纵向合计 ≈ 350，留一点点余量。上一版插画 150×150、
    顶部留白 48px，在 380 高的窗口里圆点和按钮会被挤到可视区外。
    """

    #: 插画画布边长（逻辑像素）。裸 Canvas 的尺寸是物理像素，所以要过 lpx()。
    SUN_SIZE = theme.ONBOARDING_SUN_SIZE

    def __init__(self, master: tk.Misc, on_done) -> None:
        super().__init__(master, corner_radius=0, fg_color=theme.pair("bg"),
                         border_width=1,
                         border_color=theme.pair("window_border"))
        self.on_done = on_done
        self.index = 0
        self.grid_columnconfigure(0, weight=1)

        side = theme.lpx(self.SUN_SIZE)
        self.sun = tk.Canvas(self, width=side, height=side, bg=theme.c("bg"),
                             highlightthickness=0, bd=0)
        self.sun.grid(row=0, column=0, pady=(theme.lpx(18), theme.lpx(4)))

        self.title_label = widgets.TextLabel(self, text="", font=theme.font("title"),
                                        text_color=theme.pair("text"))
        self.title_label.grid(row=1, column=0, pady=(theme.lpx(2), theme.lpx(6)))

        self.body_label = widgets.TextLabel(self, text="", font=theme.font("body"),
                                       text_color=theme.pair("text_muted"),
                                       justify="center", wraplength=240)
        self.body_label.grid(row=2, column=0, padx=theme.lpx(14))

        self.focus_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.focus_prefix = widgets.TextLabel(
            self.focus_row, text="", font=theme.font("body"),
            text_color=theme.pair("text_muted"),
        )
        self.focus_prefix.pack(side="left")
        self.focus_icon_image = icons.get_ctk("pomodoro", theme.TASK_ICON)
        self.focus_icon = ctk.CTkLabel(
            self.focus_row, text="", image=self.focus_icon_image,
            width=theme.TASK_ICON, height=theme.TASK_ICON,
        )
        self.focus_icon.pack(side="left", padx=theme.lpx(3))
        self.focus_suffix = widgets.TextLabel(
            self.focus_row, text="", font=theme.font("body"),
            text_color=theme.pair("text_muted"),
        )
        self.focus_suffix.pack(side="left")
        self.focus_followup = widgets.TextLabel(
            self, text="", font=theme.font("body"),
            text_color=theme.pair("text_muted"),
        )

        self.dots = tk.Canvas(self, width=theme.lpx(70), height=theme.lpx(14),
                              bg=theme.c("bg"), highlightthickness=0, bd=0)
        self.dots.grid(row=5, column=0, pady=(theme.lpx(10), theme.lpx(4)))

        self.primary = ctk.CTkButton(
            self, text="下一步", width=140, height=28, corner_radius=14,
            fg_color=theme.pair("accent"), hover_color=theme.pair("accent_hover"),
            text_color=theme.ON_ACCENT, font=theme.font("body"),
            command=self.next_step)
        self.primary.grid(row=6, column=0, pady=(theme.lpx(4), theme.lpx(4)))

        self.skip = ctk.CTkButton(
            self, text="跳过", width=90, height=22, corner_radius=11,
            fg_color="transparent", hover_color=theme.pair("ghost"),
            text_color=theme.pair("text_done"), font=theme.font("tiny"),
            command=self.finish)
        self.skip.grid(row=7, column=0, pady=(0, theme.lpx(10)))

        self._render()

    # ------------------------------------------------------------------
    def _render(self) -> None:
        step = STEPS[self.index]
        self.title_label.configure(text=step["title"])
        self.body_label.configure(text=step["body"])
        if step.get("focus_prefix"):
            self.focus_prefix.configure(text=step["focus_prefix"])
            self.focus_suffix.configure(text=step["focus_suffix"])
            self.focus_followup.configure(text=step["focus_followup"])
            self.focus_row.grid(row=3, column=0, pady=(theme.lpx(2), 0))
            self.focus_followup.grid(row=4, column=0, pady=(0, theme.lpx(2)))
        else:
            self.focus_row.grid_remove()
            self.focus_followup.grid_remove()
        self.primary.configure(text=step["button"])
        self._draw_sun()
        self._draw_dots()

    def _draw_sun(self) -> None:
        """按画布**实测边长**成比例绘制（不写死 150，改成插画尺寸的倍数）。

        写死尺寸的老毛病：一旦把插画调小，光环和光芒就会画到画布外，
        看起来是"太阳缺了一角"。所有半径都从 ``size`` 推导。
        """
        c = self.sun
        c.delete("all")
        try:
            size = c.winfo_width()
        except Exception:  # noqa: BLE001
            size = 0
        if size <= 1:
            size = theme.lpx(self.SUN_SIZE)
        cx = cy = size / 2
        accent, soft, orange = theme.c("accent"), theme.c("accent_soft"), theme.c("orange")
        # 三个步骤下太阳逐渐"升起来"
        lift = size * 0.107 - self.index * size * 0.053
        c.create_oval(cx - size * 0.413, cy + size * 0.04,
                      cx + size * 0.413, cy + size * 0.72,
                      fill=theme.c("gold_soft"), outline="")
        r_inner = size * 0.253
        c.create_oval(cx - r_inner, cy - r_inner + lift,
                      cx + r_inner, cy + r_inner + lift, fill=soft, outline="")
        r_core = size * 0.18
        c.create_oval(cx - r_core, cy - r_core + lift,
                      cx + r_core, cy + r_core + lift, fill=accent, outline="")
        for i in range(8):
            ang = math.radians(i * 45 + 22.5)
            r0, r1 = size * 0.227, size * 0.333
            c.create_line(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0 + lift,
                          cx + math.cos(ang) * r1, cy + math.sin(ang) * r1 + lift,
                          fill=orange, width=max(2, int(size * 0.02)),
                          capstyle="round")

    def _draw_dots(self) -> None:
        c = self.dots
        c.delete("all")
        try:
            w, h = c.winfo_width(), c.winfo_height()
        except Exception:  # noqa: BLE001
            w, h = 0, 0
        if w <= 1:
            w = theme.lpx(70)
        if h <= 1:
            h = theme.lpx(14)
        step = w / (len(STEPS) + 1)
        for i in range(len(STEPS)):
            x = step * (i + 1)
            r = h * 0.30 if i == self.index else h * 0.22
            color = theme.c("accent") if i <= self.index else theme.c("ring")
            c.create_oval(x - r, h / 2 - r, x + r, h / 2 + r, fill=color, outline="")

    # ------------------------------------------------------------------
    def next_step(self) -> None:
        if self.index + 1 >= len(STEPS):
            self.finish()
            return
        self.index += 1
        self._render()

    def finish(self) -> None:
        self.on_done()
