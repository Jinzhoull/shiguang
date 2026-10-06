# -*- coding: utf-8 -*-
"""数据模型：Task / Group。

两者都用 dataclass 表示，``from_dict`` / ``to_dict`` 负责与 JSON 互转，
字段缺失时回退到默认值，保证旧版本数据文件升级后仍可读取。
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

# 优先级：0=高（朱红） 1=中（暖橙） 2=低（淡金）
PRIORITY_HIGH, PRIORITY_MID, PRIORITY_LOW = 0, 1, 2
PRIORITY_NAMES = {PRIORITY_HIGH: "高", PRIORITY_MID: "中", PRIORITY_LOW: "低"}
PRIORITY_ORDER = (PRIORITY_HIGH, PRIORITY_MID, PRIORITY_LOW)

# 截止日期的存储格式：ISO 8601，精确到分钟（秒对"任务"这个粒度没有意义）
DUE_FORMAT = "%Y-%m-%dT%H:%M"

# 卡片上的紧凑显示格式
DUE_DISPLAY = "%m/%d %H:%M"

# --------------------------------------------------------------------------
# 到期提醒提前量（``Task.remind_offset``，单位：分钟）
# --------------------------------------------------------------------------
# 语义（UI 与提醒服务共用这一份定义，不许再各写一张表）：
#     None  -> 不提醒（提醒轮询会**跳过**该任务）
#     0     -> 准时提醒（越过截止时间的那一刻）
#     n > 0 -> 提前 n 分钟
REMIND_OFF_TIME = 0           # 准时提醒
REMIND_DEFAULT = 15           # 新建任务的默认提前量（需求指定"提前15分钟"）

#: 下拉选项 (值, 文案)。顺序即界面顺序；文案是**字段取值域**的一部分，
#: 所以跟字段定义放在一起 —— 换标签必须同时看提醒口径，不该隔着两个模块。
REMIND_OPTIONS = (
    (None, "不提醒"),
    (REMIND_OFF_TIME, "准时提醒"),
    (5, "提前5分钟"),
    (REMIND_DEFAULT, "提前15分钟"),
    (30, "提前30分钟"),
    (60, "提前1小时"),
    (1440, "提前1天"),
)
REMIND_LABELS = dict(REMIND_OPTIONS)


def new_id(prefix: str = "t") -> str:
    """生成短 ID（12 位十六进制，可读性优于完整 UUID）。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def parse_due(raw: Any) -> Optional[_dt.datetime]:
    """把任意存储值解析成 ``datetime``；无法识别时返回 ``None``。

    为什么要写得这么宽容？
        截止日期是后加的字段，用户数据文件里可能存在：
        手工改过的字符串、旧版本没有该字段、时区/格式各异的时间戳。
        任何一条解析失败都不应该让整个数据文件读不出来 —— 统一降级为"没有截止日期"。

    只给日期（如 ``2026-09-24``）时按**当天 23:59** 处理：
        用户写"9 月 24 日截止"的直觉是那一整天都还能做，
        若按 00:00 处理会让任务在当天凌晨就立刻变成"已逾期"。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, _dt.datetime):
        return raw.replace(second=0, microsecond=0)
    if isinstance(raw, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(raw)).replace(second=0, microsecond=0)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(raw).strip()
    if not text:
        return None
    for fmt, is_date_only in (
        ("%Y-%m-%dT%H:%M:%S", False),
        ("%Y-%m-%dT%H:%M", False),
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%Y-%m-%d", True),
    ):
        try:
            parsed = _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if is_date_only:
            return parsed.replace(hour=23, minute=59, second=0, microsecond=0)
        return parsed.replace(second=0, microsecond=0)
    return None


def format_due(when: Optional[_dt.datetime]) -> str:
    """序列化成存储字符串（``None`` -> ``None``）。"""
    if when is None:
        return ""
    return when.strftime(DUE_FORMAT)


def parse_remind(raw: Any) -> Optional[int]:
    """把存储值收敛成合法的提前量；无法识别一律当"不提醒"。

    容错理由与 :func:`parse_due` 完全相同：这是后加字段，用户手改文件、
    旧版本没有它、别的程序写进字符串都可能发生 —— 任何一个都不该让整份
    数据文件读不出来。区别是这里没有"猜一个合理值"的空间，认不出来就
    老实按"不提醒"处理（宁可不提醒，也不要半夜为一条脏数据弹通知）。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):      # True/False 不是合法的分钟数
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def remind_label(offset: Optional[int]) -> str:
    """提前量 -> 界面文案；未知值也给一句能看懂的兜底。"""
    if offset in REMIND_LABELS:
        return REMIND_LABELS[offset]
    return f"提前{int(offset)}分钟"


