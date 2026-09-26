# -*- coding: utf-8 -*-
"""任务卡片：勾选、编辑、番茄计时、右键菜单、拖拽抓取。

拖拽实现说明（重要）
--------------------
不在卡片上绑定 ``<B1-Motion>``：拖拽过程中列表会被重建，卡片控件会被销毁，
绑定随之丢失，拖拽就会中途断掉。
因此卡片只负责在按下鼠标时上报 (task_id, 事件)，真正的移动/释放监听
由 ``TaskDragManager`` 挂在**顶层窗口**上（见 task_page.py），这样重建列表
不会打断拖拽过程。
"""

from __future__ import annotations

import os
import tkinter as tk
from typing import Any, Callable, Optional, Tuple

import customtkinter as ctk

from .. import strings, theme
from ..models import Task
from . import menu, widgets


def _apply_color_key(tip: tk.Toplevel, key: str) -> bool:
    """把 ``key`` 设成 ``tip`` 的**透明色键**（Windows 专有）。

    置位后，窗口上所有像素颜色恰好等于 ``key`` 的地方会被系统整片抠掉
    （同时穿透点击）—— 悬停提示四周那圈为了投影留下的方形留边就此消失。

    ``-transparentcolor`` 是 Windows 才有的 wm 属性，其它平台会抛 TclError；
    这里吞掉异常返回 False，调用方行为退回"底色 = 页面底色"（旧的观感），
    不至于因为一个纯装饰属性把提示整个搞没。
    """
    try:
        tip.attributes("-transparentcolor", key)
        return True
    except Exception:  # noqa: BLE001
        return False


