# -*- coding: utf-8 -*-
"""截止日期选择器：暖色圆角浮层。

界面结构
--------
    ┌──────────────────────────────┐
    │  ◀   2026 年 9 月   ▶         │   ← 日历模式
    │  一 二 三 四 五 六 日         │
    │  ‥ 日期网格 6×7 ‥            │
    │  时间  [ 18 ] : [ 30 ]        │
    │  提醒  [ 提前15分钟 ]          │   ← 1.5.3 新增（时间行下方）
    │  今天 明天 后天  清除          │   ← 1.5.8 去掉"下周"
    │          [取消] [确定]        │
    └──────────────────────────────┘

点「时间」里的数字 → 日历区**就地**变成数字滚轮（1.5.20 重做）：

    │  ‹ 日历   选择分钟           │
    │        ┌────────┐           │   ← 滚轮：中间选中行 + 上下渐隐，
    │   28   │  30    │   32      │      拖拽 / 滚轮 / 点击换值
    │        └────────┘           │
    │  时间  [ 18 ] : [ 30 ]        │
    │  今天 明天 后天  清除          │
    │          [取消] [确定]        │

点「提醒」字段 → 同一块网格区变成 7 个提醒选项（1.5.5 重做）：

    │  ‹ 日历   选择提醒时间        │
    │  ‥ 不提醒     准时提醒 ‥      │   ← 2 列 × 4 行，垂直居中在 6 行区里
    │  ‥ 提前5分钟  提前15分钟 ‥    │
    │  时间  [ 18 ] : [ 30 ]        │
    │  提醒  [ 提前15分钟 ]          │
    │  今天 明天 后天  清除          │
    │          [取消] [确定]        │

三个字段为什么长这样
------------------------------------------
小时/分钟（1.5.2 网格 → 1.5.20 滚轮）：
1.5.2 把两个 ``CTkOptionMenu`` 换成"字段 + 就地数字网格"，解决了下拉遮挡；
但分钟是 10 列 × 6 行 = **60 个按钮**铺满整个面板，小时也有 24 个 —— 取值
一眼看全的代价是视觉密度爆炸、精确点选要瞄准小格子。1.5.20 改成**滚轮
选择器**：同一块网格区，一屏只见 5 行（中间选中、上下渐隐），拖拽 /
滚轮 / 点击都能换值，60 个分钟值不再同时挤在眼前。

* 为什么自己写而不用现成 Picker：ttk.Spinbox / Listbox 的样式归系统管，
  和 1.5.5 否掉原生下拉是同一条理由；自己画才能复用日历格子的暖米色
  体系（淡金选中带、柔橘选中字）。
* 交互保持"字段 + 就地切换"不变：点「时」字段出小时滚轮，点「分」字段
  出分钟滚轮，再点同一个字段收起回日历 —— 用户不需要重新适应。

提醒（1.5.5）：本项是 1.5.3 留下的最后一个 ``CTkOptionMenu``。它的下拉面板
**样式根本控不住** —— ``customtkinter`` 的 ``DropdownMenu`` 继承**原生
``tkinter.Menu``**，也就是 Windows 系统菜单：圆角、边框、阴影全部由系统绘制，
``dropdown_fg_color`` / ``dropdown_text_color`` 只能改到背景与文字两项，
实测面板 ``borderwidth=6``、白底黑字、带系统投影，与暖米色调完全不是一套。
既然改不动，就换成与另两个字段**完全一致**的形态：字段按钮 + 就地网格。

统一形态的收益：

* **零新增浮层**：面板不遮挡任何东西，弹窗尺寸也完全不涨（网格行高与日历一致，
  7 项 2 列 × 4 行正好居中落在 6 行网格区里）；
* **样式天然统一**：选项格子复用日历格子的同一套 ``_cell()`` 参数 ——
  浅米底、深灰棕字、柔橘悬停、暖金选中，无边框无阴影；
* 不会和 ``grab_set`` 打架，也不需要为"下拉被看门狗关掉"之类写恢复胶水。

尺寸
----
日历区固定 6 行高（``GRID_ROWS``）。以前按当月实际周数渲染，2 月和 8 月的
弹窗高度差 50px，切月时整个窗口会跳一下；现在恒为 6 行，切月/切模式都不动。

两个实现约定
------------
* 日期网格**只渲染当月**，不显示上/下月的灰日期。
  灰日期点不了会让人以为坏了，能点又要额外处理"点了跳到哪个月"，
  在小窗口里反而更困惑 —— 留白更干净。
* 沿用 ``dialogs._BaseDialog`` 的模态约定（grab_set + 居中 + 关闭还焦点），
  这样它和"编辑任务"对话框的手感完全一致，不需要用户重新适应。
"""

