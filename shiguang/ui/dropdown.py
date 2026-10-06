# -*- coding: utf-8 -*-
"""圆角暖色的应用内下拉选择器。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Sequence

import customtkinter as ctk

from .. import icons, theme
from .widgets import bind_hover
from .window_shape import schedule_rounded_region


_ACTIVE_DROPDOWN = None


class InAppDropdown(ctk.CTkFrame):
    """始终限制在所属窗口内的下拉选择器。

    选项面板是所属 Toplevel 的普通子控件，通过 ``place`` 覆盖在页面上，
    不创建额外窗口、不调用系统菜单。坐标会按当前缩放换算并钳制在客户区内；
    窄窗口或选项较多时，面板会向空间更充足的一侧展开并启用滚动。
    """

    def __init__(self, master: tk.Misc, values: Sequence[str],
                 command: Optional[Callable[[str], None]] = None,
                 title: str = "选择", width: int = 150, height: int = 32,
                 min_popup_width: int = 170, anchor: str = "w",
                 value_icons: Optional[Sequence[str]] = None) -> None:
        super().__init__(master, width=width, height=height,
                         corner_radius=12, border_width=1,
                         fg_color=theme.pair("ghost"),
                         border_color=theme.pair("control_border"))
        self._values = [str(value) for value in values] or ["默认"]
        self._value = self._values[0]
        self._selected_index = 0
        self._value_icons = list(value_icons or [])
        if len(self._value_icons) < len(self._values):
            self._value_icons.extend([""] * (len(self._values) - len(self._value_icons)))
        self._command = command
        self._title = title
        self._min_popup_width = max(120, int(min_popup_width))
        self._anchor = anchor
        self._top = self.winfo_toplevel()
        self._popup = None
        self._popup_bindings = []
        self._destroying = False

        self.grid_propagate(False)
        self._label_col = 1 if self._value_icons and self._value_icons[0] else 0
        self._arrow_col = self._label_col + 1
        self.grid_columnconfigure(self._label_col, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._value_icon_image = None
        self._value_icon_label = None
        if self._label_col:
            self._value_icon_image = self._icon_image(self._value_icons[0])
            self._value_icon_label = ctk.CTkLabel(
                self, text="", image=self._value_icon_image,
                width=20,
            )
            self._value_icon_label.grid(row=0, column=0, sticky="ns",
                                        padx=(8, 2), pady=2)
        self._label = ctk.CTkLabel(
            self, text=self._value, font=theme.font("small"),
            text_color=theme.pair("text"), anchor=anchor,
        )
        self._label.grid(row=0, column=self._label_col, sticky="nsew",
                         padx=((10 if self._label_col == 0 else 0), 6),
                         pady=2)

        self._arrow_image = icons.get_ctk("chevron", icons.SIZE_SMALL)
        # 箭头直接置于连续的圆角触发区上。旧的内嵌 arrow_box 有自己的圆角和
        # 2px 上下留白，缩放/高 DPI 下会露出两道浅色条，像是右边缺了一块。
        arrow = ctk.CTkLabel(self, text="", image=self._arrow_image,
                             width=26, fg_color="transparent")
        arrow.grid(row=0, column=self._arrow_col, sticky="ns",
                   padx=(0, 4), pady=2)

        self._trigger_parts = [self, self._label, arrow]
        if self._value_icon_label is not None:
            self._trigger_parts.append(self._value_icon_label)
        for widget in self._trigger_parts:
            widget.bind("<Button-1>", self._on_trigger, add="+")
            widget.bind("<Escape>", self._on_escape, add="+")
            widget.bind("<Down>", self._on_open_key, add="+")
            widget.bind("<Return>", self._on_open_key, add="+")
            widget.bind("<space>", self._on_open_key, add="+")
        bind_hover(self, self._hover_on, self._hover_off)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def get(self) -> str:
        """返回当前展示值，兼容 ``CTkOptionMenu.get()``。"""
        return self._value

    def get_index(self) -> int:
        """返回选中项序号，允许重复显示名的分组选项保留各自 ID。"""
        return self._selected_index

    def set(self, value: str) -> None:
        """更新当前值，不触发选择回调。"""
        value = str(value)
        if value not in self._values:
            self._values.append(value)
            self._value_icons.append("")
        self._selected_index = self._values.index(value)
        self._value = value
        try:
            self._label.configure(text=value)
        except Exception:  # noqa: BLE001
            pass
        self._sync_value_icon()

    def set_index(self, index: int) -> None:
        """按序号选中，分组名称重复时仍可精确定位。"""
        if not self._values:
            return
        self._selected_index = max(0, min(int(index), len(self._values) - 1))
        self._value = self._values[self._selected_index]
        try:
            self._label.configure(text=self._value)
        except Exception:  # noqa: BLE001
            pass
        self._sync_value_icon()

    def _icon_image(self, icon_key: str):
        key = icons.resolve_key(icon_key) if icon_key else None
        return icons.get_ctk(key or "grp_life", icons.SIZE_GROUP)

    def _sync_value_icon(self) -> None:
        if self._value_icon_label is None:
            return
        icon_key = (self._value_icons[self._selected_index]
                    if self._selected_index < len(self._value_icons) else "")
        self._value_icon_image = self._icon_image(icon_key)
        try:
            self._value_icon_label.configure(image=self._value_icon_image)
        except Exception:  # noqa: BLE001
            pass

    def _hover_on(self) -> None:
        try:
            self.configure(fg_color=theme.pair("ghost_hover"))
        except Exception:  # noqa: BLE001
            pass

    def _hover_off(self) -> None:
        try:
            self.configure(fg_color=theme.pair("ghost"))
        except Exception:  # noqa: BLE001
            pass

    def _on_trigger(self, _event=None) -> str:
        try:
            self.focus_set()
        except Exception:  # noqa: BLE001
            pass
        self.toggle()
        return "break"

    def _on_open_key(self, _event=None) -> str:
        if self._popup is None:
            self.open()
        return "break"

    def _on_escape(self, _event=None) -> str:
        self.close()
        return "break"

    def toggle(self) -> None:
        if self._popup is None:
            self.open()
        else:
            self.close()

    def open(self) -> None:
        """显示选项，并确保完整落在所属窗口客户区内。"""
        global _ACTIVE_DROPDOWN
        if self._popup is not None:
            return
        if _ACTIVE_DROPDOWN is not None and _ACTIVE_DROPDOWN is not self:
            _ACTIVE_DROPDOWN.close()
        try:
            self._top.update_idletasks()
            scale = max(0.5, float(theme.scale() or 1.0))
            top_w = max(1, int(round(self._top.winfo_width() / scale)))
            top_h = max(1, int(round(self._top.winfo_height() / scale)))
            left = int(round((self.winfo_rootx() - self._top.winfo_rootx()) / scale))
            top = int(round((self.winfo_rooty() - self._top.winfo_rooty()) / scale))
            trigger_w = max(1, int(round(self.winfo_width() / scale)))
            trigger_h = max(1, int(round(self.winfo_height() / scale)))
        except Exception:  # noqa: BLE001
            return

        row_h = 30
        row_pitch = row_h + 2
        chrome_h = 48
        visible_items = min(len(self._values), 7)
        desired_h = chrome_h + visible_items * row_pitch
        desired_w = max(trigger_w, self._min_popup_width)
        gap = 4
        margin = 4
        below_y = top + trigger_h + gap
        above_y = top - desired_h - gap
        room_below = max(0, top_h - below_y - margin)
        room_above = max(0, top - gap - margin)

        if desired_h <= room_below:
            y, popup_h = below_y, desired_h
        elif desired_h <= room_above:
            y, popup_h = above_y, desired_h
        elif room_below >= room_above:
            y, popup_h = below_y, max(88, room_below)
        else:
            popup_h = max(88, room_above)
            y = max(margin, top - popup_h - gap)
        popup_h = min(popup_h, max(88, top_h - margin * 2))
        popup_w = min(desired_w, max(120, top_w - margin * 2))
        x = left + trigger_w - popup_w
        x = max(margin, min(x, top_w - popup_w - margin))

        popup = ctk.CTkFrame(
            self._top, width=popup_w, height=popup_h,
            corner_radius=12, border_width=1,
            fg_color=theme.pair("card"), bg_color=theme.pair("bg"),
            border_color=theme.pair("window_border"),
        )
        popup.grid_propagate(False)
        popup.grid_columnconfigure(0, weight=1)
        self._popup = popup

        heading = ctk.CTkLabel(
            popup, text=self._title, font=theme.font("tiny"),
            text_color=theme.pair("text_muted"), anchor="w",
        )
        heading.grid(row=0, column=0, sticky="ew",
                     padx=12, pady=(8, 5))
        divider = ctk.CTkFrame(popup, height=1, fg_color=theme.pair("border"),
                               corner_radius=0)
        divider.grid(row=1, column=0, sticky="ew", padx=9)

        list_height = max(42, popup_h - chrome_h)
        use_scroll = (len(self._values) > visible_items
                      or len(self._values) * row_pitch > list_height)
        if use_scroll:
            option_parent = ctk.CTkScrollableFrame(
                popup, height=list_height, fg_color="transparent", corner_radius=0,
                scrollbar_button_color=theme.pair("accent_faint"),
                scrollbar_button_hover_color=theme.pair("ghost_hover"),
            )
            option_parent.grid(row=2, column=0, sticky="nsew",
                               padx=4, pady=(4, 6))
            option_parent.grid_columnconfigure(0, weight=1)
        else:
            option_parent = ctk.CTkFrame(popup, fg_color="transparent",
                                         corner_radius=0)
            option_parent.grid(row=2, column=0, sticky="nsew",
                               padx=4, pady=(4, 6))
            option_parent.grid_columnconfigure(0, weight=1)
        popup.grid_rowconfigure(2, weight=1)

        selected_index = self.get_index()
        for index, value in enumerate(self._values):
            selected = index == selected_index
            face = ctk.CTkFrame(
                option_parent, height=row_h, corner_radius=9,
                fg_color=theme.pair("accent_faint") if selected else "transparent",
            )
            face.grid(row=index, column=0, sticky="ew",
                      padx=2, pady=1)
            face.grid_propagate(False)
            has_icon = (index < len(self._value_icons)
                        and bool(self._value_icons[index]))
            label_col = 1 if has_icon else 0
            check_col = label_col + 1
            face.grid_columnconfigure(label_col, weight=1)
            if has_icon:
                option_icon = ctk.CTkLabel(
                    face, text="", image=self._icon_image(self._value_icons[index]),
                    width=20,
                )
                option_icon.grid(row=0, column=0, sticky="ns",
                                 padx=(8, 2))
            label = ctk.CTkLabel(
                face, text=value, font=theme.font("small"),
                text_color=theme.pair("text"), anchor="w",
            )
            label.grid(row=0, column=label_col, sticky="nsew",
                       padx=(9, 4))
            if selected:
                check = ctk.CTkLabel(
                    face, text="✓", width=22,
                    font=theme.font("small_bold"),
                    text_color=theme.pair("accent"), anchor="center",
                )
                check.grid(row=0, column=check_col, sticky="e",
                           padx=(0, 4))
            for child in (face, *face.winfo_children()):
                child.bind("<Button-1>",
                           lambda event, choice_index=index:
                           self._choose_index(choice_index), add="+")
                child.bind("<Escape>", self._on_escape, add="+")
            bind_hover(
                face,
                lambda f=face, i=index: f.configure(
                    fg_color=(theme.pair("accent_soft") if i == self._selected_index
                              else theme.pair("ghost_hover"))),
                lambda f=face, active=selected: f.configure(
                    fg_color=(theme.pair("accent_faint") if active else "transparent")),
            )

        popup.place(x=x, y=y)
        popup.update_idletasks()
        # CTkFrame 的四角虽画成圆形，子窗口自身仍占矩形，会盖住下方控件。
        # 裁剪它自己的 HWND，圆角外才真正露出底下的界面。
        schedule_rounded_region(popup, theme.lpx(12), native_root=False)
        try:
            popup.lift()
        except Exception:  # noqa: BLE001
            pass
        self._bind_outside_clicks()
        _ACTIVE_DROPDOWN = self

    def _bind_outside_clicks(self) -> None:
        """以一组全局 Tk 事件绑定负责点外关闭，并可精确解除。"""
        self._popup_bindings = []
        for sequence, callback in (("<Button-1>", self._outside_click),
                                   ("<Escape>", self._on_escape)):
            try:
                funcid = tk.Misc.bind_all(self._top, sequence, callback, add="+")
                self._popup_bindings.append((sequence, funcid))
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _is_inside(widget, ancestor) -> bool:
        while widget is not None:
            if widget is ancestor:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _outside_click(self, event) -> None:
        if self._popup is None:
            return
        if (self._is_inside(event.widget, self)
                or self._is_inside(event.widget, self._popup)):
            return
        self.close()

    def _choose_index(self, index: int) -> None:
        self.set_index(index)
        self.close()
        if self._command is not None:
            self._command(self._value)

    def close(self) -> None:
        global _ACTIVE_DROPDOWN
        popup, self._popup = self._popup, None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:  # noqa: BLE001
                pass
        for sequence, funcid in self._popup_bindings:
            if not funcid:
                continue
            try:
                tk.Misc._unbind(self._top, ("bind", "all", sequence), funcid)
            except Exception:  # noqa: BLE001
                pass
        self._popup_bindings = []
        if _ACTIVE_DROPDOWN is self:
            _ACTIVE_DROPDOWN = None

    def _on_destroy(self, event) -> None:
        if event.widget is self and not self._destroying:
            self._destroying = True
            self.close()