class TaskCard(ctk.CTkFrame):
    """单条任务卡片。

    布局（本轮改为**单行**紧凑排布，从左到右）
    ------------------------------------------
        逾期色条2 / 勾选框16 / 任务标题13（弹性）/ 逾期徽章 + 截止日期11 /
        弹性 / 番茄钟14 / 日历14 / 更多选项⋮14（后三个 hover 淡入）

    行内右侧这一块（``meta_row``）是**整块右对齐**的，内部固定
    「逾期徽章 → 时间文本」的顺序，所以所有任务的时间文本共享同一条右边界。

    上一版是"标题一行 + 截止日期一行"的两行结构，加上 CTkLabel 默认 28px 的
    请求高度，单张卡片实测 98.7 逻辑像素高 —— 一屏只能放 3 条任务，正是用户
    说的"手机端 App 风格"。搬到单行 + 用 ``widgets.TextLabel`` 归零默认留白后
    实测 30 逻辑像素。

    几何常量全部走 ``theme`` / ``fonts``，UI 代码里不允许出现裸数字字号。

    右侧图标默认隐藏，鼠标进入卡片才淡入（150ms）—— 这样静止状态的卡片只剩
    "勾选框 + 标题 + 截止日期"，视线不会被按钮森林淹没。
    """

    # ---- 几何常量（全部来自 theme，UI 代码里不允许出现裸数字）----
    # 本轮把行高从 52 压到 30：一条任务只需要"一行标题"的高度，
    # 上一版 52px 是"两行 + 12px 上下内边距"的手机端手感。
    MIN_HEIGHT = theme.TASK_MIN_HEIGHT
    PAD_Y = theme.TASK_PAD_Y
    PAD_X = theme.CARD_PAD_X
    STRIPE_WIDTH = theme.TASK_STRIPE_W
    ICON_SIZE = theme.TASK_ICON      # 右侧动作图标
    ICON_BTN = theme.TASK_ICON_BTN   # 图标按钮的点击热区（比图标大，好点）
    CHECK_SIZE = theme.TASK_CHECK    # 太阳勾选框直径（逻辑像素）
    FADE_STEPS = 6            # 需求 26：150ms 淡入（6 × 25ms）
    FADE_INTERVAL = 25
    TITLE_MIN_WRAP = 72       # 窗口缩到最小时标题的折行下限（逻辑像素）
    # 右侧动作区的**固定**宽度（1.5.15 起在实例上实测，见 _measure_actions）。
    # 为什么不能用静态常量：CTkButton 带 image 时有内部宽度下限（实测约
    # 36.7 逻辑像素/个，远大于 ICON_BTN=20 的"请求值"），旧常量
    # ICON_BTN*3+6=66 严重低估真实宽 114 —— 标题截断按 66 预留，悬停时
    # 图标一浮出来就压住标题/日期（1.5.13 没修干净的真因）。实例上用
    # winfo_reqwidth() 实测三个按钮的真实宽，预留永远等于实况。
    ACTIONS_PAD_X = 4         # 动作容器与元信息块（徽章+时间）之间的间隙

    def __init__(
        self,
        master: tk.Misc,
        task: Task,
        index: int,
        dispatch: Callable[[str, Any], None],
        on_press: Callable[[str, tk.Event], None],
        pomodoro_state: Optional[dict] = None,
        dragging: bool = False,
    ) -> None:
        # 卡片的**当前**底色：未完成 card / 已完成 card_done。复选框是裸 Canvas，
        # 底色必须给它一个具体单色（Canvas 不认 CTk 的 (浅,深) 元组）——
        # 上一版这里写死成 card 的白色，于是一张 card_done 底色的卡片上
        # 露出了一块 24×24 的纯白方块。
        card_bg = theme.c("card_done") if task.done else theme.c("card")
        super().__init__(
            master,
            corner_radius=theme.RADIUS_CARD,
            fg_color=(theme.pair("card_done") if task.done else theme.pair("card")),
            border_width=0,       # 需求 2：不用硬边框，靠柔和投影拉开层次
        )
        self.task = task
        self.index = index
        self.dispatch = dispatch
        self._on_press = on_press
        self._dragging = dragging
        self._hover = False
        self._hover_job: Optional[str] = None
        self._settle_job: Optional[str] = None   # 落位脉冲（见 flash_settle）
        self._fade_jobs: list[str] = []
        self._pomodoro = pomodoro_state or {}
        self._icon_photos: list = []      # 必须持引用，否则 PhotoImage 被 GC

        if dragging:
            self._apply_drag_style()

        self.grid_columnconfigure(2, weight=1)
        self.configure(height=self.MIN_HEIGHT)

        # ---- 逾期色条（需求 27：左侧 2px 柔圆角，完成后消失）----
        # ⚠ height 必须显式给小值：关了 propagate 之后，这个 frame 的**请求高度**
        #   就是它自己的 height，而 CTkFrame 的默认 height 是 200 —— 会把行撑成
        #   200px，单张任务卡片直接长到 300px 高（截图里一眼看出来的 bug）。
        #   sticky="ns" 会让它按行高拉伸，所以设 1px 不会影响最终显示。
        self.stripe = ctk.CTkFrame(self, width=self.STRIPE_WIDTH, height=1,
                                   corner_radius=1, fg_color="transparent")
        self.stripe.grid(row=0, column=0, sticky="ns",
                         padx=(self.PAD_X - 4, 0), pady=self.PAD_Y)
        self.stripe.grid_propagate(False)

        # ---- 任务状态图标（需求 27 保留；本轮 16px，与单行标题同轴）----
        # ⚠️ SunCheck 是**裸 Canvas**，它的 size 直接当物理像素用（CTk 的缩放
        # 管不到它），必须过 lpx()。这里传的是逻辑尺寸，由 SunCheck 内部换算。
        #
        # 垂直对齐：图标画布与标题标签同处 grid 第 0 行、且都不带纵向 sticky，
        # 于是两者都按行高居中 —— 实测两条中线偏差 0.0px（标题框高 lpx(20)=30
        # 来自字号 linespace，canvas 高 lpx(16)=24，共用同一个行中心）。
        # 卡片的底色变化（切完成态 / 悬停 / 拖拽）必须通过 check.set_bg() 同步，
        # 否则画布会留着上一档底色，在卡片上露出一块色差方块。
        self.check = widgets.SunCheck(
            self, size=theme.lpx(self.CHECK_SIZE), checked=task.done, bg=card_bg,
            command=lambda value: self.dispatch("toggle", (self.task.id, value)),
        )
        self.check.grid(row=0, column=1, padx=(3, 5), sticky="w")

        # ---- 标题（单行居中，长标题最多折两行）----
        # 1.5.24：已完成标题改用**深绿**（task_done_text），与淡绿卡底同族；
        # 此前用的 text_done(#BDB2A4) 是通用"弱化提示色"，压在绿色卡底上发灰。
        title_color = theme.pair("task_done_text") if task.done else theme.pair("text")
        self.title_label = widgets.TextLabel(
            self,
            text=task.title or strings.UNNAMED_TITLE,
            font=theme.font("task_title_done" if task.done else "task_title"),
            text_color=title_color,
            anchor="w",
            justify="left",
            # 注意：这里必须是**逻辑**像素。CTkLabel 会自己做 widget scaling，
            # 之前误传了 theme.lpx(176)（物理值）＝ 又乘了 1.5 倍，折行宽度被
            # 放到 264px，于是"永远不会折行"这个 bug 一直藏着没被发现。
            # 真正生效的宽度由 _sync_wraplength() 按卡片实测宽度算。
            wraplength=176,
        )
        self.title_label.grid(row=0, column=2, sticky="ew",
                             pady=theme.lpx(theme.TASK_PAD_Y))
        # 1.5.13：标题可能被省略号截断 —— 完整标题靠这条悬停提示兜底。
        # 1.5.28：**只有真被截断才弹**。"吃饭"这种本来就看全了的短标题，
        # 再糊一个白框纯属干扰（用户报的就是这个）。判据见 _title_hint_needed()，
        # 由 _fit_title() 每轮重排刷新，所以缩放/改字号后不会拿旧结论。
        self._title_truncated = False   # 当前标题是否被省略号截断
        self._title_avail = 0           # 标题当前可用宽（逻辑像素）
        self._touch_hint(self.title_label, task.title or strings.UNNAMED_TITLE,
                         gate=self._title_hint_needed)

        # ---- 右侧固定容器（1.5.15 重构）----
        # ``right_box`` 是一整块**右对齐**的固定区域，内部从左到右：
        #     [meta_row：逾期徽章 + 时间] [actions：日历 / 番茄 / ⋮]
        # 为什么要合成一个容器：旧布局 meta_row 和 actions 是两个独立 grid 列，
        # actions 用 grid()/grid_remove() 切换显隐 —— 悬停瞬间 meta_row 失去
        # 右邻居，整块**向左滑 100 多像素**贴到标题上（最小窗口下短标题
        # "吃饭"和"逾期 09/25 10:10"几乎叠在一起，就是用户截的那个画面）。
        # 现在 actions 容器**恒定占位**（pack_propagate(False) + 固定宽），
        # 悬停只切换里面按钮的显隐，meta_row 的位置永远不动 → hover 零回流。
        self.right_box = ctk.CTkFrame(self, fg_color="transparent")
        self.right_box.grid(row=0, column=3, columnspan=2, sticky="e",
                            padx=(theme.TASK_META_PAD_LEFT,
                                  theme.TASK_META_PAD_RIGHT))

        # ---- 截止日期 + 逾期徽章（需求 6；本轮从第二行搬到标题右侧同一行）----
        # 原来 due 单独占一行，"标题 17px + 元信息 15px + 上下内边距"轻松到 52px，
        # 正是用户说的"手机端行高"。搬到行内后整条任务就是**一行**，实测 30px。
        #
        # 排列顺序（1.5.8 修正）：**逾期徽章在时间前面** ——「逾期 09/25 07:21」。
        # 整块 `meta_row` 右邻就是动作图标，所以只有"徽章在左"这一种写法
        # 能让**所有**任务的时间文本落在同一条右边界上；反过来写（时间 + 徽章）
        # 时，徽章会把时间顶离右边缘，有逾期的行和没逾期的行相差一个徽章宽，
        # 多行扫下来就是"参差不齐"。
        self.meta_row = ctk.CTkFrame(self.right_box, fg_color="transparent")
        # ❗1.5.9：这两个不再是 CTkLabel，而是 widgets.AAText（PIL 超采样渲染）。
        # 原因见 widgets._aa_text_image 的模块注释：Tk/GDI 的小字号文字走
        # **亚像素**渲染（截图里笔画两侧带橙/青色描边），CTk 的圆角底衬又是
        # 多边形拼的硬阶梯 —— 用户报的就是"逾期标签和日期文字模糊、有锯齿"。
        # 两个标签共用同一个**画布高**（TASK_META_H）：高度不一致时被 pack
        # 居中之后基线会差半个像素，看着像"没对齐"。
        self.due_label = widgets.AAText(self.meta_row, bg=card_bg)
        self.overdue_tag = widgets.AAText(self.meta_row, bg=card_bg)
        # 时间文本常驻；徽章由 refresh_due() 按需 pack —— 且必须插在时间**前面**
        # （pack 是追加式，默认会落到最右边，那就是改之前的效果）。
        self.due_label.pack(side="left")

        # ---- 右侧操作区（需求 26：默认隐藏，hover 淡入）----
        # 容器**永远占位**、宽度固定（实测按钮宽之和，见 _measure_actions），
        # pack_propagate(False) 冻结尺寸；悬停只决定里面的按钮 pack 还是 forget。
        self.actions = ctk.CTkFrame(self.right_box, fg_color="transparent",
                                    height=self.ICON_BTN)
        self.actions.pack(side="left", padx=(self.ACTIONS_PAD_X, 0))
        self.actions.pack_propagate(False)

        # 日历（点击改截止日期）
        self.due_btn = widgets.IconButton(
            self.actions, text="", icon="calendar", size=self.ICON_BTN,
            icon_size=self.ICON_SIZE,
            command=lambda: self.dispatch("due", self.task.id),
        )
        self._touch_hint(self.due_btn, "设置截止日期")

        # 番茄计时 / 累计番茄（点击启动计时）
        self.timer_text = ""
        running = bool(self._pomodoro.get("task_id") == task.id
                       and self._pomodoro.get("running"))
        if running:
            self.timer_text = str(self._pomodoro.get("label", ""))
        self._timer_running = running

        self.timer_btn = widgets.IconButton(
            self.actions, text="", icon="pomodoro", size=self.ICON_BTN,
            icon_size=self.ICON_SIZE,
            command=lambda: self.dispatch("pomodoro", self.task.id),
        )
        self.set_timer_state(running, self.timer_text, task.pomodoros)

        # ---- 优先级圆点（已删除）----
        # 这里原来挂了一个 8px 的暖橙实心圆点（高/中/低 = 朱红/暖橙/淡金）。
        # 它紧挨着番茄钟图标、又是半透明光晕 + 小圆点的组合，在卡片上看着
        # 就是"一粒多余的橙色像素点"（很像没画完的占位符）。优先级本身在
        # 编辑对话框里已有完整呈现，卡片右侧不必再点一次，故整体移除。
        # 详见 icons.py 里 `_draw_dot` 删除处的说明。

        # 更多选项 ⋮
        self.more_btn = widgets.IconButton(
            self.actions, text="⋯", icon="more", size=self.ICON_BTN,
            icon_size=self.ICON_SIZE,
            command=self._show_menu,
        )

        # 紧凑模式状态：最小窗口下只留"⋯"（见 _sync_wraplength 的切换逻辑）。
        # 必须在 _measure_actions 之前就位（_measure_actions 里会读 _compact）。
        self._compact = False
        self._actions_shown = False
        # 实测三个按钮的真实请求宽 → 动作容器的固定宽度（完整 / 紧凑两档）
        self._measure_actions()
        # meta_row 必须排在 actions **左边**（pack 是追加式，后 pack 的落到右边；
        # 这里 actions 已占位，用 before= 把 meta 插到它前面）。之后 meta 的
        # 显隐由 refresh_due() 维护。
        self.meta_row.pack(side="left", before=self.actions)

        # ---- 交互绑定 ----
        # 按下鼠标 = 拖拽候选（复选框与按钮区域不参与拖拽）
        for widget in (self, self.title_label, self.meta_row, self.due_label):
            widget.bind("<Button-1>", self._handle_press, add="+")
            widget.bind("<Button-3>", lambda _e: self._show_menu(), add="+")
        self.bind("<Button-3>", lambda _e: self._show_menu(), add="+")

        widgets.bind_hover(self, self._enter, self._leave)
        # 子控件的按下事件不能冒泡成拖拽
        for widget in (self.check, self.due_btn, self.timer_btn, self.more_btn):
            widget.bind("<Button-1>", lambda _e: None, add="+")

        self.set_actions_visible(False, animate=False)
        self.refresh_due()
        # 窗口自由缩放后，标题的折行位置必须跟着卡片宽度重算（否则把窗口
        # 拉宽了标题还在老地方折两行）。1.5.16 走 80ms 防抖；1.5.17 升级为
        # "拖拽中冻结"——ResizeGate 判定拖拽中完全不排队，松手 100ms 后
        # 一次性重算（防抖兜底保留给零散的单次宽度变化）。
        widgets.RESIZE_GATE.register(self, self._sync_wraplength)
        self.bind("<Configure>", self._on_resize, add="+")
        self._resize_job: Optional[str] = None
        self._last_width = 0
        self._reveal_layout_pending = False

    def begin_reveal_layout(self) -> None:
        """Hold per-card resize work while a collapsed long list is revealed."""
        self._reveal_layout_pending = True
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:  # noqa: BLE001
                pass
            self._resize_job = None

    def finish_reveal_layout(self) -> None:
        """Fit the title once the group has its final mapped width."""
        self._reveal_layout_pending = False
        self._resize_job = None
        self._sync_wraplength()

    def cancel_reveal_layout(self) -> None:
        self._reveal_layout_pending = False

    def _on_resize(self, event=None) -> None:
        """<Configure> 处理：只在**宽度**真的变了时才重算标题适配。

        ⚠️ 事件源（1.5.13 修活的死代码）
        --------------------------------
        CTk 6.0.0 把 ``CTkFrame.bind`` **重载到内部 ``_canvas``** 上（见
        customtkinter 源码 ``CTkFrame.bind``），所以这里收到的 ``<Configure>``
        实际是 canvas 报上来的 —— canvas 与卡片同宽同高，宽度可信。
        上一版用 ``event.widget is not self`` 一刀切过滤，把 canvas 事件全拒了，
        折行/截断同步从未真正跑过。

        ⚠️ 为什么必须卡宽度（1.5.4 修的抖动）
        ------------------------------------
        Tk 的 ``<Configure>`` 不只在改尺寸时发，widget 被**移动**（x/y 变化）
        时同样会发。展开一个分组会把下方所有卡片整体下推 —— 每张卡都收到一次
        ``<Configure>``。如果照单全收，一批 ``after`` 回调会在同一时刻集中执行，
        重算结果又反过来改卡片高度，于是页面"抖"一下。
        位置变了但宽度没变时，结果不可能变，直接返回。
        """
        if event is not None:
            w = getattr(event, "widget", None)
            if w is not self and w is not getattr(self, "_canvas", None):
                return      # 其他子控件的 Configure 冒泡上来，忽略
            if event.width == self._last_width:
                return  # 只是被挪了位置，宽度没变
            self._last_width = event.width
            if self._reveal_layout_pending:
                # 分组展开时会同时映射很多卡片。先记住最终宽度，避免每张卡
                # 各自排队改标题；GroupCard 会在布局稳定后统一适配一遍。
                return
        # ⚠️ 1.5.19：宽度变了就**当帧**重算（after_idle 合并同帧多次事件）。
        # 标题截断/紧凑切换是**布局适配**（几次字宽测量 + configure，微秒级），
        # 不是贵重绘 —— 1.5.17/18 把它也冻结进 ResizeGate 后，拖拽中途标题
        # 一直保持宽窗口时的截断文本，直接顶到右侧徽章上（中途抓帧铁证）。
        # 冻结只留给真贵的横幅 4× 位图重建（见 task_page.DueBanner）。
        if self._resize_job is not None:
            return                      # 本帧已排过一次，合并
        self._resize_job = self.after_idle(self._sync_wraplength)

    def _sync_wraplength(self) -> None:
        """按卡片实测宽度重算标题可用宽（1.5.13：单行 + 省略号截断）。

        ⚠️ 1.5.13 起标题**不再折行**，放不下就截断加省略号（完整标题
        悬停可见）。原因：折行会把卡片撑到两行高，窗口拖窄时一行变两行、
        列表整体跳动；单行截断让卡片高度恒定，缩放零抖动。

        ⚠️ ``winfo_width()`` 是**物理像素**，标题测量/截断都在**逻辑像素**
        域做，所以先除回 ``scale()``。
        """
        self._resize_job = None
        if self._reveal_layout_pending:
            return
        try:
            card_px = self.winfo_width()
        except Exception:  # noqa: BLE001
            return
        if card_px <= 1:
            return
        scale = theme.scale() or 1.0
        card_logic = card_px / scale

        # 实测动作容器宽度（构造时图标未就位会量小，映射后重量才准）
        self._measure_actions()

        # ---- 紧凑模式（1.5.15）：卡片窄于阈值时只留"⋯"----
        # 最小窗口（320）下卡片 ≈295：三个图标 + 徽章 + 时间 + 标题根本排不下，
        # 与其让标题被挤成两三个字，不如收起"日历/番茄"（右键菜单里都有）。
        # 两个阈值间留迟滞带，避免临界宽度来回横跳。宽度变化才进到这里，
        # 切换是低频事件，允许这一次回流。
        if card_logic < theme.TASK_COMPACT_CARD_W:
            want = True
        elif card_logic > theme.TASK_EXPAND_CARD_W:
            want = False
        else:
            want = self._compact
        if want != self._compact:
            self._compact = want
            self._act_w = self._act_more_w if want else self._act_full_w
            try:
                self.actions.configure(width=self._act_w)
            except Exception:  # noqa: BLE001
                pass
            self._set_buttons_packed(self._actions_shown)

        # 预留：左内边距 + 色条 + 复选框 + 间距 + 右侧固定容器。
        # 动作区宽度是**实测值**（_measure_actions，紧凑/完整两档）；
        # meta_row（徽章+时间）映射着时实测宽可信，1.5.14 起它自带
        # 左右内边距（TASK_META_PAD_LEFT/RIGHT 由 right_box 统一提供）。
        reserved = (self.PAD_X + self.STRIPE_WIDTH + self.CHECK_SIZE + 8
                    + self._act_w + theme.TASK_META_PAD_RIGHT)
        try:
            if self.meta_row.winfo_manager():
                reserved += (self.meta_row.winfo_width() / scale
                             + theme.TASK_META_PAD_LEFT)
        except Exception:  # noqa: BLE001
            pass
        avail = max(self.TITLE_MIN_WRAP, int(card_logic - reserved))
        self._fit_title(avail)

    def _fit_title(self, avail: int) -> None:
        """把标题截到 ``avail`` 逻辑像素内，放不下加省略号。

        1.5.28：顺手把"这一轮到底截没截"记在 ``_title_truncated`` / ``_title_avail``
        上 —— 悬停提示的门控（``_title_hint_needed``）就靠它，而本方法每轮重排
        都会重跑（字体、缩放、窗口宽度任意一项变了都会走到这里），所以判据
        **天然随重排失效重算**，不会留旧结论。
        """
        role = "task_title_done" if self.task.done else "task_title"
        full = self.task.title or strings.UNNAMED_TITLE
        self._title_avail = avail
        try:
            from .. import fonts as _fonts
            text_w = _fonts.measure(role, full)
            ell_w = _fonts.measure(role, strings.TITLE_ELLIPSIS)
        except Exception:  # noqa: BLE001
            # 量不出宽（字体层不可用）就退回折行行为，至少不崩
            try:
                self.title_label.configure(
                    text=full, wraplength=max(self.TITLE_MIN_WRAP, avail))
            except Exception:  # noqa: BLE001
                pass
            self._title_truncated = False   # 判不定就不弹，宁少勿多
            return
        if text_w <= avail:
            shown, wrap = full, text_w + 24   # 折行宽度抬到文字宽之上，永不折
        else:
            # 从后往前找能和省略号一起放得下的最长前缀（标题不会太长，
            # 线性回退足够快；宽字符一步约省 13px，最多十几步）
            keep = len(full)
            while keep > 0:
                cand = full[:keep].rstrip() + strings.TITLE_ELLIPSIS
                if _fonts.measure(role, cand) <= avail:
                    break
                keep -= 1
            shown = (full[:keep].rstrip() + strings.TITLE_ELLIPSIS) if keep else \
                strings.TITLE_ELLIPSIS
            wrap = _fonts.measure(role, shown) + 24
        try:
            self.title_label.configure(text=shown, wraplength=max(self.TITLE_MIN_WRAP, wrap))
        except Exception:  # noqa: BLE001
            pass
        # 1.5.28：过一遍截断状态（供悬停提示门控）
        self._title_truncated = shown != full

    # ------------------------------------------------------------------
    # 右侧图标的显隐（需求 26；1.5.15 改为按钮级显隐）
    # ------------------------------------------------------------------
    def _measure_actions(self) -> None:
        """实测动作按钮的真实请求宽，得出容器两档固定宽度。

        CTkButton 带 image 的实际宽（≈36.7）大于给它的 ``width=20`` ——
        任何"按请求值算"的静态常量都会低估，只有 ``winfo_reqwidth()``
        （物理像素，除回 scale）是可信的。
        ⚠️ 构造时机上图标还没就位，reqwidth 会量小；所以每次
        ``_sync_wraplength`` 都重新实测一遍（此时卡片已映射、图标已画），
        宽度真的变了才 configure，避免无谓回流。
        """
        scale = theme.scale() or 1.0
        try:
            full = (self.due_btn.winfo_reqwidth() + self.timer_btn.winfo_reqwidth()
                    + self.more_btn.winfo_reqwidth()) / scale
            more = self.more_btn.winfo_reqwidth() / scale
        except Exception:  # noqa: BLE001
            full, more = self.ICON_BTN * 3, self.ICON_BTN
        # + ACTIONS_PAD_X：容器 pack 时的左间隙（meta 与图标之间）
        self._act_full_w = max(1, round(full) + self.ACTIONS_PAD_X)
        self._act_more_w = max(1, round(more) + self.ACTIONS_PAD_X)
        new_w = self._act_more_w if self._compact else self._act_full_w
        if new_w != getattr(self, "_act_w", None):
            self._act_w = new_w
            try:
                self.actions.configure(width=self._act_w)
            except Exception:  # noqa: BLE001
                pass

    def _visible_buttons(self) -> tuple:
        """当前模式下应该占位的按钮（紧凑模式只留"⋯"；计时中的番茄除外）。"""
        btns = []
        if not self._compact:
            btns.append(self.due_btn)
            btns.append(self.timer_btn)
        elif self._timer_running:
            # 紧凑模式下如果这张卡正在计时，mm:ss 必须可见（用户在等它走）
            btns.append(self.timer_btn)
        btns.append(self.more_btn)
        return tuple(btns)

    def _set_buttons_packed(self, shown: bool) -> None:
        """把"该显示的按钮"pack 进固定宽容器 / 全部 forget。

        先全部 forget 再按顺序 pack：pack 是追加式，直接补 pack 会把
        "日历/番茄"排到"⋯"右边，顺序就乱了。
        """
        targets = self._visible_buttons() if shown else ()
        try:
            for btn in (self.due_btn, self.timer_btn, self.more_btn):
                btn.pack_forget()
            for btn in targets:
                btn.pack(side="left", padx=1)
        except Exception:  # noqa: BLE001
            pass

    def set_actions_visible(self, visible: bool, animate: bool = True) -> None:
        """淡入/淡出右侧操作图标。

        为什么用 150ms 淡入而不是直接切换：直接切换会让图标的出现在一帧内
        跳变。150ms 的中间过程人眼察觉不到"它原来不在"，只觉得"浮出来了"。
        1.5.15 起容器恒定占位，这里切换的只是容器**内按钮**的 pack 状态
        （容器宽度不变 → meta_row 不动 → 零回流）。
        """
        self._actions_shown = bool(visible)
        for job in self._fade_jobs:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._fade_jobs.clear()

        if not animate:
            self._set_actions_alpha(1.0 if visible else 0.0)
            # 立刻隐藏：不 forget 的话透明按钮会继续吃掉鼠标事件（拖拽会
            # 莫名失效）；容器本身保留占位，meta_row 位置不动。
            self._set_buttons_packed(visible)
            return

        steps = self.FADE_STEPS
        start = 0.0 if visible else 1.0
        end = 1.0 if visible else 0.0

        def step(i: int) -> None:
            if not self.winfo_exists():
                return
            t = i / steps
            self._set_actions_alpha(start + (end - start) * t)
            if i < steps:
                self._fade_jobs.append(self.after(self.FADE_INTERVAL, step, i + 1))
            elif not visible:
                # 淡出结束后真正隐藏按钮，避免透明按钮继续吃掉鼠标事件
                self._set_buttons_packed(False)

        if visible:
            self._set_buttons_packed(True)
        step(0)

    def _set_actions_alpha(self, t: float) -> None:
        """用图标自身的透明度模拟淡入淡出（tkinter 控件没有 alpha 通道）。

        注意：徽标类控件不能调 ``fg_color`` 来"假装"透明 —— 那样只会
        把底色刷成卡片色，图标本身照样是全不透明的。正确做法是让
        ``IconButton`` 重新取一张按 ``t`` 预乘过 alpha 的图。
        """
        t = max(0.0, min(1.0, t))
        for child in self.actions.winfo_children():
            setter = getattr(child, "set_fade", None)
            if setter is None:
                continue
            try:
                setter(t)
            except Exception:  # noqa: BLE001
                continue

    def _sync_timer(self) -> None:
        """同步番茄按钮：计时中显示 mm:ss 文本，否则显示番茄图标。

        需求 14 要求全部图标自绘、不留 emoji。番茄累计数原本用
        ``🍅3`` 拼字符串，现在拆成「图标 + 数字」两个槽位：
        图标走自绘 ``pomodoro``，数字走 ``tiny`` 字号的裸文本，
        再由 ``set_timer_state`` 决定当前该显示哪一种。
        """
        self.set_timer_state(self._timer_running, self.timer_text,
                             self.task.pomodoros)

    def set_timer_state(self, running: bool, label: str = "",
                        pomodoros: int = 0) -> None:
        """统一入口：外部（task_page 的每秒回调）只调这一个方法。

        :param running: 是否正在为本任务计时
        :param label:   计时中的 ``mm:ss`` 文本
        :param pomodoros: 该任务累计完成的番茄数（>0 时在图标右侧显示）
        """
        self._timer_running = bool(running)
        if running:
            # 计时中：只显示 mm:ss（数字用等宽 tabular 字体，跳动不抖）
            self.timer_text = str(label or "")
            self.timer_btn.set_icon(None, fallback_text=self.timer_text,
                                    text_color=theme.pair("orange"))
            # mm:ss 是 5 个等宽字符（tiny_bold 10px）≈ 30 逻辑像素，
            # 按钮要留得下，否则计时数字会被自己的按钮裁掉。
            self.timer_btn.configure(
                font=theme.font("tiny_bold"),
                width=self.ICON_BTN + 16,
            )
            self._touch_hint(self.timer_btn, "正在专注")
            return

        # 非计时：番茄图标 + 累计数
        self.timer_text = ""
        self.timer_btn.set_icon("pomodoro",
                                text_color=theme.pair("text_muted"))
        self.timer_btn.configure(width=self.ICON_BTN)
        if pomodoros:
            self._touch_hint(self.timer_btn, f"已完成 {pomodoros} 个番茄")
        else:
            self._touch_hint(self.timer_btn, "开始专注")

    # ------------------------------------------------------------------
    def _title_hint_needed(self) -> bool:
        """标题是否**真的被截断**了 —— 只有这种时候才值得弹悬停提示。

        双口径，互为兜底（1.5.28）：

        1. ``_title_truncated``：``_fit_title()`` 每轮重排都写一遍，字体、缩放、
           窗口宽度任一变化都会重排 → 旧结论自动失效，不存在"量过一次就记死"。
        2. 复核口径：拿完整标题的**实测渲染宽**（``fonts.measure``，与控件在用的
           那个 CTkFont 同源）和标题**当前可用宽**（已扣掉色条、复选框、右侧动作
           容器与逾期/时间块的占用）比一次 —— 覆盖"字段还没来得及刷新"的中间态
           （刚改完名字、刚构造）。

        反过来说：**只有** ``measure(完整标题) > 可用宽`` 才返回 True。标题完整
        可见时（不管它是两个字还是十个字）一律返回 False，不建窗口、不显示。
        """
        full = self.task.title or strings.UNNAMED_TITLE
        if self._title_truncated:
            return True
        avail = self._title_avail
        if not avail:
            return False                    # 还没测过宽度：保守不弹
        try:
            from .. import fonts as _fonts
            role = "task_title_done" if self.task.done else "task_title"
            return _fonts.measure(role, full) > avail
        except Exception:  # noqa: BLE001
            return False                    # 量不出就当作没截断，宁少勿多

    # ------------------------------------------------------------------
    def _touch_hint(self, widget: tk.Misc, text: str,
                    gate: Optional[Callable[[], bool]] = None) -> None:
        """极简 tooltip：鼠标停留时才显示，避免界面被说明文字塞满。

        ``gate``（1.5.28）：**弹出前的门控** —— 返回 False 时连 ``Toplevel``
        都不建。任务标题用它实现"只有被省略号截断才提示"；不传 gate 的调用
        （番茄按钮那种确实需要说明的）保持原来的无条件行为。

        ``SHIGUANG_NO_HINT=1`` 时整体禁用（截图工具用）：抓屏时指针位置是
        用户上次留下的，正好压在某张卡片上，那枚 tooltip 就会糊在预览图上。
        """
        if os.environ.get("SHIGUANG_NO_HINT") == "1":
            return
        tip: Optional[tk.Toplevel] = None
        tip_photo = None                    # 位图引用（不持有会被 GC 掉，白框）

        def show() -> None:
            nonlocal tip, tip_photo
            if tip is not None:             # 已经在显示，别叠第二层
                return
            if gate is not None and not gate():
                return                      # 标题完整可见 —— 不弹
            try:
                host = widget.winfo_toplevel()
                host.update_idletasks()
                edge = theme.lpx(6)
                host_x = host.winfo_rootx()
                host_y = host.winfo_rooty()
                host_w = max(1, host.winfo_width())
                host_h = max(1, host.winfo_height())
                max_tip_w = max(1, host_w - edge * 2)

                tip = tk.Toplevel(self)
                tip.wm_overrideredirect(True)
                tip.withdraw()
                tip.attributes("-topmost", True)
                # 整块米白圆角卡（描边 + 文字一张 PIL 位图）；外沿透明，
                # 不绘制投影框。字体层不可用时降级回无硬边的单色标签。
                key = widgets.tooltip_key()
                tip.configure(bg=key)
                _apply_color_key(tip, key)
                rendered = widgets.tooltip_image(text, max_width=max_tip_w)
                if rendered is None:
                    tk.Label(tip, text=text, bg=theme.c("tooltip_bg"),
                             fg=theme.c("tooltip_text"),
                             font=theme.tkfont_spec("tiny"),
                             wraplength=max(1, max_tip_w - theme.lpx(16)),
                             padx=theme.lpx(8), pady=theme.lpx(3),
                             bd=0).pack()
                else:
                    from PIL import ImageTk

                    img, (bw, bh) = rendered
                    canvas = tk.Canvas(tip, width=bw, height=bh,
                                       bg=key,
                                       highlightthickness=0, bd=0)
                    canvas.pack()
                    tip_photo = ImageTk.PhotoImage(img)
                    canvas.create_image(0, 0, anchor="nw", image=tip_photo)
                tip.update_idletasks()
                tip_w = max(1, tip.winfo_reqwidth())
                tip_h = max(1, tip.winfo_reqheight())
                left = host_x + edge
                right = host_x + host_w - edge
                top = host_y + edge
                bottom = host_y + host_h - edge

                x = widget.winfo_rootx()
                x = min(x, right - tip_w)
                x = max(left, x)
                y = widget.winfo_rooty() + widget.winfo_height() + edge
                if y + tip_h > bottom:
                    y = widget.winfo_rooty() - tip_h - edge
                y = min(y, bottom - tip_h)
                y = max(top, y)
                tip.geometry(f"+{x}+{y}")
                tip.deiconify()
            except Exception:  # noqa: BLE001
                tip = None
                tip_photo = None

        def hide() -> None:
            nonlocal tip, tip_photo
            if tip is not None:
                try:
                    tip.destroy()
                except Exception:  # noqa: BLE001
                    pass
                tip = None
                tip_photo = None

        widget.bind("<Enter>", lambda _e: show(), add="+")
        widget.bind("<Leave>", lambda _e: hide(), add="+")

    # ------------------------------------------------------------------
    # 截止日期呈现
    # ------------------------------------------------------------------
    def refresh_due(self) -> None:
        """按"无 / 未到期 / 今日到期 / 已逾期 / 已完成"刷新截止日期视觉。

        颜色规则（需求 6）：
            ＊ 未设       -> 整行隐藏，卡片不占额外高度
            ＊ 已设未到期 -> 深灰棕次色
            ＊ 今日到期   -> 柔橘 + 加粗
            ＊ 已逾期     -> 朱红 + "逾期"标签
            ＊ 已完成     -> 灰色 + 删除线（与文字同步），且不再显示逾期竖线
        """
        task = self.task

        if task.due_date is None:
            self.meta_row.pack_forget()
            self.overdue_tag.pack_forget()
            self._update_stripe()
            self.after_idle(self._sync_wraplength)
            return

        text = task.due_label
        if task.done:
            role, color = "meta_strike", theme.c("task_done_text")
        elif task.due_state == "overdue":
            role, color = "meta_bold", theme.c("due_over")
        elif task.due_state == "today":
            role, color = "meta_bold", theme.c("due_soon")
        else:
            role, color = "meta", theme.c("due_text")

        self.due_label.set_text(
            text, role=role, color=color,
            strike=role.endswith("strike"), box_h=theme.lpx(theme.TASK_META_H))

        # 逾期徽章紧贴在时间**前面**：pack 默认追加到最右，必须显式 before=，
        # 否则「09/25 07:21 逾期」——徽章会把时间顶离右边缘，有逾期与没逾期的
        # 两行时间就不在同一个右边界上（1.5.8 修的就是这个）。
        if task.is_overdue:
            self.overdue_tag.set_text(
                strings.OVERDUE_TAG, role="tiny_bold", color=theme.c("on_danger"),
                pill=theme.c("due_over"),
                pill_radius=theme.lpx(theme.TASK_DUE_TAG_RADIUS),
                pad_x=theme.lpx(theme.TASK_DUE_TAG_PAD_X),
                min_h=theme.lpx(theme.TASK_DUE_TAG_H),
                box_h=theme.lpx(theme.TASK_META_H))
            self.overdue_tag.pack(side="left", before=self.due_label,
                                  padx=(0, theme.TASK_DUE_TAG_GAP))
        else:
            self.overdue_tag.pack_forget()

        # 只有真正有内容时才占位。meta_row 在 right_box 里、必须位于动作容器
        # **左侧**（before=actions）；1.5.14 的右内边距由 right_box 统一提供。
        if not self.meta_row.winfo_ismapped():
            self.meta_row.pack(side="left", before=self.actions)
        self._update_stripe()
        # meta 显隐/徽章增删会改变预留宽 —— 空闲时重截一次标题
        self.after_idle(self._sync_wraplength)

    def _update_stripe(self) -> None:
        """逾期竖线：只在"未完成且已逾期"时出现。"""
        if self.task.is_overdue:
            self.stripe.configure(fg_color=theme.pair("due_over"))
        else:
            self.stripe.configure(fg_color="transparent")

    # ------------------------------------------------------------------
    # 悬停（带 30ms 延迟消除子控件切换抖动）
    # ------------------------------------------------------------------
    def _enter(self) -> None:
        if self._hover_job:
            try:
                self.after_cancel(self._hover_job)
            except Exception:  # noqa: BLE001
                pass
            self._hover_job = None
        if self._hover or self._dragging:
            return
        self._hover = True
        self._apply_style()
        self.set_actions_visible(True)      # 需求 26：hover 才淡入右侧图标

    def _leave(self) -> None:
        if self._hover_job:
            try:
                self.after_cancel(self._hover_job)
            except Exception:  # noqa: BLE001
                pass
        self._hover_job = self.after(30, self._leave_now)

    def _leave_now(self) -> None:
        self._hover_job = None
        if not self._hover:
            return
        self._hover = False
        self._apply_style()
        self.set_actions_visible(False)

    def _apply_style(self) -> None:
        # 落位脉冲进行中：底色由 flash_settle 逐帧接管，这里插一脚会把它打断成
        # 闪烁。脉冲结束时自己会回叫一次本方法，把当前真实状态（悬停 / 常态）落定。
        if self._settle_job is not None:
            return
        if self._dragging:
            self._apply_drag_style()
            return
        # 需求 2：不用硬边框拉开层次，悬停靠底色变化表达
        # 1.5.24：悬停色按"是否已完成"分两档。已完成卡片若在悬停时退回暖米
        # （card_hover），绿底就消失了 —— 鼠标一放上去反而看不出完成状态。
        if self._hover:
            self.configure(fg_color=theme.pair(
                "card_done_hover" if self.task.done else "card_hover"))
            self._sync_meta_bg(self._hover_bg())
        else:
            self.configure(
                fg_color=(theme.pair("card_done") if self.task.done else theme.pair("card"))
            )
            self._sync_meta_bg(theme.c("card_done") if self.task.done
                               else theme.c("card"))

    def _hover_bg(self) -> str:
        """当前悬停态应当用的**单色**（裸 Canvas 用，不认 CTk 元组）。"""
        return theme.c("card_done_hover" if self.task.done else "card_hover")

    def _sync_meta_bg(self, color: str) -> None:
        """把卡片当前底色同步给两个元信息标签（复选框 + 行内标签一起走）。

        ``AAText`` 与 ``SunCheck`` 一样是**裸 Canvas**：Tk 控件没有 alpha，
        画布底色写死就会在"卡片切了底色"时露出一块色差方块。
        ⚠️ 容缺：``__init__`` 里拖拽态会在这些控件建出来**之前**就调用本方法。
        """
        check = getattr(self, "check", None)
        if check is not None:
            check.set_bg(color)
        for name in ("due_label", "overdue_tag"):
            label = getattr(self, name, None)
            if label is not None:
                label.set_bg(color)

    def _apply_drag_style(self) -> None:
        """拖拽中的"抬起"效果：更亮的底色 + 一圈柔橘描边模拟上浮（需求 31）。

        为什么不真的加阴影/位移：卡片位置由 pack 管理，拖拽过程中每次落点变化
        都会重建列表，加位移会和布局打架。用"高亮边框 + 亮一档的底色"表达
        '这个词条现在浮在手上'，在任何缩放/主题下都稳定。
        """
        self.configure(fg_color=theme.pair(self._hover_bg()),
                       border_color=theme.pair("orange"), border_width=2)
        self._sync_meta_bg(self._hover_bg())

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _handle_press(self, event: tk.Event) -> None:
        self._on_press(self.task.id, event)

    # ------------------------------------------------------------------
    def refresh_state(self, done: Optional[bool] = None) -> None:
        """原地刷新"完成状态"，保留打勾动画（不重建卡片）。

        ❗ 这个方法曾经只被调用、从未被定义。缺失导致 ``TaskPage._toggle``
        在 ``set_done`` 之后、更新计数/摘要/庆祝动画之前就抛 AttributeError，
        表现为"勾选生效了，但组内计数、缕光数字、庆祝动画全都不动"。
        补上它是本轮修掉的一个真 bug。
        """
        if done is not None:
            self.task.done = bool(done)
        try:
            if self.check._checked != self.task.done:
                self.check.set(self.task.done, notify=False)
        except Exception:  # noqa: BLE001
            pass
        self.title_label.configure(
            text=self.task.title or strings.UNNAMED_TITLE,
            font=theme.font("task_title_done" if self.task.done else "task_title"),
            text_color=(theme.pair("task_done_text") if self.task.done
                        else theme.pair("text")),
        )
        self._apply_style()
        self.refresh_due()
        # 完成态换了字重，标题测量宽会变 —— 重新截一次（卡片已映射，宽度可信）
        self.after_idle(self._sync_wraplength)

    # ------------------------------------------------------------------
    # 落位提示（1.5.7）
    # ------------------------------------------------------------------
    def _cancel_settle(self) -> None:
        if self._settle_job is not None:
            try:
                self.after_cancel(self._settle_job)
            except Exception:  # noqa: BLE001
                pass
            self._settle_job = None

    def flash_settle(self, steps: int = theme.TASK_SETTLE_STEPS,
                     interval: int = theme.TASK_SETTLE_INTERVAL) -> None:
        """勾选完成后"沉到已完成区"的落位提示：**只渐隐底色**。

        为什么不真的做位移/高度动画
        --------------------------
        卡片位置由 ``pack`` 管着，逐帧改位置或行高就是逐帧触发整页重排 ——
        那正是 1.5.4 修掉的"展开分组时画面抖动"。所以归位本身是**一次性布局**
        瞬时完成（见 ``TaskPage._reorder_for``），随后用一次约 170ms 的底色柔光
        （``accent_soft`` 渐隐回卡片自身的 card / card_done）把视线引到新位置上。
        只改颜色 = 不触发任何 reflow = 零抖动。

        ⚠️ 两个坑：
        1. 复选框是**裸 Canvas**，底色得跟着一起插值，否则脉冲期间会在卡片上
           留一块色差方块（1.5.6 的 ``set_bg`` 就是为这件事加的）。
        2. 用户点勾选框时光标**必然**停在卡片上（``_hover`` 为真），所以这里
           不能"悬停就不播"，而是让脉冲临时接管底色、结束时再 ``_apply_style()``
           把悬停态还回去。``_apply_style`` 里的 ``_settle_job`` 守卫负责互斥。
        """
        self._cancel_settle()
        done = bool(self.task.done)
        light_to = theme.LIGHT.get("card_done" if done else "card", "#FFFFFF")
        dark_to = theme.DARK.get("card_done" if done else "card", "#2D2A26")
        light_from = theme.LIGHT.get(theme.TASK_SETTLE_COLOR, light_to)
        dark_from = theme.DARK.get(theme.TASK_SETTLE_COLOR, dark_to)
        dark_mode = theme.is_dark()

        def step(i: int) -> None:
            self._settle_job = None
            try:
                if not self.winfo_exists():
                    return
                t = min(1.0, i / max(1, steps))
                light = theme.mix(light_from, light_to, t)
                dark = theme.mix(dark_from, dark_to, t)
                self.configure(fg_color=(light, dark))
                self._sync_meta_bg(dark if dark_mode else light)
            except Exception:  # noqa: BLE001
                return
            if i < steps:
                try:
                    self._settle_job = self.after(interval, step, i + 1)
                except Exception:  # noqa: BLE001
                    self._settle_job = None
            else:
                # 脉冲结束：把底色交还给常态 / 悬停态
                self._apply_style()

        step(0)

    def _show_menu(self, at: Optional[Tuple[int, int]] = None) -> None:
        """弹出右键菜单。``at`` 给屏幕坐标（物理像素），缺省用鼠标位置。

        截图/测试需要确定性位置，所以留了这个口子 —— 依赖鼠标位置的话，
        无头/脚本环境下光标停在 (0,0) 或别的显示器上，菜单会跑到屏幕外。
        """
        running = self._pomodoro.get("task_id") == self.task.id and self._pomodoro.get("running")

        # 分组名走 MenuRow.label（str 类型标注），图标与文字**分开传**。
        # 历史写法是 f"{grp.icon} {grp.name}" —— 一旦 grp.icon 是函数对象，
        # 整个内存地址就被格式化进菜单了。现在两者都过 clean_label/ellipsize 净化。
        move_items = []
        for grp in self.dispatch("groups", None):
            is_current = grp.id == self.task.group_id
            move_items.append(
                menu.MenuRow(
                    label=menu.ellipsize(str(grp.name), 10),
                    action=None if is_current else (
                        lambda gid=grp.id: self.dispatch("move", (self.task.id, gid))),
                    disabled=is_current,
                )
            )

        due_children = [
            menu.row("设置截止日期…", lambda: self.dispatch("due", self.task.id),
                     icon=self._icon("calendar")),
        ]
        if self.task.due_date is not None:
            due_children.append(
                menu.row("清除截止日期", lambda: self.dispatch("clear_due", self.task.id))
            )

        rows = [
            menu.row("编辑任务", lambda: self.dispatch("edit", self.task.id),
                     icon=self._icon("edit")),
            menu.row("停止专注" if running else "开始专注",
                     lambda: self.dispatch("pomodoro", self.task.id),
                     icon=self._icon("pomodoro")),
            menu.separator(),
            menu.row("标记未完成" if self.task.done else "标记完成",
                     lambda: self.dispatch("toggle", (self.task.id, not self.task.done)),
                     icon=self._icon("check")),
            menu.submenu("设置截止日期", due_children, icon=self._icon("calendar")),
            menu.submenu("移动到分组", move_items, icon=self._icon("move")),
            menu.separator(),
            menu.row("删除任务", lambda: self.dispatch("delete", self.task.id),
                     icon=self._icon("delete")),
        ]
        if at is not None:
            menu.popup_menu(self, rows, x=at[0], y=at[1])
        else:
            menu.popup_menu(self, rows)

    def _icon(self, name: str):
        """取图标（新图标系统；不可用时返回 None，菜单自动少一列，不会报错）。"""
        try:
            from .. import icons
            # 菜单 Canvas 是物理像素坐标系，图标尺寸要用 lpx() 后的物理值
            # 才不会在 150% 屏上看起来只有设计的 2/3 大。
            return icons.get(name, size=theme.lpx(icons.SIZE_MAIN))
        except Exception:  # noqa: BLE001
            return None


