# -*- coding: utf-8 -*-
"""新建任务浮层（替代底部常驻输入框）。

为什么做成浮层而不是常驻控件
----------------------------
常驻输入框一直挂在任务列表底部，占掉一行的空间、把列表压扁，
浏览任务时视线被拦住。改成"＋ 新建任务"按钮 + 点击弹浮层之后，
列表可以完整展示，输入动作依然是"一次点击 + 一次回车"。

形态
----
* ``overrideredirect`` 的裸 ``Toplevel`` + 裸 ``tk.Canvas``：
  圆角 12px、柔和投影（向底色渐隐的描边环，tkinter 没有真阴影）。
* 内容三行：
  1. 单行输入框 + 已选截止日期（含提醒文案）+ 日历小图标；
  2. **分组标签条** —— 从 store **动态读取**当前全部分组（工作/学习/生活…），
     每个标签带该分组自己的图标，选中态柔橘实心 + 白字白图标；
  3. 确认 / 取消按钮（与回车、Esc 等价），见 ``_build_content``。
* 关闭：Esc / 失焦（带延迟与指针兜底）/ 点外部 / 提交成功后自动收起。

置顶与让位（1.5.1 修用户报的两条）
---------------------------------
1. **不再无条件 ``-topmost``**。原来浮层恒置顶，于是①切到别的应用后它还
   挂在屏幕最上层；②它盖住了自己弹出的**日期选择器**（选择器只跟随主窗口的
   置顶设置），用户选不了时间。现在 ``sync_topmost()`` 跟随主窗口设置，
   与 ``dialogs._BaseDialog`` 同一口径。
2. **选择器打开期间浮层临时 withdraw**（``_suspend_for_modal``）。小窗模式
   下浮层与选择器纵向排不开，收起父浮层是唯一永远不遮挡的解，关掉选择器后
   原样恢复，输入内容不丢。
3. **"切走就收"补了两道**：主窗口 ``<FocusOut>``（事件路）+ 前台看门狗
   （``_watch_foreground``，轮询 ``GetForegroundWindow`` 的进程 id）。
   只靠浮层自己的 ``<FocusOut>`` 不够 —— overrideredirect 顶层窗口在部分
   切换路径下收不到那一拍事件，浮层就再也不消失。

生命周期（本轮重写，需求 ①）
----------------------------
上一版的实现有三个洞，合起来就是用户报的"浮层残留、必须重启才消失"：

1. **``close()`` 先置标志再 destroy**。``destroy()`` 一旦抛异常（被 grab 的
   对话框牵制、after 回调正在跑等），标志已经置位 → 之后每次 ``close()`` 都
   直接 return，**永远不会再销毁** → 幽灵浮层。
2. **``_child_modal`` 只有"确定"那条路会复位**。日期选择器按"取消"/X/Esc
   销毁时根本不回调 ``on_save``，标志永远停在 True，于是"点外部收起"这条
   路径彻底失效 → 浮层赖着不走。
3. **销毁后仍可能被回调**。回车事件、after 队列里存的 ``self`` 指向已销毁的
   浮层，``self.entry.focus_set()`` 抛 ``TclError``，异常被 Tk 吞掉 →
   表现为"点了没反应"。

所以这里把生命周期重写成：
* **幂等**：``_closed`` / ``_closing`` 双标志，任何入口重复调用都直接返回；
* **宁可不可见，不可残留**：``_destroy_now()`` 先 ``withdraw()`` 再 ``destroy()``，
  即使 destroy 失败也已经看不见；失败还会排一次重试并记账；
* **模态计数 + 宽限期**：子对话框用 ``try/finally`` 归还计数，不管它从哪条
  路径关闭；归还后再给 0.6s 宽限期，避免"对话框刚关、焦点转场"的瞬间被误判成失焦；
* **失焦判定加指针兜底**：焦点不在浮层内**且**指针也不在（含安全热区）才收，
  和 ``ui/menu.py`` 的看门狗同一个思路；
* **after 统一登记**：``_after()`` 记录 job id，``<Destroy>`` 里全部 cancel，
  不留野回调。

每个分支都有日志（``[NewTask] opened / submitted / cancelled / destroyed``），
出问题时能直接从 ``shiguang.log`` 看出是"没关"还是"关了又活"。

DPI 说明
--------
与 ``menu.ContextMenu`` 同一个坑：裸 ``Toplevel`` / ``Canvas`` 不受
CTk 缩放保护，所有尺寸必须过 ``theme.lpx()``，在构造时一次性换算。
"""

from __future__ import annotations

import datetime as _dt
import time
import tkinter as tk
from typing import Callable, List, Optional, Tuple

import customtkinter as ctk

from .. import icons, strings, theme
from ..models import REMIND_DEFAULT, remind_label
from . import widgets

# 淡入参数：Toplevel 有真 alpha，可以做真正的透明度动画
FADE_STEPS = 5
FADE_INTERVAL = 20        # ≈100ms

# 已选截止日期的展示格式（短到不会挤爆单行布局）
_DUE_FMT = "%m/%d %H:%M"


class GroupChipStrip(tk.Canvas):
    """横向标签条：装一组分组 chip，装不下时可以横滚。

    为什么自己做一个而不是用 ``CTkScrollableFrame``：
    CTk 的滚动容器在"高度只有 28px、内容只有几个标签"这种场景下会强行留出
    滚动条位置，药丸标签条上会凭空多出一条灰杠（和 4px 极细滚动条的既定风格
    也不一致）。这里只需要"能拖 / 能滚轮"，不需要滚动条，自己画最省事。
    """

    DRAG_TOLERANCE = 4        # 按下后移动超过这个距离才算"拖动"（逻辑像素）

    def __init__(self, master: tk.Misc, height: int, bg: str) -> None:
        self._ch = theme.lpx(height)
        super().__init__(master, height=self._ch, bg=bg, highlightthickness=0, bd=0)
        self._inner = ctk.CTkFrame(self, fg_color="transparent", height=height)
        self._win = self.create_window(0, 0, window=self._inner, anchor="nw")
        self._drag_from: Optional[int] = None
        self._drag_base = 0
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Shift-MouseWheel>", self._on_wheel)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag_from", None))

    # ------------------------------------------------------------------
    @property
    def inner(self) -> ctk.CTkFrame:
        return self._inner

    def needed_width(self) -> int:
        """内容需要的宽度（**物理像素**，因为最终要跟 Canvas 坐标比）。"""
        try:
            self._inner.update_idletasks()
            return self._inner.winfo_reqwidth()
        except Exception:  # noqa: BLE001
            return 0

    def fit(self, available: int) -> None:
        """把可视宽度设成 ``available``（物理像素），内容超出即可横滚。"""
        available = max(1, int(available))
        self.configure(width=available)
        try:
            self.coords(self._win, 0, 0)
        except Exception:  # noqa: BLE001
            pass

    def scrollable(self) -> bool:
        return self.needed_width() > self.winfo_width() + 1

    # ------------------------------------------------------------------
    def _max_offset(self) -> int:
        return max(0, self.needed_width() - self.winfo_width())

    def _set_offset(self, x: float) -> None:
        offset = max(0, min(self._max_offset(), int(x)))
        try:
            self.coords(self._win, -offset, 0)
        except Exception:  # noqa: BLE001
            pass

    def _current_offset(self) -> int:
        try:
            return -int(self.coords(self._win)[0])
        except Exception:  # noqa: BLE001
            return 0

    def _on_wheel(self, event) -> str:
        if not self.scrollable():
            return ""
        # 1.5.27：方向**只由符号决定**。以前用 `abs(delta)` 绕一圈再靠三元表达式
        # 找回正负 —— 能跑，但正是"方向被吞掉"这类 bug 的温床；这里显式写成
        # "上拨左移、下拨右移"，abs 只用于取**力度**，不参与定方向。
        delta = widgets.wheel_delta(event)
        if not delta:
            return ""
        mag = max(1, abs(int(delta))) // 2 * 2
        shift = -mag if delta > 0 else mag
        self._set_offset(self._current_offset() + shift)
        return "break"

    def _on_press(self, event) -> None:
        self._drag_from = event.x
        self._drag_base = self._current_offset()

    def _on_drag(self, event) -> None:
        if self._drag_from is None:
            return
        dx = event.x - self._drag_from
        if abs(dx) < theme.lpx(self.DRAG_TOLERANCE):
            return
        self._set_offset(self._drag_base - dx)


