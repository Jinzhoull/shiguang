# -*- coding: utf-8 -*-
"""自绘右键菜单（暖色圆角浮层）。

为什么不用原生 ``tk.Menu``
--------------------------
两个硬伤，都不能靠参数绕过：

1. **宽度不可控**。原生菜单按最长菜单项计算宽度，``"移动到分组"`` 这类带子菜单的项
   会把整个菜单撑得很宽，且 ``tk.Menu`` 没有 width/maxwidth 选项。
2. **样式不可控**。要圆角、要浅柔橘高亮、要图标与文字固定间距 —— 原生菜单是系统绘制，
   做不到。

还有一个**更隐蔽的坑**（本项目真实事故）：``tk.Menu.add_command`` 接受**非字符串**
的 label 并静默字符串化，既不报错也不警告。实测::

    m.add_command(label=<function f>, command=...)
    m.entrycget(1, "label")   # -> '1866271835712f'

于是当 ``Group.icon`` 意外变成函数对象时，``f"{grp.icon} {grp.name}"`` 会渲染出
``"<bound method ... at 0x0000023A...> 工作"`` 这种带内存地址的文案，
而且**任何测试都不会失败**，因为不抛异常。

本模块的三道防线:
    * :func:`clean_label` —— 渲染前净化，非 str 一律丢弃并告警（最后一道，保证界面干净）；
    * :class:`MenuRow` —— 用显式数据类描述菜单项，label 字段带类型标注，
      从写法上就不鼓励把函数塞进 label；
    * 配套的 ``store.set_group_icon`` 与 ``Group.from_dict`` 做写入/读取校验
      （见 ``store.py`` / ``models.py``），从源头不让脏值落库。

样式规格（需求 11/12）: 固定宽度 180px、圆角 12px、图标 18px、文字 13px、
图标与文字间距 8px、选中项浅柔橘底 ``#FDF0E0``、行高 34px、文案超 8 字省略。
"""

from __future__ import annotations

import logging
import re
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Union

import customtkinter as ctk

from .. import theme

log = logging.getLogger("shiguang.menu")

# 形如 "<bound method X.y of <...> at 0x0000023A...>" 的脏值特征。
# 注意它也可能是**字符串**（旧代码把函数对象格式化进数据后落了盘），
# 所以只判断 isinstance 是不够的，必须连字符串内容一起查。
_MEMORY_ADDR_RE = re.compile(
    r"<(?:bound\s+)?(?:method|function)\b.*?\bat\s+0x[0-9a-fA-F]+", re.S
)

# ---- 尺寸常量（需求 11/12，集中管理，禁止在业务代码里硬编码）----
MENU_WIDTH = 180          # 固定宽度，不再被最长项撑开
ROW_HEIGHT = 34
ICON_SIZE = 18
ICON_TEXT_GAP = 8         # 图标与文字间距
PAD_X = 12                # 左右内边距
RADIUS = 12
SEP_HEIGHT = 9            # 分隔线占位高
MAX_LABEL_CHARS = 8       # 超长省略（需求 11）
FADE_STEPS = 6            # 淡入帧数
FADE_INTERVAL = 16        # ≈100ms

# ---- 存活性（本轮修复"鼠标移过就消失"）----
# 菜单**不靠焦点**判断生死。原来的做法是绑定 <FocusOut> → close()，
# 而 hover 展开子菜单时子菜单会 focus_force()，父菜单立刻收到 FocusOut，
# 于是父菜单 close() → 顺手把刚打开的子菜单一起 destroy()，表现为
# "鼠标划过菜单，菜单连同子菜单瞬间消失"。现在改成纯指针判定：
# 指针离开菜单树（含安全热区）并**连续 2 拍**都没回来才关。
WATCH_INTERVAL = 120      # 轮询间隔 ms
WATCH_MISS_LIMIT = 2      # 连续落空次数（≈240ms，够指针跨越子菜单与父菜单的接缝）
SAFE_ZONE = 14            # 安全热区：指针在这个外扩范围内也算"还在菜单上"
SUBMENU_DELAY = 140       # 悬停多久才展开子菜单（防手抖，也给"点开"留了余地）
MOVE_TOLERANCE = 6        # 指针移动超过这个距离才算"用户动了手"（逻辑像素）

