# -*- coding: utf-8 -*-
"""光景（统计）页面：今日 / 连续打卡 / 本周趋势 / 累计。"""

from __future__ import annotations

import datetime as _dt
import tkinter as tk
from typing import Callable

import customtkinter as ctk

from .. import sound, stats, theme
from . import dialogs, widgets


class StatBlock(ctk.CTkFrame):
    """单个数字块（柔和渐变色块，不用生硬的边框图表）。"""

    def __init__(self, master: tk.Misc, label: str, value: str, suffix: str = "",
                 accent: bool = False) -> None:
        super().__init__(master, corner_radius=theme.RADIUS_CARD,
                         fg_color=theme.pair("accent_faint") if accent else theme.pair("card"),
                         border_width=1,
                         border_color=theme.pair("accent_soft") if accent else theme.pair("border"))
        self.grid_columnconfigure(0, weight=1)
        self.value_label = widgets.TextLabel(
            self, text=value, font=theme.font("number", -4),
            text_color=theme.pair("accent") if accent else theme.pair("text"),
        )
        self.value_label.grid(row=0, column=0, pady=(theme.lpx(7), 0))
        widgets.TextLabel(self, text=label, font=theme.font("tiny"),
                     text_color=theme.pair("text_muted")).grid(
            row=1, column=0, pady=(0, theme.lpx(7)))

    def set_value(self, value: str) -> None:
        self.value_label.configure(text=value)


