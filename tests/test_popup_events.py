"""Real Tk press/release routing regression, using only offscreen test windows.

Unlike command.invoke(), this exercises the widget and toplevel bindtags.
Foreground, focus and pointer are simulated; all close watchdogs remain enabled.
Each case runs with isolated data and a hard three-second deadline.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SHIGUANG_NO_ACTIVATE"] = "1"
os.environ["SHIGUANG_NO_HINT"] = "1"


def run_case(case):
    import tkinter as tk
    import customtkinter as ctk
    from shiguang.app import ShiguangApp
    from shiguang.store import Store
    from shiguang.ui.quick_add import QuickAddPopup
    from shiguang.ui import menu

    # Prevent any native test window from reaching the visible desktop/focus.
    geometry = tk.Toplevel.geometry
    def offscreen(self, value=None):
        if value is None:
            return geometry(self)
        size = value.split("+", 1)[0].split("-", 1)[0]
        if "x" not in size:
            size = f"{self.winfo_reqwidth()}x{self.winfo_reqheight()}"
        return geometry(self, size + "+30000+30000")
    tk.Toplevel.geometry = offscreen
    tk.Misc.focus_force = lambda self: None
    QuickAddPopup._focus_entry = lambda self: None
    QuickAddPopup._focus_inside = lambda self: True
    menu.ContextMenu.winfo_pointerxy = lambda self: (self.winfo_rootx() + 20,
                                                    self.winfo_rooty() + 20)
    seed = Store()
    seed.set_setting("onboarded", True)
    seed.add_task("事件测试任务", seed.groups[0].id)
    seed.save(force=True)

    class TestApp(ShiguangApp):
        def _initial_geometry(self):
            return "344x470+30000+30000"
        def _force_taskbar_presence(self):
            pass
        def _ensure_on_screen(self):
            pass
        def _geometry_guard(self):
            pass
        def _start_hotkey(self):
            pass
        def _start_tray(self):
            pass
        def _start_reminder(self):
            pass
        def is_foreground(self):
            return getattr(self, "test_foreground", True)

    app = TestApp()
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.update()
            time.sleep(.01)
    def click(button):
        target = button._canvas
        target.event_generate("<Enter>")
        target.event_generate("<Button-1>", x=10, y=10)
        target.event_generate("<ButtonRelease-1>", x=10, y=10)
    try:
        pump(.12)
        assert app.winfo_rootx() >= 30000 and app.winfo_rooty() >= 30000
        group = next(iter(app.page.group_cards.values()))
        if case in ("top", "outside", "blur"):
            click(app.new_task_btn)
        elif case in ("group", "replace"):
            if case == "replace":
                click(app.new_task_btn)
                pump(.08)
            click(group.add_btn)
        else:
            trigger = next(w for w in group.header.winfo_children()
                           if getattr(w, "_icon_key", "") == "more")
            click(trigger)
        pump(.85)
        if case in ("top", "group", "replace", "outside", "blur"):
            popup = getattr(app, "_quick_popup", None)
            assert popup and popup.winfo_exists() and not popup._closing, "new task popup vanished"
            assert popup.winfo_rootx() >= 30000
            assert app._main_press_tag not in popup.ok_btn._canvas.bindtags()
            if case in ("group", "replace"):
                assert popup.group_id == group.group.id
            if case == "outside":
                app.date_label._label.event_generate("<Button-1>", x=2, y=2)
                pump(.18)
                assert popup.closed
            elif case == "blur":
                app.test_foreground = False
                pump(.75)
                assert popup.closed
            else:
                title = f"{case} 创建验证"
                popup.entry.insert(0, title)
                click(popup.ok_btn)
                pump(.08)
                assert any(t.title == title for t in app.store.all_tasks())
        else:
            assert menu._ACTIVE, "group options vanished"
            panel = menu._ACTIVE[-1]
            assert panel.winfo_exists() and not panel._closing
            assert panel.winfo_rootx() >= 30000
            if case == "menu":
                # Clicking outside must still dismiss it.
                app.date_label._label.event_generate("<Button-1>", x=2, y=2)
                pump(.08)
                assert not menu._ACTIVE
            else:
                panel.canvas.event_generate("<Button-1>", x=panel.PAD + 10,
                                            y=panel.ROW_H // 2)
                pump(.12)
                popup = getattr(app, "_quick_popup", None)
                assert popup and popup.winfo_exists() and not popup._closing
                assert popup.group_id == group.group.id
        assert not errors, errors
        return {"case": case, "ctk": ctk.__version__, "survived_opening_click": True,
                "watchdogs_enabled": True, "desktop_windows": False}
    finally:
        for job in app.tk.call("after", "info"):
            app.after_cancel(job)
        app.quit_app()
        logging.shutdown()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=("top", "group", "menu", "menu-add", "replace", "outside", "blur"))
    a = p.parse_args()
    if a.case:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        with tempfile.TemporaryDirectory(prefix="shiguang-click-test-") as data:
            os.environ["SHIGUANG_DATA_DIR"] = data
            result = run_case(a.case)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    results = []
    for case in ("top", "group", "menu", "menu-add", "replace", "outside", "blur"):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case],
            capture_output=True, text=True, encoding="utf-8", timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            print(result.stdout + result.stderr)
            return 1
        results.append(json.loads(result.stdout.strip().splitlines()[-1]))
    out = ROOT / "tools/preview/popup-events.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
