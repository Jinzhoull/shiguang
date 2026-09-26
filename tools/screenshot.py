# -*- coding: utf-8 -*-
"""界面截图工具（预览 / 回归对比用）。

用法::

    python run.py --screenshot                  # 任务页（浅色）
    python run.py --screenshot out.png
    python tools/screenshot.py --all            # 批量输出全套预览图（命名带版本号）

实现要点：截图前把窗口置顶并强制重绘，再按 DPI 缩放换算成物理像素裁剪，
避免 125% 缩放下截出来只有半张图。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shiguang import __version__  # noqa: E402  （输出文件名带版本号）

# 预览/回归时**禁止抢前台**：主窗口 1.5.1 起在启动 80ms 后会主动抢前台
# （修"启动后落在别的软件底下"），那在截图场景里会把用户的焦点抢走。
# 这里在创建 App 之前设好开关，app._activate_on_start 看到就直接返回。
os.environ["SHIGUANG_NO_ACTIVATE"] = "1"
# 同理：抓屏时指针停留在用户上次的位置，恰好压在某张卡片/图标上时，那枚
# hover tooltip 会糊在预览图上（"设置截止日期"那种小方块）。截图期间整体关掉。
os.environ["SHIGUANG_NO_HINT"] = "1"

DEFAULT_OUT = ROOT / "docs" / "preview"


def _enable_dpi_awareness() -> None:
    """进程必须先自己声明 DPI 感知，否则量到的坐标会被系统虚拟化。

    实测在 150% 缩放屏上：不声明时 ``winfo_fpixels("1i")`` 返回 72（=1.0x），
    按它换算出来的截图只有实际的 3/4 大（472px 而不是 630px），
    而且窗口位置也会整体偏移。frozen_smoke 里有同样的处理。
    """
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE_V2
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _seed_demo_data() -> None:
    """造一份演示数据（只写在独立的预览目录，不动用户真实数据）。

    每次调用都**重建**：早期版本有 "if store.all_tasks(): return" 的短路，
    结果加了新的演示内容（截止日期）后重跑截图完全看不到变化 —— 这种
    "缓存让验证失真"的坑在截图工具里特别容易骗到自己。

    注意：这里**不删文件**，而是把已有 JSON 重置成空壳后让 Store 重建。
    直接 ``unlink()`` 会在批量模式下触发宿主环境的删除护栏（第一张图之后
    整个批处理就被中断），而且删了再写本来也没必要。
    """
    from shiguang.models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MID
    from shiguang.store import Store

    preview_dir = ROOT / ".preview-data"
    preview_dir.mkdir(exist_ok=True)
    data_file = preview_dir / "data.json"
    # 覆盖成最小合法结构（原子写由 Store.save 负责），不删文件
    data_file.write_text('{"groups": [], "tasks": [], "settings": {}, '
                         '"history": {}}', encoding="utf-8")

    store = Store()
    store.set_setting("onboarded", True)
    store.set_setting("tray_tip_shown", True)
    now = _dt.datetime.now()
    work, study, life = [g.id for g in store.groups]

    # ---- 带截止日期的任务（覆盖四种视觉状态）----
    overdue = store.add_task("补交上周的费用报销", work, PRIORITY_HIGH)
    store.set_due(overdue.id, now - _dt.timedelta(hours=5))          # 已逾期
    tonight = store.add_task("给妈妈打个电话", life, PRIORITY_HIGH)
    store.set_due(tonight.id, now + _dt.timedelta(hours=2)
                  if now.hour < 21 else now.replace(hour=23, minute=30))   # 今日到期
    weekend = store.add_task("周末去看展", life, PRIORITY_LOW)
    store.set_due(weekend.id, now + _dt.timedelta(days=3, hours=2))  # 未来
    done_due = store.add_task("交周报给组长", work, PRIORITY_MID)
    store.set_due(done_due.id, now - _dt.timedelta(days=1))          # 已完成（灰字删除线）

    # ---- 无截止日期的任务 ----
    minutes = store.add_task("整理这周的会议纪要", work, PRIORITY_MID)
    doc = store.add_task("把接口文档补完", work, PRIORITY_MID)
    store.add_task("回复客户邮件", work, PRIORITY_LOW)     # 无期限：垫在该组最下方
    store.add_task("读完《深度工作》第三章", study, PRIORITY_MID)
    words = store.add_task("背 20 个单词", study, PRIORITY_LOW)
    store.add_task("傍晚去河边走 30 分钟", life, PRIORITY_MID)

    # 让"工作"组同时有 已逾期 / 较远 / 无期限 三种**未完成**任务 —— 预览图里
    # 一眼能看到 1.5.7 的自动排序：截止日期升序、无期限垫底。
    store.set_due(minutes.id, now + _dt.timedelta(days=2, hours=5))

    # 已完成的三条：**显式点名**，不再用 ``tasks_in()[i]`` 取下标 —— 组内顺序
    # 从 1.5.7 起是派生值，按下标取会在改排序规则时静默换人，预览图内容跟着漂。
    store.set_done(done_due.id, True)
    store.set_done(doc.id, True)
    store.set_done(words.id, True)

    today = _dt.date.today()
    for i, count in enumerate([2, 4, 1, 5, 3, 0, 4]):
        day = (today - _dt.timedelta(days=6 - i)).isoformat()
        store.data["history"][day] = count
    # 让"累计拾起"与历史口径一致（真实运行时二者天然同步，演示数据需要手工对齐）
    store.data["total_completed"] = sum(store.data["history"].values())
    store.save(force=True)


def _collect_toplevels(widget, out, depth: int = 0) -> None:
    """递归收集控件树里的 Toplevel（浮层）。

    菜单的 master 是任务卡片而非主窗口，只看 ``app.winfo_children()`` 会漏。
    """
    if depth > 8:
        return
    for child in widget.winfo_children():
        try:
            if child.winfo_class() == "Toplevel":
                out.append(child)
            _collect_toplevels(child, out, depth + 1)
        except Exception:  # noqa: BLE001
            continue


def _raise_floating(app) -> None:
    """把已显示的浮层也置顶，避免被置顶的主窗口盖住。

    主窗口在截图前会被设成 ``-topmost``，而日期选择器 / 右键菜单这些
    Toplevel 默认不带 topmost —— z-order 上永远在主窗口下面，截出来
    只有主窗口（这个坑让"浮层截图"白跑了两轮）。这里统一抬起来再等
    几拍让 Windows 重排 z-order。
    """
    tops: list = []
    try:
        _collect_toplevels(app, tops)
    except Exception:  # noqa: BLE001
        return
    raised = False
    for child in tops:
        try:
            if not child.winfo_ismapped():
                continue
            child.attributes("-topmost", True)
            # ❗浮层可能有 alpha 淡入（新建任务浮层就是 5 步 × 20ms）。
            # 抓屏发生在动画中途时，截到的是**半透明**的卡片 —— 底下的任务
            # 列表透出来，看起来像"内容错位、文字溢出卡片"，其实只是没淡完。
            # 这里在抓屏前把 alpha 拉满（抓的是静态图，动画过程无意义）。
            try:
                if float(child.attributes("-alpha")) < 1.0:
                    child.attributes("-alpha", 1.0)
            except Exception:  # noqa: BLE001
                pass
            child.lift()
            raised = True
        except Exception:  # noqa: BLE001
            continue
    if raised:
        for _ in range(12):
            try:
                app.update()
            except Exception:  # noqa: BLE001
                break
            time.sleep(0.03)


def _freeze_watchdog(obj) -> None:
    """停掉浮层的"指针看门狗"（截图/自动化专用）。

    菜单与图标浮层靠 ``_watch`` 轮询**真实指针**位置判生死：连续两拍不在
    窗口上就自己收掉。人手上这是对的，但截图时指针在别的地方（甚至是别的
    显示器），浮层会在抓图前自己关掉 —— 于是"浮层预览图"里只有主窗口。
    这里显式摘掉定时器，让浮层在抓图期间稳定停留。
    """
    job = getattr(obj, "_watch_job", None)
    if job is None:
        return
    try:
        obj.after_cancel(job)
    except Exception:  # noqa: BLE001
        pass
    obj._watch_job = None


def _freeze_menu_watchdog() -> None:
    """把当前活着的那张右键菜单的看门狗摘掉（菜单是模块级 ``_ACTIVE`` 管的）。"""
    try:
        from shiguang.ui import menu as _menu

        for m in list(_menu._ACTIVE):
            _freeze_watchdog(m)
    except Exception:  # noqa: BLE001
        pass


def _assert_confirm_placed(app) -> None:
    """抓图前自检：确认弹窗必须**落在主窗口内**（1.5.3 的硬要求）。

    需求写的是"弹窗居中于主窗口，不超出主窗口边界"。只靠肉眼看图，越界
    十来个像素根本看不出来 —— 所以让它自己量一遍并打警告。
    """
    dlg = getattr(app, "_shot_confirm", None)
    if dlg is None:
        return
    try:
        wx, wy = app.winfo_rootx(), app.winfo_rooty()
        ww, wh = app.winfo_width(), app.winfo_height()
        dx, dy = dlg.winfo_rootx(), dlg.winfo_rooty()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN 确认弹窗位置自检失败：{exc}")
        return
    inside = (dx >= wx - 1 and dy >= wy - 1
              and dx + dw <= wx + ww + 1 and dy + dh <= wy + wh + 1)
    print(f"  确认弹窗 {dw}x{dh}@{dx},{dy}  主窗口 {ww}x{wh}@{wx},{wy}  "
          f"落在窗口内={inside}")
    if not inside:
        print("  WARN 确认弹窗越出了主窗口边界")


def _assert_floating_visible(what: str) -> None:
    """抓图前自检：要拍的那个浮层必须还在屏幕上（mapped）。

    这条检查是**必需的**，不是保险：预览图曾经整批"菜单是空的"，原因是菜单
    被指针看门狗提前收掉了，而抓图逻辑对此毫无感知 —— 图照存，只是没菜单，
    肉眼不放大根本发现不了。工具自己必须能发现这种"拍了个寂寞"。
    """
    try:
        from shiguang.ui import menu as _menu

        alive = []
        for m in list(_menu._ACTIVE):
            try:
                if m.winfo_exists() and m.winfo_ismapped():
                    alive.append(m)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        alive = []
    if not alive:
        print(f"  WARN {what}抓图时已不在屏幕上（被看门狗收掉了？）")


def _reset_floating_registry() -> None:
    """清掉浮层的模块级登记表。

    菜单与图标浮层的 ``_ACTIVE`` 是**模块级**列表，而本工具在同一进程里为
    每张图新建一个 Tk 解释器：上一个解释器被销毁时，里面的菜单对象不会走
    ``close()``，于是登记表里留下"上一个世界的"死引用 —— 下一个解释器里
    ``_assert_floating_visible`` 会把它们当成"菜单还在"，或反过来误判。
    每张图开始前清一次最省事。
    """
    try:
        from shiguang.ui import menu as _menu

        _menu._ACTIVE.clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        from shiguang.ui import icon_picker as _picker

        _picker.ACTIVE.clear()
    except Exception:  # noqa: BLE001
        pass


def _grab_checked(bbox, app=None, tries: int = 4):
    """抓屏并**校验**，全黑/单色一律重试；始终失败返回 ``None``。

    为什么必须校验：实测 ``ImageGrab.grab()`` 会偶发返回一张**纯黑**图
    （连跑 4 次就有 1 次。屏幕锁定、显示器休眠、DWM 抽风都会这样）。
    黑图以前是直接落盘的 —— 预览图集里混进一张纯黑，不打开看根本发现不了。
    宁可这一张不生成，也不能用黑图覆盖掉上一版的好图。
    """
    from PIL import ImageGrab

    for attempt in range(max(1, tries)):
        img = None
        try:
            img = ImageGrab.grab(bbox=tuple(int(v) for v in bbox),
                                 all_screens=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  抓屏失败（第 {attempt + 1} 次）：{exc}")
        if img is not None and img.width > 4 and img.height > 4:
            # getcolors 返回 None = 颜色数超出上限 = 绝对不是单色图
            cols = img.convert("RGB").getcolors(4096)
            if cols is None or len(cols) > 1:
                return img
        if attempt + 1 < tries:
            print(f"  抓到空白/黑图，重试（第 {attempt + 1} 次）")
            try:
                if app is not None:
                    app.attributes("-topmost", True)
                    app.lift()
                    for _ in range(10):
                        app.update()
                        time.sleep(0.03)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.6)
    return None


def capture(out_path: str = "", mode: str = "", page: str = "tasks",
            demo: bool = True, dark: bool = False, wait: float = 1.5,
            particles: bool = False, scroll: float = 0.0,
            extra: str = "") -> int:
    # 1.5.28：悬停提示门控要能在预览图里被看见，就得放行 SHIGUANG_NO_HINT。
    # 这个开关是在**卡片构造时**读的（_touch_hint 会直接 return），所以必须
    # 在建 App 之前定好。
    if extra.startswith("hint"):
        os.environ.pop("SHIGUANG_NO_HINT", None)
    else:
        os.environ["SHIGUANG_NO_HINT"] = "1"

    if demo:
        preview_dir = ROOT / ".preview-data"
        preview_dir.mkdir(exist_ok=True)
        os.environ["SHIGUANG_DATA_DIR"] = str(preview_dir)

    # 批量截图时每张图都会新建一个 Tk 解释器，上一轮的 PhotoImage 已经随旧
    # 解释器一起销毁，但 icons 的模块级缓存还留着它们 —— 下一次取到就是
    # "image pyimageN doesn't exist"，并且**只在批量模式下才炸**（单张不会）。
    # 所以这里必须在建窗口前把缓存清空。
    try:
        from shiguang import icons
        icons.invalidate()
    except Exception:  # noqa: BLE001
        pass
    # 同理：浮层的模块级登记表里会留着上一个解释器的死引用，一并清掉
    _reset_floating_registry()

    import customtkinter as ctk

    if mode:
        ctk.set_appearance_mode(mode)
    elif dark:
        ctk.set_appearance_mode("Dark")

    from shiguang.app import ShiguangApp
    from shiguang import theme

    if demo:
        _seed_demo_data()

    app = ShiguangApp()
    # 截图工具会主动销毁窗口，此时挂起的 after 动画回调会以
    # "invalid command name ...step / application has been destroyed" 的形式
    # 冒到 stderr。这是工具侧的正常现象，不该污染输出 —— 直接吞掉。
    app.report_callback_exception = lambda *a, **k: None
    # Tcl 层还有自己的 bgerror 处理器（after 回调在解释器销毁后触发，
    # 走的是这条路径，report_callback_exception 拦不住）。一并静音。
    try:
        app.tk.eval("proc bgerror {msg} {}")
    except Exception:  # noqa: BLE001
        pass
    app.store.set_setting("onboarded", True)
    # 主题必须写进 store 再让 App 自己应用：App.__init__ 会按 settings["theme"]
    # 调 ctk.set_appearance_mode()，如果我们只是在外面不设置，它会把外观模式
    # 覆盖回 "system"（这台机器是浅色）—— 截图就成了"深浅两版一模一样"。
    if mode:
        app.store.set_setting("theme", "dark" if mode.lower() == "dark" else "light")
        app.on_setting("theme", app.store.settings.get("theme"))
    if page != "tasks":
        app.show_page(page)
    try:
        app.attributes("-topmost", True)
    except Exception:  # noqa: BLE001
        pass
    app.update()
    app.lift()

    # 手动泵事件，等待控件布局与动画稳定
    def pump(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.update()
            time.sleep(0.03)

    pump(wait)

    if scroll:
        # 滚动到指定位置（设置页的"通知"卡片在折叠线以下，不滚看不到）
        try:
            holder = getattr(app.page, "body", None)
            if holder is not None:
                holder._parent_canvas.yview_moveto(scroll)
                pump(0.5)
        except Exception as exc:  # noqa: BLE001
            print(f"  滚动失败：{exc}")

    if particles:
        # 粒子动画约 1.5 秒，抓 0.45 秒那一帧：粒子已散开、还没熄灭，最有观感
        app.particles.play(26)
        pump(0.45)

    if extra == "banner":
        # 逾期提醒条：用**真实点击序列**展开（Enter → Button-1 → ButtonRelease-1），
        # 不直接调 ``_toggle_detail()``。
        # 1.5.5 在这里修过一个真 bug：chevron 既挂着 CTkButton 的 command
        # （走 <ButtonRelease-1>），又被额外绑了一次 <Button-1> 的 toggle，
        # 一次点击翻两次 → 净效果为零（"点箭头毫无反应"）。走真实事件，
        # 图能截出来就同时证明点击链路是通的。
        # 1.5.10 起整条横幅是一张位图，点击热区 = 横幅画布本身。
        try:
            banner = getattr(app.page, "banner", None)
            if banner is not None and not getattr(banner, "_expanded", False):
                cv = banner.canvas
                cv.event_generate("<Button-1>", x=6, y=6)
                cv.event_generate("<ButtonRelease-1>", x=6, y=6)
            pump(0.6)
            if banner is not None:
                print(f"  逾期条展开={getattr(banner, '_expanded', None)}（真实点击）")
        except Exception as exc:  # noqa: BLE001
            print(f"  提醒条展开失败：{exc}")

    elif extra == "quickadd":
        # 新建任务浮层：从顶部"＋ 新建任务"按钮弹出，等淡入动画走完
        try:
            app.open_quick_add()
            pump(0.3)
            # ❗浮层有一段 5 步 × 20ms 的 alpha 淡入。pump() 只是 app.update()
            # 的忙碌循环，而淡入是靠 after() 排程的 —— 一旦截图工具的泵循环
            # 抢在 after 队列前面，截到的就是**半透明**的浮层（alpha 卡在 0.8），
            # 卡片后面的任务列表透出来，看起来像"浮层内容错位、文字溢出卡片"。
            # 这里直接把 alpha 拉满、并再泵一拍让重绘落地。
            pop = getattr(app, "_quick_popup", None)
            if pop is not None:
                try:
                    pop.attributes("-alpha", 1.0)
                except Exception:  # noqa: BLE001
                    pass
                # 预览可读性：真实入口弹在"＋ 新建任务"按钮下方，会正好压住
                # 日期栏与打卡条 —— 作为静态图读者分不清"卡片内容"和
                # "被盖住的页面内容"（这一轮就在这里白排查了两轮）。
                # 往下挪到任务列表上方，让卡片四周都是干净底色。
                try:
                    pop.geometry(
                        f"+{pop.winfo_rootx()}+{pop.winfo_rooty() + theme.lpx(120)}")
                except Exception:  # noqa: BLE001
                    pass
            pump(0.4)
        except Exception as exc:  # noqa: BLE001
            print(f"  新建浮层弹出失败：{exc}")

    elif extra == "menu":
        # 右键菜单截图：直接调任务卡片的菜单方法，比伪造鼠标事件稳。
        # 必须显式给坐标 —— 缺省走鼠标位置，脚本环境下光标可能停在
        # (0,0) 或别的显示器，菜单会弹到屏幕外、截出来只剩右半边。
        try:
            card = None
            for c in app.page.card_map.values():
                card = c
                break
            if card is not None:
                card._show_menu(at=(app.winfo_rootx() + theme.lpx(60),
                                    app.winfo_rooty() + theme.lpx(300)))
                _freeze_menu_watchdog()
                pump(0.4)
                _assert_floating_visible("任务右键菜单")
        except Exception as exc:  # noqa: BLE001
            print(f"  菜单弹出失败：{exc}")

    elif extra == "groupmenu":
        # 分组菜单（本轮需求 ④）：同样给确定坐标
        try:
            gc = next(iter(app.page.group_cards.values()), None)
            if gc is not None:
                gc._show_group_menu(at=(app.winfo_rootx() + theme.lpx(40),
                                        app.winfo_rooty() + theme.lpx(300)))
                _freeze_menu_watchdog()
                pump(0.4)
                _assert_floating_visible("分组菜单")
        except Exception as exc:  # noqa: BLE001
            print(f"  分组菜单弹出失败：{exc}")

    elif extra == "due":
        # 日期选择器（1.5.2：时间改为"字段 + 就地数字网格"，不再有下拉；
        # 1.5.3：时间行下方多了"提醒"下拉 —— 传 remind 让柔橘描边出现在图里）
        try:
            from shiguang.models import REMIND_DEFAULT
            from shiguang.ui.due_picker import DuePickerDialog

            app._shot_due = DuePickerDialog(app, None, lambda *_a: None,
                                            remind=REMIND_DEFAULT)
            pump(0.8)
        except Exception as exc:  # noqa: BLE001
            print(f"  日期选择器弹出失败：{exc}")

    elif extra == "duetime":
        # 日期选择器的**分钟滚轮**（1.5.20 取代 60 格按钮网格）：验证滚轮
        # 渲染、选中带与字段联动。
        try:
            from shiguang.ui import due_picker as _dp
            from shiguang.ui.due_picker import DuePickerDialog

            dlg = DuePickerDialog(app, None, lambda *_a: None)
            app._shot_due = dlg
            pump(0.8)
            dlg._set_mode(_dp.MODE_MINUTE)
            pump(0.3)
        except Exception as exc:  # noqa: BLE001
            print(f"  分钟滚轮弹出失败：{exc}")

    elif extra == "duehour":
        # 日期选择器的**小时滚轮**（1.5.20 取代 24 格按钮网格）：一图同时
        # 验证滚轮选中态与底部快捷行「今天 明天 后天 清除」。
        try:
            from shiguang.ui import due_picker as _dp
            from shiguang.ui.due_picker import DuePickerDialog

            dlg = DuePickerDialog(app, None, lambda *_a: None)
            app._shot_due = dlg
            pump(0.8)
            dlg._set_mode(_dp.MODE_HOUR)
            pump(0.3)
        except Exception as exc:  # noqa: BLE001
            print(f"  小时滚轮弹出失败：{exc}")

    elif extra == "remind":
        # 提醒选项的就地网格（1.5.5）：提醒选择器从**原生下拉**改成
        # "字段 + 就地网格"，与分钟网格同一块区域、同一套格子样式。
        try:
            from shiguang.models import REMIND_DEFAULT
            from shiguang.ui import due_picker as _dp
            from shiguang.ui.due_picker import DuePickerDialog

            dlg = DuePickerDialog(app, None, lambda *_a: None, remind=REMIND_DEFAULT)
            app._shot_due = dlg
            pump(0.8)
            dlg._set_mode(_dp.MODE_REMIND)
            pump(0.4)
        except Exception as exc:  # noqa: BLE001
            print(f"  提醒网格弹出失败：{exc}")

    elif extra == "confirm":
        # 删除确认弹窗（1.5.3：自绘标题栏的紧凑确认框，宽度锁 268 逻辑像素）
        try:
            from shiguang.ui import dialogs

            grp = app.store.groups[0]
            tasks = app.store.tasks_in(grp.id)
            name = tasks[0].title if tasks else "写周报（终稿）"
            app._shot_confirm = dialogs.ConfirmDialog(
                app, "删除任务", f"确定要删除「{name}」吗？",
                on_ok=lambda: None, danger=True)
            pump(0.6)
            _assert_confirm_placed(app)
        except Exception as exc:  # noqa: BLE001
            print(f"  确认弹窗弹出失败：{exc}")

    elif extra == "iconpicker":
        # 图标选择浮层（需求 ⑥）：12 格网格。
        # 必须显式给坐标 —— ``popup_icon_picker`` 缺省按真实指针居中弹出，
        # 而脚本环境的光标可能在任何地方（甚至另一台显示器），
        # 那样截出来的并集框会大得离谱、浮层本体只占一个角。
        try:
            from shiguang.ui import icon_picker

            gc = next(iter(app.page.group_cards.values()), None)
            gid = getattr(getattr(gc, "group", None), "id", "")
            cur = app.store.group_icon(gid) if gid else ""
            app._shot_picker = icon_picker.popup_icon_picker(
                app, lambda _k: None, current=cur,
                x=app.winfo_rootx() + theme.lpx(215),
                y=app.winfo_rooty() + theme.lpx(185))
            _freeze_watchdog(app._shot_picker)
            pump(0.7)
        except Exception as exc:  # noqa: BLE001
            print(f"  图标浮层弹出失败：{exc}")

    elif extra == "empty":
        # 空状态："今天还没有拾起任何光"（需求 ④ 的主文案）
        try:
            for _t in list(app.store.all_tasks()):
                app.store.remove_task(_t.id)
            app.store.save()
            app.page.render()
            pump(0.7)
        except Exception as exc:  # noqa: BLE001
            print(f"  空状态构造失败：{exc}")

    elif extra == "alldone":
        # 全部完成态："今天的光都拾起来了"（需求 ④ 的第三个状态）
        try:
            for _t in list(app.store.all_tasks()):
                app.store.update_task(_t.id, done=True)
            app.store.save()
            app.page.render()
            pump(0.7)
        except Exception as exc:  # noqa: BLE001
            print(f"  全部完成态构造失败：{exc}")

    elif extra == "resized":
        # 缩小后的窗口（需求 ② 的回归观感）：证明缩放之后不留残影、
        # 布局不塌、文字不裁。``geometry`` 收逻辑像素，物理换算交给 CTk。
        try:
            app.geometry("392x600")
            pump(0.8)
            app.page.render()
            pump(0.5)
        except Exception as exc:  # noqa: BLE001
            print(f"  缩窗失败：{exc}")

    elif extra == "narrow":
        # 1.5.15 布局回归：最小窗口（320×400 逻辑）+ 长标题逾期任务，验证
        # "标题省略号、徽章+时间右对齐、紧凑模式只留⋯"。
        try:
            app.geometry("320x400")
            pump(0.8)
            grp = app.store.groups[0]
            app.store.add_task("这是一条非常长的任务标题用于展示省略号截断效果哦", grp.id,
                               due_date="2026-01-01 09:00")
            app.store.save()
            app.page.render()
            pump(0.6)
        except Exception as exc:  # noqa: BLE001
            print(f"  最小窗口场景构造失败：{exc}")

    elif extra == "checked":
        # 打勾后停留原位（本轮改动 1）：把组内中间一条勾上，看它是否只
        # 落到"未完成之后、已完成之前"，其余卡片顺序完全不动。
        try:
            grp = app.store.groups[0]
            tasks = app.store.tasks_in(grp.id)
            if len(tasks) >= 2:
                target = tasks[1]          # 第二条：勾上后应挪到未完成区之后
                app.page._toggle(target.id, True)
                pump(0.5)
                print(f"  已勾选「{target.title}」，卡片顺序："
                      f"{[c.task.title for c in app.page.group_cards[grp.id].cards]}")
            else:
                print("  任务不足 2 条，跳过")
        except Exception as exc:  # noqa: BLE001
            print(f"  勾选场景构造失败：{exc}")

    elif extra == "states":
        # 1.5.24：三种卡片状态同框 —— 常态（白卡）/ 悬停（暖米）/ 已完成（淡绿）。
        # 悬停只切底色（``_hover`` + ``_apply_style``），不调 ``_enter()`` ——
        # 后者会顺带淡入右侧动作图标，而抓屏时指针位置是**用户上次留下的**，
        # 图标一旦浮现在指针底下就会弹出"设置截止日期"提示框，脏了整张预览图。
        try:
            app.page.render()
            pump(0.6)
            picked = None
            for card in app.page.group_cards[app.store.groups[0].id].cards:
                if card.task.done:
                    continue
                if not card.task.is_overdue:
                    picked = card
                    break
                picked = picked or card
            if picked is not None:
                picked._hover = True
                picked._apply_style()
                pump(0.4)
                print(f"  悬停「{picked.task.title}」")
            # 滚动条固定成**常态色**：抓屏时指针恰好在滚动区内，会触发 Enter
            # 把滑块染成柔橘 —— 预览图要展示的是用户默认看到的那一条。
            app.page.scroll._set_hover(False)
            pump(0.2)
        except Exception as exc:  # noqa: BLE001
            print(f"  状态对比场景构造失败：{exc}")

    elif extra == "scrolled":
        # 1.5.24：列表滚到中段 + 滚动条显式悬停 —— 滑块停在中途，宽度/圆角/
        # 配色一眼可数（贴顶时滑块顶到上沿，看不出"条"的形态）。
        try:
            app.page.render()
            pump(0.6)
            app.page.scroll._parent_canvas.yview_moveto(0.42)
            app.page.scroll._scrollbar.set(0.42, 0.86)
            app.page.scroll._set_hover(True)
            pump(0.4)
        except Exception as exc:  # noqa: BLE001
            print(f"  滚动场景构造失败：{exc}")

    elif extra == "bar":
        # 1.5.25：内容多于一屏、滚动条按**常态色**显示（不悬停）——
        # 这一张要展示的就是用户默认看到的那条半透明柔橘，所以必须
        # 显式 ``_set_hover(False)``：抓屏时指针可能正落在滚动区里。
        try:
            app.page.render()
            pump(0.7)
            app.page.scroll._sync_visibility()
            pump(0.3)
            app.page.scroll._parent_canvas.yview_moveto(0.45)
            app.page.scroll._scrollbar.set(0.45, 0.88)
            app.page.scroll._set_hover(False)
            pump(0.4)
            print(f"  内容多：scrollbar shown = {app.page.scroll.bar_shown}")
        except Exception as exc:  # noqa: BLE001
            print(f"  滚动条场景构造失败：{exc}")

    elif extra == "sparse":
        # 1.5.25：内容**短于一屏** → 滚动条应连同它占的 6px 一起隐藏。
        # 这里直接把演示数据砍到只剩两条无期限任务（顺带让逾期横幅也收起来），
        # 不动用户真实数据目录（演示数据在 .preview-data/）。
        try:
            store = app.store
            keep = [t.id for t in store.all_tasks()
                    if t.due_date is None and not t.done][:2]
            for t in list(store.all_tasks()):
                if t.id not in keep:
                    store.remove_task(t.id)
            store.save(force=True)
            app.page.render()
            pump(0.9)
            app.page.scroll._sync_visibility()
            pump(0.4)
            canvas_h = app.page.scroll._parent_canvas.winfo_height()
            need_h = app.page.scroll.winfo_reqheight()
            print(f"  内容少：need={need_h} avail={canvas_h} "
                  f"scrollbar shown = {app.page.scroll.bar_shown}")
        except Exception as exc:  # noqa: BLE001
            print(f"  内容少场景构造失败：{exc}")

    elif extra.startswith("wheel"):
        # 1.5.27：滚轮**方向**的实证场景（1.5.26 的场景只验了"有滚动"，
        # 没验方向，所以"只能向下"才漏到用户手里 —— 这里两个方向各拍一张）。
        #   wheelup   → 上拨 3 格（delta=+120）：列表上滚、滑块上移
        #   wheeldown → 下拨 3 格（delta=-120）：列表下滚
        #   wheelpage → 先切走再切回（证明 show_page 重建后路由仍唯一且方向对）
        # 投递用**真实的** WM_MOUSEWHEEL，wparam 高字 = 有符号 delta。
        try:
            import ctypes as _ct

            if extra == "wheelpage":
                for name in ("stats", "settings", "tasks"):
                    app.show_page(name)
                    pump(0.6)
            app.page.render()
            pump(0.7)
            up = extra != "wheeldown"
            delta = 120 if up else -120
            canvas = app.page.scroll._parent_canvas
            toplevel_hwnd = _ct.windll.user32.GetAncestor(
                _ct.c_void_p(app.winfo_id()), 2)
            x = canvas.winfo_rootx() + canvas.winfo_width() // 2
            y = canvas.winfo_rooty() + canvas.winfo_height() // 2
            # 起点给足余量才能看出方向：上拨从偏下的位置往上走，
            # 下拨从顶部往下走（否则贴着底边，位移只剩几像素、看不出区别）
            canvas.yview_moveto(0.32 if up else 0.0)
            pump(0.35)
            before = canvas.yview()[0]
            for _ in range(3):
                _ct.windll.user32.PostMessageW(
                    _ct.c_void_p(toplevel_hwnd), 0x020A,
                    ((delta & 0xFFFF) << 16),
                    ((y & 0xFFFF) << 16) | (x & 0xFFFF))
                pump(0.18)
            after = canvas.yview()[0]
            moved = after - before
            want = "变小(上滚)" if up else "变大(下滚)"
            ok = (moved < -1e-6) if up else (moved > 1e-6)
            print(f"  滚轮{'上拨' if up else '下拨'} delta={delta:+d}×3："
                  f"yview {before:.4f} → {after:.4f} Δ={moved:+.4f} "
                  f"期望{want} {'✅' if ok else '❌'}")
            bar = app.page.scroll._bar
            if bar is not None and bar.winfo_ismapped():
                bar.set(*canvas.yview())
            app.page.scroll._set_hover(False)
            pump(0.4)
        except Exception as exc:  # noqa: BLE001
            print(f"  滚轮场景构造失败：{exc}")

    elif extra == "cursorclean":
        # 1.5.26：缩放一次之后，指针回到内容区必须是普通箭头。
        # 抓屏抓不到系统指针，所以这里把结论打成日志（系统 GetCursorInfo +
        # 根窗口 cursor 两个口径），预览图展示的是"缩放后布局没坏"。
        try:
            import ctypes as _ct

            user32 = _ct.windll.user32

            class _CI(_ct.Structure):
                _fields_ = [("cbSize", _ct.c_ulong), ("flags", _ct.c_ulong),
                            ("hCursor", _ct.c_void_p),
                            ("pt", _ct.c_long * 2)]

            def _cursor_name():
                info = _CI()
                info.cbSize = _ct.sizeof(_CI)
                user32.GetCursorInfo(_ct.byref(info))
                table = {user32.LoadCursorW(None, _ct.c_wchar_p(v)): n
                         for v, n in ((32512, "ARROW"), (32644, "SIZEWE"),
                                      (32645, "SIZENS"), (32642, "SIZENWSE"))}
                return table.get(info.hCursor, f"#{info.hCursor}")

            class _Ev:
                def __init__(self, xr, yr):
                    self.x_root, self.y_root, self.widget = xr, yr, app

            wx, wy = app.winfo_rootx(), app.winfo_rooty()
            cy = wy + app.winfo_height() // 2
            app._on_root_press(_Ev(wx + 3, cy))          # 左边缘按下
            pump(0.4)
            for step in range(1, 6):                     # 拖 40px 后松手
                app._on_root_drag(_Ev(wx + 3 + step * 8, cy))
                pump(0.12)
            app._on_root_release(_Ev(wx + 43, cy))
            pump(1.6)
            print(f"  松手处（左边缘内 3px）：root.cursor="
                  f"{app.cget('cursor')!r} 脏名单={len(app._cursor_marks)}"
                  f"  ← 此时指针确实还在热区里，箭头是对的")
            # 把指针移到内容区正中再要一次结论。用 app 自己的重算入口
            # （``_refresh_cursor_at``）而不是 SetCursorPos：截图进程不抢前台，
            # Windows 压根不投递 WM_MOUSEMOVE，光挪系统光标读不到新状态。
            cx = app.winfo_rootx() + app.winfo_width() // 2
            cy2 = app.winfo_rooty() + app.winfo_height() // 2
            user32.SetCursorPos(cx, cy2)
            pump(0.3)
            app._refresh_cursor_at(cx, cy2)
            pump(0.4)
            print(f"  指针移到内容正中：系统指针={_cursor_name()} "
                  f"root.cursor={app.cget('cursor')!r} "
                  f"脏名单={len(app._cursor_marks)}")
            app.page.render()
            pump(0.6)
        except Exception as exc:  # noqa: BLE001
            print(f"  缩放指针场景构造失败：{exc}")

    elif extra == "popupclosed":
        # 1.5.26：新建任务浮层开了再关 —— 浮层关掉后 grab 要释放、
        # 指针不能留着、列表还能继续滚。
        try:
            import ctypes as _ct

            app.page.render()
            pump(0.6)
            app.quick_add_task()
            pump(1.1)
            popup = getattr(app, "_quick_popup", None)
            if popup is not None:
                popup.close(reason="shot")
                pump(0.9)
            canvas = app.page.scroll._parent_canvas
            toplevel_hwnd = _ct.windll.user32.GetAncestor(
                _ct.c_void_p(app.winfo_id()), 2)
            x = canvas.winfo_rootx() + canvas.winfo_width() // 2
            y = canvas.winfo_rooty() + canvas.winfo_height() // 2
            before = canvas.yview()[0]
            _ct.windll.user32.PostMessageW(
                _ct.c_void_p(toplevel_hwnd), 0x020A,
                (0xFF88 << 16), ((y & 0xFFFF) << 16) | (x & 0xFFFF))
            pump(0.4)
            after = canvas.yview()[0]
            print(f"  浮层关闭后：grab={app.grab_current()} "
                  f"root.cursor={app.cget('cursor')!r} "
                  f"滚轮 Δyview={after - before:+.4f}")
            app.page.scroll._set_hover(False)
            pump(0.3)
        except Exception as exc:  # noqa: BLE001
            print(f"  浮层开关场景构造失败：{exc}")

    elif extra in ("hintshort", "hintlong", "hintresize"):
        # 1.5.28：悬停提示门控 —— 只有标题被省略号截断时才弹白框。
        # hintshort  短标题（完整可见）悬停：**什么都不该弹**
        # hintlong   长标题（必然截断）悬停：弹框，内容是完整标题
        # hintresize 窄窗口下标题刚好变成截断：弹框
        try:
            import tkinter as tk

            grp = app.store.groups[0]
            now = _dt.datetime.now()
            short = app.store.add_task("吃饭", grp.id)
            app.store.set_due(short.id, now + _dt.timedelta(minutes=30))
            long_t = app.store.add_task(
                "整理这个季度的项目复盘并把结论同步给全部同事", grp.id)
            app.store.set_due(long_t.id, now + _dt.timedelta(hours=1))
            app.store.save()

            if extra == "hintresize":
                app.geometry("320x400")      # 最小窗口：可用宽最小
                pump(0.9)
            app.page.render()
            pump(1.0)

            def _fire_hint(card, seq: str) -> None:
                """CTkLabel.bind 把回调同时转绑到内部 _canvas 与 _label 上
                （customtkinter/ctk_label.py:307），必须发给它们才触发 ——
                真实鼠标事件本来就落在它们身上。"""
                lbl = card.title_label
                for target in (getattr(lbl, "_canvas", None),
                               getattr(lbl, "_label", None)):
                    if target is not None:
                        target.event_generate(seq)
                pump(0.4)

            target_task = short if extra == "hintshort" else long_t
            card = app.page.card_map.get(target_task.id)
            if card is None:
                print("  悬停场景：找不到目标卡片")
            else:
                tips_before = [w for w in card.winfo_children()
                               if isinstance(w, tk.Toplevel)]
                _fire_hint(card, "<Enter>")
                tips_after = [w for w in card.winfo_children()
                              if isinstance(w, tk.Toplevel)]
                want = (extra != "hintshort")
                got = len(tips_after) > 0
                print(f"  {target_task.title[:16]!r}：truncated="
                      f"{card._title_truncated} avail={card._title_avail} "
                      f"tooltip {len(tips_before)}→{len(tips_after)} "
                      f"（{'✅符合预期' if got == want else '❌不符'}）")
                if tips_after:
                    # 1.5.30：确认外层方框已被色键抠掉（透明区会露出背后的卡片）
                    t0 = tips_after[0]
                    try:
                        key = theme.c("bg")
                        tc = str(t0.attributes("-transparentcolor") or "")
                        print(f"  Toplevel：overrideredirect="
                              f"{bool(t0.overrideredirect())} bg={t0.cget('bg')!r} "
                              f"透明色键={tc!r}（期望 {key!r} "
                              f"{'✅' if tc.lower() == key.lower() else '❌'}）")
                    except Exception as exc:  # noqa: BLE001
                        print(f"  透明色键读取失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  悬停提示场景构造失败：{exc}")

    elif extra in ("bannerfold", "banneropen"):
        # 1.5.29：逾期横幅改成"柔和橘红渐层底 + 琥珀黄警示图标 + 分层明细"。
        # bannerfold = 折叠态（只看头部一行）；banneropen = 展开态（明细列表）。
        try:
            # 明细排版要看出"标题左、日期右、行距分层"的效果，一条不够 ——
            # 补几条逾期任务（只写预览用的独立数据目录）。
            grp = app.store.groups[0]
            now = _dt.datetime.now()
            for i, title in enumerate(("补交季度报销单",
                                       "回复客户的合同修改意见",
                                       "把上个月的账单核对一遍",
                                       "取一下体检报告")):
                t = app.store.add_task(title, grp.id)
                app.store.set_due(t.id, now - _dt.timedelta(hours=i + 2))
            app.store.save()
            app.page.render()
            pump(0.9)

            banner = getattr(app.page, "banner", None)
            if banner is None:
                print("  提醒条场景：找不到 banner")
            else:
                if extra == "banneropen" and not banner._expanded:
                    cv = banner.canvas
                    cv.event_generate("<Button-1>", x=6, y=6)
                    cv.event_generate("<ButtonRelease-1>", x=6, y=6)
                pump(1.0)
                items = getattr(banner, "_detail_items", [])
                print(f"  横幅 kind={banner._bg_key} 展开={banner._expanded} "
                      f"明细={len(items)} 条 更多={banner._detail_more} "
                      f"高={banner.winfo_height()}")
                for title, when in items[:4]:
                    print(f"    · {title!r} @ {when}")
        except Exception as exc:  # noqa: BLE001
            print(f"  提醒条场景构造失败：{exc}")

    # 浮层（日期选择器 / 右键菜单）都是独立 Toplevel，且都不带 -topmost。
    # 主窗口为了截图被强行置顶了，于是浮层永远压在它下面、截出来只有主窗口。
    # 这里统一把浮层也顶上去，等 z-order 重排完再抓图。
    _raise_floating(app)
    # 坐标已经是**物理像素**，不要再乘缩放系数。
    #
    # 实测（150% 缩放屏）：进程声明 DPI 感知后，``winfo_rootx()`` 返回 1214，
    # 而 Win32 ``GetWindowRect`` 也返回 1214 —— 两者完全一致，说明 Tk 报的
    # 就是物理坐标。早期版本乘了 1.5，结果裁剪框整体跑到窗口右下角之外，
    # 截出来一片黑（真踩过）。
    #
    # 之所以仍然保留 DPI 感知声明：不声明时 Tk 会拿到虚拟化坐标，
    # 窗口位置发生偏移，同样会截歪。
    x = app.winfo_rootx()
    y = app.winfo_rooty()
    w = app.winfo_width()
    h = app.winfo_height()
    # 右键菜单是独立的 Toplevel，而且它的 master 是**任务卡片**（不是主窗口），
    # 所以必须递归整棵控件树去找；只看 app.winfo_children() 是找不到的。
    # 找到后把主窗口与浮层的矩形求并集，菜单才会出现在预览图里。
    left, top, right, bottom = x, y, x + w, y + h

    toplevels: list = []
    try:
        _collect_toplevels(app, toplevels)
    except Exception:  # noqa: BLE001
        pass
    for child in toplevels:
        try:
            if not child.winfo_ismapped():
                continue
            cx, cy = child.winfo_rootx(), child.winfo_rooty()
            cw, ch = child.winfo_width(), child.winfo_height()
            if cw <= 1 or ch <= 1:
                continue
            left, top = min(left, cx), min(top, cy)
            right, bottom = max(right, cx + cw), max(bottom, cy + ch)
        except Exception:  # noqa: BLE001
            continue
    bbox = (left, top, right, bottom)
    image = _grab_checked(bbox, app=app)
    if image is None:
        # 宁可不生成，也不要用黑图覆盖上一版的好图（黑图不打开看不出来）
        print(f"  FAIL 抓屏连续失败（全黑/空白），跳过 {out_path or '(未命名)'}"
              f"  bbox={bbox}  窗口={x},{y}+{w}x{h}  "
              f"屏幕={app.winfo_vrootwidth()}x{app.winfo_vrootheight()}"
              f"@{app.winfo_vrootx()},{app.winfo_vrooty()}")
        try:
            app.quit_app()
        except Exception:  # noqa: BLE001
            pass
        return 1
    scale = 1.0

    if not out_path:
        suffix = "dark" if theme.is_dark() else "light"
        out_path = str(DEFAULT_OUT / f"shiguang-{page}-{suffix}.png")
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "PNG")
    print(f"已保存 {target}  ({image.width}x{image.height}, scale={scale:.2f})")

    try:
        app.quit_app()
    except Exception:  # noqa: BLE001
        pass
    # 退场后补几拍事件循环，把残留的 after 动画回调跑完。
    # 不这么做的话，批量模式下上一张图的后台任务会在解释器销毁后触发，
    # 刷出一屏 "invalid command name ...step / application has been destroyed"。
    try:
        for _ in range(6):
            app.update()
            time.sleep(0.05)
    except Exception:  # noqa: BLE001
        pass
    try:
        app.destroy()
    except Exception:  # noqa: BLE001
        pass
    return 0


def capture_all() -> int:
    """生成全部预览图。

    覆盖（需求 38：深浅色各覆盖 今日页 / 逾期提醒条 / 右键菜单 / 光景统计 / 设置页）：
        1)  今日页（浅色）：任务卡片布局、圆角、投影、分组徽章
        2)  今日页（深色）：深色配色对比度（bg #2B2825 / card #36322D）
        3)  逾期提醒条（浅色）：朱红底白字 + 展开明细
        4)  逾期提醒条（深色）
        5)  右键菜单（浅色）：固定 180px、图标 + 文案、无内存地址
        6)  右键菜单（深色）
        7)  光景统计（浅色）
        8)  光景统计（深色）
        9)  设置页（浅色）
        10) 设置页（深色）
        11) 粒子动画瞬间（浅色）
        12) 新建任务浮层（浅色）：按钮下方弹出、圆角投影、输入框 + 日历 + 分组 chips
        13) 新建任务浮层（深色）
        14) 日期选择器（浅色，本轮需求 ③：紧凑尺寸）
        15) 日期选择器（深色）
        16) 分组右键菜单（浅色，本轮需求 ④：稳定可选中）
        17) 分组右键菜单（深色）
        18) 图标选择浮层（浅色，需求 ⑥：4×3 暖色网格 + 柔橘选中态）
        19) 图标选择浮层（深色）
        20) 空状态（浅色，需求 ④：主文案"今天还没有拾起任何光"）
        21) 空状态（深色）
        22) 全部完成态（浅色，需求 ④ 的第三个状态）
        23) 全部完成态（深色）
        24) 缩窄窗口（浅色，需求 ②：缩放后不留残影、布局不塌）
        25) 缩窄窗口（深色）
        26) 提醒选项网格（浅色，1.5.5：提醒从原生下拉改为就地网格）
        27) 提醒选项网格（深色）
        28) 小时网格（浅色，1.5.8：标题精简 + 快捷行去掉"下周"）
        29) 小时网格（深色）
    """
    jobs = [
        ("tasks", "Light", "shiguang-1-tasks-light.png", False, 0.0, ""),
        ("tasks", "Dark", "shiguang-2-tasks-dark.png", False, 0.0, ""),
        ("tasks", "Light", "shiguang-3-banner-light.png", False, 0.0, "banner"),
        ("tasks", "Dark", "shiguang-4-banner-dark.png", False, 0.0, "banner"),
        ("tasks", "Light", "shiguang-5-menu-light.png", False, 0.0, "menu"),
        ("tasks", "Dark", "shiguang-6-menu-dark.png", False, 0.0, "menu"),
        ("stats", "Light", "shiguang-7-stats-light.png", False, 0.0, ""),
        ("stats", "Dark", "shiguang-8-stats-dark.png", False, 0.0, ""),
        ("settings", "Light", "shiguang-9-settings-light.png", False, 0.30, ""),
        ("settings", "Dark", "shiguang-10-settings-dark.png", False, 0.30, ""),
        ("tasks", "Light", "shiguang-11-particles-light.png", True, 0.0, ""),
        ("tasks", "Light", "shiguang-12-quickadd-light.png", False, 0.0, "quickadd"),
        ("tasks", "Dark", "shiguang-13-quickadd-dark.png", False, 0.0, "quickadd"),
        ("tasks", "Light", "shiguang-14-due-light.png", False, 0.0, "due"),
        ("tasks", "Dark", "shiguang-15-due-dark.png", False, 0.0, "due"),
        ("tasks", "Light", "shiguang-16-groupmenu-light.png", False, 0.0, "groupmenu"),
        ("tasks", "Dark", "shiguang-17-groupmenu-dark.png", False, 0.0, "groupmenu"),
        # ---- 本轮新增（需求 ②④⑥）----
        ("tasks", "Light", "shiguang-18-iconpicker-light.png", False, 0.0, "iconpicker"),
        ("tasks", "Dark", "shiguang-19-iconpicker-dark.png", False, 0.0, "iconpicker"),
        ("tasks", "Light", "shiguang-20-empty-light.png", False, 0.0, "empty"),
        ("tasks", "Dark", "shiguang-21-empty-dark.png", False, 0.0, "empty"),
        ("tasks", "Light", "shiguang-22-alldone-light.png", False, 0.0, "alldone"),
        ("tasks", "Dark", "shiguang-23-alldone-dark.png", False, 0.0, "alldone"),
        ("tasks", "Light", "shiguang-24-resized-light.png", False, 0.0, "resized"),
        ("tasks", "Dark", "shiguang-25-resized-dark.png", False, 0.0, "resized"),
        # ---- 1.5.13：最小窗口下的省略号截断与不重叠 ----
        ("tasks", "Light", "shiguang-30-narrow-light.png", False, 0.0, "narrow"),
        ("tasks", "Dark", "shiguang-31-narrow-dark.png", False, 0.0, "narrow"),
        # ---- 1.5.5 新增：提醒选项的就地网格（原为原生下拉，样式不可控）----
        ("tasks", "Light", "shiguang-26-remind-light.png", False, 0.0, "remind"),
        ("tasks", "Dark", "shiguang-27-remind-dark.png", False, 0.0, "remind"),
        # ---- 1.5.8：小时/分钟网格标题精简 + 快捷行去掉"下周" ----
        ("tasks", "Light", "shiguang-28-duehour-light.png", False, 0.0, "duehour"),
        ("tasks", "Dark", "shiguang-29-duehour-dark.png", False, 0.0, "duehour"),
    ]
    def _run(job):
        page, mode, name, particles, scroll, extra = job
        # 输出名统一成 `shiguang-<版本>-<场景>-<明暗>.png`。列表里的 `shiguang-<N>-`
        # 编号只做**文档索引**，不落进文件名 —— 否则每次改版都得批量改名，
        # 旧图还会和新图混在同一套命名里（1.5.4 之前的真实状况）。
        stem = re.sub(r"^shiguang-\d+-", "", name)
        out = DEFAULT_OUT / f"shiguang-{__version__}-{stem}"
        return capture(str(out), mode=mode, page=page,
                       particles=particles, scroll=scroll, extra=extra)

    failed = []
    for job in jobs:
        if _run(job) != 0:
            failed.append(job)
    # 单张失败基本是环境抖动（抓屏偶发空白），整批跑完再补拍 —— 补拍放在
    # 最后而不是原地重试，是因为失败往往是"这一刻屏幕不可渲染"，
    # 原地连试 4 次也会一起失败，隔开几秒就没问题了。
    for attempt in range(2):
        if not failed:
            break
        print(f"\n补拍第 {attempt + 1} 轮：{len(failed)} 张")
        time.sleep(2.0)
        failed = [job for job in failed if _run(job) != 0]
    if failed:
        print("\n以下预览图最终仍未生成：" + ", ".join(j[2] for j in failed))
        return 1
    print(f"\n全部 {len(jobs)} 张预览图已生成")
    return 0


def main() -> int:
    # 必须在任何窗口创建之前声明 DPI 感知，否则 150% 屏上截出来只有 3/4 大
    _enable_dpi_awareness()
    parser = argparse.ArgumentParser(description="拾光界面截图")
    parser.add_argument("out", nargs="?", default="", help="输出 PNG 路径")
    parser.add_argument("--mode", default="", choices=["", "Light", "Dark"])
    parser.add_argument("--page", default="tasks", choices=["tasks", "stats", "settings"])
    parser.add_argument("--no-demo", action="store_true", help="使用真实数据目录")
    parser.add_argument("--all", action="store_true", help="批量输出全部预览图")
    parser.add_argument("--extra", default="", help="抓图场景：banner / quickadd / "
                        "menu / groupmenu / due / duetime / duehour / remind / "
                        "confirm / iconpicker / empty / alldone / resized / "
                        "narrow / checked / states / scrolled / bar / sparse")
    args = parser.parse_args()
    if args.all:
        return capture_all()
    return capture(args.out, mode=args.mode, page=args.page, demo=not args.no_demo,
                   extra=args.extra)


if __name__ == "__main__":
    raise SystemExit(main())
