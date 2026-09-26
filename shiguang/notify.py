# -*- coding: utf-8 -*-
"""Windows 系统通知（任务到期提醒）。

为什么不用 win10toast / plyer？
    这两个库为了"弹一条通知"会各自拖入 pywin32 或平台桥接层，打包后体积 +3~8MB。
    而在 Windows 上发通知的本质就是 ``Shell_NotifyIconW`` + ``NIF_INFO`` 一个调用，
    用标准库 ctypes 直接调即可 —— 零额外依赖、零体积增长。这也是需求里
    "体积超过 3MB 就改用 ctypes" 那条的落地方式：我们直接一步到位。

三级降级链（任一级成功即停止，全部失败也只写日志）：
    1. **复用已在运行的 pystray 托盘图标** —— 它内部就是 Shell_NotifyIcon，
       还省掉自己建窗口的功夫，是首选；
    2. 托盘不可用时，用 ctypes 自建一个隐藏窗口发通知（``_CtypesToast``）；
    3. 都不可用就返回 False，绝不抛异常打断主流程。

关于线程：``Shell_NotifyIconW`` 本身可以从任意线程调用，但需要一个能收消息的
窗口。``_CtypesToast`` 因此独立起一个 daemon 线程跑自己的消息循环，
与 Tk 主线程完全隔离。
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("shiguang.notify")

# --------------------------------------------------------------------------
# Win32 常量
# --------------------------------------------------------------------------
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x00000000, 0x00000001, 0x00000002
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x00000001
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x00000010, 0x00000040
WS_POPUP = 0x80000000
CW_USEDEFAULT = -0x80000000
ERROR_CLASS_ALREADY_EXISTS = 1410
IDI_INFORMATION = 32516


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
        ("hIconSm", ctypes.c_void_p),
    ]


class _MSG(ctypes.Structure):
    """最小化版 Win32 ``MSG``。

    为什么不直接用 ``ctypes.wintypes.MSG``：``ctypes.wintypes`` 在非 Windows 平台
    导入时会因为部分类型定义而直接抛异常，而这个模块是跨平台打包的。
    自己声明一份，布局与 x64 上的真实 MSG 一致（48 字节）。
    """

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 的数据结构（Vista+ 完整版）。

    ``cbSize`` 必须与实际结构大小一致，否则 Windows 会直接拒绝调用。
    """

    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeoutOrVersion", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class _CtypesToast:
    """自建隐藏窗口 + Shell_NotifyIconW 的兜底实现。"""

    CLASS_NAME = "ShiguangToastWnd"
    WM_CLOSE, WM_DESTROY = 0x0010, 0x0002
    _class_registered = False
    _lock = threading.Lock()

    def __init__(self, icon_path: Optional[Path] = None) -> None:
        self.icon_path = Path(icon_path) if icon_path else None

    # ------------------------------------------------------------------
    def _ensure_class(self, kernel32, user32):
        """注册窗口类（进程内只需一次）。"""
        with self._lock:
            if _CtypesToast._class_registered:
                return True

            @ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                ctypes.c_uint, ctypes.c_long)
            def _wndproc(hwnd, msg, wparam, lparam):  # noqa: ANN001
                # 我们不关心回调消息，收到 WM_DESTROY 就结束循环
                if msg == self.WM_DESTROY:
                    user32.PostQuitMessage(0)
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wndproc_ref = _wndproc     # 必须保活，否则回调被 GC 掉会崩

            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(_wndproc, ctypes.c_void_p)
            wc.cbClsExtra = wc.cbWndExtra = 0
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.hIcon = 0
            wc.hCursor = 0
            wc.hbrBackground = 0
            wc.lpszMenuName = None
            wc.lpszClassName = self.CLASS_NAME
            wc.hIconSm = 0
            user32.RegisterClassExW(ctypes.byref(wc))
            err = kernel32.GetLastError()
            if err not in (0, ERROR_CLASS_ALREADY_EXISTS):
                log.warning("注册通知窗口类失败：%s", err)
                return False
            _CtypesToast._class_registered = True
            return True

    def _load_icon(self, kernel32, user32):
        """优先用品牌图标（暖阳），失败则退回系统信息图标。"""
        if self.icon_path and self.icon_path.exists():
            handle = user32.LoadImageW(
                None, str(self.icon_path), IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if handle:
                return handle
        return user32.LoadIconW(None, ctypes.c_void_p(IDI_INFORMATION))

    # ------------------------------------------------------------------
    def show(self, title: str, message: str, timeout: float = 8.0) -> bool:
        """发一条通知。返回是否成功。"""
        try:
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
        except Exception as exc:  # noqa: BLE001
            log.warning("加载 Win32 库失败：%s", exc)
            return False

        if not self._ensure_class(kernel32, user32):
            return False

        result = {"ok": False}
        done = threading.Event()

        def worker() -> None:
            try:
                hwnd = user32.CreateWindowExW(
                    0, self.CLASS_NAME, "Shiguang", WS_POPUP,
                    CW_USEDEFAULT, CW_USEDEFAULT, 0, 0, None, None,
                    kernel32.GetModuleHandleW(None), None)
                if not hwnd:
                    log.warning("创建通知窗口失败")
                    return

                nid = _NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
                nid.hWnd = hwnd
                nid.uID = 0x51          # 拾光的 ASCII 码，随便取一个稳定值
                nid.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE | NIF_INFO
                nid.uCallbackMessage = 0x0400      # WM_USER
                nid.hIcon = self._load_icon(kernel32, user32)
                nid.szTip = "拾光 · 任务提醒"[:127]
                # 正文/标题都按 szInfo/szInfoTitle 的容量截断，超长会导致调用失败
                nid.szInfo = str(message)[:255]
                nid.szInfoTitle = str(title)[:63]
                nid.dwInfoFlags = NIIF_INFO

                if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                    log.warning("Shell_NotifyIconW(NIM_ADD) 失败")
                    user32.DestroyWindow(hwnd)
                    return
                # Vista+ 上气泡文案要通过 NIM_MODIFY 再写一次才生效
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                result["ok"] = True

                # 让气泡显示一会儿，然后撤掉图标（否则托盘会留下一个幽灵图标）
                deadline = time.monotonic() + timeout
                msg = _MSG()
                while time.monotonic() < deadline:
                    # 处理本线程窗口的消息，同时留出时间让气泡显示
                    if not user32.PeekMessageW(ctypes.byref(msg), hwnd, 0, 0, 1):
                        time.sleep(0.1)
                    else:
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                    if done.is_set():
                        break

                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                user32.DestroyWindow(hwnd)
            except Exception as exc:  # noqa: BLE001
                log.warning("发送通知异常：%s", exc)
            finally:
                done.set()

        thread = threading.Thread(target=worker, name="shiguang-toast", daemon=True)
        thread.start()
        # 给窗口创建 + NIM_ADD 一点时间，确保返回值可用
        done.wait(timeout=2.0)
        return bool(result["ok"])


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------
_toast: Optional[_CtypesToast] = None


def available(tray=None) -> bool:
    """当前环境是否有可用的通知通道。"""
    if tray is not None and getattr(tray, "available", False):
        return True
    import sys

    return sys.platform.startswith("win")


def notify(title: str, message: str, tray=None,
           icon_path: Optional[Path] = None) -> bool:
    """发送一条系统通知。

    ``tray`` 传入正在运行的 ``Tray`` 实例即可走最省事的原生通道。
    返回是否成功；任何失败都只记日志，不抛异常。
    """
    global _toast

    # ---- 通道 1：复用托盘图标（pystray 内部就是 Shell_NotifyIcon）----
    if tray is not None and getattr(tray, "available", False):
        try:
            if tray.notify(title, message):
                return True
        except Exception as exc:  # noqa: BLE001
            log.warning("托盘通道发通知失败，降级：%s", exc)

    # ---- 通道 2：ctypes 自建窗口 ----
    import sys

    if not sys.platform.startswith("win"):
        log.info("非 Windows 平台，跳过系统通知：%s / %s", title, message)
        return False
    try:
        if _toast is None:
            _toast = _CtypesToast(icon_path)
        return _toast.show(title, message)
    except Exception as exc:  # noqa: BLE001
        log.warning("ctypes 通道发通知失败：%s", exc)
        return False
