# -*- coding: utf-8 -*-
"""分组"更换图标"浮层（需求 ⑥）。

为什么不做成右键菜单的一项
--------------------------
上一版把 12 个图标塞进 ``menu.popup_menu`` 的候选列表里，结果是：

* 菜单宽度固定 180px，图标只能挤在 18px 的一列上，**看不出画的是什么**；
* 菜单行高 34px，12 项排下来接近 500px 高，在小窗口上要滚屏才看得全；
* 图标本身就是彩色的，再和"文字 + 悬停底色"混在一行里，视觉完全糊在一起。

所以改成一个独立浮层：**4 列 × 3 行**的暖色图标网格，格子 44px 见方，
选中的那格是柔橘实心 + 白色图标，一眼就能看出"当前用的是哪个"。

生命周期（与 ``quick_add.QuickAddPopup`` 同一套纪律）
--------------------------------------------------
菜单/浮层类控件最容易出的问题就是"关不掉"或"关了还活"，所以这里：

* **幂等**：``_closed`` / ``_closing`` 双标志，任何入口重复调用直接返回；
* **先 withdraw 再 destroy**：即使 destroy 抛异常，屏幕上已经看不见了；
* **after 统一登记**：``<Destroy>`` 里全部 cancel，不留野回调；
* **Esc / 点外部 / 选中一项** 三条关闭路径，全部走同一个 :meth:`close`；
* 登记进 ``menu._ACTIVE``：主窗口的 Esc 兜底、以及"点任意位置收起菜单"
  那套逻辑会自动把本浮层一起收掉。

DPI 说明
--------
和 ``menu.ContextMenu`` 同一个坑：裸 ``Toplevel`` / ``Canvas`` 不受 CTk 缩放
保护，``geometry``、Canvas 宽高、绘制坐标**全是物理像素**。所以模块顶部的
设计值一律在对构造时过一遍 ``theme.lpx()``，之后只认物理值。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Tuple

from .. import icons, theme

# ---- 设计尺寸（逻辑像素，构造时统一换算）----
COLS = 4
CELL = 44                 # 格子边长
GAP = 8                   # 格子间距
PAD = 12                  # 浮层内边距
RADIUS = 14               # 浮层圆角
HEADER = 30               # 标题行高度
ICON_IN_CELL = 20         # 格子里图标的大小
ICON_CENTER_Y = 14        # 图标中心距格子顶部的高度
LABEL_BOTTOM = 3          # 名称文字距格子底部的距离
TITLE = "更换图标"

HOVER_LIGHT = "#FDF0E0"
HOVER_DARK = "#463829"

FADE_STEPS = 6
FADE_INTERVAL = 16        # ≈100ms
WATCH_INTERVAL = 140
WATCH_MISS_LIMIT = 2      # 连续两拍指针在外才收（给指针穿过接缝留时间）
WATCH_GRACE_TICKS = 4     # 开场免收拍数（≈560ms）
SAFE_ZONE = 14

ACTIVE: list = []


def close_active() -> None:
    """关掉所有开着的图标浮层（主窗口 Esc 兜底 / 点击别处时调用）。"""
    for popup in list(ACTIVE):
        try:
            popup.close()
        except Exception:  # noqa: BLE001
            pass


class IconPickerPopup(tk.Toplevel):
    """12 格图标选择浮层。

    :param on_pick: ``f(icon_key) -> None``，选中回调（写库由调用方负责）
    :param current:  当前使用的图标键，用于高亮
    """

    def __init__(self, master: tk.Misc, on_pick: Callable[[str], None],
                 current: str = "", title: str = TITLE) -> None:
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:  # noqa: BLE001
            pass

        self._on_pick = on_pick
        # 归一化"当前图标"：库里存的可能是历史 emoji（``"💼"``），而网格里的格子
        # 是图标键（``grp_work``）。不归一的话 ``key == self._current`` 永远为假 ——
        # 表现就是"打开浮层，一个都没高亮"，用户以为当前用的不是这 12 个里的任何一个。
        resolved = icons.resolve_key(current) if current else None
        self._current = resolved or current or ""
        self._title = title
        self._closed = False
        self._closing = False
        self._jobs: list = []
        self._watch_job: Optional[str] = None
        self._fade_job: Optional[str] = None
        self._watch_miss = 0
        self._grace = 0
        self._hover: Optional[str] = None

        # ---- 逻辑 → 物理（一次性）----
        self.CELL = theme.lpx(CELL)
        self.GAP = theme.lpx(GAP)
        self.PAD = theme.lpx(PAD)
        self.R = theme.lpx(RADIUS)
        self.HEADER = theme.lpx(HEADER)
        self.ICON = theme.lpx(ICON_IN_CELL)
        self.ICON_Y = theme.lpx(ICON_CENTER_Y)
        self.LABEL_INSET = theme.lpx(LABEL_BOTTOM)
        self.KEYS = list(icons.GROUP_ICON_KEYS)
        self.COLS = COLS
        self.ROWS = (len(self.KEYS) + COLS - 1) // COLS
        grid_w = COLS * self.CELL + (COLS - 1) * self.GAP
        self.W = grid_w + self.PAD * 2
        self.H = self.HEADER + self.ROWS * self.CELL \
                 + (self.ROWS - 1) * self.GAP + self.PAD

        self.configure(bg=theme.c("card"))
        self.canvas = tk.Canvas(self, bg=theme.c("card"), highlightthickness=0,
                                bd=0, width=self.W, height=self.H)
        self.canvas.pack(fill="both", expand=True)

        self._draw()
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<Escape>", lambda _e: self.close())
        # Destroy 上清 after 队列：外部直接 destroy（解释器退出、父窗口销毁）
        # 时也要保证没有野回调指向已销毁的控件
        self.bind("<Destroy>", self._on_destroy, add="+")

    # ------------------------------------------------------------------
    # 几何
    # ------------------------------------------------------------------
    def _cell_origin(self, index: int) -> Tuple[int, int]:
        """第 ``index`` 个格子的左上角（物理像素）。"""
        row, col = divmod(index, COLS)
        x = self.PAD + col * (self.CELL + self.GAP)
        y = self.HEADER + row * (self.CELL + self.GAP)
        return x, y

    def _cell_at(self, x: int, y: int) -> Optional[int]:
        for i in range(len(self.KEYS)):
            cx, cy = self._cell_origin(i)
            if cx <= x <= cx + self.CELL and cy <= y <= cy + self.CELL:
                return i
        return None

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def _draw(self) -> None:
        from . import widgets

        c = self.canvas
        c.delete("all")
        border = theme.c("border")
        bg = theme.c("card")
        # 浮层底打上 ``popupbg`` 标签：它必须在**所有**格子之下，
        # 而格子底色又必须在它**之上**（见 :meth:`_draw_cell` 的说明）。
        widgets.round_rect(c, 0, 0, self.W, self.H, self.R, fill=border,
                           outline="", tags=("popupbg",))
        widgets.round_rect(c, theme.lpx(1), theme.lpx(1), self.W - theme.lpx(1),
                           self.H - theme.lpx(1), self.R - 1, fill=bg,
                           outline="", tags=("popupbg",))

        c.create_text(self.PAD, self.HEADER / 2, text=self._title, anchor="w",
                      fill=theme.c("text"), font=theme.font("group_title"))

        for i, key in enumerate(self.KEYS):
            self._draw_cell(i, key)

    def _draw_cell(self, index: int, key: str) -> None:
        from . import widgets

        c = self.canvas
        x, y = self._cell_origin(index)
        selected = (key == self._current)
        hovered = (key == self._hover)
        tag = f"cell{index}"
        c.delete(tag)

        if selected:
            # 选中 = **柔橘实心**（theme "orange" #E89A4A），不是暖阳金 ——
            # 设计稿里"当前用的那个图标"跟顶栏"＋ 新建任务"是同一支橘色。
            fill = theme.c("orange")
        elif hovered:
            fill = HOVER_DARK if theme.is_dark() else HOVER_LIGHT
        else:
            fill = theme.c("bg_alt")
        widgets.round_rect(c, x, y, x + self.CELL, y + self.CELL,
                           theme.lpx(10), fill=fill, outline="",
                           tags=(tag, "cellbg"))
        # ⚠️ 这里**绝不能** ``tag_lower("cellbg")``。
        # 曾经为了"底色压在图标下"加过这一句，结果把格子底色沉到了浮层底
        # （``popupbg``）**之下** —— 不透明的浮层底一盖，12 个格子看起来
        # 完全没有底色，选中格的柔橘实心也一起消失，而它的图标是白色的，
        # 压在浅色底上直接隐形（预览图里第一格只剩"公文包"三个字，没有图标）。
        # 绘制顺序本身已经是对的：浮层底先画，格子底色后画，天然在上；
        # 同一个格子里也是先底色再图标。不需要任何 raise/lower。

        if selected:
            # 选中态：柔橘实心上的图标必须转白，否则暖棕图标压在橘色上几乎看不见
            photo = icons.get_tinted(key, self.ICON, "#FFFFFF")
        else:
            photo = icons.get(key, self.ICON)
        if photo is not None:
            # create_image 的坐标是**中心点**。图标刻意偏上放（不是格子正中）：
            # 格子底下还要放名称，图标放正中会直接压在文字上
            # （tiny=10pt、行高约 14.5，居中放必然重叠 10px）。
            c.create_image(x + self.CELL / 2, y + self.ICON_Y,
                           image=photo, tags=(tag,))
        # 图标名称（中文），鼠标停一下就知道是什么图标（零 emoji 上屏）。
        # 选中格是柔橘实心，名称必须跟着图标一起转白 —— 否则灰棕色文字压在
        # 橘色上对比度只有 1.9:1，等于糊成一片。
        label = icons.GROUP_ICON_LABELS.get(key, "")
        if label:
            c.create_text(x + self.CELL / 2,
                          y + self.CELL - self.LABEL_INSET,
                          text=label, anchor="s", tags=(tag,),
                          fill="#FFFFFF" if selected
                          else theme.c("text_muted"),
                          font=theme.font("tiny"))

    def _set_hover(self, key: Optional[str]) -> None:
        if key == self._hover:
            return
        old, self._hover = self._hover, key
        for probe in (old, key):
            if probe and probe in self.KEYS:
                self._draw_cell(self.KEYS.index(probe), probe)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _on_motion(self, event) -> None:
        idx = self._cell_at(event.x, event.y)
        self._set_hover(self.KEYS[idx] if idx is not None else None)

    def _on_click(self, event) -> None:
        idx = self._cell_at(event.x, event.y)
        if idx is None:
            return
        key = self.KEYS[idx]
        self._current = key
        self._draw()
        callback = self._on_pick
        # 先关再回调：回调里通常要 render() 整页，浮层留着会盖在新内容上
        self.close(reason="picked")
        if callback is not None:
            try:
                callback(key)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 存活性：指针离开才收（与 ui/menu.py 同一套看门狗）
    # ------------------------------------------------------------------
    def _watch(self) -> None:
        self._watch_job = None
        if self._closed or self._closing or not self.winfo_exists():
            return
        if self._grace > 0:
            # 开场宽限：deiconify() 之后窗口要等一个真实事件循环周期才被映射，
            # 这一拍 ``winfo_rootx()`` 可能还是旧值（0），拿它和指针比必然"在外"。
            # 不宽限的话，机器一忙（首次映射 >280ms）浮层就会刚出现就自己收掉 ——
            # 用户看到的是"点了更换图标，闪一下什么都没了"。
            self._grace -= 1
            self._watch_miss = 0
            self._watch_job = self._after(WATCH_INTERVAL, self._watch)
            return
        if self._pointer_inside():
            self._watch_miss = 0
        else:
            self._watch_miss += 1
            if self._watch_miss >= WATCH_MISS_LIMIT:
                self.close(reason="blur")
                return
        self._watch_job = self._after(WATCH_INTERVAL, self._watch)

    def _pointer_inside(self) -> bool:
        try:
            if not self.winfo_viewable():
                return True              # 还没映射：几何不可信，保守留住
            px, py = self.winfo_pointerxy()
            x, y = self.winfo_rootx(), self.winfo_rooty()
        except Exception:  # noqa: BLE001
            return True                  # 拿不到就保守留住，别无故消失
        pad = theme.lpx(SAFE_ZONE)
        return (x - pad <= px <= x + self.winfo_width() + pad
                and y - pad <= py <= y + self.winfo_height() + pad)

    # ------------------------------------------------------------------
    # after 统一登记
    # ------------------------------------------------------------------
    def _after(self, delay: int, func, *args) -> str:
        job = self.after(delay, func, *args)
        self._jobs.append(job)
        return job

    def _cancel_jobs(self) -> None:
        for job in self._jobs:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._jobs.clear()
        self._watch_job = self._fade_job = None

    def _on_destroy(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._closed = True
        self._cancel_jobs()
        try:
            if self in ACTIVE:
                ACTIVE.remove(self)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 显示 / 关闭
    # ------------------------------------------------------------------
    def show_at(self, x: int, y: int, watch: bool = True) -> None:
        """在屏幕坐标 ``(x, y)``（**物理像素**）弹出，自动避开屏幕边角。"""
        self.update_idletasks()
        try:
            vx, vy = self.winfo_vrootx(), self.winfo_vrooty()
            vw, vh = self.winfo_vrootwidth(), self.winfo_vrootheight()
            sw, sh = vx + vw, vy + vh
        except Exception:  # noqa: BLE001
            vx, vy, sw, sh = 0, 0, 1920, 1080
        margin = theme.lpx(6)
        x = max(vx, min(x, sw - self.W - margin))
        y = max(vy, min(y, sh - self.H - margin))
        self.geometry(f"{self.W}x{self.H}+{int(x)}+{int(y)}")
        self.deiconify()
        self.attributes("-topmost", True)
        if watch:
            ACTIVE.append(self)
            self._watch_miss = 0
            self._grace = WATCH_GRACE_TICKS   # 等窗口真映射了再开始判死
            self._watch()
        self._fade_in()
        try:
            self.focus_force()           # 抢焦点才能立刻吃 Esc
        except Exception:  # noqa: BLE001
            pass

    def _fade_in(self) -> None:
        def step(i: int) -> None:
            self._fade_job = None
            if self._closed or not self.winfo_exists():
                return
            try:
                self.attributes("-alpha", min(1.0, 0.6 + i * (0.4 / FADE_STEPS)))
            except Exception:  # noqa: BLE001
                return
            if i < FADE_STEPS:
                self._fade_job = self._after(FADE_INTERVAL, step, i + 1)

        step(0)

    def close(self, reason: str = "") -> None:
        """幂等关闭。

        顺序很关键：**先 withdraw 再 destroy**。destroy 有可能失败（正被
        事件回调占用、解释器正在收尾等），那时如果只设了标志就返回，
        屏幕上会永远留着一块浮层 —— 这正是新建任务浮层当年"必须重启"的原因，
        这里按同一套纪律写。
        """
        if self._closed or self._closing:
            return
        self._closing = True
        self._cancel_jobs()
        try:
            if self in ACTIVE:
                ACTIVE.remove(self)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.withdraw()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass
        self._closed = True
        self._closing = False


def popup_icon_picker(master: tk.Misc, on_pick: Callable[[str], None],
                      current: str = "", title: str = TITLE,
                      x: Optional[int] = None,
                      y: Optional[int] = None) -> IconPickerPopup:
    """便捷入口：先收掉旧的（同一时间只允许一个），再在 ``(x, y)`` 弹出。

    ``x``/``y`` 缺省用当前指针位置减去半个浮层，让浮层"盖着指针"出现。
    """
    close_active()
    popup = IconPickerPopup(master, on_pick, current=current, title=title)
    if x is None or y is None:
        try:
            px, py = master.winfo_pointerxy()
        except Exception:  # noqa: BLE001
            px, py = 0, 0
        x = px - popup.W // 2 if x is None else x
        y = py - theme.lpx(10) if y is None else y
    popup.show_at(int(x), int(y))
    return popup