# 当前唯一打开的顶层菜单。app.py 的 <Button-1> 用它做"点别处即收起"。
_ACTIVE: List["ContextMenu"] = []


def close_active() -> None:
    """关闭当前打开的右键菜单（点主窗口任意位置时调用）。"""
    while _ACTIVE:
        menu = _ACTIVE.pop()
        try:
            menu.close()
        except Exception:  # noqa: BLE001
            pass

# 选中项高亮底（需求 12：浅柔橘底）
HOVER_LIGHT = "#FDF0E0"
HOVER_DARK = "#463829"


def clean_label(text, fallback: str = "") -> str:
    """把任意值净化成可安全显示的菜单文案。

    这是防线的最后一道：即使上游漏了校验，界面上也只会显示 ``fallback``，
    而不会出现 ``<bound method ... at 0x...>`` 这种内存地址。

    两种脏值都要拦：
    1. **非字符串**（函数/方法/任意对象）—— 直接丢弃；
    2. **内容是内存地址的字符串** —— 旧版本把函数对象格式化后落了盘，
       文件里存的就是这种文本，只判断类型抓不住它。
    """
    if not isinstance(text, str):
        # 这里**故意**不用 str(text)：str(某个函数) 正是要消灭的那种输出。
        log.warning("菜单文案收到非字符串值 %r（type=%s），已丢弃",
                    text, type(text).__name__)
        value = fallback
    elif _MEMORY_ADDR_RE.search(text):
        log.warning("菜单文案含内存地址样式的脏值 %r，已丢弃", text[:80])
        value = fallback
    else:
        value = text
    value = value.replace("\n", " ").strip()
    if len(value) > MAX_LABEL_CHARS:
        value = value[: MAX_LABEL_CHARS - 1] + "…"
    return value


def ellipsize(text: str, limit: int = MAX_LABEL_CHARS) -> str:
    """纯省略，不做类型处理（需要保留完整语义时用，如子菜单里的分组名）。"""
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class MenuRow:
    """一个菜单项。

    ``label`` 的类型标注就是 ``str`` —— 图标与文字分开放，避免
    ``f"{obj.icon} {obj.name}"`` 这种把不信任对象直接格式化的写法。

    ``icon`` 可以为:
        * ``None``        无图标
        * ``str``         一个 emoji/字符（过渡期兼容）
        * ``tk.PhotoImage`` PIL 生成的位图（新图标系统）
    """

    label: str
    action: Optional[Callable[[], None]] = None
    icon: object = None
    children: List["MenuRow"] = field(default_factory=list)
    disabled: bool = False

    @property
    def is_separator(self) -> bool:
        return self.label == "-"

    @property
    def is_submenu(self) -> bool:
        return bool(self.children)


def row(label, action=None, icon=None) -> MenuRow:
    return MenuRow(label=label, action=action, icon=icon)


def separator() -> MenuRow:
    return MenuRow(label="-")


def submenu(label, children: Sequence[MenuRow], icon=None) -> MenuRow:
    return MenuRow(label=label, children=list(children), icon=icon)


# 兼容旧写法：把 (label, callable) / (label, [子项]) 元组转成 MenuRow
def _coerce(entry) -> Optional[MenuRow]:
    if entry is None:
        return separator()
    if isinstance(entry, MenuRow):
        return entry
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        label, action = entry
        if isinstance(action, (list, tuple)):
            children = [c for c in (_coerce(e) for e in action) if c is not None]
            return submenu(label, children)
        return row(label, action)
    log.warning("无法识别的菜单项 %r，已跳过", entry)
    return None


