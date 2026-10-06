# -*- coding: utf-8 -*-
"""任务主页：问候卡 + 分组列表 + 快速添加。

组内顺序（1.5.7 起）
--------------------
**自动实时排序**，口径只有一处（:func:`models.sort_key`）：未完成在上（按截止
日期升序、无期限垫底），已完成在下（按完成时间降序）。``Task.order`` 退化为
"同键兜底"位次，仍然由拖拽维护 —— 所以同一天到期 / 都没有期限的几条任务，
拖拽调先后依然有效；截止日期一旦不同，位置由日期说话。

拖拽排序原理
------------
1. 卡片按下鼠标只上报 ``(task_id, event)``，真正的移动/释放监听挂在顶层窗口；
2. 移动超过 5px 才算拖拽，避免误触；
3. 每次落点变化就改数据 + 重建列表。重建会销毁卡片控件，但因为监听在顶层窗口，
   拖拽不会被打断 —— 这也是为什么不在卡片上绑定 <B1-Motion>。
"""

from __future__ import annotations

import datetime as _dt
import logging
import tkinter as tk
from typing import Callable, Dict, List, Optional, Tuple

import customtkinter as ctk

from .. import icons, sound, stats, strings, theme
from ..config import data_dir
from ..models import (PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MID, REMIND_DEFAULT,
                      remind_label, sort_key, task_matches_filter)
from . import dialogs, due_picker, widgets
from .dropdown import InAppDropdown
from .task_card import GroupCard, TaskCard

WEEKDAY_FULL = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


# --------------------------------------------------------------------------
# 拖拽管理器
# --------------------------------------------------------------------------
class DragManager:
    """统一处理任务卡片的拖拽落点计算。"""

    THRESHOLD = theme.TASK_DRAG_THRESHOLD

    def __init__(self, page: "TaskPage") -> None:
        self.page = page
        self.task_id: Optional[str] = None
        self.active = False
        self._pending = False
        self._origin = (0, 0)
        self._last_target: Optional[Tuple[str, int]] = None

    def bind(self, root: tk.Misc) -> None:
        root.bind("<B1-Motion>", self.on_motion, add="+")
        root.bind("<ButtonRelease-1>", self.on_release, add="+")

    def on_press(self, task_id: str, event: tk.Event) -> None:
        if self.page.has_active_filter:
            return
        self.task_id = task_id
        self._pending = True
        self.active = False
        self._origin = (event.x_root, event.y_root)
        self._last_target = None

    def on_motion(self, event: tk.Event) -> None:
        if not self._pending or not self.task_id:
            return
        if not self.active:
            dx = abs(event.x_root - self._origin[0])
            dy = abs(event.y_root - self._origin[1])
            if dx + dy < self.THRESHOLD:
                return
            self.active = True
            self.page.render()          # 让被拖动的卡片立刻显示"抬起"样式
        target = self.page.locate(event.y_root)
        if target is None or target == self._last_target:
            return
        self._last_target = target
        self.page.apply_drop(self.task_id, target[0], target[1])

    def on_release(self, _event: tk.Event) -> None:
        was_active = self.active
        self._pending = False
        self.active = False
        self.task_id = None
        self._last_target = None
        if was_active:
            self.page.finish_drag()


# --------------------------------------------------------------------------
# 顶部问候卡
# --------------------------------------------------------------------------
class SummaryCard(ctk.CTkFrame):
    """紧凑统计条（本轮改动）：一行数字 + 一行细进度条。

    上一版是"问候语 + 28px 大数字 + 进度条 + 详情"的四行大卡（≈160px 高），
    在 470px 高的窗口里占掉 1/3 —— 正是用户说的"手机端 App 风格、留白过多"。
    现在压成两行、约 44px：

        0 缕光                    连续 0 天 · 本周 0 缕 · 累计 0 缕
        ▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁（细进度条）

    鼓励语在这一版**移到「光景」页**：那里本来就有一段完整的话，主页只放数据。
    同一件事说两遍，用户读到的是"同一个道理，两种说法"。
    """

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, corner_radius=theme.RADIUS_CARD,
                         fg_color=theme.pair("card"),
                         border_width=1, border_color=theme.pair("border"))
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        side = theme.lpx(theme.CARD_PAD_X)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=side, pady=(theme.lpx(4), 0))
        # 中间空列吃下弹性：数字贴左、详情贴右，不会挤在一起。
        head.grid_columnconfigure(1, weight=1)

        # 数字与单位放同一个 pack 容器里，读起来是"0 缕光"一个词组，
        # 而不是两个各自居中的块（上一版就是后者，看着很散）。
        # 1.5.12 收紧：单位贴数字更近（2px）、基线上移 2px，整行更紧凑。
        figure = ctk.CTkFrame(head, fg_color="transparent")
        figure.grid(row=0, column=0, sticky="w")
        self.number = widgets.TextLabel(figure, text="0",
                                        font=theme.font("number"),
                                        text_color=theme.pair("accent"))
        self.number.pack(side="left")
        self.unit = widgets.TextLabel(figure, text="缕光", font=theme.font("tiny"),
                                      text_color=theme.pair("text_muted"))
        self.unit.pack(side="left", padx=(theme.lpx(2), 0), pady=(theme.lpx(6), 0))

        self.detail = widgets.TextLabel(head, text="", font=theme.font("tiny"),
                                        text_color=theme.pair("text_muted"), anchor="e")
        self.detail.grid(row=0, column=2, sticky="e")

        # 1.5.11：常驻可见的专注入口。此前唯一入口是任务卡**悬停**才出现的
        # ⏱ 图标，新手根本找不到。统计条右侧放一枚同尺寸图标按钮，
        # 不依赖悬停；点击走 app.start_focus（"自由专注"，无任务也可计时）。
        self.focus_btn = widgets.IconButton(head, text="", icon="pomodoro", size=26,
                                            command=self.app.start_focus)
        self.focus_btn.grid(row=0, column=3, padx=(theme.lpx(6), 0))

        # 细进度条：4px 高就够表达"今天捡到几成"，上一版 8px 在小窗里像一条斑马线。
        # 1.5.12：与数字行的间距 2 -> 2、底部 5 -> 4，整卡收得更紧。
        self.progress = widgets.SoftProgress(self, width=200, height=4,
                                             bg=theme.c("card"), value=0.0)
        self.progress.grid(row=1, column=0, sticky="ew", padx=side,
                           pady=(theme.lpx(2), theme.lpx(4)))

        self.update_data()

    def update_data(self) -> None:
        store = self.app.store
        today = stats.today_count(store)
        done, total = stats.progress(store)
        self.number.configure(text=str(today))
        self.progress.set_value(done / total if total else 0.0)
        # 只留两个数字：这一行要和左边"9月23日 周三"挤在同一行里，
        # 三组数字（连续/本周/累计）在最小窗宽 310 下会把统计条撑到 312 逻辑
        # 像素（可用只有 290）。"累计"在「光景」页有完整呈现。
        self.detail.configure(
            text=f"连续 {stats.streak(store)} 天 · 本周 {stats.week_total(store)} 缕"
        )

    def refresh_theme(self) -> None:
        self.progress._bg = theme.c("card")
        self.progress.set_value(self.progress._value)