class GroupCard(ctk.CTkFrame):
    """分组卡片：标题栏 + 可折叠的任务列表。

    标题栏构成（本轮需求 16/17/19/20，行高 40px）
    --------------------
        折叠箭头 ▾(12px) + 图标(22px) + 名称(14px Bold) + 任务数徽章(间距 6px)
        + 弹性空白 + "＋"(18px，hover 淡入) + ⋯菜单

    折叠/展开是**瞬时切换**（1.5.4：删掉了 200ms 高度动画 —— 那套逐帧改容器
    高度会带动整页重排，用户看到的就是"展开时画面抖动"）。
    """

    def __init__(
        self,
        master: tk.Misc,
        app,
        group,
        tasks,
        render_task: Callable,
        dispatch: Callable[[str, Any], None],
    ) -> None:
        super().__init__(master, corner_radius=theme.RADIUS_CARD, fg_color="transparent")
        self.app = app
        self.group = group
        self.dispatch = dispatch
        self.cards: list[TaskCard] = []
        self._photo = None
        self._empty_hint = None
        self._reveal_job: Optional[str] = None

        self.grid_columnconfigure(0, weight=1)

        # ---------------- 标题栏（需求 16/17/19/20：行高 40px，图标 22px）----------------
        self.header = ctk.CTkFrame(self, corner_radius=8, fg_color="transparent",
                                   height=theme.GROUP_HEADER_HEIGHT)
        self.header.grid(row=0, column=0, sticky="ew", pady=(2, 1), padx=1)
        self.header.grid_propagate(False)
        # 弹性空白**单独占一列**（col 4，不放任何控件）。
        # 不要把弹性挂在徽章那一列上：窗口收窄到接近下限（380）时，grid 会
        # 优先压缩带权重的那一列，于是 "2/5" 徽章被压成一条**空药丸**
        # （窄窗口预览图里一眼可见）。空白列请求宽度是 0，挤压时它先让位，
        # 徽章就能保住它那 42px。
        self.header.grid_columnconfigure(4, weight=1)
        # 单行撑满整行高：grid 默认把"装得下但没权重"的行贴在顶部，
        # 于是 22px 图标、16px 箭头、14px 文字的基线各不相同，看着像没对齐。
        # 给该行一个权重，所有子控件就会在同一条**垂直中轴**上。
        self.header.grid_rowconfigure(0, weight=1)

        # -- 折叠箭头 ▾（1.5.12：14px、柔橘色、折叠态浅米底衬）--
        # 用户反馈"箭头不够强烈、难以察觉"，三处一起改：
        #   * 尺寸 GROUP_CHEVRON 12 -> 14；
        #   * 颜色从灰棕换成 accent（柔橘）—— 折叠态由 _animate_chevron 画；
        #   * 折叠时给按钮一个 ghost（浅米）圆角底，标出可点击区域。
        # 构造时先摆好初始角度（折叠的分组直接停在 90°），动画由 toggle 触发。
        self._chevron_angle = 90.0 if group.collapsed else 0.0
        self._chevron_job: Optional[str] = None
        self.chevron = widgets.IconButton(
            self.header, text="▾", icon="chevron", size=theme.GROUP_HEADER_HEIGHT - 8,
            icon_size=theme.GROUP_CHEVRON,
            command=lambda: self.dispatch("toggle_group", group.id),
        )
        self.chevron.grid(row=0, column=0, padx=(theme.GROUP_CHEVRON_GAP, 0))
        self._apply_chevron_pill()
        self._set_chevron_angle(self._chevron_angle)

        # -- 分组图标（自绘，22px；需求 16）--
        self.icon_label = ctk.CTkLabel(self.header, text="", width=theme.GROUP_ICON,
                                       image=None)
        self._set_group_icon_image()
        self.icon_label.grid(row=0, column=1, padx=(theme.GROUP_CHEVRON_GAP, 0))

        # -- 分组名（14px Bold，与图标基线对齐）--
        self.title_btn = ctk.CTkButton(
            self.header,
            text=str(group.name),
            font=theme.font("group_title"),
            text_color=theme.pair("text"),
            fg_color="transparent",
            hover_color=theme.pair("ghost"),
            anchor="w",
            height=theme.GROUP_HEADER_HEIGHT - 6,
            command=lambda: self.dispatch("toggle_group", group.id),
        )
        self.title_btn.grid(row=0, column=2,
                            padx=(theme.GROUP_ICON_TEXT_GAP, 0), sticky="w")
        # 宽度收成"正好一个文字宽"：CTkButton 默认 140px，会在分组名后面一直
        # 留一大截空白，把"2/5"徽章推到很远的地方 —— 默认窗口下看着像两截，
        # 窄窗口下（可用宽度不够）更明显。实测文字宽 + 一点呼吸量即可。
        try:
            from .. import fonts as _fonts
            self.title_btn.configure(
                width=_fonts.measure("group_title", str(group.name)) + 8)
        except Exception:  # noqa: BLE001
            pass

        # -- 任务数徽章（圆形暖灰底 + 11px 数字，与名称间距 6px）--
        done = sum(1 for t in tasks if t.done)
        self.count_label = ctk.CTkLabel(
            self.header,
            text=f"{done}/{len(tasks)}",
            font=theme.font("badge"),
            text_color=theme.pair("text_muted"),
            fg_color=theme.pair("ghost"),
            corner_radius=7,
            width=theme.GROUP_BADGE_W, height=theme.GROUP_BADGE_H,
        )
        self.count_label.grid(row=0, column=3, sticky="w",
                              padx=(theme.GROUP_BADGE_GAP, 3))

        # -- 弹簧 + 右侧按钮（弹簧 = 空列 4，见上面的 columnconfigure）--
        # "＋"（18px）默认隐藏，悬停标题行才淡入（需求 17）
        self.add_btn = widgets.IconButton(
            self.header, text="＋", icon="plus", size=theme.GROUP_BTN,
            icon_size=theme.TASK_ICON,
            command=lambda: self.dispatch("add_to_group", group.id),
        )
        self.add_btn.grid(row=0, column=5, padx=(2, 0))
        self.add_btn.set_fade(0.0)

        menu_btn = widgets.IconButton(
            self.header, text="⋯", icon="more", size=theme.GROUP_BTN,
            icon_size=theme.TASK_ICON,
            command=self._show_group_menu,
        )
        menu_btn.grid(row=0, column=6, padx=(2, 2))

        # hover 淡入淡出：指针在标题行任一子控件上都算"在行内"
        self._bind_header_hover()

        # ---------------- 任务列表 ----------------
        self.container = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.container.grid(row=1, column=0, sticky="ew", padx=0)
        if group.collapsed:
            self.container.grid_remove()

        for i, task in enumerate(tasks):
            card = render_task(self.container, task, i)
            if card is not None:
                card.pack(fill="x", pady=(0, theme.TASK_ROW_GAP))
                self.cards.append(card)

        if not tasks:
            # 文案取自 strings（需求 ④：全代码库不允许出现第二份空状态文案）
            self._empty_hint = widgets.TextLabel(
                self.container,
                text=strings.EMPTY_GROUP_HINT,
                font=theme.font("empty"),
                text_color=theme.pair("text_done"),
                wraplength=theme.EMPTY_WRAP,
                justify="left",
            )
            self._empty_hint.pack(anchor="w", padx=theme.lpx(theme.CARD_PAD_X),
                                  pady=(2, theme.lpx(6)))

    # ------------------------------------------------------------------
    def _bind_header_hover(self) -> None:
        """"＋"按钮跟随指针淡入/淡出（需求 17）。

        不直接信 Enter/Leave：指针从标题行移进子按钮时会先触发 header 的
        Leave 再触发按钮的 Enter，直接切换会闪。统一延迟一拍后按**指针
        实际位置**判定，和任务卡片操作区的做法一致。
        """
        targets = [self.header, getattr(self.header, "_canvas", None)]
        try:
            targets += list(self.header.winfo_children())
        except Exception:  # noqa: BLE001
            pass
        for w in targets:
            if w is None:
                continue
            w.bind("<Enter>", lambda _e: self.after(30, self._sync_add_fade), add="+")
            w.bind("<Leave>", lambda _e: self.after(60, self._sync_add_fade), add="+")

    def _sync_add_fade(self) -> None:
        try:
            x, y = self.winfo_pointerxy()
            hx, hy = self.header.winfo_rootx(), self.header.winfo_rooty()
            inside = (hx <= x <= hx + self.header.winfo_width()
                      and hy <= y <= hy + self.header.winfo_height())
            self.add_btn.set_fade(1.0 if inside else 0.0)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def _set_group_icon_image(self) -> None:
        """设置分组图标（自绘，22px）。

        ``group.icon`` 存的是**历史 emoji**（``"💼"`` / ``"📖"`` / ``"🌿"``），
        不是图标注册表的键名。这里必须走一次 ``icons.resolve_key()``
        把它翻译成 ``grp_work`` 之类的自绘键；否则注册表查不到，
        图标会静默变空 —— 分组标题左侧就只剩名字，看着像缺了一块。
        """
        raw = self.group.icon if isinstance(self.group.icon, str) else ""
        photo = None
        try:
            from .. import icons
            key = icons.resolve_key(raw) or "grp_work"
            photo = icons.get_ctk(key, theme.GROUP_ICON)
        except Exception:  # noqa: BLE001
            photo = None
        self._photo = photo
        try:
            # 取不到图标时**什么都不显示**（原来退化成 "·"，会在分组标题左侧
            # 留下一个无缘无故的小点 —— 和本轮删掉的橙色圆点一样属于"装饰性
            # 噪点"，宁可空白也不要假信息）。
            self.icon_label.configure(image=photo, text="")
        except Exception:  # noqa: BLE001
            pass

    def update_count(self) -> None:
        """刷新标题栏的完成计数（如 2/5）。"""
        tasks = self.app.store.tasks_in(self.group.id)
        done = sum(1 for t in tasks if t.done)
        self.count_label.configure(text=f"{done}/{len(tasks)}")

    def remove_task_card(self, task_id: str) -> bool:
        """Remove one task in place, without rebuilding the rest of the page."""
        removed = [card for card in self.cards if card.task.id == task_id]
        if not removed:
            return False
        self.cards = [card for card in self.cards if card.task.id != task_id]
        for card in removed:
            try:
                card.destroy()
            except Exception:  # noqa: BLE001
                pass

        if not self.cards and not self.app.store.tasks_in(self.group.id):
            if self._empty_hint is None:
                self._empty_hint = widgets.TextLabel(
                    self.container,
                    text=strings.EMPTY_GROUP_HINT,
                    font=theme.font("empty"),
                    text_color=theme.pair("text_done"),
                    wraplength=theme.EMPTY_WRAP,
                    justify="left",
                )
                self._empty_hint.pack(anchor="w", padx=theme.lpx(theme.CARD_PAD_X),
                                      pady=(2, theme.lpx(6)))
        return True

    # ------------------------------------------------------------------
    # 折叠 / 展开（1.5.4：瞬时切换，不做高度动画）
    # ------------------------------------------------------------------
    def set_collapsed(self, collapsed: bool) -> None:
        """瞬时切换分组内容的显隐，一次布局、零中间态。

        为什么删掉了原来那套 200ms 高度动画（1.5.4 修的"画面抖动"）
        ----------------------------------------------------------
        旧实现用 ``after`` 逐帧改 ``container`` 的 ``height``（8 帧 × 25ms，
        全程 ``grid_propagate(False)``）。三个问题叠在一起就是用户看到的抖动：

        1. 逐帧改高度 = 逐帧触发整页重排，滚动容器里下方所有分组每 25ms
           被推一次；
        2. ``grid_propagate(False)`` 期间容器高度小于内容高度，卡片被**裁切**；
        3. 首帧把高度压到 1 像素、末帧又交还给自适应布局，两端各硬跳一下。

        现在直接 ``grid()`` / ``grid_remove()``：``grid_remove`` 会记住 grid
        参数，``grid()`` 原样恢复，既不用重建控件，也不会动别的分组。
        """
        try:
            if collapsed:
                if self._reveal_job is not None:
                    try:
                        self.after_cancel(self._reveal_job)
                    except Exception:  # noqa: BLE001
                        pass
                    self._reveal_job = None
                for card in self.cards:
                    card.cancel_reveal_layout()
                self.container.grid_remove()
            else:
                for card in self.cards:
                    card.begin_reveal_layout()
                self.container.grid()
                if self.cards:
                    if self._reveal_job is not None:
                        self.after_cancel(self._reveal_job)
                    self._reveal_job = self.after_idle(self._finish_reveal_layout)
        except Exception:  # noqa: BLE001
            pass

    def _finish_reveal_layout(self) -> None:
        self._reveal_job = None
        for card in self.cards:
            try:
                if card.winfo_exists():
                    card.finish_reveal_layout()
            except Exception:  # noqa: BLE001
                card.cancel_reveal_layout()

    # ------------------------------------------------------------------
    # 折叠箭头（1.5.12：颜色 / 底衬 / 150ms 旋转动画）
    # ------------------------------------------------------------------
    def _apply_chevron_pill(self) -> None:
        """折叠态给箭头一块浅米（ghost）圆角底，标出"这里可以点"。"""
        try:
            collapsed = bool(getattr(self.group, "collapsed", False))
            self.chevron.configure(
                fg_color=theme.pair("ghost") if collapsed else "transparent",
                hover_color=theme.pair("ghost_hover") if collapsed
                else theme.pair("ghost"))
        except Exception:  # noqa: BLE001
            pass

    def _set_chevron_angle(self, angle: float) -> None:
        """把箭头画成 ``angle`` 度（0=朝下 ▾，90=朝右 ▸），柔橘色。"""
        self._chevron_angle = angle
        try:
            from .. import icons
            photo = icons.get_ctk_chevron_rotated(
                theme.GROUP_CHEVRON, angle, theme.c("accent"))
        except Exception:  # noqa: BLE001
            photo = None
        if photo is not None:
            try:
                self.chevron.configure(image=photo, text="")
                self._chevron_photo = photo      # 持引用防 GC
            except Exception:  # noqa: BLE001
                pass

    def _animate_chevron(self, target: float) -> None:
        """150ms 旋转动画：逐帧换图（22.5°/帧），**不改任何布局尺寸**。

        与 1.5.4 的教训一致 —— 动画绝不能逐帧改容器高度（整页重排=抖动）；
        这里只替换 IconButton 上的位图，几何完全不动。
        """
        if self._chevron_job is not None:
            try:
                self.after_cancel(self._chevron_job)
            except Exception:  # noqa: BLE001
                pass
            self._chevron_job = None
        start = self._chevron_angle
        steps = max(1, theme.GROUP_CHEVRON_STEPS)
        interval = max(10, theme.GROUP_CHEVRON_ANIM_MS // steps)

        def step(i: int) -> None:
            self._chevron_job = None
            ang = start + (target - start) * ((i + 1) / steps)
            self._set_chevron_angle(ang)
            if i + 1 < steps:
                self._chevron_job = self.after(interval, lambda: step(i + 1))

        step(0)

    def animate_chevron(self, collapsing: bool) -> None:
        """toggle_group 入口：转箭头 + 同步折叠底衬。"""
        self._apply_chevron_pill()
        self._animate_chevron(90.0 if collapsing else 0.0)

    def _show_group_menu(self, at: Optional[Tuple[int, int]] = None) -> None:
        """弹出分组右键菜单。``at`` 给屏幕坐标（物理像素），缺省用鼠标位置。

        与 :meth:`_show_menu` 同理：脚本/测试环境的光标位置不可控，留这个口子
        才能稳定截图与断言。

        菜单构成（需求 ⑥：**5 项 + 两条分隔线**，不允许再出现"设为快速添加
        分组"）：添加任务 / 折叠展开 · 重命名 / 更换图标 · 删除分组。

        为什么砍掉"设为快速添加分组"：它有"设置默认分组"这个语义，但
        ``settings["default_group"]`` 真正生效的地方是**浮层没指定分组时**
        的回落值，而浮层每次打开都默认选中当前分组 —— 用户设完看不到任何
        变化（既没有勾选标记，也没有行为差别），只会以为功能坏了。
        删掉它，默认分组由"最近一次使用的分组"隐式维护，不需要用户操心。
        """
        rows = [
            menu.row("添加任务", lambda: self.dispatch("add_to_group", self.group.id),
                     icon=self._icon("plus")),
            menu.row("展开分组" if self.group.collapsed else "折叠分组",
                     lambda: self.dispatch("toggle_group", self.group.id),
                     icon=self._icon("chevron")),
            menu.separator(),
            menu.row("重命名分组", lambda: self.dispatch("rename_group", self.group.id),
                     icon=self._icon("edit")),
            menu.row("更换图标", lambda: self.dispatch("icon_group", self.group.id),
                     icon=self._icon_from_group()),
            menu.separator(),
            menu.row("删除分组", lambda: self.dispatch("delete_group", self.group.id),
                     icon=self._icon("delete")),
        ]
        menu.popup_menu(self.header, rows, *(at or (None, None)))

    def _icon_from_group(self):
        """当前分组的图标键（给"更换图标"菜单项做预览）。

        ⚠️ 返回的是**图标键**（``grp_work`` 这种），不是 ``group.icon`` 原值 ——
        后者可能是历史 emoji（``"💼"``），再喂给 :meth:`_icon` 也能吃
        （``icons.get`` 内部有 emoji 迁移表），但"更换图标"这一项应当和
        分组标题左侧**显示同一张图**，所以统一在这里翻译一次。
        优先读 ``store.group_icon``（settings 里的权威值），再回落到旧字段。
        """
        try:
            key = self.app.store.group_icon(self.group.id)
        except Exception:  # noqa: BLE001
            key = self.group.icon if isinstance(self.group.icon, str) else ""
        if key:
            return self._icon(key)
        return self._icon("grp_life")

    def _icon(self, name: str):
        """菜单项图标。

        两个坑（历史代码全踩了）：

        1. 尺寸要给**物理像素** —— 菜单是裸 ``tk.Canvas``，画布坐标已经是物理
           像素，``menu.ICON_SIZE`` 却只是 18 的逻辑值，直接拿来出图会偏小。
        2. 第二个位置参数是 ``size``，不是颜色。旧代码写的是
           ``icons.get(name, theme.c("text_muted"))``，颜色字符串进到
           ``int()`` 里立刻抛异常，又被这里的 ``except`` 吞掉 ——
           表现就是"菜单项左侧留了空位却不画图标"。
        """
        try:
            from .. import icons
            return icons.get(name, theme.lpx(menu.ICON_SIZE))
        except Exception:  # noqa: BLE001
            return None
