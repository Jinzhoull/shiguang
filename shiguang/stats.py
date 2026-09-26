# -*- coding: utf-8 -*-
"""统计计算与鼓励文案。

统计口径（重要，避免不同页面出现不一致的数字）：
    * 今日完成数 = history[今天]，按"完成动作"累加，取消完成会回退；
    * 连续打卡天数 = 从今天（或昨天）向前连续有完成记录的天数；
    * 本周趋势 = 本周一 ~ 本周日，未到的日期为 0；
    * 累计拾光 = history 全量求和（等价于 total_completed，用于交叉校验）。
"""

from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Tuple

from . import strings
from .models import Task
from .store import Store, date_key

WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def encouragement(count: int) -> str:
    """根据今日完成数返回一句鼓励。

    实现已挪到 :mod:`shiguang.strings`（需求 ④：文案只能有一处定义）。
    这里保留同名函数，是因为它是 ``stats`` 对外的既有契约，调用点不用改。
    """
    return strings.encourage(count)


def today_count(store: Store) -> int:
    return store.history_get(date_key())


def streak(store: Store) -> int:
    """连续打卡天数。

    规则：今天有完成记录就从今天开始数；今天还没有则从昨天开始数
    （避免"早上刚打开软件就断签"的挫败感）。
    """
    today = _dt.date.today()
    start_offset = 0
    if store.history_get(today.isoformat()) == 0:
        start_offset = 1
    days = 0
    cursor = today - _dt.timedelta(days=start_offset)
    while True:
        if store.history_get(cursor.isoformat()) > 0:
            days += 1
            cursor -= _dt.timedelta(days=1)
        else:
            break
        if days > 3650:   # 安全阀
            break
    return days


def week_trend(store: Store) -> List[Tuple[str, int, bool]]:
    """本周趋势：``[(周几, 完成数, 是否今天), ...]``，周一为第一项。"""
    today = _dt.date.today()
    monday = today - _dt.timedelta(days=today.weekday())
    result: List[Tuple[str, int, bool]] = []
    for i in range(7):
        day = monday + _dt.timedelta(days=i)
        result.append((WEEKDAY_CN[i], store.history_get(day.isoformat()), day == today))
    return result


def week_total(store: Store) -> int:
    return sum(v for _, v, _ in week_trend(store))


def recent_days(store: Store, days: int = 7) -> List[Tuple[str, int, bool]]:
    """最近 N 天趋势（含今天），用于替代视图。"""
    today = _dt.date.today()
    out: List[Tuple[str, int, bool]] = []
    for i in range(days - 1, -1, -1):
        day = today - _dt.timedelta(days=i)
        out.append((str(day.day), store.history_get(day.isoformat()), day == today))
    return out


def summary(store: Store) -> Dict[str, int]:
    """统计面板顶部三个数字。"""
    return {
        "today": today_count(store),
        "streak": streak(store),
        "week": week_total(store),
        "total": store.total_completed,
    }


# --------------------------------------------------------------------------
# 专注（番茄）口径 —— 与完成统计同一套"按天历史 + 全量计数器"的结构，
# 光景页专注卡片是唯一消费方，数字只此一处实现。
# --------------------------------------------------------------------------
def focus_today(store: Store) -> int:
    return store.focus_get(date_key())


def focus_week(store: Store) -> int:
    today = _dt.date.today()
    monday = today - _dt.timedelta(days=today.weekday())
    return sum(store.focus_get((monday + _dt.timedelta(days=i)).isoformat())
               for i in range(7))


def focus_summary(store: Store) -> Dict[str, int]:
    return {
        "today": focus_today(store),
        "week": focus_week(store),
        "total": store.total_focus,
    }


def today_tasks(store: Store) -> List:
    """今天相关的任务：今天创建的，或者今天完成的（昨天遗留的一般不算）。"""
    key = date_key()
    return [
        t for t in store.tasks
        if date_key(t.created_at) == key or (t.done_at and date_key(t.done_at) == key)
    ]


def progress(store: Store) -> Tuple[int, int]:
    """今天任务完成进度 ``(已完成, 总数)``。"""
    todays = today_tasks(store)
    return sum(1 for t in todays if t.done), len(todays)


def all_done_today(store: Store) -> bool:
    """今天是否所有任务都已完成（且至少有一条）。"""
    todays = today_tasks(store)
    return bool(todays) and all(t.done for t in todays)


# --------------------------------------------------------------------------
# 截止日期（提醒条 / 系统通知共用同一套口径，避免两边数字对不上）
# --------------------------------------------------------------------------
def overdue_tasks(store: Store) -> List[Task]:
    """已逾期：截止时间已过且未完成。"""
    return [t for t in store.tasks if t.is_overdue]


def due_today_tasks(store: Store) -> List[Task]:
    """今天稍后到期且未完成（不含已逾期，避免与 overdue 重复计数）。"""
    return [t for t in store.tasks if t.is_due_today]


def just_overdue_tasks(store: Store, within_minutes: int = 5) -> List[Task]:
    """刚刚逾期（``within_minutes`` 分钟内越过截止时间）且未完成的任务。

    为什么要单独一个函数：轮询每 60 秒跑一次，"刚过期"只应该在越线那一刻
    提醒一次。用"过期时长 <= 窗口"来判定，而不是用"已过期"，
    否则每次轮询都会把陈年逾期任务重新翻出来。
    """
    out: List[Task] = []
    for task in store.tasks:
        left = task.minutes_until_due()
        if left is None or task.done:
            continue
        if -within_minutes <= left < 0:
            out.append(task)
    return out


def remind_due_tasks(store: Store, grace_minutes: int = 5) -> List[Task]:
    """按**每条任务自己的** ``remind_offset`` 算出这一轮该提醒哪些任务。

    口径（与下拉选项一一对应，只此一处实现）：
        * ``remind_offset is None`` —— 不提醒，**直接跳过**（需求明确要求
          轮询跳过该任务）；
        * ``remind_offset == 0``    —— 准时提醒：越过截止时间那一刻；
        * ``remind_offset == n > 0``—— 提前 n 分钟：进入"最后 n 分钟"就提醒。

    判定窗口为什么取 ``-grace <= 距截止分钟数 <= 提前量``
    --------------------------------------------------
    轮询节拍是 60 秒，而"刚好等于某一分钟"这种判定在真实时钟下几乎命不中，
    所以窗口下端放宽 ``grace``（复用 ``due_just_minutes``，默认 5 分钟），
    让"越过截止时间"的那一拍一定能被捞到；上端就是任务自己设的提前量。

    这样还顺手解决了两个边界：
        * 新建时就只剩 3 分钟到期的任务（提前量 15）会**立刻**提醒，
          而不是等到"提醒时刻"早已白白错过；
        * 早就逾期几小时的任务不会在每次轮询时反复翻出来。
    """
    grace = max(1, int(grace_minutes))
    out: List[Task] = []
    for task in store.tasks:
        offset = task.remind_offset
        if offset is None or task.done or task.due_date is None:
            continue                      # 不提醒 / 已完成 / 没期限 -> 跳过
        left = task.minutes_until_due()
        if left is None:
            continue
        if -grace <= left <= max(0, int(offset)):
            out.append(task)
    # 按截止时间升序：最紧急的排在通知文案最前面
    out.sort(key=lambda t: t.due_date or _dt.datetime.max)
    return out


def next_due(store: Store) -> Task | None:
    """最近一个未完成、且有截止日期的任务（提醒条排序用）。"""
    candidates = [t for t in store.tasks if t.due_date is not None and not t.done]
    if not candidates:
        return None
    return min(candidates, key=lambda t: t.due_date)
