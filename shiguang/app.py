# -*- coding: utf-8 -*-
"""拾光主窗口。

职责
----
* 组装窗口外壳（头部 / 页面容器 / 底部导航 / 浮层）；
* 持有 Store、Pomodoro、托盘、全局快捷键这些单例服务；
* 跨线程安全：托盘与热键的回调都在子线程，统一塞进 ``self._queue``，
  由主线程 ``_pump`` 每 120ms 消费一次，绝不在子线程里碰 Tk 控件。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from . import (__app_name__, __version__, assets_gen, icons, notify,
               sound, stats, strings, theme)
from .assets_gen import ensure_icon
from .config import data_dir, log_file
from .hotkey import GlobalHotkey
from .pomodoro import Pomodoro
from .reminder import ReminderService
from .store import Store
from .tray import Tray
from .ui import dialogs, menu as ui_menu, widgets
from .ui.onboarding import OnboardingOverlay
from .ui.particles import ParticleLayer
from .ui.settings_page import SettingsPage
from .ui.stats_page import StatsPage
from .ui.task_page import TaskPage

WEEKDAY_FULL = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
# 顶部行用的是**缩写**：完整"星期四"在 344 宽的窗口里会把切换器挤到边上，
# 而"周四"两个字已经足够表意。
WEEKDAY_SHORT = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
NAV_LABELS = {"tasks": "任务", "stats": "光景", "settings": "设置"}

log = logging.getLogger("shiguang.app")

# Windows 矩形结构：窗口位置校正的多个方法都要用，抽到模块级避免重复定义
if sys.platform.startswith("win"):
    import ctypes as _ctypes

    class _RECT(_ctypes.Structure):
        _fields_ = [("left", _ctypes.c_long), ("top", _ctypes.c_long),
                    ("right", _ctypes.c_long), ("bottom", _ctypes.c_long)]

    class _POINT(_ctypes.Structure):
        _fields_ = [("x", _ctypes.c_long), ("y", _ctypes.c_long)]

    class _MONITORINFO(_ctypes.Structure):
        _fields_ = [("cbSize", _ctypes.c_ulong), ("rcMonitor", _RECT),
                    ("rcWork", _RECT), ("dwFlags", _ctypes.c_ulong)]

    SPI_GETWORKAREA = 0x0030
    MONITOR_DEFAULTTONEAREST = 0x00000002
else:  # pragma: no cover - 仅为非 Windows 平台占位
    _RECT = _POINT = _MONITORINFO = None  # type: ignore[assignment]
    SPI_GETWORKAREA = 0
    MONITOR_DEFAULTTONEAREST = 0


def _setup_logging() -> None:
    try:
        logging.basicConfig(
            filename=str(log_file()), level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=logging.INFO)


class ShiguangApp(ctk.CTk):
    """应用主窗口。"""

    DEFAULT_SIZE = (theme.DEFAULT_WINDOW_W, theme.DEFAULT_WINDOW_H)
    # 最小尺寸：**逻辑像素**，与 theme.MIN_WINDOW_* 同源。
    # 380×600 是"三张卡片 + 药丸导航 + 标题栏"都还站得住的下限；
    # minsize 之外，手工缩放的热区判定（_resize_zone）也用同一组数字兜底。
    MIN_SIZE = (theme.MIN_WINDOW_W, theme.MIN_WINDOW_H)

    # 托盘单击去抖：pystray 的 Windows 后端每次 WM_LBUTTONUP 都会激活 default 项，
    # 双击会连发两次 toggle，不去抖的话窗口会"闪一下又没了"（需求 5：双击 = 同单击）
    TOGGLE_DEBOUNCE = 0.35

    def __init__(self) -> None:
        super().__init__()

        _setup_logging()

        # ---------- 数据与服务 ----------
        self.store = Store()
        settings = self.store.settings
        ctk.set_appearance_mode(theme.MODE_MAP.get(settings.get("theme", "system"), "System"))
        # 字体体系：探测字体栈 + 读 DPI 缩放（必须在建任何控件之前，字号才一致）
        self.font_info = theme.init_fonts(self, settings.get("font_family", ""))
        self._last_dark = theme.is_dark()

        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._closing = False
        self._hinted_tray = False
        # 显式记录"窗口是否被藏到托盘"。
        # 为什么不查 winfo_viewable()：托盘回调触发时焦点在托盘上，
        # 此时窗口明明可见，winfo_viewable() 却是 0 —— 用状态推断会让 toggle 永远只"显示"不"隐藏"。
        self._hidden = False
        self._last_toggle = 0.0
        self._last_celebrate = 0.0
        self._save_warning_job: Optional[str] = None
        self._pump_job: Optional[str] = None
        self.page: Optional[ctk.CTkFrame] = None
        self.current_page = "tasks"
        self.onboarding: Optional[OnboardingOverlay] = None
        self._celebration = None

        self.pomodoro = Pomodoro(self, self._on_pomodoro_tick, self._on_pomodoro_finish)
        self.hotkey = GlobalHotkey(lambda: self._queue.put(("hotkey_toggle", None)))
        self.tray: Optional[Tray] = None
        self.reminder: Optional[ReminderService] = None

        # ---------- 界面 ----------
        self._setup_window()
        self._build_chrome()
        self._build_overlays()
        self.show_page("tasks", remember=False)

        # ---------- 后台服务 ----------
        self._start_hotkey()
        self._start_tray()
        self._start_reminder()
        self.sync_tray()

        self._pump()

        # 启动时把窗口推到最前（1.5.1 修复"启动后窗口落在最底层、被别的软件压住"）。
        # 必须排在 __init__ 之后：那时 mainloop 还没跑，窗口尚未映射，
        # Windows 的前台锁一定拒绝。80ms 后窗口已映射，AttachThreadInput 才抢得动。
        # 再补两拍（500ms / 1100ms）：用户此刻前台是别的软件，头一次失败很正常。
        self.after(80, self._activate_on_start)
        self.after(500, self._activate_retry, 1)

        if not settings.get("onboarded", False):
            self.after(400, self.show_onboarding)
        if self.store.load_warnings:
            self.after(1100, self._show_data_recovery_notice)

    # ==================================================================
    # 窗口
    # ==================================================================
    def _work_area(self, hwnd: int = 0):
        """返回可用区域 ``(left, top, right, bottom)``，单位是物理像素。

        Windows 上优先取**窗口所在显示器**的可用区（已排除任务栏），
        拿不到时退化为主显示器的 SPI_GETWORKAREA，再退化到整屏。

        为什么不用 SPI_GETWORKAREA 一步到位：它只返回**主显示器**的可用区。
        窗口在副屏时会被强行拉回主屏 —— 而"多显示器下窗口跑到别的屏幕上"
        正是这次要一并解决的场景。
        """
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        if not sys.platform.startswith("win"):
            return 0, 0, screen_w, screen_h

        user32 = _ctypes.windll.user32
        try:
            hwnd = hwnd or self._hwnd()
            if hwnd:
                monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                if monitor:
                    info = _MONITORINFO()
                    info.cbSize = _ctypes.sizeof(_MONITORINFO)
                    if user32.GetMonitorInfoW(monitor, _ctypes.byref(info)):
                        work = info.rcWork
                        if work.right > work.left and work.bottom > work.top:
                            return work.left, work.top, work.right, work.bottom
        except Exception:  # noqa: BLE001
            pass

        try:
            rect = _RECT()
            if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, _ctypes.byref(rect), 0):
                if rect.right > rect.left and rect.bottom > rect.top:
                    return rect.left, rect.top, rect.right, rect.bottom
        except Exception:  # noqa: BLE001
            pass
        # 最后的兜底也必须留在**物理像素**这一侧。原先这里返回 winfo_screenwidth()，
        # 而那是 Tk 的（可能仍是未感知态的）屏幕尺寸，和上面两个 API 的物理像素
        # 混在一起会让所有几何比较失真。用 GetSystemMetrics 保持一致的口径。
        try:
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            if sw > 0 and sh > 0:
                return 0, 0, sw, sh
        except Exception:  # noqa: BLE001
            pass
        return 0, 0, screen_w, screen_h

    def _dpi_scale(self) -> float:
        """逻辑像素 → 物理像素的倍率。**唯一来源是 ``theme.scale()``**。

        ⚠️ 不要用 ``winfo_fpixels("1i") / 96``（本轮踩过的真坑）
        -----------------------------------------------------
        ``fonts.init()`` 会把 ``tk scaling`` 主动锁成 **1.0**（防止"我们乘一次、
        Tk 再乘一次"的平方缩放，见 fonts.py 顶部说明）。而 ``winfo_fpixels("1i")``
        算的是"1 英寸 = 多少像素"，它直接取决于 ``tk scaling``：

        * 未锁时（150% 屏）：``tk scaling = 1.5`` → 返回 144 → 除以 96 = 1.5 ✔
        * **锁定后**：``tk scaling = 1.0`` → 返回 **72** → 除以 96 = **0.75** ✘

        于是 150% 屏上这里恒得 0.75，而真实倍率是 1.5。所有"物理 ÷ 这个假倍率
        → 交给 ctk 再 × 1.5"的地方都会**净放大一倍**：``_ensure_on_screen`` 把
        660×1007 的窗口回写成 ``880x1343``，ctk 再乘 1.5 得到 1320×2014 —— 比屏幕
        还大，且被 ``<Configure>`` 存进 config.json，从此每次启动都这么大。
        """
        try:
            value = float(theme.scale())
        except Exception:  # noqa: BLE001
            value = 1.0
        return value if value > 0 else 1.0

    def _hwnd(self) -> int:
        """主窗口的真实 HWND（ctk 的 winfo_id 返回的是内层窗口，要取父级）。"""
        if not sys.platform.startswith("win"):
            return 0
        try:
            user32 = _ctypes.windll.user32
            return user32.GetParent(self.winfo_id()) or self.winfo_id()
        except Exception:  # noqa: BLE001
            return 0

    def _window_rect(self):
        """窗口真实矩形 ``(left, top, right, bottom)``（物理像素），失败返回 None。"""
        if not sys.platform.startswith("win"):
            return None
        hwnd = self._hwnd()
        if not hwnd:
            return None
        try:
            frame = _RECT()
            _ctypes.windll.user32.GetWindowRect(hwnd, _ctypes.byref(frame))
            return frame.left, frame.top, frame.right, frame.bottom
        except Exception:  # noqa: BLE001
            return None

    def _geometry_guard(self) -> None:
        """恢复窗口前校验位置：显示器拔插 / 缩放变化后，原坐标可能已在可见区外。

        与 ``_ensure_on_screen`` 的区别：那个负责"微调挪进来"（保留用户的位置偏好），
        这个负责"彻底不可见时重置到默认位置"（否则用户找不到窗口）。
        """
        if self._closing:
            return
        rect = self._window_rect()
        if rect is None:
            return
        left, top, right, bottom = self._work_area()
        l, t, r, b = rect
        # 至少要有一角明显落在可用区内，否则视为"不可见"
        visible_x = min(r, right) - max(l, left)
        visible_y = min(b, bottom) - max(t, top)
        if visible_x >= 80 and visible_y >= 60:
            return
        self.store.log(f"窗口矩形 {rect} 已不在可用区，重置位置")
        try:
            self.geometry(self._initial_geometry())
            self.update_idletasks()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"重置窗口位置失败：{exc}")

    def _ensure_on_screen(self) -> None:
        """窗口映射后自校正：超出可用区就压回去，落在区外就整体挪回来。

        为什么不能只靠算：窗口装饰有几十像素开销，150% 缩放下 700 逻辑高度
        ≈ 1050 物理像素，**超过本机的可用高度**，"算出来的位置"必然有一部分
        落在屏幕外。这里以**真实窗口矩形**为准做修正。

        为什么全程只用 ``SetWindowPos``、不碰 ``self.geometry()``
        ---------------------------------------------------------
        CTk 的 ``geometry()`` 对宽高会**再乘一遍** ``window_scaling``，而坐标原样
        使用。只要调用方自己动过一次缩放（哪怕只是除法），就会叠加成平方缩放。
        本轮就是这么踩的：``_dpi_scale()`` 曾经错算成 0.75，这里把 660 物理宽
        除以 0.75 得 880，ctk 再乘 1.5 → **1320**，窗口一夜之间比屏幕还大，
        还被 ``<Configure>`` 写进存档。

        现在统一在**物理像素**里干活：读 ``GetWindowRect``、写 ``SetWindowPos``，
        中间不做任何逻辑/物理换算，ctk 的缩放逻辑也就无从介入。
        """
        if not sys.platform.startswith("win") or self._closing:
            return
        try:
            user32 = _ctypes.windll.user32
            hwnd = self._hwnd()
            if not hwnd:
                return
            rect = self._window_rect()
            if rect is None:
                return
            left, top, right, bottom = self._work_area(hwnd)
            avail_w = max(1, right - left)
            avail_h = max(1, bottom - top)

            l, t, r, b = rect
            w, h = r - l, b - t
            margin = theme.lpx(8)
            min_w = theme.lpx(theme.MIN_WINDOW_W)
            min_h = theme.lpx(theme.MIN_WINDOW_H)

            # 1) 尺寸：只压"比可用区还大"的部分，下限是最小尺寸
            new_w = max(min_w, min(w, max(min_w, avail_w - margin)))
            new_h = max(min_h, min(h, max(min_h, avail_h - margin)))

            # 2) 位置：按**新尺寸**重新夹进可用区（右/下越界优先，再兜左/上）
            new_l, new_t = l, t
            if new_l + new_w > right:
                new_l = right - new_w
            if new_l < left:
                new_l = left
            if new_t + new_h > bottom:
                new_t = bottom - new_h
            if new_t < top:
                new_t = top

            if (new_w, new_h) == (w, h) and (new_l, new_t) == (l, t):
                return                      # 已经在可用区里，不必打扰窗口

            SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
            user32.SetWindowPos(hwnd, 0, int(new_l), int(new_t),
                                int(new_w), int(new_h),
                                SWP_NOZORDER | SWP_NOACTIVATE)
            self.update_idletasks()
            self.store.log(f"窗口自校正 {w}x{h}+{l}+{t} -> "
                           f"{new_w}x{new_h}+{new_l}+{new_t}")
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"窗口自校正失败：{exc}")

    def _initial_geometry(self) -> str:
        """计算初始窗口几何：逻辑 344×470（`theme.DEFAULT_WINDOW_*`），**居中**屏幕。

        坑（真踩过）：``winfo_screenwidth()`` 在 DPI 感知进程里返回的是**物理像素**，
        而 customtkinter 的 ``geometry()`` 会把尺寸按缩放倍率放大、却**原样使用**
        ``+x+y``。两边混用就会出现"窗口一半跑到屏幕外"。
        所以这里统一用物理像素算位置，尺寸再除回逻辑像素交给 ctk。
        """
        scale = self._dpi_scale()

        left, top, right, bottom = self._work_area()
        avail_w = max(200, right - left)
        avail_h = max(200, bottom - top)

        # 先按可用区域限制逻辑尺寸，避免在小屏 + 高缩放下窗口放不下。
        # 注意：344×470 是"设计默认值"，不是"一定要这么大"。
        # 下限用 MIN_WINDOW_*：夹出来的尺寸比最小尺寸还小的话，
        # minsize() 会把它顶回去，中间白闪一下。
        width = min(self.DEFAULT_SIZE[0],
                    max(float(theme.MIN_WINDOW_W), (avail_w - 40) / scale))
        height = min(self.DEFAULT_SIZE[1],
                     max(float(theme.MIN_WINDOW_H), (avail_h - 24) / scale))

        phys_w = width * scale
        phys_h = height * scale
        # 居中：左右/上下各留一半余量。居中的窗口"看起来是刚打开的"，
        # 而偏到某一侧会让人以为窗口位置丢了（上一版偏右 75%）。
        x = left + (avail_w - phys_w) / 2
        y = top + (avail_h - phys_h) / 2
        x = max(left, min(x, right - phys_w))
        y = max(top, min(y, bottom - phys_h))
        return f"{int(width)}x{int(height)}+{int(x)}+{int(y)}"

    def _restore_geometry(self, geometry: str) -> bool:
        """恢复上次的窗口位置；如果它已经不在可见区域（拔掉外接屏等情况）就放弃。"""
        import re

        match = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", (geometry or "").strip())
        if not match:
            return False
        width, height, x, y = (int(v) for v in match.groups())
        if width < theme.MIN_WINDOW_W or height < theme.MIN_WINDOW_H:
            # 上次可能是"更小尺寸下限"时代的存档，直接放弃，走默认尺寸
            return False
        scale = self._dpi_scale()
        left, top, right, bottom = self._work_area()
        phys_w = width * scale
        phys_h = height * scale
        # 至少要有大半窗口落在可用区域里，否则重新计算
        if x < left - 40 or y < top - 40:
            return False
        if x + phys_w < left + theme.GEOMETRY_MIN_VISIBLE:
            return False
        if y + phys_h < top + theme.GEOMETRY_MIN_VISIBLE:
            return False
        if x > right - 80 or y > bottom - 80:
            return False
        try:
            self.geometry(geometry)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ==================================================================
    # 窗口几何记忆（需求 12）
    # ==================================================================
    #: 存哪个设置键。本轮改用 "window_geometry"（语义明确），
    #: 读取时回落到旧的 "geometry"，老数据文件不会丢位置。
    GEOMETRY_KEY = "window_geometry"
    LEGACY_GEOMETRY_KEY = "geometry"

    def _schedule_geometry_save(self, delay: Optional[int] = None) -> None:
        """请求在"用户停手"之后落盘窗口几何（默认 500ms 防抖）。

        为什么必须防抖：拖边框时 ``<Configure>`` 每帧都发，直接写盘等于每帧
        打开一次文件 + fsync。更糟的是**中间态的尺寸会被写进存档** ——
        下次打开就恢复成拖动中途那个奇怪的尺寸。
        """
        if self._closing:
            return
        if self._geometry_job is not None:
            try:
                self.after_cancel(self._geometry_job)
            except Exception:  # noqa: BLE001
                pass
        wait = theme.GEOMETRY_SAVE_DELAY if delay is None else delay
        self._geometry_job = self.after(wait, self._save_geometry)

    def _save_geometry(self, force: bool = False) -> None:
        """校验并写入窗口几何。

        写入前三道校验（需求 12）：

        1. 尺寸不小于 ``MIN_WINDOW_*`` —— 否则下次打开会得到一个小到没法用的窗口；
        2. 至少有一块明显落在**当前**可用区里 —— 否则（拔了外接屏）下次打开
           窗口在屏幕外，用户根本找不到；
        3. 跳过最大化状态 —— 最大化时 ``geometry()`` 给的是铺满屏幕的矩形，
           存下来下次"还原"就没意义了。
        """
        self._geometry_job = None
        if self._closing:
            return
        if getattr(self, "_maximized", False):
            # 最大化状态下的 geometry() 是"铺满屏幕"，存下来没意义。
            # 用户主动关闭时（force）改存"最大化之前"那一份，
            # 下次打开才是他习惯的窗口大小。
            if not force:
                return
            geometry = self._restore_geom or ""
        else:
            geometry = ""
        if not geometry:
            try:
                geometry = self.geometry()
            except Exception:  # noqa: BLE001
                return
        if not self._geometry_sane(geometry):
            return
        try:
            self.store.set_setting(self.GEOMETRY_KEY, geometry)
            self.save_data()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"窗口几何保存失败：{exc}")

    def save_data(self, force: bool = False) -> bool:
        """保存本地数据；失败时保留脏状态并在界面提示，后续操作会再次尝试。"""
        if self.store.save(force=force):
            if self._save_warning_job is not None:
                try:
                    self.after_cancel(self._save_warning_job)
                except Exception:  # noqa: BLE001
                    pass
                self._save_warning_job = None
            return True

        if self._save_warning_job is None:
            try:
                self._save_warning_job = self.after_idle(self._show_save_warning)
            except Exception:  # noqa: BLE001
                self._save_warning_job = None
        return False

    def _show_save_warning(self) -> None:
        self._save_warning_job = None
        if self.store.dirty:
            self.toast("这次修改还没有保存，请检查磁盘空间或数据目录后重试。",
                       duration=7000)

    def _show_data_recovery_notice(self) -> None:
        if self.store.load_warnings:
            if self.store.recovery_backup_path:
                message = "本地数据有异常，拾光已尽量恢复；原文件副本已保留在数据目录。"
            else:
                message = "本地数据有异常，拾光已尽量恢复；请尽快在设置中备份当前数据。"
            self.toast(message, duration=8000)

    def _ensure_saved_before_exit(self) -> bool:
        """退出前强制写盘；失败时让用户重试或留在应用中处理。"""
        while not self.store.save(force=True):
            try:
                self.deiconify()
                self._hidden = False
                self.lift()
            except Exception:  # noqa: BLE001
                pass
            retry = messagebox.askretrycancel(
                "拾光还没有保存",
                "最近的任务修改仍只在内存中。请检查数据目录权限或磁盘空间，然后重试保存。",
                parent=self,
            )
            if not retry:
                return False
        return True

    def _geometry_sane(self, geometry: str) -> bool:
        """几何字符串是不是"值得存"的。"""
        import re

        match = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", (geometry or "").strip())
        if not match:
            return False
        width, height, x, y = (int(v) for v in match.groups())
        if width < theme.MIN_WINDOW_W or height < theme.MIN_WINDOW_H:
            return False
        # 尺寸不能比可用区还大：正常操作下做不到（再怎么拖也被屏幕挡住），
        # 一旦出现就说明有人把缩放系数算错了 —— 存下去下次启动就会得到一个
        # "比屏幕还大"的窗口，而且用户还找不到原因。宁可丢弃这一份存档。
        left, top, right, bottom = self._work_area()
        scale = self._dpi_scale()
        max_w = (right - left) / scale + 8
        max_h = (bottom - top) / scale + 8
        if width > max_w or height > max_h:
            return False
        rect = self._window_rect()
        if rect is None:
            return True                    # 拿不到真实矩形（非 Windows）就先信它
        l, t, r, b = rect
        visible_x = min(r, right) - max(l, left)
        visible_y = min(b, bottom) - max(t, top)
        return visible_x >= theme.GEOMETRY_MIN_VISIBLE and visible_y >= 40

    def _on_window_configure(self, event) -> None:
        """根窗口 ``<Configure>``：请求防抖落盘。

        必须过滤 ``event.widget is not self``：``<Configure>`` 会从**每个子控件**
        冒泡到根窗口，不过滤的话鼠标划过任意控件都会触发一次保存请求。
        """
        if event is not None and getattr(event, "widget", None) is not self:
            return
        if self._closing:
            return
        self._schedule_geometry_save()
        # 1.5.19：紧凑判定**同步**执行。它只是几次 reqwidth 读取 + 一次幂等
        # 的 set_compact（状态没变直接跳过），成本微秒级 —— 此前 200ms 防抖
        # 在连续拖拽中永远被挤掉，紧凑模式要等松手才生效，拖拽中途顶行
        # 一直保持完整导航互相挤压。
        self._sync_nav_compact()

    def _sync_nav_compact(self) -> None:
        """窗口放不下完整导航时隐藏"光景"，保"新建/任务/设置"不重叠。

        判定按**当前窗口逻辑宽**对"日期 + 新建 + 完整导航 + 内边距"的总需求；
        带 10px 迟滞带，避免临界宽度来回横跳。compact 状态没变时
        ``nav.set_compact`` 内部直接跳过，不会重建画布。
        """
        self._nav_compact_job = None
        try:
            if self._closing or not self.nav.winfo_exists():
                return
            scale = theme.scale() or 1.0
            win_w = self.winfo_width() / scale
            date_w = self.date_label.winfo_reqwidth() / scale
            new_w = self.new_task_btn.winfo_reqwidth() / scale
        except Exception:  # noqa: BLE001
            return
        need = (date_w + new_w + self.nav.full_width_logic()
                + theme.PAGE_PAD_X * 2 + theme.NAV_COMPACT_MARGIN)
        cur = getattr(self.nav, "_compact", False)
        want = win_w < need + (10 if cur else 0)
        self.nav.set_compact(want)

    def _setup_window(self) -> None:
        # 改动二：完全隐藏原生标题栏，由 _build_titlebar 自绘唯一一套
        # （品牌太阳 + "拾光" + 最小化/置顶/关闭）。原生与自绘并存就是
        # 截图里"两套窗口控制"的来源。必须在映射前设置才生效。
        self.overrideredirect(True)
        # 任务栏文字只保留品牌名（窗口内"拾光"只出现一次：标题栏）
        self.title(__app_name__)
        self.configure(fg_color=theme.pair("bg"))
        self.minsize(*self.MIN_SIZE)
        # 显式声明可缩放：overrideredirect 下系统边框已经没了，真正生效的是
        # _bind_resize() 装的那圈热区；这一行是给 Tk 的几何管理器一个明确信号，
        # 避免某些 Tk 版本因为默认 resizable(0,0) 而拒绝后续尺寸变更。
        self.resizable(True, True)

        # 1.5.17：**每次启动都回到默认最优尺寸**（theme.DEFAULT_WINDOW_*，
        # 逻辑 344×470，居中），不再恢复上次退出时保存的窗口尺寸。存档里的
        # 比例是用户上一次拖拽的结果，恢复它等于把"变形现场"带进新会话，
        # 也让"启动即最优排版"失去意义。存档键顺手清掉，避免留死数据；
        # 会话内仍照常落盘（_on_window_configure），只是下次启动不读。
        if self.store.settings.get("layout_rev") != theme.LAYOUT_REV:
            self.store.set_setting("layout_rev", theme.LAYOUT_REV)
        for _gk in (self.GEOMETRY_KEY, self.LEGACY_GEOMETRY_KEY):
            if self.store.settings.get(_gk):
                self.store.settings.pop(_gk, None)
        self.save_data()
        self.geometry(self._initial_geometry())

        # 图标（运行期生成，避免打包时携带二进制资源）
        try:
            png, ico = ensure_icon()
            if png.exists():
                self._icon_image = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_image)
            if ico.exists():
                self._icon_ico = str(ico)      # 对话框要复用它（见 dialogs._apply_window_icon）
                self.iconbitmap(default=str(ico))
        except Exception:  # noqa: BLE001
            pass

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Control-q>", lambda _e: self.quit_app())
        self.bind("<Control-n>", lambda _e: self.quick_add_task())
        self.bind("<Control-comma>", lambda _e: self.show_page("settings"))
        # Esc 也收右键菜单：菜单自己有 <Escape>，但它不一定拿得到焦点
        # （子菜单、点击过主窗口之后都可能不在它身上），这里兜一层。
        self.bind("<Escape>", self._on_escape)

        # 自由缩放：状态先初始化再绑事件（事件可能在 _build_chrome 之前就到达）
        self._resize_edge = ""
        self._resize_origin: Optional[tuple] = None
        self._resize_pending: Optional[tuple[int, int]] = None
        self._resize_job: Optional[str] = None
        self._resize_last_frame = 0.0
        self._resize_surface = None
        self._resize_hidden_widgets: list[tk.Misc] = []
        self._resize_surface_job: Optional[str] = None
        self._cursor_widget: Optional[tk.Misc] = None
        # 被我们写过缩放指针的控件（1.5.26）：指针是沿控件树继承的，
        # 写脏的可能是一堆**祖先**容器，硬复位时要一起清（见 _write_cursor）
        self._cursor_marks: dict = {}
        self._cursor_reset_job: Optional[str] = None
        # 最后一次已知的指针屏幕坐标：硬复位后按它把指针"重算"回来，
        # 而不是简单抹掉（见 _run_cursor_reset）
        self._pointer_xy: Optional[tuple] = None
        self._geometry_job: Optional[str] = None
        self._interaction_job: Optional[str] = None
        self._bind_resize()
        # 滚轮（1.5.26）：全应用**只装一条**路由，且必须装在任何滚动容器
        # 之前 —— bind_all 是按注册顺序串在 all 链上的，路由排在前面才能
        # "滚到了就 break"，避免和 CTk 自己的处理器各滚一次。
        widgets.install_wheel_router(self)
        # 窗口几何记忆：<Configure> 里防抖落盘（需求 12）。
        # 用 add="+" 而不是覆盖 —— CTk 自己可能也绑了 <Configure>。
        self.bind("<Configure>", self._on_window_configure, add="+")
        # 窗口级指针保险（1.5.26）：布局变动/重新映射/拿回焦点时都复位一次
        self.bind("<Configure>", lambda _e: self._schedule_cursor_reset(), add="+")
        self.bind("<Map>", lambda _e: self._schedule_cursor_reset(), add="+")
        self.bind("<FocusIn>", self._on_root_focus_in, add="+")
        # 清理旧浮层必须先于控件自身的命令。CTk 5 在按下时执行 command，
        # CTk 6 在松开时执行；放在普通 toplevel 绑定里会收掉同一点击新建的浮层。
        self._main_press_tag = f"ShiguangMainPress{id(self)}"
        self.bind_class(self._main_press_tag, "<Button-1>", self._before_main_press)
        self.bind("<Map>", self._on_main_widget_map, add="+")
        self._install_main_press_tag(self)

        # overrideredirect 会顺手把窗口从任务栏抹掉，必须把 APPWINDOW 样式
        # 补回来，否则最小化之后用户找不到回来的入口
        self.after(30, self._force_taskbar_presence)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.apply_topmost()
        # 等窗口真正映射出来后再校正一次位置（此时才能拿到真实的窗口矩形）
        self.after(150, self._ensure_on_screen)

    @staticmethod
    def _bd() -> int:
        """窗口描边的**物理**像素宽度。

        ``place`` 的坐标/尺寸都是物理像素，不归 CTk 的缩放管，所以这里直接产出
        lpx 过的值，调用方不要自己再乘。
        """
        return theme.lpx(theme.WINDOW_BORDER)

    # ==================================================================
    # 窗口描边（1.5.2）
    # ==================================================================
    def _build_window_border(self) -> None:
        """在主窗口最外圈压上四条细描边（柔橘色）。

        为什么是"四条覆盖条"而不是别的手段：

        * **不能靠系统边框** —— 主窗口是 ``overrideredirect``，压根没有非客户区，
          DWM 的 ``DWMWA_BORDER_COLOR``（且只在 Win11 生效）无从下手；
        * **不能靠"根窗口底色改描边色 + 各区块内缩"** —— 区块之间的横向留白
          （``PAGE_PAD_X``）露出来的也是根窗口底色，那样会变成一圈 10px 的橘色带；
        * 描边条用 ``tk.Frame``（不是 CTk 控件）：没有画布、没有圆角、没有缩放，
          成本可以忽略；``place`` 的 ``relwidth/relheight`` 会随窗口缩放自动拉伸，
          不需要接进任何布局计算。

        层级：这四条是根窗口里**最后创建**的兄弟控件，压在全部内容之上；若之后
        再有浮层被 ``place`` 上来（引导页、Toast），要补调
        :meth:`_raise_window_border` 把它们抬回去。
        """
        bd = self._bd()
        specs = (
            {"x": 0, "y": 0, "relwidth": 1, "height": bd},                       # 上
            {"x": 0, "rely": 1, "y": -bd, "relwidth": 1, "height": bd},          # 下
            {"x": 0, "y": 0, "relheight": 1, "width": bd},                       # 左
            {"relx": 1, "x": -bd, "y": 0, "relheight": 1, "width": bd},          # 右
        )
        self._border_frames: List[tk.Frame] = []
        for spec in specs:
            frame = tk.Frame(self, bd=0, highlightthickness=0, takefocus=0)
            frame.place(**spec)
            self._border_frames.append(frame)
        self._sync_window_border()

    def _sync_window_border(self) -> None:
        """把描边条的底色对齐当前主题。

        必须显式同步：``tk.Frame`` 不认 CTk 那套 ``(浅色, 深色)`` 双色元组，
        主题切换时它不会自己变（其余的 CTk 控件会）。
        """
        color = theme.c("window_border")
        for frame in getattr(self, "_border_frames", ()):
            try:
                frame.configure(bg=color)
            except Exception:  # noqa: BLE001
                pass

    def _raise_window_border(self) -> None:
        """把描边条抬到最上层（有别的浮层刚被 place 上来时调用）。"""
        for frame in getattr(self, "_border_frames", ()):
            try:
                frame.lift()
            except Exception:  # noqa: BLE001
                pass

    def _force_taskbar_presence(self) -> None:
        """overrideredirect 窗口恢复任务栏图标。

        Windows 把无装饰窗口默认按"工具窗口"处理（不进任务栏）。补
        ``WS_EX_APPWINDOW`` 后必须 withdraw → deiconify 一次让系统重新评估，
        任务栏按钮才会出现。
        """
        if not sys.platform.startswith("win"):
            return
        try:
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            user32 = _ctypes.windll.user32
            hwnd = self._hwnd()
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            if self.winfo_viewable() and not self._hidden:
                self.withdraw()
                self.after(20, self.deiconify)
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"恢复任务栏图标失败：{exc}")

    # ==================================================================
    # 外壳：标题栏 / 日期栏 / 内容 / 导航
    # ==================================================================
    def _build_titlebar(self) -> None:
        """自绘标题栏：窗口控制唯一的一套。

        布局：左侧 太阳图标 + "拾光"／ 中间留白（可拖动）／
        右侧 最小化 - 最大化(还原) - 关闭。**没有置顶按钮**。

        要点：
        * 品牌名"拾光"在界面上只出现这一次（需求 12），slogan 与日期都不放这里；
        * 标题栏区域（含品牌图标/文字）绑定拖动与双击最大化，按钮除外；
        * 关闭按钮 hover 换朱红图标 + 浅朱红底（需求 9 的危险操作反馈）。
        """
        self.titlebar = ctk.CTkFrame(self, fg_color=theme.pair("card"),
                                     height=theme.TITLE_HEIGHT, corner_radius=0)
        self.titlebar.grid(row=0, column=0, sticky="ew")
        self.titlebar.grid_propagate(False)
        self.titlebar.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(self.titlebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=(theme.TITLE_SIDE_PAD, 0))
        sun_img = icons.get_ctk("nav_today", theme.TITLE_ICON)
        self._brand_icon = sun_img
        widgets.TextLabel(brand, text="", image=sun_img,
                     width=theme.TITLE_ICON).pack(side="left")
        widgets.TextLabel(brand, text=__app_name__, font=theme.font("group_title"),
                     text_color=theme.pair("text")).pack(side="left", padx=(6, 0))

        tools = ctk.CTkFrame(self.titlebar, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e", padx=(0, theme.TITLE_SIDE_PAD - 8))

        # 需求 20/21：窗口控制**只保留这一组**，不重复出现在设置页。
        # 最小化=柔橘横线 / 最大化(还原)=方框 / 关闭=× hover 朱红。
        # 本轮改动：原先的"置顶图钉"换成"最大化/还原" —— 置顶按钮位置被
        # 最大化占用后，置顶能力仍保留在设置项与快捷键里（见 toggle_topmost）。
        self.min_btn = widgets.IconButton(tools, text="—", icon="minimize",
                                          command=self.minimize_window,
                                          size=theme.TITLE_BTN)
        self.min_btn.pack(side="left", padx=2)
        self.max_btn = widgets.IconButton(tools, text="", icon="maximize",
                                          command=self.toggle_maximize,
                                          size=theme.TITLE_BTN)
        self.max_btn.pack(side="left", padx=2)
        self.hide_btn = widgets.IconButton(
            tools, text="", icon="close", command=self.on_close,
            size=theme.TITLE_BTN, hover=theme.pair("close_hover"))
        self.hide_btn.pack(side="left", padx=2)
        # 需求 9：hover 时 × 变朱红 —— tkinter 按钮没有 icon-hover 联动，手工绑
        self.hide_btn.bind("<Enter>",
                           lambda _e: self.hide_btn.set_icon("close_hover"), add="+")
        self.hide_btn.bind("<Leave>",
                           lambda _e: self.hide_btn.set_icon("close"), add="+")

        # 拖动 + 双击最大化（品牌区/空白区都能拖，按钮区除外）
        self._bind_titlebar_drag(self.titlebar)
        self._bind_titlebar_drag(brand)

    def _bind_titlebar_drag(self, widget: tk.Misc) -> None:
        """给标题栏区域（含 CTk 内部 canvas 与子标签）递归绑定拖动/双击。"""
        for w in (widget, getattr(widget, "_canvas", None)):
            if w is None:
                continue
            w.bind("<Button-1>", self._titlebar_press, add="+")
            w.bind("<B1-Motion>", self._titlebar_motion, add="+")
            w.bind("<Double-Button-1>",
                   lambda _e: self.toggle_maximize(), add="+")
        try:
            children = widget.winfo_children()
        except Exception:  # noqa: BLE001
            children = []
        for child in children:
            if isinstance(child, (ctk.CTkButton,)):
                continue                      # 按钮区不参与拖动
            self._bind_titlebar_drag(child)

    def _titlebar_press(self, event) -> None:
        """按下标题栏：记录按下点相对窗口左上角的偏移（物理像素）。

        边缘热区优先：如果这一按落在缩放热区里（顶边 6px 与标题栏重叠），
        就把拖动候选清掉，交给 ``_on_root_press`` 去缩放 —— 否则用户想拉
        上边缘，结果整个窗口被拖走了。
        """
        if self._resize_zone(event.x_root, event.y_root):
            self._drag_origin = None
            return
        self._drag_origin = (event.x_root - self.winfo_x(),
                             event.y_root - self.winfo_y())
        # 拖动窗口期间同样只做布局、不做昂贵重绘（需求 13）
        self._begin_interaction()

    def _titlebar_motion(self, event) -> None:
        if self._drag_origin is None or self._resize_edge:
            return
        if self._maximized:
            # 最大化状态下拖动 = 先还原，再让标题栏跟随鼠标
            self.toggle_maximize()
        dx, dy = self._drag_origin
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    # ==================================================================
    # 自由缩放（overrideredirect 窗口没有系统边框，热区自己画）
    # ==================================================================
    def _resize_zone(self, x_root: int, y_root: int) -> str:
        """命中哪条边/哪个角，返回 ``"n"/"s"/"e"/"w"/"ne"/"nw"/"se"/"sw"`` 或 ``""``。

        为什么需要这一步：``overrideredirect(True)`` 会把窗口的**系统边框一起
        去掉**（这正是"标题栏只保留一套控件"的前提），代价是系统不再提供
        拖拽缩放 —— 用户拉边没有任何反应，窗口看起来"被锁死了"。
        Windows 的原生做法是处理 ``WM_NCHITTEST``，Tk 不暴露；所以我们在窗口
        四周留一圈透明的热区，按下即进入手工缩放。

        热区宽度取 ``theme.RESIZE_BAND``（逻辑像素），四角额外用一个稍大的
        ``RESIZE_BAND_CORNER`` —— 只按边算的话，角上那几像素很难点准。
        """
        if self._closing or getattr(self, "_maximized", False):
            return ""
        band = theme.lpx(theme.RESIZE_BAND)
        corner = theme.lpx(theme.RESIZE_BAND_CORNER)
        x, y = self.winfo_x(), self.winfo_y()
        w, h = self.winfo_width(), self.winfo_height()
        dx, dy = x_root - x, y_root - y
        # 指针已经离窗口太远（拖出屏幕外）就不算命中
        if not (-band <= dx <= w + band and -band <= dy <= h + band):
            return ""
        if dx <= corner and dy <= corner:
            return "nw"
        if dx >= w - corner and dy <= corner:
            return "ne"
        if dx <= corner and dy >= h - corner:
            return "sw"
        if dx >= w - corner and dy >= h - corner:
            return "se"
        horiz = "w" if dx <= band else ("e" if dx >= w - band else "")
        vert = "n" if dy <= band else ("s" if dy >= h - band else "")
        return vert + horiz

    # 缩放时的鼠标指针（用 X 标准名字，Windows 上 Tk 会映射到系统资源）
    _RESIZE_CURSORS = {
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
        "nw": "top_left_corner", "se": "bottom_right_corner",
        "ne": "top_right_corner", "sw": "bottom_left_corner",
    }
    # 上面这些值的集合：用来在全树清扫时认出"这是缩放指针"（见 _purge_resize_cursors）
    _RESIZE_CURSOR_NAMES = frozenset(_RESIZE_CURSORS.values())

    # ------------------------------------------------------------------
    # 指针管理（1.5.26 重做）
    # ------------------------------------------------------------------
    # 旧写法只在"当前指针下的那个控件"上写 cursor，从不在离开时复位上一个。
    # **Tk 的指针解析是沿控件树向上继承的**：某个祖先控件的 cursor 非空时，
    # 它下面所有没显式设过 cursor 的子控件都会跟着显示那个指针。于是——
    #
    #   指针在窗口边缘（热区）时，事件目标是"边缘处那个容器"（最坏是根窗口 .）
    #   → 容器被写成缩放箭头；快速移进内容区时 Tk 可能一次 Motion 都不落在
    #   那个容器上，它就此**永久**顶着缩放箭头，并把箭头传染给它下面的一切
    #   （实测：切页、开关浮层都洗不掉，直到指针某次恰好又扫过那个容器）。
    #
    # 修法：① 每次改指针都**对称复位**上一个被写过的控件；② 所有"硬复位时机"
    # （松手/失焦/映射/Configure/换页/收浮层）把记录过的脏控件一起清掉。
    # 见 theme.CURSOR_RESET_DEBOUNCE_MS。
    def _write_cursor(self, widget: tk.Misc, cursor: str) -> None:
        """给某个控件写指针，并把它记进"被我写脏过"的名单。"""
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                return
            if widget.cget("cursor") == cursor:
                key = str(widget)
                if cursor:
                    self._cursor_marks[key] = widget
                return
            widget.configure(cursor=cursor)
        except Exception:  # noqa: BLE001
            return
        key = str(widget)
        if cursor:
            self._cursor_marks[key] = widget
        else:
            self._cursor_marks.pop(key, None)

    def reset_cursor(self, force: bool = False) -> None:
        """把所有被写脏的控件复位成默认指针（窗口级保险）。

        ``force=False`` 时，缩放手势进行中不打断（那时缩放箭头**应该**留着）。
        只有"确实被写脏过"（名单非空、或根窗口顶着缩放指针）才做全树清扫 ——
        那是 O(控件数) 的操作，不能挂在每个 <Configure> 上白跑。
        """
        if self._closing:
            return
        if self._resize_edge and not force:
            return
        marked = bool(self._cursor_marks)
        for widget in list(self._cursor_marks.values()):
            self._write_cursor(widget, "")
        self._cursor_marks.clear()
        self._cursor_widget = None
        # 根窗口 + 四条描边再兜一次：这两个最容易被写脏、也最容易漏
        root_poisoned = self._is_resize_cursor(self)
        for widget in (self, *getattr(self, "_border_frames", ())):
            if self._is_resize_cursor(widget):
                try:
                    widget.configure(cursor="")
                except Exception:  # noqa: BLE001
                    pass
        if marked or root_poisoned:
            self._purge_resize_cursors()

    def _is_resize_cursor(self, widget: tk.Misc) -> bool:
        try:
            value = widget.cget("cursor")
        except Exception:  # noqa: BLE001
            return False
        return isinstance(value, str) and value in self._RESIZE_CURSOR_NAMES

    def _purge_resize_cursors(self, keep: Optional[tk.Misc] = None) -> int:
        """全树兜底：把还顶着缩放指针的控件统统复位（只放过 ``keep``）。

        正常路径靠"对称复位 + 脏名单"就够了，这一层是防"名单漏记/被别的
        代码路径清掉"的极端情况 —— 一个被写脏的**祖先**容器会让它下面整片
        子控件顶着缩放箭头，而且**永远不会自愈**。
        """
        cleared = 0
        stack: List[tk.Misc] = [self]
        while stack:
            node = stack.pop()
            try:
                stack.extend(node.winfo_children())
            except Exception:  # noqa: BLE001
                continue
            if node is keep or not self._is_resize_cursor(node):
                continue
            try:
                node.configure(cursor="")
                cleared += 1
            except Exception:  # noqa: BLE001
                pass
        return cleared

    def _on_root_motion(self, event) -> None:
        """任意位置的移动：在热区里就换成缩放指针，否则恢复。"""
        if self._closing:
            return
        if self._resize_edge:
            return                       # 正在缩放，指针保持不变
        self._pointer_xy = (event.x_root, event.y_root)
        zone = self._resize_zone(event.x_root, event.y_root)
        cursor = self._RESIZE_CURSORS.get(zone, "")
        widget = event.widget
        previous = self._cursor_widget
        if previous is not None and previous is not widget:
            # 换控件了：先把上一个复位 —— 少了这一步，边缘处被写脏的容器
            # 会把它下面的子控件一起带成缩放箭头（见本段开头的说明）
            self._write_cursor(previous, "")
        self._cursor_widget = widget
        self._write_cursor(widget, cursor)

    def _refresh_cursor_at(self, x_root: int, y_root: int) -> None:
        """按屏幕坐标重算一次指针（松手后不掉帧的关键：位置没动也不该变样）。"""
        if self._closing:
            return
        zone = self._resize_zone(x_root, y_root)
        cursor = self._RESIZE_CURSORS.get(zone, "")
        widget = None
        try:
            widget = self.winfo_containing(x_root, y_root)
        except Exception:  # noqa: BLE001
            widget = None
        if widget is None:
            widget = self
        if self._cursor_widget is not None and self._cursor_widget is not widget:
            self._write_cursor(self._cursor_widget, "")
        self._cursor_widget = widget
        self._write_cursor(widget, cursor)

    def _schedule_cursor_reset(self) -> None:
        """窗口级保险的去抖复位（<Configure>/<Map> 会连发很多次）。

        取**尾部去抖**：连发期间只保留最后一次，忙的时候不打断（真的在缩放
        时上面两处早退会直接跳过），停下来 120ms 后复位一次。
        """
        if self._closing or self._resize_edge:
            return
        if self._cursor_reset_job is not None:
            try:
                self.after_cancel(self._cursor_reset_job)
            except Exception:  # noqa: BLE001
                pass
        self._cursor_reset_job = self.after(
            theme.CURSOR_RESET_DEBOUNCE_MS, self._run_cursor_reset)

    def _run_cursor_reset(self) -> None:
        """复位脏指针，并**按最后已知的指针位置重算**。

        只清不算是错的：指针停在窗口边缘、恰好在此时来一发 <Configure>，
        复位会把该显示的缩放箭头也抹掉（要等下一次鼠标移动才回来）。
        """
        self._cursor_reset_job = None
        if self._closing or self._resize_edge:
            return
        self.reset_cursor()
        if self._pointer_xy is not None:
            self._refresh_cursor_at(*self._pointer_xy)

    def _on_root_focus_in(self, _event=None) -> None:
        """拿回焦点时复位（浮层关掉后焦点回到主窗口，旧指针可能还是浮层的）。"""
        self.reset_cursor(force=True)

    def _on_escape(self, _event=None) -> None:
        """Esc：收浮层 + 复位指针（浮层自己那份 cursor 随它一起消失）。"""
        try:
            self._close_floating(reason="esc")
        except Exception:  # noqa: BLE001
            pass
        self.reset_cursor(force=True)

    def _close_floating(self, reason: str = "replace") -> None:
        """收起所有"悬浮层"：右键菜单 + 图标选择浮层 + 新建任务浮层（需求 ⑥/①）。

        为什么要集中一处：它们都是**独立的 overrideredirect 顶层窗口**，各自
        管各自的存活。主窗口的 Esc、以及"在窗口内任意位置按下"都应该一起收掉，
        漏掉任何一个，用户就会遇到"按了 Esc 还挂着一块浮层"的观感 —— 本轮要修
        的浮层残留，本质都是这个。

        新建任务浮层自己绑了 ``<Escape>``，但它**不一定拿得到键盘焦点**
        （实测第二次打开时 ``focus_get()`` 是 None），所以这里必须兜一层。
        """
        try:
            ui_menu.close_active()
        except Exception:  # noqa: BLE001
            pass
        try:
            from .ui import icon_picker
            icon_picker.close_active()
        except Exception:  # noqa: BLE001
            pass
        popup = getattr(self, "_quick_popup", None)
        if popup is not None:
            try:
                if getattr(popup, "closed", False):
                    self._quick_popup = None
                else:
                    popup.close(reason=reason)
            except Exception:  # noqa: BLE001
                pass

    def _install_main_press_tag(self, widget) -> None:
        tags = widget.bindtags()
        if self._main_press_tag not in tags:
            widget.bindtags((self._main_press_tag, *tags))

    def _on_main_widget_map(self, event) -> None:
        try:
            if event.widget.winfo_toplevel() is self:
                self._install_main_press_tag(event.widget)
        except (AttributeError, tk.TclError):
            pass

    def _before_main_press(self, _event) -> None:
        """仅主页面点击先收旧浮层；浮层自身及其子控件不使用这个标签。"""
        try:
            self._close_floating(reason="outside-click")
        except Exception:  # noqa: BLE001
            pass

    def _on_root_press(self, event) -> None:
        """控件执行动作后的主窗口绑定，仅处理缩放与指针。"""
        zone = self._resize_zone(event.x_root, event.y_root)
        if not zone:
            # 在内容区按下 = "点掉了浮层/菜单" —— 顺手把指针复位一次。
            # 放在这里而不是 _close_floating 里：按在热区上时要保留缩放箭头，
            # 手势中又没有 Motion 来重画，早复位会让箭头在拖动时消失。
            self.reset_cursor(force=True)
            return
        self._resize_edge = zone
        self._begin_interaction()
        self._resize_origin = (
            event.x_root, event.y_root,
            self._window_rect() or (self.winfo_x(), self.winfo_y(),
                                    self.winfo_x() + self.winfo_width(),
                                    self.winfo_y() + self.winfo_height()),
        )
        self._resize_pending = None
        self._resize_last_frame = 0.0
        self._drag_origin = None          # 边缘优先：这一次按下不是"拖窗口"
        self._start_resize_surface()

    # ------------------------------------------------------------------
    # 交互（拖动/缩放）期间的节流（需求 13）
    # ------------------------------------------------------------------
    def _begin_interaction(self) -> None:
        """手势开始：告诉各控件"现在是缩放中，只做布局、别做昂贵重绘"。"""
        widgets.set_resizing(True)
        if self._interaction_job is not None:
            try:
                self.after_cancel(self._interaction_job)
            except Exception:  # noqa: BLE001
                pass
            self._interaction_job = None

    def _end_interaction(self) -> None:
        """手势结束：放开节流，并安排一次"停手后的全量重绘"。

        ``RESIZE_REDRAW_DELAY`` 不只是等一等 —— 它同时把
        "手势结束"和"最后一次布局完成"对齐，避免在布局还没稳定时重画。
        """
        widgets.set_resizing(False)
        if self._interaction_job is not None:
            try:
                self.after_cancel(self._interaction_job)
            except Exception:  # noqa: BLE001
                pass
        self._interaction_job = self.after(theme.RESIZE_REDRAW_DELAY,
                                           self._after_interaction_idle)

    def _after_interaction_idle(self) -> None:
        """停手 200ms：把被延后的昂贵重绘补上（药丸投影、渐变图、插画）。"""
        self._interaction_job = None
        if self._closing:
            return
        try:
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        try:
            widgets.redraw_all(self)
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"缩放后重绘失败：{exc}")

    def _on_root_drag(self, event) -> None:
        """缩放中合并高频鼠标事件，直接更新主窗口尺寸。"""
        if not self._resize_edge or self._resize_origin is None:
            return
        if not sys.platform.startswith("win"):
            return
        self._resize_pending = (event.x_root, event.y_root)
        if self._resize_job is not None:
            return
        # 鼠标事件可能远快于屏幕刷新；每帧只应用最后一个坐标。
        remaining = max(0, 16 - int((time.monotonic() - self._resize_last_frame) * 1000))
        if remaining:
            self._resize_job = self.after(remaining, self._apply_pending_resize)
        else:
            self._apply_pending_resize()

    def _apply_pending_resize(self) -> None:
        self._resize_job = None
        if not self._resize_edge or self._resize_origin is None or self._resize_pending is None:
            return
        x_root, y_root = self._resize_pending
        self._resize_pending = None
        ox, oy, rect = self._resize_origin
        dx, dy = x_root - ox, y_root - oy
        left, top, right, bottom = rect
        min_w = theme.lpx(theme.MIN_WINDOW_W)
        min_h = theme.lpx(theme.MIN_WINDOW_H)
        edge = self._resize_edge
        if "e" in edge:
            right = max(left + min_w, right + dx)
        if "w" in edge:
            left = min(right - min_w, left + dx)
        if "s" in edge:
            bottom = max(top + min_h, bottom + dy)
        if "n" in edge:
            top = min(bottom - min_h, top + dy)
        rect = (int(left), int(top), int(right), int(bottom))
        self._resize_last_frame = time.monotonic()
        if rect != self._window_rect():
            self._set_resize_rect(rect)

    def _start_resize_surface(self) -> None:
        """在真实窗口上放一层完整画面，避免拖动时露出子控件的半成品。"""
        if self._resize_surface_job is not None:
            self.after_cancel(self._resize_surface_job)
            self._resize_surface_job = None
        self._finish_resize_surface()
        rect = self._window_rect()
        if rect is None:
            return
        try:
            from .ui.resize_surface import ResizeSurface
            surface = ResizeSurface(self, rect)
            self._resize_surface = surface
            self._resize_hidden_widgets = list(self.grid_slaves())
            for child in self._resize_hidden_widgets:
                child.grid_remove()
            surface.canvas.tk.call("raise", surface.canvas._w)
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"建立缩放画面失败：{exc}")
            self._finish_resize_surface()

    def _finish_resize_surface(self) -> None:
        """原生控件完成最终布局后移走覆盖画面。"""
        self._resize_surface_job = None
        self._restore_resize_widgets()
        surface, self._resize_surface = self._resize_surface, None
        if surface is not None:
            try:
                surface.destroy()
            except Exception:  # noqa: BLE001
                pass

    def _restore_resize_widgets(self) -> None:
        """先在覆盖画面下恢复控件，让 Tk 有时间完成布局和绘制。"""
        for child in self._resize_hidden_widgets:
            try:
                if child.winfo_exists():
                    child.grid()
            except Exception:  # noqa: BLE001
                pass
        self._resize_hidden_widgets = []
        try:
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            pass

    def _set_resize_rect(self, rect: tuple[int, int, int, int]) -> None:
        """按物理像素更新真实窗口，拖动中即可看到当前尺寸。"""
        if not sys.platform.startswith("win"):
            return
        left, top, right, bottom = rect
        try:
            hwnd = self._hwnd()
            user32 = _ctypes.windll.user32
            surface = self._resize_surface
            old_rect = self._window_rect()
            old_area = ((old_rect[2] - old_rect[0]) * (old_rect[3] - old_rect[1])
                        if old_rect is not None else 0)
            new_area = (right - left) * (bottom - top)
            # Growing the HWND before its child canvas is ready exposes Tk's
            # background for one paint.  Present the new frame first.
            if surface is not None and new_area >= old_area:
                surface.render(right - left, bottom - top)
            if not hwnd or not user32.SetWindowPos(
                    hwnd, 0, left, top, right - left, bottom - top,
                    0x0004 | 0x0010):
                raise OSError("SetWindowPos 没有应用窗口尺寸")
            if surface is not None and new_area < old_area:
                surface.render(right - left, bottom - top)
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"应用缩放尺寸失败：{exc}")

    def _on_root_release(self, _event) -> None:
        """松手：结束缩放手势、结束节流、落盘几何。

        这里也是**拖动标题栏**的收尾（同一个 <ButtonRelease-1> 事件），
        所以"移动窗口后记住位置"和"缩放后记住尺寸"走的是同一段代码 ——
        上一版只处理了缩放分支，窗口被拖到别处再打开又回到老位置（需求 12）。
        """
        was_resizing = bool(self._resize_edge)
        was_dragging = self._drag_origin is not None
        if was_resizing:
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
                self._resize_job = None
            self._resize_pending = (_event.x_root, _event.y_root)
            self._apply_pending_resize()
        self._resize_edge = ""
        self._resize_origin = None
        self._resize_pending = None
        self._drag_origin = None
        # 松手强复位（1.5.26）：手势期间指针是"冻结"的，收手势时把它彻底
        # 清一遍，再按当前坐标重算 —— 位置没动也该显示该位置该有的指针。
        self._pointer_xy = (_event.x_root, _event.y_root)
        self.reset_cursor(force=True)
        self._refresh_cursor_at(_event.x_root, _event.y_root)
        if not (was_resizing or was_dragging):
            return
        try:
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        self._end_interaction()
        if was_resizing and self._resize_surface is not None:
            self._restore_resize_widgets()
            self._resize_surface_job = self.after(
                theme.RESIZE_REDRAW_DELAY + 160, self._finish_resize_surface)
        # 手势结束后再落盘：走默认防抖（500ms），拖动过程中<Configure>请求的
        # 那一堆保存会被这一条挤掉，最终写下去的是"停手后的静止尺寸"。
        self._schedule_geometry_save()

    def _remember_geometry(self) -> None:
        """历史方法名，保留兼容：等价于"立刻落盘窗口几何"。"""
        self._schedule_geometry_save(delay=0)

    def _bind_resize(self) -> None:
        """把缩放/指针反馈挂到根窗口。

        利用 Tk 的 bindtags：事件先给控件、再给**所属 toplevel**、最后给 all。
        所以绑在根窗口上就能收到整棵子树的鼠标事件，不需要给每个控件单独绑。
        """
        self.bind("<Motion>", self._on_root_motion, add="+")
        self.bind("<Button-1>", self._on_root_press, add="+")
        self.bind("<B1-Motion>", self._on_root_drag, add="+")
        self.bind("<ButtonRelease-1>", self._on_root_release, add="+")

    def toggle_maximize(self) -> None:
        """最大化（铺满当前显示器可用区）/ 还原。

        三个入口共用：标题栏的最大化按钮、双击标题栏、拖动已最大化的窗口。
        """
        if self._maximized:
            if self._restore_geom:
                try:
                    self.geometry(self._restore_geom)
                except Exception:  # noqa: BLE001
                    pass
            self._maximized = False
            self._sync_max_btn()
            return
        self._restore_geom = self.geometry()
        left, top, right, bottom = self._work_area()
        scale = self._dpi_scale()
        # ctk 的 geometry() 会把宽高乘缩放系数、位置原样使用（物理像素）
        width = int((right - left) / scale)
        height = int((bottom - top) / scale)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self._maximized = True
        self._sync_max_btn()

    def _build_chrome(self) -> None:
        self._maximized = False
        self._restore_geom = ""
        self._drag_origin: Optional[tuple] = None

        self._build_titlebar()

        # ---------------- 顶部行（row 1）：日期 + 新建 + 页面切换器 ----------------
        # 本轮把整条**底部悬浮药丸删掉**，页面切换器挪到这一行右侧。
        # 纵向因此省下 56(药丸) + 12(底距) + 6(投影) = 74px；内容区也不再需要
        # "为悬浮元素让路"的底部内边距，可以一路贴到窗口底边。
        self.topbar = ctk.CTkFrame(self, fg_color="transparent")
        self.topbar.grid(row=1, column=0, sticky="ew",
                         padx=theme.PAGE_PAD_X, pady=(theme.PAGE_GAP, 2))
        self.topbar.grid_columnconfigure(0, weight=1)

        # 日期与切换器、新建按钮同字号（都是 tiny）：一行里三种字号会把"哪块
        # 是导航、哪块是信息"混淆；而且 11px 的日期宽 93 逻辑像素，在最小窗宽
        # 310（顶部行可用 290）下会和右侧挤成一团。
        self.date_label = widgets.TextLabel(self.topbar, text="",
                                            font=theme.font(theme.SWITCH_FONT),
                                            text_color=theme.pair("text_muted"))
        self.date_label.grid(row=0, column=0, sticky="w")

        # 「新建」小药丸。**不带图标**：CTkButton 一旦有 image，请求宽度就会有一个
        # 内部下限（实测"图标 + 2 汉字"在 1.0 缩放下恒为 59 逻辑像素，显式给
        # width=32 也压不下去），会把右边的切换器挤出可视区。纯文字按钮则完全
        # 尊重 width。图标在分组标题的 "＋" 上已经有了，这里不重复。
        self.new_task_btn = ctk.CTkButton(
            self.topbar, text="新建",
            anchor="center",
            width=theme.fit_width("新建", theme.SWITCH_FONT,
                                  pad_x=theme.SWITCH_PAD_X),
            height=theme.SWITCH_H, corner_radius=theme.SWITCH_RADIUS,
            fg_color=theme.pair("orange"), hover_color=theme.pair("orange_hover"),
            text_color=theme.ON_ACCENT,
            font=theme.font(theme.SWITCH_FONT),
            command=lambda: self.open_quick_add(),
        )
        self.new_task_btn.grid(row=0, column=1, sticky="e",
                               padx=(0, theme.PAGE_GAP))

        # 页面切换器（键就是页面名，图标名单独给 —— 避免"页面名"与"图标名"
        # 被硬绑在一起：早期版本靠 f"{key}_on" 拼图标名，一旦页面改名，
        # 图标就静默消失且没有任何检查能发现）。
        self.nav = widgets.TopNavSwitcher(
            self.topbar,
            [("tasks", NAV_LABELS["tasks"], "nav_today"),
             ("stats", NAV_LABELS["stats"], "nav_stats"),
             ("settings", NAV_LABELS["settings"], "nav_settings")],
            command=self._on_nav_key,
        )
        self.nav.grid(row=0, column=2, sticky="e")
        self.nav.set_current("tasks", animate=False)

        # ---------------- 番茄钟横幅（row 2，默认隐藏）----------------
        self.pomodoro_bar = ctk.CTkFrame(self, corner_radius=14,
                                         fg_color=theme.pair("accent_faint"),
                                         border_width=1,
                                         border_color=theme.pair("accent_soft"))
        self.pomodoro_bar.grid_columnconfigure(1, weight=1)
        # 番茄图标走自绘（需求 14），不再用 🍅 emoji
        _pomo_img = icons.get_ctk("pomodoro", icons.SIZE_MAIN + 2)
        widgets.TextLabel(self.pomodoro_bar, text="",
                     image=_pomo_img, font=theme.font("h2")).grid(
            row=0, column=0, padx=(14, 6), pady=8)
        self.pomodoro_label = widgets.TextLabel(self.pomodoro_bar, text="",
                                           font=theme.font("small"),
                                           text_color=theme.pair("text"), anchor="w")
        self.pomodoro_label.grid(row=0, column=1, sticky="ew")
        self.pomodoro_time = widgets.TextLabel(self.pomodoro_bar, text="",
                                          font=theme.font("h2"),
                                          text_color=theme.pair("accent"))
        self.pomodoro_time.grid(row=0, column=2, padx=6)
        widgets.IconButton(self.pomodoro_bar, text="", icon="close", size=26,
                           command=self.stop_pomodoro).grid(row=0, column=3, padx=(0, 10))
        self.pomodoro_bar.grid(row=2, column=0, sticky="ew",
                               padx=theme.PAGE_PAD_X, pady=(2, 2))
        self.pomodoro_bar.grid_remove()

        # ---------------- 内容容器（row 3，weight=1）----------------
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        # 不再为悬浮导航留底部内边距：导航已经搬到顶部的切换器里，
        # 内容区可以一路贴到窗口底边（这一条也是"底部大导航"最实在的收益）。
        self.content.grid(row=3, column=0, sticky="nsew",
                          padx=theme.PAGE_PAD_X, pady=(0, theme.PAGE_GAP))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.refresh_chrome()

    def _build_overlays(self) -> None:
        self.toast_widget = widgets.Toast(self)
        # 粒子层是独立的透明置顶浮窗（不随页面重建销毁，否则每次刷新都要重建）
        self.particles = ParticleLayer(self)
        # 描边必须**最后**建：它是根窗口的兄弟控件，靠创建顺序压在所有内容之上
        self._build_window_border()

    # ==================================================================
    # 页面
    # ==================================================================
    PAGES = {"tasks": TaskPage, "stats": StatsPage, "settings": SettingsPage}

    def show_page(self, name: str, remember: bool = True) -> None:
        if name not in self.PAGES:
            name = "tasks"
        if remember:
            self.current_page = name
        # 换页等于"浮层全没了 + 整页控件被销毁"：先把还活着的脏指针复位，
        # 再把名单清空（里面会留下旧页面已销毁控件的死引用）
        self.reset_cursor(force=True)
        if self.page is not None:
            self.page.destroy()
        self._cursor_marks.clear()
        self._cursor_widget = None
        self.page = self.PAGES[name](self.content, self)
        self.page.grid(row=0, column=0, sticky="nsew")
        self.nav.set_current(name)
        self.refresh_chrome()

    def _on_nav_key(self, key: str) -> None:
        """导航栏点击（key 就是页面名，不用再做标签↔键的反查）。"""
        self.show_page(key)

    def refresh(self) -> None:
        """重建当前页面（数据变动后调用）。"""
        self.show_page(self.current_page, remember=False)

    # ==================================================================
    # 头部刷新
    # ==================================================================
    def refresh_chrome(self) -> None:
        today = _dt.date.today()
        self.date_label.configure(
            text=f"{today.month}/{today.day} {WEEKDAY_SHORT[today.weekday()]}")
        # 窗口控制按钮的图标随状态切：最大化时显示"还原"（两个叠框），
        # 平时显示"最大化"（单个方框）。形状区分比颜色区分在小图标上更可靠。
        self._sync_max_btn()

    def _sync_max_btn(self) -> None:
        """同步最大化按钮的图标（最大化中 = 还原符号）。"""
        btn = getattr(self, "max_btn", None)
        if btn is None:
            return
        try:
            btn.set_icon("restore" if getattr(self, "_maximized", False)
                         else "maximize", fallback_text="")
        except Exception:  # noqa: BLE001
            pass

    def _sync_day_night_icon(self) -> None:
        """昼夜翻转时把"今日"导航图标从太阳换成月牙（反之亦然）。

        只在昼夜真的跨过去了才重画 —— 定时器每分钟都会跑，无脑刷新
        等于每分钟重建一次导航图标缓存，白费。
        """
        try:
            night = icons.is_night()
        except Exception:  # noqa: BLE001
            return
        if night == getattr(self, "_nav_night", None):
            return
        self._nav_night = night
        try:
            self.nav.refresh_theme()
        except Exception:  # noqa: BLE001
            pass

    # ==================================================================
    # 设置联动
    # ==================================================================
    def on_setting(self, key: str, value: Any) -> None:
        self.store.set_setting(key, value)
        self.save_data()
        if key == "theme":
            ctk.set_appearance_mode(theme.MODE_MAP.get(value, "System"))
            self._last_dark = theme.is_dark()
            # Canvas 背景是固定单色，主题切换必须手工重画导航与图标
            try:
                icons.invalidate()
                self.nav.refresh_theme()
                # 描边条是裸 tk.Frame，不认 (浅,深) 双色元组，得手工同步
                self._sync_window_border()
            except Exception:  # noqa: BLE001
                pass
            self.refresh()
        elif key == "always_on_top":
            self.apply_topmost()
            self.refresh_chrome()
        elif key == "close_to_tray":
            pass
        elif key == "hotkey":
            self._start_hotkey()
        elif key in ("due_banner", "due_particles"):
            # 这两个只影响界面呈现，重建当前页即可生效
            self.refresh()
        elif key == "due_notify":
            # 关闭时顺手清空已提醒记录：重新打开后能立刻恢复提醒能力
            if self.reminder is not None and not value:
                self.reminder.reset()
        elif key in ("pomodoro_minutes", "sound_enabled"):
            pass

    def apply_topmost(self) -> None:
        topmost = bool(self.store.settings.get("always_on_top", False))
        try:
            self.attributes("-topmost", topmost)
        except Exception:  # noqa: BLE001
            pass
        # 新建任务浮层跟着一起同步：它不再**无条件**置顶（1.5.1 改动），
        # 但用户手动开了"窗口置顶"时，浮层理应与主窗口保持一致。
        popup = getattr(self, "_quick_popup", None)
        if popup is not None:
            try:
                popup.sync_topmost()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 前台 / 激活（1.5.1 新增）
    # ------------------------------------------------------------------
    def is_foreground(self) -> bool:
        """当前**前台窗口**是否属于本进程（主窗口 / 浮层 / 对话框 / 菜单都算）。

        为什么需要它（1.5.1 的真 bug）
        ----------------------------
        新建任务浮层原来无条件 ``-topmost True``，用户 Alt+Tab 到别的应用后
        它还赖在屏幕最上层；而"失焦就收"这条路径当时只绑了浮层自己的
        ``<FocusOut>``，overrideredirect 顶层窗口在部分切换路径下收不到那一拍
        事件 → 浮层永远不消失。这里给出一条**权威、与事件无关**的判据：
        直接问系统"前台窗口的进程 id 是不是我"。

        比 PID 而不是比 HWND，是因为本进程的窗口不止一个（浮层、日期选择器、
        右键菜单、引导浮层、粒子层…），逐个枚举窗口去比既啰嗦又容易漏。
        非 Windows 平台没有这个 API，返回 True —— 那些平台交给 ``<FocusOut>``。
        """
        if not sys.platform.startswith("win"):
            return True
        try:
            user32 = _ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            if not fg:
                return False
            # 取**根窗口**：焦点常常落在子控件（entry / canvas）上。
            GA_ROOT = 2
            root = user32.GetAncestor(fg, GA_ROOT) or fg
            pid = _ctypes.c_ulong()
            user32.GetWindowThreadProcessId(root, _ctypes.byref(pid))
            return int(pid.value) == os.getpid()
        except Exception:  # noqa: BLE001
            return True

    def _force_foreground(self) -> bool:
        """把主窗口抢到前台（Windows 前台锁的完整解法）。

        为什么光调 ``SetForegroundWindow`` 不够：Windows 只允许"当前前台进程"
        或"最近收到过用户输入的进程"改前台窗口。**刚双击启动的进程**虽然符合
        直觉，却常常拿不到前台 —— 于是窗口出来了却在别的软件底下（用户报的
        "启动后窗口在最底层"）。

        ``AttachThreadInput`` 把自己的输入队列挂到当前前台线程上，本线程就临时
        获得了前台进程的权限，这时 ``SetForegroundWindow`` 才会被接受。
        用完立刻 detach，绝不长期挂着别人的输入队列。
        """
        if not sys.platform.startswith("win"):
            try:
                self.focus_force()
            except Exception:  # noqa: BLE001
                pass
            return True
        try:
            user32 = _ctypes.windll.user32
            kernel32 = _ctypes.windll.kernel32
            hwnd = self._hwnd()
            if not hwnd:
                return False
            if user32.GetForegroundWindow() == hwnd:
                return True
            # ⚠️ 线程 id 是 32 位无符号数，可能超过 c_int 的范围 ——
            #    不声明 argtypes 的话 ctypes 会按 c_int 传，运气差就抛
            #    OverflowError（"argument out of range"）。三个参数都是 DWORD。
            if not getattr(user32.AttachThreadInput, "argtypes", None):
                user32.AttachThreadInput.argtypes = (
                    _ctypes.c_ulong, _ctypes.c_ulong, _ctypes.c_int)
            fg = user32.GetForegroundWindow()
            cur_thread = int(kernel32.GetCurrentThreadId()) & 0xFFFFFFFF
            fg_thread = int(user32.GetWindowThreadProcessId(fg, None) or 0) & 0xFFFFFFFF
            attached = False
            if fg_thread and fg_thread != cur_thread:
                attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
            if attached:
                user32.AttachThreadInput(fg_thread, cur_thread, False)
            return ok
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"抢前台失败：{exc}")
            return False

    def _activation_disabled(self) -> bool:
        """是否禁用"启动抢前台"（截图 / 无头预览等场景）。

        ``tools/screenshot.py`` 会在创建 App 之前设 ``SHIGUANG_NO_ACTIVATE=1`` ——
        预览是后台动作，抢走用户当前应用的焦点是很讨厌的副作用。
        """
        return bool(os.environ.get("SHIGUANG_NO_ACTIVATE"))

    def _activate_on_start(self) -> None:
        """启动时确保窗口可见、在前台（1.5.1 新增）。

        与 ``show_and_focus`` 同一套路子（先置顶"闪一下"再还原 —— 这是唯一
        能稳定把窗口推到最前的办法），区别是**必须在映射之后再补几拍重试**：
        进程启动时窗口尚未映射，前台锁那一关当场就会拒绝，只试一次会失败。
        用户已经在用电脑（前台是别的软件），所以头一次失败很正常，
        500ms / 1100ms 各再补一次就够了 —— 不做无限重试。
        """
        if self._closing or self._hidden or self._activation_disabled():
            return
        try:
            if not self.winfo_viewable():
                self.deiconify()
            self.lift()
        except Exception:  # noqa: BLE001
            pass

        topmost = bool(self.store.settings.get("always_on_top", False))
        try:
            self.attributes("-topmost", True)
            self.update_idletasks()
            self.attributes("-topmost", topmost)
        except Exception:  # noqa: BLE001
            pass

        # AttachThreadInput 这条路径在窗口**已映射**时才有效，所以 retry 不是保险
        ok = self._force_foreground()
        try:
            self.focus_force()
        except Exception:  # noqa: BLE001
            pass
        if not ok:
            self.store.log("启动激活未成功，稍后重试")
        self.after(30, self._ensure_on_screen)

    def _activate_retry(self, round_: int) -> None:
        """启动激活的补试（最多 2 轮：500ms、1100ms）。"""
        if self._closing or self._hidden or self._activation_disabled():
            return
        if self.is_foreground() and not self._is_minimized():
            return
        topmost = bool(self.store.settings.get("always_on_top", False))
        try:
            self.attributes("-topmost", True)
            self.update_idletasks()
            self.attributes("-topmost", topmost)
        except Exception:  # noqa: BLE001
            pass
        self._force_foreground()
        try:
            self.focus_force()
        except Exception:  # noqa: BLE001
            pass
        if round_ < 2:
            self.after(600, self._activate_retry, round_ + 1)

    def minimize_window(self) -> None:
        """最小化到任务栏（需求 21：最小化=柔橘横线图标）。

        与 ``hide_to_tray`` 的区别：这个是"最小化"，任务栏还有图标可点回来；
        托盘则是彻底藏起来。两者是不同语义，所以是两个按钮、两条路径。

        overrideredirect 的窗口 ``iconify()`` 在 Windows 上不生效（Tk 对无
        装饰窗口的 state 管理是残缺的），必须直接调 ``ShowWindow(SW_MINIMIZE)``。
        """
        if sys.platform.startswith("win"):
            try:
                SW_MINIMIZE = 6
                _ctypes.windll.user32.ShowWindow(self._hwnd(), SW_MINIMIZE)
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            self.iconify()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"最小化失败：{exc}")

    def _is_minimized(self) -> bool:
        """窗口是否处于最小化状态（overrideredirect 下 ``state()`` 不可靠）。"""
        if sys.platform.startswith("win"):
            try:
                return bool(_ctypes.windll.user32.IsIconic(self._hwnd()))
            except Exception:  # noqa: BLE001
                pass
        try:
            return self.state() == "iconic"
        except Exception:  # noqa: BLE001
            return False

    def _restore_from_minimize(self) -> None:
        """从最小化恢复（ShowWindow 最小化后，deiconify 救不回来）。"""
        if sys.platform.startswith("win"):
            try:
                SW_RESTORE = 9
                _ctypes.windll.user32.ShowWindow(self._hwnd(), SW_RESTORE)
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            self.deiconify()
        except Exception:  # noqa: BLE001
            pass

    def toggle_topmost(self) -> None:
        self.on_setting("always_on_top", not self.store.settings.get("always_on_top", False))
        self.toast("窗口已置顶" if self.store.settings.get("always_on_top") else "已取消置顶")

    # ==================================================================
    # 番茄钟
    # ==================================================================
    def toggle_pomodoro(self, task_id: str) -> None:
        task = self.store.task(task_id)
        if task is None:
            return
        minutes = int(self.store.settings.get("pomodoro_minutes", 25))
        if self.pomodoro.running and self.pomodoro.task_id == task_id:
            self.pomodoro.stop()
            self.toast("已结束这次专注")
            return
        self.pomodoro.start(task_id, minutes)
        self.toast(f"开始专注 {minutes} 分钟 · {task.title}")

    def start_focus(self) -> None:
        """常驻可见的专注入口（1.5.11：统计条计时图标 / 空状态按钮）。

        不挂靠具体任务也能专注 —— "自由专注"同样计入每日专注统计，
        只是没有任何任务的番茄数 +1。正在专注时再点只提示进度，不重置。
        """
        if self.pomodoro.running:
            self.toast(f"正在专注中 · 剩余 {self.pomodoro.label}")
            return
        minutes = int(self.store.settings.get("pomodoro_minutes", 25))
        self.pomodoro.start("", minutes)
        self.toast(f"开始专注 {minutes} 分钟")

    def stop_pomodoro(self) -> None:
        self.pomodoro.stop()
        self.toast("已结束这次专注")

    def _on_pomodoro_tick(self, state: Dict[str, Any]) -> None:
        if state.get("running"):
            task = self.store.task(state.get("task_id") or "")
            title = task.title if task else "自由专注"
            self.pomodoro_label.configure(text=f"专注中 · {title}")
            self.pomodoro_time.configure(text=state.get("label", ""))
            self.pomodoro_bar.grid()
        else:
            self.pomodoro_bar.grid_remove()
        if isinstance(self.page, TaskPage):
            self.page.update_pomodoro(state)

    def _on_pomodoro_finish(self, task_id: Optional[str]) -> None:
        self.pomodoro_bar.grid_remove()
        sound.play("chime", bool(self.store.settings.get("sound_enabled", True)))
        # 1.5.11：专注一律计入每日历史与总数（"自由专注"也算）；
        # 有挂靠任务时才给任务的 pomodoros +1（见 store.add_focus）。
        self.store.add_focus(task_id or "")
        self.save_data()
        # 轻柔提示 + 3 秒自动消失的 toast（需求 23）
        self.toast("专注结束，休息一下吧", duration=3000)
        if isinstance(self.page, TaskPage):
            self.page.render()
        elif isinstance(self.page, StatsPage):
            self.page.refresh()
        self.sync_tray()

    # ==================================================================
    # 提示 / 庆祝
    # ==================================================================
    def toast(self, message: str, duration: int = 2600,
              anchor: Optional[tk.Misc] = None,
              celebration: bool = False,
              action_label: str = "",
              action_command: Optional[Callable[[], None]] = None) -> None:
        try:
            self.toast_widget.show(
                message, duration, anchor=anchor, celebration=celebration,
                action_label=action_label, action_command=action_command)
        except Exception:  # noqa: BLE001
            pass

    def celebrate(self, group_only: bool = False,
                  anchor: Optional[tk.Misc] = None) -> None:
        """完成任务时的庆祝：粒子迸发 + 随机治愈文案。

        ``group_only=True`` 表示"某个分组刚被清空"，用一半左右的粒子做个轻量反馈
        （需求 14）；整页全部完成时才播满量粒子并弹出治愈文案。

        两次动画之间至少间隔 1 秒（需求 15）：连点勾选框时不至于把 90 帧动画叠加起来，
        否则 CPU 会被几层粒子同时拖满，反而卡顿。
        """
        import random

        now = time.monotonic()
        if now - self._last_celebrate < 1.0:
            return
        self._last_celebrate = now

        if self.store.settings.get("due_particles", True):
            try:
                count = 10 if group_only else 26
                self.particles.play(count)
            except Exception as exc:  # noqa: BLE001
                self.store.log(f"粒子动画失败：{exc}")

        if group_only:
            return

        # 文案统一取自 strings（需求 ④）：治愈句和空状态短语同源，改一处全站生效
        message = random.choice(strings.ALL_DONE_MESSAGES)
        # 粒子先飞 0.5 秒，文案再出现，两者叠在一起更有层次
        self.after(500, lambda: self.toast(
            message, duration=4200, anchor=anchor, celebration=True))
        sound.play("chime", bool(self.store.settings.get("sound_enabled", True)))

    # ==================================================================
    # 引导
    # ==================================================================
    def show_onboarding(self) -> None:
        if self.onboarding is not None:
            return
        self.onboarding = OnboardingOverlay(self, self._finish_onboarding)
        self.onboarding.place(x=0, y=0, relwidth=1, relheight=1)
        self.onboarding.lift()
        # 引导页铺满整窗，会把描边盖掉 —— 抬回来（1.5.2）
        self._raise_window_border()
        self.onboarding.lift()

    def _finish_onboarding(self) -> None:
        if self.onboarding is not None:
            self.onboarding.destroy()
            self.onboarding = None
        self.store.set_setting("onboarded", True)
        self.save_data()
        self.toast("从第一缕光开始吧")

    # ==================================================================
    # 托盘 / 快捷键
    # ==================================================================
    def post(self, action: str, payload: Any = None) -> None:
        """供子线程调用的线程安全入口。"""
        self._queue.put((action, payload))

    def _start_tray(self) -> None:
        try:
            png, _ico = ensure_icon()
            self.tray = Tray(png, self.post, self._tray_summary)
            self.tray.start()
        except Exception:  # noqa: BLE001
            self.tray = None

    def _tray_summary(self) -> str:
        return f"今日已拾起 {stats.today_count(self.store)} 缕光"

    def sync_tray(self) -> None:
        if self.tray is None:
            return
        self.tray.set_tooltip(f"{__app_name__} · {self._tray_summary()}")
        self.tray.refresh()

    def _start_hotkey(self) -> None:
        if not self.store.settings.get("hotkey_enabled", True):
            self.hotkey.unregister()
            return
        spec = self.store.settings.get("hotkey", "Ctrl+Shift+T")
        ok, err = self.hotkey.register(spec)
        if not ok:
            self.store.log(f"全局快捷键注册失败：{spec} / {err}")

    # ==================================================================
    # 截止日期提醒
    # ==================================================================
    def _start_reminder(self) -> None:
        """启动提醒节拍器（daemon 线程只负责每 60 秒敲一次钟）。"""
        try:
            self.reminder = ReminderService(
                self.store, self.post,
                enabled=lambda: bool(self.store.settings.get("due_notify", True)),
            )
            self.reminder.start()
        except Exception as exc:  # noqa: BLE001
            self.reminder = None
            self.store.log(f"提醒服务启动失败：{exc}")

    def _check_due(self) -> None:
        """结算一轮到期提醒并发系统通知（主线程执行）。"""
        if self.reminder is None or self._closing:
            return
        try:
            pending = self.reminder.evaluate()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"到期结算失败：{exc}")
            return
        if not pending:
            return
        title, message = ReminderService.build_message(pending)
        if self.store.settings.get("due_notify", True):
            self._notify(title, message)
        just = sum(1 for kind, _ in pending if kind == "just")
        if just:
            self.toast(f"有 {just} 项任务已到期", duration=3600)
        self.store.log(f"到期提醒：{message}")
        # 提醒条里的"逾期数量"可能变了，顺手刷新当前页
        self.refresh()

    def check_due_now(self) -> None:
        """供测试/手动触发：立刻跑一轮到期检查。"""
        self._check_due()

    def _handle_action(self, action: str, payload: Any) -> None:
        """主线程动作分发。

        命名约定：托盘（tray.py）与热键投递的动作名必须在这里有对应分支。
        末尾的 else 是**故意保留**的兜底 —— 之前托盘点击无效就是因为
        tray 发的是 "toggle" 而这里只认 "toggle_window"，没有兜底就静默丢了。
        """
        if action == "toggle_window":
            self.toggle_window()
        elif action == "hotkey_toggle":
            self._hotkey_toggle()
        elif action == "show":
            self.show_and_focus()
        elif action == "hide":
            self.hide_to_tray()
        elif action == "quick_add":
            self.quick_add_task()
        elif action == "stats":
            self.show_and_focus()
            self.show_page("stats")
        elif action == "about":
            self.show_and_focus()
            dialogs.about(self)
        elif action == "due_tick":
            self._check_due()
            self._sync_day_night_icon()
        elif action == "quit":
            self.quit_app()
        else:
            # 未知动作必须留痕，否则同类"名字对不上"的 bug 还会再藏一次
            self.store.log(f"收到未处理的动作：{action!r}")
            log.warning("未处理的动作：%r", action)

    # ==================================================================
    # 窗口显隐（唯一入口）
    # ==================================================================
    # 托盘点击、全局快捷键、托盘右键"显示/隐藏"、设置页里的相关动作
    # 全部走 show_and_focus() / hide_to_tray()，不再各写一份 —— 之前托盘失效
    # 的根因就是"动作名不一致 + 实现散落"。
    def toggle_window(self) -> None:
        """切换窗口显隐。

        带去抖：pystray 的 Windows 后端在双击时会连发两次激活，
        不去抖就会"显示→立刻隐藏"，用户看到的是闪一下什么都没发生。
        """
        now = time.monotonic()
        if now - self._last_toggle < self.TOGGLE_DEBOUNCE:
            return
        self._last_toggle = now

        if self._hidden or self._is_minimized() or not self.winfo_viewable():
            self.show_and_focus()
        else:
            self.hide_to_tray()

    def show_and_focus(self) -> None:
        """恢复窗口并强制置顶聚焦。托盘点击、快捷键、外部调用统一复用。"""
        if self._closing:
            return
        self._hidden = False
        try:
            # 1) withdraw / 最小化过就先恢复
            if self._is_minimized():
                self._restore_from_minimize()
            elif not self.winfo_viewable():
                self.deiconify()

            # 2) 重新校验位置：withdraw 期间显示器可能被拔插，或缩放变了，
            #    存档坐标可能已经落在可见区外 —— 复用 _geometry_guard
            self.update_idletasks()
            self._geometry_guard()

            # 3) 提到最前
            self.lift()

            # 4) 置顶"闪一下"再还原：Windows 上单纯 focus_force() 常常抢不到前台
            #    （前台锁规则），先置顶能把窗口强制推到最前
            topmost = bool(self.store.settings.get("always_on_top", False))
            try:
                self.attributes("-topmost", True)
                self.update_idletasks()
                self.attributes("-topmost", topmost)
            except Exception:  # noqa: BLE001
                pass

            # 5) 强制聚焦
            self.focus_force()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"show_and_focus 失败：{exc}")

        # 6) 真实矩形自检：窗口装饰会让"算出来的位置"偏出可用区，必须在映射后校正
        self.after(30, self._ensure_on_screen)

    def show_window(self) -> None:
        """``show_and_focus`` 的旧名字，保留兼容既有调用点。"""
        self.show_and_focus()

    def hide_to_tray(self) -> None:
        """隐藏到托盘（不退出进程）。"""
        self._hidden = True
        try:
            self.withdraw()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"隐藏窗口失败：{exc}")
        self._hint_tray_once()

    def _hint_tray_once(self) -> None:
        """首次藏到托盘时提示一次"拾光还在"，之后不再打扰。"""
        if self.store.settings.get("tray_tip_shown") or self._hinted_tray:
            return
        self._hinted_tray = True
        self.store.set_setting("tray_tip_shown", True)
        self.save_data()
        message = "拾光已藏在托盘里啦，右键图标可以退出"
        # 窗口已经藏起来了，toast 是看不见的 —— 这里必须用系统通知/托盘气泡
        self._notify("拾光 · 还在陪着你", message)
        self.store.log("首次隐藏到托盘，已提示用户")

    def _notify(self, title: str, message: str) -> bool:
        """统一的通知出口（托盘气泡优先，失败降级到 ctypes 原生通知）。"""
        try:
            icon = assets_gen.ensure_icon()[1]
        except Exception:  # noqa: BLE001
            icon = None
        return notify.notify(title, message, tray=self.tray, icon_path=icon)

    def quick_add_task(self) -> None:
        self.show_and_focus()
        self.open_quick_add()

    def open_quick_add(self, anchor: Optional[tk.Misc] = None,
                       group_id: str = "") -> None:
        """弹出"新建任务"浮层。

        所有入口（顶部"＋ 新建任务"按钮 / 分组标题"＋" / Ctrl+N / 托盘"快速
        添加" / 全局快捷键）统一走这里。``anchor`` 缺省用顶部按钮，浮层弹在
        按钮下方。重复打开时先收掉旧的 —— 同一时间只允许一个浮层。

        ⚠️ 本轮修的两处（浮层残留）：
        1. 旧浮层可能**已经销毁**而引用还在（``<Destroy>`` 里会摘引用，但
           外部 destroy / 解释器清理等路径未必经过）。所以这里既看 ``close()``
           的幂等守卫，也看 ``closed`` 属性，任何一条成立都不再碰它。
        2. ``QuickAddPopup`` 构造中途抛异常时，已经 ``super().__init__`` 出来的
           Toplevel 会以 withdraw 状态残留。所以失败分支里显式把它收掉。
        """
        from .ui.quick_add import QuickAddPopup

        if not isinstance(self.page, TaskPage):
            self.show_page("tasks")
        # store 里一个分组都没有时先补上默认三组（需求 24），
        # 否则浮层的分组标签条会是空的
        try:
            self.store.ensure_default_groups()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"补默认分组失败：{exc}")

        # 清场：提醒条明细 / 右键菜单 / 图标浮层都可能正开着，而它们都是独立
        # 顶层窗口，会压在新浮层上或跟它抢焦点。QuickAddPopup 构造里也会做一遍
        # （防止别处直接 new 它），这里再调一次是因为本方法可能在页面切换之后
        # 才执行，那时页面上的浮层对象已经换了一批。
        try:
            self._close_floating()
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"清场失败：{exc}")

        old = getattr(self, "_quick_popup", None)
        if old is not None:
            try:
                if getattr(old, "closed", False):
                    self._quick_popup = None
                else:
                    old.close(reason="reopen")
            except Exception:  # noqa: BLE001
                pass
        if anchor is None:
            anchor = self.new_task_btn
        try:
            self._quick_popup = QuickAddPopup(
                self, anchor, group_id=group_id,
                on_submit=self.page.add_quick_task,
            )
        except Exception as exc:  # noqa: BLE001
            self.store.log(f"新建任务浮层打开失败：{exc}")
            self._quick_popup = None

    def _hotkey_toggle(self) -> None:
        """全局快捷键：呼出窗口后自动聚焦到新建输入框（改动一第 2 条）。"""
        was_away = self._hidden or self._is_minimized() or not self.winfo_viewable()
        self.toggle_window()
        if was_away and not self._hidden:
            # 刚被呼出来：等窗口稳定后弹出输入浮层并聚焦
            self.after(200, self.open_quick_add)

    # ==================================================================
    # 主循环
    # ==================================================================
    def _pump(self) -> None:
        if self._closing:
            return
        try:
            while True:
                action, payload = self._queue.get_nowait()
                try:
                    self._handle_action(action, payload)
                except Exception as exc:  # noqa: BLE001
                    self.store.log(f"处理消息失败 {action}: {exc}")
        except queue.Empty:
            pass
        self._check_system_theme()
        self._pump_job = self.after(120, self._pump)

    def _check_system_theme(self) -> None:
        """跟随系统主题：仅当实际模式变化时才重建界面（Canvas 需要重绘）。"""
        dark = theme.is_dark()
        if dark != self._last_dark:
            self._last_dark = dark
            if self.store.settings.get("theme") == "system":
                self.refresh()

    # ==================================================================
    # 退出
    # ==================================================================
    def on_close(self) -> None:
        """点关闭按钮 / Alt+F4：按设置决定"藏到托盘"还是"真的退出"。"""
        # 关窗口前把几何存下来（force：绕开 maximized 跳过，见 _save_geometry）
        self._save_geometry(force=True)
        if not self.save_data():
            return
        if (self.store.settings.get("close_to_tray", True)
                and self.tray is not None and self.tray.available):
            self.hide_to_tray()      # 首次会提示"拾光已藏在托盘里"
            return
        self.quit_app()

    def quit_app(self) -> None:
        """真的退出：停掉所有后台服务再销毁窗口。

        托盘右键"退出"、菜单里的"退出拾光"、Ctrl+Q 都走这里，
        与"隐藏到托盘"是两条完全分开的路径。
        """
        if self._closing:
            return
        # 必须在置 _closing 之前存 —— _save_geometry 见到 _closing 就直接返回
        self._save_geometry(force=True)
        # 把最终还原尺寸一起落盘，并在任何后台服务停止前确认写入成功。
        geom = self._restore_geom if getattr(self, "_maximized", False) else self.geometry()
        self.store.set_setting("geometry", geom)
        if not self._ensure_saved_before_exit():
            return
        self._closing = True
        if self._pump_job:
            try:
                self.after_cancel(self._pump_job)
            except Exception:  # noqa: BLE001
                pass
            self._pump_job = None
        if self.reminder is not None:
            try:
                self.reminder.stop()
            except Exception:  # noqa: BLE001
                pass
        if getattr(self, "particles", None) is not None:
            try:
                self.particles.destroy()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.hotkey.unregister()
        except Exception:  # noqa: BLE001
            pass
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.destroy()
        except Exception:  # noqa: BLE001
            pass


def diagnostics() -> Dict[str, Any]:
    """打印 DPI / 缩放相关状态（排查"打包后界面发虚或变小"用）。"""
    info: Dict[str, Any] = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
    }
    if not sys.platform.startswith("win"):
        info["platform"] = sys.platform
        return info

    import ctypes

    try:
        info["dpi_aware_flag"] = bool(ctypes.windll.user32.IsProcessDPIAware())
    except Exception as exc:  # noqa: BLE001
        info["dpi_aware_flag"] = f"err {exc}"
    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
        info["process_awareness"] = {0: "unaware", 1: "system", 2: "per-monitor"}.get(
            awareness.value, awareness.value)
    except Exception as exc:  # noqa: BLE001
        info["process_awareness"] = f"err {exc}"
    try:
        info["system_dpi"] = ctypes.windll.user32.GetDpiForSystem()
    except Exception:  # noqa: BLE001
        info["system_dpi"] = ctypes.windll.user32.GetDeviceCaps(
            ctypes.windll.user32.GetDC(0), 88)
    try:
        import customtkinter as ctk

        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

        info["ctk_widget_scaling"] = round(ScalingTracker.get_widget_scaling(None), 3)
        info["ctk_window_scaling"] = round(ScalingTracker.get_window_scaling(None), 3)
        info["ctk_dpi"] = ScalingTracker.get_window_dpi(None)
    except Exception as exc:  # noqa: BLE001
        info["ctk_widget_scaling"] = f"err {exc}"
    return info


def _enable_dpi_awareness() -> None:
    """开启进程 DPI 感知。

    必须在创建任何窗口之前调用。否则在高 DPI（如 150%）显示器上，
    Windows 会把窗口位图拉伸放大 —— 界面会明显发虚。
    打包成 exe 后 PyInstaller 的默认清单不带 per-monitor 感知，所以这里显式设置。
    """
    if not sys.platform.startswith("win"):
        return
    import ctypes

    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    """程序入口。"""
    _enable_dpi_awareness()
    try:
        app = ShiguangApp()
    except Exception as exc:  # noqa: BLE001
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                f"{__app_name__} 启动失败",
                f"{exc}\n\n日志：{log_file()}\n数据目录：{data_dir()}")
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        raise
    app.mainloop()
    return 0