# --------------------------------------------------------------------------
# 组内显示顺序（1.5.7）
# --------------------------------------------------------------------------
# 排序规则**只有这一处实现**（store / 页面 / 导出全部调它）。历史上 UI 层
# 各写了一份"sorted(..., key=done)"，于是同一份数据在不同视图里顺序不一样；
# 现在顺序是**派生值**，不再是往 ``Task.order`` 里存出来的结果。
def sort_key(task: "Task") -> tuple:
    """组内显示顺序的排序键：``sorted(tasks, key=sort_key)``。

    规则（用户口径，别在页面里再抄一份）：

    1. **未完成永远排在已完成之上**（第 0 位 0 / 1）——
       所以"未完成一组、已完成一组，两组内部再各自排序"是自然结果，
       不需要在 UI 里手工插入分隔。
    2. 未完成按**截止日期升序**：最近的排最前；**没有截止日期的垫底**。
    3. 已完成按**完成时间降序**：最近完成的在前。
    4. 同键时依次用 ``order``（拖拽留下的手排位）和 ``created_at`` 兜底 ——
       排序必须**稳定**：刷新前后、重启之后同一条任务不能自己换位置，
       而且"同一天到期的几条"仍然可以靠拖拽调先后。

    元组前两位一定不同源比较：已完成那支的第 1 位是"完成时间取负"，
    未完成那支的第 1 位是"有无截止"，两者只在第 0 位相同时才会被比较，
    而第 0 位一旦相同，两支的第 1 位就是同一种含义（同为日期/同为时间），
    所以混合排序不会出现"时间戳和 0/1 比大小"的错位。
    """
    order = int(task.order)
    created = float(task.created_at)
    if task.done:
        # 完成时间取负 = 降序；异常数据（done 但没有 done_at）当最旧，沉底。
        stamp = float(task.done_at) if task.done_at else 0.0
        return (1, -stamp, 0.0, order, created)
    due = task.due_date
    if due is None:
        return (0, 1, 0.0, order, created)
    return (0, 0, due.timestamp(), order, created)


def sorted_tasks(tasks) -> list:
    """按 :func:`sort_key` 排好序的**新列表**（不改动入参顺序）。"""
    return sorted(tasks, key=sort_key)


