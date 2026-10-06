"""Silent core/UI audit. Every worker has a 3-second hard deadline.

The three PNGs are offscreen snapshots from the actual production renderers.
Tk roots remain withdrawn; no desktop window or image viewer is opened.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import ctypes
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SHIGUANG_NO_ACTIVATE"] = "1"
os.environ["SHIGUANG_NO_HINT"] = "0"


def contrast(first, second):
    def luminance(color):
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(rgb, (.2126, .7152, .0722)))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


def border_probe(image, border):
    from PIL import ImageColor
    rgb = ImageColor.getrgb(border)
    image = image.convert("RGBA")
    w, h = image.size
    runs = []
    for x, y, dx, dy in ((w // 2, 0, 0, 1), (w // 2, h - 1, 0, -1),
                         (0, h // 2, 1, 0), (w - 1, h // 2, -1, 0)):
        run = 0
        for n in range(12):
            sample = image.getpixel((x + n * dx, y + n * dy))
            if sample[3] < 250 or max(abs(sample[c] - rgb[c]) for c in range(3)) > 16:
                break
            run += 1
        runs.append(run)
    assert min(runs) >= 1 and max(runs) - min(runs) <= 1, runs
    assert all(image.getpixel(p)[3] == 0 for p in ((0, 0), (w - 1, 0),
                                                   (0, h - 1), (w - 1, h - 1)))
    return runs


def native_probe(root, image, radius):
    """Check the actual Win32 clipping region without mapping a window."""
    import tkinter as tk
    from ctypes import wintypes
    from shiguang.ui.window_shape import apply_rounded_region, apply_particle_region
    if os.name != "nt":
        return {"supported": False}
    win = tk.Toplevel(root)
    win.withdraw()
    win.overrideredirect(True)
    w, h = image.size
    proxy = SimpleNamespace(winfo_id=win.winfo_id, winfo_width=lambda: w,
                            winfo_height=lambda: h)
    assert apply_rounded_region(proxy, radius)
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    g.CreateRectRgn.argtypes = (ctypes.c_int,) * 4
    g.CreateRectRgn.restype = wintypes.HRGN
    g.PtInRegion.argtypes = (wintypes.HRGN, ctypes.c_int, ctypes.c_int)
    u.GetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN)
    g.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    region = g.CreateRectRgn(0, 0, 0, 0)
    try:
        hwnd = u.GetAncestor(win.winfo_id(), 2)
        assert u.GetWindowRgn(hwnd, region) == 3
        assert g.PtInRegion(region, w // 2, h // 2)
        assert not any(g.PtInRegion(region, x, y) for x, y in
                       ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
        assert apply_particle_region(proxy, [(10, 10, 18, 18)])
        u.GetWindowRgn(hwnd, region)
        assert g.PtInRegion(region, 14, 14) and not g.PtInRegion(region, 30, 30)
        assert apply_particle_region(proxy, [])
        assert u.GetWindowRgn(hwnd, region) == 1  # empty, no full-window flash
    finally:
        g.DeleteObject(region)
        win.destroy()
    return {"rounded": True, "particle_region": True, "color_key": False}


def render_worker(out):
    import datetime as dt
    import customtkinter as ctk
    from PIL import Image
    from shiguang import theme
    from shiguang.store import Store
    from shiguang.ui.task_page import DueBanner
    from shiguang.ui.widgets import toast_image, tooltip_image

    root = ctk.CTk()
    root.withdraw()
    theme.init_fonts(root)
    store = Store()
    now = dt.datetime.now()
    tasks = [store.add_task(title, store.groups[0].id) for title in
             ("整理本周的工作计划", "回复客户的合同修改意见")]
    for task in tasks:
        store.set_due(task.id, now - dt.timedelta(hours=2))
    app = SimpleNamespace(store=store)
    banner = DueBanner(root, app)
    banner.grid()
    report = {}
    try:
        for mode in ("Light", "Dark"):
            ctk.set_appearance_mode(mode)
            theme_key = mode.lower()
            width = theme.lpx(theme.DEFAULT_WINDOW_W - theme.PAGE_PAD_X * 2)
            tip, _ = tooltip_image("整理本季度项目复盘，并把结论同步给同事", width,
                                   theme.lpx(theme.DEFAULT_WINDOW_H // 2))
            toast, _, _ = toast_image("今天的任务都完成了，好好休息一下", width, True)
            action, _, action_box = toast_image("已删除任务", width, action_label="撤销")
            assert action_box and action_box[0] > 0 and action_box[2] < action.width
            ratios = {
                "tooltip": contrast(theme.c("tooltip_text"), theme.c("tooltip_bg")),
                "toast": contrast(theme.c("toast_text"), theme.c("toast_bg")),
                "banner_title": min(contrast(theme.c("banner_over_text"), theme.c(k))
                                    for k in ("banner_over_top", "banner_over_bottom")),
                "banner_date": contrast(theme.c("banner_item_date"), theme.c("banner_over_bottom")),
                "logo_mark": contrast(theme.c("banner_icon_mark"), theme.c("banner_icon_fill")),
            }
            assert min(ratios.values()) >= 4.5, ratios
            report[theme_key] = {"contrast": {k: round(v, 2) for k, v in ratios.items()},
                "tooltip_border": border_probe(tip, theme.c("tooltip_border")),
                "toast_border": border_probe(toast, theme.c("toast_border")),
                "action_border": border_probe(action, theme.c("toast_border"))}
            banner.refresh()
            banner._cancel_fade()
            assert banner._icon_kind == "warning"
            folded = banner.render_image(width, theme.lpx(theme.BANNER_HEAD_H))
            banner._toggle_detail()
            expanded = banner.render_image(width, int(banner.cget("height")))
            assert expanded.height > folded.height and banner._expanded
            banner.collapse()
            assert not banner._expanded
            if mode == "Light":
                report["native"] = native_probe(root, tip, theme.lpx(theme.TOOLTIP_RADIUS))
                tip.save(out / "tooltip.png")
                toast.save(out / "toast.png")
                # Both real states in one snapshot, with normal page spacing.
                gap = theme.lpx(theme.PAGE_GAP)
                shot = Image.new("RGB", (width, folded.height + expanded.height + gap), theme.c("bg"))
                shot.paste(folded, (0, 0), folded)
                shot.paste(expanded, (0, folded.height + gap), expanded)
                shot.save(out / "overdue-banner.png")
        for task in tasks:
            store.set_due(task.id, now + dt.timedelta(minutes=30))
        banner.refresh()
        assert banner._icon_kind == "calendar"
        for task in tasks:
            store.set_done(task.id, True)
        banner.refresh()
        assert not banner.grid_info() and not banner._expanded
        report["banner_states"] = ["overdue-folded", "overdue-expanded", "today", "hidden"]
        report["snapshot_method"] = "production-renderer-offscreen; no mapped windows"
        return report
    finally:
        root.destroy()


def ui_worker():
    import customtkinter as ctk
    from shiguang import fonts, theme
    from shiguang.app import ShiguangApp
    from shiguang.store import Store
    from shiguang.ui import dialogs
    from shiguang.ui.due_picker import DuePickerDialog
    from shiguang.ui.task_page import DragManager
    from shiguang.ui.widgets import ThinScrollFrame, toast_image

    seed = Store()
    seed.set_setting("onboarded", True)
    seed.add_task("吃饭", seed.groups[0].id)
    seed.save(force=True)

    class QuietApp(ShiguangApp):
        def _setup_window(self):
            self.withdraw()
            super()._setup_window()
        def deiconify(self):
            self.withdraw()
        def _force_taskbar_presence(self):
            pass
        def _start_hotkey(self):
            pass
        def _start_tray(self):
            pass
        def _start_reminder(self):
            pass
        def celebrate(self, *a, **k):
            pass

    global _UI_PROBE_ROOT
    app = QuietApp()
    _UI_PROBE_ROOT = app
    failures = []
    app.report_callback_exception = lambda *args: failures.append(str(args))
    try:
        app.update_idletasks()
        assert not app.winfo_viewable()
        card = next(iter(app.page.card_map.values()))
        card._title_truncated = False
        card._title_avail = fonts.measure("task_title", card.task.title) + 1
        assert not card._title_hint_needed()
        card._title_avail = 1
        assert card._title_hint_needed()
        state = card.timer_btn._shiguang_hint_state
        binding = card.timer_btn._canvas.bind("<Enter>")
        for _ in range(10):
            card.set_timer_state(False, "", 0)
        assert card.timer_btn._shiguang_hint_state is state
        assert card.timer_btn._canvas.bind("<Enter>") == binding
        for page in ("stats", "settings", "tasks"):
            app.show_page(page)
            app.update_idletasks()
        assert not app.winfo_viewable() and not failures
        # Date controls are constructed and exercised while their show is suppressed.
        original_show = dialogs._BaseDialog.show
        dialogs._BaseDialog.show = lambda self: None
        result = []
        try:
            picker = DuePickerDialog(app, on_save=lambda *value: result.append(value), restore_focus=False)
            picker._pick_remind(None)
            picker._clear()
            assert result == [(None, None)] and app.grab_current() is None
        finally:
            dialogs._BaseDialog.show = original_show
        # Core drag debounce: repeated pointer events at one target apply once.
        calls = []
        page = SimpleNamespace(has_active_filter=False, render=lambda: None,
            locate=lambda y: ("work", 0), apply_drop=lambda *a: calls.append(a),
            finish_drag=lambda: None)
        drag = DragManager(page)
        drag.on_press("id", SimpleNamespace(x_root=0, y_root=0))
        drag.on_motion(SimpleNamespace(x_root=1, y_root=1))
        assert not drag.active
        for _ in range(10):
            drag.on_motion(SimpleNamespace(x_root=20, y_root=20))
        assert len(calls) == 1
        drag.on_release(None)
        # Scrollbar hysteresis is tested without mapping a scroll window.
        scroll = SimpleNamespace(_bar_job=None, _bar_busy=False, _bar=object(),
            _bar_shown=False, _parent_canvas=SimpleNamespace(winfo_height=lambda: 300),
            winfo_reqheight=lambda: 600)
        scroll._set_bar_shown = lambda value: setattr(scroll, "_bar_shown", value)
        ThinScrollFrame._sync_visibility(scroll)
        assert scroll._bar_shown
        scroll.winfo_reqheight = lambda: 100
        ThinScrollFrame._sync_visibility(scroll)
        assert not scroll._bar_shown
        # Exercise the actual Canvas layout and hit area while mapping is suppressed.
        ran = []
        app.winfo_width = lambda: theme.lpx(theme.DEFAULT_WINDOW_W)
        app.winfo_height = lambda: theme.lpx(theme.DEFAULT_WINDOW_H)
        app.winfo_viewable = lambda: True  # simulate host visibility; HWND stays hidden
        app.is_foreground = lambda: True
        app.update_idletasks = lambda: None
        toast = app.toast_widget
        toast.deiconify = lambda: None
        toast.update_idletasks = lambda: None
        toast.show("已删除任务", action_label="撤销", action_command=lambda: ran.append(True))
        assert int(toast.canvas.cget("width")) == toast._image.width
        assert int(toast.canvas.cget("height")) == toast._image.height
        assert int(toast.canvas.cget("highlightthickness")) == 0
        assert toast.overrideredirect() and not toast.winfo_viewable()
        x1, y1, x2, y2 = toast._action_box
        toast._click(SimpleNamespace(x=(x1 + x2) // 2, y=(y1 + y2) // 2))
        assert ran == [True] and toast._job is None and not toast.winfo_viewable()
        if os.name == "nt":
            assert not ctypes.windll.user32.IsWindowVisible(app._hwnd())
        assert (theme.DEFAULT_WINDOW_W, theme.DEFAULT_WINDOW_H) == (344, 470)
        assert (theme.MIN_WINDOW_W, theme.MIN_WINDOW_H) == (320, 400)
        return {"startup_pages": True, "tooltip_gate": True, "single_hint_binding": True,
                "date_clear": True, "drag_debounce": True, "scroll_visibility": True,
                "toast_action": True, "windows_mapped": False,
                "interactive_shutdown_checked": False}
    except BaseException:
        import traceback
        print(traceback.format_exc(), flush=True)
        raise
    finally:
        # This worker has never mapped its root or entered a normal mainloop.
        # Process isolation owns its lifetime; interactive shutdown is not probed.
        for job in app.tk.call("after", "info"):
            app.after_cancel(job)
        logging.shutdown()


def static_worker():
    colors, keys = [], []
    for path in [*(ROOT / "shiguang/ui").glob("*.py"), ROOT / "shiguang/app.py", ROOT / "shiguang/icons.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.fullmatch(r"#[0-9a-fA-F]{6}", node.value):
                    colors.append(f"{path.name}:{node.lineno}")
                if node.value == "-transparentcolor":
                    keys.append(f"{path.name}:{node.lineno}")
    assert not colors and not keys, (colors, keys)
    return {"ui_modules_scanned": len(list((ROOT / "shiguang/ui").glob("*.py"))),
            "literal_colors": colors, "color_keys": keys}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("static", "core", "ui", "render"))
    parser.add_argument("--out", default=str(ROOT / "tools/preview"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.worker:
        if os.name == "nt":
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        with tempfile.TemporaryDirectory(prefix="shiguang-audit-") as data:
            os.environ["SHIGUANG_DATA_DIR"] = data
            try:
                if args.worker == "core":
                    from tests.test_core import run
                    with contextlib.redirect_stdout(io.StringIO()):
                        assert run() == 0
                    result = {"crud_persistence_backup_focus": True}
                else:
                    result = {"static": static_worker, "ui": ui_worker,
                              "render": lambda: render_worker(out)}[args.worker]()
                print(json.dumps(result, ensure_ascii=False))
            finally:
                logging.shutdown()
        if args.worker == "ui":
            sys.stdout.flush()
            os._exit(0)  # release the isolated, never-mapped Tcl interpreter
        return 0
    from shiguang import __version__
    report = {"version": __version__, "operation_timeout_seconds": 3}
    for operation in ("static", "core", "ui", "render"):
        started = time.monotonic()
        attempts = 3 if operation == "render" else 1
        for attempt in range(attempts):
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                "--worker", operation, "--out", str(out)], capture_output=True, text=True,
                encoding="utf-8", timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if completed.returncode == 0:
                report[operation] = json.loads(completed.stdout.strip().splitlines()[-1])
                break
            if attempt + 1 == attempts:
                raise RuntimeError(f"{operation}: {completed.stdout.strip()} {completed.stderr.strip()}")
        report[operation]["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (out / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
