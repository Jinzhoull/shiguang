# -*- coding: utf-8 -*-
"""截止日期提醒：后台轮询 + 去重结算。

架构决定（为什么线程只负责"敲钟"）
----------------------------------
需求写的是"起一个 daemon 线程每 60 秒检查一次"。但直接让子线程去读 ``Store``
是危险的：主线程随时可能在增删任务，两个线程同时遍历同一个列表，轻则读到
中间态，重则抛异常。

所以这里的分工是：
    * ``ReminderService`` 的 daemon 线程**只做一件事** —— 每 60 秒往主线程队列
      投递一个 ``("due_tick", None)``；
    * 真正的判定与通知由主线程 ``ShiguangApp._handle_action`` 调 ``evaluate()`` 完成。

这样既满足"后台每 60 秒检查一次"，又保证数据只在主线程被访问 —— 和托盘/热键
回调走的是同一套线程安全约定，不会引入第二套并发模型。

去重规则：每个任务只提醒一次。已提醒的 task_id 存在内存 Set 里
（不落盘 —— 重启后重新提醒一次是合理行为，也避免配置文件被塞满历史 ID）。

提醒与否由**任务自己的** ``remind_offset`` 决定（1.5.3 新增）：
``None`` = 不提醒（轮询跳过该任务）、``0`` = 准时提醒、``n`` = 提前 n 分钟。
判定实现只有一处：:func:`shiguang.stats.remind_due_tasks`。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import stats
from .models import Task
from .store import Store

log = logging.getLogger("shiguang.reminder")


class ReminderService:
    """截止日期提醒的后台节拍器 + 结算逻辑。"""

    INTERVAL = 60          # 秒

    def __init__(self, store: Store, post: Callable[[str, object], None],
                 enabled: Optional[Callable[[], bool]] = None) -> None:
        self.store = store
        self.post = post
        self.enabled = enabled or (lambda: True)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.reminded: Set[str] = set()      # 已提醒过的 task_id

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="shiguang-reminder",
                                        daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        # 首轮延迟 5 秒，避开启动时的界面构建高峰
        if self._stop.wait(5.0):
            return
        while True:
            try:
                if self.enabled():
                    self.post("due_tick", None)
            except Exception as exc:  # noqa: BLE001
                log.warning("提醒节拍投递失败：%s", exc)
            if self._stop.wait(self.INTERVAL):
                break

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    # ------------------------------------------------------------------
    # 去重（纯函数，便于单测）
    # ------------------------------------------------------------------
    @staticmethod
    def dedupe_probe(ids: List[str], seen: Set[str]) -> List[str]:
        """返回 ``ids`` 中尚未提醒过的部分。

        保持原顺序、并在本次调用内去重（同一个 id 传入两次也只算一次）。
        纯函数：不修改 ``seen``，便于测试。
        """
        out: List[str] = []
        for item in ids:
            if item not in seen and item not in out:
                out.append(item)
        return out

    # ------------------------------------------------------------------
    # 结算（必须在主线程调用）
    # ------------------------------------------------------------------
    def evaluate(self) -> List[Tuple[str, Task]]:
        """算出这一轮该提醒哪些任务，并把这些 id 记为"已提醒"。

        判定口径自 1.5.3 起**按任务自己的 ``remind_offset``** 走
        （见 :func:`stats.remind_due_tasks`）：
        ``None`` = 不提醒，轮询直接跳过；``0`` = 准时提醒；``n>0`` = 提前 n 分钟。
        全局的 ``due_soon_minutes`` 因此退居旧键（保留只为读老配置文件不报错），
        不再参与判定 —— 否则"某任务设了不提醒却仍被全局阈值提醒"，与新功能矛盾。

        返回 ``[(kind, task), ...]``：``"just"`` 表示已经到点/越过截止，
        ``"soon"`` 表示还在截止时间之前。已提醒过的不再返回。
        """
        settings = self.store.settings
        grace_minutes = int(settings.get("due_just_minutes", 5) or 5)
        candidates = stats.remind_due_tasks(self.store, grace_minutes)

        pending = self.dedupe_probe([t.id for t in candidates], self.reminded)
        by_id: Dict[str, Task] = {t.id: t for t in candidates}

        out: List[Tuple[str, Task]] = []
        for task_id in pending:
            task = by_id.get(task_id)
            if task is None:
                continue
            left = task.minutes_until_due()
            kind = "just" if (left is not None and left <= 0) else "soon"
            out.append((kind, task))
            self.reminded.add(task_id)
        return out

    def forget(self, task_id: str) -> None:
        """任务被删除或改了截止日期时，把它的提醒记录清掉。

        否则同一任务改过时间后不会再收到提醒 —— 这是很容易漏掉的一环。
        """
        self.reminded.discard(task_id)

    def reset(self) -> None:
        self.reminded.clear()

    # ------------------------------------------------------------------
    @staticmethod
    def build_message(items: List[Tuple[str, Task]]) -> Tuple[str, str]:
        """把待提醒任务合成一条通知文案。

        多条时合并成"你有 N 项任务已到期"（需求 8），单条时给出任务标题与截止时间。
        """
        if not items:
            return "", ""
        title = "拾光 · 任务到期提醒"
        if len(items) == 1:
            kind, task = items[0]
            when = task.due_date.strftime("%m/%d %H:%M") if task.due_date else ""
            prefix = "已到期" if kind == "just" else "即将到期"
            return title, f"{task.title}（{prefix} · {when}）"

        just = sum(1 for kind, _ in items if kind == "just")
        soon = len(items) - just
        parts = []
        if just:
            parts.append(f"{just} 项已到期")
        if soon:
            parts.append(f"{soon} 项即将到期")
        names = "、".join(t.title for _, t in items[:3])
        more = "…" if len(items) > 3 else ""
        return title, f"你有 {'，'.join(parts)}：{names}{more}"