# --------------------------------------------------------------------------
# 浮层
# --------------------------------------------------------------------------
class ContextMenu(tk.Toplevel):
    """暖色圆角右键菜单浮层。

    DPI 说明
    --------
    菜单是 ``overrideredirect`` 的裸 ``Toplevel`` + 裸 ``tk.Canvas``，
    **CTk 完全不管它** —— ``geometry()``、Canvas 宽高、绘制坐标全是物理像素。
    模块顶部的 MENU_WIDTH / ROW_HEIGHT 等是按逻辑像素写的设计值，
    所以这里在构造时统一换算成物理像素（``self.W`` / ``self.ROW_H`` ...），
    绘制与布局只认这组物理值。

    踩坑记录：早期直接用 MENU_WIDTH 当窗口宽度，150% 屏上 180 物理像素
    只有 120 逻辑像素宽，菜单内容按 180 逻辑排版 ⇒ 整体被裁掉一半，
    并且弹出位置还会偏（``winfo_pointerx()`` 给的是物理坐标）。
    """

    def __init__(self, master: tk.Misc, rows: Sequence[MenuRow]) -> None:
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:  # noqa: BLE001
            pass

        bg = theme.c("card")
        self.configure(bg=bg)

        # ---- 逻辑 → 物理（一次性换算，后续只用物理值）----
        self.W = theme.lpx(MENU_WIDTH)
        self.ROW_H = theme.lpx(ROW_HEIGHT)
        self.SEP_H = theme.lpx(SEP_HEIGHT)
        self.ICON = theme.lpx(ICON_SIZE)
        self.ICON_GAP = theme.lpx(ICON_TEXT_GAP)
        self.PAD = theme.lpx(PAD_X)
        self.R = theme.lpx(RADIUS)

        self._rows = list(rows)
        self._chain: List[tk.Toplevel] = []
        self._hover_job: Optional[str] = None
        self._watch_job: Optional[str] = None
        self._submenu_job: Optional[str] = None
        self._watch_miss = 0
        self._ever_inside = False
        self._open_pointer: Optional[tuple] = None
        self._open_submenu_row: Optional[MenuRow] = None
        self._closing = False
        self._is_submenu = False

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                width=self.W)
        self.canvas.pack(fill="both", expand=True)

        self._height = self._layout()
        self.canvas.configure(height=self._height)
        self._draw_background()
        self._bind_events()

    # ------------------------------------------------------------------
    def _layout(self) -> int:
        """按行类型累计高度，不做任何自适应 —— 高度必须完全可控。"""
        h = 0
        for r in self._rows:
            h += self.SEP_H if r.is_separator else self.ROW_H
        return max(self.ROW_H, h)

    def _draw_background(self) -> None:
        """圆角背景：tkinter 没有真正的圆角窗口，用四角小圆补出柔角。

        外圈画一个略深一点的描边色，制造"柔和投影"的观感（用颜色而非真阴影，
        因为 Toplevel 无法只给卡片加阴影）。
        """
        import shiguang.ui.widgets as widgets

        border = theme.c("border")
        bg = theme.c("card")
        r = self.R
        # 全部用物理像素（self.W / self._height 均已换算）
        # 外描边（打 menubg 标签：悬停高亮要靠它把自己夹到"菜单底之上、
        # 行内容之下"，见 _on_motion）
        widgets.round_rect(self.canvas, 0, 0, self.W, self._height, r,
                           fill=border, outline="", tags="menubg")
        # 内填充（留 1px 边）
        widgets.round_rect(self.canvas, 1, 1, self.W - 1, self._height - 1, r - 1,
                           fill=bg, outline="", tags="menubg")

        y = 0
        for r_ in self._rows:
            if r_.is_separator:
                mid = y + self.SEP_H / 2
                self.canvas.create_line(self.PAD + theme.lpx(4), mid,
                                        self.W - self.PAD - theme.lpx(4), mid,
                                        fill=theme.c("border"))
                y += self.SEP_H
                continue
            self._draw_row(r_, y)
            y += self.ROW_H

    def _draw_row(self, r: MenuRow, y: int) -> None:
        color = theme.c("text_muted") if r.disabled else theme.c("text")
        x = self.PAD

        if r.icon is not None:
            self._draw_icon(r.icon, x, y + (self.ROW_H - self.ICON) / 2)
            x += self.ICON + self.ICON_GAP

        label = clean_label(r.label, fallback="（无标题）")
        self.canvas.create_text(
            x, y + self.ROW_H / 2, text=label, anchor="w",
            fill=color, font=theme.font("body"),
        )

        if r.is_submenu:
            chevron = "›"
            self.canvas.create_text(
                self.W - self.PAD, y + self.ROW_H / 2, text=chevron,
                anchor="e", fill=theme.c("text_muted"), font=theme.font("small"),
            )

    def _draw_icon(self, icon, x: float, y: float) -> None:
        """图标可以是 PhotoImage（新图标系统）或字符（过渡兼容）。"""
        if isinstance(icon, str):
            self.canvas.create_text(x + self.ICON / 2, y + self.ICON / 2,
                                    text=icon, fill=theme.c("text_muted"),
                                    font=theme.font("small"))
        else:
            try:
                self.canvas.create_image(x, y, image=icon, anchor="nw")
            except Exception as exc:  # noqa: BLE001
                log.info("菜单图标绘制失败（%s），该项改为无图标", exc)

    # ------------------------------------------------------------------
    def _bind_events(self) -> None:
        """事件绑定。

        ⚠️ 这里**故意不绑 <FocusOut>**。菜单是独立的 overrideredirect 顶层窗口，
        一旦用焦点判生死，展开子菜单（子菜单是另一个顶层窗口）就会把父菜单
        判死 —— 连子菜单一起销毁。存活性统一交给 :meth:`_watch` 按指针位置判定。
        """
        self.bind("<Escape>", lambda _e: self.close())
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Leave>", lambda _e: self._clear_hover())

    # ------------------------------------------------------------------
    # 存活性：指针离开菜单树才关（含安全热区 + 连续两拍确认）
    # ------------------------------------------------------------------
    def _watch(self) -> None:
        self._watch_job = None
        if not self.winfo_exists() or self._closing:
            return
        if self._pointer_inside():
            self._watch_miss = 0
            self._ever_inside = True
        else:
            # 还没进过菜单、指针也没动过 —— 说明菜单被屏幕边缘挤开了，
            # 指针停在原地。这种情况要给它留时间，不能秒关（否则在屏幕
            # 右下角右键，菜单一出现就自己没了）。
            moved = self._pointer_moved()
            if self._ever_inside or moved:
                self._watch_miss += 1
                if self._watch_miss >= WATCH_MISS_LIMIT:
                    self.close()
                    return
            else:
                self._watch_miss = 0
        self._watch_job = self.after(WATCH_INTERVAL, self._watch)

    def _pointer_moved(self) -> bool:
        """指针相对"菜单弹出那一刻"是否移动过（判定用户是否在操作别处）。"""
        if self._open_pointer is None:
            return True
        try:
            px, py = self.winfo_pointerxy()
        except Exception:  # noqa: BLE001
            return False
        ox, oy = self._open_pointer
        tol = theme.lpx(MOVE_TOLERANCE)
        return abs(px - ox) > tol or abs(py - oy) > tol

    def _pointer_inside(self) -> bool:
        """指针是否还在「本菜单 + 它的整条子菜单链」上（外扩安全热区）。

        拿不到指针位置时保守返回 True —— 宁可菜单多留一会儿，也不要让它
        莫名其妙自己消失（用户会觉得"点不中"）。
        """
        try:
            px, py = self.winfo_pointerxy()
        except Exception:  # noqa: BLE001
            return True
        zone = theme.lpx(SAFE_ZONE)
        for win in [self] + list(self._chain):
            try:
                if not win.winfo_exists():
                    continue
                x, y = win.winfo_rootx(), win.winfo_rooty()
                if (x - zone <= px <= x + win.winfo_width() + zone
                        and y - zone <= py <= y + win.winfo_height() + zone):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _row_at(self, y: int) -> Optional[tuple]:
        """返回 (row, y_top)，跳过分隔线。"""
        cursor = 0
        for r in self._rows:
            if r.is_separator:
                cursor += self.SEP_H
                continue
            if cursor <= y < cursor + self.ROW_H:
                return (r, cursor)
            cursor += self.ROW_H
        return None

    def _on_motion(self, event) -> None:
        hit = self._row_at(event.y)
        self._clear_hover()
        if hit is None:
            return
        r, y_top = hit
        # 悬停项：浅柔橘底 + 圆角，右侧留 6px 不贴边
        import shiguang.ui.widgets as widgets

        widgets.round_rect(self.canvas, theme.lpx(6), y_top + theme.lpx(2),
                           self.W - theme.lpx(6),
                           y_top + self.ROW_H - theme.lpx(2), theme.lpx(8),
                           fill=HOVER_DARK if theme.is_dark() else HOVER_LIGHT,
                           outline="", tags="hover")
        # 层序必须是：**菜单底 → 悬停高亮 → 行内容**。
        # 只把高亮 ``tag_lower("hover")`` 的话，它会一路沉到菜单底色
        # （``menubg``）**之下** —— 被不透明的卡片底一盖，鼠标划过菜单
        # 完全没有高亮反馈（不放大截图根本发现不了）。
        # 再把整组菜单底也下沉一次，高亮就正好夹在中间。
        self.canvas.tag_lower("hover")
        self.canvas.tag_lower("menubg")

        if r.is_submenu and not r.disabled:
            # 同一个子菜单项上继续移动鼠标，不要重建（重建 = 闪烁 + 指针追不上）
            if self._open_submenu_row is r:
                return
            self._schedule_submenu(r, y_top)
        else:
            # 停在普通项上：说明不是"要去子菜单"，立即收起
            self._cancel_submenu_job()
            self._close_submenus()

    def _clear_hover(self) -> None:
        self.canvas.delete("hover")

    def _on_click(self, event) -> None:
        hit = self._row_at(event.y)
        if hit is None:
            return
        r, y_top = hit
        if r.disabled:
            return
        if r.is_submenu:
            # 需求 ④"改为点击触发"：点带子菜单的项也能直接展开，
            # 不必依赖"悬停对了位置"这件事
            self._cancel_submenu_job()
            self._open_submenu(r, y_top)
            return
        self.close()
        if r.action is not None:
            r.action()

    # ------------------------------------------------------------------
    def _schedule_submenu(self, r: MenuRow, y_top: int) -> None:
        """延迟展开子菜单（悬停触发用）。"""
        self._cancel_submenu_job()
        self._submenu_job = self.after(
            SUBMENU_DELAY, lambda: self._open_submenu(r, y_top))

    def _cancel_submenu_job(self) -> None:
        if self._submenu_job is not None:
            try:
                self.after_cancel(self._submenu_job)
            except Exception:  # noqa: BLE001
                pass
            self._submenu_job = None

    def _open_submenu(self, r: MenuRow, y_top: int) -> None:
        self._submenu_job = None
        if self._open_submenu_row is r and self._chain:
            return                        # 已经是它，别重建（重建会让指针"追不上"）
        self._close_submenus()
        sub = ContextMenu._build_sub(self, r.children)
        if sub is None:
            return
        self._open_submenu_row = r
        self._chain.append(sub)
        # watch=False：存活性由顶层菜单统管，子菜单跟着父菜单一起走
        sub.show_at(self.winfo_rootx() + self.W - theme.lpx(6),
                    self.winfo_rooty() + y_top + 2, watch=False)

    def _close_submenus(self) -> None:
        self._open_submenu_row = None
        for w in self._chain:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._chain.clear()

    @classmethod
    def _build_sub(cls, master: tk.Misc, children: Sequence[MenuRow]) -> Optional["ContextMenu"]:
        rows = [c for c in children if c is not None]
        if not rows:
            return None
        menu = cls.__new__(cls)
        tk.Toplevel.__init__(menu, master)
        menu.withdraw()
        menu.overrideredirect(True)
        try:
            menu.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        bg = theme.c("card")
        menu.configure(bg=bg)
        # 子菜单走 __new__ 手工初始化，绕过了 __init__，所以这组物理尺寸
        # 必须在这里再算一次（否则子菜单会退回逻辑像素，宽度只有一半）。
        menu.W = theme.lpx(MENU_WIDTH)
        menu.ROW_H = theme.lpx(ROW_HEIGHT)
        menu.SEP_H = theme.lpx(SEP_HEIGHT)
        menu.ICON = theme.lpx(ICON_SIZE)
        menu.ICON_GAP = theme.lpx(ICON_TEXT_GAP)
        menu.PAD = theme.lpx(PAD_X)
        menu.R = theme.lpx(RADIUS)
        menu._rows = rows
        menu._chain = []
        menu._hover_job = None
        menu._watch_job = None
        menu._submenu_job = None
        menu._watch_miss = 0
        menu._ever_inside = False
        menu._open_pointer = None
        menu._open_submenu_row = None
        menu._closing = False
        menu._is_submenu = True
        menu.canvas = tk.Canvas(menu, bg=bg, highlightthickness=0, bd=0,
                                width=menu.W)
        menu.canvas.pack(fill="both", expand=True)
        menu._height = menu._layout()
        menu.canvas.configure(height=menu._height)
        menu._draw_background()
        menu._bind_events()
        return menu

    # ------------------------------------------------------------------
    def show_at(self, x: int, y: int, watch: bool = True) -> None:
        """在屏幕坐标 (x, y) 弹出，并自动避开屏幕右/下边缘。

        ``x``/``y`` 必须是**物理像素**（``winfo_pointerx()``、
        ``winfo_rootx()`` 返回的都是物理值）；``geometry()`` 也按物理像素解释。

        ``watch=False`` 给子菜单用：存活性由顶层菜单统一看守。
        """
        self.update_idletasks()
        w, h = self.W, self._height
        # 边界必须和 x/y **同单位**。
        # ``winfo_screenwidth()`` 在部分 Tk + DPI 组合下报的是**逻辑**宽
        # （实测同一进程里它返回 1280，而 ``winfo_rootx()`` 给的是 1920 制下的
        # 物理坐标 1203）。拿逻辑宽去钳物理坐标，1263 就被判成"越界"并整块左移到
        # 1004 —— 菜单跑到主窗口左边，甚至盖住旁边别的应用。
        # 虚拟根窗口 (vroot) 与 ``winfo_rootx()`` 同源同单位，一律按它算。
        try:
            vx, vy = self.winfo_vrootx(), self.winfo_vrooty()
            vw, vh = self.winfo_vrootwidth(), self.winfo_vrootheight()
            if vw <= 1 or vh <= 1:
                raise ValueError("vroot 不可用")
            sw, sh = vx + vw, vy + vh
        except Exception:  # noqa: BLE001
            vx, vy, sw, sh = 0, 0, 1920, 1080
        margin = theme.lpx(4)
        x = max(vx, min(x, sw - w - margin))
        y = max(vy, min(y, sh - h - margin))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.deiconify()
        self.attributes("-topmost", True)
        if watch:
            _ACTIVE.append(self)
            try:
                self._open_pointer = self.winfo_pointerxy()
            except Exception:  # noqa: BLE001
                self._open_pointer = None
            self._watch()
        self._fade_in()
        if watch:
            # 只有顶层菜单抢焦点：这样 Esc 立刻生效，而且菜单一出现就能
            # 用键盘操作。子菜单**绝不**抢焦点 —— 正是它当年抢焦点把
            # 父菜单判成了"失焦"，才导致整条菜单链瞬间消失。
            try:
                self.focus_force()
            except Exception:  # noqa: BLE001
                pass

    def _fade_in(self) -> None:
        """淡入（tkinter 支持 Toplevel 的 -alpha）。"""
        def step(i: int) -> None:
            if not self.winfo_exists():
                return
            try:
                self.attributes("-alpha", min(1.0, 0.55 + i * (0.45 / FADE_STEPS)))
            except Exception:  # noqa: BLE001
                return
            if i < FADE_STEPS:
                self._hover_job = self.after(FADE_INTERVAL, step, i + 1)
            else:
                self._hover_job = None
        step(0)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        for job in (self._hover_job, self._watch_job, self._submenu_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:  # noqa: BLE001
                    pass
        self._hover_job = self._watch_job = self._submenu_job = None
        try:
            if self in _ACTIVE:
                _ACTIVE.remove(self)
        except Exception:  # noqa: BLE001
            pass
        self._close_submenus()
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _row_texts(self) -> List[str]:
        """当前所有菜单行的最终显示文本（含子菜单）。

        给测试用：要断言"菜单里没有任何内存地址文本"，就得能拿到
        **真正渲染出来的** label，而不是构造时的输入 —— 净化可能改了内容。
        """
        texts: List[str] = []
        for row in self._rows:
            if row.label:
                texts.append(row.label)
            if row.children:
                for child in row.children:
                    if child.label:
                        texts.append(child.label)
        return texts


def popup_menu(master: tk.Misc, items: Sequence, x: Optional[int] = None,
               y: Optional[int] = None) -> Optional[ContextMenu]:
    """弹出菜单。``items`` 支持 :class:`MenuRow` 或旧的 ``(label, action)`` 元组。"""
    rows = [r for r in (_coerce(e) for e in items) if r is not None]
    if not rows:
        return None
    menu = ContextMenu(master, rows)
    if x is None:
        x = master.winfo_pointerx()
    if y is None:
        y = master.winfo_pointery()
    menu.show_at(x, y)
    return menu
