# -*- coding: utf-8 -*-
"""系统托盘。

驱动方式：pystray 在**子线程**里跑自己的消息循环，因此所有菜单回调都必须
先把动作投递到主线程队列（``post`` 由 App 注入），不能在回调里直接碰 Tk。

❗ 曾经踩过的坑（真 bug）：这里的动作名必须与 ``app._handle_action`` 里的分支**完全一致**。
    之前这里写的是 ``item("toggle")``，而 App 只认 ``"toggle_window"``，
    两边名字对不上 + ``_handle_action`` 没有 else 兜底 → 点击托盘图标后事件
    确实触发了，但动作被静默丢弃，表现为"托盘图标点了没反应，快捷键却正常"。
    现在统一使用 ``ACTION_TOGGLE`` 常量，并且 App 会给未知动作写日志。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("shiguang.tray")

# 动作名常量：托盘与 App 共用，避免再次出现"名字对不上"的静默失败
ACTION_TOGGLE = "toggle_window"
ACTION_QUICK_ADD = "quick_add"
ACTION_STATS = "stats"
ACTION_ABOUT = "about"
ACTION_QUIT = "quit"


class Tray:
    """托盘图标与右键菜单。

    单击行为约定：**切换显隐** —— 窗口可见则隐藏到托盘，不可见则恢复并聚焦。
    双击与单击等价（pystray 的 Windows 后端会让每次 LBUTTONUP 都激活 default 项，
    双击会连发两次，因此 App 侧对 toggle 做了 350ms 去抖，避免"闪一下就没了"）。
    """

    def __init__(
        self,
        icon_path: Path,
        post: Callable[[str, object], None],
        summary_provider: Callable[[], str],
    ) -> None:
        """``post(action, payload)``：把动作丢给主线程；``summary_provider``：托盘提示文案。"""
        self.icon_path = Path(icon_path)
        self.post = post
        self.summary_provider = summary_provider
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._available = False

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> bool:
        """启动托盘（失败返回 False，不影响主窗口使用）。"""
        try:
            import pystray
            from PIL import Image
        except Exception as exc:  # noqa: BLE001
            log.warning("托盘不可用（缺少 pystray/Pillow）：%s", exc)
            return False

        try:
            if not self.icon_path.exists():
                from .assets_gen import ensure_icon

                ensure_icon()
            image = Image.open(self.icon_path)

            def item(action: str) -> Callable:
                return lambda *_: self.post(action, None)

            menu = pystray.Menu(
                # default=True 的项就是"单击/激活"时触发的项（见模块 docstring）
                pystray.MenuItem("显示 / 隐藏", item(ACTION_TOGGLE), default=True),
                pystray.MenuItem("快速添加任务", item(ACTION_QUICK_ADD)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: self.summary_provider(), None, enabled=False),
                pystray.MenuItem("打开统计", item(ACTION_STATS)),
                pystray.MenuItem("关于拾光", item(ACTION_ABOUT)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", item(ACTION_QUIT)),
            )
            self._icon = pystray.Icon(
                "shiguang",
                icon=image,
                title="拾光 · 拾起每一天",
                menu=menu,
            )
            self._thread = threading.Thread(target=self._run, name="shiguang-tray", daemon=True)
            self._thread.start()
            self._available = True
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("托盘启动失败：%s", exc)
            return False

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception as exc:  # noqa: BLE001
            log.warning("托盘消息循环退出：%s", exc)

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
        self._icon = None
        self._available = False

    def refresh(self) -> None:
        """菜单文案需要重算时调用（pystray 会重新求值 lambda）。"""
        if self._icon is None:
            return
        try:
            self._icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def notify(self, title: str, message: str) -> bool:
        """托盘气泡通知（Windows 支持）。返回是否成功，供 notify.py 决定是否降级。"""
        if self._icon is None:
            return False
        try:
            self._icon.notify(message, title)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("托盘气泡通知失败：%s", exc)
            return False

    def set_tooltip(self, text: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.title = text
        except Exception:  # noqa: BLE001
            pass
