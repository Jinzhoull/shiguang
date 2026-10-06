# -*- coding: utf-8 -*-
"""Native clipping for borderless rounded popup windows.

Canvas/CTk rounded cards only round their painted surface.  A top-level window
still owns a rectangular native surface, which can leak its background through
the four corners.  On Windows, a window region clips that surface itself so
the surrounding application shows through instead of a square patch.
"""

from __future__ import annotations

import sys
import tkinter as tk


def attach_native_owner(window: tk.Misc, owner: tk.Misc) -> bool:
    """Keep a borderless modal above its owner through Windows activation.

    Tk's ``transient`` does not assign a Win32 owner to an
    ``overrideredirect`` popup. Without that relationship, reactivating the
    main window can put it above a grabbed dialog and lock out the entire UI.
    Set the actual outer HWND's owner after the popup has been mapped.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetWindow.restype = wintypes.HWND
        user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int,
                                             wintypes.HWND)
        user32.SetWindowLongPtrW.restype = wintypes.HWND
        popup_hwnd = user32.GetAncestor(window.winfo_id(), 2)
        owner_hwnd = user32.GetAncestor(owner.winfo_id(), 2)
        if not popup_hwnd or not owner_hwnd or popup_hwnd == owner_hwnd:
            return False
        if user32.GetWindow(popup_hwnd, 4) != owner_hwnd:  # GW_OWNER
            user32.SetWindowLongPtrW(popup_hwnd, -8, owner_hwnd)  # GWLP_HWNDPARENT
        return user32.GetWindow(popup_hwnd, 4) == owner_hwnd
    except Exception:  # noqa: BLE001 - nonstandard Windows/Tk builds
        return False


def apply_rounded_region(window: tk.Misc, radius: int,
                         native_root: bool = True, size=None) -> bool:
    """Clip a mapped popup or child panel to a rounded rectangle.

    ``radius`` and the measured dimensions are physical pixels.  The region is
    recalculated by callers after each new geometry is applied. ``native_root``
    selects the outer popup HWND; false clips an in-app child panel itself.
    Other window systems keep their existing Canvas/CTk rounded rendering.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        width = max(1, int(size[0] if size is not None else window.winfo_width()))
        height = max(1, int(size[1] if size is not None else window.winfo_height()))
        radius = max(1, min(int(radius), width // 2, height // 2))

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        gdi32.CreateRoundRectRgn.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL

        # Tk's identifier can refer to its child/client HWND; GA_ROOT yields
        # the actual popup window whose rectangular surface must be clipped.
        hwnd = wintypes.HWND(window.winfo_id())
        if native_root:
            hwnd = user32.GetAncestor(hwnd, 2) or hwnd
        diameter = radius * 2
        region = gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, diameter, diameter)
        if not region:
            return False
        if not user32.SetWindowRgn(hwnd, region, True):
            gdi32.DeleteObject(region)
            return False
        # SetWindowRgn owns the HRGN after success.
        return True
    except Exception:  # noqa: BLE001 - unsupported/older Windows builds
        return False


def apply_particle_region(window: tk.Misc, bounds) -> bool:
    """Clip a particle layer to its live dots; an empty list hides all pixels.

    The native window contains only the circles, so no color key or opaque
    rectangular overlay is needed. SetWindowRgn takes ownership of the result.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    import math
    from ctypes import wintypes

    region = None
    try:
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
        gdi32.CreateRectRgn.argtypes = (ctypes.c_int,) * 4
        gdi32.CreateRectRgn.restype = wintypes.HRGN
        gdi32.CreateEllipticRgn.argtypes = (ctypes.c_int,) * 4
        gdi32.CreateEllipticRgn.restype = wintypes.HRGN
        gdi32.CombineRgn.argtypes = (wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int)
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        region = gdi32.CreateRectRgn(0, 0, 0, 0)
        if not region:
            return False
        for left, top, right, bottom in bounds:
            dot = gdi32.CreateEllipticRgn(math.floor(left), math.floor(top),
                                        math.ceil(right) + 1, math.ceil(bottom) + 1)
            if dot:
                gdi32.CombineRgn(region, region, dot, 2)  # RGN_OR
                gdi32.DeleteObject(dot)
        hwnd = user32.GetAncestor(window.winfo_id(), 2)
        if hwnd and user32.SetWindowRgn(hwnd, region, True):
            region = None
            return True
        return False
    except Exception:  # noqa: BLE001
        return False
    finally:
        if region:
            ctypes.windll.gdi32.DeleteObject(region)


def schedule_rounded_region(window: tk.Misc, radius: int,
                            native_root: bool = True) -> None:
    """Clip mapped popups now and keep the region in sync with their size.

    An ``after_idle`` callback can remain queued throughout a CTk modal's
    lifetime on Windows, leaving a rectangular native background around its
    painted rounded card. A normal timer and a size watcher avoid that race.
    """
    if sys.platform != "win32":
        return
    try:
        state = getattr(window, "_shiguang_round_region", None)
        if state is None:
            state = {"radius": radius, "native_root": native_root,
                     "size": None, "job": None}
            setattr(window, "_shiguang_round_region", state)

            def on_configure(event) -> None:
                if event.widget is not window:
                    return
                size = (int(event.width), int(event.height), state["radius"],
                        state["native_root"])
                if size == state["size"] or state["job"] is not None:
                    return
                try:
                    state["job"] = window.after(20, apply)
                except Exception:  # noqa: BLE001
                    pass

            window.bind("<Configure>", on_configure, add="+")
        else:
            state["radius"] = radius
            state["native_root"] = native_root

        def apply() -> None:
            state["job"] = None
            try:
                if not window.winfo_exists() or not window.winfo_viewable():
                    return
                width, height = window.winfo_width(), window.winfo_height()
                if width <= 2 or height <= 2:
                    return
                size = (width, height, state["radius"], state["native_root"])
                if size != state["size"] and apply_rounded_region(
                        window, state["radius"], state["native_root"]):
                    state["size"] = size
            except Exception:  # noqa: BLE001 - popup may close during callback
                pass

        apply()
        if state["job"] is not None:
            window.after_cancel(state["job"])
        state["job"] = window.after(50, apply)
    except Exception:  # noqa: BLE001
        pass


def paint_round_corners(window: tk.Misc, radius: int, border_width: int,
                        fill: str, border: str) -> None:
    """以同一张超采样位图覆盖四角，匹配直边的物理描边宽度。

    Windows 区域只负责裁掉窗外像素；CTk 的内层圆角在不同 DPI 下会
    把弧线画得比直边厚。四角使用相同的 PIL 曲线，避免四套光栅化规则。
    """
    try:
        from PIL import Image, ImageTk
        from .widgets import aa_round_rect

        width, height = window.winfo_width(), window.winfo_height()
        radius = max(1, min(int(radius), width // 2, height // 2))
        side = min(radius + max(2, border_width) + 2, width // 2, height // 2)
        if side <= 2:
            return
        atlas_size = side * 2
        picture = aa_round_rect((atlas_size, atlas_size), radius, fill,
                                border=border, border_w=border_width)
        underlay = Image.new("RGBA", (atlas_size, atlas_size), border)
        underlay.alpha_composite(picture)
        for label in getattr(window, "_round_corner_labels", ()):
            label.destroy()
        labels = []
        photos = []
        positions = ((0, 0, 0, 0),
                     (width - side, 0, side, 0),
                     (0, height - side, 0, side),
                     (width - side, height - side, side, side))
        for x, y, crop_x, crop_y in positions:
            photo = ImageTk.PhotoImage(underlay.crop(
                (crop_x, crop_y, crop_x + side, crop_y + side)))
            label = tk.Label(window, image=photo, bg=border, bd=0,
                             highlightthickness=0)
            label.place(x=x, y=y, width=side, height=side)
            label.lift()
            photos.append(photo)
            labels.append(label)
        window._round_corner_labels = labels
        window._round_corner_photos = photos
    except Exception:  # noqa: BLE001 - optional decoration must not block a dialog
        pass
