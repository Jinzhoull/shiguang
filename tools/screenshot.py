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


def _above_in_window_stack(front, back):
    """Windows Z 序实测：前窗是否排在后窗上方；无法测量返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetTopWindow.argtypes = [wintypes.HWND]
        user32.GetTopWindow.restype = wintypes.HWND
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetWindow.restype = wintypes.HWND
        front_hwnd = user32.GetAncestor(front.winfo_id(), 2)
        back_hwnd = user32.GetAncestor(back.winfo_id(), 2)
        current = user32.GetTopWindow(None)
        for _ in range(4096):
            if not current:
                break
            if current == front_hwnd:
                return True
            if current == back_hwnd:
                return False
            current = user32.GetWindow(current, 2)  # GW_HWNDNEXT
    except Exception:  # noqa: BLE001
        pass
    return None


def _native_round_region_ok(window, native_root=True):
    """Windows 上确认整个弹窗被裁成圆角，不能只检查 CTk 绘制内容。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN]
        user32.GetWindowRgn.restype = ctypes.c_int
        gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
        gdi32.CreateRectRgn.restype = wintypes.HRGN
        gdi32.PtInRegion.argtypes = [wintypes.HRGN, ctypes.c_int, ctypes.c_int]
        gdi32.PtInRegion.restype = wintypes.BOOL
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL

        hwnd = wintypes.HWND(window.winfo_id())
        if native_root:
            hwnd = user32.GetAncestor(hwnd, 2) or hwnd
        region = gdi32.CreateRectRgn(0, 0, 1, 1)
        if not hwnd or not region:
            return False
        try:
            if user32.GetWindowRgn(hwnd, region) != 3:  # COMPLEXREGION
                return False
            width, height = window.winfo_width(), window.winfo_height()
            corners = ((0, 0), (width - 1, 0),
                       (0, height - 1), (width - 1, height - 1))
            return (all(not gdi32.PtInRegion(region, x, y) for x, y in corners)
                    and bool(gdi32.PtInRegion(region, width // 2, height // 2)))
        finally:
            gdi32.DeleteObject(region)
    except Exception:  # noqa: BLE001
        return False


def _uniform_window_border(image, bbox, window, color, radius):
    """量四边宽度与四个角的描边连续性。"""
    from PIL import ImageColor

    rgb = ImageColor.getrgb(color)
    pixels = image.convert("RGB")
    x = window.winfo_rootx() - bbox[0]
    y = window.winfo_rooty() - bbox[1]
    width, height = window.winfo_width(), window.winfo_height()
    cx, cy = x + width // 2, y + height // 2
    points = ((cx, y, 0, 1), (cx, y + height - 1, 0, -1),
              (x, cy, 1, 0), (x + width - 1, cy, -1, 0))
    runs = []
    for px, py, dx, dy in points:
        run = 0
        for offset in range(12):
            sample = (px + dx * offset, py + dy * offset)
            if not (0 <= sample[0] < image.width and 0 <= sample[1] < image.height):
                break
            if pixels.getpixel(sample) != rgb:
                break
            run += 1
        runs.append(run)
    def border_tone(pixel):
        return all(abs(pixel[channel] - rgb[channel]) <= 24 for channel in range(3))

    corners = []
    for right in (False, True):
        xs = (range(x + width - radius - 3, x + width) if right
              else range(x, x + radius + 3))
        for bottom in (False, True):
            ys = (range(y + height - radius, y + height) if bottom
                  else range(y, y + radius))
            corners.append(all(any(border_tone(pixels.getpixel((px, py))) for px in xs)
                               for py in ys))
    return (max(runs) - min(runs) <= 1 and min(runs) >= 1
            and all(corners)), runs, corners


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
    if extra.startswith(("hint", "toast")):
        app.is_foreground = lambda: True  # freeze only the preview watchdog
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
        # Modal-window regression scenes must use the app's configured stacking
        # mode. Forcing only the root above every other window can hide an owned
        # modal while its grab remains active, manufacturing the exact deadlock
        # these scenes are meant to detect.
        modal_scene = extra in ("taskdialog-due", "quickadd-due", "taskdialog",
                                "menu-due-action", "menu-due-clear-action",
                                "menu-move-view", "menu-move-action",
                                "menu-edit-action", "menu-delete-action",
                                "group-rename-action", "group-icon-action")
        app.attributes("-topmost", bool(app.store.settings.get("always_on_top", False))
                       if modal_scene else True)
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
            if holder is None:
                holder = next((child for child in app.page.winfo_children()
                               if hasattr(child, "_parent_canvas")), None)
            if holder is not None:
                holder._parent_canvas.yview_moveto(scroll)
                pump(0.5)
        except Exception as exc:  # noqa: BLE001
            print(f"  滚动失败：{exc}")

    if particles:
        # 粒子动画约 1.5 秒，抓 0.45 秒那一帧：粒子已散开、还没熄灭，最有观感
        app.particles.play(26)
        pump(0.45)

    regression_ok = True
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

    elif extra == "nav-focus":
        # 复现点击/Tab 聚焦导航后，Tk Canvas 自带的一圈橙色矩形高亮。
        app.nav.focus_force()
        pump(0.25)
        print(f"  导航焦点={app.focus_get() == app.nav} "
              f"高亮宽度={app.nav.cget('highlightthickness')}")
        regression_ok = (app.focus_get() == app.nav
                         and int(app.nav.cget("highlightthickness")) == 0)
        app.nav.event_generate("<Right>")
        pump(0.12)
        keyboard_ok = app.nav.current() == "stats"
        app.nav.event_generate("<Left>")
        pump(0.12)
        regression_ok = regression_ok and keyboard_ok and app.nav.current() == "tasks"
        print(f"  左右方向键切换={keyboard_ok and app.nav.current() == 'tasks'}")

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

    elif extra in ("filter", "filter-narrow"):
        # 任务筛选器：验证圆角面板在主窗口客户区内展开，不生成系统菜单窗口。
        try:
            if extra == "filter-narrow":
                app.geometry("320x400")
                pump(0.6)
            dropdown = app.page.filter_menu
            dropdown.open()
            pump(0.35)
            pop = dropdown._popup
            if pop is not None:
                ax, ay = app.winfo_rootx(), app.winfo_rooty()
                bx, by = ax + app.winfo_width(), ay + app.winfo_height()
                px, py = pop.winfo_rootx(), pop.winfo_rooty()
                pw, ph = pop.winfo_width(), pop.winfo_height()
                inside = px >= ax and py >= ay and px + pw <= bx and py + ph <= by
                rounded = _native_round_region_ok(pop, native_root=False)
                regression_ok = regression_ok and inside and rounded
                print(f"  筛选面板 {pw}x{ph}@{px},{py} 主窗口 {app.winfo_width()}x"
                      f"{app.winfo_height()}@{ax},{ay} 落在窗口内={inside} "
                      f"子窗口圆角裁剪={rounded}")
                if not inside:
                    print("  WARN 任务筛选面板超出主窗口边界")
        except Exception as exc:  # noqa: BLE001
            print(f"  筛选面板弹出失败：{exc}")

    elif extra == "groupdropdown":
        # 任务编辑里的分组选择器与主页面共用同一套应用内下拉样式。
        try:
            from shiguang.ui.dialogs import TaskDialog

            dialog = TaskDialog(app)
            app._shot_group_dialog = dialog
            pump(0.6)
            dialog.group_menu.open()
            pump(0.35)
            pop = dialog.group_menu._popup
            if pop is not None:
                ax, ay = dialog.winfo_rootx(), dialog.winfo_rooty()
                bx, by = ax + dialog.winfo_width(), ay + dialog.winfo_height()
                px, py = pop.winfo_rootx(), pop.winfo_rooty()
                pw, ph = pop.winfo_width(), pop.winfo_height()
                inside = px >= ax and py >= ay and px + pw <= bx and py + ph <= by
                rounded = _native_round_region_ok(pop, native_root=False)
                regression_ok = regression_ok and inside and rounded
                print(f"  分组选择面板落在编辑窗口内={inside} "
                      f"子窗口圆角裁剪={rounded}")
        except Exception as exc:  # noqa: BLE001
            print(f"  分组选择面板弹出失败：{exc}")

    elif extra == "prompt":
        # 自绘标题栏的单行输入弹窗：核对重命名分组不再使用 Windows 默认边框。
        try:
            from shiguang.ui.dialogs import PromptDialog

            app._shot_prompt = PromptDialog(
                app, "重命名分组", "分组名称", "工作", lambda _value: None)
            pump(0.6)
        except Exception as exc:  # noqa: BLE001
            print(f"  单行输入弹窗失败：{exc}")

    elif extra == "taskdialog":
        # 完整任务编辑窗：检查新增/编辑表单迁入统一标题栏后的布局。
        try:
            from shiguang.ui.dialogs import TaskDialog

            app._shot_task_dialog = TaskDialog(app)
            pump(0.6)
        except Exception as exc:  # noqa: BLE001
            print(f"  任务编辑弹窗失败：{exc}")

    elif extra == "taskdialog-due":
        # 回归嵌套模态：任务编辑 -> 截止日期。先取消、再确认一次，检查每次关闭
        # 都把窗口层级与 grab 交还给任务编辑，再重新打开供截图核对前景显示。
        try:
            from shiguang.ui.dialogs import TaskDialog
            from shiguang.ui.due_picker import DuePickerDialog

            owner = TaskDialog(app)
            app._shot_task_dialog = owner
            pump(0.25)

            def open_due():
                owner.due_btn.invoke()
                pump(0.3)
                found = next((w for w in owner.winfo_children()
                              if isinstance(w, DuePickerDialog)), None)
                if found is None:
                    print("  DEBUG task children=" + repr([
                        (w, w.winfo_class(), w.winfo_viewable())
                        for w in owner.winfo_children()]))
                return found

            def grab_owner():
                grab = app.grab_current()
                return grab.winfo_toplevel() if grab is not None else None

            def owns_grab(window):
                grab_top = grab_owner()
                return (grab_top is not None and window is not None
                        and str(grab_top._w) == str(window._w))

            picker = open_due()
            opened_ok = bool(
                picker and picker.winfo_viewable()
                and str(picker.transient()) == str(owner._w)
                and owns_grab(picker))
            print(f"  任务编辑内日期页可见/在前景并取得 grab={opened_ok}")
            if picker:
                picker._cancel()
            pump(0.25)
            cancel_ok = bool(
                owner.winfo_viewable() and owns_grab(owner))
            print(f"  取消后任务编辑仍在前景且恢复 grab={cancel_ok}")

            picker = open_due()
            if picker:
                picker._confirm()
            pump(0.25)
            save_ok = bool(
                owner.winfo_viewable() and owns_grab(owner)
                and owner._due is not None)
            print(f"  确认后任务编辑恢复且截止日期已写入={save_ok}")
            picker = open_due()
            if not (opened_ok and cancel_ok and save_ok and picker
                    and picker.winfo_viewable() and owns_grab(picker)):
                print("  WARN 嵌套日期选择器回归未通过")
        except Exception as exc:  # noqa: BLE001
            print(f"  任务编辑截止日期回归失败：{exc}")

    elif extra == "quickadd-due":
        # 回归快速新建的日期页：取消/确认后浮层都应恢复前景，且确认值留在输入行。
        try:
            from shiguang.ui.due_picker import DuePickerDialog

            app.open_quick_add()
            pump(0.3)
            owner = getattr(app, "_quick_popup", None)

            def open_due():
                if owner is None or owner._closed:
                    return None
                owner.cal_btn.invoke()
                pump(0.3)
                return next((w for w in app.winfo_children()
                             if isinstance(w, DuePickerDialog)), None)

            def grab_owner():
                grab = app.grab_current()
                return grab.winfo_toplevel() if grab is not None else None

            def owns_grab(window):
                grab_top = grab_owner()
                return (grab_top is not None and window is not None
                        and str(grab_top._w) == str(window._w))

            picker = open_due()
            opened_ok = bool(picker and picker.winfo_viewable()
                             and owns_grab(picker))
            print(f"  快速新建日期页可见/在前景并取得 grab={opened_ok}")
            if picker:
                picker._cancel()
            pump(0.25)
            cancel_ok = bool(owner and not owner._closed and owner.winfo_viewable())
            print(f"  取消后快速新建浮层恢复前景={cancel_ok}")

            picker = open_due()
            if picker:
                picker._confirm()
            pump(0.25)
            save_ok = bool(
                owner and not owner._closed and owner.winfo_viewable()
                and owner._due is not None)
            print(f"  确认后快速新建恢复且截止日期已写入={save_ok}")
            picker = open_due()
            if not (opened_ok and cancel_ok and save_ok and picker
                    and picker.winfo_viewable() and owns_grab(picker)):
                print("  WARN 快速新建日期选择器回归未通过")
        except Exception as exc:  # noqa: BLE001
            print(f"  快速新建截止日期回归失败：{exc}")

    elif extra == "about":
        # 关于窗口也继承 _BaseDialog，纳入同一套外观回归。
        try:
            from shiguang.ui.dialogs import AboutDialog

            app._shot_about = AboutDialog(app)
            pump(0.6)
        except Exception as exc:  # noqa: BLE001
            print(f"  关于弹窗失败：{exc}")

    elif extra == "search":
        # 搜索的放大镜/有字清除叉，以及叉只清查询、不改状态筛选的语义回归。
        try:
            from shiguang import strings

            page = app.page
            page.filter_key = "overdue"
            page.filter_menu.set(strings.TASK_FILTER_OPTIONS[2])
            page.search_entry.insert("end", "报销")
            page._sync_search_clear()
            page._render_search_now()
            pump(0.35)
            page.search_clear.invoke()
            preserves_filter = (
                page.filter_key == "overdue"
                and page.filter_menu.get() == strings.TASK_FILTER_OPTIONS[2])
            print(f"  搜索叉保留逾期筛选={preserves_filter}")
            page.search_entry.insert("end", "报销")
            page._sync_search_clear()
            page._render_search_now()
            pump(0.35)
        except Exception as exc:  # noqa: BLE001
            print(f"  搜索场景构造失败：{exc}")

    elif extra == "search-empty":
        try:
            page = app.page
            # 初始化时已经是空查询；不要 programmatically delete 一次，否则
            # CustomTkinter 会把原生 placeholder 的绘制状态清掉，截图反而失真。
            page._sync_search_clear()
            pump(0.2)
            print("  空搜索状态正常：",
                  not page.search_entry.get() and not page.search_clear.winfo_manager())
        except Exception as exc:  # noqa: BLE001
            print(f"  空搜索场景构造失败：{exc}")

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

    elif extra in ("menu-due-action", "menu-due-clear-action",
                   "menu-move-view", "menu-move-action",
                   "menu-edit-action", "menu-delete-action",
                   "group-rename-action", "group-icon-action"):
        # 真实操作链：任务右键菜单 -> 子选项 -> 截止日期弹窗/移动分组。
        # 只测直接构造 DuePickerDialog 会漏掉菜单的置顶残留与点击时序。
        from shiguang.ui import menu as ui_menu
        from shiguang.ui.due_picker import DuePickerDialog
        from shiguang.ui.dialogs import ConfirmDialog, PromptDialog, TaskDialog
        from shiguang.ui.icon_picker import IconPickerPopup

        def click_named(menu_obj, label: str) -> bool:
            y = 0
            for item in menu_obj._rows:
                height = menu_obj.SEP_H if item.is_separator else menu_obj.ROW_H
                if item.label.startswith(label):
                    menu_obj.canvas.event_generate(
                        "<Button-1>", x=menu_obj.PAD + theme.lpx(20),
                        y=y + height // 2)
                    pump(0.18)
                    return True
                y += height
            return False

        try:
            group_action = extra.startswith("group-")
            card = next(iter(app.page.card_map.values()))
            group_card = next(iter(app.page.group_cards.values()))
            at = (app.winfo_rootx() + app.winfo_width() - theme.lpx(14),
                  app.winfo_rooty() + app.winfo_height() - theme.lpx(180))
            if group_action:
                group_card._show_group_menu(at=at)
            else:
                card._show_menu(at=at)
            pump(0.2)
            parent = ui_menu._ACTIVE[-1]
            _freeze_watchdog(parent)
            labels = [item.label for item in parent._rows]
            if not group_action:
                if "10 分钟后再提醒" in labels:
                    raise AssertionError("任务菜单仍显示已删除的延后提醒选项")
                due_row = next((item for item in parent._rows
                                if item.label == "设置截止日期"), None)
                if due_row is None or due_row.is_submenu:
                    raise AssertionError("截止日期应直接打开日期窗")
            wanted = {
                "menu-due-action": "设置截止日期",
                "menu-due-clear-action": "设置截止日期",
                "menu-move-view": "移动到分组",
                "menu-move-action": "移动到分组",
                "menu-edit-action": "编辑任务",
                "menu-delete-action": "删除任务",
                "group-rename-action": "重命名分组",
                "group-icon-action": "更换图标",
            }[extra]
            if not click_named(parent, wanted):
                raise AssertionError(f"任务菜单缺少 {wanted}")
            if parent.winfo_exists():
                _freeze_watchdog(parent)
            panel = (parent._chain[-1] if getattr(parent, "_chain", None)
                     else parent)
            if extra in ("menu-move-view", "menu-move-action"):
                group_rows = [item for item in panel._rows if not item.is_separator
                              and item is not panel._back_row]
                icons_ok = all(item.icon is not None for item in group_rows)
                selected_ok = sum(bool(item.selected) for item in group_rows) == 1
                px, py = parent.winfo_rootx(), parent.winfo_rooty()
                sx, sy = panel.winfo_rootx(), panel.winfo_rooty()
                overlap = (parent is not panel and
                           px < sx + panel.winfo_width() and sx < px + parent.winfo_width()
                           and py < sy + panel.winfo_height() and sy < py + parent.winfo_height())
                wx, wy = app.winfo_rootx(), app.winfo_rooty()
                inside = (sx >= wx and sy >= wy
                          and sx + panel.winfo_width() <= wx + app.winfo_width()
                          and sy + panel.winfo_height() <= wy + app.winfo_height())
                print(f"  移动分组面板在软件内={inside} 与父菜单重叠={overlap} "
                      f"图标齐全={icons_ok} 当前分组标记={selected_ok}")
                regression_ok = (inside and not overlap and panel is parent
                                 and icons_ok and selected_ok)
                if extra == "menu-move-action":
                    original = card.task.group_id
                    target = next((g for g in app.store.groups if g.id != original), None)
                    if target is None or not click_named(panel, target.name):
                        raise AssertionError("移动分组子面板缺少其他分组")
                    pump(0.3)
                    moved = app.store.task(card.task.id).group_id == target.id
                    closed = not parent.winfo_exists() and not ui_menu._ACTIVE
                    print(f"  菜单移动分组：已移动={moved} 菜单关闭={closed}")
                    regression_ok = regression_ok and moved and closed
            else:
                pump(0.65)
                tops = []
                _collect_toplevels(app, tops)
                expected = {
                    "menu-due-action": DuePickerDialog,
                    "menu-due-clear-action": DuePickerDialog,
                    "menu-edit-action": TaskDialog,
                    "menu-delete-action": ConfirmDialog,
                    "group-rename-action": PromptDialog,
                    "group-icon-action": IconPickerPopup,
                }[extra]
                picker = next((w for w in tops if isinstance(w, expected)
                               and w.winfo_exists()), None)
                grab = app.grab_current()
                grab_ok = (expected is IconPickerPopup or
                           (picker is not None and grab is not None
                            and str(grab.winfo_toplevel()._w) == str(picker._w)))
                menu_closed = not parent.winfo_exists() and not ui_menu._ACTIVE
                picker_ready = bool(picker and picker.winfo_viewable() and grab_ok)
                above = _above_in_window_stack(picker, app) if picker else None
                print(f"  {extra}：菜单关闭={menu_closed} "
                      f"目标窗口可见且 grab 正确={picker_ready} "
                      f"目标窗口高于主窗口={above}")
                regression_ok = (menu_closed and picker_ready and
                                 (above is True if os.name == "nt" else above is not False))
                if extra == "menu-due-clear-action" and picker is not None:
                    picker._clear()
                    pump(0.4)
                    cleared = app.store.task(card.task.id).due_date is None
                    picker_closed = not picker.winfo_exists()
                    print(f"  日期窗清除截止日期：已清除={cleared} 弹窗关闭={picker_closed}")
                    regression_ok = regression_ok and cleared and picker_closed
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL 任务菜单子选项回归：{exc}")
            regression_ok = False

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

    elif extra in ("toast", "toast-action"):
        # Toast 浮层：检查圆角窗口裁切在普通提示与完成提示两种布局下均有效。
        try:
            if extra == "toast-action":
                app.toast_widget.show("已删除任务", duration=6000,
                                      action_label="撤销", action_command=lambda: None)
            else:
                app.toast_widget.show("今天的任务都完成了，光是你的了", duration=6000,
                                      celebration=True)
            pump(0.35)
        except Exception as exc:  # noqa: BLE001
            print(f"  Toast 弹出失败：{exc}")

    elif extra == "toast-done":
        # 完成卡的淡绿色背景曾被拿来填整个 Toast 原生窗口，圆角外泄露绿块。
        try:
            card = next(card for card in app.page.card_map.values() if card.task.done)
            app.toast_widget.show("你把今天的光都收进了口袋", duration=6000,
                                  anchor=card.check, celebration=True)
            pump(0.35)
        except Exception as exc:  # noqa: BLE001
            print(f"  完成任务 Toast 弹出失败：{exc}")

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

    elif extra in ("alldone", "alldone-narrow"):
        # 全部完成态与最窄窗口排版。
        try:
            for _t in list(app.store.all_tasks()):
                app.store.update_task(_t.id, done=True)
            app.store.save()
            app.page.render()
            if extra == "alldone-narrow":
                app.geometry("392x600")
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
                    t0 = tips_after[0]
                    native_ok = _native_round_region_ok(t0)
                    canvas = next((w for w in t0.winfo_children()
                                   if isinstance(w, tk.Canvas)), None)
                    canvas_ok = bool(canvas and int(canvas.cget("highlightthickness")) == 0)
                    print(f"  无边框={bool(t0.overrideredirect())} "
                          f"原生圆角={native_ok} Canvas 无高亮={canvas_ok}")
                    regression_ok = regression_ok and native_ok and canvas_ok
                regression_ok = regression_ok and got == want
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
    if extra in ("menu-due-action", "menu-due-clear-action",
                 "menu-move-view", "menu-move-action",
                 "menu-edit-action", "menu-delete-action",
                 "group-rename-action", "group-icon-action"):
        # 断言已经在自然 Z 序下完成；截图时再把测试应用抬到桌面前面，避免
        # 抓进当前用户正在看的其他窗口而误判这张菜单的视觉效果。
        try:
            app.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
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
    if extra in ("prompt", "taskdialog", "due", "about", "confirm",
                 "quickadd", "iconpicker", "menu", "toast", "toast-done",
                 "toast-action") and os.name == "nt":
        mapped = [child for child in toplevels if child.winfo_ismapped()]
        rounded = bool(mapped) and all(_native_round_region_ok(child) for child in mapped)
        regression_ok = regression_ok and rounded
        print(f"  原生圆角：{'通过' if rounded else '失败'}  窗口数={len(mapped)}")
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
    if extra in ("prompt", "taskdialog", "due", "about", "confirm"):
        popup = next((child for child in toplevels if child.winfo_ismapped()), None)
        if popup is not None:
            radius = theme.lpx(theme.CONFIRM_RADIUS if extra == "confirm"
                               else theme.RADIUS_MENU) + 2
            border_ok, widths, corners = _uniform_window_border(
                image, bbox, popup, theme.c("window_border"), radius)
            regression_ok = regression_ok and border_ok
            print(f"  四边描边：{'通过' if border_ok else '失败'}  "
                  f"像素={widths} 四角连续={corners}")
    if extra in ("toast", "toast-done", "toast-action"):
        border_ok, widths, corners = _uniform_window_border(
            image, bbox, app.toast_widget, theme.c("toast_border"), theme.lpx(theme.TOAST_RADIUS))
        regression_ok = regression_ok and border_ok
        print(f"  Toast 描边：{'通过' if border_ok else '失败'} "
              f"像素={widths} 四角连续={corners}")
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
    return 0 if regression_ok else 1


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
    parser.add_argument("--scroll", type=float, default=0.0,
                        help="页面滚动位置（0.0 到 1.0，检查长页面下半部分）")
    parser.add_argument("--no-demo", action="store_true", help="使用真实数据目录")
    parser.add_argument("--all", action="store_true", help="批量输出全部预览图")
    parser.add_argument("--extra", default="", help="抓图场景：banner / quickadd / filter / filter-narrow / "
                        "menu / menu-due-action / menu-due-clear-action / "
                        "menu-move-view / menu-move-action / "
                        "menu-edit-action / menu-delete-action / group-rename-action / "
                        "group-icon-action / groupmenu / due / duetime / duehour / remind / "
                        "groupdropdown / prompt / taskdialog / about / search / "
                        "taskdialog-due / quickadd-due / "
                        "search-empty / confirm / toast / toast-done / toast-action / "
                        "iconpicker / empty / alldone / alldone-narrow / nav-focus / resized / "
                        "narrow / checked / states / scrolled / bar / sparse")
    args = parser.parse_args()
    if args.all:
        return capture_all()
    return capture(args.out, mode=args.mode, page=args.page, demo=not args.no_demo,
                   scroll=args.scroll, extra=args.extra)


if __name__ == "__main__":
    raise SystemExit(main())
