# -*- coding: utf-8 -*-
"""数据持久化层：本地 JSON 存储。

为什么用 JSON 而不是 SQLite？
    * 数据量级很小（几百条任务），JSON 读写整文件的开销可以忽略；
    * 用户能直接查看/备份/手改数据，符合"绿色版"的直觉；
    * 不需要额外依赖，打包体积更小。

可靠性保障：
    * 原子写：先写 ``data.json.tmp`` 再 ``os.replace``，避免断电/崩溃写坏文件；
    * 损坏恢复：解析失败时把坏文件另存为 ``data.corrupt-<时间戳>.json`` 并重新开始，
      绝不静默丢数据；
    * 字段兼容：读取时用 ``from_dict`` 兜底缺失字段，旧数据文件不会导致崩溃。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import DATA_VERSION, default_data, data_file
from .models import (REMIND_DEFAULT, Group, Task, new_id, parse_due,
                     parse_remind, sort_key)

# 完成一条任务时，history 里用日期做键
DATE_FMT = "%Y-%m-%d"

DEFAULT_ICON = "🌿"


def _safe_icon(value, fallback: str = DEFAULT_ICON) -> str:
    """把任意值收敛成合法的分组图标字符串。

    非字符串一律退回 ``fallback``。**不要**改成 ``str(value)`` ——
    那正是把 ``"<bound method ... at 0x...>"`` 写进数据的原因。
    """
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def date_key(ts: Optional[float] = None) -> str:
    """把时间戳格式化成 ``YYYY-MM-DD``（本地时区）。"""
    return time.strftime(DATE_FMT, time.localtime(ts if ts else time.time()))


class Store:
    """数据仓库。所有 UI 层读写都走这里。"""

    #: 分组图标映射所在的 settings 键（{group_id: icon_key}）
    GROUP_ICONS_KEY = "group_icons"

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else data_file()
        self.data: Dict[str, Any] = default_data()
        self.groups: List[Group] = []
        self.tasks: List[Task] = []
        self.dirty = False
        self.last_save_error = ""
        self.load_warnings: List[str] = []
        self.recovery_backup_path = ""
        self.load()

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def load(self) -> None:
        raw: Dict[str, Any] = {}
        self.load_warnings.clear()
        quarantined = False
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if not isinstance(raw, dict):
                    raise ValueError("根节点不是对象")
            except Exception as exc:  # noqa: BLE001 - 任何损坏都要能恢复
                self._quarantine(exc)
                self.load_warnings.append("本地数据文件无法读取，已保留原文件副本并使用空白数据启动。")
                quarantined = True
                raw = {}

        base = default_data()
        settings = dict(base["settings"])
        raw_settings = raw.get("settings", {})
        if isinstance(raw_settings, dict):
            settings.update(raw_settings)
        elif "settings" in raw:
            self.load_warnings.append("设置内容格式异常，已恢复默认设置。")
        # Settings are user-editable JSON too. A single malformed value must not
        # break startup later (for example int("oops") in the focus timer).
        bool_settings = {
            "always_on_top", "close_to_tray", "hotkey_enabled", "sound_enabled",
            "due_notify", "due_banner", "due_particles", "tray_tip_shown", "onboarded",
        }
        string_settings = {"font_family", "geometry", "window_geometry", "hotkey",
                           "default_group"}
        integer_ranges = {
            "pomodoro_minutes": (1, 120), "break_minutes": (1, 60),
            "due_soon_minutes": (1, 1440), "due_just_minutes": (1, 1440),
        }
        for key, default_value in base["settings"].items():
            if key not in settings:
                continue
            value = settings[key]
            invalid = False
            if key in bool_settings:
                invalid = type(value) is not bool
            elif key in string_settings:
                invalid = not isinstance(value, str)
            elif key == "theme":
                invalid = not isinstance(value, str) or value not in {"system", "light", "dark"}
            elif key in integer_ranges:
                low, high = integer_ranges[key]
                try:
                    if isinstance(value, bool):
                        raise ValueError("布尔值不是时长")
                    parsed = int(value)
                    invalid = not low <= parsed <= high
                    if not invalid and type(value) is not int:
                        settings[key] = parsed
                except (TypeError, ValueError, OverflowError):
                    invalid = True
            if invalid:
                settings[key] = default_value
                self.load_warnings.append(f"设置“{key}”格式异常，已恢复默认值。")
        # 旧数据文件里没有 group_icons（本轮新增），补齐并保证类型正确 ——
        # 用户手改过文件时它可能是任意类型，后续 set_group_icon 会直接写进去。
        if not isinstance(settings.get(self.GROUP_ICONS_KEY), dict):
            if self.GROUP_ICONS_KEY in settings:
                self.load_warnings.append("分组图标设置格式异常，已恢复默认图标。")
            settings[self.GROUP_ICONS_KEY] = {}

        groups_raw = raw.get("groups")
        if not isinstance(groups_raw, list) or not groups_raw:
            if "groups" in raw:
                if not isinstance(groups_raw, list):
                    self.load_warnings.append("分组列表格式异常，已恢复默认分组。")
                elif not groups_raw:
                    self.load_warnings.append("分组列表为空，已恢复默认分组。")
            groups_raw = base["groups"]
        tasks_raw = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
        if "tasks" in raw and not isinstance(raw.get("tasks"), list):
            self.load_warnings.append("任务列表格式异常，已恢复为空列表。")
        history_raw = raw.get("history", {})
        history = history_raw if isinstance(history_raw, dict) else {}
        if "history" in raw and not isinstance(history_raw, dict):
            self.load_warnings.append("完成历史格式异常，已恢复为空。")
        focus_raw = raw.get("focus_history", {})
        focus = focus_raw if isinstance(focus_raw, dict) else {}
        if "focus_history" in raw and not isinstance(focus_raw, dict):
            self.load_warnings.append("专注历史格式异常，已恢复为空。")

        def safe_count(value: Any, label: str) -> int:
            """读入统计计数时拒绝坏值，不让一个脏数字阻止整个应用启动。"""
            try:
                if isinstance(value, bool):
                    raise ValueError("布尔值不是计数")
                number = int(value)
                if isinstance(value, float) and value != number:
                    raise ValueError("计数不能是小数")
                if number < 0:
                    raise ValueError("计数不能为负数")
                return number
            except (TypeError, ValueError, OverflowError):
                self.load_warnings.append(f"{label}格式异常，已按 0 处理。")
                return 0

        safe_history = {
            str(key): safe_count(value, "完成历史")
            for key, value in history.items()
        }
        safe_focus = {
            str(key): safe_count(value, "专注历史")
            for key, value in focus.items()
        }
        version = safe_count(raw.get("version", DATA_VERSION), "数据版本") or DATA_VERSION

        self.data = {
            "version": version,
            "settings": settings,
            "history": safe_history,
            "total_completed": safe_count(raw.get("total_completed", 0), "累计完成数"),
            # 1.5.11：每日专注记录。旧文件缺字段按 0 处理（无需迁移），
            # 脏值（非 dict / 非数字）一律归零，不让坏数据把启动流程炸掉。
            "focus_history": safe_focus,
            "total_focus": safe_count(raw.get("total_focus", 0), "累计专注数"),
        }
        self.groups = []
        for item in groups_raw:
            if not isinstance(item, dict):
                self.load_warnings.append("有一个分组记录格式异常，已跳过。")
                continue
            try:
                self.groups.append(Group.from_dict(item))
            except (TypeError, ValueError, OverflowError):
                self.load_warnings.append("有一个分组记录无法读取，已跳过。")
        self.tasks = []
        for item in tasks_raw:
            if not isinstance(item, dict):
                self.load_warnings.append("有一条任务记录格式异常，已跳过。")
                continue
            try:
                self.tasks.append(Task.from_dict(item))
            except (TypeError, ValueError, OverflowError):
                self.load_warnings.append("有一条任务记录无法读取，已跳过。")
        self._repair()
        if self.load_warnings and not quarantined:
            self._quarantine(ValueError("数据字段格式异常，已尽量恢复可读取内容"))

    def _quarantine(self, exc: Exception) -> None:
        """数据文件损坏时保留现场，避免静默丢数据。"""
        self.recovery_backup_path = ""
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bad = self.path.with_name(f"data.corrupt-{stamp}.json")
            suffix = 1
            while bad.exists():
                bad = self.path.with_name(f"data.corrupt-{stamp}-{suffix}.json")
                suffix += 1
            shutil.copy2(self.path, bad)
            self.recovery_backup_path = str(bad)
            self.log(f"数据文件解析失败({exc})，已备份到 {bad.name}")
            if self.load_warnings:
                self.load_warnings.append(f"原始数据副本已保存为 {bad.name}。")
        except Exception:  # noqa: BLE001
            self.log(f"数据文件备份失败：{exc}")

    def _repair(self) -> None:
        """修正脏数据：分组顺序、孤立任务、order 连续性。"""
        if not self.groups:
            self.groups = [Group.from_dict(g) for g in default_data()["groups"]]
        self.groups.sort(key=lambda g: g.order)
        for i, g in enumerate(self.groups):
            g.order = i

        valid_ids = {g.id for g in self.groups}
        fallback = self.groups[0].id
        default_group = self.data["settings"].get("default_group")
        if default_group not in valid_ids:
            if default_group:
                self.load_warnings.append("默认分组不存在，已改为第一个分组。")
            self.data["settings"]["default_group"] = fallback
        for t in self.tasks:
            if t.group_id not in valid_ids:
                t.group_id = fallback
        self._normalize_orders()

    def _normalize_orders(self) -> None:
        """把 ``order`` 归一成"同键内的位次"（0 起连续）。

        ``order`` 不再是组内绝对顺序 —— 绝对顺序由 :func:`models.sort_key`
        派生。它只负责**同键兜底**（同一天到期 / 都没有期限的几条任务谁在前），
        归一化是为了拖拽结果稳定、并且旧版本存下来的手排位能被原样继承。
        """
        for g in self.groups:
            items = self.tasks_in(g.id)
            for i, t in enumerate(items):
                t.order = i

    def save(self, force: bool = False) -> bool:
        """原子写入磁盘；返回成功状态，失败时保留 dirty 供重试。"""
        if not self.dirty and not force:
            return True
        payload = {
            "version": DATA_VERSION,
            "settings": self.data["settings"],
            "groups": [g.to_dict() for g in self.groups],
            "tasks": [t.to_dict() for t in self.tasks],
            "history": self.data["history"],
            "total_completed": self.data["total_completed"],
            "focus_history": self.data["focus_history"],
            "total_focus": self.data["total_focus"],
        }
        tmp = self.path.with_suffix(".json.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)   # 原子替换
            self.dirty = False
            self.last_save_error = ""
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_save_error = str(exc)
            self.log(f"保存失败：{exc}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def log(self, message: str) -> None:
        """写一行日志（失败不影响主流程）。"""
        try:
            from .config import log_file

            with open(log_file(), "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    @property
    def settings(self) -> Dict[str, Any]:
        return self.data["settings"]

    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        self.dirty = True

    # ------------------------------------------------------------------
    # 分组
    # ------------------------------------------------------------------
    def group(self, group_id: str) -> Optional[Group]:
        for g in self.groups:
            if g.id == group_id:
                return g
        return None

    def add_group(self, name: str, icon: str = "🌿") -> Group:
        grp = Group(id=new_id("g"), name=name.strip() or "新分组",
                    icon=_safe_icon(icon), order=len(self.groups))
        self.groups.append(grp)
        self.dirty = True
        return grp

    def set_group_icon(self, group_id: str, icon) -> bool:
        """设置分组图标。**非字符串一律拒绝**并返回 False。

        这道校验是必需的：曾经发生过 ``grp.icon`` 被写进函数对象，
        随后 ``f"{grp.icon} {grp.name}"`` 在右键菜单里渲染成
        ``"<bound method ... at 0x0000023A...> 工作"``。

        ``Group.from_dict`` 里的 ``str()`` 兜底救不了它 —— 因为**写入路径不经过
        ``from_dict``**，是直接 ``grp.icon = icon`` 落库的。所以必须在写入口拦住。

        本轮（需求 26）额外写一份 ``settings["group_icons"]`` 映射：
        图标属于"用户偏好"而不是"任务数据"，放在 settings 里才能和主题、快捷键
        这类配置一起备份/迁移，也方便以后给分组图标做批量重置。
        两处同时写是有意的 —— ``Group.icon`` 保住旧版本读取路径，
        settings 映射是新的权威来源（见 :meth:`group_icon`）。
        """
        grp = self.group(group_id)
        if grp is None:
            return False
        if not isinstance(icon, str):
            self.log(f"拒绝把非字符串写入分组图标：{icon!r}（type={type(icon).__name__}）")
            return False
        icon = icon.strip()
        if not icon:
            return False
        grp.icon = icon
        icons_map = self.settings.get(self.GROUP_ICONS_KEY)
        if not isinstance(icons_map, dict):
            icons_map = {}
        icons_map[group_id] = icon
        self.settings[self.GROUP_ICONS_KEY] = icons_map
        self.dirty = True
        return True

    def group_icon(self, group_id: str) -> str:
        """取分组图标的**原始存值**（可能是图标键 ``grp_work``，也可能是历史 emoji）。

        读取顺序：
          1. ``settings["group_icons"][group_id]`` —— 本轮新增的权威来源；
          2. ``Group.icon`` —— 历史字段，旧数据文件只有它；
          3. 空串 —— 交给调用方兜底。

        归一化（emoji → 键）不在这里做：数据层不依赖图标系统，
        由 UI 层的 ``icons.resolve_key()`` 负责。
        """
        icons_map = self.settings.get(self.GROUP_ICONS_KEY)
        if isinstance(icons_map, dict):
            value = icons_map.get(group_id)
            if isinstance(value, str) and value.strip():
                return value.strip()
        grp = self.group(group_id)
        if grp is not None and isinstance(grp.icon, str):
            return grp.icon
        return ""

    def ensure_default_groups(self) -> bool:
        """保证至少存在默认的 工作 / 学习 / 生活 三个分组（需求 24）。

        什么时候会缺：用户手动改过 ``data.json``、或者数据文件里 ``groups`` 是个
        空列表。``load()`` 已经对"空列表"做了兜底，但那是**读取时**的行为 ——
        运行期把分组删到只剩一个、或从旧备份导入之后，仍然可能落到"没有分组"。
        返回 True 表示这次真的补了。

        注意"至少保留一个分组"的规则由 ``remove_group`` 负责，
        本方法只负责"一个都没有"这一种极端情况。
        """
        if self.groups:
            return False
        from .config import DEFAULT_GROUPS

        for raw in DEFAULT_GROUPS:
            grp = Group.from_dict(dict(raw))
            self.groups.append(grp)
        for i, g in enumerate(self.groups):
            g.order = i
        self.dirty = True
        self.log("分组为空，已重建默认三组（工作/学习/生活）")
        return True

    def remove_group(self, group_id: str, move_tasks_to: Optional[str] = None) -> None:
        """删除分组；其中的任务移动到 ``move_tasks_to``，未指定则一起删除。"""
        grp = self.group(group_id)
        if grp is None:
            return
        if len(self.groups) <= 1:
            return  # 至少保留一个分组
        if move_tasks_to and self.group(move_tasks_to):
            for t in self.tasks_in(group_id):
                t.group_id = move_tasks_to
        else:
            self.tasks = [t for t in self.tasks if t.group_id != group_id]
        self.groups = [g for g in self.groups if g.id != group_id]
        for i, g in enumerate(self.groups):
            g.order = i
        self.dirty = True

    def toggle_group_collapsed(self, group_id: str) -> None:
        grp = self.group(group_id)
        if grp:
            grp.collapsed = not grp.collapsed
            self.dirty = True

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------
    def tasks_in(self, group_id: str) -> List[Task]:
        """某分组下的任务，**按显示顺序**（口径见 :func:`models.sort_key`）。

        1.5.7 起顺序是**派生**的，不是存出来的：未完成在上（按截止日期升序，
        无截止日期的垫底），已完成在下（按完成时间降序）。

        ``Task.order`` 因此退化为"同键兜底"的稳定位次 —— 它仍然由拖拽维护，
        所以"同一天到期的几条""都没有期限的几条"依旧能靠拖拽调整先后；
        而一旦截止日期不同，位置就完全由日期说话（这正是需求要的自动排序）。
        """
        items = [t for t in self.tasks if t.group_id == group_id]
        items.sort(key=sort_key)
        return items

    def all_tasks(self) -> List[Task]:
        return list(self.tasks)

    def task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def add_task(
        self,
        title: str,
        group_id: str = "",
        priority: int = 1,
        note: str = "",
        due_date=None,
        remind_offset=REMIND_DEFAULT,
    ) -> Task:
        title = title.strip()
        if not group_id or self.group(group_id) is None:
            group_id = self.groups[0].id
        task = Task(title=title, group_id=group_id, priority=priority, order=0, note=note,
                    due_date=parse_due(due_date),
                    remind_offset=parse_remind(remind_offset))
        self.tasks.append(task)
        # 新任务插到本组**同键最前**（``order=0``，其余同键项顺延）。
        # 有截止日期时位置仍由日期决定；没有日期时它会落在"无期限区"的最上面 ——
        # 新建完抬头就能看见，不至于每次都沉到列表最底部。
        self.move_task_to(task.id, group_id, 0)
        self.dirty = True
        return task

    def update_task(self, task_id: str, **fields: Any) -> Optional[Task]:
        task = self.task(task_id)
        if task is None:
            return None
        old_group = task.group_id
        for key, value in fields.items():
            if not hasattr(task, key):
                continue
            # due_date 允许传字符串/时间戳（例如从对话框回来），统一归一化成 datetime
            if key == "due_date":
                value = parse_due(value)
            # remind_offset 同理：None 是**有意义的值**（不提醒），
            # 不能像别的字段那样"None 就当没传"处理。
            elif key == "remind_offset":
                value = parse_remind(value)
            setattr(task, key, value)
        if task.group_id != old_group:
            # 换分组后重新排到末尾
            task.order = len([t for t in self.tasks_in(task.group_id) if t.id != task.id])
            self._normalize_orders()
        self.dirty = True
        return task

    def set_due(self, task_id: str, when) -> Optional[Task]:
        """设置 / 清除截止日期（``when`` 传 None 表示清除）。"""
        return self.update_task(task_id, due_date=when)

    def remove_task(self, task_id: str) -> None:
        task = self.task(task_id)
        if task is None:
            return
        if task.done:
            self._revert_history(task)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        self._normalize_orders()
        self.dirty = True

    def restore_task(self, raw: Dict[str, Any]) -> Optional[Task]:
        """恢复一条刚删除的任务；保留 ID、截止时间与原排序位次。"""
        try:
            task = Task.from_dict(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if self.task(task.id) is not None:
            return None
        if self.group(task.group_id) is None and self.groups:
            task.group_id = self.groups[0].id
        if task.done:
            key = date_key(task.done_at)
            self.data["history"][key] = int(self.data["history"].get(key, 0)) + 1
            self.data["total_completed"] = int(self.data["total_completed"]) + 1
        self.tasks.append(task)
        self.dirty = True
        return task

    def clear_completed(self) -> int:
        """清空已完成任务，返回删除条数（历史数据保留）。"""
        done = [t for t in self.tasks if t.done]
        if not done:
            return 0
        self.tasks = [t for t in self.tasks if not t.done]
        self._normalize_orders()
        self.dirty = True
        return len(done)

    # ---------------- 完成 / 取消完成 ----------------
    def set_done(self, task_id: str, done: bool) -> Optional[Task]:
        """标记完成状态，并维护按天历史与连续打卡。"""
        task = self.task(task_id)
        if task is None or task.done == done:
            return task
        if done:
            task.complete()
            key = date_key(task.done_at)
            self.data["history"][key] = int(self.data["history"].get(key, 0)) + 1
            self.data["total_completed"] = int(self.data["total_completed"]) + 1
        else:
            key = date_key(task.done_at)
            task.reopen()
            self.data["history"][key] = max(0, int(self.data["history"].get(key, 0)) - 1)
            self.data["total_completed"] = max(0, int(self.data["total_completed"]) - 1)
        self.dirty = True
        return task

    def _revert_history(self, task: Task) -> None:
        key = date_key(task.done_at)
        self.data["history"][key] = max(0, int(self.data["history"].get(key, 0)) - 1)
        self.data["total_completed"] = max(0, int(self.data["total_completed"]) - 1)

    def add_pomodoro(self, task_id: str) -> None:
        """完成任务计时（保留旧名做兼容）：计数 + 任务番茄数一起加。"""
        self.add_focus(task_id)

    def add_focus(self, task_id: str = "") -> None:
        """记录一次完成的专注（番茄）。

        * 每日历史 ``focus_history`` 与总计数 ``total_focus`` **总是**累加 ——
          这两条是统计页的数据源，且不随任务删除 / 清空而丢失；
        * 传入有效 ``task_id`` 时才给对应任务的 ``pomodoros`` +1
          （"自由专注"没有挂靠任务，只进统计）。
        """
        key = date_key()
        self.data["focus_history"][key] = int(self.data["focus_history"].get(key, 0)) + 1
        self.data["total_focus"] = int(self.data["total_focus"]) + 1
        task = self.task(task_id) if task_id else None
        if task:
            task.pomodoros += 1
        self.dirty = True

    # ---------------- 拖拽排序 ----------------
    def move_task_to(self, task_id: str, target_group_id: str, target_index: int) -> None:
        """把任务插入到指定分组的指定位置。

        target_index 为插入位置（0 = 最前，len = 最后）。
        """
        task = self.task(task_id)
        target = self.group(target_group_id)
        if task is None or target is None:
            return
        siblings = [t for t in self.tasks_in(target_group_id) if t.id != task_id]
        target_index = max(0, min(target_index, len(siblings)))
        siblings.insert(target_index, task)
        task.group_id = target_group_id
        for i, t in enumerate(siblings):
            t.order = i
        self._normalize_orders()
        self.dirty = True

    def move_group(self, group_id: str, target_index: int) -> None:
        grp = self.group(group_id)
        if grp is None:
            return
        others = [g for g in self.groups if g.id != group_id]
        target_index = max(0, min(target_index, len(others)))
        others.insert(target_index, grp)
        self.groups = others
        for i, g in enumerate(self.groups):
            g.order = i
        self.dirty = True

    # ------------------------------------------------------------------
    # 历史 / 统计辅助
    # ------------------------------------------------------------------
    @property
    def history(self) -> Dict[str, int]:
        return self.data["history"]

    def history_get(self, key: str) -> int:
        return int(self.data["history"].get(key, 0))

    @property
    def total_completed(self) -> int:
        return int(self.data["total_completed"])

    def focus_history(self) -> Dict[str, int]:
        return self.data["focus_history"]

    def focus_get(self, key: str) -> int:
        return int(self.data["focus_history"].get(key, 0))

    @property
    def total_focus(self) -> int:
        return int(self.data["total_focus"])

    # ------------------------------------------------------------------
    # 导入导出
    # ------------------------------------------------------------------
    def export_csv(self, path: Path, include_done: bool = True) -> int:
        """导出任务为 CSV（UTF-8 BOM，Excel 直接双击不乱码）。

        返回写入的行数。
        """
        import csv

        rows: Iterable[Task] = self.tasks if include_done else [t for t in self.tasks if not t.done]
        group_names = {g.id: g.name for g in self.groups}
        count = 0
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["任务内容", "分组", "优先级", "状态", "截止时间", "提醒", "创建时间", "完成时间",
                 "番茄数", "备注"]
            )
            # 与界面同序（见 models.sort_key）：同分组内完成态沉底，
            # 导出的表直接看就是用户看到的那一列顺序。
            for t in sorted(rows, key=lambda x: (x.group_id, sort_key(x))):
                writer.writerow(
                    [
                        t.title,
                        group_names.get(t.group_id, ""),
                        t.priority_name,
                        "已完成" if t.done else "待完成",
                        t.due_date.strftime("%Y-%m-%d %H:%M") if t.due_date else "",
                        t.remind_text if t.due_date else "",
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(t.created_at)),
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(t.done_at)) if t.done_at else "",
                        t.pomodoros,
                        t.note.replace("\n", " "),
                    ]
                )
                count += 1
        return count

    def export_json(self, path: Path) -> None:
        """导出完整数据备份。"""
        if not self.save(force=True):
            raise OSError(f"无法先保存当前数据，未创建备份：{self.last_save_error}")
        shutil.copy2(self.path, path)

    def import_json(self, path: Path) -> int:
        """从备份恢复完整数据（覆盖当前数据文件），返回恢复的任务条数。

        校验（不合格直接抛 ValueError，由调用方 toast 给用户）：
            * JSON 可解析且根节点是对象；
            * ``groups`` 是非空列表、``tasks`` 是列表 —— 缺任务还能当空档案
              恢复，缺分组就真的是坏档；
            * ``version`` 不高于当前程序版本 —— 高版本备份塞进旧程序会出
              无法预料的字段错位，宁可拒绝。

        通过校验后先把**当前**数据文件留一份现场（``data.pre-restore-*.json``），
        再整体替换并重新加载。``load()`` 自带修复/隔离逻辑，旧版本备份
        （version < DATA_VERSION）缺的字段会按默认值补齐；最后 ``save(force=True)``
        把版本号归一到当前 DATA_VERSION。
        """
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError("备份文件格式不正确")
        if not isinstance(raw.get("groups"), list) or not raw["groups"]:
            raise ValueError("备份文件缺少分组数据")
        if not isinstance(raw.get("tasks"), list):
            raise ValueError("备份文件缺少任务数据")
        version = int(raw.get("version", 0) or 0)
        if version > DATA_VERSION:
            raise ValueError(f"备份来自更新版本（v{version}），请先升级拾光")

        if not self.save(force=True):
            raise OSError(f"当前数据尚未保存，未执行恢复：{self.last_save_error}")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            keep = self.path.with_name(f"data.pre-restore-{stamp}.json")
            shutil.copy2(self.path, keep)
            self.log(f"恢复前已保留当前数据：{keep.name}")
        except Exception:  # noqa: BLE001
            pass
        staged = self.path.with_suffix(".restore.tmp")
        try:
            shutil.copy2(path, staged)
            os.replace(staged, self.path)
        finally:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
        self.load()
        self.dirty = True
        if not self.save(force=True):  # 版本号归一 + 落盘
            raise OSError(f"备份已载入，但整理后的数据未能保存：{self.last_save_error}")
        self.log(f"已从备份恢复 {len(self.tasks)} 条任务（version={version}）")
        return len(self.tasks)