# --------------------------------------------------------------------------
# 提醒条
# --------------------------------------------------------------------------
class DueBanner(tk.Frame):
    """逾期 / 今日到期提醒条 —— **整条一张 PIL 位图**（1.5.10 重做）。

    呈现规则（需求 7）
    ------------------
    * 有逾期     -> 朱红底白字「有 N 项任务已逾期」，点击展开明细
    * 只有今日到期 -> 柔橘底白字「今天有 N 项任务到期」
    * 都没有     -> ``grid_remove()`` 完全隐藏，**不留空白条**

    为什么是"整条一张位图"（两代失败的教训）
    ----------------------------------------
    1.5.9 试过两版都不行：

    * **CTkFrame 底衬 + 四角抗锯齿补丁**：CTk 的圆角 polygon 与 PIL
      ``rounded_rectangle`` 不逐像素重合（角上毛刺）；渐入动画每帧同时重画
      CTk 底衬和四张补丁，两条渲染路径不同步（满屏残影）。
    * **canvas 位图底衬 + 文字控件叠加**：横幅里的 ``CTkLabel`` 都是
      ``fg_color="transparent"`` —— 而 CTk 的伪透明 = **拿"父容器色"不透明
      地铺满**。底衬搬进 canvas 后，CTk 解析到的父色是页面色，一块页面色
      的 label 正好盖住横幅中部，只露出上下 ``pady`` 的边（"空心框"）。

    现在图标、标题、箭头、明细**全部画进同一张位图**（4× 超采样 → LANCZOS），
    横幅里除了一块画布没有任何 CTk 控件 —— 渲染路径只有一条，物理上不可能
    再出现层序或颜色不同步。文字走 PIL/FreeType 灰度抗锯齿（ClearType 彩边
    一并消失），经 :func:`fonts.pil_font` 解析。

    展开 / 折叠：瞬时切换（改 ``height`` 触发一次重画）。**不做逐帧高度动画**
    —— 横幅在滚动容器里，逐帧改高 = 每帧重排整页，正是 1.5.4 "展开分组抖动"
    的成因；渐入动画只动底色、不动几何，与抖动无涉。
    """

    FADE_STEPS = theme.BANNER_FADE_STEPS
    FADE_INTERVAL = theme.BANNER_FADE_INTERVAL        # 45 × 40ms = 1.8s 名义值 ≈ 实际 2 秒
    _SS = theme.BANNER_BITMAP_SS                   # 超采样倍率（与 widgets._AA_SS 同值）

    def __init__(self, master: tk.Misc, app) -> None:
        self._bg_key = "banner_over"         # 底色键（强调色，见 refresh）
        # 1.5.29：横幅底改成"上道 / 下道 / 细描边 / 文字"四色一组，逐个记录键名，
        # 绘制时按当前明暗档取实际色值（Canvas 只认单色，必须现取）。
        self._top_key = "banner_over_top"
        self._bottom_key = "banner_over_bottom"
        self._border_key = "banner_over_border"
        self._text_key = "banner_over_text"
        self._expanded = False
        self._fade_job: Optional[str] = None
        self._cfg_job: Optional[str] = None  # <Configure> 防抖（1.5.16）
        self._draw_key = None                # (宽, 高, 底色键) 重绘去重
        self._preview_size = None            # 拖动中廉价画布的尺寸
        self._photo = None                   # 位图引用（防 GC）
        self._rendered_expanded = False       # 最近一次高质量位图的展开状态
        self._head_text = ""                 # 「有 N 项任务已逾期」
        self._icon_kind = "warning"          # warning / calendar
        # 1.5.29：明细改成**结构化**的 [(标题, 时间)] —— 标题要深红加粗、时间要
        # 暖灰小字，两者字号颜色都不同，一个拼好的字符串根本画不出来。
        self._detail_items: List[Tuple[str, str]] = []
        self._detail_more = False            # 超出上限时末尾补一行"…"
        super().__init__(master, bg=theme.c("bg"),
                         height=theme.lpx(theme.BANNER_HEAD_H))
        self.app = app
        self.store = app.store
        # 高度完全由 ``_apply_height`` 显式管理（折叠 36 / 展开按明细行数），
        # 不让内容反推 —— ⚠️ 1.5.18：canvas 从 place 改 pack 后必须**两个**
        # propagate 都关：grid_propagate 只忽略 grid 管理的子控件，pack 的
        # canvas（默认请求高 ~198 物理像素）照样能把 frame 撑成"假展开"
        # 高度、明细区一片空白（本轮预览图抓到的真回归）。
        self.grid_propagate(False)
        self.pack_propagate(False)

        self.canvas = tk.Canvas(self, bg=theme.c("bg"),
                                highlightthickness=0, bd=0, cursor="hand2",
                                height=1)
        # 1.5.18：place(relwidth/relheight) 改 pack(fill/expand) —— 同一容器里
        # 不混用两种几何管理器；画布跟随 frame 宽高，效果完全一致。
        self.canvas.pack(fill="both", expand=True)
        # 整条横幅都是点击热区（含箭头），点击切换展开/折叠。
        self.canvas.bind("<Button-1>", lambda _e: self._toggle_detail())
        # 宽高变化（窗口缩放 / 展开折叠 / 从隐藏到显示）→ 重画。
        # ⚠️ 1.5.16：必须**防抖** —— 旧写法依赖 (宽,高,色) 去重，但拖边框时
        # 宽度每帧都变，去重形同虚设：每帧都新建一张 宽×4 × 高×4 的 RGBA
        # 位图（PIL 绘制 + LANCZOS 缩小 + PhotoImage），主线程被吃满，其余
        # 控件的重绘全部饿死 —— 拖拽时整页空白块/错乱就是它。
        # 拖动中改用轻量 Tk 图元同步画出当前尺寸；停手后由 ResizeGate
        # 重建 4× 超采样位图。保留旧位图会在扩宽时露出空白。
        widgets.RESIZE_GATE.register(self, self._schedule_redraw)
        self.bind("<Configure>", self._on_configure, add="+")

    # ------------------------------------------------------------------
    # 内容位图
    # ------------------------------------------------------------------
    def _on_configure(self, event=None) -> None:
        # <Configure> 会从子控件冒泡上来（CTk 的 bind 还会挂到内部 _canvas），
        # 只认 frame 自己的事件
        if event is not None and getattr(event, "widget", None) is not self:
            return
        # 旧 PhotoImage 不会随 Canvas 扩宽；先用 Tk 图元画一帧轻量预览，
        # 让拖动中的横幅始终覆盖当前宽度，停手后再换回高质量位图。
        quality_layout = self._expanded != self._rendered_expanded
        dragging = widgets.RESIZE_GATE.configure_event(self)
        if dragging and not quality_layout:
            self._draw_resize_preview()
            # 拖拽中：除了不排队，还要撤掉"进入拖拽前"已排队的防抖任务，
            # 否则它会在拖拽中途漏跑一次完整重绘（1.5.17 探针实测）。
            self._cancel_redraw_job()
            return
        if quality_layout:
            self._cancel_redraw_job()
            self._cfg_job = self.after_idle(self._draw_quality_layout)
            return
        self._schedule_redraw()

    def _draw_quality_layout(self) -> None:
        self._cfg_job = None
        try:
            if self.winfo_exists():
                self._draw_banner()
        except Exception:  # noqa: BLE001
            pass

    def _draw_resize_preview(self) -> None:
        """拖拽中的**廉价**预览：Tk 图元画当前尺寸的一帧，停手后换高质量位图。

        1.5.29 起底色是浅橘红渐层，文字改深色 —— 预览也同步换色，否则拖边框
        那一瞬间会闪出一屏"白字压在浅底上"的不可读画面。
        """
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8 or (w, h) == self._preview_size:
            return
        self._preview_size = (w, h)
        self._draw_key = None
        top_c = theme.c(self._top_key)
        bottom_c = theme.c(self._bottom_key)
        text_c = theme.c(self._text_key)
        pad = theme.lpx(theme.BANNER_PAD_X)
        radius = min(theme.lpx(theme.BANNER_RADIUS), h // 2)
        self.canvas.delete("all")
        widgets.round_rect(self.canvas, 0, 0, w, h, radius,
                           fill=bottom_c, outline="")
        split = int(theme.lpx(theme.BANNER_HEAD_H) * theme.BANNER_SPLIT)
        widgets.round_rect(self.canvas, 0, 0, w, split + radius, radius,
                           fill=top_c, outline="")
        mid = theme.lpx(theme.BANNER_HEAD_H) / 2
        icon_size = theme.lpx(theme.BANNER_ICON)
        icon_fill = theme.c("banner_icon_fill")
        icon_edge = theme.c("banner_icon_edge")
        if self._icon_kind == "warning":
            self.canvas.create_polygon(
                pad + icon_size / 2, mid - icon_size / 2,
                pad + icon_size, mid + icon_size / 2,
                pad, mid + icon_size / 2,
                outline=icon_edge, fill=icon_fill, width=max(1, theme.lpx(1)))
            self.canvas.create_text(pad + icon_size / 2, mid + 1, text="!",
                                    fill=theme.c("banner_icon_mark"),
                                    font=theme.tkfont_spec("tiny"))
        else:
            self.canvas.create_rectangle(pad, mid - icon_size / 2,
                                         pad + icon_size, mid + icon_size / 2,
                                         outline=icon_edge, fill=icon_fill,
                                         width=max(1, theme.lpx(1)))
        self.canvas.create_text(
            pad + icon_size + theme.lpx(theme.BANNER_TEXT_GAP), mid,
            text=self._head_text, anchor="w", fill=text_c,
            font=theme.tkfont_spec("small_bold"))
        arrow_x = w - pad - theme.lpx(theme.BANNER_ARROW) / 2
        self.canvas.create_text(arrow_x, mid, text="▴" if self._expanded else "▾",
                                fill=text_c, font=theme.tkfont_spec("small_bold"))
        if self._expanded:
            y = theme.lpx(theme.BANNER_HEAD_H + theme.BANNER_DETAIL_TOP)
            line_h = theme.lpx(theme.BANNER_DETAIL_LINE_H)
            date_c = theme.c("banner_item_date")
            for title, when in self._detail_items:
                self.canvas.create_text(pad, y, text=f"· {title}", anchor="nw",
                                        fill=text_c, font=theme.tkfont_spec("tiny"))
                if when:
                    self.canvas.create_text(w - pad, y, text=when, anchor="ne",
                                            fill=date_c, font=theme.tkfont_spec("tiny"))
                y += line_h
            if self._detail_more:
                self.canvas.create_text(pad, y, text="…", anchor="nw",
                                        fill=date_c, font=theme.tkfont_spec("tiny"))

    def _cancel_redraw_job(self) -> None:
        if getattr(self, "_cfg_job", None) is not None:
            try:
                self.after_cancel(self._cfg_job)
            except Exception:  # noqa: BLE001
                pass
            self._cfg_job = None

    def _schedule_redraw(self) -> None:
        """80ms 防抖排队一次完整重绘（settle 与非拖拽路径共用入口）。"""
        self._cancel_redraw_job()
        self._cfg_job = self.after(theme.RESIZE_DEBOUNCE_MS, self._redraw_soon)

    def _redraw_soon(self) -> None:
        self._cfg_job = None
        if widgets.RESIZE_GATE.dragging:
            # 拖拽中漏网的防抖任务（事件间歇 >50ms 时重新排队的）：直接丢弃，
            # 松手后 ResizeGate 的 settle 回调（= 本方法）会统一完整重绘。
            return
        try:
            if self.winfo_exists():
                self._draw_banner()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _elide(text: str, font, max_w: float) -> str:
        """把 ``text`` 截到 ``max_w`` 内（放不下加省略号）。

        1.5.29：明细从"整段折行"改成"一条任务一行、标题超宽就省略号"——
        折行会让同一块横幅的行数随宽度跳变（拖窗口时高度跟着抖），而且
        折出来的第二行没有项目符号、也没有日期，读起来是断的。
        """
        try:
            if font.getlength(text) <= max_w:
                return text
        except Exception:  # noqa: BLE001
            return text
        keep = len(text)
        while keep > 0:
            cand = text[:keep].rstrip() + strings.TITLE_ELLIPSIS
            try:
                if font.getlength(cand) <= max_w:
                    return cand
            except Exception:  # noqa: BLE001
                return cand
            keep -= 1
        return strings.TITLE_ELLIPSIS

    def _apply_height(self) -> None:
        """折叠 = 头部高；展开 = 头部 + 明细行数 × 行高。行数变了才改高。"""
        head = theme.lpx(theme.BANNER_HEAD_H)
        if not self._expanded:
            if int(self.cget("height")) != head:
                self.configure(height=head)
            return
        w = max(2, self.winfo_width())
        line_h = theme.lpx(theme.BANNER_DETAIL_LINE_H)
        try:
            from .. import fonts
            dfont = fonts.pil_font("tiny", theme.BANNER_DETAIL_FONT_DELTA)
            if dfont is not None:
                asc, desc = dfont.getmetrics()
                line_h = asc + desc + theme.lpx(theme.BANNER_DETAIL_LEAD)
        except Exception:  # noqa: BLE001
            pass
        n = max(1, len(self._detail_items) + (1 if self._detail_more else 0))
        want = head + theme.lpx(theme.BANNER_DETAIL_TOP) + n * line_h
        if int(self.cget("height")) != want:
            self.configure(height=want)

    def _draw_banner(self, fade: float = 1.0) -> None:
        """重画整条位图（幂等；宽高与内容都没变就跳过）。

        ``fade`` < 1 时把**所有**色值都从页面底色插值过来 —— 渐入动画每帧调用
        一次（见 :meth:`_fade_in`）。只让底衬淡入、文字图标先跳出来的话会闪。
        """
        try:
            w = max(2, self.winfo_width())
            h = max(2, self.winfo_height())
        except Exception:  # noqa: BLE001
            return
        if w < 8 or h < 8:
            return
        # 去重键带上底色键、展开态与明细条数：换主题、切"逾期/今日到期"、展开
        # 折叠都会改变画面，只有真一样时才跳过（refresh 另外会显式清空一次）。
        key = (w, h, self._bg_key, self._expanded,
               self._head_text, tuple(self._detail_items), self._detail_more,
               theme.is_dark(), round(fade, 3))
        if key == self._draw_key:
            return
        self._draw_key = key
        from PIL import ImageTk
        image = self.render_image(w, h, fade)
        if image is None:
            return
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._preview_size = None
        self._rendered_expanded = self._expanded

    def render_image(self, w: int, h: int, fade: float = 1.0):
        """生成实际横幅位图；可在 withdrawn 根窗口下进行离屏验证。"""
        try:
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return
        try:
            from .. import fonts
            head_font = fonts.pil_font("small_bold", supersample=self._SS)
            detail_font = fonts.pil_font("tiny", theme.BANNER_DETAIL_FONT_DELTA,
                                         supersample=self._SS)
            # 1.5.29：日期比明细正文再小一档（11 → 10px），是"标题醒目、日期弱化"
            # 分层感的关键一笔 —— 同字号只换颜色的话两者仍然糊在一起。
            date_font = fonts.pil_font("tiny", theme.BANNER_DATE_FONT_DELTA,
                                       supersample=self._SS)
        except Exception:  # noqa: BLE001
            head_font = detail_font = date_font = None

        ss = self._SS
        radius = max(0, min(theme.lpx(theme.BANNER_RADIUS), h // 2, w // 2))
        W, H = w * ss - 1, h * ss - 1
        big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        head_h = theme.lpx(theme.BANNER_HEAD_H)
        pad_x = theme.lpx(theme.BANNER_PAD_X)
        mid_y = head_h // 2 * ss
        top_c = theme.mix(theme.c("bg"), theme.c(self._top_key), fade)
        bottom_c = theme.mix(theme.c("bg"), theme.c(self._bottom_key), fade)
        border_c = theme.mix(theme.c("bg"), theme.c(self._border_key), fade)
        text_c = theme.mix(theme.c("bg"), theme.c(self._text_key), fade)

        # 1) 底衬（1.5.29）：**上下两道 + 过渡带**，整体再按圆角裁切。
        #    旧版是一条纯平饱和色，压在暖白纸感界面里像一块贴纸，也看不出边界。
        #    两道浅色的分界落在头部内（**按头部高度算，不是整条高度**）——
        #    按整条比例算的话，展开明细后分界线会跑到列表中间，像"底衬在文字
        #    后面换了块颜色"，而不是头部的一个层次。
        split = int(theme.lpx(theme.BANNER_HEAD_H) * ss * theme.BANNER_SPLIT)
        band = max(1, int(theme.lpx(theme.BANNER_BAND_H) * ss))
        layer = Image.new("RGB", (w * ss, h * ss), bottom_c)
        ld = ImageDraw.Draw(layer)
        stop = max(0, split - band // 2)
        if stop:
            ld.rectangle([0, 0, w * ss - 1, stop - 1], fill=top_c)
        for k in range(band):
            y = stop + k
            if y > split + band:
                break
            ld.line([0, y, w * ss - 1, y],
                    fill=theme.mix(top_c, bottom_c, k / float(band)))
        mask = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, W, H],
                                              radius=radius * ss, fill=255)
        big.paste(layer, (0, 0), mask)
        # 极细描边（PIL 的 outline 向内收，不会顶出圆角）
        draw.rounded_rectangle([0, 0, W, H], radius=radius * ss,
                               outline=border_c,
                               width=max(1, theme.lpx(theme.BANNER_BORDER_W) * ss))
        # 2) 图标（琥珀黄填充 + 深橘描边 + 感叹号）
        self._draw_icon(draw, pad_x * ss, mid_y, ss)
        # 3) 标题文字（PIL/FreeType 灰度抗锯齿）
        if self._head_text and head_font is not None:
            icon_right = pad_x + theme.lpx(theme.BANNER_ICON)
            tx = (icon_right + theme.lpx(theme.BANNER_TEXT_GAP)) * ss
            draw.text((tx, mid_y), self._head_text, font=head_font,
                      fill=text_c, anchor="lm")
        # 4) 展开箭头（▾ / ▴，随展开状态翻转）
        ax = (w - pad_x - theme.lpx(theme.BANNER_ARROW)) * ss
        size = theme.lpx(theme.BANNER_ARROW) * ss
        cy = mid_y
        if self._expanded:
            pts = [(ax, cy + size * 0.2), (ax + size, cy + size * 0.2),
                   (ax + size / 2, cy - size * 0.45)]
        else:
            pts = [(ax, cy - size * 0.2), (ax + size, cy - size * 0.2),
                   (ax + size / 2, cy + size * 0.45)]
        draw.polygon(pts, fill=text_c)
        # 5) 展开明细（1.5.29 重排）
        #    旧版是"· 标题（09/25 10:10）"整串同色同字号，一眼看过去全是灰白一片。
        #    现在：小方块符号 + **标题深红**（与头部同色，保持"这是同一件事"的
        #    心理归类）+ 日期右对齐的**暖灰小字**，标题左、日期右两道，扫读时
        #    先看到"哪几件事"，需要时再看时间。
        if self._expanded and detail_font is not None:
            asc, desc = detail_font.getmetrics()
            line_h = asc + desc + theme.lpx(theme.BANNER_DETAIL_LEAD) * ss
            base = theme.lpx(theme.BANNER_PAD_X) * ss
            indent = base + theme.lpx(theme.BANNER_DETAIL_INDENT) * ss
            right = (w * ss) - indent
            top = head_h * ss + theme.lpx(theme.BANNER_DETAIL_TOP) * ss
            date_c = theme.c("banner_item_date")
            # 细分隔线：头部与明细的视觉分层（描边色向底色插值，不喧宾夺主）
            draw.line([indent, top - theme.lpx(4) * ss,
                       right, top - theme.lpx(4) * ss],
                      fill=theme.mix(border_c, bottom_c, 0.35),
                      width=max(1, ss // 2))
            bs = theme.lpx(theme.BANNER_BULLET) * ss
            gap = theme.lpx(theme.BANNER_BULLET_GAP) * ss
            title_gap = theme.lpx(theme.BANNER_TITLE_GAP) * ss
            rows = list(self._detail_items)
            for idx, (title, when) in enumerate(rows):
                row_top = top + idx * line_h
                cy = row_top + line_h / 2
                baseline = row_top + (line_h - (asc + desc)) / 2 + asc
                # 圆润小方块符号（比"·"更"排版化"，也更贴主题的圆角语言）
                draw.rounded_rectangle(
                    [indent, cy - bs / 2, indent + bs, cy + bs / 2],
                    radius=max(0, theme.lpx(theme.BANNER_BULLET_RADIUS) * ss),
                    fill=text_c)
                tx = indent + bs + gap
                date_w = date_font.getlength(when) if (when and date_font) else 0
                avail = right - tx - (date_w + title_gap if date_w else 0)
                draw.text((tx, baseline), self._elide(title, detail_font, avail),
                          font=detail_font, anchor="ls", fill=text_c)
                if date_w:
                    draw.text((right, baseline), when, font=date_font,
                              anchor="rs", fill=date_c)
            if self._detail_more:
                row_top = top + len(rows) * line_h
                baseline = row_top + (line_h - (asc + desc)) / 2 + asc
                draw.text((indent, baseline), strings.TITLE_ELLIPSIS,
                          font=detail_font, anchor="ls", fill=date_c)
        return big.resize((w, h), Image.LANCZOS)

    def _draw_icon(self, draw, x: int, cy: int, ss: int) -> None:
        """横幅左侧图标：**琥珀黄实心 + 深橘描边 + 感叹号**（1.5.29 重绘）。

        旧版是"白描边空心三角"：压在饱和朱红底上还算有轮廓，底衬一改成浅色
        就只剩一根细线，完全没有警示该有的分量。实心暖黄 + 深橘轮廓压在浅橘红
        底上，既能一眼看见，又不至于像交通锥那样刺眼。
        ``x/cy`` 已是超采样坐标，``cy`` = 头部行的中线。
        """
        s = theme.lpx(theme.BANNER_ICON) * ss
        fill_c = theme.c("banner_icon_fill")
        glow_c = theme.c("banner_icon_glow")
        edge_c = theme.c("banner_icon_edge")
        mark_c = theme.c("banner_icon_mark")
        ew = max(1, round(theme.lpx(theme.BANNER_ICON_EDGE_W) * ss))
        if self._icon_kind == "calendar":
            # 日历：琥珀底圆角方框 + 深橘轮廓 + 两个挂耳 + 一条横线
            draw.rounded_rectangle([x, cy - s * 0.42, x + s, cy + s * 0.42],
                                   radius=max(1, round(s * 0.16)),
                                   fill=fill_c, outline=edge_c, width=ew)
            for ex in (x + s * 0.26, x + s * 0.74):
                draw.line([ex, cy - s * 0.56, ex, cy - s * 0.28],
                          fill=edge_c, width=max(ew, round(s * 0.12)))
            draw.line([x + ew, cy - s * 0.08, x + s - ew, cy - s * 0.08],
                      fill=edge_c, width=max(1, round(s * 0.08)))
            return
        # 警告三角：顶点在上、底边在下
        top = (x + s / 2, cy - s * 0.50)
        right = (x + s * 0.99, cy + s * 0.44)
        left = (x + s * 0.01, cy + s * 0.44)
        draw.polygon([top, right, left], fill=fill_c)
        # 微弱高光：左上角一小块更亮的黄，平面色块因此有一点体积感
        draw.polygon([(x + s / 2, cy - s * 0.32),
                      (x + s * 0.22, cy + s * 0.30),
                      (x + s * 0.46, cy + s * 0.30)], fill=glow_c)
        # 描边走"闭合折线 + joint=curve"：polygon 自带的 outline 三个顶点是尖角，
        # 折线会在拐角处自动磨圆，14px 的小图标上这点差别很显眼。
        draw.line([top, right, left, top], fill=edge_c, width=ew, joint="curve")
        # 感叹号：竖条 + 圆点
        draw.line([x + s / 2, cy - s * 0.22, x + s / 2, cy + s * 0.10],
                  fill=mark_c, width=max(1, round(s * 0.13)))
        dot_r = max(1, round(s * 0.075))
        draw.ellipse([x + s / 2 - dot_r, cy + s * 0.24 - dot_r,
                      x + s / 2 + dot_r, cy + s * 0.24 + dot_r], fill=mark_c)

    # ------------------------------------------------------------------
    def _set_style(self, kind: str, icon: str) -> None:
        """一次把"逾期 / 今日到期"这组色键与图标类型都切过去（1.5.29）。

        为什么要收成一个方法：底色从一个常量变成了四个（上道、下道、描边、
        文字），散在 refresh 的两个分支里写八行赋值，很容易改漏一个 ——
        改漏的表现是"同一张横幅里橘红底配橘字"，肉眼看不出是哪一处错。
        """
        self._bg_key = f"banner_{kind}"
        self._top_key = f"banner_{kind}_top"
        self._bottom_key = f"banner_{kind}_bottom"
        self._border_key = f"banner_{kind}_border"
        self._text_key = f"banner_{kind}_text"
        self._icon_kind = icon

    def refresh(self) -> None:
        """按当前数据重算内容；无提醒时彻底隐藏。"""
        if not self.store.settings.get("due_banner", True):
            self._hide()
            return

        overdue = stats.overdue_tasks(self.store)
        today = stats.due_today_tasks(self.store)

        if overdue:
            self._set_style("over", "warning")
            self._head_text = f"有 {len(overdue)} 项任务已逾期"
            self._detail_items = [(t.title or strings.UNNAMED_TITLE,
                                   t.due_date.strftime("%m/%d %H:%M"))
                                  for t in overdue[:theme.BANNER_DETAIL_MAX]]
            self._detail_more = len(overdue) > theme.BANNER_DETAIL_MAX
            self._show()
        elif today:
            self._set_style("soon", "calendar")
            self._head_text = f"今天有 {len(today)} 项任务到期"
            self._detail_items = [(t.title or strings.UNNAMED_TITLE,
                                   t.due_date.strftime("%H:%M"))
                                  for t in today[:theme.BANNER_DETAIL_MAX]]
            self._detail_more = len(today) > theme.BANNER_DETAIL_MAX
            self._show()
        else:
            self._hide()
        self._draw_key = None
        self._apply_height()
        self._draw_banner()

    # ------------------------------------------------------------------
    def _show(self) -> None:
        if self.winfo_ismapped():
            # 已经在显示：只换底色，不重播渐入（否则每次刷新都闪一次）
            self._draw_key = None
            self._draw_banner()
            return
        self.grid()
        self._fade_in()

    def _hide(self) -> None:
        self._cancel_fade()
        self._expanded = False
        self.grid_remove()

    def _cancel_fade(self) -> None:
        if self._fade_job is not None:
            try:
                self.after_cancel(self._fade_job)
            except Exception:  # noqa: BLE001
                pass
            self._fade_job = None

    def _fade_in(self) -> None:
        """从页面底色渐入到提醒条底色（ease-out）。

        1.5.29 起底衬是"上下两道渐层"，渐入改成对**每一档色值**一起做
        "页面底色 → 目标色"的插值（把 ``fade`` 交给 :meth:`_draw_banner`），
        描边、文字、图标同步淡入。只让底衬淡入、文字先跳出来会闪一帧。
        只动颜色不动几何，与 1.5.4 的"逐帧改高度 = 每帧整页重排"的抖动成因无涉。
        """
        self._cancel_fade()
        steps = self.FADE_STEPS

        def step(i: int) -> None:
            if not self.winfo_exists():
                return
            if widgets.RESIZE_GATE.dragging:
                # 拖拽中冻结（1.5.17）：原地重试同一步，不重画位图 ——
                # 渐入只是"变颜色"，冻几帧看不出来；松手后自动续播。
                self._fade_job = self.after(self.FADE_INTERVAL, step, i)
                return
            t = i / steps
            eased = 1 - (1 - t) ** 3          # ease-out cubic
            self._draw_key = None
            self._draw_banner(eased)
            if i < steps:
                self._fade_job = self.after(self.FADE_INTERVAL, step, i + 1)
            else:
                self._fade_job = None
                self._draw_key = None
                self._draw_banner()

        step(0)

    def _toggle_detail(self) -> None:
        self._expanded = not self._expanded
        self._apply_height()                   # 高度一变，<Configure> 触发重画

    def collapse(self) -> None:
        """收起明细（幂等）。供"别的浮层要弹出"时让路用。

        与 ``_toggle_detail`` 分开，是因为语义不同：这个是"收起来"，
        不管当前是不是展开的，也不该在某些情况下变成"展开"。
        提醒条本体（那一条红/橘色的横条）保持显示 —— 逾期数字本身是有用的信息，
        用户要收的只是它展开的 6 行明细。
        """
        if not self._expanded:
            return
        self._expanded = False
        self._apply_height()


# --------------------------------------------------------------------------
# 任务主页
# --------------------------------------------------------------------------
class TaskPage(ctk.CTkFrame):
    FILTER_KEYS = {
        strings.TASK_FILTER_OPTIONS[0]: "all",
        strings.TASK_FILTER_OPTIONS[1]: "today",
        strings.TASK_FILTER_OPTIONS[2]: "overdue",
        strings.TASK_FILTER_OPTIONS[3]: "open",
        strings.TASK_FILTER_OPTIONS[4]: "done",
    }

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.store = app.store
        self.group_cards: Dict[str, GroupCard] = {}
        self.card_map: Dict[str, TaskCard] = {}
        self.drag = DragManager(self)
        self.filter_key = "all"
        self._search_job: Optional[str] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # ---- 统计条 ----
        self.summary = SummaryCard(self, app)
        self.summary.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAGE_GAP))

        # ---- 提醒条（无提醒时整体隐藏，不占空间）----
        self.banner = DueBanner(self, app)
        self.banner.grid(row=1, column=0, sticky="ew", pady=(0, theme.PAGE_GAP))
        self.banner.grid_remove()

        # ---- 搜索与筛选 ----
        self.search_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.search_bar.grid(row=2, column=0, sticky="ew",
                             pady=(0, theme.lpx(theme.PAGE_GAP)))
        self.search_bar.grid_columnconfigure(0, weight=1)
        self.search_shell = ctk.CTkFrame(
            self.search_bar, height=32, corner_radius=16,
            fg_color=theme.pair("card"), border_width=1,
            border_color=theme.pair("control_border"))
        self.search_shell.grid(row=0, column=0, sticky="ew",
                               padx=(0, theme.lpx(8)))
        self.search_shell.grid_propagate(False)
        self.search_shell.grid_columnconfigure(1, weight=1)
        # 清除按钮隐藏时，grid_remove() 默认会把第 2 列一并收缩；Entry
        # 随之铺到外壳最右边，方角盖住圆角外壳右上/右下角。固定保留按钮槽，
        # 让空搜索和有内容两种状态使用完全相同的输入区宽度。
        self.search_shell.grid_columnconfigure(
            2, minsize=theme.lpx(30))
        self.search_shell.grid_rowconfigure(0, weight=1)
        self._search_icon_image = icons.get_ctk("search", icons.SIZE_SMALL)
        self.search_icon = ctk.CTkLabel(
            self.search_shell, text="", image=self._search_icon_image,
            width=18, fg_color="transparent")
        self.search_icon.grid(row=0, column=0, padx=(10, 3))
        self.search_entry = ctk.CTkEntry(
            self.search_shell, placeholder_text=strings.TASK_SEARCH_HINT,
            font=theme.font("small"), height=28, corner_radius=0,
            fg_color="transparent", border_width=0,
            text_color=theme.pair("text"),
            placeholder_text_color=theme.pair("text_done"),
        )
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.search_clear = widgets.IconButton(
            self.search_shell, text="×", icon="close", icon_size=12,
            size=24, radius=12, fg="transparent",
            hover=theme.pair("ghost_hover"),
            text_color=theme.pair("text_muted"),
            command=self._clear_search,
        )
        self.search_clear.grid(row=0, column=2, padx=(2, 4))
        self.search_clear.grid_remove()
        self.search_entry.bind(
            "<FocusIn>", lambda _e: self.search_shell.configure(
                border_color=theme.pair("accent")), add="+")
        self.search_entry.bind(
            "<FocusOut>", lambda _e: self.search_shell.configure(
                border_color=theme.pair("control_border")), add="+")
        self.search_entry.bind("<KeyRelease>", self._queue_search_render, add="+")
        self.search_entry.bind("<Return>", self._render_search_now, add="+")
        self.search_entry.bind("<Escape>", self._clear_search, add="+")
        self.filter_menu = InAppDropdown(
            self.search_bar, values=list(self.FILTER_KEYS), width=106, height=32,
            min_popup_width=150, title="任务筛选", anchor="center",
            command=self._set_filter,
        )
        self.filter_menu.set(strings.TASK_FILTER_OPTIONS[0])
        self.filter_menu.grid(row=0, column=1, sticky="e")

        # ---- 分组滚动区 ----
        # height 必须给一个"小"的初值：滚动区的内容高度会被当作请求高度上报，
        # 若不限制，它会把整个窗口的请求高度顶起来。
        # 真正的显示高度由 grid 的 weight=1 撑开。
        self.scroll = widgets.ThinScrollFrame(
            self, height=200,
        )
        self.scroll.grid(row=3, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        # ---- 快速添加：不再有底部常驻输入栏（改动一）----
        # 入口改为三处：页面顶部"＋ 新建任务"按钮 / 分组标题"＋" / 全局快捷键，
        # 点击后统一弹 QuickAddPopup 浮层（见 app.open_quick_add）。

        self.render()

    def _default_group(self):
        gid = self.store.settings.get("default_group")
        grp = self.store.group(gid) or (self.store.groups[0] if self.store.groups else None)
        return grp

    def _set_default_group(self, group_id: str) -> None:
        self.store.set_setting("default_group", group_id)
        self.app.save_data()

    @property
    def has_active_filter(self) -> bool:
        return (self.filter_key != "all"
                or bool(self.search_entry.get().strip()))

    def _queue_search_render(self, _event=None) -> None:
        self._sync_search_clear()
        if self._search_job is not None:
            try:
                self.after_cancel(self._search_job)
            except Exception:  # noqa: BLE001
                pass
        self._search_job = self.after(140, self._render_search_now)

    def _render_search_now(self, _event=None) -> str:
        if self._search_job is not None:
            try:
                self.after_cancel(self._search_job)
            except Exception:  # noqa: BLE001
                pass
            self._search_job = None
        self.render()
        return "break"

    def _sync_search_clear(self) -> None:
        """只有存在查询文字时才显示清除叉，且它只清搜索内容。"""
        try:
            if self.search_entry.get():
                self.search_clear.grid()
            else:
                self.search_clear.grid_remove()
        except Exception:  # noqa: BLE001
            pass

    def _set_filter(self, label: str) -> None:
        self.filter_key = self.FILTER_KEYS.get(label, "all")
        self.render()

    def _clear_search(self, _event=None) -> str:
        self.search_entry.delete(0, "end")
        self._sync_search_clear()
        self._render_search_now()
        try:
            self.search_entry.focus_set()
        except Exception:  # noqa: BLE001
            pass
        return "break"

    def clear_filters(self) -> None:
        self.filter_key = "all"
        self.filter_menu.set(strings.TASK_FILTER_OPTIONS[0])
        self.search_entry.delete(0, "end")
        self._sync_search_clear()
        self.render()

    def _matches_filter(self, task, query: str) -> bool:
        return task_matches_filter(task, query=query, filter_key=self.filter_key)

    def _render_no_results(self) -> None:
        holder = ctk.CTkFrame(self.scroll, fg_color="transparent")
        holder.pack(fill="x", padx=theme.lpx(12), pady=theme.lpx(28))
        widgets.TextLabel(
            holder, text=strings.TASK_FILTER_EMPTY_TITLE, font=theme.font("h2"),
            text_color=theme.pair("text"), anchor="center",
        ).pack(fill="x")
        widgets.TextLabel(
            holder, text=strings.TASK_FILTER_EMPTY_HINT, font=theme.font("small"),
            text_color=theme.pair("text_muted"), anchor="center",
        ).pack(fill="x", pady=(theme.lpx(4), theme.lpx(10)))
        ctk.CTkButton(
            holder, text=strings.TASK_FILTER_CLEAR, height=28, corner_radius=14,
            fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text"), font=theme.font("small"),
            command=self.clear_filters,
        ).pack(anchor="center")

    def add_quick_task(self, title: str, group_id: str = "",
                       due_date=None, remind_offset=REMIND_DEFAULT) -> None:
        """浮层提交：新建任务 + 给新卡片一个呼吸反馈。

        位置**不再手写**：``store.add_task`` 会把新任务排到"同键最前"，而组内
        最终位置由 :func:`models.sort_key` 派生（有截止日期按日期、没有则落在
        无期限区最上面）。上一版这里还额外调了一次 ``move_task_to(..., 0)``
        硬插到列表顶部 —— 在自动排序下那一步是无效动作，且会让"新任务永远在
        第一条"和"按截止日期排"两条规则互相打架。
        """
        grp = self.store.group(group_id) if group_id else None
        if grp is None:
            grp = self._default_group()
        if grp is None:
            return
        task = self.store.add_task(title, grp.id, PRIORITY_MID, due_date=due_date,
                                   remind_offset=remind_offset)
        self.app.save_data()
        self.render()
        self._refresh_chrome()
        card = self.card_map.get(task.id)
        if card is not None:
            try:
                widgets.pulse(card, steps=8, interval=30)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render(self) -> None:
        """重建分组列表（拖拽过程中也会调用，代价可控）。"""
        widgets.clear(self.scroll)
        self.group_cards.clear()
        self.card_map.clear()

        self.banner.refresh()

        all_tasks = self.store.all_tasks()
        tasks_total = len(all_tasks)
        if tasks_total == 0:
            self._render_empty_state(has_tasks=False, all_done=False)
            self.summary.update_data()
            return

        query = self.search_entry.get().strip().casefold()
        visible_tasks = [t for t in all_tasks if self._matches_filter(t, query)]
        if self.has_active_filter and not visible_tasks:
            self._render_no_results()
            self.summary.update_data()
            return
        visible_ids = {task.id for task in visible_tasks}

        # "全部完成"是第三个空状态（需求 ④）。它**不替换**任务列表 ——
        # 全做完就把完成的东西藏起来，用户想复查或撤销都没地方点。
        # 所以只在列表上方加一条轻量的完成态卡片。
        if not self.has_active_filter and stats.all_done_today(self.store):
            self._render_empty_state(has_tasks=True, all_done=True)

        for grp in self.store.groups:
            tasks = [t for t in self.store.tasks_in(grp.id) if t.id in visible_ids]
            if self.has_active_filter and not tasks:
                continue
            card = GroupCard(self.scroll, self.app, grp, tasks,
                             render_task=self._render_task, dispatch=self.dispatch,
                             visible_task_ids=(visible_ids
                                               if self.has_active_filter else None))
            card.pack(fill="x", padx=0, pady=(0, 1))
            self.group_cards[grp.id] = card

        self.summary.update_data()

    def _render_empty_state(self, has_tasks: bool = False,
                            all_done: bool = False) -> None:
        """空状态卡片（需求 ④：文案只能来自 ``strings``，且必须折行 + 居中）。

        三个"没有内容可看"的状态共用一个渲染器，靠 ``strings.empty_title/hint``
        分派：无任务 / 全部完成 / （加载中，见 strings 的说明）。

        为什么这几行标签要显式 ``wraplength`` + ``justify="center"``：
        ``CTkLabel`` 默认**不折行**，窗口被拖窄时整行文字直接溢出卡片边界
        （这就是截图里"文字被裁"的来源）；而 ``anchor`` 默认是 ``center``，
        文本块自身居中但**行内**左对齐，多行时右边缘参差不齐。两个都要给。
        """
        title = strings.empty_title(has_tasks, all_done)
        hint = strings.empty_hint(has_tasks, all_done)
        if not title:
            return
        # 折行宽度：先给一个基于设计窗宽的上限，随后由 _fit() 按**卡片实测宽度**
        # 修正。只给常量是不够的 —— 窗口被拖到最小尺寸（380 逻辑像素）时，
        # 380 逻辑宽的折行盒比卡片还宽，文字照样被裁。
        wrap = theme.EMPTY_WRAP

        if all_done:
            # 完成反馈采用居中纵向排布：太阳、文案和按钮共用同一中心线。
            holder = ctk.CTkFrame(
                self.scroll, corner_radius=theme.RADIUS_CARD,
                fg_color=theme.pair("card"), border_width=1,
                border_color=theme.pair("tooltip_border"))
            holder.pack(fill="x", pady=(0, 0))
            inner = ctk.CTkFrame(holder, fg_color="transparent", corner_radius=0)
            inner.pack(fill="x", padx=theme.lpx(16), pady=theme.lpx(7))
            badge = ctk.CTkFrame(
                inner, width=32, height=32, corner_radius=16,
                fg_color=theme.pair("accent_soft"))
            badge.pack(anchor="center", pady=(0, theme.lpx(4)))
            badge.pack_propagate(False)
            try:
                sun = icons.get_ctk("grp_sun", 20)
            except Exception:  # noqa: BLE001
                sun = None
            ctk.CTkLabel(badge, text="", image=sun).place(
                relx=0.5, rely=0.5, anchor="center")

            title_label = widgets.TextLabel(
                inner, text=title, font=theme.font("h2"),
                text_color=theme.pair("text"), anchor="center", justify="center",
                wraplength=wrap)
            title_label.pack(fill="x")
            hint_label = widgets.TextLabel(
                inner, text=hint, font=theme.font("small"),
                text_color=theme.pair("text_muted"), anchor="center", justify="center",
                wraplength=wrap)
            hint_label.pack(fill="x", pady=(theme.lpx(2), 0))
            ctk.CTkButton(
                inner, text=strings.FOCUS_CTA_AGAIN, width=116, height=28,
                corner_radius=14, fg_color=theme.pair("accent"),
                hover_color=theme.pair("accent_hover"),
                text_color=theme.ON_ACCENT, font=theme.font("small"),
                command=self.app.start_focus,
            ).pack(anchor="center", pady=(theme.lpx(7), 0))

            def _fit_done_labels(_event=None) -> None:
                try:
                    width = inner.winfo_width()
                except Exception:  # noqa: BLE001
                    return
                if width == _fit_done_labels.last[0]:
                    return
                _fit_done_labels.last[0] = width
                avail = int((width - theme.lpx(8)) / (theme.scale() or 1.0))
                if avail > 40:
                    title_label.configure(wraplength=avail)
                    hint_label.configure(wraplength=avail)

            _fit_done_labels.last = [0]
            inner.bind("<Configure>", _fit_done_labels, add="+")
            widgets.RESIZE_GATE.register(inner, _fit_done_labels)
            self.after(0, _fit_done_labels)
            return

        holder = ctk.CTkFrame(self.scroll, corner_radius=theme.RADIUS_CARD,
                              fg_color=theme.pair("card"),
                              border_width=1, border_color=theme.pair("border"))
        holder.pack(fill="x", pady=(0, 0))
        if not all_done:
            widgets.EmptyIllustration(holder, bg=theme.c("card")).pack(
                pady=(theme.lpx(theme.EMPTY_PAD_TOP),
                      theme.lpx(theme.EMPTY_ICON_GAP)))
        else:
            # "全部完成"是好事，给一枚太阳当主视觉，不用"空无一物"的插画。
            # ⚠️ get_ctk 的 size 是**逻辑像素**（CTkImage 内部按缩放重渲染），
            # 传 lpx() 后的物理值会让图标大一倍。
            try:
                _img = icons.get_ctk("grp_sun", 30)
            except Exception:  # noqa: BLE001
                _img = None
            if _img is not None:
                ctk.CTkLabel(holder, text="", image=_img).pack(
                    pady=(theme.lpx(theme.EMPTY_PAD_TOP),
                          theme.lpx(theme.EMPTY_ICON_GAP)))

        side = theme.lpx(theme.EMPTY_SIDE_PAD)
        title_label = ctk.CTkLabel(holder, text=title, font=theme.font("h2"),
                                   text_color=theme.pair("text"),
                                   wraplength=wrap, justify="center")
        title_label.pack(fill="x", padx=side)
        hint_label = ctk.CTkLabel(holder, text=hint, font=theme.font("small"),
                                  text_color=theme.pair("text_muted"),
                                  wraplength=wrap, justify="center")
        hint_label.pack(fill="x", padx=side, pady=(theme.lpx(theme.EMPTY_TEXT_GAP), 0))

        def _fit_labels(_event=None) -> None:
            """按卡片实测宽度重算折行宽度（窗口缩放后仍然不出界）。

            ⚠️ winfo_width() 是物理像素、wraplength 是逻辑值（CTk 内部
            自己乘缩放），先除回 scale() —— 1.5.13 统一修正。
            ⚠️ 1.5.16 防抖 + 宽度去重：拖边框时此回调每帧都发，逐帧
            configure(wraplength) 会连累 CTkLabel 反复重排。
            """
            try:
                w = holder.winfo_width()
            except Exception:  # noqa: BLE001
                return
            if w == _fit_labels.last[0]:    # 尺寸没变就跳过
                return
            _fit_labels.last[0] = w
            avail = int((w - side * 2) / (theme.scale() or 1.0))
            if avail <= 40:
                return
            for lbl in (title_label, hint_label):
                try:
                    lbl.configure(wraplength=avail)
                except Exception:  # noqa: BLE001
                    pass

        _fit_labels.last = [0]
        job = {"id": None}

        def _on_cfg(_event=None) -> None:
            # 1.5.19：布局适配（便宜、内部有宽度未变跳过）当帧 after_idle 执行；
            # 冻结只留给贵重绘（横幅 4× 位图）。
            if job["id"] is not None:
                return                  # 本帧已排过，合并
            job["id"] = holder.after_idle(_fit_labels)

        widgets.RESIZE_GATE.register(holder, _fit_labels)

        holder.bind("<Configure>", _on_cfg, add="+")
        self.after(0, _fit_labels)      # 布局完成后再校一次

        if not all_done:
            ctk.CTkButton(
                holder, text=strings.EMPTY_ALL_CTA, height=28, corner_radius=14,
                fg_color=theme.pair("accent"), hover_color=theme.pair("accent_hover"),
                text_color=theme.ON_ACCENT, font=theme.font("small"),
                command=lambda: self.dispatch("new_task", None),
            ).pack(pady=(theme.lpx(theme.EMPTY_CTA_TOP), theme.lpx(4)))
            # 1.5.11：空状态也放一个专注入口 —— 还没有任务也能"自由专注"，
            # 顺带让新用户知道有专注这回事（不依赖任务卡悬停才可见）。
            ctk.CTkButton(
                holder, text=strings.FOCUS_CTA, height=28, corner_radius=14,
                fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                text_color=theme.pair("text"), font=theme.font("small"),
                command=self.app.start_focus,
            ).pack(pady=(0, theme.lpx(theme.EMPTY_PAD_BOTTOM)))

    def _render_task(self, master: tk.Misc, task, index: int) -> TaskCard:
        card = TaskCard(
            master, task, index,
            dispatch=self.dispatch,
            on_press=self.drag.on_press,
            pomodoro_state=self.app.pomodoro.state,
            dragging=(self.drag.active and self.drag.task_id == task.id),
        )
        card.bind("<Double-Button-1>", lambda _e: self.dispatch("edit", task.id), add="+")
        card.title_label.bind("<Double-Button-1>",
                              lambda _e: self.dispatch("edit", task.id), add="+")
        self.card_map[task.id] = card
        return card

    # ------------------------------------------------------------------
    # 拖拽落点
    # ------------------------------------------------------------------
    def locate(self, y_root: int) -> Optional[Tuple[str, int]]:
        """根据鼠标纵坐标判断应落到哪个分组的第几个位置。"""
        nearest: Optional[Tuple[str, int]] = None
        best_dist = 10 ** 9
        for gid, gc in self.group_cards.items():
            container = gc.container
            if not container.winfo_ismapped():
                continue
            top = container.winfo_rooty()
            bottom = top + container.winfo_height()
            cards = gc.cards
            if top - 12 <= y_root <= bottom + 12:
                pos = len(cards)
                for i, card in enumerate(cards):
                    center = card.winfo_rooty() + card.winfo_height() / 2
                    if y_root < center:
                        pos = i
                        break
                dragged_index = next(
                    (i for i, c in enumerate(cards) if c.task.id == self.drag.task_id), -1
                )
                if 0 <= dragged_index < pos:
                    pos -= 1
                return gid, pos
            dist = min(abs(y_root - top), abs(y_root - bottom))
            if dist < best_dist:
                best_dist = dist
                nearest = (gid, len(cards))
        return nearest

    def apply_drop(self, task_id: str, group_id: str, index: int) -> None:
        if self.has_active_filter:
            return
        grp = self.store.group(group_id)
        if grp is None:
            return
        if grp.collapsed:
            grp.collapsed = False      # 拖到折叠分组时自动展开
        self.store.move_task_to(task_id, group_id, index)
        self.app.save_data()
        self.render()

    def finish_drag(self) -> None:
        self.app.save_data()
        self.render()
        self._refresh_chrome()

    # ------------------------------------------------------------------
    # 动作分发
    # ------------------------------------------------------------------
    def dispatch(self, action: str, payload):
        if action == "groups":
            return list(self.store.groups)
        if action == "toggle":
            self._toggle(*payload)
        elif action == "edit":
            self._edit(payload)
        elif action == "delete":
            self._delete(payload)
        elif action == "pomodoro":
            self.app.toggle_pomodoro(payload)
        elif action == "move":
            task_id, group_id = payload
            self.store.update_task(task_id, group_id=group_id)
            self.app.save_data()
            self.render()
            self._refresh_chrome()
        elif action == "due":
            self._pick_due(payload)
        elif action == "new_task":
            self.app.open_quick_add()
        elif action == "add_to_group":
            self.app.open_quick_add(group_id=payload)
        elif action == "toggle_group":
            self._toggle_group(payload)
        elif action == "rename_group":
            self._rename_group(payload)
        elif action == "icon_group":
            self._icon_group(payload)
        elif action == "delete_group":
            self._delete_group(payload)
        elif action == "default_group":
            self._set_default_group(payload)
        return None

    def _toggle_group(self, group_id: str) -> None:
        """折叠/展开分组（1.5.4：瞬时切换，不整页重建、也不播高度动画）。

        两层"不做"都要留着：

        * **不 ``render()`` 重建整页** —— 重建是瞬切，而且拖拽时会打断；
          这里只动这一个 GroupCard 的内容显隐，其余控件完全不受影响。
        * **不播 200ms 高度动画**（1.5.4 删）—— 旧实现逐帧改容器 ``height``，
          会把滚动容器里下方所有分组每 25ms 推一次，就是用户报的"展开时抖动"。
        """
        grp = self.store.group(group_id)
        if grp is None:
            return
        card = self.group_cards.get(group_id)
        if card is None:
            self.store.toggle_group_collapsed(group_id)
            self.app.save_data()
            self.render()
            return
        collapsing = not grp.collapsed
        grp.collapsed = collapsing
        self.app.save_data()
        card.set_collapsed(collapsing)
        # 1.5.12：箭头不再瞬移换向 —— GroupCard 内部播 150ms 旋转动画
        # （只换图标位图，不动布局），折叠态同步切换浅米底衬。
        card.animate_chevron(collapsing)

    def _toggle(self, task_id: str, value: bool) -> None:
        """勾选 / 取消勾选：**只重排这一张卡片，其他卡片一动不动**。

        为什么不能直接 ``render()``
        ---------------------------
        整页重建会让所有分组卡片先销毁再重建，视觉上"闪一下"，而且用户刚
        点击的那张卡片会从鼠标下方消失（勾错的人连撤销都找不到地方点）。
        所以这里只做三件事：改数据 → 按排序键重排该卡片的显示顺序 →
        原地刷新它自己。

        排序规则（1.5.7 起是**自动实时排序**）
        ---------------------------------------
        位置完全由 :func:`models.sort_key` 决定：未完成按截止日期升序（无期限
        的垫底）、已完成按完成时间降序，未完成整体在已完成之上。所以勾选完成的
        那一刻，这张卡会自己"沉"到已完成区 —— 不需要在这里手工插入分隔，
        也不允许再出现"只按 done 分组、组内保持原先后"的旧写法（那会让同一份
        数据在不同视图里顺序不一致）。
        """
        task = self.store.task(task_id)
        if task is None:
            return
        group_id = task.group_id
        self.app.reminder.forget(task_id)
        self.store.set_done(task_id, value)
        self.app.save_data()

        if self.filter_key != "all":
            self.render()
            self._refresh_chrome()
            if value:
                self.app.sync_tray()
                if stats.all_done_today(self.store):
                    self.app.celebrate()
                else:
                    siblings = self.store.tasks_in(group_id)
                    if siblings and all(t.done for t in siblings):
                        self.app.celebrate(group_only=True)
            return

        self._reorder_for(task_id)

        card = self.card_map.get(task_id)
        if card is not None:
            # 原地更新，保留打勾动画。
            # （这个方法此前在 TaskCard 上不存在，抛出 AttributeError 把后面的
            #   计数刷新、摘要刷新、庆祝动画全都带崩了 —— 曾修过的真 bug。）
            card.refresh_state(value)
        for gc in self.group_cards.values():
            gc.update_count()
        self.summary.update_data()
        self.banner.refresh()
        self._refresh_chrome()
        if value:
            self.app.sync_tray()
            if stats.all_done_today(self.store):
                anchor = card.check if card is not None else None
                self.app.celebrate(anchor=anchor)
            else:
                # 某个分组刚好清空：来一发小型粒子（需求 14）
                siblings = self.store.tasks_in(group_id)
                if siblings and all(t.done for t in siblings):
                    self.app.celebrate(group_only=True)

    def _reorder_for(self, task_id: str) -> None:
        """按排序键把 ``task_id`` 所在分组的卡片重新 pack 一遍。

        重排只发生在**同一个分组内部**，且用 ``pack_forget`` + ``pack`` 原地
        重排而不是重建控件 —— 卡片对象、它的动画状态、鼠标悬停态都还在。

        顺序取自 ``models.sort_key``（与 ``store.tasks_in`` 同一把尺子），
        所以界面顺序和导出 / 其他视图永远一致。

        "平滑"是怎么做到的
        ------------------
        归位是**一次性布局**（所有卡片在一个 tick 内就位，没有中间态、没有
        逐帧重排），搬完之后再对移动的那张卡做一次底色柔光脉冲（``flash_settle``）
        让视线跟得上。刻意不做逐帧位移动画：那是 1.5.4 里"展开分组画面抖动"的
        同一个成因（每帧重排整页）。
        """
        task = self.store.task(task_id)
        if task is None:
            return
        gc = self.group_cards.get(task.group_id)
        if gc is None:
            return
        ordered = sorted(gc.cards, key=lambda c: sort_key(c.task))
        if [id(c) for c in ordered] == [id(c) for c in gc.cards]:
            return                      # 顺序没变，不要白动布局（避免闪烁）
        try:
            for card in ordered:
                card.pack_forget()
            for card in ordered:
                card.pack(fill="x", pady=(0, theme.TASK_ROW_GAP))
        except Exception:  # noqa: BLE001
            return
        gc.cards = ordered
        moved = self.card_map.get(task_id)
        if moved is not None:
            try:
                moved.flash_settle()
            except Exception:  # noqa: BLE001
                pass

    def _edit(self, task_id: Optional[str], group_id: Optional[str] = None) -> None:
        task = self.store.task(task_id) if task_id else None
        dialogs.TaskDialog(
            self.app, task=task, preset_group=group_id,
            on_save=self._on_task_saved,
        )

    def _on_task_saved(self, task_id: Optional[str], fields: dict) -> None:
        if task_id:
            self.store.update_task(task_id, **fields)
        else:
            task = self.store.add_task(
                fields.get("title", ""),
                fields.get("group_id", ""),
                fields.get("priority", PRIORITY_MID),
                fields.get("note", ""),
                fields.get("due_date"),
            )
            task_id = task.id
        self.app.save_data()
        # 改了截止日期就清掉提醒记录，否则新时间不会再触发提醒
        if self.app.reminder is not None and task_id:
            self.app.reminder.forget(task_id)
        self.render()
        self._refresh_chrome()

    # ------------------------------------------------------------------
    # 截止日期
    # ------------------------------------------------------------------
    def _pick_due(self, task_id: str) -> None:
        task = self.store.task(task_id)
        if task is None:
            return
        due_picker.pick_due(
            self.app, task.due_date,
            lambda when, remind: self._apply_due(task_id, when, remind),
            remind=task.remind_offset,
        )

    def _apply_due(self, task_id: str, when, remind=None) -> None:
        task = self.store.task(task_id)
        if task is None:
            return
        # 日期与提醒一起写：两者在同一个弹窗里选，分两次落盘只会多一次
        # "改了一半"的中间态（而且 remind=None 的含义就是"不提醒"，不能当成没传）
        self.store.update_task(task_id, due_date=when, remind_offset=remind)
        self.app.save_data()
        # 时间被改过 -> 允许重新提醒（不然同一个任务改完时间就再也不会提醒了）
        if self.app.reminder is not None:
            self.app.reminder.forget(task_id)
        self.render()
        self._refresh_chrome()
        self.app.sync_tray()
        if task.due_date is None:
            self.app.toast("已清除截止日期")
        else:
            tail = ("" if task.remind_offset is None
                    else f" · {remind_label(task.remind_offset)}")
            self.app.toast(f"截止时间设为 {task.due_date.strftime('%m/%d %H:%M')}{tail}")

    def _delete(self, task_id: str) -> None:
        task = self.store.task(task_id)
        if task is None:
            return
        snapshot = task.to_dict()
        # 先拿到卡片引用：淡出动画需要它，而 render() 会重建卡片表
        card = self.card_map.get(task_id)

        def do_delete() -> None:
            def remove() -> None:
                self.store.remove_task(task_id)
                self.app.save_data()
                if self.app.reminder is not None:
                    self.app.reminder.forget(task_id)
                if self.app.page is self and self.winfo_exists():
                    self.card_map.pop(task_id, None)
                    group_card = self.group_cards.get(task.group_id)
                    if (self.has_active_filter or not self.store.all_tasks()
                            or group_card is None):
                        self.render()
                    elif group_card.remove_task_card(task_id):
                        group_card.update_count()
                        self.summary.update_data()
                        self.banner.refresh()
                    else:
                        self.render()
                self._refresh_chrome()
                self.app.sync_tray()

                def undo() -> None:
                    restored = self.store.restore_task(snapshot)
                    if restored is None:
                        self.app.toast("这条任务暂时无法恢复")
                        return
                    self.app.save_data()
                    if self.app.reminder is not None:
                        self.app.reminder.forget(restored.id)
                    current_page = self.app.page
                    if isinstance(current_page, TaskPage):
                        current_page.render()
                    self.app.refresh_chrome()
                    self.app.sync_tray()
                    self.app.toast(strings.TASK_FILTER_RESTORE)

                self.app.toast(
                    "已移除这一条，轻装继续", duration=6000,
                    action_label=strings.TASK_FILTER_UNDO,
                    action_command=undo,
                )

            # 先淡出再移除（需求 21）：直接消失会让人觉得"点错了"
            if card is not None:
                try:
                    widgets.fade_out(card, steps=10, interval=20, on_done=remove)
                    return
                except Exception:  # noqa: BLE001
                    pass
            remove()

        dialogs.confirm(self.app, "删除任务", f"确定要删除「{task.title}」吗？",
                        on_ok=do_delete, danger=True)

    def _rename_group(self, group_id: str) -> None:
        grp = self.store.group(group_id)
        if grp is None:
            return

        def on_ok(text: str) -> None:
            if not text:
                return
            grp.name = text
            self.app.save_data()
            self.render()

        dialogs.prompt(self.app, "重命名分组", "分组名称", grp.name, on_ok)

    def _icon_group(self, group_id: str) -> None:
        """更换分组图标（需求 ⑥：12 格暖色图标网格浮层）。

        ⚠️ 不再用右键菜单承载候选图标（上一版就是这么写的，三个问题）：
        菜单宽度锁 180px、行高 34px，12 项要么图标被挤成 18px 看不清、
        要么排到 500px 高要滚屏；而且图标本身是彩色的，和文字、悬停底色
        混在一行里完全糊在一起。所以改成一个独立浮层（见 ``ui/icon_picker``），
        4×3 网格、格子 44px、选中态柔橘实心 + 白色图标。

        写入统一走 :meth:`Store.set_group_icon`（带白名单校验），
        它同时维护 ``settings["group_icons"]``（权威来源）和 ``Group.icon``
        （旧字段兼容），所以换图标后**重启也不会丢**。
        """
        grp = self.store.group(group_id)
        if grp is None:
            return
        from . import icon_picker

        current = self.store.group_icon(group_id)
        # 不给 x/y：浮层会盖在**指针位置**弹出（和右键菜单一致的语义）。
        # 这不只是为了好看 —— 浮层自己有一个"指针离开就收"的看门狗，
        # 弹到离指针很远的地方会被立刻判成"用户已经去看别处了"而秒关。
        picker = icon_picker.popup_icon_picker(
            self.app,
            on_pick=lambda key: self._set_group_icon(group_id, key),
            current=current,
            title=f"更换「{grp.name}」的图标",
        )
        # 留给脚本/测试的把手：断言"浮层确实开了、而且高亮的是当前图标"
        self._icon_picker = picker

    def _set_group_icon(self, group_id: str, icon: str) -> None:
        # 写入口做类型校验：非 str 直接拒绝并记日志（见 store.set_group_icon 注释）
        if not self.store.set_group_icon(group_id, icon):
            return
        self.app.save_data()
        self.render()

    def _delete_group(self, group_id: str) -> None:
        grp = self.store.group(group_id)
        if grp is None:
            return
        if len(self.store.groups) <= 1:
            self.app.toast("至少保留一个分组哦")
            return
        others = [g for g in self.store.groups if g.id != group_id]
        count = len(self.store.tasks_in(group_id))

        def on_ok() -> None:
            self.store.remove_group(group_id, move_tasks_to=others[0].id)
            if self.store.settings.get("default_group") == group_id:
                self.store.set_setting("default_group", others[0].id)
            self.app.save_data()
            self.render()
            self._sync_chip()
            self.app.toast(f"已删除分组，{count} 项任务移到「{others[0].name}」")

        dialogs.confirm(self.app, "删除分组",
                        f"删除「{grp.name}」后，其中的 {count} 项任务会移动到「{others[0].name}」。",
                        on_ok=on_ok, ok_text="删除并移动", danger=True)

    # ------------------------------------------------------------------
    def _refresh_chrome(self) -> None:
        self.app.refresh_chrome()

    def update_summary(self) -> None:
        self.summary.update_data()

    def update_pomodoro(self, state: dict) -> None:
        """计时器每秒回调，只更新对应卡片的按钮，避免整页重建。"""
        running_id = state.get("task_id") if state.get("running") else None
        label = state.get("label", "")
        for task_id, card in self.card_map.items():
            card.set_timer_state(
                running=(task_id == running_id),
                label=label if task_id == running_id else "",
                pomodoros=card.task.pomodoros,
            )
