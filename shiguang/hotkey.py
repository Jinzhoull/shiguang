# -*- coding: utf-8 -*-
"""全局快捷键。

Windows 分支走原生 ``RegisterHotKey``（ctypes 直接调 user32，零依赖、不弹 UAC），
在独立线程里跑消息循环；其他平台尝试 pynput，不可用则优雅降级为"未启用"。

线程模型
--------
热键回调发生在**子线程**，绝不能直接操作 Tk 控件。约定：回调只往队列里塞消息，
由主线程的 ``after`` 轮询消费（见 app.py 的 ``_pump``）。
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Callable, Optional, Tuple

log = logging.getLogger("shiguang.hotkey")

# ---- Windows 常量 ----
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_KEY_NAMES = {
    "SPACE": 0x20, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B,
    "ENTER": 0x0D, "RETURN": 0x0D, "BACKSPACE": 0x08,
    "INSERT": 0x2D, "DELETE": 0x2E, "DEL": 0x2E,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}


def parse(spec: str) -> Optional[Tuple[int, int]]:
    """把 ``"Ctrl+Shift+T"`` 解析成 ``(modifiers, virtual_key)``；非法返回 None。"""
    if not spec:
        return None
    mods = 0
    vk = None
    for raw in spec.replace(" ", "").split("+"):
        part = raw.strip()
        if not part:
            continue
        key = part.upper()
        if key in ("CTRL", "CONTROL", "CMDORCTRL"):
            mods |= MOD_CONTROL
        elif key == "SHIFT":
            mods |= MOD_SHIFT
        elif key in ("ALT", "OPTION"):
            mods |= MOD_ALT
        elif key in ("WIN", "META", "SUPER", "CMD", "COMMAND"):
            mods |= MOD_WIN
        elif len(key) == 1 and key.isalnum():
            vk = ord(key)
        elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
            vk = 0x70 + int(key[1:]) - 1
        elif key in _KEY_NAMES:
            vk = _KEY_NAMES[key]
        else:
            return None
    if vk is None:
        return None
    return mods, vk


def normalize(spec: str) -> str:
    """规范化用户输入，例如 ``"ctrl + shift + t"`` -> ``"Ctrl+Shift+T"``。"""
    parsed = parse(spec)
    if not parsed:
        return ""
    mods, vk = parsed
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_WIN:
        parts.append("Win")
    if 0x41 <= vk <= 0x5A:
        parts.append(chr(vk))
    elif 0x30 <= vk <= 0x39:
        parts.append(chr(vk))
    elif 0x70 <= vk <= 0x87:
        parts.append(f"F{vk - 0x70 + 1}")
    else:
        inverse = {v: k for k, v in _KEY_NAMES.items()}
        parts.append(inverse.get(vk, "?"))
    return "+".join(parts)


class GlobalHotkey:
    """注册一个全局快捷键，触发时回调 ``callback()``（在子线程执行）。"""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._current: str = ""
        self._active = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def current(self) -> str:
        return self._current

    # ------------------------------------------------------------------
    def register(self, spec: str) -> Tuple[bool, str]:
        """注册（会先注销旧的）。返回 ``(是否成功, 错误说明)``。"""
        self.unregister()
        spec = normalize(spec or "")
        if not spec:
            return False, "快捷键格式无法识别"
        parsed = parse(spec)
        if parsed is None:
            return False, "快捷键格式无法识别"
        if sys.platform.startswith("win"):
            ok, msg = self._register_win(parsed)
        else:
            ok, msg = self._register_portable(spec)
        if ok:
            self._current = spec
            self._active = True
        return ok, msg

    def unregister(self) -> None:
        with self._lock:
            if sys.platform.startswith("win"):
                self._unregister_win()
            else:
                self._unregister_portable()
            self._active = False

    # ------------------------------------------------------------------
    # Windows 原生实现
    # ------------------------------------------------------------------
    def _register_win(self, parsed: Tuple[int, int]) -> Tuple[bool, str]:
        mods, vk = parsed
        holder: dict = {"ok": False, "err": "", "ready": threading.Event()}

        def worker() -> None:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self._thread_id = kernel32.GetCurrentThreadId()
            hotkey_id = 0xA5A5
            try:
                if not user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                    holder["err"] = "该快捷键已被其他程序占用"
                    holder["ready"].set()
                    return
                holder["ok"] = True
                holder["ready"].set()
                msg = ctypes.wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                    if msg.message == WM_HOTKEY:
                        try:
                            self._callback()
                        except Exception:  # noqa: BLE001
                            log.exception("热键回调异常")
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception as exc:  # noqa: BLE001
                holder["err"] = str(exc)
                holder["ready"].set()

        self._thread = threading.Thread(target=worker, name="shiguang-hotkey", daemon=True)
        self._thread.start()
        holder["ready"].wait(timeout=2.0)
        return holder["ok"], holder["err"]

    def _unregister_win(self) -> None:
        if not self._thread_id:
            return
        try:
            ctypes.windll.user32.UnregisterHotKey(None, 0xA5A5)
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        self._thread_id = 0
        self._thread = None

    # ------------------------------------------------------------------
    # 跨平台实现（pynput 可选依赖）
    # ------------------------------------------------------------------
    def _register_portable(self, spec: str) -> Tuple[bool, str]:
        try:
            from pynput import keyboard  # type: ignore
        except Exception:  # noqa: BLE001
            return False, "当前平台需安装 pynput 才能使用全局快捷键"

        combo = spec.replace("Ctrl", "<ctrl>").replace("Shift", "<shift>").replace("Alt", "<alt>")
        combo = combo.replace("Win", "<cmd>")
        parts = combo.split("+")
        combo = "+".join(p.lower() if not p.startswith("<") else p for p in parts)

        try:
            def worker() -> None:
                try:
                    with keyboard.GlobalHotKeys({combo: self._callback}) as listener:
                        self._portable_listener = listener
                        listener.join()
                except Exception:  # noqa: BLE001
                    log.exception("pynput 监听失败")

            self._portable_listener = None
            self._thread = threading.Thread(target=worker, name="shiguang-hotkey", daemon=True)
            self._thread.start()
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def _unregister_portable(self) -> None:
        listener = getattr(self, "_portable_listener", None)
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
        self._portable_listener = None
        self._thread = None


# ctypes.wintypes 需要显式导入才会被挂到 ctypes 上
if sys.platform.startswith("win"):
    import ctypes.wintypes  # noqa: E402,F401