from __future__ import annotations

import calendar
import datetime as _dt
import math
import tkinter as tk
from typing import Callable, List, Optional

import customtkinter as ctk

from .. import theme
from ..models import REMIND_OPTIONS, parse_remind, remind_label
from . import widgets
from .dialogs import _BaseDialog

WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
GRID_ROWS = 6          # 日历区固定行数（也是滚轮/提醒面板的高度基准）
PANEL_COLS = 10        # 就地面板占满 10 列（_columns 的上限；日历只用 7 列）
MODE_DATE, MODE_HOUR, MODE_MINUTE, MODE_REMIND = "date", "hour", "minute", "remind"


class WheelColumn(tk.Canvas):
    """单列数字滚轮（1.5.20）：小时/分钟选择的就地面板。

    为什么自己画
    ------------
    ttk.Spinbox / Listbox 的外观归系统主题管，与 1.5.5 否掉原生下拉是同一条
    理由 —— 深浅色、圆角、暖米色一概控不住。自己画才能复用日历格子的配色：
    选中行 = 淡金圆角带（``accent_faint``）+ 柔橘文字（``orange``），上下
    行向背景色渐隐（Canvas 无透明度，用 :func:`theme.mix` 模拟）。

    模型
    ----
    滚动位置 ``_offset`` 是**连续**的（单位：项），只有吸附到整数行之后才
    回调 ``on_change`` —— 字段按钮的数字在滚轮停稳时更新，拖动途中不闪。

    交互：滚轮（每格 ±1 项）、按住拖拽、直接点某一行；松手或滚轮停 140ms
    内吸附到最近一行。数值不循环（00 顶到底就停），比循环轮更不容易"转过头"。
    """

    def __init__(self, master: tk.Misc, count: int, index: int,
                 on_change: Callable[[int], None]) -> None:
        self._count = max(1, int(count))
        self._on_change = on_change
        self._item_h = theme.lpx(theme.DUE_WHEEL_ITEM_H)   # 裸 Canvas → 物理像素
        self._bg = theme.c("bg")                            # Canvas 只认单色
        super().__init__(master, width=theme.lpx(120),
                         height=self._item_h * theme.DUE_WHEEL_VISIBLE,
                         bg=self._bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self._offset = float(max(0, min(self._count - 1, int(index))))
        self._fired = int(round(self._offset))              # 上一次回调出去的值
        self._photos: dict = {}                             # (text, role, color) → PhotoImage
        self._band_photo = None
        self._band_key = None
        self._snap_job: Optional[str] = None
        self._press_y: Optional[int] = None
        self._offset0 = 0.0
        self._moved = False
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Destroy>", lambda _e: self._cancel_snap(), add="+")
        self._redraw()

    # ------------------------------------------------------------------
    def _cancel_snap(self) -> None:
        if self._snap_job is not None:
            try:
                self.after_cancel(self._snap_job)
            except Exception:  # noqa: BLE001
                pass
            self._snap_job = None

    def _set_offset(self, value: float) -> None:
        self._offset = max(0.0, min(float(self._count - 1), float(value)))
        self._redraw()

    def _on_wheel(self, event) -> None:
        # 1.5.27：改用统一的 wheel_delta —— 正=上滚。原写法直接读 event.delta，
        # 在 X11（只有 Button-4/5、没有 delta）上恒为 0，滚轮永远不动；
        # 统一入口后 Windows / macOS / X11 三处口径一致。
        delta = widgets.wheel_delta(event)
        if not delta:
            return
        self._set_offset(self._offset - delta / 120.0)
        self._cancel_snap()
        self._snap_job = self.after(theme.DUE_WHEEL_SNAP_MS, self._snap)

    def _on_press(self, event) -> None:
        self._press_y = event.y
        self._offset0 = self._offset
        self._moved = False
        self._cancel_snap()

    def _on_motion(self, event) -> None:
        if self._press_y is None:
            return
        if abs(event.y - self._press_y) > theme.lpx(4):
            self._moved = True
        self._set_offset(self._offset0 + (self._press_y - event.y) / self._item_h)

    def _on_release(self, event) -> None:
        pressed = self._press_y is not None
        self._press_y = None
        if not pressed:
            return
        if not self._moved:
            # 没拖动 = 点击：点到哪一行就选哪一行
            cy = self.winfo_height() / 2.0
            self._set_offset(round(self._offset + (event.y - cy) / self._item_h))
        self._snap()

    def _snap(self) -> None:
        self._snap_job = None
        target = int(round(self._offset))
        self._offset = float(target)
        self._redraw()
        if target != self._fired:
            self._fired = target
            self._on_change(target)

    # ------------------------------------------------------------------
    def _text_photo(self, text: str, role: str, color: str):
        """一行抗锯齿数字位图（复用 widgets 的 4× 超采样缓存）。"""
        key = (text, role, color)
        photo = self._photos.get(key)
        if photo is None:
            image = widgets._aa_text_image(text, role, color,
                                           box_h=theme.DUE_WHEEL_ITEM_H)
            if image is None:
                return None
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(image, master=self)
            self._photos[key] = photo
        return photo

    def _band_photo_for(self, width: int):
        """中部选中带：随容器宽度重建的淡金圆角位图（PIL 4× 超采样）。"""
        key = (width, self._bg)
        if self._band_key == key:
            return self._band_photo
        from PIL import Image, ImageDraw, ImageTk

        ss = 4
        h = max(2, int(round(self._item_h * 1.18)))
        radius = max(2, theme.lpx(theme.DUE_CELL_RADIUS))
        big = Image.new("RGBA", (width * ss, h * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        draw.rounded_rectangle([ss, ss, width * ss - ss, h * ss - ss],
                               radius=radius * ss, fill=theme.c("accent_faint"))
        self._band_photo = ImageTk.PhotoImage(
            big.resize((width, h), Image.LANCZOS), master=self)
        self._band_key = key
        return self._band_photo

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 8 or h < 8:
            return
        cy = h / 2.0
        band = self._band_photo_for(w)
        if band is not None:
            self.create_image(w / 2.0, cy, image=band)
        base = int(math.floor(self._offset))
        for i in range(base - theme.DUE_WHEEL_VISIBLE,
                       base + theme.DUE_WHEEL_VISIBLE + 1):
            if i < 0 or i >= self._count:
                continue
            y = cy + (i - self._offset) * self._item_h
            if y < -self._item_h or y > h + self._item_h:
                continue
            dist = abs(i - self._offset)
            if dist < 0.5:
                role, color = "body", theme.c("orange")
            else:
                role = "small"
                color = theme.mix(theme.c("text"), self._bg,
                                  min(1.0, max(0.0, dist - 0.25) / 2.0) * 0.85)
            photo = self._text_photo(f"{i:02d}", role, color)
            if photo is not None:
                self.create_image(w / 2.0, y, image=photo)
            else:
                # 字体解析失败的降级：Tk 自绘文字（字号必须负数 —— 正数是
                # "点"，会被 tk scaling 再乘一次）
                family, size, weight = theme.tkfont_spec(role)
                self.create_text(w / 2.0, y, text=f"{i:02d}", fill=color,
                                 font=(family, -int(size * theme.scale()), weight))


class DuePickerDialog(_BaseDialog):
    """选择 / 清除任务的截止日期。"""

    # 比通用对话框窄：去掉"下周"后（1.5.8）最宽的一行是时间/提醒字段，
    # 快捷按钮那行降到 ~226 → 弹窗宽度就落在下限 300。
    MIN_DIALOG_W = theme.DUE_MIN_W

    def __init__(self, app, when: Optional[_dt.datetime] = None,
                 on_save: Optional[Callable[..., None]] = None,
                 remind: Optional[int] = None) -> None:
        super().__init__(app, "截止日期")
        self.on_save = on_save
        # 提醒提前量（分钟）：None = 不提醒。回调统一按 (when, remind) 两参返回。
        self._remind: Optional[int] = parse_remind(remind)

        # 默认值：没设过就建议"一小时后"（比"现在"更符合直觉，不会一打开就是逾期）
        base = when or (_dt.datetime.now() + _dt.timedelta(hours=1))
        base = base.replace(second=0, microsecond=0)
        self._selected: _dt.date = base.date()
        self._view_year: int = base.year
        self._view_month: int = base.month
        # ❗不再把分钟对齐到 5 的倍数（1.5.2）：分钟全量可选，默认值也没理由被改动
        self._hour: int = base.hour
        self._minute: int = base.minute
        self._mode: str = MODE_DATE

        # 全部是**逻辑像素**：CTk 控件自己会缩放，传 lpx() 就是双重放大。
        pad = {"padx": theme.DUE_PAD_X}
        self.grid_columnconfigure(0, weight=1)

        # 表头行（星期）在时间模式下会被 grid_remove()，靠这一行 minsize 兜住
        # 高度 —— 否则弹窗会在"日期 ↔ 数字"切换时上下跳一下。grid 的 minsize
        # 是**物理**像素，要过 lpx()。
        self.grid_rowconfigure(1, minsize=theme.lpx(theme.DUE_WEEKDAY_H))

        # ---------------- 顶部：月份切换 / 时间模式返回 ----------------
        # 1.5.21：三列**等权 + 左右列同 minsize**（左按钮 | 标题 | 右按钮）。
        # 此前只有中列 weight=1，左列被「‹ 日历」占住、右列空着 → 标题整体
        # 被挤偏。但仅等权还不够 —— 等权只均分**多余**空间，左列请求宽
        # （返回按钮 ~50）与右列（0）不同，列宽仍不相等，标题偏右一个按钮宽。
        # 左右列取同一个 minsize（≥ 返回按钮宽）后两列恒等宽，标题绝对居中。
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew",
                  pady=(theme.DUE_PAD_TOP, theme.DUE_ROW_GAP), **pad)
        for col in (0, 1, 2):
            head.grid_columnconfigure(col, weight=1)
        for col in (0, 2):
            head.grid_columnconfigure(
                col, minsize=theme.lpx(theme.DUE_NAV_COL_W))

        self.prev_btn = self._nav_btn(head, "◀", lambda: self._shift_month(-1),
                                      theme.DUE_NAV_W)
        self.prev_btn.grid(row=0, column=0)
        # 「‹ 日历」与 ◀ 抢同一个格位：两选一显示，空间零增量
        self.back_btn = self._nav_btn(head, "‹ 日历", lambda: self._set_mode(MODE_DATE),
                                      theme.fit_width("‹ 日历", "tiny", pad_x=6))
        self.next_btn = self._nav_btn(head, "▶", lambda: self._shift_month(+1),
                                      theme.DUE_NAV_W)
        self.next_btn.grid(row=0, column=2)

        self.month_label = widgets.TextLabel(head, text="", font=theme.font("group_title"),
                                             text_color=theme.pair("text"))
        self.month_label.grid(row=0, column=1)

        # ---------------- 星期表头 ----------------
        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.grid(row=1, column=0, sticky="ew", **pad)
        self.weekday_labels: List[ctk.CTkLabel] = []
        for i in range(7):
            self.header.grid_columnconfigure(i, weight=1)
            lab = widgets.TextLabel(self.header, text=WEEKDAYS[i], font=theme.font("tiny"),
                                    text_color=theme.pair("text_done"))
            lab.grid(row=0, column=i)
            self.weekday_labels.append(lab)

        # ---------------- 网格区（日期 / 小时 / 分钟 共用）----------------
        self.grid_holder = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_holder.grid(row=2, column=0, sticky="ew", **pad)
        # 行高固定：既让日历不随月份跳动，也让数字网格与日历区等高
        for r in range(GRID_ROWS):
            self.grid_holder.grid_rowconfigure(
                r, minsize=theme.lpx(theme.DUE_PICK_CELL_H + theme.DUE_CELL_GAP * 2))

        # ---------------- 时间 ----------------
        # 1.5.22：恢复**贴左**（1.5.21 曾把本行居中，用户明确要求改回）。
        # 字段行与"提醒"行同构贴左，只有顶部标题保持绝对居中（1.5.21）。
        time_row = ctk.CTkFrame(self, fg_color="transparent")
        time_row.grid(row=3, column=0, sticky="w",
                      pady=(theme.DUE_ROW_GAP, 0), **pad)
        widgets.TextLabel(time_row, text="时间", font=theme.font("tiny"),
                          text_color=theme.pair("text_muted")).pack(side="left",
                                                                    padx=(0, 6))
        self.hour_field = self._time_field(time_row, MODE_HOUR)
        self.hour_field.pack(side="left")
        widgets.TextLabel(time_row, text=":", font=theme.font("small"),
                          text_color=theme.pair("text_muted")).pack(side="left", padx=3)
        self.minute_field = self._time_field(time_row, MODE_MINUTE)
        self.minute_field.pack(side="left")

        # ---------------- 提醒（1.5.3 新增；1.5.5 从下拉改为字段+就地网格）----
        # 形状与「时/分」字段**完全同构**：一个按钮，点它把网格区切到提醒选项。
        # 不用下拉的理由见模块 docstring —— CTk 的 DropdownMenu 是原生
        # ``tkinter.Menu``，圆角/边框/阴影都不归我们管（实测 borderwidth=6、
        # 白底黑字带系统投影），"改样式"这条路根本走不通。
        #
        # 历史坑，别再回头：
        # * 1.5.3 "外层 Frame 当描边环 + 内衬 OptionMenu" → 环与内衬同为
        #   corner_radius 而半高差 1，两条弧不同心，橙色全堆在两端成粗边；
        # * 1.5.4 去掉描边环、只留柔橘文字 → 字段本身好看了，**展开的面板**
        #   还是原生菜单，用户一眼就看出"白底黑字 + 硬边框 + 阴影"。
        remind_row = ctk.CTkFrame(self, fg_color="transparent")
        remind_row.grid(row=4, column=0, sticky="w",
                        pady=(theme.DUE_ROW_GAP, 0), **pad)
        widgets.TextLabel(remind_row, text="提醒", font=theme.font("tiny"),
                          text_color=theme.pair("text_muted")).pack(side="left",
                                                                    padx=(0, 6))
        self.remind_field = self._remind_field(remind_row)
        self.remind_field.pack(side="left")

        # ---------------- 快捷选项 ----------------
        # 只有「今天 / 明天 / 后天」三个日偏移 + 一个「清除」（1.5.8 去掉"下周"）。
        # 少一个按钮后本行的请求宽度降到 ~226 逻辑像素，不再是本弹窗最宽的一行
        # （原本 5 个按钮撑到 ~306，把弹窗宽度顶到下限 DUE_MIN_W 以上）；
        # 宽度回到 300 的同时，剩下的按钮按 weight 均分，行内观感不变。
        quick = ctk.CTkFrame(self, fg_color="transparent")
        quick.grid(row=5, column=0, sticky="ew",
                   pady=(theme.DUE_ROW_GAP, 0), **pad)
        for i, (label, days) in enumerate((("今天", 0), ("明天", 1), ("后天", 2))):
            quick.grid_columnconfigure(i, weight=1)
            ctk.CTkButton(
                quick, text=label, width=theme.DUE_QUICK_W,
                height=theme.DUE_QUICK_H,
                corner_radius=theme.DUE_QUICK_H // 2,
                fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                text_color=theme.pair("text"), font=theme.font("tiny"),
                command=lambda d=days: self._quick(d),
            ).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
        quick.grid_columnconfigure(3, weight=1)
        ctk.CTkButton(
            quick, text="清除", width=theme.DUE_QUICK_W, height=theme.DUE_QUICK_H,
            corner_radius=theme.DUE_QUICK_H // 2,
            fg_color=theme.pair("accent_faint"), hover_color=theme.pair("accent_soft"),
            text_color=theme.pair("danger"), font=theme.font("tiny"),
            command=self._clear,
        ).grid(row=0, column=3, sticky="ew", padx=(6, 0))

        # ---------------- 按钮 ----------------
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=6, column=0, sticky="ew",
                     pady=(theme.DUE_ROW_GAP + 2, theme.DUE_PAD_BOTTOM), **pad)
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(buttons, text="取消", width=theme.DUE_BTN_W,
                      height=theme.DUE_BTN_H,
                      corner_radius=theme.DUE_BTN_H // 2,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text_muted"), font=theme.font("tiny"),
                      command=self._cancel).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(buttons, text="确定", width=theme.DUE_BTN_W,
                      height=theme.DUE_BTN_H,
                      corner_radius=theme.DUE_BTN_H // 2,
                      fg_color=theme.pair("accent"), hover_color=theme.pair("accent_hover"),
                      text_color=theme.ON_ACCENT, font=theme.font("tiny"),
                      command=self._confirm).grid(row=0, column=2)

        self.bind("<Escape>", lambda _e: self._cancel())
        self._sync_head()
        self._sync_fields()
        self._render_panel()
        self.show()

    # ------------------------------------------------------------------
    # 小控件工厂
    # ------------------------------------------------------------------
    def _nav_btn(self, master: tk.Misc, text: str, command: Callable[[], None],
                 width: int) -> ctk.CTkButton:
        return ctk.CTkButton(
            master, text=text, width=width, height=theme.DUE_HEAD_H - 4,
            corner_radius=(theme.DUE_HEAD_H - 4) // 2,
            fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text_muted"), font=theme.font("tiny"),
            command=command,
        )

    def _time_field(self, master: tk.Misc, mode: str) -> ctk.CTkButton:
        """时/分字段：一个纯数字按钮，点它把下面的网格切到对应模式。"""
        return ctk.CTkButton(
            master, text="00", width=theme.DUE_TIME_W, height=theme.DUE_TIME_H,
            corner_radius=theme.DUE_TIME_H // 2,
            font=theme.font("small"),
            command=lambda: self._set_mode(mode),
        )

    def _remind_field(self, master: tk.Misc) -> ctk.CTkButton:
        """提醒字段：文案按钮（不是下拉），点它把网格区切到提醒选项。

        与 ``_time_field`` 的唯一区别是宽度（要放得下「提前15分钟」）与文案，
        外观参数**完全相同** —— 三个字段在同一行视觉体系里。
        """
        return ctk.CTkButton(
            master, text=remind_label(self._remind),
            width=theme.DUE_REMIND_W, height=theme.DUE_TIME_H,
            corner_radius=theme.DUE_TIME_H // 2,
            font=theme.font("small"),
            command=lambda: self._set_mode(MODE_REMIND),
        )

    def _cell(self, text: str, *, width: int, height: int, active: bool,
              today: bool, command: Callable[[], None],
              hover_key: str = "ghost",
              font_role: str = "tiny") -> ctk.CTkButton:
        """网格格子。日历 / 数字 / 提醒三处共用 —— 样式永远一致。

        ``hover_key`` 让提醒选项用 ``accent_soft``（柔橘浅底）做悬停，日历与
        数字网格保持 ``ghost``；``font_role`` 让提醒选项用 12px（``small``），
        因为它是**词组**而不是两位数字，tiny（10px）读起来太吃力。
        """
        if active:
            fg, hover, fg_text = (theme.pair("accent"), theme.pair("accent_hover"),
                                  theme.ON_ACCENT)
        elif today:
            fg, hover, fg_text = (theme.pair("accent_faint"), theme.pair("accent_soft"),
                                  theme.pair("accent"))
        else:
            fg, hover, fg_text = "transparent", theme.pair(hover_key), theme.pair("text")
        return ctk.CTkButton(
            self.grid_holder, text=text, width=width, height=height,
            corner_radius=theme.DUE_CELL_RADIUS, fg_color=fg, hover_color=hover,
            text_color=fg_text, font=theme.font(font_role), command=command,
        )

    # ------------------------------------------------------------------
    # 模式切换与状态同步
    # ------------------------------------------------------------------
    def _set_mode(self, mode: str) -> None:
        # 点已经选中的那个字段 = 收起网格回到日历（同一个按钮就能来回切）
        self._mode = MODE_DATE if self._mode == mode else mode
        self._sync_head()
        self._sync_fields()
        self._render_panel()

    def _sync_head(self) -> None:
        if self._mode == MODE_DATE:
            self.back_btn.grid_remove()
            self.prev_btn.grid()
            self.next_btn.grid()
            self.month_label.configure(text=f"{self._view_year} 年 {self._view_month} 月")
            self.header.grid()
            return
        self.prev_btn.grid_remove()
        self.next_btn.grid_remove()
        self.back_btn.grid(row=0, column=0)
        # 标题只报"在选什么"，不带取值范围：网格里 00–23 / 00–59 一眼就能看全，
        # 括号里的数字属于冗余信息，还会把标题行顶长（1.5.8 精简）。
        self.month_label.configure(text={
            MODE_HOUR: "选择小时",
            MODE_MINUTE: "选择分钟",
            MODE_REMIND: "选择提醒时间",
        }.get(self._mode, ""))
        # 星期表头与网格类面板无关，收起来（行高由 row 1 的 minsize 兜住）
        self.header.grid_remove()

    def _sync_fields(self) -> None:
        """三个字段按钮的文案与激活态一起刷。

        "激活"= 网格区当前正显示它那一套选项，用 ``accent`` 底 + 高亮文字；
        其余用 ``ghost`` 底。提醒还多一层语义：**提醒开着**时文字取柔橘，
        否则用正文色 —— 这样不展开选项也能一眼看出"这条任务到底提不提醒"。
        """
        for field, mode, text in (
                (self.hour_field, MODE_HOUR, f"{self._hour:02d}"),
                (self.minute_field, MODE_MINUTE, f"{self._minute:02d}"),
                (self.remind_field, MODE_REMIND, remind_label(self._remind))):
            active = self._mode == mode
            if active:
                text_color = theme.ON_ACCENT
            elif mode == MODE_REMIND and self._remind is not None:
                text_color = theme.pair("orange")
            else:
                text_color = theme.pair("text")
            field.configure(
                text=text,
                fg_color=theme.pair("accent") if active else theme.pair("ghost"),
                hover_color=(theme.pair("accent_hover") if active
                             else theme.pair("ghost_hover")),
                text_color=text_color,
            )

    # ------------------------------------------------------------------
    # 网格渲染
    # ------------------------------------------------------------------
    def _shift_month(self, delta: int) -> None:
        month = self._view_month + delta
        year = self._view_year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self._view_month, self._view_year = month, year
        self._sync_head()
        self._render_panel()

    def _columns(self, count: int) -> None:
        """只给真正用到的列开权重。

        全部 10 列都开着 weight=1 是个坑：日历只用 7 列，另外 3 列也会各分走
        1/10 宽度，7 个 30px 的格子挤在 7/10 的宽度里 → 整块横向溢出。
        """
        for i in range(PANEL_COLS):
            self.grid_holder.grid_columnconfigure(i, weight=1 if i < count else 0)

    def _render_panel(self) -> None:
        for child in list(self.grid_holder.winfo_children()):
            child.destroy()
        if self._mode == MODE_HOUR:
            self._render_wheel(24, self._hour)
        elif self._mode == MODE_MINUTE:
            self._render_wheel(60, self._minute)
        elif self._mode == MODE_REMIND:
            self._render_reminds()
        else:
            self._render_days()

    def _render_days(self) -> None:
        self._columns(7)
        self.month_label.configure(text=f"{self._view_year} 年 {self._view_month} 月")

        # monthrange 返回 (1 号是周几, 当月天数)，周一 = 0，正好对上"一~日"表头
        first_weekday, days_in_month = calendar.monthrange(self._view_year, self._view_month)
        today = _dt.date.today()

        for day in range(1, days_in_month + 1):
            cell = first_weekday + day - 1
            row, col = divmod(cell, 7)
            date = _dt.date(self._view_year, self._view_month, day)
            self._cell(
                str(day),
                width=theme.DUE_CELL_W, height=theme.DUE_CELL_H,
                active=date == self._selected, today=date == today,
                command=lambda d=date: self._pick(d),
            ).grid(row=row, column=col,
                   padx=theme.DUE_CELL_GAP, pady=theme.DUE_CELL_GAP)

    def _render_wheel(self, count: int, current: int) -> None:
        """时/分就地滚轮（1.5.20）：占满整块 6 行网格区，弹窗尺寸零变化。"""
        self._columns(PANEL_COLS)
        wheel = WheelColumn(self.grid_holder, count=count, index=current,
                            on_change=self._pick_wheel)
        wheel.grid(row=0, column=0, columnspan=PANEL_COLS, rowspan=GRID_ROWS,
                   sticky="nsew")

    def _render_reminds(self) -> None:
        """提醒选项就地网格（7 项 → 2 列 × 4 行，居中落在 6 行网格区里）。

        两个刻意的取值：

        * **行数必须 ≤ GRID_ROWS**。``grid_holder`` 的每行 minsize 是按日历行
          定的，一旦铺到第 7 行，容器就会长高 26px —— 弹窗跟着跳一下，正是
          1.5.2 修掉的"切模式弹窗变高"。所以 7 项走 2 列而不是 1 列。
        * 最后一项若独占一行就 ``columnspan`` 铺满（7 = 2×3 + 1），否则右下角
          会空出一块，看起来像少了一项。
        """
        cols = theme.DUE_REMIND_COLS
        total = len(REMIND_OPTIONS)
        rows = (total + cols - 1) // cols
        offset = max(0, (GRID_ROWS - rows) // 2)
        self._columns(cols)
        for i, (value, label) in enumerate(REMIND_OPTIONS):
            row, col = divmod(i, cols)
            span = cols if (i == total - 1 and total % cols == 1) else 1
            self._cell(
                label,
                width=(theme.DUE_REMIND_CELL_W * span
                       + theme.DUE_CELL_GAP * 2 * (span - 1)),
                height=theme.DUE_PICK_CELL_H,
                active=(value == self._remind), today=False,
                command=lambda v=value: self._pick_remind(v),
                hover_key="accent_soft", font_role="small",
            ).grid(row=row + offset, column=col, columnspan=span,
                   padx=theme.DUE_CELL_GAP, pady=theme.DUE_CELL_GAP)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _pick(self, date: _dt.date) -> None:
        self._selected = date
        # 点到别的月份时顺带把视图切过去，省一次点击
        if date.month != self._view_month or date.year != self._view_year:
            self._view_year, self._view_month = date.year, date.month
            self._sync_head()
        self._render_panel()

    def _pick_wheel(self, value: int) -> None:
        """滚轮吸附后的回调：只改值 + 刷字段按钮，**不重渲染面板**。

        重渲染会销毁正在使用的滚轮 —— 拖拽/吸附中途面板被拆掉，手势直接断掉，
        这是与旧数字网格（点一下 = 选定，可以整块重建）最本质的区别。
        """
        if self._mode == MODE_HOUR:
            self._hour = value % 24
        elif self._mode == MODE_MINUTE:
            self._minute = value % 60
        self._sync_fields()

    def _pick_remind(self, value: Optional[int]) -> None:
        """选中一项提醒提前量。

        ``value`` 来自 ``models.REMIND_OPTIONS``，第一项就是 ``None``（不提醒）。
        仍然过一遍 ``parse_remind``：选项表哪天真被改脏了，这里也不会把
        非法值带进落库路径（与 ``data.json`` 读取端同一把闸）。
        """
        self._remind = parse_remind(value)
        self._sync_fields()
        self._render_panel()

    def _quick(self, days: int) -> None:
        self._pick(_dt.date.today() + _dt.timedelta(days=days))

    def _clear(self) -> None:
        callback = self.on_save
        self._finish()
        if callback:
            callback(None, self._remind)

    def _confirm(self) -> None:
        when = _dt.datetime(self._selected.year, self._selected.month, self._selected.day,
                            self._hour, self._minute)
        callback = self.on_save
        self._finish()
        if callback:
            callback(when, self._remind)


def pick_due(app, when: Optional[_dt.datetime] = None,
             on_save: Optional[Callable[..., None]] = None,
             remind: Optional[int] = None) -> None:
    """打开截止日期选择器。

    ``on_save`` 的签名是 ``(when, remind)``：``when`` 为 ``None`` 表示清除
    截止日期，``remind`` 为 ``None`` 表示不提醒。带提醒一起返回是因为两者
    在界面上就挨着（提醒行在时间行下方），分两次回调反而容易出现
    "日期改了、提醒没跟上"的半截状态。
    """
    DuePickerDialog(app, when=when, on_save=on_save, remind=remind)