class QuickAddPopup(tk.Toplevel):
    """"＋ 新建任务"按钮下方的悬浮输入浮层。"""

    def __init__(
        self,
        app,
        anchor: tk.Misc,
        group_id: str = "",
        on_submit: Optional[Callable[..., None]] = None,
    ) -> None:
        super().__init__(app)
        self.app = app
        self.on_submit = on_submit
        self._due: Optional[_dt.datetime] = None
        # 提醒提前量（分钟，None = 不提醒）：默认"提前15分钟"。
        # 真正的选择在截止日期弹窗里（时间行下方），这里只保存状态并在
        # 截止日期标签旁把它显示出来 —— 否则设过了也看不出来。
        self._remind: Optional[int] = REMIND_DEFAULT
        self._closed = False
        self._closing = False
        self._jobs: set = set()
        self._modal_count = 0                 # 子对话框（日期选择器）打开计数
        self._blur_grace_until = 0.0          # 这段时间内不做失焦判定
        self._submitted = False               # 内容是否已投递给 on_submit（防重复落库）
        self._hint_hidden = False             # 输入提示是否已被"点击输入框"收走
        self._away_strikes = 0                # 前台看门狗：连续几次判定"不在前台"
        self._had_foreground = False          # 打开后是否**曾经**拿到过前台
        self._host_bind = None                # 主窗口 <FocusOut> 的绑定 id（销毁时要摘）
        self._suspended = False               # 是否因"子对话框开着"临时收起
        self.close_reason = ""                # 关闭原因（谁第一个把它关掉的）
        self._chips: dict = {}
        self.group_id = group_id

        # ---- 开场先收起别的浮层（本轮改动）----
        # 提醒条 / 右键菜单 / 图标浮层都可能正开着，而它们都是独立顶层窗口。
        # 新任务浮层一旦弹出，它们要么盖在输入框上、要么跟新浮层抢焦点 ——
        # 尤其提醒条展开的明细有 6 行，正好压在浮层位置。
        # 这里是"让路"，不是"关闭"，所以不写任何取消语义。
        self._collapse_others()

        # ---- 分组列表与初始选中（需求 ⑤：动态读取，不硬编码）----
        # 显式传进来的 group_id（分组标题上的"＋"）优先，其次是用户设过的
        # "常用分组"，最后退到第一个分组 —— 与 add_quick_task 的兜底一致。
        self._groups = list(getattr(app.store, "groups", []) or [])
        if not group_id:
            default = app.store.settings.get("default_group")
            if any(g.id == default for g in self._groups):
                group_id = default
            elif self._groups:
                group_id = self._groups[0].id
        self.group_id = group_id

        self.withdraw()
        self.overrideredirect(True)
        # ❗不再无条件 -topmost True（1.5.1 修复）。
        # 原来浮层永远置顶，于是两个后果：①切到别的应用它仍挂在最上层；
        # ②它盖住了**自己弹出的日期选择器**（选择器只跟随主窗口的置顶设置）。
        # 现在改成"跟随主窗口的置顶设置"，与 dialogs._BaseDialog 口径一致。
        self.sync_topmost()
        try:
            self.attributes("-alpha", 0.0)
        except Exception:  # noqa: BLE001
            pass

        bg = theme.c("card")
        self.configure(bg=bg)

        # ---- 逻辑 → 物理（一次性换算）----
        self.SHADOW = theme.lpx(theme.POPUP_SHADOW)
        self.R = theme.lpx(theme.POPUP_RADIUS)
        self.W = theme.lpx(theme.POPUP_WIDTH) + self.SHADOW * 2
        self.H = theme.lpx(theme.POPUP_HEIGHT) + self.SHADOW * 2

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                width=self.W, height=self.H)
        self.canvas.pack(fill="both", expand=True)

        # 先建内容（此时还不知道最终尺寸），量出内容真正需要多大，再定浮层大小
        self._build_content()
        self._fit_size()
        self._draw_background()
        self._bind_events()
        # 提示默认可见（需求：一直显示，点了输入框才隐藏）。
        # 必须在 _fit_size 之后 —— place 定位依赖 entry 已经完成布局。
        self._sync_hint_label()

        # 销毁兜底：不管从哪条路径销毁（外部 destroy、解释器退出），
        # 都要清掉 after 队列并通知 app 摘掉引用，避免"幽灵浮层"。
        self.bind("<Destroy>", self._on_destroy, add="+")

        self._place_below(anchor)
        self.deiconify()
        self._fade_in(0)
        # 开场免失焦窗口（必需，不是保险）：
        # 弹出后的头 450ms 里焦点要在 主窗口 → 浮层 → 输入框 之间交接，
        # 期间一定会有 <FocusOut>。而失焦判定会延迟 POPUP_BLUR_GRACE(150ms)
        # 后复查 —— 那一拍焦点很可能还在"半路上"，于是刚开好的浮层被判成
        # "用户去看别处了"并立刻开始淡出（_closing=True）。
        # 后果不只是闪一下：此时用户敲的回车会走到 submit()，而 submit() 见到
        # _closing 直接 return，**任务静默丢失**（gui_smoke 偶发"提交后任务
        # 落库 6 -> 6"就是这条路径）。
        self._blur_grace_until = time.monotonic() + theme.POPUP_OPEN_GRACE / 1000.0
        self._after(30, self._focus_entry)
        # 前台看门狗在**宽限期设定之后**才起跑：否则它可能抢在
        # _blur_grace_until 生效前做第一轮判定，把刚弹出的浮层当场收掉。
        self._watch_foreground()
        self._log(f"opened anchor={getattr(anchor, 'winfo_class', lambda: '?')()} "
                  f"group={self.group_id or '-'} groups={len(self._groups)}")

    # ------------------------------------------------------------------
    # 开场让路：收起别的浮层
    # ------------------------------------------------------------------
    def _collapse_others(self) -> None:
        """把正开着的提醒条明细 / 右键菜单 / 图标浮层收起来。

        这三者都可能和新任务浮层同时存在，且都是**独立 overrideredirect 顶层
        窗口**，z-order 上谁也不服谁。最碍事的是提醒条：展开态有 6 行明细，
        高度正好落在浮层位置，用户点"＋ 新建任务"时输入框会被整块盖住。

        全部包在 try 里：任何一个收不掉都不该阻断浮层打开 —— 让路是尽力而为。
        """
        page = getattr(self.app, "page", None)
        try:
            banner = getattr(page, "banner", None)
            if banner is not None:
                banner.collapse()
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import menu as _menu
            _menu.close_active()
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import icon_picker
            icon_picker.close_active()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 置顶 / 前台（1.5.1 新增）
    # ------------------------------------------------------------------
    def sync_topmost(self) -> None:
        """浮层置顶状态**跟随主窗口设置**，不再无条件置顶。

        后果上的区别（用户报的两条都在这里）：
        * 不再无条件 ``-topmost``：切到别的应用时浮层不会赖在屏幕最上层；
        * 与 :class:`dialogs._BaseDialog` 同一口径 —— 日期选择器的 z-order
          不再被自己的父浮层压住（原来浮层恒置顶、选择器不置顶 → 选择器
          的下半截被浮层盖住，时间就没法选了）。
        """
        try:
            topmost = bool(self.app.store.settings.get("always_on_top", False))
            self.attributes("-topmost", topmost)
        except Exception:  # noqa: BLE001
            pass

    def _app_foreground(self) -> bool:
        """前台窗口是否属于本进程；探不到时保守返回 True（宁可不收，不可误收）。"""
        try:
            ours = bool(self.app.is_foreground())
        except Exception:  # noqa: BLE001
            return True
        if ours:
            self._had_foreground = True
        return ours

    def _left_to_other_app(self) -> bool:
        """用户是不是"离开到别的应用去了"（可以据此收起浮层）。

        ⚠️ 必须要求 ``_had_foreground``：全局快捷键（Ctrl+Shift+T）从别的应用
        唤起浮层时，Windows 的前台锁**可能拒绝**把前台交给我们 —— 那一拍
        "前台不是本进程"并不代表用户走了，只代表我们没抢到。按失焦处理就会
        当场误杀一个刚打开的浮层（比原来的 bug 更难查）。所以：
        先确认"我们拿到过前台"，之后的失去才算真的离开。
        """
        if self._app_foreground():
            return False
        return bool(self._had_foreground)

    # ------------------------------------------------------------------
    # 日志 / after 管理
    # ------------------------------------------------------------------
    def _log(self, message: str) -> None:
        try:
            self.app.store.log(f"{strings.LOG_TAG} {message}")
        except Exception:  # noqa: BLE001
            pass

    def _after(self, delay: int, func: Callable, *args):
        """登记 after job，销毁时统一取消（不留野回调）。

        任务跑完会把自己从册子上划掉 —— 否则 ``<FocusOut>`` 频繁触发时
        （打开日期选择器期间一次能攒出十几个）这个集合会一直涨，
        而里面绝大多数 id 早就执行完了，``after_cancel`` 它们纯属白干。
        """
        holder: dict = {}

        def run() -> None:
            self._jobs.discard(holder.get("id"))
            self._run_job(func, *args)

        job = self.after(delay, run)
        holder["id"] = job
        self._jobs.add(job)
        return job

    def _run_job(self, func: Callable, *args) -> None:
        """执行登记过的任务；控件已销毁时静默跳过。"""
        if self._closed:
            return
        try:
            func(*args)
        except tk.TclError:
            pass                     # 控件已销毁，属正常竞态
        except Exception as exc:  # noqa: BLE001
            self._log(f"job 失败 {getattr(func, '__name__', func)}: {exc}")

    def _cancel_jobs(self) -> None:
        for job in list(self._jobs):
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._jobs.clear()

    def _focus_entry(self) -> None:
        """把键盘焦点抢到输入框。

        为什么必须 ``focus_force()``，只用 ``focus_set()`` 不够（本轮真 bug）
        ------------------------------------------------------------------
        ``focus_set()`` 只在**本窗口已经是焦点窗口**时才生效；而浮层是
        ``overrideredirect`` 顶层窗口，Windows 未必把它激活。实测现象：
        第一次打开碰巧焦点落对（``focus_get()`` 是 entry），**第二次打开时
        ``focus_get()`` 返回 None** —— 于是 Esc / 回车全都无人处理，
        用户看到的就是"浮层关不掉、必须重启"。

        ``focus_force()`` 无条件抢焦点（``ui/menu.py`` 的菜单也是这么做的）。
        代价是它会抢走别的应用的焦点，但从托盘 / 全局快捷键唤起浮层的语义
        本来就是"我要立刻记一件事"，抢焦点是预期行为。

        另外根窗口的 Esc 还兜了一层（见 ``app._close_floating``），
        即使某些环境下抢焦点仍然失败，Esc 也一定能把浮层收掉。
        """
        if self._closed:
            return
        try:
            self.focus_force()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.entry.focus_set()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 尺寸：按内容的实测尺寸定，绝不裁掉分组（需求 ⑤）
    # ------------------------------------------------------------------
    def _content_pad(self) -> int:
        """内容区距画布边缘的内衬（物理像素，四边相同）。

        ``1`` 是画布留白、``POPUP_BORDER_W`` 是描边本身、``POPUP_BODY_INSET``
        是描边内侧到内容之间的呼吸位。四边必须**完全对称** —— 原来上下用的是
        "画布高 - SHADOW×2"，而卡片本体只到 ``H - SHADOW - 1``，内容比卡片
        多伸出去 1px，正好把下边框盖掉。
        """
        return (1 + theme.lpx(theme.POPUP_BORDER_W)
                + theme.lpx(theme.POPUP_BODY_INSET))

    def _content_box(self) -> Tuple[int, int, int, int]:
        """内容区矩形 ``(x1, y1, x2, y2)``（物理像素）。"""
        pad = self._content_pad()
        return pad, pad, self.W - pad, self.H - self.SHADOW - pad

    def _fit_size(self) -> None:
        """按"内容实际需要"决定浮层宽高。

        上一版是按"中文字数 × 13px"**估算** chips 行宽度，估偏了就把第三个
        分组直接裁掉（截图里只剩"工作/学习"，而 store 里明明有三个分组）。
        控件自己会算 ``reqwidth`` / ``reqheight``，没有理由再自己猜。

        宽度夹在 ``POPUP_WIDTH`` 与 ``POPUP_MAX_W`` 之间：
        下限保证输入行有足够空间；上限之外交给 :class:`GroupChipStrip` 横滚。
        高度同理取"设计值"与"内容实测值"的较大者 —— 一旦以后往浮层里再加一行，
        忘记同步 ``POPUP_HEIGHT`` 也不会把内容裁掉。
        """
        self._measure(quiet=False)
        strip_need = self.strip.needed_width() if hasattr(self, "strip") else 0
        row_need = self._row_req_width()
        content_w = max(theme.lpx(theme.POPUP_WIDTH),
                        strip_need + theme.lpx(theme.POPUP_RADIUS * 2),
                        row_need + theme.lpx(theme.POPUP_RADIUS * 2))
        # ❗上限有两个，取较小者：
        #   1. 设计上限 POPUP_MAX_W（= 主窗口最小宽 − 两侧安全间隙）；
        #   2. **主窗口当前实际宽度** − 安全间隙 —— 这条是防溢出的关键。
        #      只卡第 1 条不够：用户把窗口拖到比 MIN_WINDOW_W 还窄时（理论上
        #      拖不到，但最大化/还原与多屏切换会让 geometry 出现瞬时异常值），
        #      或者别处改了 MIN_WINDOW_W 而忘了同步 POPUP_MAX_W，
        #      浮层就会再次越出主窗口 —— 那正是用户报的"生活分组被裁"。
        avail = self._max_fit_width()
        content_w = min(content_w, theme.lpx(theme.POPUP_MAX_W), avail)
        self.W = int(content_w) + self.SHADOW * 2

        need_h = 0
        try:
            need_h = self.inner.winfo_reqheight()
        except Exception:  # noqa: BLE001
            pass
        self.H = (max(theme.lpx(theme.POPUP_HEIGHT), int(need_h),
                      theme.lpx(theme.POPUP_CHIP_H))
                  + self.SHADOW + self._content_pad() * 2)

        try:
            self.canvas.configure(width=self.W, height=self.H)
        except Exception:  # noqa: BLE001
            pass
        if hasattr(self, "strip"):
            # chips 的可用宽度 = 内容宽 - 左右内边距
            self.strip.fit(self.W - self.SHADOW * 2 - theme.lpx(theme.POPUP_RADIUS) * 2)
        self._sync_inner_size()

    def _max_fit_width(self) -> int:
        """浮层能占用的最大内容宽（**物理像素**），保证落在主窗口内。

        取值来源是主窗口的**客户区实际宽度**（物理像素）。探不到时退到
        ``POPUP_MAX_W``，绝不返回一个会越界的值。
        """
        try:
            host = self.app.winfo_width()
        except Exception:  # noqa: BLE001
            host = 0
        if host <= 1:
            return theme.lpx(theme.POPUP_MAX_W)
        gap = theme.lpx(theme.POPUP_EDGE_PAD) * 2
        return max(theme.lpx(theme.POPUP_WIDTH), host - gap)

    def _measure(self, quiet: bool = True) -> None:
        """让 Tk 把布局算完，这样 reqwidth/reqheight 才是真实值。"""
        try:
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            if not quiet:
                pass

    def _row_req_width(self) -> int:
        """输入行的自然宽度（物理像素）。"""
        try:
            self.row.update_idletasks()
            return self.row.winfo_reqwidth()
        except Exception:  # noqa: BLE001
            return 0

    def _sync_inner_size(self) -> None:
        if not hasattr(self, "inner"):
            return
        try:
            x1, y1, x2, y2 = self._content_box()
            self.canvas.coords(self._inner_win, x1, y1)
            self.canvas.itemconfigure(self._inner_win, width=max(1, x2 - x1),
                                      height=max(1, y2 - y1))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 绘制与布局
    # ------------------------------------------------------------------
    def _draw_background(self) -> None:
        """圆角卡片 + 投影 + **描边**（全部物理像素）。

        用 tag 统一清理：宽度会在 ``_fit_size`` 之后重画一次，
        逐条 delete 容易漏掉上一轮画的投影层，留下残影。

        1.5.9 —— 描边为什么重写
        -----------------------
        用户报"新建任务浮层的**底部边框没有画出来**、和背景融在一起"。
        两个原因叠在一起：

        1. **描边用 ``round_rect(outline=…)`` 画不出来**。``round_rect`` 是
           "两个矩形 + 四个圆"拼的，``outline`` 会把六条边**全部**描一遍，
           而最后画的椭圆填充又把下半部分的描边盖掉 —— 只剩顶部一条线。
           现在改成"外圈实心边框色 + 内压一块本体色"，并且整块走 PIL 超采样，
           圆角与描边都是连续边缘。
        2. **内容窗口把下边框盖住了**。内容窗口原来是 ``(SHADOW, SHADOW)``
           起、高 ``H - 2×SHADOW``，而卡片本体只到 ``H - SHADOW - 1`` ——
           内容比卡片**多伸出去 1px**，右下角还露出一小块白方块。
           现在内容区由 :meth:`_content_box` 统一给出：四边内衬完全对称，
           下边缘与卡片本体之间留出 ``POPUP_BODY_INSET``。
        """
        self.canvas.delete("chrome")
        shadow = theme.c("shadow")
        bg = theme.c("card")
        page = theme.c("bg")
        # 描边色：用专用的浮层描边（比通用 border 更深一点，才压得住投影）
        try:
            idx = 1 if theme.is_dark() else 0
            border = theme.POPUP_BORDER[idx]
        except Exception:  # noqa: BLE001
            border = theme.c("border")

        inner_t = theme.POPUP_SHADOW_TINT
        outer_t = theme.POPUP_SHADOW_TINT_OUT
        span = max(1, self.SHADOW - 1)
        for i in range(self.SHADOW):
            t = i / span if span else 0.0
            off = theme.lpx(i + 1)
            # 内层最深、外层最浅 —— 渐隐到几乎看不见，避免出现硬边
            color = theme.mix(shadow, page, inner_t + (outer_t - inner_t) * t)
            widgets.round_rect(
                self.canvas, theme.lpx(2), off,
                self.W - theme.lpx(2), self.H - theme.lpx(2) + off,
                self.R, fill=color, outline="", tags=("chrome",))
        # 卡片本体（最后画，天然盖在投影上）：描边 + 圆角一次性由 PIL 出图
        from PIL import ImageTk

        body = widgets.aa_round_rect(
            (self.W - 2, self.H - self.SHADOW - 2), self.R, bg,
            border=border, border_w=theme.lpx(theme.POPUP_BORDER_W))
        self._body_photo = ImageTk.PhotoImage(body)
        self.canvas.create_image(1, 1, anchor="nw", image=self._body_photo,
                                 tags=("chrome",))

    def _build_content(self) -> None:
        """输入行 + 分组标签条，嵌在 Canvas 的一个 window 里。"""
        inner = ctk.CTkFrame(self, fg_color=theme.pair("card"))
        inner.grid_columnconfigure(0, weight=1)
        self.inner = inner

        # ---------------- 第一行：输入 + 截止日期 + 提交 ----------------
        # 本轮改动①：输入框**常驻**在浮层顶部，不再依赖点分组标签才出现。
        # 它一直是第一行、一直在 → 打开浮层就能直接打字（见 _focus_entry）。
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew",
                 padx=(theme.POPUP_RADIUS, theme.POPUP_RADIUS),
                 pady=(theme.POPUP_ROW_GAP, 0))
        row.grid_columnconfigure(0, weight=1)
        self.row = row

        # ❗**不要用 CTkEntry 的 placeholder_text**：它的实现在收到 <FocusIn>
        # 时就把占位文本抹掉（`_activate_placeholder` 绑在 FocusIn 上）。而浮层
        # 打开后 30ms 就会 `_focus_entry()` 自动聚焦 → 占位文字**一弹出来就没了**，
        # 只有手动点走再点回来才偶尔看到（用户报的"某些场景长按才触发"就是这个）。
        #
        # 需求：默认一直显示，**用户点了输入框才隐藏**。所以这里自己画一个提示，
        # 生命周期由我们控（见 _sync_hint_label / _on_entry_click）。
        self.entry = ctk.CTkEntry(
            row, border_width=0,
            fg_color="transparent", font=theme.font("body"),   # body = 13px
            text_color=theme.pair("text"),
            height=theme.POPUP_ENTRY_H,
        )
        self.entry.grid(row=0, column=0, sticky="ew")
        # 提示文字盖在输入框左上角：它是 entry 的**兄弟控件**，用 place 定位
        # （不能用 grid —— entry 占满第 0 列，grid 会把它挤到旁边）。
        self.hint_label = ctk.CTkLabel(
            row, text=strings.NEW_TASK_HINT, anchor="w",
            font=theme.font("body"), text_color=theme.pair("text_done"),
        )
        self.hint_label.place(in_=self.entry, x=theme.lpx(2), rely=0.5,
                              anchor="w")
        # 点提示 = 点输入框（提示会挡住点击，必须显式转发焦点 + 触发隐藏）
        for w in (self.hint_label, getattr(self.hint_label, "_canvas", None)):
            if w is not None:
                w.bind("<Button-1>", self._on_entry_click, add="+")
        # ❗只有真点击（ButtonPress）才隐藏提示；<FocusIn> 不能隐藏 ——
        # 浮层打开会自动聚焦，那样提示一弹出来就没了。FocusIn 只做状态对齐。
        self.entry.bind("<Button-1>", self._on_entry_click, add="+")
        self.entry.bind("<FocusIn>", self._sync_hint_label, add="+")
        # 真正输入了内容才让提示彻底消失（清空后回来）
        self.entry.bind("<KeyRelease>", self._sync_hint_label, add="+")
        self.entry.bind("<Return>", lambda _e: self.submit())
        self.entry.bind("<KP_Enter>", lambda _e: self.submit())
        # 光标在行首/行尾时，左右方向键切换分组（需求 23），
        # 光标在文字中间时仍然是正常的移动光标 —— 不牺牲文本编辑手感。
        self.entry.bind("<Left>", self._on_entry_left)
        self.entry.bind("<Right>", self._on_entry_right)

        # 已选截止日期的小标签（默认不占位，选了才显示）
        self.due_label = ctk.CTkLabel(row, text="", font=theme.font("tiny"),
                                      text_color=theme.pair("orange"))
        self.due_label.grid(row=0, column=1)
        self.due_label.grid_remove()

        self.cal_btn = widgets.IconButton(
            row, text="", icon="calendar", size=theme.POPUP_ENTRY_H,
            command=self._pick_due,
        )
        self.cal_btn.grid(row=0, column=2, padx=1)

        # ---------------- 第二行：分组标签条（需求 ⑤）----------------
        holder = ctk.CTkFrame(inner, fg_color="transparent")
        holder.grid(row=1, column=0, sticky="ew",
                    padx=(theme.POPUP_RADIUS, theme.POPUP_RADIUS),
                    pady=(theme.POPUP_ROW_GAP, 0))
        self.strip = GroupChipStrip(holder, height=theme.POPUP_CHIP_H,
                                    bg=theme.c("card"))
        self.strip.pack(side="left")
        self._build_chips()

        # ---------------- 第三行：确认 / 取消（本轮新增）----------------
        # 输入框回车、点"确认"、Esc、点"取消" 四条路都能结束这次输入。
        # 保留回车是为了"一次点击 + 一次回车"的快速录入手感，
        # 补上按钮是为了让不熟悉回车的人也能提交。
        # 1.5.12：行首加常驻快捷键提示 —— Enter/Esc 此前没有任何可见提示，
        # 只能靠猜；辅助色 10px 小字，不与按钮抢注意力。
        actions = ctk.CTkFrame(inner, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew",
                     padx=(theme.POPUP_RADIUS, theme.POPUP_RADIUS),
                     pady=(theme.POPUP_ROW_GAP, theme.POPUP_ROW_GAP))
        widgets.TextLabel(actions, text=strings.QUICK_ADD_KEYS_HINT,
                          font=theme.font("tiny"),
                          text_color=theme.pair("text_done"), anchor="w").pack(
            side="left", padx=(theme.lpx(2), 0))
        self.cancel_btn = ctk.CTkButton(
            actions, text="取消", width=theme.POPUP_BTN_W,
            height=theme.POPUP_BTN_H, corner_radius=theme.POPUP_BTN_H // 2,
            fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text_muted"), font=theme.font("small"),
            command=lambda: self.close(reason="cancel"),
        )
        self.cancel_btn.pack(side="right", padx=(0, 0))
        self.ok_btn = ctk.CTkButton(
            actions, text="确认", width=theme.POPUP_BTN_W,
            height=theme.POPUP_BTN_H, corner_radius=theme.POPUP_BTN_H // 2,
            fg_color=theme.pair("orange"), hover_color=theme.pair("orange_hover"),
            text_color=theme.POPUP_ON_ORANGE, font=theme.font("small"),
            command=self.submit,
        )
        self.ok_btn.pack(side="right", padx=(theme.POPUP_CHIP_GAP, 0))

        # 嵌入 Canvas：铺满卡片**内容区**（描边与投影都留在外面，不被盖住）
        pad = self._content_pad()
        self._inner_win = self.canvas.create_window(
            pad, pad, anchor="nw", window=inner,
            width=max(1, self.W - pad * 2),
            height=max(1, self.H - self.SHADOW - pad * 2),
        )
        self._sync_inner_size()
        self._place_holder = holder

    def _build_chips(self) -> None:
        """从 store 动态生成分组 chip：分组图标 + 名称。"""
        strip = self.strip.inner
        ctk.CTkLabel(strip, text="分组", font=theme.font("tiny"),
                     text_color=theme.pair("text_done")).pack(
            side="left", padx=(2, theme.POPUP_CHIP_GAP))
        for g in self._groups:
            chip = ctk.CTkButton(
                strip, text=f" {g.name}", height=theme.POPUP_CHIP_H,
                # ❗必须显式给 width，否则 CTkButton 会退回它的**默认宽度 140**。
                # 实测：不给 width 时每个 chip 都是 210 物理宽（=140 逻辑），
                # 三个分组就要 705 物理，而可视区只有 516 —— 于是"生活"被推出
                # 视野、必须横滚才能看到（用户报的"生活分组未完全显示"）。
                # 这里按"图标 + 2 个汉字 + 左右内边距"给一个贴合内容的值，
                # 再由 Tk 的 pack 自然排布，三个分组在 364 逻辑宽内装得下。
                width=self._chip_width(g.name),
                corner_radius=theme.POPUP_CHIP_H // 2,
                fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                text_color=theme.pair("text_muted"),
                font=theme.font(theme.POPUP_CHIP_FONT),      # meta = 12px
                image=self._chip_icon(g, selected=False),
                compound="left", anchor="center",
                command=lambda gid=g.id: self._select_group(gid),
            )
            chip.pack(side="left", padx=(0, theme.POPUP_CHIP_GAP))
            self._chips[g.id] = chip
        self._sync_chips()

    @staticmethod
    def _chip_width(name: str) -> int:
        """chip 的贴合宽度（**逻辑像素**，CTkButton 自己会乘缩放）。

        口径 = 左右内边距 + 图标 + 间隙 + 文字宽（中文按字号计，英文按 0.55 折）。
        宁可略宽一点也不要窄：CTkButton 文字超宽会被截断，而 chip 上截断的
        分组名比"多两像素"难看得多。
        """
        pad = theme.POPUP_CHIP_GAP * 2 + 4
        icon = theme.POPUP_CHIP_ICON
        gap = 4
        base = theme.font_size(theme.POPUP_CHIP_FONT)
        text = 0.0
        for ch in name.strip():
            text += base if ord(ch) > 0x2E80 else base * 0.55
        # chip 的文本前面有一个空格（把图标和文字推开），宽度也要算进去
        text += base * 0.35
        return int(pad + icon + gap + text) + 1

    def _chip_icon(self, group, selected: bool):
        """chip 上的分组图标：选中态用白色（柔橘底上原色图标几乎看不清）。

        图标来源走 ``store.group_icon()`` → ``icons.resolve_key()``，
        和历史 emoji 数据兼容。
        """
        try:
            raw = self.app.store.group_icon(group.id) or getattr(group, "icon", "")
            key = icons.resolve_key(raw) or icons.DEFAULT_GROUP_ICON
            color = "#FFFFFF" if selected else ""
            if color:
                return icons.get_ctk_tinted(key, theme.POPUP_CHIP_ICON, color)
            return icons.get_ctk(key, theme.POPUP_CHIP_ICON)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # 分组选择（需求 ⑤）
    # ------------------------------------------------------------------
    def _select_group(self, group_id: str) -> None:
        """点选分组 chip：只改选中态，焦点交回输入框，随时可以继续敲字。"""
        if self._closed:
            return
        self.group_id = group_id
        self._sync_chips()
        self._focus_entry()

    def _cycle_group(self, delta: int) -> None:
        """键盘左右切换分组。"""
        ids = [g.id for g in self._groups]
        if not ids:
            return
        try:
            index = ids.index(self.group_id)
        except ValueError:
            index = 0
        self._select_group(ids[(index + delta) % len(ids)])

    def _on_entry_left(self, _event):
        """光标已在行首时，左方向键切换上一个分组。"""
        try:
            if self.entry.index("insert") == 0:
                self._cycle_group(-1)
                return "break"
        except Exception:  # noqa: BLE001
            pass
        return None
    def _on_entry_right(self, _event):
        """光标已在行尾时，右方向键切换下一个分组。"""
        try:
            if self.entry.index("insert") >= len(self.entry.get()):
                self._cycle_group(1)
                return "break"
        except Exception:  # noqa: BLE001
            pass
        return None

    # ------------------------------------------------------------------
    # 输入提示（"想做点什么？"）
    # ------------------------------------------------------------------
    def _on_entry_click(self, _event=None):
        """用户**真的点了**输入框 → 提示让位。

        需求原文：「只有当用户点击该输入框时才隐藏」。所以判据是"鼠标点击"。

        为什么另开一个方法而不是复用 ``_sync_hint_label``：浮层打开后 30ms 会
        ``_focus_entry()`` 自动抢焦点（那是 Esc / 回车能工作的前提，不能去掉）。
        如果按 <FocusIn> 判定，自动聚焦会立刻把提示收走 —— 又回到用户报的
        "提示基本看不见"。所以绑定是分开的：``<Button-1>`` → 这里（隐藏），
        ``<FocusIn>`` → ``_sync_hint_label``（只对齐状态，不主动关提示）。
        """
        self._hide_hint()
        return None

    def _hide_hint(self) -> None:
        lbl = getattr(self, "hint_label", None)
        if lbl is None:
            return
        try:
            if getattr(self, "_hint_hidden", False):
                return
            self._hint_hidden = True
            lbl.place_forget()
        except Exception:  # noqa: BLE001
            pass

    def _sync_hint_label(self, _event=None) -> None:
        """把提示的显隐同步到当前实际状态（幂等）。

        显示条件：**没点过输入框** 且 **还没输入内容**。
        这条既覆盖"默认一直显示"，也覆盖"用户点了就隐藏"，
        并且用户清空文字后不会把提示弹回来（他已经点过框了，那里有插入符）。
        """
        lbl = getattr(self, "hint_label", None)
        if lbl is None:
            return
        text = ""
        try:
            text = self.entry.get()
        except Exception:  # noqa: BLE001
            pass
        want = (not text) and (not getattr(self, "_hint_hidden", False))
        try:
            if want:
                lbl.place(in_=self.entry, x=theme.lpx(2), rely=0.5, anchor="w")
            else:
                lbl.place_forget()
        except Exception:  # noqa: BLE001
            pass

    def _sync_chips(self) -> None:
        for gid, chip in self._chips.items():
            selected = (gid == self.group_id)
            group = next((g for g in self._groups if g.id == gid), None)
            try:
                chip.configure(
                    image=self._chip_icon(group, selected),
                    fg_color=theme.pair("orange") if selected else theme.pair("ghost"),
                    hover_color=theme.pair("orange_hover") if selected
                    else theme.pair("ghost_hover"),
                    text_color=(theme.POPUP_ON_ORANGE if selected
                                else theme.pair("text_muted")),
                )
            except Exception:  # noqa: BLE001
                pass

    def _bind_events(self) -> None:
        self.bind("<Escape>", lambda _e: self.close(reason="esc"))
        self.bind("<FocusOut>", self._on_focus_out)
        # 标签条上的左右键（焦点在 chip 上时）
        self.bind("<Left>", lambda _e: self._cycle_group(-1))
        self.bind("<Right>", lambda _e: self._cycle_group(1))
        # 主窗口失焦也算一次"可能离开了"（需求：绑定主窗口 FocusOut）。
        # 单靠浮层自己的 <FocusOut> 不够：浮层是 overrideredirect 顶层窗口，
        # "切到别的应用"这一拍事件未必送到它。主窗口是普通窗口，事件可靠得多。
        # 记下 funcid，销毁时精确摘掉 —— 用 add="+" 不能靠 unbind 全清，
        # 那会把别处绑在同名事件上的回调一起摘掉。
        try:
            self._host_bind = self.app.bind("<FocusOut>", self._on_host_focus_out,
                                            add="+")
        except Exception:  # noqa: BLE001
            self._host_bind = None

    # ------------------------------------------------------------------
    # 位置：锚点按钮下方，放不下就翻上去
    # ------------------------------------------------------------------
    def _place_below(self, anchor: tk.Misc) -> None:
        try:
            ax = anchor.winfo_rootx()
            ay = anchor.winfo_rooty() + anchor.winfo_height()
            aw = anchor.winfo_width()
        except Exception:  # noqa: BLE001
            ax = ay = aw = 0
        gap = theme.lpx(theme.POPUP_GAP)
        # 屏幕边界用 vroot 系列：它们和 winfo_rootx 同源同单位（物理像素）。
        # 用 winfo_screenwidth() 会在高 DPI 下拿到逻辑宽，去钳物理坐标必然钳错。
        try:
            screen_w = self.winfo_vrootwidth() or self.winfo_screenwidth()
            screen_h = self.winfo_vrootheight() or self.winfo_screenheight()
        except Exception:  # noqa: BLE001
            screen_w, screen_h = 1920, 1080

        edge = theme.lpx(theme.POPUP_EDGE_PAD)

        # ❗主窗口边界是**硬约束**，屏幕边界只是兜底。
        # 浮层是从主窗口里的按钮弹出的，用户的预期是"它属于这个窗口" ——
        # 只要它有一部分落在主窗口外，就是本轮的 bug（"生活"分组被裁）。
        # 两级钳制：先按主窗口客户区钳，再按屏幕钳（防主窗口本身超出屏幕）。
        win_l = win_t = win_r = win_b = None
        try:
            win_l = self.app.winfo_rootx()
            win_t = self.app.winfo_rooty()
            win_r = win_l + self.app.winfo_width()
            win_b = win_t + self.app.winfo_height()
        except Exception:  # noqa: BLE001
            pass

        x = ax
        if x + self.W > screen_w - edge:
            x = ax + aw - self.W          # 右边贴不下就右对齐到锚点右缘
        x = max(edge, min(x, screen_w - self.W - edge))
        if win_l is not None and win_r - win_l > self.W:
            # 主窗口够宽：把浮层收进窗口左右边界内
            x = max(win_l + edge, min(x, win_r - self.W - edge))

        y = ay + gap
        if y + self.H > screen_h - edge:
            try:
                y = anchor.winfo_rooty() - self.H - gap
            except Exception:  # noqa: BLE001
                y = screen_h - self.H - edge
        y = max(edge, y)
        if win_t is not None and win_b - win_t > self.H:
            # 主窗口够高：优先让它落在窗口内（下缘贴窗口底也要完整可见）
            y = max(win_t + edge, min(y, win_b - self.H - edge))
        self.geometry(f"{self.W}x{self.H}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------------
    # 失焦 / 关闭（本轮重写的核心）
    # ------------------------------------------------------------------
    def _on_focus_out(self, _event) -> None:
        # 延迟一拍：焦点此刻可能正在"离开浮层 去子控件/子对话框"的路上
        if self._closed or self._closing:
            return
        self._after(theme.POPUP_BLUR_GRACE, self._check_focus)

    def _on_host_focus_out(self, _event=None) -> None:
        """主窗口失去焦点时也走一次失焦判定（1.5.1 新增）。

        ``<FocusOut>`` 是 add="+" 绑在主窗口上的，所以**主窗口内部**的焦点
        转移（点任务列表、点输入框）也会触发它 —— 那没关系：判定函数会先看
        焦点是不是还在浮层里，是就什么都不做。真正要抓的是"焦点交到了别的
        应用手里"，那一拍浮层自己的事件可能根本不来。
        """
        if self._closed or self._closing:
            return
        # 先问一次系统：焦点已经交给别的**应用**时，这一步立刻就能定性，
        # 不必再等宽限期（宽限期只是给"焦点在自家窗口之间转场"留的）。
        self._after(theme.POPUP_BLUR_GRACE, self._check_focus)

    def _watch_foreground(self) -> None:
        """前台看门狗：切走就收（1.5.1 新增，修"浮层赖在别的应用最上层"）。

        与 ``<FocusOut>`` 事件并行的一道**轮询兜底**。为什么两道都要：
        事件那条在正常情况下更快（150ms 就判定完），但它对 overrideredirect
        顶层窗口不是 100% 可靠；轮询这条慢一点（最多 2×300ms≈600ms）却与
        事件无关，一定能收掉。

        排除项（不能收）：
        * ``_modal_count > 0`` —— 日期选择器开着，浮层已临时 withdraw；
        * 宽限期内 —— 开场焦点交接、对话框刚关闭；
        * 浮层已经被收起（``_suspended``）或正在关闭。
        """
        if self._closed or self._closing:
            return
        now = time.monotonic()
        if self._modal_count > 0 or self._suspended or now < self._blur_grace_until:
            self._away_strikes = 0
        elif self._left_to_other_app():
            self._away_strikes += 1
            if self._away_strikes >= theme.POPUP_WATCH_STRIKES:
                self.close(reason="blur")
                return
        else:
            self._away_strikes = 0
        self._after(theme.POPUP_WATCH_MS, self._watch_foreground)

    def _check_focus(self) -> None:
        """失焦判定：焦点不在浮层内就收（模态对话框与宽限期除外）。

        为什么不能只看 ``<FocusOut>`` 就立刻关：焦点在"浮层 → 自己的子控件 →
        日期选择器"之间跳转时会连着发好几拍 FocusOut，立刻关会误杀。
        所以延迟 ``POPUP_BLUR_GRACE`` 再判断，且判断时先看两件事：

        1. ``_modal_count`` —— 日期选择器还开着，明确不能收；
        2. ``_blur_grace_until`` —— 对话框**刚刚**关闭，焦点正在被
           ``_BaseDialog._finish()`` 强制还给主窗口。这一拍焦点必然不在浮层上，
           没有宽限期就会"点确定顺带把浮层也收掉"。

        关掉这两个口子之后，``FocusOut`` 就是权威信号：焦点离开 = 用户去看别处了。
        这里**不**再看指针位置 —— 早期版本用"指针还在浮层上就不收"来保护上面
        第 2 种情况，结果指针恰好压在浮层上时（比如 Alt+Tab 切走）浮层永远不关，
        反而变成另一种残留。宽限期是确定性方案，指针判定不是。
        """
        if self._closed or self._closing:
            return
        if self._modal_count > 0:
            return
        now = time.monotonic()
        if now < self._blur_grace_until:
            # 宽限期内不判定，但**必须排一次"宽限期结束后的复查"**：
            # 否则"恰好在宽限期内失焦"（比如点完日期选择器的确定、紧接着点主窗口）
            # 这一次判定被跳过之后就再也没人来判了 —— 浮层会一直留着，
            # 而那正是用户报的"浮层残留"。
            self._after(int((self._blur_grace_until - now) * 1000) + 80,
                        self._check_focus)
            return
        # 权威判据（1.5.1）：前台窗口已经不属于本进程，说明用户在别的应用里。
        # 这一条**先于** focus_inside 判断 —— 事件可能压根没送到浮层
        # （用户报的"Alt+Tab 走了、浮层还挂在最上层"就是这条），而这里
        # 是直接问系统，不依赖任何事件是否投递成功。
        ours = self._app_foreground()
        if not ours:
            if self._had_foreground:
                self.close(reason="blur")
            # 从没拿到过前台（全局快捷键唤起被前台锁拒绝）：不判，
            # 免得误杀一个刚打开的浮层 —— 那种情况交给 Esc / 点外部。
            return
        if self._focus_inside():
            return
        self.close(reason="blur")

    def _focus_inside(self) -> bool:
        try:
            widget = self.focus_displayof()
        except Exception:  # noqa: BLE001
            widget = None
        if widget is None:
            return False
        cur = widget
        while cur is not None:
            if cur is self:
                return True
            try:
                cur = cur.master
            except Exception:  # noqa: BLE001
                break
        return False

    def pointer_inside(self) -> bool:
        """指针是否落在浮层（含安全热区）内。供测试断言"热区判定"用。"""
        try:
            x, y = self.winfo_pointerxy()
            left, top = self.winfo_rootx(), self.winfo_rooty()
        except Exception:  # noqa: BLE001
            return False
        zone = theme.lpx(theme.POPUP_SAFE_ZONE)
        return (left - zone <= x <= left + self.W + zone
                and top - zone <= y <= top + self.H + zone)

    # ------------------------------------------------------------------
    def close(self, reason: str = "api", animate: bool = True) -> None:
        """幂等关闭。任何入口（提交/Esc/失焦/点外部/换新浮层）都走这里。

        ``reason`` 只用于日志，排查"浮层为什么没了/为什么还在"时非常省事。
        """
        if self._closed or self._closing:
            return
        self._closing = True
        self.close_reason = reason or "api"    # 只记第一条（后续幂等调用不改）
        self._log(f"cancelled reason={reason}")
        self._cancel_jobs()
        # 先释放可能的 grab，否则 destroy 会被 Windows 上的模态状态拖住
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        if animate and self._can_animate():
            self._fade_out(0, lambda: self._destroy_now(reason))
        else:
            self._destroy_now(reason)

    def _can_animate(self) -> bool:
        if not self.winfo_exists():
            return False
        try:
            return float(self.attributes("-alpha")) > 0.05
        except Exception:  # noqa: BLE001
            return False

    def _destroy_now(self, reason: str = "") -> None:
        """真正的销毁：**先 withdraw 再 destroy**。

        顺序很重要。``destroy()`` 在个别情况下会抛 ``TclError``（被 grab 的
        子对话框牵制、子控件正被回调持有等）。上一版先置 ``_closing`` 再 destroy，
        一旦抛异常就再也没机会重试 —— 浮层永久留在屏幕上。
        现在即使 destroy 失败，也已经 ``withdraw()`` 过：用户看不见，
        而且会排一次重试，最坏情况也只是多一个隐藏顶层窗口。
        """
        if self._closed:
            return
        self._cancel_jobs()
        try:
            self.withdraw()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except Exception as exc:  # noqa: BLE001
            self._log(f"destroy 失败（{reason}）：{exc}，排一次重试")
            try:
                self.after(200, self._retry_destroy)
                return
            except Exception:  # noqa: BLE001
                pass
        self._closed = True
        self._log("destroyed")

    def _retry_destroy(self) -> None:
        try:
            self.destroy()
        except Exception as exc:  # noqa: BLE001
            self._log(f"destroy 重试仍失败：{exc}")
        self._closed = True

    def _on_destroy(self, event=None) -> None:
        """``<Destroy>`` 兜底：无论谁销毁我们，都要清理干净。

        ``<Destroy>`` 会对**每个子控件**都触发一次，必须用 ``event.widget is self``
        过滤，否则第一次子控件销毁就把状态清了。
        """
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self._closed = True
        self._closing = True
        self._cancel_jobs()
        # 摘掉绑在主窗口上的 <FocusOut>：不摘的话每次开浮层都会在 app 上
        # 多留一个回调，几十次之后主窗口一失焦就要跑一长串废弃回调。
        if self._host_bind is not None:
            try:
                self.app.unbind("<FocusOut>", self._host_bind)
            except Exception:  # noqa: BLE001
                pass
            self._host_bind = None
        # 通知 app 摘掉引用，避免它拿着一个已销毁的对象继续调用
        try:
            if getattr(self.app, "_quick_popup", None) is self:
                self.app._quick_popup = None
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 子对话框让位（1.5.1 新增：修"浮层盖住日期/时间选择器"）
    # ------------------------------------------------------------------
    def _suspend_for_modal(self) -> None:
        """子对话框（日期选择器）弹出前把浮层收起来。

        为什么直接收起而不是"往下挪一点"：小窗模式下（344×470，下限 310×380）
        浮层本身约 90 高、选择器约 430 高，两者都要完整落在窗口里时纵向根本
        排不开 —— 挪到哪都会压住一块。而选择器是**模态**的，用户此刻只可能
        在跟它交互，所以"父浮层临时让位"是唯一永远不会遮挡的解，
        也省掉了再算一遍几何的麻烦。

        用 withdraw 而不是 close：输入的文字、已选分组/日期都要留着，
        选择器关掉后原样回来，用户不必重打一遍。
        """
        if self._closed or self._suspended:
            return
        self._suspended = True
        self._away_strikes = 0
        try:
            self.withdraw()
        except Exception:  # noqa: BLE001
            pass
        self._log("suspended (modal open)")

    def _resume_after_modal(self) -> None:
        """选择器关闭后把浮层原样恢复，并把焦点收回输入框。"""
        if self._closed or not self._suspended:
            return
        self._suspended = False
        try:
            self.sync_topmost()
            self.deiconify()
            self.lift()
        except Exception:  # noqa: BLE001
            pass
        # 恢复后同样要给一段免失焦窗口：焦点要在 选择器 → 主窗口 → 浮层 →
        # 输入框 之间再交接一轮，紧跟着的失焦判定必然看到"焦点不在浮层上"，
        # 没有这段宽限期就会"选完日期浮层自己没了"。
        self._blur_grace_until = time.monotonic() + 0.5
        self._away_strikes = 0
        self._log("resumed (modal closed)")

    # ------------------------------------------------------------------
    # 截止日期
    # ------------------------------------------------------------------
    def _pick_due(self) -> None:
        from .due_picker import DuePickerDialog

        if self._closed:
            return
        self._modal_count += 1
        released = False
        # 先把浮层收起来再开选择器：不这样做的话，浮层会盖住选择器的
        # 「时间」那一行和底部按钮（选择器按窗口居中，正好落在浮层区域）。
        self._suspend_for_modal()

        def release() -> None:
            """归还模态计数 + 恢复浮层 + 把焦点还给输入框。

            三件事都必要：

            * 归还计数 —— 否则"点外部收起"这条路径永久失效（上一版的真 bug）；
            * 恢复浮层 —— 与 _suspend_for_modal 配对，且必须在这里做：
              取消/X/Esc 三条关闭路径都不会走 on_save；
            * **把焦点抢回来** —— 用户关掉日期选择器之后还在"写这条任务"的
              上下文里，焦点理应回到输入框。不抢的话焦点停在主窗口上，
              浮层既收不起来（没人给它发 FocusOut）也打不了字，
              就成了"卡在半空中的框"。
            """
            nonlocal released
            if released:
                return
            released = True
            self._modal_count = max(0, self._modal_count - 1)
            self._resume_after_modal()
            self._blur_grace_until = time.monotonic() + 0.6
            self._after(90, self._focus_entry)

        def on_save(when: Optional[_dt.datetime],
                    remind: Optional[int] = None) -> None:
            release()
            if self._closed:
                return
            self._due = when
            self._remind = remind
            self._sync_due_label()
            try:
                self.lift()
            except Exception:  # noqa: BLE001
                pass
            self._focus_entry()

        dialog = None
        try:
            dialog = DuePickerDialog(self.app, self._due, on_save,
                                     remind=self._remind)
        except Exception as exc:  # noqa: BLE001
            self._log(f"日期选择器打开失败：{exc}")
            release()
            return
        # 关键：**取消/关闭**这条路径不会调 on_save，必须在销毁事件上兜一层。
        # 上一版就是漏了这一步，_child_modal 永远停在 True，浮层再也收不起来。
        try:
            dialog.bind("<Destroy>",
                        lambda e: release() if getattr(e, "widget", None) is dialog else None,
                        add="+")
        except Exception:  # noqa: BLE001
            release()

    # ------------------------------------------------------------------
    # 截止日期标签（日期 + 提醒）
    # ------------------------------------------------------------------
    def _sync_due_label(self) -> None:
        """把已选日期与提醒一起显示在输入行右侧；没选日期就整块收起。

        带上提醒文案是必要的：提醒在**弹窗里**选，关掉弹窗后如果输入行只显示
        日期，用户根本看不出"这条到底会不会提醒、提前多久"。
        """
        try:
            if self._due is None:
                self.due_label.configure(text="")
                self.due_label.grid_remove()
                return
            tail = "" if self._remind is None else f" · {remind_label(self._remind)}"
            self.due_label.configure(text=self._due.strftime(_DUE_FMT) + tail)
            self.due_label.grid()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 提交
    # ------------------------------------------------------------------
    def submit(self) -> None:
        """提交：把输入框内容交给 ``on_submit``，并收起浮层。

        两条硬约束（都是真 bug 换来的）
        ------------------------------
        1. **``_closing`` 不等于"用户放弃了"**。它只说明浮层正在淡出，可能是
           失焦、也可能是上一次提交。只要框里有内容语义，用户敲回车就是要保存。
           上一版这里是 ``if self._closed or self._closing: return`` ——
           于是"浮层因偶发失焦开始淡出"与"用户正好按回车"撞在同一拍时，
           任务被**静默丢弃**（gui_smoke 偶发的"提交后任务落库 6 -> 6"）。
        2. **同一份内容只投递一次**（``_submitted`` 标志）。回车连按、或
           "回车 + 失焦"同时到达时不能落出两条任务。

        空正文的处理（本轮改动）
        ------------------------
        以前是 `if not title: focus_entry(); return` —— 敲回车"没反应"，
        用户不知道是没提交成功、还是自己没按到。现在改成：**照样记下来**，
        正文兜底成"（未命名）"，并通过 toast 明确告知。
        代价是可能多出一条空任务，但那比"回车无反应"好排查得多 ——
        卡片上的"（未命名）"一眼就能看见，随手删掉即可。
        """
        if self._closed or self._submitted:
            return
        try:
            raw = self.entry.get()
        except tk.TclError:
            return
        title = (raw or "").strip()
        auto_named = not title
        if auto_named:
            title = strings.UNNAMED_TITLE

        self._submitted = True
        due = self._due
        gid = self.group_id
        remind = self._remind
        self._log(f"submitted group={gid or '-'} length={len(title)}"
                  f"{' auto_named' if auto_named else ''}")
        # 先收起浮层（幂等）再投递：回调里通常会 render() 整页，
        # 浮层留着会盖在新内容上。
        self.close(reason="submitted")
        if auto_named:
            self._toast(strings.EMPTY_SUBMIT_TOAST)
        callback = self.on_submit
        if callback is None:
            self._log("提交回调为空，内容未落库")
            return
        try:
            callback(title, gid, due, remind)
        except Exception as exc:  # noqa: BLE001
            self._log(f"提交回调失败：{exc}")

    def _toast(self, message: str) -> None:
        """轻提示。失败就只记日志 —— 提示本身不该再制造一个错误。"""
        try:
            self.app.toast(message)
        except Exception as exc:  # noqa: BLE001
            self._log(f"toast 失败：{exc}")

    # ------------------------------------------------------------------
    # 淡入 / 淡出
    # ------------------------------------------------------------------
    def _fade_in(self, i: int) -> None:
        if self._closed or self._closing or not self.winfo_exists():
            return
        try:
            self.attributes("-alpha", min(1.0, (i + 1) / FADE_STEPS))
        except Exception:  # noqa: BLE001
            return
        if i + 1 < FADE_STEPS:
            self._after(FADE_INTERVAL, self._fade_in, i + 1)

    def _fade_out(self, i: int, on_done: Callable[[], None]) -> None:
        """淡出到不可见再销毁（需求 ①：withdraw 做淡出，动画结束再 destroy）。"""
        steps = theme.POPUP_FADE_OUT
        if self._closed or not self.winfo_exists():
            on_done()
            return
        try:
            self.attributes("-alpha", max(0.0, 1.0 - (i + 1) / steps))
        except Exception:  # noqa: BLE001
            on_done()
            return
        if i + 1 < steps:
            self._after(theme.POPUP_FADE_OUT_INTERVAL, self._fade_out, i + 1, on_done)
        else:
            on_done()

    # ------------------------------------------------------------------
    # 供测试与外部检查的状态
    # ------------------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._closed

    def current_group_ids(self) -> List[str]:
        """当前标签条上真实渲染出来的分组 id（测试用：断言"生活"没被裁掉）。"""
        return list(self._chips.keys())
