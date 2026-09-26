# -*- coding: utf-8 -*-
"""番茄计时器。

只做一件事：以 1 秒为粒度倒数，并在每个 tick 回调里把状态喂给 UI。
计时结束播放轻柔提示音（见 assets_gen.play）。

注意：计时器由 App 持有**唯一实例**，同一时刻只允许一个任务在专注。
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable, Optional


class Pomodoro:
    """25 分钟专注倒计时。"""

    def __init__(self, root: tk.Misc, on_tick: Callable[[dict], None],
                 on_finish: Callable[[Optional[str]], None]) -> None:
        self.root = root
        self.on_tick = on_tick
        self.on_finish = on_finish
        self.task_id: Optional[str] = None
        self.total = 25 * 60
        self.remaining = 0
        self.running = False
        self._end_at = 0.0
        self._job: Optional[str] = None

    # ------------------------------------------------------------------
    @property
    def label(self) -> str:
        """``mm:ss`` 文本。"""
        secs = max(0, self.remaining)
        return f"{secs // 60:02d}:{secs % 60:02d}"

    @property
    def state(self) -> dict:
        return {"task_id": self.task_id, "running": self.running, "label": self.label}

    # ------------------------------------------------------------------
    def start(self, task_id: str, minutes: int = 25) -> None:
        self.stop(silent=True)
        self.task_id = task_id
        self.total = max(1, int(minutes)) * 60
        self.remaining = self.total
        self._end_at = time.time() + self.total
        self.running = True
        self._schedule()
        self._emit()

    def toggle(self, task_id: str, minutes: int = 25) -> None:
        """同一任务再次点击 = 停止；否则切换到该任务。"""
        if self.task_id == task_id and self.running:
            self.stop()
        else:
            self.start(task_id, minutes)

    def stop(self, silent: bool = False) -> None:
        self._cancel()
        was = self.running
        self.running = False
        self.task_id = None
        self.remaining = 0
        if not silent:
            self._emit()
        elif was:
            self._emit()

    # ------------------------------------------------------------------
    def _schedule(self) -> None:
        self._cancel()
        self._job = self.root.after(1000, self._tick)

    def _cancel(self) -> None:
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None

    def _tick(self) -> None:
        self._job = None
        if not self.running:
            return
        self.remaining = int(round(self._end_at - time.time()))
        if self.remaining <= 0:
            task_id = self.task_id
            self.running = False
            self.remaining = 0
            self.task_id = None
            self._emit()
            self.on_finish(task_id)
            return
        self._emit()
        self._schedule()

    def _emit(self) -> None:
        try:
            self.on_tick(self.state)
        except Exception:  # noqa: BLE001
            pass