class StatsPage(ctk.CTkFrame):
    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.store = app.store
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        widgets.TextLabel(self, text="你的光景", font=theme.font("title"),
                     text_color=theme.pair("text"), anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(2, 2))
        self.subtitle = widgets.TextLabel(self, text="", font=theme.font("small"),
                                     text_color=theme.pair("text_muted"), anchor="w")
        self.subtitle.grid(row=1, column=0, sticky="ew", pady=(0, theme.lpx(theme.PAGE_GAP)))

        body = widgets.ThinScrollFrame(self, height=200)
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)

        # ---- 三个数字块 ----
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", pady=(0, theme.lpx(theme.PAGE_GAP)))
        for i in range(3):
            row.grid_columnconfigure(i, weight=1)
        data = stats.summary(self.store)
        self.block_today = StatBlock(row, "今日拾起", str(data["today"]), accent=True)
        self.block_today.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.block_streak = StatBlock(row, "连续打卡", str(data["streak"]))
        self.block_streak.grid(row=0, column=1, sticky="ew", padx=5)
        self.block_total = StatBlock(row, "累计拾起", str(data["total"]))
        self.block_total.grid(row=0, column=2, sticky="ew", padx=(5, 0))

        # ---- 本周趋势 ----
        chart_card = ctk.CTkFrame(body, corner_radius=theme.RADIUS_CARD, fg_color=theme.pair("card"),
                                  border_width=1, border_color=theme.pair("border"))
        chart_card.grid(row=1, column=0, sticky="ew",
                        pady=(0, theme.lpx(theme.PAGE_GAP)))
        chart_card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(chart_card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew",
                  padx=theme.lpx(theme.CARD_PAD_X), pady=(theme.lpx(8), 0))
        head.grid_columnconfigure(0, weight=1)
        widgets.TextLabel(head, text="本周完成趋势", font=theme.font("h2"),
                     text_color=theme.pair("text")).grid(row=0, column=0, sticky="w")
        self.week_label = widgets.TextLabel(head, text="", font=theme.font("tiny"),
                                       text_color=theme.pair("text_muted"))
        self.week_label.grid(row=0, column=1, sticky="e")
        # 图表高度 132 -> 88：小窗里它只是"一眼扫过"的趋势条，不需要半屏。
        # 宽度给底值即可 —— grid 的 sticky="ew" 会把它拉到卡片实宽。
        self.chart = widgets.SoftBarChart(chart_card, width=230, height=88,
                                          bg=theme.c("card"))
        self.chart.grid(row=1, column=0, sticky="ew",
                        padx=theme.lpx(4), pady=(theme.lpx(4), theme.lpx(8)))

        # ---- 专注统计（1.5.11：番茄数据此前只落盘不展示）----
        focus_card = ctk.CTkFrame(body, corner_radius=theme.RADIUS_CARD,
                                  fg_color=theme.pair("card"),
                                  border_width=1, border_color=theme.pair("border"))
        focus_card.grid(row=2, column=0, sticky="ew",
                        pady=(0, theme.lpx(theme.PAGE_GAP)))
        focus_card.grid_columnconfigure(0, weight=1)
        widgets.TextLabel(focus_card, text="专注统计", font=theme.font("h2"),
                          text_color=theme.pair("text")).grid(
            row=0, column=0, sticky="w", padx=theme.lpx(theme.CARD_PAD_X),
            pady=(theme.lpx(8), 0))
        self.focus_vals: dict[str, widgets.TextLabel] = {}
        focus_row = ctk.CTkFrame(focus_card, fg_color="transparent")
        focus_row.grid(row=1, column=0, sticky="ew", padx=theme.lpx(theme.CARD_PAD_X),
                       pady=(theme.lpx(2), theme.lpx(8)))
        for i, (key, label) in enumerate(
                [("today", "今日专注"), ("week", "本周专注"), ("total", "累计专注")]):
            focus_row.grid_columnconfigure(i, weight=1)
            cell = ctk.CTkFrame(focus_row, fg_color="transparent")
            cell.grid(row=0, column=i)
            num = widgets.TextLabel(cell, text="0", font=theme.font("number"),
                                    text_color=theme.pair("accent") if key == "today"
                                    else theme.pair("text"))
            num.pack()
            widgets.TextLabel(cell, text=label, font=theme.font("tiny"),
                              text_color=theme.pair("text_muted")).pack()
            self.focus_vals[key] = num

        # ---- 鼓励 ----
        self.encourage_card = ctk.CTkFrame(
            body, corner_radius=theme.RADIUS_CARD, fg_color=theme.pair("accent_faint"),
            border_width=1, border_color=theme.pair("accent_soft"))
        self.encourage_card.grid(row=3, column=0, sticky="ew",
                                 pady=(0, theme.lpx(theme.PAGE_GAP)))
        self.encourage_label = widgets.TextLabel(
            self.encourage_card, text="", font=theme.font("body"),
            text_color=theme.pair("text"), wraplength=240, justify="left")
        self.encourage_label.pack(padx=theme.lpx(theme.CARD_PAD_X),
                                  pady=theme.lpx(8), anchor="w")

        # ---- 近 30 天小结 ----
        # 1.5.12 修"平均每天 0.0 缕"被截断：CTkLabel 默认**不折行**，
        # 一行写不下就静默裁掉尾巴。除了给初始 wraplength，还要按卡片
        # 实测宽度动态修正（窗口拖窄后固定值照样溢出）—— 见 _fit_wrap。
        self.month_card = ctk.CTkFrame(body, corner_radius=theme.RADIUS_CARD, fg_color=theme.pair("card"),
                                       border_width=1, border_color=theme.pair("border"))
        self.month_card.grid(row=4, column=0, sticky="ew",
                             pady=(0, theme.lpx(theme.PAGE_GAP)))
        self.month_label = widgets.TextLabel(
            self.month_card, text="", font=theme.font("small"),
            text_color=theme.pair("text_muted"), justify="left", anchor="w",
            wraplength=260)
        self.month_label.pack(padx=theme.lpx(theme.CARD_PAD_X),
                              pady=theme.lpx(8), anchor="w")
        self._fit_wrap(self.encourage_card, self.encourage_label)
        self._fit_wrap(self.month_card, self.month_label)

        # ---- 导出 ----
        ctk.CTkButton(
            body, text="导出任务数据为 CSV", height=28, corner_radius=14,
            fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text"), font=theme.font("small"),
            command=lambda: dialogs.export_tasks_csv(self.app),
        ).grid(row=5, column=0, sticky="ew", pady=(2, theme.lpx(10)))

        self.refresh()

    # ------------------------------------------------------------------
    @staticmethod
    def _fit_wrap(card: ctk.CTkFrame, label: widgets.TextLabel) -> None:
        """按卡片实测宽度动态修 wraplength（修"近 30 天"文案被截断）。

        ⚠️ 单位口径（1.5.13 修的真 bug）：``winfo_width()`` 是**物理像素**，
        而 ``CTkLabel`` 的 ``wraplength`` 是**逻辑值**（内部自己乘缩放系数）。
        上一版把物理宽直接塞给 wraplength，150% 屏上恒放大 1.5 倍 —— 折行盒
        比卡片还宽，"平均每天 0.0 缕"的尾巴永远被裁掉。先除回 ``scale()``。

        只关心宽度维度：``<Configure>`` 在卡片被移动（x/y 变）时也会触发，
        高度变化不值得重算 —— 宽度没变就直接返回，避免与滚动容器互相引发
        重排（1.5.4 抖动的教训）。

        ⚠️ 1.5.16 加防抖：拖边框时此回调每帧都发（而且 bind 在 CTkFrame 上
        实际挂在内部 _canvas、子控件事件也会到），逐帧 ``configure(wraplength)``
        会连累 CTkLabel 反复重排。停手 80ms 后才算一次，宽度没变直接跳过。
        """
        pad = theme.lpx(theme.CARD_PAD_X)
        job = {"id": None}
        last = {"w": 0}

        def _fit(_event=None) -> None:
            job["id"] = None
            try:
                w = card.winfo_width()
            except Exception:  # noqa: BLE001
                return
            if w == last["w"]:          # 尺寸没变就跳过，避免无谓重排
                return
            last["w"] = w
            avail = (w - pad * 2) / (theme.scale() or 1.0)
            avail = int(avail)
            if avail <= 60:
                return
            try:
                if abs(float(label.cget("wraplength")) - avail) > 0.5:
                    label.configure(wraplength=avail)
            except Exception:  # noqa: BLE001
                pass

        def _on_cfg(_event=None) -> None:
            # 1.5.19：wraplength 修正是**布局适配**（便宜且幂等，内部有
            # 宽度未变跳过），当帧 after_idle 执行；冻结只留给贵重绘。
            if job["id"] is not None:
                return                  # 本帧已排过，合并
            job["id"] = label.after_idle(_fit)

        widgets.RESIZE_GATE.register(card, _fit)
        card.bind("<Configure>", _on_cfg, add="+")
        try:
            label.after(0, _fit)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        store = self.store
        data = stats.summary(store)
        self.block_today.set_value(str(data["today"]))
        self.block_streak.set_value(str(data["streak"]))
        self.block_total.set_value(str(data["total"]))

        trend = stats.week_trend(store)
        self.chart.set_data(trend)
        self.week_label.configure(text=f"本周共 {sum(v for _, v, _ in trend)} 缕")

        today = _dt.date.today()
        self.subtitle.configure(text=f"{today.month} 月 {today.day} 日 · 拾光记录")
        self.encourage_label.configure(text=stats.encouragement(data["today"]))

        # 近 30 天
        total_30 = 0
        active_days = 0
        for i in range(30):
            day = (today - _dt.timedelta(days=i)).isoformat()
            value = store.history_get(day)
            total_30 += value
            if value:
                active_days += 1
        avg = total_30 / 30
        self.month_label.configure(
            text=f"近 30 天：拾起 {total_30} 缕光，活跃 {active_days} 天，"
                 f"平均每天 {avg:.1f} 缕。\n最好的状态不是冲刺，而是每天都来一次。"
        )

        # 专注统计（1.5.11）
        focus = stats.focus_summary(store)
        for key, value in focus.items():
            label = self.focus_vals.get(key)
            if label is not None:
                label.configure(text=str(value))

    # ------------------------------------------------------------------
    # 导出入口已收敛到 dialogs.export_tasks_csv（1.5.11），
    # 与设置页共用同一个实现。
    # ------------------------------------------------------------------
