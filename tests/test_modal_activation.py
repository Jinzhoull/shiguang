"""Manual Windows regression: reactivate the app while a modal dialog is open.

Run with the project Python and ``PYTHONPATH`` set to the project/dependencies.
The helper window belongs to a second process, so this exercises a real
application switch rather than merely changing focus among Tk widgets.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SHIGUANG_NO_ACTIVATE"] = "1"
os.environ["SHIGUANG_NO_HINT"] = "1"

from tools.screenshot import _above_in_window_stack  # noqa: E402


def main() -> int:
    if os.name != "nt":
        print("Windows-only integration test")
        return 0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)

    from shiguang.app import ShiguangApp
    from shiguang.ui.dialogs import ConfirmDialog, TaskDialog
    from shiguang.ui.due_picker import DuePickerDialog

    with tempfile.TemporaryDirectory(prefix="shiguang-modal-test-") as data:
        os.environ["SHIGUANG_DATA_DIR"] = data
        app = ShiguangApp()
        app.store.set_setting("onboarded", True)
        app.report_callback_exception = lambda *a, **k: None
        helper = None

        def pump(seconds: float) -> None:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.update()
                time.sleep(0.02)

        try:
            pump(0.8)
            app._force_foreground()
            pump(0.1)
            root_hwnd = user32.GetAncestor(app.winfo_id(), 2)
            title = f"Shiguang modal switch probe {os.getpid()}"
            child_code = (
                "import tkinter as tk; root=tk.Tk(); "
                f"root.title({title!r}); root.geometry('270x120+50+50'); "
                "root.update(); root.mainloop()"
            )
            helper = subprocess.Popen([sys.executable, "-c", child_code])
            hwnd = 0
            for _ in range(100):
                pump(0.03)
                hwnd = user32.FindWindowW(None, title)
                if hwnd:
                    break
            if not hwnd:
                raise AssertionError("helper window did not open")
            app._force_foreground()
            pump(0.15)

            def check_switch(name, dialog, owner) -> None:
                pump(0.4)
                dialog_hwnd = user32.GetAncestor(dialog.winfo_id(), 2)
                owner_hwnd = user32.GetAncestor(owner.winfo_id(), 2)
                native_owner = int(user32.GetWindow(dialog_hwnd, 4) or 0)
                if native_owner != owner_hwnd:
                    raise AssertionError(f"{name}: native owner not set")
                if not _above_in_window_stack(dialog, owner):
                    raise AssertionError(f"{name}: dialog initially behind owner")
                user32.SetForegroundWindow(hwnd)
                pump(0.35)
                if user32.GetForegroundWindow() != hwnd:
                    raise AssertionError(f"{name}: could not switch to other app")
                # Reactivate the main HWND, as happens on taskbar return.
                # Tk's overrideredirect dialog cannot be selected in Alt+Tab.
                app._force_foreground()
                pump(0.55)
                front = user32.GetForegroundWindow()
                grab = app.grab_current()
                above_root = _above_in_window_stack(dialog, app)
                above_owner = _above_in_window_stack(dialog, owner)
                print(f"{name}: foreground={front:x} root={root_hwnd:x} "
                      f"dialog={dialog_hwnd:x} owner={native_owner:x} "
                      f"above_root={above_root} above_owner={above_owner} "
                      f"grab={grab}")
                if front not in (root_hwnd, dialog_hwnd):
                    raise AssertionError(f"{name}: app did not reactivate")
                if not above_root or not above_owner or grab is None \
                        or grab.winfo_toplevel() != dialog:
                    raise AssertionError(f"{name}: modal hidden behind its owner")

            picker = DuePickerDialog(app, on_save=lambda *_: None)
            check_switch("direct due picker", picker, app)
            picker._finish()
            pump(0.2)

            task = TaskDialog(app)
            check_switch("task editor", task, app)
            nested = DuePickerDialog(app, on_save=lambda *_: None, owner=task)
            check_switch("nested due picker", nested, task)
            nested._finish()
            pump(0.25)
            if app.grab_current() != task:
                raise AssertionError("nested dialog did not restore parent grab")
            task._finish()
            pump(0.2)

            confirm = ConfirmDialog(app, "删除任务", "确定删除吗？", lambda: None)
            check_switch("confirmation", confirm, app)
            confirm._finish()
            pump(0.2)

            print("PASS modal windows remain usable after switching applications")
            return 0
        finally:
            if helper is not None:
                helper.terminate()
                helper.wait(timeout=5)
            try:
                app.quit_app()
            except Exception:
                app.destroy()
            logging.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
