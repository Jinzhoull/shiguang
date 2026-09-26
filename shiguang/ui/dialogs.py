# -*- coding: utf-8 -*-
"""对话框：任务编辑、确认、单行输入、关于。

统一约定
--------
* 一律用 ``CTkToplevel`` + ``grab_set()`` 做模态，保证主窗口不会被误操作；
  （唯一例外是 :class:`ConfirmDialog` —— 它自绘标题栏，用裸 ``tk.Toplevel``
  的 ``overrideredirect`` 窗口，见该类文档）
* 打开时居中到主窗口，关闭后把焦点还给主窗口（否则快捷键会失效）；
* 所有对话框都不直接改数据，只把结果通过 ``on_save`` / ``on_ok`` 回调交回调用方。
"""

from __future__ import annotations

import datetime as _dt
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from .. import __app_name__, __slogan__, __version__, theme
from ..models import (PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MID, PRIORITY_NAMES,
                      REMIND_DEFAULT, Task, remind_label)
from . import widgets


def export_tasks_csv(app) -> None:
    """导出任务 CSV 的**唯一**入口（1.5.11 收敛）。

    之前光景页和设置页各写了一份"弹保存框 -> export_csv -> toast"，
    默认文件名等细节已经开始漂移。两个页面都只调这里，改口径只动一处。
    """
    path = filedialog.asksaveasfilename(
        parent=app, title="导出任务数据",
        initialfile=f"拾光-任务导出-{_dt.date.today().isoformat()}.csv",
        defaultextension=".csv",
        filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")])
    if not path:
        return
    try:
        count = app.store.export_csv(Path(path))
        app.toast(f"已导出 {count} 条任务到 CSV")
    except Exception as exc:  # noqa: BLE001
        app.toast(f"导出失败：{exc}")


def _center_on(win: ctk.CTkToplevel, parent: tk.Misc) -> None:
    """把窗口居中到父窗口上。

    ⚠️ ``winfo_reqwidth()`` 返回的是**物理像素**，而 ctk 的 ``geometry()``
    会把宽高**再乘一次**缩放系数。早期直接把 reqwidth 传进去，于是 150% 屏上
    每个对话框都比设计大 50%（日期选择器实测被撑到 718×753 物理像素，
    比主界面还壮观）。这里先除回逻辑像素再交给 geometry()。
    """
    win.update_idletasks()
    scale = theme.scale() or 1.0
    # 下限走主题常量（各对话框可用 MIN_DIALOG_W/MIN_DIALOG_H 覆盖），
    # 不再写死 —— 日期选择器这类紧凑面板被硬撑开会白多出一圈空档。
    min_w = getattr(win, "MIN_DIALOG_W", theme.DIALOG_MIN_W)
    min_h = getattr(win, "MIN_DIALOG_H", theme.DIALOG_MIN_H)
    w = max(min_w, int(round(win.winfo_reqwidth() / scale)))
    h = max(min_h, int(round(win.winfo_reqheight() / scale)))
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
    except Exception:  # noqa: BLE001
        px, py, pw, ph = 300, 200, 420, 640
    x = px + max(0, (pw - int(w * scale)) // 2)
    y = py + max(0, (ph - int(h * scale)) // 3)
    win.geometry(f"{w}x{h}+{x}+{y}")


class _BaseDialog(ctk.CTkToplevel):
    """模态对话框基类。"""

    def __init__(self, app, title: str) -> None:
        super().__init__(app)
        self.app = app
        self.title(title)
        self.configure(fg_color=theme.pair("bg"))
        self.resizable(False, False)
        self.transient(app)
        ctk.set_appearance_mode(theme.MODE_MAP.get(app.store.settings.get("theme", "system"),
                                                   "System"))
        self.grid_columnconfigure(0, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._apply_window_icon(app)
        # Windows 下置顶于父窗口
        try:
            self.attributes("-topmost", bool(app.store.settings.get("always_on_top", False)))
        except Exception:  # noqa: BLE001
            pass

    def _apply_window_icon(self, app) -> None:
        """给对话框的原生标题栏装上品牌图标。

        主窗口是 ``overrideredirect``（没有原生标题栏），对话框有 —— 于是
        标题栏左上角露出的是 Tk 的默认图标（一个蓝白方块），跟暖色调完全不搭。
        ``app.iconbitmap(default=...)`` 设的是"后续新建 toplevel 的默认图标"，
        实测对 CTkToplevel 不生效，只能自己再设一次。

        ⚠️ 必须用 ``iconbitmap`` 而不是 ``iconphoto``：``CTkToplevel.__init__``
        排了一个 ``after(200, self._windows_set_titlebar_icon)``，只要用户没调过
        ``iconbitmap``（CTk 重写它时会立一个 ``_iconbitmap_method_called`` 标志），
        200ms 后它就把标题栏图标换成 CustomTkinter 自带的
        ``CustomTkinter_icon_Windows.ico`` —— 也就是那个蓝白方块。用 ``iconphoto``
        设的图标会在那一拍被吃掉，所以这里以 ``iconbitmap`` 为准。
        """
        ico = getattr(app, "_icon_ico", None)
        if ico:
            try:
                self.iconbitmap(str(ico))
            except Exception:  # noqa: BLE001
                pass
        # 再补一份 iconphoto：任务栏 / Alt-Tab 的图标走这条更稳，两者不冲突。
        icon = getattr(app, "_icon_image", None)
        if icon is not None:
            try:
                self.iconphoto(True, icon)
            except Exception:  # noqa: BLE001
                pass

    def _finish(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        parent = self.app
        self.destroy()
        try:
            parent.focus_force()
            parent.lift()
        except Exception:  # noqa: BLE001
            pass

    def _cancel(self) -> None:
        self._finish()

    def show(self) -> None:
        self.after(10, self._do_show)

    def _do_show(self) -> None:
        _center_on(self, self.app)
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# 任务编辑
# --------------------------------------------------------------------------
class TaskDialog(_BaseDialog):
    """新增 / 编辑任务。"""

    def __init__(self, app, task: Optional[Task] = None,
                 preset_group: Optional[str] = None,
                 on_save: Optional[Callable[[Optional[str], dict], None]] = None) -> None:
        super().__init__(app, "编辑任务" if task else "新的任务")
        self.task = task
        self.on_save = on_save

        pad = {"padx": 20}
        ctk.CTkLabel(self, text="任务内容", font=theme.font("small"),
                     text_color=theme.pair("text_muted")).grid(
            row=0, column=0, sticky="w", pady=(18, 4), **pad)

        self.title_entry = ctk.CTkEntry(
            self, font=theme.font("body"), height=38, corner_radius=12,
            fg_color=theme.pair("card"), border_color=theme.pair("border"),
            text_color=theme.pair("text"), placeholder_text="想做的事…",
        )
        self.title_entry.grid(row=1, column=0, sticky="ew", **pad)
        self.title_entry.insert(0, task.title if task else "")

        # ---- 分组 ----
        ctk.CTkLabel(self, text="分组", font=theme.font("small"),
                     text_color=theme.pair("text_muted")).grid(
            row=2, column=0, sticky="w", pady=(14, 4), **pad)
        self._group_ids = [g.id for g in app.store.groups]
        names = [f"{g.icon} {g.name}" for g in app.store.groups]
        current = task.group_id if task else (preset_group or app.store.settings.get("default_group"))
        if current not in self._group_ids:
            current = self._group_ids[0] if self._group_ids else ""
        index = self._group_ids.index(current) if current in self._group_ids else 0
        self.group_menu = ctk.CTkOptionMenu(
            self, values=names or ["默认"], height=34, corner_radius=12,
            fg_color=theme.pair("ghost"), button_color=theme.pair("border"),
            button_hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text"), font=theme.font("small"),
            dropdown_fg_color=theme.pair("card"),
            dropdown_text_color=theme.pair("text"),
            dropdown_hover_color=theme.pair("accent_soft"),
        )
        if names:
            self.group_menu.set(names[index])
        self.group_menu.grid(row=3, column=0, sticky="ew", **pad)

        # ---- 优先级 ----
        ctk.CTkLabel(self, text="优先级", font=theme.font("small"),
                     text_color=theme.pair("text_muted")).grid(
            row=4, column=0, sticky="w", pady=(14, 4), **pad)
        self.priority_var = ctk.StringVar(
            value=PRIORITY_NAMES.get(task.priority if task else PRIORITY_MID, "中"))
        self.priority_seg = ctk.CTkSegmentedButton(
            self, values=["高", "中", "低"], variable=self.priority_var,
            font=theme.font("small"), height=34,
            fg_color=theme.pair("ghost"),
            selected_color=theme.pair("accent"),
            selected_hover_color=theme.pair("accent_hover"),
            unselected_color=theme.pair("ghost"),
            unselected_hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text"),
        )
        self.priority_seg.grid(row=5, column=0, sticky="ew", **pad)

        # ---- 截止日期 ----
        ctk.CTkLabel(self, text="截止日期", font=theme.font("small"),
                     text_color=theme.pair("text_muted")).grid(
            row=6, column=0, sticky="w", pady=(14, 4), **pad)
        self._due = task.due_date if task else None
        # 提醒提前量：编辑时沿用任务现值；新建时用默认（提前 15 分钟）。
        # 这里只保存状态，真正的选择器在截止日期弹窗里（时间行下方）。
        self._remind = (task.remind_offset if task is not None
                        else REMIND_DEFAULT)
        due_row = ctk.CTkFrame(self, fg_color="transparent")
        due_row.grid(row=7, column=0, sticky="ew", **pad)
        due_row.grid_columnconfigure(0, weight=1)
        # 日历图标走自绘（需求 14：不用 emoji），用 compound="left" 把图和字拼起来
        self._due_icon = None
        try:
            from .. import icons
            self._due_icon = icons.get_ctk("calendar", icons.SIZE_MAIN)
        except Exception:  # noqa: BLE001
            self._due_icon = None
        self.due_btn = ctk.CTkButton(
            due_row, text="", height=34, corner_radius=12,
            fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text"), font=theme.font("small"),
            image=self._due_icon,
            compound="left" if self._due_icon is not None else "text",
            anchor="w", command=self._pick_due,
        )
        self.due_btn.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(due_row, text="清除", width=60, height=34, corner_radius=12,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text_muted"), font=theme.font("tiny"),
                      command=self._clear_due).grid(row=0, column=1, padx=(8, 0))
        self._sync_due_button()

        # ---- 备注 ----
        ctk.CTkLabel(self, text="备注（可选）", font=theme.font("small"),
                     text_color=theme.pair("text_muted")).grid(
            row=8, column=0, sticky="w", pady=(14, 4), **pad)
        self.note_box = ctk.CTkTextbox(
            self, height=64, corner_radius=12, font=theme.font("small"),
            fg_color=theme.pair("card"), border_color=theme.pair("border"),
            border_width=1, text_color=theme.pair("text"),
        )
        self.note_box.grid(row=9, column=0, sticky="ew", **pad)
        if task and task.note:
            self.note_box.insert("1.0", task.note)

        # ---- 按钮 ----
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=10, column=0, sticky="ew", pady=(18, 18), **pad)
        buttons.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(buttons, text="取消", width=88, height=36, corner_radius=18,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text_muted"), font=theme.font("small"),
                      command=self._cancel).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(buttons, text="保存", width=88, height=36, corner_radius=18,
                      fg_color=theme.pair("accent"), hover_color=theme.pair("accent_hover"),
                      text_color=("#FFFFFF", "#2D2A26"), font=theme.font("small"),
                      command=self._save).grid(row=0, column=2)

        self.title_entry.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.after(60, lambda: (self.title_entry.focus_set(),
                                self.title_entry.icursor("end")))
        self.show()

    # ------------------------------------------------------------------
    # 截止日期
    # ------------------------------------------------------------------
    def _sync_due_button(self) -> None:
        # 图标已由 image/compound 承担，这里只负责文本（需求 14：字面不含 emoji）
        if self._due is None:
            self.due_btn.configure(text="  未设置", text_color=theme.pair("text_done"))
        else:
            # 把提醒一并带出来：否则"选了提前 15 分钟"在关掉弹窗后完全不可见
            tail = "" if self._remind is None else f" · {remind_label(self._remind)}"
            self.due_btn.configure(
                text=f"  {self._due.strftime('%Y/%m/%d %H:%M')}{tail}",
                text_color=theme.pair("text"),
            )

    def _pick_due(self) -> None:
        # 延迟导入：due_picker 依赖本模块的 _BaseDialog，模块级导入会成环
        from . import due_picker

        due_picker.pick_due(self.app, self._due, self._on_due_picked,
                            remind=self._remind)

    def _on_due_picked(self, when, remind=None) -> None:
        self._due = when
        self._remind = remind
        self._sync_due_button()

    def _clear_due(self) -> None:
        self._due = None
        self._sync_due_button()

    def _save(self) -> None:
        title = self.title_entry.get().strip()
        if not title:
            self.title_entry.configure(border_color=theme.pair("danger"))
            return
        name = self.group_menu.get()
        group_id = self._group_ids[0] if self._group_ids else ""
        for gid, grp in zip(self._group_ids, self.app.store.groups):
            if f"{grp.icon} {grp.name}" == name:
                group_id = gid
                break
        priority = {"高": PRIORITY_HIGH, "中": PRIORITY_MID, "低": PRIORITY_LOW}.get(
            self.priority_var.get(), PRIORITY_MID)
        fields = {
            "title": title,
            "group_id": group_id,
            "priority": priority,
            "note": self.note_box.get("1.0", "end").strip(),
            "due_date": self._due,
            "remind_offset": self._remind,
        }
        task_id = self.task.id if self.task else None
        self._finish()
        if self.on_save:
            self.on_save(task_id, fields)


# --------------------------------------------------------------------------
# 确认框
# --------------------------------------------------------------------------
class ConfirmDialog(tk.Toplevel):
    """紧凑型模态确认框（1.5.3 重写）。

    为什么要自己画标题栏
    --------------------
    上一版是 ``CTkToplevel`` + **系统标题栏**：标题栏高度由系统决定（约 30
    物理像素，改不动），窗口宽度又跟着正文的 ``wraplength`` 走 —— 实测在
    344×470 的主窗口上弹出一个 340 宽的确认框，"删一条任务"这种小事和整个
    应用一样宽，跟"桌面小插件"的定位完全不搭（用户报的图1）。

    现在改成：``overrideredirect`` 无边框 + 自绘一行紧凑标题（16px 太阳图标
    + 13px 标题 + 16px 关闭），宽度锁在 ``theme.CONFIRM_W``（268 逻辑像素，
    落在需求给的 260–280 区间内），高度完全由内容决定，并且**保证整框落在
    主窗口内**（居中；主窗口装不下时按边距收进来）。

    与 ``_BaseDialog`` 的关系
    -------------------------
    刻意不继承：基类那套（原生标题栏图标、``_center_on`` 按 reqwidth 居中）
    全都建立在"窗口有系统边框"的假设上。这里保持独立，只沿用同一套模态约定
    —— ``grab_set`` + Esc 取消 + 回车确定 + 关闭后把焦点还给主窗口。
    """

    def __init__(self, app, title: str, message: str,
                 on_ok: Callable[[], None], ok_text: str = "确定",
                 danger: bool = False) -> None:
        super().__init__(app)
        self.app = app
        self.on_ok = on_ok
        self._done = False
        self._drag_origin = None

        self.withdraw()
        self.overrideredirect(True)
        try:
            self.transient(app)
        except Exception:  # noqa: BLE001
            pass
        # 主题必须**在取色之前**同步：本窗口不继承 CTk 基类的自动配色，
        # theme.c() 读的是当前 appearance mode，不同步就会拿错深浅色。
        ctk.set_appearance_mode(theme.MODE_MAP.get(
            app.store.settings.get("theme", "system"), "System"))
        # 与 dialogs._BaseDialog 一个口径：跟随主窗口的置顶设置，不无条件置顶
        # （无条件置顶会盖住"用户随后打开的对话框"，也是 1.5.1 修过的老毛病）。
        try:
            self.attributes("-topmost",
                            bool(app.store.settings.get("always_on_top", False)))
        except Exception:  # noqa: BLE001
            pass
        self.configure(bg=theme.c("bg"))

        card = ctk.CTkFrame(
            self, fg_color=theme.pair("card"), bg_color=theme.pair("bg"),
            corner_radius=theme.CONFIRM_RADIUS,
            border_width=1, border_color=theme.pair("border"),
        )
        card.pack(fill="both", expand=True)
        card.grid_columnconfigure(0, weight=1)
        self.card = card

        # ---------------- 标题行：太阳 + 标题 + 关闭 ----------------
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew",
                  padx=theme.CONFIRM_PAD_X, pady=(theme.CONFIRM_PAD_TOP, 0))
        head.grid_columnconfigure(1, weight=1)
        self.head = head

        try:
            from .. import icons
            sun = icons.get_ctk("nav_today", theme.CONFIRM_ICON)
        except Exception:  # noqa: BLE001
            sun = None
        self._sun = widgets.TextLabel(
            head, text="", image=sun, width=theme.CONFIRM_ICON,
            height=theme.CONFIRM_ICON)
        self._sun.grid(row=0, column=0, sticky="w")

        self._title = widgets.TextLabel(
            head, text=title, font=theme.font("small_bold"),
            text_color=theme.pair("text"))
        self._title.grid(row=0, column=1, sticky="w",
                         padx=(theme.CONFIRM_TITLE_GAP, 0))

        widgets.IconButton(
            head, text="", icon="close", icon_size=theme.CONFIRM_CLOSE,
            size=theme.CONFIRM_CLOSE, command=self._cancel,
        ).grid(row=0, column=2, sticky="e")

        # ---------------- 提示语（13px）----------------
        widgets.TextLabel(
            card, text=message, font=theme.font("body"),
            text_color=theme.pair("text"), justify="left", anchor="w",
            wraplength=theme.CONFIRM_W - theme.CONFIRM_PAD_X * 2,
        ).grid(row=1, column=0, sticky="ew",
               padx=theme.CONFIRM_PAD_X,
               pady=(theme.CONFIRM_MSG_GAP_TOP, theme.CONFIRM_MSG_GAP_BOTTOM))

        # ---------------- 底部按钮（小尺寸圆角，横向紧凑排列）----------------
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="e",
                     padx=theme.CONFIRM_PAD_X, pady=(0, theme.CONFIRM_PAD_BOTTOM))
        self._button(buttons, "取消", self._cancel, danger=False, ghost=True).pack(
            side="left", padx=(0, theme.CONFIRM_BTN_GAP))
        self._button(buttons, ok_text, self._ok, danger=danger).pack(side="left")

        # 标题行可拖动：无边框窗口没有系统拖拽，不补这一手就只能钉在原地
        for widget in (head, self._sun, self._title):
            widget.bind("<Button-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_move, add="+")

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        self.show()

    # ------------------------------------------------------------------
    def _button(self, master: tk.Misc, text: str, command: Callable[[], None],
                danger: bool, ghost: bool = False) -> ctk.CTkButton:
        """按文字宽算贴合宽度的小圆角按钮。

        ❗必须显式给 ``width``：``CTkButton`` 不给宽就是默认 **140 逻辑像素**，
        两个按钮并排会把 268 宽的卡片直接撑爆（本项目已在浮层 chip 与日期
        选择器的快捷按钮上各踩过一次）。
        """
        width = max(theme.CONFIRM_BTN_MIN_W,
                    theme.fit_width(text, "small", pad_x=theme.CONFIRM_BTN_PAD_X))
        if ghost:
            fg, hover = theme.pair("ghost"), theme.pair("ghost_hover")
            text_color = theme.pair("text_muted")
        else:
            key = "danger" if danger else "accent"
            fg = theme.pair(key)
            hover = theme.pair("orange_hover" if danger else "accent_hover")
            text_color = theme.ON_ACCENT
        return ctk.CTkButton(
            master, text=text, width=width, height=theme.CONFIRM_BTN_H,
            corner_radius=theme.CONFIRM_BTN_H // 2,
            fg_color=fg, hover_color=hover, text_color=text_color,
            font=theme.font("small"), command=command,
        )

    # ------------------------------------------------------------------
    # 位置与尺寸
    # ------------------------------------------------------------------
    def show(self) -> None:
        self.after(10, self._do_show)

    def _do_show(self) -> None:
        self._place()
        try:
            self.deiconify()
            self.lift()
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass
        # 截图 / 预览等场景不抢焦点（与 app 的"启动抢前台"同一套开关）
        if self._may_activate():
            try:
                self.focus_force()
            except Exception:  # noqa: BLE001
                pass

    def _may_activate(self) -> bool:
        try:
            return not self.app._activation_disabled()
        except Exception:  # noqa: BLE001
            return True

    def _place(self) -> None:
        """宽度锁在 260–280，高度按内容，整体居中于主窗口且不越界。

        ⚠️ 本类是**裸** ``tk.Toplevel``：``geometry()`` 收的是**物理像素**
        （只有 ``CTkToplevel`` 才会再乘一次缩放）。所以这里一律用
        ``theme.lpx()`` 换算，不要照抄 ``_center_on`` 里"先除回逻辑像素"的写法。
        """
        self.update_idletasks()
        need_w = self.winfo_reqwidth()
        need_h = self.winfo_reqheight()
        w = max(theme.lpx(theme.CONFIRM_MIN_W),
                min(theme.lpx(theme.CONFIRM_MAX_W),
                    max(theme.lpx(theme.CONFIRM_W), need_w)))
        h = max(theme.lpx(60), need_h)

        edge = theme.lpx(theme.CONFIRM_PAD_X)
        try:
            px, py = self.app.winfo_rootx(), self.app.winfo_rooty()
            pw, ph = self.app.winfo_width(), self.app.winfo_height()
        except Exception:  # noqa: BLE001
            px = py = 0
            pw = ph = 0

        if pw > w + edge and ph > h + edge:
            # 主窗口装得下：居中（纵向略偏上，视觉重心更稳），再按边距钳一次
            x = px + (pw - w) // 2
            y = py + max(edge, (ph - h) // 3)
            x = min(max(x, px + edge), px + pw - w - edge)
            y = min(max(y, py + edge), py + ph - h - edge)
        else:
            # 极小窗口（理论上到不了）：退到屏幕居中，至少别越出屏幕
            try:
                sx, sy = self.winfo_vrootx(), self.winfo_vrooty()
                sw, sh = self.winfo_vrootwidth(), self.winfo_vrootheight()
            except Exception:  # noqa: BLE001
                sw, sh, sx, sy = 1280, 720, 0, 0
            x = sx + max(0, (sw - w) // 2)
            y = sy + max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    # ------------------------------------------------------------------
    # 拖动（无边框窗口的标配）
    # ------------------------------------------------------------------
    def _drag_start(self, event) -> None:
        self._drag_origin = (event.x_root - self.winfo_x(),
                             event.y_root - self.winfo_y())

    def _drag_move(self, event) -> None:
        if self._drag_origin is None:
            return
        dx, dy = self._drag_origin
        try:
            self.geometry(f"+{int(event.x_root - dx)}+{int(event.y_root - dy)}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass
        if self._may_activate():
            try:
                self.app.focus_force()
                self.app.lift()
            except Exception:  # noqa: BLE001
                pass

    def _cancel(self) -> None:
        self._finish()

    def _ok(self) -> None:
        callback = self.on_ok
        self._finish()
        if callback:
            callback()


def confirm(app, title: str, message: str, on_ok: Callable[[], None],
            ok_text: str = "确定", danger: bool = False) -> None:
    ConfirmDialog(app, title, message, on_ok, ok_text=ok_text, danger=danger)


# --------------------------------------------------------------------------
# 单行输入
# --------------------------------------------------------------------------
class PromptDialog(_BaseDialog):
    def __init__(self, app, title: str, label: str, initial: str,
                 on_ok: Callable[[str], None]) -> None:
        super().__init__(app, title)
        self.on_ok = on_ok
        ctk.CTkLabel(self, text=label, font=theme.font("small"),
                     text_color=theme.pair("text_muted")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(20, 6))
        self.entry = ctk.CTkEntry(self, font=theme.font("body"), height=36,
                                  corner_radius=12, fg_color=theme.pair("card"),
                                  border_color=theme.pair("border"),
                                  text_color=theme.pair("text"))
        self.entry.grid(row=1, column=0, sticky="ew", padx=20)
        self.entry.insert(0, initial)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=20, pady=(16, 18))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(buttons, text="取消", width=80, height=34, corner_radius=17,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text_muted"), font=theme.font("small"),
                      command=self._cancel).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(buttons, text="保存", width=80, height=34, corner_radius=17,
                      fg_color=theme.pair("accent"), hover_color=theme.pair("accent_hover"),
                      text_color=("#FFFFFF", "#2D2A26"), font=theme.font("small"),
                      command=self._ok).grid(row=0, column=2)
        self.entry.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.after(60, lambda: (self.entry.focus_set(), self.entry.select_range(0, "end")))
        self.show()

    def _ok(self) -> None:
        text = self.entry.get().strip()
        callback = self.on_ok
        self._finish()
        if callback:
            callback(text)


def prompt(app, title: str, label: str, initial: str,
           on_ok: Callable[[str], None]) -> None:
    PromptDialog(app, title, label, initial, on_ok)


# --------------------------------------------------------------------------
# 关于
# --------------------------------------------------------------------------
class AboutDialog(_BaseDialog):
    def __init__(self, app) -> None:
        super().__init__(app, f"关于{__app_name__}")
        self.grid_columnconfigure(0, weight=1)

        mark = tk.Canvas(self, width=110, height=110, bg=theme.c("bg"),
                         highlightthickness=0, bd=0)
        mark.grid(row=0, column=0, pady=(22, 4))
        self._draw_sun(mark, 110)
        mark.bind("<Button-1>", lambda _e: self._spin(mark, 0))

        ctk.CTkLabel(self, text=__app_name__, font=theme.font("title"),
                     text_color=theme.pair("text")).grid(row=1, column=0)
        ctk.CTkLabel(self, text=f"v{__version__}", font=theme.font("tiny"),
                     text_color=theme.pair("text_muted")).grid(row=2, column=0, pady=(0, 2))
        ctk.CTkLabel(self, text=__slogan__, font=theme.font("h2"),
                     text_color=theme.pair("accent")).grid(row=3, column=0, pady=(6, 2))
        ctk.CTkLabel(
            self,
            text="谢谢你愿意把每一天的光，交给拾光来收藏。\n愿你在这里拾起的每一缕，都算数。",
            font=theme.font("small"), text_color=theme.pair("text_muted"),
            justify="center",
        ).grid(row=4, column=0, padx=26, pady=(10, 4))
        ctk.CTkLabel(self, text="数据保存在你的电脑里，不上传任何服务器。",
                     font=theme.font("tiny"),
                     text_color=theme.pair("text_done")).grid(row=5, column=0, pady=(2, 0))
        ctk.CTkButton(self, text="好", width=110, height=34, corner_radius=17,
                      fg_color=theme.pair("accent"), hover_color=theme.pair("accent_hover"),
                      text_color=("#FFFFFF", "#2D2A26"), font=theme.font("small"),
                      command=self._cancel).grid(row=6, column=0, pady=(16, 20))
        self.bind("<Escape>", lambda _e: self._cancel())
        self.show()

    @staticmethod
    def _draw_sun(canvas: tk.Canvas, size: int) -> None:
        import math

        canvas.delete("all")
        c = size / 2
        accent = theme.c("accent")
        soft = theme.c("accent_soft")
        orange = theme.c("orange")
        canvas.create_oval(c - 34, c - 34, c + 34, c + 34, fill=soft, outline="")
        canvas.create_oval(c - 24, c - 24, c + 24, c + 24, fill=accent, outline="")
        for i in range(8):
            ang = math.radians(i * 45 + 22.5)
            canvas.create_line(c + math.cos(ang) * 32, c + math.sin(ang) * 32,
                               c + math.cos(ang) * 46, c + math.sin(ang) * 46,
                               fill=orange, width=3, capstyle="round")

    def _spin(self, canvas: tk.Canvas, step: int) -> None:
        """点一下太阳会转一圈（彩蛋，不占功能）。"""
        if step >= 12 or not canvas.winfo_exists():
            return
        canvas.delete("all")
        import math

        c = 55
        shift = step * 7.5
        accent = theme.c("accent")
        soft = theme.c("accent_soft")
        orange = theme.c("orange")
        canvas.create_oval(c - 34, c - 34, c + 34, c + 34, fill=soft, outline="")
        canvas.create_oval(c - 24, c - 24, c + 24, c + 24, fill=accent, outline="")
        for i in range(8):
            ang = math.radians(i * 45 + 22.5 + shift)
            canvas.create_line(c + math.cos(ang) * 32, c + math.sin(ang) * 32,
                               c + math.cos(ang) * 46, c + math.sin(ang) * 46,
                               fill=orange, width=3, capstyle="round")
        self.after(40, lambda: self._spin(canvas, step + 1))


def about(app) -> None:
    AboutDialog(app)
