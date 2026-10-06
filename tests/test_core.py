# -*- coding: utf-8 -*-
"""核心逻辑自检（11 组，不建窗口、不弹窗、不打开日常数据文件）。

运行：``python tests/test_core.py``（或 ``python run.py --selftest``）。

覆盖任务增删改、完成历史、日期与提醒、排序、持久化与损坏恢复、备份导入导出、
专注统计等用户数据关键路径。界面像素和交互由 ``tools/screenshot.py`` 另行检查。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_FAILED: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        _FAILED.append(f"{name}  {detail}".strip())
    print(f"  {'ok  ' if condition else 'FAIL'} {name}"
          + ("" if condition else f"  -> {detail}"))


def _fresh(tmp: Path):
    os.environ["SHIGUANG_DATA_DIR"] = str(tmp)
    from shiguang.store import Store

    return Store(tmp / "data.json")


# ---------------------------------------------------------------- 1
def test_basic_crud(tmp: Path) -> None:
    store = _fresh(tmp / "t1")
    check("默认 3 个分组", [g.name for g in store.groups] == ["工作", "学习", "生活"])
    check("初始任务为空", len(store.all_tasks()) == 0)

    work, study = store.groups[0].id, store.groups[1].id
    t1 = store.add_task("写周报", work, 0)
    store.add_task("改接口文档", work, 1)
    t3 = store.add_task("读十页书", study, 2)
    check("添加 3 条", len(store.all_tasks()) == 3)
    check("组内 order 连续", [t.order for t in store.tasks_in(work)] == [0, 1])

    # 跨组移位。
    # ⚠️ 1.5.7 起组内显示顺序是**派生**的（见 test_sorting）：同键（都没有截止
    #    日期）时才比 order，所以"插到首位"后组内顺序是 读/改/写 —— 后加的两条
    #    因为 order 更小反而在前面。这不是回归，是排序口径变了。
    store.move_task_to(t3.id, work, 0)
    check("跨组移动到首位",
          [t.title for t in store.tasks_in(work)] == ["读十页书", "改接口文档", "写周报"],
          str([t.title for t in store.tasks_in(work)]))
    check("原分组已清空", len(store.tasks_in(study)) == 0)
    store.move_task_to(t3.id, work, 2)
    check("组内移动到最后", [t.title for t in store.tasks_in(work)][-1] == "读十页书",
          str([t.title for t in store.tasks_in(work)]))

    store.update_task(t1.id, title="写周报（终稿）")
    check("改标题", store.task(t1.id).title == "写周报（终稿）")
    store.remove_task(t1.id)
    check("删除任务", store.task(t1.id) is None and len(store.all_tasks()) == 2)

    # 分组删除要把任务迁走，不能连带丢任务
    gid = store.groups[0].id
    store.remove_group(gid, move_tasks_to=study)
    check("删分组后任务已迁移",
          all(t.group_id == study for t in store.all_tasks()),
          str([(t.title, t.group_id) for t in store.all_tasks()]))


# ---------------------------------------------------------------- 2
def test_done_and_history(tmp: Path) -> None:
    store = _fresh(tmp / "t2")
    gid = store.groups[0].id
    t1 = store.add_task("甲", gid)
    t2 = store.add_task("乙", gid)
    today = _dt.date.today().isoformat()

    store.set_done(t1.id, True)
    check("history 今日 +1", store.history_get(today) == 1)
    check("total +1", store.total_completed == 1)
    store.set_done(t1.id, True)
    check("重复完成不重复计数", store.history_get(today) == 1)
    store.set_done(t1.id, False)
    check("取消完成会回退",
          store.history_get(today) == 0 and store.total_completed == 0)

    from shiguang import stats

    store.set_done(t1.id, True)
    store.set_done(t2.id, True)
    check("今日完成数 = 2", stats.today_count(store) == 2)
    check("进度 (2,2)", stats.progress(store) == (2, 2), str(stats.progress(store)))
    check("全部完成判定", stats.all_done_today(store) is True)
    store.set_done(t2.id, False)
    check("取消后不再是全部完成", stats.all_done_today(store) is False)


# ---------------------------------------------------------------- 3
def test_streak(tmp: Path) -> None:
    store = _fresh(tmp / "t3")
    from shiguang import stats

    store.data["history"] = {
        (_dt.date.today() - _dt.timedelta(days=i)).isoformat(): 1 for i in range(3)
    }
    check("连续 3 天 = 3", stats.streak(store) == 3, str(stats.streak(store)))
    store.data["history"][(_dt.date.today() - _dt.timedelta(days=3)).isoformat()] = 0
    check("断签后仍为 3", stats.streak(store) == 3, str(stats.streak(store)))
    # 今天还没完成时从昨天算：避免"早上打开就断签"
    store.data["history"] = {
        (_dt.date.today() - _dt.timedelta(days=1)).isoformat(): 1
    }
    check("今天未完成时从昨天算", stats.streak(store) == 1, str(stats.streak(store)))
    trend = stats.week_trend(store)
    check("本周趋势 7 项", len(trend) == 7)
    check("今日标记唯一", sum(1 for _, _, t in trend if t) == 1)


# ---------------------------------------------------------------- 4
def test_due_parse(tmp: Path) -> None:
    from shiguang.models import parse_due

    ok = _dt.datetime(2026, 9, 21, 18, 30)
    for raw, expect in (("2026-09-21T18:30", ok),
                        ("2026-09-21 18:30", ok),
                        ("2026-09-21T18:30:00", ok)):
        got = parse_due(raw)
        check(f"解析 {raw}", got is not None and got.replace(second=0) == ok, repr(got))
    for bad in (None, "", "  ", "不是日期", "2026-13-45T99:99", [], {}):
        check(f"容错 {bad!r}", parse_due(bad) is None, repr(parse_due(bad)))


# ---------------------------------------------------------------- 5
def test_due_compat(tmp: Path) -> None:
    """序列化：写入 ISO 字符串、读回同值；缺字段的旧数据要降级成"无期限"。"""
    store = _fresh(tmp / "t5")
    gid = store.groups[0].id
    when = _dt.datetime.now().replace(microsecond=0, second=0) \
        + _dt.timedelta(days=3)
    t = store.add_task("带截止", gid, due_date=when)
    store.save(force=True)
    raw = json.loads((tmp / "t5" / "data.json").read_text(encoding="utf-8"))
    stored = raw["tasks"][0].get("due_date")
    check("截止时间写成字符串（不是 datetime 对象）",
          isinstance(stored, str), repr(stored))
    reloaded = _fresh(tmp / "t5")
    check("重载后截止时间一致",
          reloaded.task(t.id).due_date == when,
          repr(reloaded.task(t.id).due_date))

    # 旧数据（没有任何 due 字段）不许崩
    legacy_dir = tmp / "t5legacy"
    legacy = _fresh(legacy_dir)
    legacy.save(force=True)
    raw = json.loads((legacy_dir / "data.json").read_text(encoding="utf-8"))
    raw["version"] = 0
    raw["tasks"] = [{"id": "old1", "title": "祖传任务", "group_id": gid,
                     "done": False, "order": 0, "priority": 0}]
    (legacy_dir / "data.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    again = _fresh(legacy_dir)
    old = again.task("old1")
    check("旧数据能读回来", old is not None and old.title == "祖传任务")
    check("旧数据缺 due 字段降级为无期限",
          old is not None and old.due_date is None, repr(getattr(old, "due_date", "?")))


# ---------------------------------------------------------------- 6
def test_due_states(tmp: Path) -> None:
    store = _fresh(tmp / "t6")
    gid = store.groups[0].id
    now = _dt.datetime.now()
    # ⚠️ "今天到期"必须显式钉在今天的 23:59，不能用 now + 2h ——
    # 测试跑在 22:00 之后时 now+2h 已经是明天，断言就会莫名其妙地失败。
    today_late = _dt.datetime.combine(_dt.date.today(), _dt.time(23, 59))
    over = store.add_task("已逾期", gid, due_date=now - _dt.timedelta(hours=5))
    soon = store.add_task("今天到期", gid, due_date=today_late)
    later = store.add_task("还早", gid, due_date=now + _dt.timedelta(days=5))
    none = store.add_task("没期限", gid)
    old_open = store.add_task("昨日任务", gid, due_date=now - _dt.timedelta(days=1))
    store.update_task(none.id, note="客户电话记录")

    # is_overdue / is_due_today 是**属性**，不是方法（这里踩过一次）
    check("逾期判定", over.is_overdue is True)
    store.set_done(over.id, True)
    check("完成后不算逾期", store.task(over.id).is_overdue is False)
    check("今日到期判定", soon.is_due_today is True)
    check("未来不算今日到期", later.is_due_today is False)
    check("未逾期的今天到期", soon.is_overdue is False)
    check("无期限不参与提醒",
          none.due_date is None and none.is_overdue is False
          and none.is_due_today is False)
    check("按截止时间升序、无期限排最后",
          [t.title for t in sorted(store.all_tasks(),
                                   key=lambda t: (t.due_date is None, t.due_date))]
           == ["昨日任务", "已逾期", "今天到期", "还早", "没期限"],
          str([t.title for t in sorted(
              store.all_tasks(),
              key=lambda t: (t.due_date is None, t.due_date))]))
    from shiguang.models import task_matches_filter

    check("今日筛选包含今天到期、未完成的任务",
          task_matches_filter(soon, filter_key="today", today=_dt.date.today())
          and not task_matches_filter(over, filter_key="today", today=_dt.date.today()))
    check("逾期筛选只含未完成的逾期任务",
          task_matches_filter(old_open, filter_key="overdue")
          and not task_matches_filter(over, filter_key="overdue"))
    check("状态筛选区分未完成和已完成",
          task_matches_filter(old_open, filter_key="open")
          and task_matches_filter(over, filter_key="done")
          and not task_matches_filter(over, filter_key="open"))
    check("搜索同时匹配标题与备注",
          task_matches_filter(none, query="客户电话")
          and not task_matches_filter(none, query="不存在"))


# ---------------------------------------------------------------- 7
def test_persist_and_recover(tmp: Path) -> None:
    store = _fresh(tmp / "t7")
    gid = store.groups[0].id
    t1 = store.add_task("要保持的任务", gid, 0)
    store.set_done(t1.id, True)
    store.set_setting("theme", "dark")
    store.save(force=True)

    reloaded = _fresh(tmp / "t7")
    check("重载任务数一致", len(reloaded.all_tasks()) == 1)
    check("重载完成状态保留", reloaded.task(t1.id).done is True)
    check("重载设置保留", reloaded.settings.get("theme") == "dark")

    with open(tmp / "t7" / "data.json", "w", encoding="utf-8") as fh:
        fh.write("{ 这不是合法 JSON")
    recovered = _fresh(tmp / "t7")
    check("损坏文件不崩溃（回落默认）", len(recovered.groups) == 3)
    check("损坏文件已备份留档", bool(list((tmp / "t7").glob("data.corrupt-*.json"))),
          str(list((tmp / "t7").glob("*.json"))))

    # JSON 可解析但字段类型错误时，保留可读任务并修复坏值。
    schema_dir = tmp / "t7-schema"
    schema = _fresh(schema_dir)
    schema_task = schema.add_task("仍要保留的任务", schema.groups[0].id)
    schema.save(force=True)
    schema_path = schema_dir / "data.json"
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    payload["history"] = {"2026-09-26": "not-a-number"}
    payload["tasks"].append({"id": "broken", "title": "坏任务",
                             "group_id": schema.groups[0].id,
                             "priority": "not-a-number"})
    schema_path.write_text(json.dumps(payload), encoding="utf-8")
    schema_recovered = _fresh(schema_dir)
    check("字段异常不会阻止启动", schema_recovered.task(schema_task.id) is not None)
    check("单条坏任务被跳过而好任务保留",
          len(schema_recovered.all_tasks()) == 1
          and schema_recovered.task(schema_task.id) is not None)
    check("异常计数安全回退为 0",
          schema_recovered.history_get("2026-09-26") == 0)
    check("字段修复前保留原始文件副本",
          bool(list(schema_dir.glob("data.corrupt-*.json"))))

    # Settings are JSON input too: invalid types and out-of-range durations must
    # fall back safely before UI/services attempt to consume them.
    settings_dir = tmp / "t7-settings"
    settings_store = _fresh(settings_dir)
    settings_store.add_task("保留设置异常时的任务", settings_store.groups[0].id)
    settings_store.save(force=True)
    settings_path = settings_dir / "data.json"
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    settings_payload["settings"].update({
        "theme": ["dark"], "onboarded": "false", "pomodoro_minutes": "oops",
        "break_minutes": 0, "due_just_minutes": -2,
        "group_icons": ["not-a-map"], "default_group": "deleted-group",
    })
    settings_path.write_text(json.dumps(settings_payload), encoding="utf-8")
    safe_settings = _fresh(settings_dir)
    check("坏设置不会阻止启动且保留可读任务",
          len(safe_settings.all_tasks()) == 1)
    check("坏设置字段安全回落默认值",
          safe_settings.settings["theme"] == "system"
          and safe_settings.settings["onboarded"] is False
          and safe_settings.settings["pomodoro_minutes"] == 25
          and safe_settings.settings["break_minutes"] == 5
          and safe_settings.settings["due_just_minutes"] == 5
          and safe_settings.settings["group_icons"] == {}
          and safe_settings.settings["default_group"] == safe_settings.groups[0].id)
    check("设置修复前也保留原始文件副本",
          bool(list(settings_dir.glob("data.corrupt-*.json"))))

    empty_groups_dir = tmp / "t7-empty-groups"
    empty_groups = _fresh(empty_groups_dir)
    empty_groups.save(force=True)
    empty_path = empty_groups_dir / "data.json"
    empty_payload = json.loads(empty_path.read_text(encoding="utf-8"))
    empty_payload["groups"] = []
    empty_path.write_text(json.dumps(empty_payload), encoding="utf-8")
    empty_recovered = _fresh(empty_groups_dir)
    check("空分组数据恢复默认分组并留档",
          len(empty_recovered.groups) == 3
          and bool(list(empty_groups_dir.glob("data.corrupt-*.json"))))

    # 模拟原子替换失败，确认失败状态对调用方可见且可以重试。
    from shiguang import store as store_module

    save_probe = _fresh(tmp / "t7-save-failure")
    save_probe.add_task("写入失败探针", save_probe.groups[0].id)
    real_replace = store_module.os.replace
    try:
        store_module.os.replace = lambda *_a, **_k: (_ for _ in ()).throw(
            PermissionError("simulated write failure"))
        failed = save_probe.save()
    finally:
        store_module.os.replace = real_replace
    check("保存失败有明确返回值并保留待保存状态",
          failed is False and save_probe.dirty and bool(save_probe.last_save_error))
    check("恢复写入后可重试成功",
          save_probe.save() is True and not save_probe.dirty)

    backup_probe = _fresh(tmp / "t7-backup-failure")
    backup_probe.add_task("备份失败探针", backup_probe.groups[0].id)
    backup_output = tmp / "t7-backup-failure" / "export.json"
    try:
        store_module.os.replace = lambda *_a, **_k: (_ for _ in ()).throw(
            PermissionError("simulated write failure"))
        try:
            backup_probe.export_json(backup_output)
            backup_rejected = False
        except OSError:
            backup_rejected = True
    finally:
        store_module.os.replace = real_replace
    check("保存失败时不生成过期备份",
          backup_rejected and not backup_output.exists())

    undo_store = _fresh(tmp / "t7-undo")
    undo_task = undo_store.add_task("撤销用已完成任务", undo_store.groups[0].id)
    undo_store.set_done(undo_task.id, True)
    completion_total = undo_store.total_completed
    history_key = _dt.date.today().isoformat()
    snapshot = undo_task.to_dict()
    undo_store.remove_task(undo_task.id)
    restored = undo_store.restore_task(snapshot)
    check("撤销删除恢复原任务 ID", restored is not None and restored.id == undo_task.id)
    check("撤销删除恢复完成统计", undo_store.total_completed == completion_total
          and undo_store.history_get(history_key) == 1)
    undo_store.save(force=True)
    check("撤销后的任务重启后仍存在",
          _fresh(tmp / "t7-undo").task(undo_task.id) is not None)


# ---------------------------------------------------------------- 8
def test_csv_and_hotkey(tmp: Path) -> None:
    store = _fresh(tmp / "t8")
    gid = store.groups[0].id
    store.add_task("导出我", gid, due_date=_dt.datetime(2026, 9, 21, 18, 30))
    out = tmp / "t8" / "export.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    store.export_csv(out)
    text = out.read_text(encoding="utf-8-sig")
    check("CSV 有表头", "任务内容" in text.splitlines()[0], text.splitlines()[0])
    check("CSV 含任务名", "导出我" in text)
    check("CSV 含截止时间列", "2026-09-21 18:30" in text, text[:200])

    from shiguang import strings

    check("空状态文案按状态取（且非空）",
          bool(strings.empty_title(False, False))
          and strings.empty_title(False, False) != strings.empty_title(True, True),
          strings.empty_title(False, False))
    check("鼓励文案含数字", "3" in strings.encourage(3), strings.encourage(3))


# ---------------------------------------------------------------- 9
def test_remind(tmp: Path) -> None:
    """到期提醒：默认提前量、不提醒要跳过、序列化兼容（缺字段 vs 显式 null）。"""
    from shiguang import stats
    from shiguang.models import REMIND_DEFAULT

    store = _fresh(tmp / "t9")
    gid = store.groups[0].id
    soon = store.add_task("十分钟后到期", gid,
                          due_date=_dt.datetime.now() + _dt.timedelta(minutes=10))
    check("新建任务默认提前量 = 15 分钟",
          soon.remind_offset == REMIND_DEFAULT, repr(soon.remind_offset))
    check("提前 15 分钟 -> 10 分钟后到期的任务已在提醒窗口内",
          [t.id for t in stats.remind_due_tasks(store)] == [soon.id],
          str([t.title for t in stats.remind_due_tasks(store)]))

    store.update_task(soon.id, remind_offset=None)
    check("设为不提醒后，轮询跳过该任务",
          stats.remind_due_tasks(store) == [])
    check("不提醒的任务 remind_enabled 为假", store.task(soon.id).remind_enabled is False)

    store.update_task(soon.id, remind_offset=5)
    check("提前 5 分钟 -> 10 分钟后到期的任务还不在窗口内",
          stats.remind_due_tasks(store) == [])
    store.update_task(soon.id, remind_offset=0)
    check("准时提醒：未到点前不提醒", stats.remind_due_tasks(store) == [])

    # 逾期但不早于 grace 窗口：应命中；逾期太久：不该反复翻出来
    over = store.add_task("刚逾期", gid,
                          due_date=_dt.datetime.now() - _dt.timedelta(minutes=1))
    check("刚逾期的任务会被提醒",
          over.id in [t.id for t in stats.remind_due_tasks(store)])
    store.update_task(over.id, due_date=_dt.datetime.now() - _dt.timedelta(hours=6))
    check("逾期几小时的任务不再反复提醒",
          over.id not in [t.id for t in stats.remind_due_tasks(store)])

    # ReminderService：同一任务只提醒一次，forget 后可重新提醒
    from shiguang.reminder import ReminderService

    svc = ReminderService(store, lambda *_a: None)
    store.update_task(soon.id, remind_offset=15)
    store.update_task(over.id, remind_offset=None)
    got = svc.evaluate()
    check("evaluate 只捞该提醒的任务", [t.id for _k, t in got] == [soon.id],
          str([(k, t.title) for k, t in got]))
    check("同一任务第二轮不再提醒", svc.evaluate() == [])
    svc.forget(soon.id)
    check("forget 之后可以重新提醒", len(svc.evaluate()) == 1)
    check("推迟提醒操作可用", svc.snooze(soon.id, 10))
    check("推迟期间不重复提醒", svc.evaluate() == [])
    svc._snoozed_until[soon.id] = 0.0
    check("推迟时间到后重新提醒", len(svc.evaluate()) == 1)
    restarted = ReminderService(store, lambda *_a: None)
    check("重启后重新检查未完成的提醒", len(restarted.evaluate()) == 1)
    title, message = ReminderService.build_message([("soon", store.task(soon.id))])
    check("通知文案非空", bool(title) and bool(message), message)

    # 序列化兼容：旧数据缺字段 -> 用默认提前量；显式 null -> 不提醒
    legacy_dir = tmp / "t9legacy"
    legacy = _fresh(legacy_dir)
    legacy.save(force=True)
    raw = json.loads((legacy_dir / "data.json").read_text(encoding="utf-8"))
    raw["version"] = 1
    raw["tasks"] = [
        {"id": "old1", "title": "祖传任务", "group_id": gid, "order": 0},
        {"id": "old2", "title": "显式不提醒", "group_id": gid, "order": 1,
         "remind_offset": None},
    ]
    (legacy_dir / "data.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    again = _fresh(legacy_dir)
    check("旧数据缺 remind 字段 -> 用默认提前量",
          again.task("old1").remind_offset == REMIND_DEFAULT,
          repr(again.task("old1").remind_offset))
    check("显式 null -> 不提醒（不会被默认值覆盖）",
          again.task("old2").remind_offset is None,
          repr(again.task("old2").remind_offset))

    from shiguang.models import parse_remind

    for raw_value in ("不是数字", -3, [], {}, True, None, ""):
        check(f"脏值 {raw_value!r} 降级为不提醒", parse_remind(raw_value) is None,
              repr(parse_remind(raw_value)))
    check("合法值原样通过", parse_remind(30) == 30 and parse_remind("60") == 60,
          f"{parse_remind(30)!r} {parse_remind('60')!r}")


# ---------------------------------------------------------------- 10
def test_sorting(tmp: Path) -> None:
    """组内显示顺序（1.5.7 自动排序）：

    未完成按**截止日期升序**（无期限垫底）→ 已完成按**完成时间降序**沉底；
    同键时 order 兜底，所以"同一天到期"的几条仍然拖得动。
    """
    store = _fresh(tmp / "t10")
    gid = store.groups[0].id
    now = _dt.datetime.now()

    def titles():
        return [t.title for t in store.tasks_in(gid)]

    store.add_task("后天", gid, due_date=now + _dt.timedelta(days=2))
    soon = store.add_task("一小时后", gid, due_date=now + _dt.timedelta(hours=1))
    store.add_task("无期限", gid)
    late = store.add_task("明天", gid, due_date=now + _dt.timedelta(days=1))
    check("未完成按截止日期升序，无期限垫底",
          titles() == ["一小时后", "明天", "后天", "无期限"], str(titles()))

    store.set_done(soon.id, True)
    check("勾选完成后自动沉到最下方",
          titles() == ["明天", "后天", "无期限", "一小时后"], str(titles()))

    store.set_done(late.id, True)
    # 完成时间显式拉开 60 秒，避免依赖 time.time() 的分辨率
    store.task(late.id).done_at = store.task(soon.id).done_at + 60
    check("已完成按完成时间降序（最近完成的在前）",
          titles() == ["后天", "无期限", "明天", "一小时后"], str(titles()))

    store.set_done(late.id, False)
    check("取消完成后回到未完成区并按截止日期归位",
          titles() == ["明天", "后天", "无期限", "一小时后"], str(titles()))

    # 稳定性：存盘再读回来，顺序不能自己变
    store.save(force=True)
    again = _fresh(tmp / "t10")
    check("重新读入后顺序不变",
          [t.title for t in again.tasks_in(gid)] == ["明天", "后天", "无期限", "一小时后"],
          str([t.title for t in again.tasks_in(gid)]))

    # 同键（同一天到期）时 order 兜底 —— 拖拽调先后依然有效
    a = store.add_task("同刻A", gid, due_date=now + _dt.timedelta(days=3))
    b = store.add_task("同刻B", gid, due_date=now + _dt.timedelta(days=3))
    store.move_task_to(a.id, gid, 0)
    check("同一天到期的任务：拖拽优先于默认次序",
          titles().index("同刻A") < titles().index("同刻B"), str(titles()))
    store.move_task_to(b.id, gid, 0)
    check("反过来拖同样生效",
          titles().index("同刻B") < titles().index("同刻A"), str(titles()))

    # 排序键本身：已完成永远在未完成之后，与截止日期无关
    from shiguang.models import sort_key, sorted_tasks

    done_task = store.task(soon.id)
    undone_overdue = store.add_task("早就逾期", gid,
                                    due_date=now - _dt.timedelta(days=5))
    check("逾期未完成仍排在已完成之上",
          sort_key(undone_overdue) < sort_key(done_task))
    check("sorted_tasks 不改动入参顺序",
          [t.id for t in sorted_tasks([done_task, undone_overdue])]
          == [undone_overdue.id, done_task.id])


# ---------------------------------------------------------------- 11
def test_backup_and_focus(tmp: Path) -> None:
    """备份导出/恢复闭环 + 专注统计口径（1.5.11）。"""
    from shiguang.config import DATA_VERSION
    from shiguang import stats

    store = _fresh(tmp / "t11")
    gid = store.groups[0].id
    store.add_task("甲", gid)
    store.add_task("乙", gid)

    # 备份 -> 再改 -> 恢复
    backup = tmp / "t11" / "backup.json"
    store.export_json(backup)
    store.add_task("丙（恢复后应消失）", gid)
    store.remove_task(store.tasks_in(gid)[0].id)
    count = store.import_json(backup)
    check("恢复返回任务条数", count == 2, str(count))
    check("恢复后任务与备份一致",
          sorted(t.title for t in store.tasks) == ["乙", "甲"],
          str([t.title for t in store.tasks]))
    check("恢复后版本号归一到当前 DATA_VERSION", store.data["version"] == DATA_VERSION,
          str(store.data["version"]))
    again = _fresh(tmp / "t11")
    check("恢复结果已落盘可重读", len(again.tasks) == 2, str(len(again.tasks)))

    # 恢复前的现场备份文件已生成
    check("恢复前保留了当前数据现场",
          any(p.name.startswith("data.pre-restore-") for p in (tmp / "t11").glob("*.json")),
          str([p.name for p in (tmp / "t11").glob("*.json")]))

    # 坏档拒绝
    bad = tmp / "t11" / "bad.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    try:
        store.import_json(bad)
        check("坏备份被拒绝", False, "未抛异常")
    except ValueError:
        check("坏备份被拒绝", True)
    except Exception as exc:  # noqa: BLE001
        check("坏备份被拒绝（异常类型）", False, repr(exc))

    # 专注统计：每日历史 + 全量计数 + 任务番茄数
    t = store.tasks[0]
    store.add_focus(t.id)
    store.add_focus()                      # 自由专注（无任务）
    check("今日专注 = 2", stats.focus_today(store) == 2, str(stats.focus_today(store)))
    check("本周专注 = 2", stats.focus_week(store) == 2, str(stats.focus_week(store)))
    check("累计专注 = 2", stats.focus_summary(store)["total"] == 2,
          str(stats.focus_summary(store)))
    check("挂靠任务的番茄数 +1", t.pomodoros == 1, str(t.pomodoros))
    store.save(force=True)
    reloaded = _fresh(tmp / "t11")
    today_key = _dt.date.today().isoformat()
    check("专注历史已落盘可重读", reloaded.focus_get(today_key) == 2,
          str(reloaded.focus_get(today_key)))


def run() -> int:
    _FAILED.clear()
    tmp = Path(tempfile.mkdtemp(prefix="shiguang-test-"))
    for name, fn in (("1. 默认数据与增删改", test_basic_crud),
                     ("2. 完成状态与历史口径", test_done_and_history),
                     ("3. 连续打卡与趋势", test_streak),
                     ("4. 截止日期解析容错", test_due_parse),
                     ("5. 截止日期序列化与旧数据兼容", test_due_compat),
                     ("6. 逾期/今日到期口径", test_due_states),
                     ("7. 持久化与损坏恢复", test_persist_and_recover),
                     ("8. CSV 导出与文案表", test_csv_and_hotkey),
                     ("9. 到期提醒口径与序列化", test_remind),
                     ("10. 组内自动排序", test_sorting),
                     ("11. 备份恢复闭环与专注统计", test_backup_and_focus)):
        print(f"== {name} ==")
        try:
            fn(tmp)
        except Exception as exc:  # noqa: BLE001
            _FAILED.append(f"{name} 抛异常：{exc!r}")
            print(f"  FAIL {name} 抛异常：{exc!r}")
    if _FAILED:
        print(f"\n结果：{len(_FAILED)} 项未通过")
        for item in _FAILED:
            print(f"  - {item}")
        return 1
    print("\n结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
