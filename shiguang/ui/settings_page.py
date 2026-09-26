# -*- coding: utf-8 -*-
"""设置页面：外观 / 窗口 / 快捷键 / 专注 / 数据 / 关于。"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .. import __app_name__, __version__, sound, strings, theme
from . import dialogs, widgets


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.store = app.store
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        widgets.TextLabel(self, text="设置", font=theme.font("title"),
                     text_color=theme.pair("text"), anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(2, 8))

        body = widgets.ThinScrollFrame(self, height=200)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        # 留给截图工具滚动定位用（"通知与特效"卡片在折叠线以下）
        self.body = body

        self._build_appearance(body, 0)
        self._build_window(body, 1)
        self._build_notify(body, 2)
        self._build_focus(body, 3)
        self._build_data(body, 4)
        self._build_about(body, 5)

    # ------------------------------------------------------------------
    def _section(self, parent: tk.Misc, row: int, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, corner_radius=theme.RADIUS_CARD, fg_color=theme.pair("card"),
                            border_width=1, border_color=theme.pair("border"))
        card.grid(row=row, column=0, sticky="ew", pady=(0, theme.lpx(theme.PAGE_GAP)))
        card.grid_columnconfigure(0, weight=1)
        widgets.TextLabel(card, text=title, font=theme.font("h2"),
                     text_color=theme.pair("text")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))
        return card

    def _row_label(self, parent: tk.Misc, row: int, text: str, hint: str = "") -> None:
        holder = ctk.CTkFrame(parent, fg_color="transparent")
        holder.grid(row=row, column=0, sticky="ew", padx=theme.lpx(16))
        holder.grid_columnconfigure(1, weight=1)
        widgets.TextLabel(holder, text=text, font=theme.font("small"),
                     text_color=theme.pair("text")).grid(row=0, column=0, sticky="w")
        if hint:
            # 提示文案必须允许折行并占满剩余宽度：卡片可用宽度在 640 逻辑像素
            # 的窗口下只有 ~350px，不折行的话后半句直接被卡片边缘切掉
            # （截图里"…右键托盘图标可"断在半截就是这个原因）。
            # 用 grid + weight 让标签拿到真实宽度，再用 wraplength 兜底。
            widgets.TextLabel(holder, text=hint, font=theme.font("tiny"),
                         text_color=theme.pair("text_done"),
                         wraplength=200, justify="left",
                         anchor="w").grid(row=1, column=0, columnspan=2,
                                          sticky="w", pady=(theme.lpx(2), 0))

    # ------------------------------------------------------------------
    def _build_appearance(self, parent: tk.Misc, row: int) -> None:
        card = self._section(parent, row, "外观")
        self._row_label(card, 1, "主题")
        seg = ctk.CTkSegmentedButton(
            card, values=["跟随系统", "浅色", "深色"],
            command=self._on_theme,
            font=theme.font("small"), height=26, corner_radius=8,
            fg_color=theme.pair("ghost"),
            selected_color=theme.pair("accent"),
            selected_hover_color=theme.pair("accent_hover"),
            unselected_color=theme.pair("ghost"),
            unselected_hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text"),
        )
        seg.set(theme.MODE_LABEL.get(self.store.settings.get("theme", "system"), "跟随系统"))
        seg.grid(row=2, column=0, sticky="ew", padx=16, pady=(6, 16))

    def _on_theme(self, value: str) -> None:
        mapping = {v: k for k, v in theme.MODE_LABEL.items()}
        self.app.on_setting("theme", mapping.get(value, "system"))

    def _build_window(self, parent: tk.Misc, row: int) -> None:
        """窗口与快捷键（需求 22）。

        这里**只保留**两件东西：全局快捷键 + 关闭时最小化到托盘。
        置顶开关已删除 —— 标题栏右上角是「最小化 / 最大化 / 关闭」三件套，
        不再有图钉按钮；置顶能力移到设置项的 ``always_on_top``，
        同一功能出现两处只会让人怀疑它们状态不同步。
        """
        card = self._section(parent, row, "窗口与快捷键")

        self._row_label(card, 1, "关闭时最小化到托盘", "点关闭只藏起来，右键托盘图标可退出")
        # ---- 开关配色（关键）----
        # 旋钮直接用纯白的话，和卡片（也是纯白）糊在一起 —— 选中态只剩一坨
        # 黄色，看上去像个歪掉的 "C"（截图一眼可见）。1.5.11 起旋钮色收敛到
        # theme.SWITCH_KNOB / SWITCH_KNOB_HOVER（暖奶油 + accent_soft 描边），
        # 本页三个开关共用同一套常量。
        # 所有尺寸/描边都是 CTk 自己缩放的量，一律传**逻辑值**，不能过 lpx()。
        self.tray_switch = ctk.CTkSwitch(
            card, text="", width=42, height=20,
            switch_width=38, switch_height=18, corner_radius=9,
            command=lambda: self.app.on_setting(
                "close_to_tray", bool(self.tray_switch.get())),
            fg_color=theme.pair("ring"),
            progress_color=theme.pair("accent"),
            button_color=theme.SWITCH_KNOB,
            button_hover_color=theme.SWITCH_KNOB_HOVER,
            border_width=1,
            border_color=theme.pair("accent_soft"),
        )
        if self.store.settings.get("close_to_tray", True):
            self.tray_switch.select()
        self.tray_switch.grid(row=2, column=0, sticky="w",
                              padx=theme.lpx(16), pady=(theme.lpx(6), theme.lpx(12)))

        self._row_label(card, 3, "全局快捷键", "随时呼出或隐藏主窗口")
        hotkey_row = ctk.CTkFrame(card, fg_color="transparent")
        hotkey_row.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 4))
        hotkey_row.grid_columnconfigure(0, weight=1)
        self.hotkey_entry = ctk.CTkEntry(
            hotkey_row, font=theme.font("small"), height=26, corner_radius=8,
            fg_color=theme.pair("bg_alt"), border_color=theme.pair("border"),
            border_width=1, text_color=theme.pair("text"))
        self.hotkey_entry.grid(row=0, column=0, sticky="ew")
        self.hotkey_entry.insert(0, self.store.settings.get("hotkey", "Ctrl+Shift+T"))
        # 1.5.12：按下组合键**实时**显示成 "Ctrl+Shift+A" 的形态。
        # 捕获走 <KeyPress> + return "break"（不让字符落进输入框），
        # 修饰键掩码是 Tk 的标准位：Ctrl=0x4 / Shift=0x1 / Alt(Mod1)=0x8 /
        # Win(Mod4)=0x40。纯修饰键按下不更新，避免留下一串 "Ctrl+Ctrl"。
        self.hotkey_entry.bind("<KeyPress>", self._on_hotkey_key)
        self._saved_job = None
        ctk.CTkButton(hotkey_row, text="应用", width=56, height=26, corner_radius=8,
                      fg_color=theme.pair("accent"), hover_color=theme.pair("accent_hover"),
                      text_color=("#FFFFFF", "#2D2A26"), font=theme.font("small"),
                      command=self._apply_hotkey).grid(row=0, column=1, padx=(8, 0))
        self.hotkey_hint = widgets.TextLabel(
            card, text="", font=theme.font("tiny"), text_color=theme.pair("text_muted"),
            anchor="w", justify="left")
        self.hotkey_hint.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 16))
        self._hotkey_status()

    def _hotkey_status(self) -> None:
        hk = getattr(self.app, "hotkey", None)
        if hk is not None and hk.active:
            self.hotkey_hint.configure(text=f"已生效：{hk.current}  ·  支持 Ctrl / Shift / Alt / Win 组合",
                                       text_color=theme.pair("text_muted"))
        else:
            self.hotkey_hint.configure(
                text=strings.HOTKEY_FAIL_HINT,
                text_color=theme.pair("danger"))

    # ------------------------------------------------------------------
    # 快捷键输入（1.5.12：实时捕获 + 保存反馈）
    # ------------------------------------------------------------------
    _HOTKEY_MODS = ((0x0004, "Ctrl"), (0x0001, "Shift"),
                    (0x0008, "Alt"), (0x0040, "Win"))
    _HOTKEY_MOD_KEYS = {"Control_L", "Control_R", "Shift_L", "Shift_R",
                        "Alt_L", "Alt_R", "Meta_L", "Meta_R",
                        "Win_L", "Win_R", "Super_L", "Super_R"}

    def _on_hotkey_key(self, event) -> str:
        """把按下的组合键格式化进输入框（替代自由文本编辑）。"""
        ks = event.keysym or ""
        if ks in self._HOTKEY_MOD_KEYS:
            return "break"
        parts = [name for mask, name in self._HOTKEY_MODS if event.state & mask]
        key = ks if len(ks) > 1 else ks.upper()
        if key and key not in parts:
            parts.append(key)
        if parts:
            self.hotkey_entry.delete(0, "end")
            self.hotkey_entry.insert(0, "+".join(parts))
        return "break"

    def _apply_hotkey(self) -> None:
        value = self.hotkey_entry.get().strip()
        self.app.on_setting("hotkey", value)
        self.store.set_setting("hotkey_enabled", True)
        self.store.save()
        self._hotkey_status()
        hk = getattr(self.app, "hotkey", None)
        if hk is not None and hk.active:
            self._flash_saved()
        self.app.toast(f"快捷键已设置为 {hk.current}" if hk and hk.active
                       else "这个快捷键没能注册成功，换一个试试？")

    def _flash_saved(self) -> None:
        """保存成功反馈：边框柔橘 + "✓ 已保存"，1.5 秒后恢复常规状态。"""
        if self._saved_job is not None:
            try:
                self.after_cancel(self._saved_job)
            except Exception:  # noqa: BLE001
                pass
            self._saved_job = None
        try:
            self.hotkey_entry.configure(border_color=theme.pair("accent"))
            self.hotkey_hint.configure(text=strings.HOTKEY_SAVED_HINT,
                                       text_color=theme.pair("accent"))
        except Exception:  # noqa: BLE001
            return
        self._saved_job = self.after(theme.HOTKEY_SAVED_MS, self._end_saved_flash)

    def _end_saved_flash(self) -> None:
        self._saved_job = None
        try:
            self.hotkey_entry.configure(border_color=theme.pair("border"))
        except Exception:  # noqa: BLE001
            pass
        self._hotkey_status()

    def _build_notify(self, parent: tk.Misc, row: int) -> None:
        """通知子卡片：到期提醒 + 完成特效。"""
        card = self._section(parent, row, "通知与特效")

        self._row_label(card, 1, "到期系统通知", "任务到期时弹出 Windows 通知")
        self.notify_switch = self._switch(
            card, "due_notify",
            lambda: self.app.on_setting("due_notify", bool(self.notify_switch.get())))
        self.notify_switch.grid(row=2, column=0, sticky="w", padx=16, pady=(4, 8))

        self._row_label(card, 3, "逾期界面提醒条", "主页顶部显示逾期 / 今日到期提示")
        self.banner_switch = self._switch(
            card, "due_banner",
            lambda: self.app.on_setting("due_banner", bool(self.banner_switch.get())))
        self.banner_switch.grid(row=4, column=0, sticky="w", padx=16, pady=(4, 8))

        self._row_label(card, 5, "完成粒子特效", "全部完成时迸发暖金粒子")
        self.particle_switch = self._switch(
            card, "due_particles",
            lambda: self.app.on_setting("due_particles", bool(self.particle_switch.get())))
        self.particle_switch.grid(row=6, column=0, sticky="w", padx=16, pady=(4, 8))

        hint = widgets.TextLabel(
            card,
            text="任务到期时将通过系统通知和界面提醒条双重提醒。\n"
                 "每项任务只提醒一次；修改截止时间后会重新提醒。",
            font=theme.font("tiny"), text_color=theme.pair("text_done"),
            anchor="w", justify="left", wraplength=320)
        hint.grid(row=7, column=0, sticky="w", padx=16, pady=(2, 8))

        ctk.CTkButton(
            card, text="发一条测试通知", height=26, corner_radius=13,
            fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
            text_color=theme.pair("text"), font=theme.font("tiny"),
            command=self._test_notify,
        ).grid(row=8, column=0, sticky="ew", padx=16, pady=(0, 16))

    def _switch(self, parent: tk.Misc, key: str, command) -> ctk.CTkSwitch:
        """统一样式的开关。

        两点踩坑记录：
        * 尺寸一律传**逻辑值** —— CTkSwitch 自己做 DPI 缩放，传 lpx() 会双重放大；
        * 旋钮色收敛到 theme.SWITCH_KNOB（暖奶油）—— 不能直接复用 knob（纯白），
          卡片也是纯白，旋钮在卡片上完全隐形，选中态只剩一坨黄色。
        """
        switch = ctk.CTkSwitch(
            parent, text="", width=42, height=20,
            switch_width=38, switch_height=18, corner_radius=9,
            command=command,
            fg_color=theme.pair("ring"),
            progress_color=theme.pair("accent"),
            button_color=theme.SWITCH_KNOB,
            button_hover_color=theme.SWITCH_KNOB_HOVER,
            border_width=1,
            border_color=theme.pair("accent_soft"),
        )
        if self.store.settings.get(key, True):
            switch.select()
        return switch

    def _test_notify(self) -> None:
        ok = self.app._notify("拾光 · 任务到期提醒",
                              "这是一条测试通知，看到它就说明提醒通道是通的")
        self.app.toast("测试通知已发出" if ok else "通知通道不可用（可能被系统关掉了）")

    def _build_focus(self, parent: tk.Misc, row: int) -> None:
        card = self._section(parent, row, "专注")
        self._row_label(card, 1, "专注时长", "分钟")
        # 1.5.11：CTkOptionMenu -> 就地芯片行。原生下拉的展开面板是
        # tkinter.Menu，圆角/边框归系统画、无法定制，是全应用最后一个
        # 原生菜单风格控件。芯片的圆角/配色/字号全部来自 theme 常量，
        # 交互与新建浮层的分组芯片一致（点谁选谁，无浮层无遮挡）。
        values = ["15", "25", "30", "45", "60"]
        current = str(self.store.settings.get("pomodoro_minutes", 25))
        if current not in values:          # 老配置里的自定值也保留可见
            values.append(current)
        self.focus_value = current
        chip_row = ctk.CTkFrame(card, fg_color="transparent")
        chip_row.grid(row=2, column=0, sticky="w", padx=16, pady=(4, 12))
        self.focus_chips: dict[str, ctk.CTkButton] = {}
        for i, value in enumerate(values):
            chip = ctk.CTkButton(
                chip_row, text=value, width=theme.fit_width(
                    value, "small", pad_x=theme.CHOICE_PAD_X),
                height=theme.CHOICE_H, corner_radius=theme.CHOICE_RADIUS,
                fg_color=theme.pair("accent" if value == current else "ghost"),
                hover_color=theme.pair("accent_hover" if value == current
                                       else "ghost_hover"),
                text_color=theme.ON_ACCENT if value == current
                else theme.pair("text"),
                font=theme.font("small"),
                command=lambda v=value: self._pick_focus(v),
            )
            chip.grid(row=0, column=i, padx=(0 if i == 0 else theme.CHOICE_GAP // 2,
                                              theme.CHOICE_GAP // 2))
            self.focus_chips[value] = chip

        self._row_label(card, 3, "结束提示音")
        sound_row = ctk.CTkFrame(card, fg_color="transparent")
        sound_row.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 16))
        self.sound_switch = ctk.CTkSwitch(
            sound_row, text="", command=lambda: self._toggle_sound(),
            fg_color=theme.pair("ring"), progress_color=theme.pair("accent"),
            button_color=theme.SWITCH_KNOB,
            button_hover_color=theme.SWITCH_KNOB_HOVER,
            border_width=1, border_color=theme.pair("accent_soft"),
            width=44, height=20, switch_width=38, switch_height=18,
            corner_radius=9,
        )
        if self.store.settings.get("sound_enabled", True):
            self.sound_switch.select()
        self.sound_switch.pack(side="left")
        ctk.CTkButton(sound_row, text="试听", width=60, height=26, corner_radius=13,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text_muted"), font=theme.font("tiny"),
                      command=lambda: sound.play("chime", True)).pack(side="left", padx=(12, 0))

    def _pick_focus(self, value: str) -> None:
        """芯片点选：只重刷这一排芯片的选中态，不动整页。"""
        if value == self.focus_value:
            return
        self.focus_value = value
        self.app.on_setting("pomodoro_minutes", int(value))
        for v, chip in self.focus_chips.items():
            selected = v == value
            chip.configure(
                fg_color=theme.pair("accent" if selected else "ghost"),
                hover_color=theme.pair("accent_hover" if selected else "ghost_hover"),
                text_color=theme.ON_ACCENT if selected else theme.pair("text"),
            )

    def _toggle_sound(self) -> None:
        enabled = bool(self.sound_switch.get())
        self.app.on_setting("sound_enabled", enabled)
        if enabled:
            sound.play("chime", True)

    def _build_data(self, parent: tk.Misc, row: int) -> None:
        from ..config import data_dir

        card = self._section(parent, row, "数据")
        # 1.5.12：不再把完整绝对路径（C:\Users\...\data）拍在普通用户脸上，
        # 平时只显示 "父目录/目录名"；悬停 600ms 后临时换成完整路径
        # （轻量 tooltip，不引入浮层），移开即恢复。
        path = data_dir()
        full = str(path)
        try:
            short = f"{path.parent.name}/{path.name}"
        except Exception:  # noqa: BLE001
            short = full
        self._path_full, self._path_short = full, short
        self._path_job = None
        self._path_label = widgets.TextLabel(card, text=short, font=theme.font("tiny"),
                                             text_color=theme.pair("text_muted"),
                                             anchor="w", cursor="hand2")
        self._path_label.grid(row=1, column=0, sticky="w", padx=16, pady=(2, 8))
        for w in (self._path_label, getattr(self._path_label, "_canvas", None)):
            if w is not None:
                w.bind("<Enter>", self._path_hover_on, add="+")
                w.bind("<Leave>", self._path_hover_off, add="+")

        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        for i in range(2):
            row1.grid_columnconfigure(i, weight=1)
        ctk.CTkButton(row1, text="打开文件夹", height=26, corner_radius=13,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text"), font=theme.font("small"),
                      command=self._open_data_dir).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(row1, text="导出 CSV", height=26, corner_radius=13,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text"), font=theme.font("small"),
                      command=self._export_csv).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 8))
        for i in range(2):
            row2.grid_columnconfigure(i, weight=1)
        ctk.CTkButton(row2, text="备份数据", height=26, corner_radius=13,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text"), font=theme.font("small"),
                      command=self._backup).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        # 1.5.11：补上"从备份恢复"，备份/恢复才是一个完整闭环
        ctk.CTkButton(row2, text="从备份恢复", height=26, corner_radius=13,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text"), font=theme.font("small"),
                      command=self._restore).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ctk.CTkButton(card, text="清空已完成", height=26, corner_radius=13,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text_muted"), font=theme.font("small"),
                      command=self._clear_done).grid(row=4, column=0, sticky="ew",
                                                     padx=16, pady=(0, 8))

        ctk.CTkButton(card, text="重新观看新手引导", height=26, corner_radius=13,
                      fg_color=theme.pair("ghost"), hover_color=theme.pair("ghost_hover"),
                      text_color=theme.pair("text_muted"), font=theme.font("small"),
                      command=self._replay_onboarding).grid(
            row=5, column=0, sticky="ew", padx=16, pady=(0, 16))

    # ------------------------------------------------------------------
    # 数据目录路径：悬停显示完整路径（1.5.12）
    # ------------------------------------------------------------------
    def _path_hover_on(self, _event=None) -> None:
        if getattr(self, "_path_job", None) is not None:
            return
        try:
            self._path_job = self.after(600, self._path_show_full)
        except Exception:  # noqa: BLE001
            self._path_job = None

    def _path_show_full(self) -> None:
        self._path_job = None
        try:
            self._path_label.configure(text=self._path_full)
        except Exception:  # noqa: BLE001
            pass

    def _path_hover_off(self, _event=None) -> None:
        if getattr(self, "_path_job", None) is not None:
            try:
                self.after_cancel(self._path_job)
            except Exception:  # noqa: BLE001
                pass
            self._path_job = None
        try:
            self._path_label.configure(text=self._path_short)
        except Exception:  # noqa: BLE001
            pass

    def _open_data_dir(self) -> None:
        from ..config import data_dir

        path = str(data_dir())
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"打开失败：{exc}")

    def _export_csv(self) -> None:
        # 1.5.11：实现收敛到 dialogs.export_tasks_csv，与光景页共用同一入口
        dialogs.export_tasks_csv(self.app)

    def _restore(self) -> None:
        """从备份恢复：选文件 -> store.import_json 校验+覆盖 -> 刷新界面。"""
        path = filedialog.askopenfilename(
            parent=self.app, title="从备份恢复",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            count = self.store.import_json(Path(path))
        except Exception as exc:  # noqa: BLE001 - 校验失败文案直接给用户
            self.app.toast(f"恢复失败：{exc}")
            return
        # 备份里可能带着不同的主题/设置，按恢复后的配置重新应用一遍
        try:
            self.app.on_setting("theme", self.store.settings.get("theme", "system"))
        except Exception:  # noqa: BLE001
            pass
        self.app.refresh()
        self.app.toast(f"已从备份恢复 {count} 条任务")

    def _backup(self) -> None:
        import datetime as _dt

        path = filedialog.asksaveasfilename(
            parent=self.app, title="备份数据",
            initialfile=f"拾光-备份-{_dt.date.today().isoformat()}.json",
            defaultextension=".json", filetypes=[("JSON 文件", "*.json")])
        if not path:
            return
        try:
            self.store.export_json(path)
            self.app.toast("数据已备份")
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"备份失败：{exc}")

    def _clear_done(self) -> None:
        done = [t for t in self.store.all_tasks() if t.done]
        if not done:
            self.app.toast("还没有已完成的任务")
            return

        def on_ok() -> None:
            count = self.store.clear_completed()
            self.store.save()
            self.app.refresh()
            self.app.toast(f"已清空 {count} 条已完成任务（历史统计保留）")

        dialogs.confirm(self.app, "清空已完成",
                        f"将删除 {len(done)} 条已完成任务，历史统计不受影响。",
                        on_ok=on_ok, ok_text="清空", danger=True)

    def _replay_onboarding(self) -> None:
        self.app.store.set_setting("onboarded", False)
        self.app.show_onboarding()

    def _build_about(self, parent: tk.Misc, row: int) -> None:
        card = self._section(parent, row, "关于")
        ctk.CTkButton(card, text=f"关于{__app_name__} · v{__version__}",
                      height=28, corner_radius=14,
                      fg_color=theme.pair("accent_faint"),
                      hover_color=theme.pair("accent_soft"),
                      text_color=theme.pair("text"), font=theme.font("small"),
                      command=lambda: dialogs.about(self.app)).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(2, 6))
        widgets.TextLabel(card, text="拾起每一天，不负好时光",
                     font=theme.font("slogan"),
                     text_color=theme.pair("accent")).grid(
            row=2, column=0, pady=(0, 16))