@dataclass
class Task:
    """一条任务（一缕光）。"""

    id: str = field(default_factory=lambda: new_id("t"))
    title: str = ""
    group_id: str = ""
    priority: int = PRIORITY_MID
    done: bool = False
    created_at: float = field(default_factory=time.time)
    done_at: Optional[float] = None
    order: int = 0
    pomodoros: int = 0          # 累计完成的番茄数
    note: str = ""
    # 截止日期（None = 无截止日期）。存储时转 ISO 字符串，见 to_dict。
    due_date: Optional[_dt.datetime] = None
    # 到期提醒提前量（分钟）：None = 不提醒，0 = 准时提醒，n = 提前 n 分钟。
    # 取值域见 REMIND_OPTIONS，默认「提前15分钟」。
    remind_offset: Optional[int] = REMIND_DEFAULT

    # ---------------- 序列化 ----------------
    def to_dict(self) -> Dict[str, Any]:
        """转成可直接 json.dump 的结构。

        注意：不能直接用 ``asdict(self)`` —— ``due_date`` 是 datetime 对象，
        json 序列化会直接抛 TypeError。必须在这里手工转成 ISO 字符串。
        """
        raw = asdict(self)
        raw["due_date"] = format_due(self.due_date) or None
        return raw

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Task":
        return cls(
            id=str(raw.get("id") or new_id("t")),
            title=str(raw.get("title", "")),
            group_id=str(raw.get("group_id", "")),
            priority=int(raw.get("priority", PRIORITY_MID)),
            done=bool(raw.get("done", False)),
            created_at=float(raw.get("created_at", time.time())),
            done_at=(float(raw["done_at"]) if raw.get("done_at") else None),
            order=int(raw.get("order", 0)),
            pomodoros=int(raw.get("pomodoros", 0)),
            note=str(raw.get("note", "")),
            # 旧数据没有这个字段 -> parse_due(None) -> None，天然兼容
            due_date=parse_due(raw.get("due_date")),
            # 提醒字段同样是后加的，但**缺字段**与**显式 null** 语义不同：
            #   缺字段（旧版本写的）-> 用默认提前量，升级后不至于静默失去提醒；
            #   显式 null（用户选了"不提醒"）-> None，必须原样保留。
            # 所以这里不能用 raw.get(..., 默认值) 一把梭 —— 那会把"不提醒"
            # 也当成"没这个字段"，用户设了不提醒、重启一次又开始提醒。
            remind_offset=(REMIND_DEFAULT if "remind_offset" not in raw
                           else parse_remind(raw.get("remind_offset"))),
        )

    # ---------------- 行为 ----------------
    def complete(self) -> None:
        self.done = True
        self.done_at = time.time()

    def reopen(self) -> None:
        self.done = False
        self.done_at = None

    @property
    def priority_name(self) -> str:
        return PRIORITY_NAMES.get(self.priority, "中")

    # ---------------- 截止日期 ----------------
    @property
    def due_state(self) -> str:
        """相对**当前时间**的状态：``none`` / ``future`` / ``today`` / ``overdue``。

        只看时间，不看 ``done``：已完成任务的视觉由 UI 层单独处理
        （灰字 + 删除线，不显示逾期竖线）。这样"时间事实"和"呈现方式"解耦，
        统计口径和界面样式不会互相污染。
        """
        if self.due_date is None:
            return "none"
        now = _dt.datetime.now()
        if self.due_date < now:
            return "overdue"
        if self.due_date.date() == now.date():
            return "today"
        return "future"

    @property
    def is_overdue(self) -> bool:
        """已逾期 = 截止时间已过 **且** 任务未完成（口径与需求一致）。"""
        return self.due_state == "overdue" and not self.done

    @property
    def is_due_today(self) -> bool:
        """今天到期（含今天稍后到期；已逾期不算，避免两条提醒重复计数）。"""
        return self.due_state == "today" and not self.done

    @property
    def due_label(self) -> str:
        """卡片上的紧凑文案，形如 ``09/24 18:30``；无截止日期返回空串。"""
        if self.due_date is None:
            return ""
        return self.due_date.strftime(DUE_DISPLAY)

    def minutes_until_due(self) -> Optional[float]:
        """距离截止还有多少分钟（已过期为负数）。无截止日期返回 None。"""
        if self.due_date is None:
            return None
        return (self.due_date - _dt.datetime.now()).total_seconds() / 60.0

    # ---------------- 到期提醒 ----------------
    @property
    def remind_text(self) -> str:
        """提前量的界面文案（"不提醒" / "提前15分钟" / …）。"""
        return remind_label(self.remind_offset)

    @property
    def remind_enabled(self) -> bool:
        """是否参与提醒轮询（没有截止日期就没什么可提醒的）。"""
        return self.remind_offset is not None and self.due_date is not None


def task_matches_filter(task: "Task", query: str = "", filter_key: str = "all",
                        today: Optional[_dt.date] = None) -> bool:
    """纯任务筛选规则，供任务页和数据层自检共用。"""
    normalized = (query or "").strip().casefold()
    if normalized and normalized not in (task.title + " " + task.note).casefold():
        return False
    current_day = today or _dt.date.today()
    if filter_key == "today":
        return (not task.done and task.due_date is not None
                and task.due_date.date() == current_day)
    if filter_key == "overdue":
        return task.is_overdue
    if filter_key == "open":
        return not task.done
    if filter_key == "done":
        return task.done
    return True


@dataclass
class Group:
    """任务分组。"""

    id: str = field(default_factory=lambda: new_id("g"))
    name: str = "新分组"
    icon: str = "🌿"
    order: int = 0
    collapsed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Group":
        return cls(
            id=str(raw.get("id") or new_id("g")),
            name=str(raw.get("name", "新分组")),
            # 这里**必须**校验类型，不能简单 str()：
            # 万一文件里混进了 "<bound method ... at 0x...>" 这种字符串（旧版本写坏的），
            # str() 会原样保留，然后被渲染进右键菜单。非 str 或形如内存地址的值一律丢弃。
            icon=_sanitize_icon(raw.get("icon")),
            order=int(raw.get("order", 0)),
            collapsed=bool(raw.get("collapsed", False)),
        )


# 形如 "<bound method ... at 0x0000023A...>" / "<function ... at 0x...>" 的脏值特征
_MEMORY_ADDR_RE = re.compile(r"<(?:bound\s+)?(?:method|function)\b.*?\bat\s+0x[0-9a-fA-F]+")


def _sanitize_icon(value: Any, fallback: str = "🌿") -> str:
    """把文件里读到的图标收敛成可用字符串；识别并丢弃内存地址样式的脏值。"""
    if isinstance(value, str):
        text = value.strip()
        if text and not _MEMORY_ADDR_RE.search(text):
            return text
    if value not in (None, ""):
        logging.getLogger("shiguang.models").warning(
            "分组图标是脏值 %r（type=%s），已回退为 %r",
            value, type(value).__name__, fallback)
    return fallback
