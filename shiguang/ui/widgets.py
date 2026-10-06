# -*- coding: utf-8 -*-
"""可复用 UI 组件：任务状态图标、柔和柱状图、空状态插画、浮层提示等。

Canvas 手工绘制的通用注意事项
----------------------------
* tkinter 的 Canvas **没有透明度**，"淡出"一律用 ``theme.mix()`` 向背景色插值模拟；
* Canvas 的背景色必须是固定的单色（不能用 CTk 的 (浅,深) 元组），
  所以这些组件的构造函数都要求传入 ``bg``，且主题切换时由外层重建控件。
"""

from __future__ import annotations

import math
import re
import sys
import tkinter as tk
import weakref
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk

from .. import icons, theme
from .window_shape import apply_rounded_region


# --------------------------------------------------------------------------
# 绘图小工具
# --------------------------------------------------------------------------
def round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
               radius: float, **kwargs) -> List[int]:
    """在 Canvas 上画圆角矩形（两个矩形 + 四个圆拼合），返回 item id 列表。"""
    r = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    items: List[int] = []
    items.append(canvas.create_rectangle(x1 + r, y1, x2 - r, y2, **kwargs))
    items.append(canvas.create_rectangle(x1, y1 + r, x2, y2 - r, **kwargs))
    for cx, cy in ((x1 + r, y1 + r), (x2 - r, y1 + r), (x1 + r, y2 - r), (x2 - r, y2 - r)):
        items.append(canvas.create_oval(cx - r, cy - r, cx + r, cy + r, **kwargs))
    return items


def lerp_color(a: str, b: str, t: float) -> str:
    return theme.mix(a, b, t)


def bind_hover(widget: tk.Misc, on_enter: Callable[[], None],
               on_leave: Callable[[], None], depth: int = 0) -> None:
    """给控件及其子控件递归绑定悬停事件（子控件会截获事件，必须递归绑）。"""
    widget.bind("<Enter>", lambda _e: on_enter(), add="+")
    widget.bind("<Leave>", lambda _e: on_leave(), add="+")
    if depth < 3:
        for child in widget.winfo_children():
            bind_hover(child, on_enter, on_leave, depth + 1)


def clear(parent: tk.Misc) -> None:
    """销毁全部子控件（重建页面用）。"""
    for child in list(parent.winfo_children()):
        child.destroy()


# --------------------------------------------------------------------------
# 抗锯齿文字 / 抗锯齿圆角（PIL 超采样）
# --------------------------------------------------------------------------
# 为什么需要这两件工具
# --------------------
# Tk 的 Canvas 文字走 GDI，小字号下默认按**亚像素（ClearType）**渲染：放大看
# 笔画左右两侧是橙/青色的"彩边"，笔画本身也没有灰度过渡；CTk 控件的圆角底衬
# 又是 ``create_polygon`` 拼的，边缘是**硬阶梯**。用户看到的就是
# "逾期标签和旁边的日期文字模糊、有锯齿"。
#
# 解法与图标一致：PIL **4× 超采样 → LANCZOS 缩小 → 贴 Canvas**。
# PIL/FreeType 走灰度抗锯齿，没有彩边；缩小的过程把阶梯磨成连续过渡。
_AA_SS = theme.BITMAP_SS                      # 超采样倍率


_AA_CACHE: dict = {}

def _aa_text_image(text: str, role: str, color: str, pill: Optional[str] = None,
                   pill_radius: int = 0, pad_x: int = 0, pad_y: int = 0,
                   strike: bool = False, min_h: int = 0, box_h: int = 0):
    """渲染一行文字为 RGBA 位图（**逻辑像素**尺寸）；取不到字体返回 ``None``。

    版式约定
    --------
    * 画布高度 = ``max(内容高, min_h, box_h)``，内容**垂直居中**。给 ``box_h``
      是为了让同一行里的几个标签高度一致 —— 高度不同的标签被 ``pack`` 居中后，
      基线会差半个像素，看着就是"没对齐"。
    * 基线用 ``(ascent + descent)`` 的**参考行框**定位，不用当前文本的墨迹框：
      "09/25"（无下伸部）与"今天"（无下伸部）换字时基线会跳。
    * ``pin_y`` 用 ``anchor="ls"``（左基线）落笔，字号是**像素**（已经在
      :func:`fonts.pil_font` 里乘过缩放与超采样）。
    """
    from .. import fonts

    font = fonts.pil_font(role, supersample=_AA_SS)
    if font is None:
        return None

    from PIL import Image, ImageDraw

    ss = _AA_SS
    ascent, descent = font.getmetrics()
    line_ss = max(1, ascent + descent)
    try:
        text_w_ss = float(font.getlength(text))
    except Exception:  # noqa: BLE001
        text_w_ss = float(font.getbbox(text)[2])

    content_w = int(math.ceil(text_w_ss / ss)) + max(0, int(pad_x)) * 2
    content_h = int(math.ceil(line_ss / ss)) + max(0, int(pad_y)) * 2
    w = max(1, content_w)
    h = max(1, content_h, int(min_h), int(box_h))

    big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    top = (h - content_h) / 2.0                       # 内容块在画布里的上边
    if pill:
        radius = max(0, int(pill_radius)) * ss
        draw.rounded_rectangle(
            [0, int(round(top * ss)), w * ss - 1, int(round((top + content_h) * ss)) - 1],
            radius=radius, fill=pill)
    pen_y = (top + max(0, int(pad_y))) * ss + ascent
    draw.text((max(0, int(pad_x)) * ss, pen_y), text, font=font, fill=color,
              anchor="ls")
    if strike:
        y = pen_y - line_ss * 0.30
        draw.line([0, y, w * ss, y], fill=color, width=max(1, round(ss * 0.9)))
    return big.resize((w, h), Image.LANCZOS)


class AAText(tk.Canvas):
    """一行**抗锯齿**文字（可选圆角底衬 / 删除线）。接口与 ``CTkLabel`` 近似。

    与 ``TextLabel`` 的区别：渲染走 PIL（见 :func:`_aa_text_image`），
    所以圆角底衬和笔画边缘都是干净的；代价是每次换字要重画一张小位图
    （12px 的一行只要 0.2ms 量级，卡片重绘频率极低，可以忽略）。

    降级：字体解析不出来时（PIL 缺失 / 系统字体对不上）退回 **Tk 自绘文字**，
    版式一致、只是没有抗锯齿 —— 不能因为字体探测失败就不显示日期。
    """

    def __init__(self, master: tk.Misc, bg: str = theme.LIGHT["card"], cursor: str = "",
                 **kwargs) -> None:
        super().__init__(master, width=1, height=1, bg=bg,
                         highlightthickness=0, bd=0, **kwargs)
        self._bg = bg
        self._photo = None
        self._spec = None
        self._fallback = None
        if cursor:
            self.configure(cursor=cursor)

    # ------------------------------------------------------------------
    def set_text(self, text: str, role: str = "meta", color: str = theme.LIGHT["text"],
                 pill: Optional[str] = None, pill_radius: int = 0,
                 pad_x: int = 0, pad_y: int = 0, strike: bool = False,
                 min_h: int = 0, min_w: int = 0, box_h: int = 0) -> None:
        """换文字/配色。同参数重复调用是幂等的（会直接命中缓存）。"""
        spec = (text, role, color, pill, int(pill_radius), int(pad_x),
                int(pad_y), bool(strike), int(min_h), int(min_w), int(box_h),
                self._bg)
        if spec == self._spec:
            return
        self._spec = spec
        self._fallback = None

        image = None
        key = (text, role, color, pill, int(pill_radius), int(pad_x), int(pad_y),
               bool(strike), int(min_h), int(box_h))
        if text:
            image = _AA_CACHE.get(key)
            if image is None:
                try:
                    image = _aa_text_image(text, role, color, pill, pill_radius,
                                           pad_x, pad_y, strike, min_h, box_h)
                except Exception:  # noqa: BLE001
                    image = None
                if image is not None:
                    _AA_CACHE[key] = image

        self.delete("all")
        if image is None:
            self._draw_fallback(text, role, color, min_h, min_w, box_h, pill)
            return
        from PIL import ImageTk

        self._photo = ImageTk.PhotoImage(image)
        self.configure(width=image.width, height=image.height)
        self.create_image(0, 0, anchor="nw", image=self._photo)

    # ------------------------------------------------------------------
    def _draw_fallback(self, text, role, color, min_h, min_w, box_h,
                       pill) -> None:
        """字体拿不到时的降级：Tk 自绘文字（版式一致、无抗锯齿）。

        ⚠️ 这里的量宽必须走 :func:`fonts.measure`（与控件真正在用的字体同源）。
        直接拿 ``tkfont.Font(size=<正数>)`` 量会得到两倍宽 —— 这个坑踩过两轮。
        """
        from .. import fonts

        h = max(1, int(min_h), int(box_h) or theme.TASK_META_H)
        if not text:
            self.configure(width=max(1, int(min_w)), height=h)
            return
        try:
            fam, size, weight = fonts.tkfont_spec(role)
        except Exception:  # noqa: BLE001
            fam, size, weight = ("TkDefaultFont", -12, "normal")
        w = max(1, int(min_w), int(fonts.measure(role, text)) + h)
        self.configure(width=w, height=h)
        if pill:
            round_rect(self, 0, 0, w, h, int(pill_radius), fill=pill, outline="")
        self.create_text(w / 2, h / 2, text=text, fill=color,
                         font=(fam, size, weight))

    # ------------------------------------------------------------------
    def set_bg(self, bg: str) -> None:
        """同步画布底色（跟随卡片底色变化）。"""
        if bg == self._bg:
            return
        self._bg = bg
        try:
            self.configure(bg=bg)
        except Exception:  # noqa: BLE001
            pass


def aa_round_rect(size: Tuple[int, int], radius: int, fill: str,
                  border: Optional[str] = None, border_w: int = 0,
                  cache: bool = True):
    """整块圆角矩形（可选描边）的 RGBA 位图，逻辑像素尺寸，内部 4× 超采样。

    ❌ 为什么不用 ``round_rect``（两个矩形 + 四个圆拼合）
    --------------------------------------------------
    两个毛病叠在一起：
    1. **不做抗锯齿** —— 圆角是硬阶梯；
    2. 给拼合体加 ``outline`` 时，**六条边每一条都会被描出来**（内部接线、
       两条竖线、两条横线都在），而且最后画上去的椭圆填充会把下半部分的
       描边盖掉 —— 现象正是"浮层底部边框整条消失"。
    所以描边由同一张 PIL 超采样位图绘制外沿和内沿；菜单、图标选择和
    快速新建浮层也共用这条绘制路径。
    """
    from PIL import Image, ImageDraw

    w = max(1, int(size[0]))
    h = max(1, int(size[1]))
    r = max(0, int(radius))
    bw = max(0, int(border_w))
    key = ("rr", w, h, r, fill, border, bw)
    if cache:
        cached = _AA_CACHE.get(key)
        if cached is not None:
            return cached
    ss = _AA_SS
    big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    if border and bw > 0:
        draw.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1],
                               radius=r * ss, fill=border)
        inset = max(1, int(round(bw * ss)))
        draw.rounded_rectangle([inset, inset, w * ss - 1 - inset, h * ss - 1 - inset],
                               radius=max(0, r * ss - inset), fill=fill)
    else:
        draw.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1],
                               radius=r * ss, fill=fill)
    img = big.resize((w, h), Image.LANCZOS)
    if cache:
        _AA_CACHE[key] = img
    return img


def tooltip_image(text: str, max_width: Optional[int] = None,
                  max_height: Optional[int] = None):
    """悬停提示整块位图：米白圆角卡 + 柔橘细描边 + 深灰棕文字。

    为什么整块出图（1.5.29）
    ------------------------
    提示是**独立 Toplevel** 里的悬浮元素，硬边特别显眼：
    * ``create_text`` 走 GDI 的 ClearType，小字号带橙/青描边；
    * Canvas 图元不做抗锯齿，圆角是硬阶梯；拼合体加 ``outline`` 还会六条边全描。
    所以圆角、描边、文字全部走 PIL 4× 超采样 → LANCZOS，渲染路径只有
    一条（与 ``DueBanner`` 同一套做法）。透明外沿不绘制阴影或矩形底框。

    返回 ``(PIL.Image, (物理宽, 物理高))``；字体层不可用时返回 ``None``，
    调用方用无高亮 Canvas 降级绘制。
    """
    from PIL import Image, ImageColor, ImageDraw

    from .. import fonts as _fonts

    ss = _AA_SS
    font = _fonts.pil_font("tiny", 0, supersample=ss)
    if font is None:
        return None
    asc, desc = font.getmetrics()
    pad_x = theme.lpx(theme.TOOLTIP_PAD_X) * ss
    pad_y = theme.lpx(theme.TOOLTIP_PAD_Y) * ss
    width_limit = max(1, int(max_width) * ss) if max_width is not None else None
    text_limit = (max(1, width_limit - pad_x * 2)
                  if width_limit is not None else None)

    # 长标题按字形宽度折行，保证整张提示卡能放进主窗口。
    lines: List[str] = []
    for paragraph in str(text).split("\n") or [""]:
        current = ""
        for char in paragraph:
            candidate = current + char
            if (current and text_limit is not None
                    and font.getlength(candidate) > text_limit):
                lines.append(current)
                current = char
            else:
                current = candidate
        lines.append(current)

    max_line_w = max((font.getlength(line) for line in lines), default=0)
    min_w = theme.lpx(theme.TOOLTIP_MIN_W) * ss
    w = max(min_w, int(math.ceil(max_line_w)) + pad_x * 2)
    if width_limit is not None:
        w = min(w, width_limit)
    line_h = asc + desc
    h = line_h * max(1, len(lines)) + pad_y * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    radius = min(theme.lpx(theme.TOOLTIP_RADIUS) * ss, (h - 1) // 2, (w - 1) // 2)

    if max_height is not None:
        max_lines = max(1, (int(max_height) * ss - pad_y * 2) // line_h)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and font.getlength(last + "…") > (text_limit or w):
                last = last[:-1]
            lines[-1] = last + "…"
            h = line_h * len(lines) + pad_y * 2
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img, "RGBA")
    # 位图尺寸就是卡片尺寸；不擦除最外圈，保留连续细描边。
    # Canvas 在 native region 内显示这张图，不需要颜色键或投影边距。
    bw = max(1, theme.lpx(theme.TOOLTIP_BORDER_W) * ss)
    draw.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=radius,
        fill=ImageColor.getrgb(theme.c("tooltip_bg")) + (255,),
        outline=ImageColor.getrgb(theme.c("tooltip_border")) + (255,),
        width=bw)

    # 3) 文字：逐行左对齐，基线定位（baseline 之上再留 pad_y）
    text_color = ImageColor.getrgb(theme.c("tooltip_text")) + (255,)
    for index, line in enumerate(lines):
        draw.text((pad_x, pad_y + asc + index * line_h), line,
                  font=font, anchor="ls", fill=text_color)

    lw, lh = max(1, math.ceil(w / ss)), max(1, math.ceil(h / ss))
    small = img.resize((lw, lh), Image.LANCZOS)
    return small, (lw, lh)


def toast_image(message: str, max_width: int, celebration: bool = False,
                action_label: str = ""):
    """Toast 的完整客户区位图；返回图、物理尺寸和可选动作点击区。"""
    from PIL import Image, ImageDraw
    from .. import fonts

    ss = theme.BITMAP_SS
    font = fonts.pil_font("small", supersample=ss)
    action_font = fonts.pil_font("tiny", supersample=ss)
    if font is None or action_font is None:
        raise RuntimeError("提示字体不可用")
    px, py = theme.lpx(theme.TOAST_PAD_X), theme.lpx(theme.TOAST_PAD_Y)
    gap = theme.lpx(theme.TOAST_GAP)
    icon = theme.lpx(theme.TOAST_ICON) if celebration and not action_label else 0
    action_w = max(theme.lpx(theme.TOAST_ACTION_W),
                   math.ceil(action_font.getlength(action_label) / ss) + px) if action_label else 0
    extra = (action_w + gap) if action_label else 2 * (icon + gap) if icon else 0
    available = max(ss, (max_width - px * 2 - extra) * ss)
    lines = []
    for paragraph in str(message).split("\n"):
        line = ""
        for char in paragraph:
            if line and font.getlength(line + char) > available:
                lines.append(line)
                line = char
            else:
                line += char
        lines.append(line)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    if len(lines) > theme.TOAST_MAX_LINES:
        lines = lines[:theme.TOAST_MAX_LINES]
        while lines[-1] and font.getlength(lines[-1] + "…") > available:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    text_w = math.ceil(max((font.getlength(line) for line in lines), default=0) / ss)
    width = min(max_width, max(1, px * 2 + text_w + extra))
    action_h = theme.lpx(theme.TOAST_ACTION_H) if action_label else 0
    height = py * 2 + max(math.ceil(line_h * len(lines) / ss), icon, action_h)
    radius = min(theme.lpx(theme.TOAST_RADIUS), width // 2, height // 2)
    border = max(1, theme.lpx(theme.TOAST_BORDER_W))
    big = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle((0, 0, width * ss - 1, height * ss - 1),
                           radius=radius * ss, fill=theme.c("toast_border"))
    inset = border * ss
    draw.rounded_rectangle((inset, inset, width * ss - 1 - inset, height * ss - 1 - inset),
                           radius=max(0, (radius - border) * ss), fill=theme.c("toast_bg"))
    text_left = px + (icon + gap if icon else 0)
    text_right = width - px - (icon + gap if icon else action_w + gap if action_label else 0)
    text_y = (height * ss - line_h * len(lines)) / 2 + asc
    for index, line in enumerate(lines):
        draw.text(((text_left + text_right) * ss / 2, text_y + index * line_h), line,
                  font=font, fill=theme.c("toast_text"), anchor="ms")
    if icon:
        sparkle = icons.get_pil("toast_sparkle", icon * ss)
        if sparkle is not None:
            big.paste(sparkle, (px * ss, (height * ss - sparkle.height) // 2), sparkle)
    action_box = None
    if action_label:
        left, top = width - px - action_w, (height - action_h) // 2
        action_box = (left, top, left + action_w, top + action_h)
        draw.rounded_rectangle(tuple(v * ss for v in action_box),
                               radius=action_h * ss / 2, fill=theme.c("accent_soft"))
        draw.text(((left + action_w / 2) * ss, height * ss / 2), action_label,
                  font=action_font, fill=theme.c("text"), anchor="mm")
    return big.resize((width, height), Image.LANCZOS), (width, height), action_box


def aa_corner(radius: int, inside: str, outside: str, pad: int = 2):
    """生成**左上角**的抗锯齿圆角补丁（``(radius+pad)`` 见方，RGBA）。

    用途：CTk 控件自己的圆角底衬是 ``create_polygon`` 拼的、**不做抗锯齿**，
    在大圆角（提醒条 r=14）上会露出硬阶梯 —— 用户报的"横幅右下角有杂散像素点"。
    做法是在四个角各压一张同尺寸的补丁：圆角外涂页面色、圆角内涂横幅色，
    与控件自身的圆角几何完全重合，只是边缘换成了连续过渡。

    其余三个角用 ``Image.transpose()`` 翻转即可。
    """
    from PIL import Image, ImageDraw

    ss = _AA_SS
    size = max(2, int(radius) + max(1, int(pad)))
    big = Image.new("RGBA", (size * ss, size * ss), outside)
    draw = ImageDraw.Draw(big)
    # 画一个 2× 大的圆角矩形，只让它的左上角落在补丁里 —— 另外三个圆角
    # 被补丁边界裁掉，不会污染补丁的其它角。
    draw.rounded_rectangle([0, 0, size * 2 * ss - 1, size * 2 * ss - 1],
                           radius=max(1, int(radius)) * ss, fill=inside)
    return big.resize((size, size), Image.LANCZOS)


def aa_corners(radius: int, inside: str, outside: str,
               pad: int = 2) -> List[object]:
    """四角补丁，顺序 = 左上 / 右上 / 左下 / 右下（见 :func:`aa_corner`）。"""
    from PIL import Image

    base = aa_corner(radius, inside, outside, pad)
    return [base,
            base.transpose(Image.FLIP_LEFT_RIGHT),
            base.transpose(Image.FLIP_TOP_BOTTOM),
            base.transpose(Image.ROTATE_180)]


# --------------------------------------------------------------------------
# "正在被拖拽缩放"的全局标志（需求 ② / 13）
# --------------------------------------------------------------------------
# 这个标志控制实时缩放期间的昂贵位图重绘：主窗口逐帧改变尺寸，
# 药丸投影、渐变图等昂贵重绘留到手势结束后统一补做。
# 谁在缩放由 app 通过 set_resizing() 告知（只有 app 知道缩放手势的起止）。
_RESIZING = False


def set_resizing(value: bool) -> None:
    """由 app 在缩放/拖动开始时调用，结束时调用 False。"""
    global _RESIZING
    _RESIZING = bool(value)


def is_resizing() -> bool:
    return _RESIZING


def redraw_all(root: tk.Misc, depth: int = 0) -> int:
    """递归重画树里所有 :class:`AutoRedrawCanvas`（缩放手势结束后调用）。

    为什么需要一次"兜底全量重绘"：各 Canvas 自己有一套 60ms 的 ``<Configure>``
    防抖，正常够用；但缩放窗口时最后那一帧的 Configure 有可能被后面的
    布局变化吃掉，留下"内容停在旧尺寸"的残影。手势结束后统一扫一遍最省心 ——
    只在拖拽结束时跑一次，代价可以忽略。

    返回重画的控件个数（测试用得上：断言"确实扫到了画布"）。
    """
    count = 0
    if isinstance(root, AutoRedrawCanvas):
        try:
            root._auto_redraw()
            count += 1
        except Exception:  # noqa: BLE001
            pass
    if depth > 12:
        return count
    try:
        children = list(root.winfo_children())
    except Exception:  # noqa: BLE001
        return count
    for child in children:
        count += redraw_all(child, depth + 1)
    return count


class ResizeGate:
    """全局"拖拽缩放中"判定与一次性重绘调度（1.5.17）。

    背景
    ----
    连续的 ``<Configure>`` 可能让各控件的延迟重绘在手势间隙反复执行。
    这个门把连续事件归为一次交互，并在安静下来后统一重绘：

    * 相邻两次 ``<Configure>`` 间隔 < ``theme.RESIZE_DRAG_TICK_MS``（50ms）
      → 判定"拖拽中"：注册的 drag 回调被调用，settle 重绘不再排队；
    * 事件流安静超过 ``theme.RESIZE_QUIET_MS``（100ms）→ 判定"松手"：
      一次性执行全部注册的 settle 回调（完整重排/重建位图）。

    用法
    ----
    控件在 ``__init__`` 里 ``RESIZE_GATE.register(self, self._settle_redraw)``
    （可选第三个参数 = 拖拽中的轻量回调）；``<Configure>`` 回调里先
    ``if RESIZE_GATE.configure_event(self): return``，拖拽中就什么都不做。
    settle 回调执行时自动跳过已销毁的控件（防泄漏）。
    """

    TICK_MS = theme.RESIZE_DRAG_TICK_MS
    QUIET_MS = theme.RESIZE_QUIET_MS

    def __init__(self) -> None:
        self._last = 0.0                     # 上次 <Configure> 的 monotonic 秒
        self._quiet_job: Optional[str] = None
        self._quiet_owner: Optional[tk.Misc] = None   # job 注册在谁身上
        self.dragging = False
        self._entries: List[tuple] = []      # (widget, settle_cb, drag_cb|None)

    # -- 注册 ----------------------------------------------------------
    def register(self, widget: tk.Misc, on_settle: Callable[[], None],
                 on_drag: Optional[Callable[[], None]] = None) -> None:
        self._entries.append((widget, on_settle, on_drag))

    # -- 每次事件 ------------------------------------------------------
    def configure_event(self, widget: tk.Misc) -> bool:
        """喂入一次 ``<Configure>``。返回 True = 拖拽中（调用方跳过重活）。"""
        import time as _time

        now = _time.monotonic()
        gap_ms = (now - self._last) * 1000.0
        self._last = now
        if not self.dragging and 0.0 < gap_ms < self.TICK_MS:
            self.dragging = True
        if self.dragging:
            for w, _sc, dc in self._entries:
                if dc is None:
                    continue
                try:
                    if w.winfo_exists():
                        dc()
                except Exception:  # noqa: BLE001
                    pass
        # 无论是否拖拽，都重排"松手"定时器。
        # ⚠️ 必须统一挂在**主窗口**（winfo_toplevel）上：tkinter 的
        # ``after_cancel`` 会在"调用者身上" deletecommand —— 若 A 控件注册的
        # job 被 B 控件取消，A 销毁时会二次删除同一命令，抛
        # ``TclError: can't delete Tcl command``（本轮真踩过）。挂主窗口后
        # 注册/取消永远同一对象，且主窗口寿命 = 应用寿命，不会中途销毁。
        if self._quiet_job is not None and self._quiet_owner is not None:
            try:
                self._quiet_owner.after_cancel(self._quiet_job)
            except Exception:  # noqa: BLE001
                pass
        try:
            owner = widget.winfo_toplevel()
            self._quiet_job = owner.after(self.QUIET_MS, self._settle)
            self._quiet_owner = owner
        except Exception:  # noqa: BLE001
            self._quiet_job = None
            self._quiet_owner = None
        return self.dragging

    # -- 松手 ----------------------------------------------------------
    def _settle(self) -> None:
        self._quiet_job = None
        self.dragging = False
        alive: List[tuple] = []
        for entry in self._entries:
            w, sc, dc = entry
            try:
                if not w.winfo_exists():
                    continue        # 已销毁：顺手清出注册表，防泄漏
            except Exception:  # noqa: BLE001
                continue
            alive.append(entry)
            try:
                sc()
            except Exception:  # noqa: BLE001
                pass
        self._entries = alive


#: 模块级单例：全应用共享同一个"拖拽中"状态。
RESIZE_GATE = ResizeGate()


class AutoRedrawCanvas(tk.Canvas):
    """裸 ``tk.Canvas`` 的"跟着尺寸重画"基类（需求 ②：缩放不许有残影）。

    为什么需要它
    ------------
    Canvas 里的东西是**画**上去的。窗口被拉大时，Tk 只会改画布自身的尺寸，
    **不会**重画里面的内容 —— 于是：

    * 进度条的填充段还是按旧宽度画的 → 右侧空一截；
    * 柱状图的柱子按旧宽度排 → 整体被挤到左边或裁掉；
    * 插画的太阳停在旧坐标 → 视觉上偏到一边。

    同一个坑在 CTk 控件上不存在（它们自己处理 Configure），所以只针对裸 Canvas。

    为什么带防抖
    ------------
    拖边框时每帧都会发一次 ``<Configure>``。对渐变柱状图这种要画几十条
    ``create_rectangle`` 的控件，逐帧重画会把主线程占满，画面反而抖成残影。
    这里统一延迟 ``REDRAW_DELAY`` 再重画一次 —— 用户停手的那一帧画面是准的，
    拖动过程中画面是"旧内容被裁切"，比"卡住"好接受得多。
    """

    REDRAW_DELAY = theme.RESIZE_DEBOUNCE_MS   # 1.5.16：统一走主题防抖口径

    def _init_auto_redraw(self) -> None:
        self._redraw_job: Optional[str] = None
        self.bind("<Configure>", self._on_auto_configure, add="+")

    def _on_auto_configure(self, event=None) -> None:
        # <Configure> 也会被子控件冒泡上来（widget 不是自己），忽略
        if event is not None and getattr(event, "widget", None) is not self:
            return
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except Exception:  # noqa: BLE001
                pass
        self._redraw_job = self.after(self.REDRAW_DELAY, self._auto_redraw)

    def _auto_redraw(self) -> None:
        self._redraw_job = None
        try:
            if not self.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        self._draw()


# 右键菜单已迁移到 ui/menu.py（自绘浮层）。这里保留一个转发，避免历史调用点失效。
def popup_menu(widget: tk.Misc, items, x: Optional[int] = None,
               y: Optional[int] = None):
    """转发到 :mod:`shiguang.ui.menu` 的自绘菜单。

    历史实现用的是原生 ``tk.Menu``，它有两个无法绕过的限制：宽度按最长项撑开、
    样式完全由系统绘制。更糟的是 ``add_command`` 会**静默**接受非字符串 label，
    本项目因此出现过菜单里显示 ``<bound method ... at 0x...>`` 的事故。
    新实现见 ``ui/menu.py`` 模块头注释。
    """
    from . import menu as menu_mod
    return menu_mod.popup_menu(widget, items, x, y)


# --------------------------------------------------------------------------
# 紧凑文字标签
# --------------------------------------------------------------------------
class TextLabel(ctk.CTkLabel):
    """紧凑排版用的文字标签：请求高度 = 文字本来的行高，不受 CTk 默认值拖累。

    为什么必须包一层
    ----------------
    ``CTkLabel`` 的 ``height`` 默认 **28 逻辑像素**，而它的**请求高度**是
    ``max(height, 内部 tk.Label 的内容高度)``。内部那个 ``tk.Label`` 是 grid 在
    画布里的，画布默认开着几何传播 —— 于是：

    * 一行 11px 的辅助文字，请求高度也是 28（实测 42 物理像素）；
    * ``height=0`` / ``height=1`` 都不会报错，但**压不到内容高度以下**；
    * ``wraplength`` 折行后内容高度成倍增长，显式给的 ``height`` 彻底失效
      （实测一行 17px 的标题把请求高度顶到 58px = 3 行）。

    在"桌面小插件"这种每一像素都要省的场景里这是灾难：一屏 8 条任务会白白
    多出 200 多像素空白（改前单张任务卡 98.7 逻辑高，改后 30）。

    这里统一把 ``height`` 归 1 —— 请求高度就完全由文字内容决定（= 字体
    metrics 的 ``linespace``），既不裁字，也不留 CTk 的默认留白。

    需要外框有**确定**高度时（徽章、CTA 按钮、图标位）显式传 ``height=``，
    本类不覆盖显式值。
    """

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        kwargs.setdefault("height", 1)
        super().__init__(master, **kwargs)


# --------------------------------------------------------------------------
# 任务状态图标（勾选框）
# --------------------------------------------------------------------------
# 圆环与对勾用 PIL 以 4× 超采样渲染成**带 alpha 的位图**再贴到 Canvas 上。
# 为什么不用 ``create_oval``：Tk 的 Canvas 图元**不做抗锯齿**，1.4px 的细线
# 会被量化成"一粒一粒"的硬块（这正是上一版"简陋"观感的来源之一）。
_CHECK_PATH = theme.TASK_CHECK_PATH
_CHECK_SS = theme.BITMAP_SS                     # 超采样倍率


def _render_check_pil(size: int, ring_w: float, mark_w: float, ring_color: str,
                      mark_color: str, checked: bool):
    """生成一张 ``size×size`` 的 RGBA 状态图标（圆环 + 可选对勾）。"""
    from PIL import Image, ImageDraw

    big = size * _CHECK_SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = big / 2.0
    # 半径取**中心线**半径：外径 = DIAM × size，扣掉半个线宽才是路径半径，
    # 否则线宽会全部长在圆外，视觉直径比设计值大一整条线。
    r = max(1.0, (size * theme.TASK_CHECK_DIAM - ring_w) / 2.0) * _CHECK_SS
    draw.ellipse([c - r, c - r, c + r, c + r], outline=ring_color,
                 width=max(1, round(ring_w * _CHECK_SS)))
    if checked:
        pts = [(c + px * big, c + py * big) for px, py in _CHECK_PATH]
        w = max(1, round(mark_w * _CHECK_SS))
        draw.line(pts, fill=mark_color, width=w, joint="curve")
        # 两端补圆帽（``ImageDraw.line`` 默认是方头，端点会切一刀）
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=mark_color)
    return img.resize((size, size), Image.LANCZOS)


class SunCheck(tk.Canvas):
    """任务状态图标：**柔橘细线圆环**（未完成）/ 环内嵌简洁对勾（已完成）。

    为什么重做（1.5.6）
    ------------------
    上一版：未完成 = 纯白实心圆 + 灰色描边 + 中央一枚浅色小点；已完成 =
    实心暖金圆 + 白色对勾 + 一圈光晕。两个毛病：

    1. **纯白方块**。画布底色当初写死成 ``card`` 的白色，没跟着卡片的实际底色
       走 —— 已完成卡片的底色是 ``card_done``（#F7F2E9），于是那 24×24 的白色
       画布在卡片上露出一块生硬的白方块。现在底色由调用方按**卡片当前底色**
       传入，并在卡片换色时通过 :meth:`set_bg` 同步。
    2. **实心色块太重**。整屏都是细线 + 淡色，"一坨实心暖金圆"在视觉上比标题
       还抢眼。现在两个状态共用同一枚**柔橘细线圆环**（无填充），仅以环内有无
       对勾区分；线宽/半径比全部走 ``theme``。

    为什么每帧新建 ``PhotoImage`` 而不做模块级缓存
    ----------------------------------------------
    批量截图时每张图都会新建一个 Tk 解释器，上一次的 PhotoImage 会变成死引用
    （``image "pyimageN" doesn't exist``）。PIL 原图是纯数据、可以跨解释器复用，
    PhotoImage 不行。所以只缓存 PIL 图；控件重画频率极低（状态切换 / 悬停进出），
    新建一张 24px 的 PhotoImage 开销可以忽略。
    """

    _PIL_CACHE: dict = {}

    def __init__(self, master: tk.Misc, size: int = 26, checked: bool = False,
                 bg: str = theme.LIGHT["card"], command: Optional[Callable[[bool], None]] = None,
                 animate: bool = True) -> None:
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self._size = size
        self._checked = checked
        self._bg = bg
        self._command = command
        self._animate = animate
        self._hover = False
        self._jobs: List[str] = []
        self._animating = False
        self._photo = None            # 必须持引用，否则 PhotoImage 被 GC
        self.bind("<Button-1>", self._on_click)
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.bind("<FocusIn>", lambda _e: self._draw(), add="+")
        self.bind("<FocusOut>", lambda _e: self._draw(), add="+")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._draw()

    # ---------------- 基础绘制 ----------------
    def _pil(self, checked: bool, hover: bool):
        ring_w = theme.lpx(theme.TASK_CHECK_RING_W_HOVER if (hover and not checked)
                           else theme.TASK_CHECK_RING_W)
        mark_w = theme.lpx(theme.TASK_CHECK_MARK_W)
        ring_c = theme.c("orange_hover") if (hover and not checked) else theme.c("orange")
        mark_c = theme.c("orange")
        key = (self._size, round(ring_w, 2), round(mark_w, 2), ring_c, mark_c, checked)
        img = self._PIL_CACHE.get(key)
        if img is None:
            img = _render_check_pil(self._size, ring_w, mark_w, ring_c, mark_c, checked)
            self._PIL_CACHE[key] = img
        return img

    def _draw(self) -> None:
        # 打勾动画期间不要重绘：delete("all") 会把正在扩散的光环一起抹掉
        if self._animating:
            return
        from PIL import ImageTk

        self.delete("all")
        c = self._size / 2.0
        self._photo = ImageTk.PhotoImage(self._pil(self._checked, self._hover))
        self.create_image(c, c, image=self._photo, anchor="center")
        if self.focus_get() is self:
            self.create_oval(
                1, 1, self._size - 1, self._size - 1,
                outline=theme.c("accent_soft"), width=max(1, theme.lpx(1)),
            )

    def set_bg(self, bg: str) -> None:
        """同步画布底色 = 卡片**当前**底色（换主题 / 切完成态 / 拖拽高亮时调用）。"""
        if bg == self._bg:
            return
        self._bg = bg
        try:
            self.configure(bg=bg)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 事件 ----------------
    def _on_enter(self, _event=None) -> None:
        self._hover = True
        if not self._checked:
            self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        if not self._checked:
            self._draw()

    def _on_click(self, _event=None) -> None:
        try:
            self.focus_set()
        except Exception:  # noqa: BLE001
            pass
        self.set(not self._checked, notify=True)

    def _on_key(self, event=None) -> str:
        if event is not None and getattr(event, "keysym", "") in ("space", "Return"):
            self.set(not self._checked, notify=True)
        return "break"

    def set(self, value: bool, notify: bool = False) -> None:
        changed = value != self._checked
        self._checked = value
        self._draw()
        if value and self._animate and changed:
            self._burst()
        if notify and self._command:
            self._command(value)

    def refresh(self) -> None:
        self._draw()

    # ---------------- 光晕动画 ----------------
    def _burst(self) -> None:
        """打勾瞬间：一圈柔橘光环从中心向外扩散并淡出（约 400ms）。

        配色改成与图标同族的柔橘（旧版是暖阳金，和现在的细线圆环不搭）。
        动画期间由本函数创建的 item 会在结束时 ``delete`` —— tkinter 的
        canvas item 不会自动回收，留着就成残影。

        ❗这里必须用 ``theme.c("orange")`` 这类**当前模式**的单色，不能用
        ``pair()`` 的元组（Canvas 图元不认元组）。
        """
        s = self._size
        c = s / 2.0
        outer_color = theme.c("orange")
        inner_color = theme.c("accent_soft")
        bg = self._bg

        rings: List[Tuple[int, str, float]] = [
            (self.create_oval(c, c, c, c, outline="", fill=""), inner_color, 0.0),
            (self.create_oval(c, c, c, c, outline="", fill=""), outer_color, 0.22),
        ]
        frames = 25                      # 25 帧 × 16ms ≈ 400ms
        self._animating = True

        def finish() -> None:
            self._animating = False
            for rid, _, _ in rings:
                try:
                    self.delete(rid)
                except Exception:  # noqa: BLE001
                    pass

        def step(i: int) -> None:
            if not self.winfo_exists():
                self._animating = False
                return
            if i > frames:
                finish()
                return
            t = i / frames
            for rid, tone, offset in rings:
                # 错峰启动：外圈稍晚一点，形成"一圈追一圈"的呼吸感
                tt = (t - offset) / max(0.001, 1.0 - offset)
                if tt <= 0:
                    self.itemconfigure(rid, fill="")
                    continue
                tt = min(1.0, tt)
                r = 7 + tt * 12
                color = theme.mix(tone, bg, min(1.0, tt ** 0.85))
                self.coords(rid, c - r, c - r, c + r, c + r)
                self.itemconfigure(rid, fill=color)
            job = self.after(16, step, i + 1)
            self._jobs.append(job)

        step(0)

    def destroy(self) -> None:  # noqa: D401
        for job in self._jobs:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._jobs.clear()
        self._photo = None
        super().destroy()


# --------------------------------------------------------------------------
# 圆形图标按钮
# --------------------------------------------------------------------------
class IconButton(ctk.CTkButton):
    """极简圆形/圆角图标按钮。

    ``glyph`` 可以是：
    * **图标键**（如 ``"calendar"``）—— 走 ``shiguang.icons`` 自绘图标，
      颜色由图标系统控制，深浅色自动适配；
    * **普通字符**（如 ``"＋"``）—— 用 ``text`` 直接渲染，字号由 ``font_role`` 决定。

    需求 14 要求弃用 emoji，所以传 emoji 时会自动尝试映射到图标键。
    """

    def __init__(self, master: tk.Misc, text: str, command: Callable[[], None],
                 size: int = 28, fg: Optional[Tuple[str, str]] = None,
                 hover: Optional[Tuple[str, str]] = None,
                 text_color: Optional[Tuple[str, str]] = None,
                 font_role: str = "button", radius: Optional[int] = None,
                 icon: Optional[str] = None,
                 icon_size: Optional[int] = None) -> None:
        # 显式给了 icon 就用 icon；否则若 text 命中图标键/emoji 映射，也走图标
        glyph = icon
        if glyph is None:
            glyph = _as_icon_key(text)
        self._photo = None
        self._icon_key = glyph
        # 图标尺寸独立可调（如分组标题的 12px 折叠箭头），默认主尺寸 18px
        self._icon_size = icon_size
        if glyph is not None:
            try:
                from .. import icons
                # 用 CTkImage：CTk 控件在 HighDPI 下能正确缩放它
                self._photo = icons.get_ctk(glyph, icon_size or icons.SIZE_MAIN)
            except Exception:  # noqa: BLE001
                self._photo = None

        # 取到图标就用图片；否则退回文字
        if self._photo is not None:
            label, image = "", self._photo
        else:
            label, image = text, None

        super().__init__(
            master,
            text=label,
            image=image,
            width=size,
            height=size,
            corner_radius=radius if radius is not None else size // 2,
            fg_color=fg if fg is not None else "transparent",
            hover_color=hover if hover is not None else theme.pair("ghost"),
            text_color=text_color if text_color is not None else theme.pair("text_muted"),
            font=theme.font(font_role),
            command=command,
        )

    def set_icon(self, key: Optional[str], fallback_text: str = "",
                 text_color: Optional[Tuple[str, str]] = None) -> None:
        """换图标（计时器/置顶状态变化时用），取不到图标时退回文字。"""
        photo = None
        if key:
            try:
                from .. import icons
                photo = icons.get_ctk(key, self._icon_size or icons.SIZE_MAIN)
            except Exception:  # noqa: BLE001
                photo = None
        self._photo = photo
        self._icon_key = key
        if photo is not None:
            self.configure(image=photo, text="")
        else:
            self.configure(image=None, text=fallback_text)
        if text_color is not None:
            self.configure(text_color=text_color)

    def set_fade(self, t: float) -> None:
        """按 ``t``（0~1）调整图标可见度，用于 hover 淡入淡出。

        tkinter 控件没有 alpha 通道，所以走 ``icons.get_ctk_faded``
        重新拿一张按透明度预乘过的图（内部有分档缓存，不会每帧重绘）。
        """
        key = self._icon_key
        if not key:
            # 字符按钮：直接调文字颜色向底色靠拢
            base = theme.pair("card")
            muted = theme.pair("text_muted")
            try:
                self.configure(text_color=(theme.mix(base[0], muted[0], t),
                                           theme.mix(base[1], muted[1], t)))
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            from .. import icons
            photo = icons.get_ctk_faded(key, self._icon_size or icons.SIZE_MAIN, fade=t)
            if photo is not None:
                self.configure(image=photo)
        except Exception:  # noqa: BLE001
            pass


def _as_icon_key(text: str) -> Optional[str]:
    """把 emoji / 图标键统一成图标键；不能识别返回 None。"""
    if not text:
        return None
    try:
        from .. import icons
    except Exception:  # noqa: BLE001
        return None
    if text in icons._PAINTERS:
        return text
    return icons.EMOJI_TO_KEY.get(text)


# --------------------------------------------------------------------------
# 柔和渐变进度条
# --------------------------------------------------------------------------
class SoftProgress(AutoRedrawCanvas):
    """圆角进度条，填充部分带暖金渐变。

    注意：这是裸 ``tk.Canvas``，CTk 的 DPI 自动缩放**管不到它** ——
    ``width`` / ``height`` 会原样当物理像素用。所以调用方传进来的逻辑尺寸
    必须在这里过一遍 ``theme.lpx()``，否则 150% 屏上 8px 的条只剩 5px 高，
    看着像一条脏线（并且会和上方文字贴到一起）。

    被 grid 拉宽时靠 ``AutoRedrawCanvas`` 的 ``<Configure>`` 重画（需求 ②）。
    """

    def __init__(self, master: tk.Misc, width: int = 300, height: int = 10,
                 bg: str = theme.LIGHT["card"], value: float = 0.0) -> None:
        self._cw = theme.lpx(width)
        self._ch = theme.lpx(height)
        super().__init__(master, width=self._cw, height=self._ch, bg=bg,
                         highlightthickness=0, bd=0)
        self._bg = bg
        self._value = value
        self._init_auto_redraw()
        self._draw()

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self._draw()

    def refresh_theme(self) -> None:
        """换肤后重画（Canvas 背景是写死的单色，不会自动跟主题走）。"""
        self._bg = theme.c("card")
        self.configure(bg=self._bg)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        # 宽度以实测为准（grid sticky="ew" 会把它拉宽），高度用构造时的值
        cw = self.winfo_width()
        if cw <= 1:
            cw = self._cw
        ch = self._ch
        r = ch / 2
        round_rect(self, 0, 0, cw, ch, r, fill=theme.c("track"), outline="")
        fill_w = cw * self._value
        if fill_w < 1:
            return
        fill_w = max(ch, fill_w)
        seg = 14
        for i in range(seg):
            t0, t1 = i / seg, (i + 1) / seg
            color = theme.mix(theme.c("accent_soft"), theme.c("accent"), min(1.0, 0.25 + t0 * 0.9))
            x0 = fill_w * t0
            x1 = fill_w * t1 + 1
            round_rect(self, x0, 0, x1, ch, r, fill=color, outline="")


# --------------------------------------------------------------------------
# 柔和柱状图
# --------------------------------------------------------------------------
class SoftBarChart(AutoRedrawCanvas):
    """柔和渐变柱状图（无坐标轴线，符合"避免生硬图表线条"的要求）。

    与 :class:`SoftProgress` 一样是裸 ``tk.Canvas`` —— CTk 不会缩放它，
    所有间距都必须自己过 ``lpx()``。早期 pad_top 写死 22，在 150% 屏上
    只有 14.7 逻辑像素，柱子顶端的数值标签被画到画布外（截图里"4"被切掉一半）。

    窗口缩放时靠 ``AutoRedrawCanvas`` 重画：柱子数量固定，宽度按实测画布宽
    重新分配，所以拉宽窗口柱子会跟着变粗而不是整体挤在左边（需求 ②）。
    """

    def __init__(self, master: tk.Misc, width: int = 340, height: int = 130,
                 bg: str = theme.LIGHT["card"]) -> None:
        self._cw = theme.lpx(width)
        self._ch = theme.lpx(height)
        super().__init__(master, width=self._cw, height=self._ch, bg=bg,
                         highlightthickness=0, bd=0)
        self._bg = bg
        self._data: Sequence[Tuple[str, int, bool]] = []
        self._SS = 4             # 超采样倍率（与 DueBanner 同值）
        self._photo = None       # 位图引用（防 GC）
        self._init_auto_redraw()

    def set_data(self, data: Sequence[Tuple[str, int, bool]]) -> None:
        self._data = list(data)
        self._draw()

    def refresh_theme(self) -> None:
        """换肤后重画（Canvas 背景与配色都是写死的单色，不会自动跟主题走）。"""
        self._bg = theme.c("card")
        self.configure(bg=self._bg)
        self._draw()

    def _draw(self) -> None:
        """整图 PIL 离屏渲染（1.5.12：柱体边缘抗锯齿）。

        旧实现直接在 tk.Canvas 上画圆角矩形 —— Tk 图元**不做抗锯齿**，
        柱子边缘呈明显锯齿/虚线感。现在整张图（柱体渐变 + 空位提示 +
        数值/星期文字）在 4× 超采样画布上绘制 → LANCZOS 缩回 → 单张
        ``create_image`` 贴上，边缘连续平滑；文字顺带走 PIL/FreeType
        灰度抗锯齿，ClearType 彩边一并消失。PIL/字体不可用时退回旧画法。
        """
        self.delete("all")
        if not self._data:
            return
        try:
            from PIL import Image, ImageChops, ImageDraw, ImageTk
            from .. import fonts
            value_font = fonts.pil_font("tiny_bold", supersample=self._SS)
            small_font = fonts.pil_font("small_bold", supersample=self._SS)
            tiny_font = fonts.pil_font("small", supersample=self._SS)
        except Exception:  # noqa: BLE001
            self._draw_fallback()
            return
        cw = self.winfo_width()
        if cw <= 1:
            cw = self._cw
        ch = self._ch
        ss = self._SS
        img = Image.new("RGB", (cw * ss, ch * ss), self._bg)
        d = ImageDraw.Draw(img)
        pad_top = theme.lpx(24)
        pad_bottom = theme.lpx(22)
        n = len(self._data)
        gap = theme.lpx(12)
        bar_w = max(theme.lpx(12), (cw - gap * (n + 1)) / n)
        max_val = max([v for _, v, _ in self._data] + [1])
        usable = ch - pad_top - pad_bottom
        stub = theme.lpx(6)
        track = theme.c("track")
        accent = theme.c("accent")
        accent_soft = theme.c("accent_soft")
        orange = theme.c("orange")
        text_c = theme.c("text")
        muted_c = theme.c("text_muted")

        def mix(c1, c2, t):
            return theme.mix(c1, c2, t)

        for i, (label, value, is_today) in enumerate(self._data):
            x0 = gap + i * (bar_w + gap)
            x1 = x0 + bar_w
            base_y = ch - pad_bottom
            # 基线空位提示（超采样坐标系）
            d.rounded_rectangle([x0 * ss, (base_y - stub) * ss, x1 * ss, base_y * ss],
                                radius=theme.lpx(3) * ss, fill=track)
            if value > 0:
                h = max(stub, usable * (value / max_val))
                y0 = base_y - h
                top = accent if is_today else accent_soft
                bottom = orange if is_today else accent
                bw = max(1, int(round(bar_w * ss)))
                bh = max(1, int(round(h * ss)))
                # 顶部大圆角 + 其余小圆角：两块圆角矩形遮罩取并集
                mask = Image.new("L", (bw, bh), 0)
                md = ImageDraw.Draw(mask)
                md.rounded_rectangle([0, 0, bw - 1, bh - 1],
                                     radius=max(1, theme.lpx(2) * ss), fill=255)
                cap_r = int(min(theme.lpx(8) * ss, bw / 2.4))
                cap = Image.new("L", (bw, bh), 0)
                ImageDraw.Draw(cap).rounded_rectangle(
                    [0, 0, bw - 1, min(bh - 1, cap_r * 2)], radius=cap_r, fill=255)
                mask = ImageChops.lighter(mask, cap)
                # 垂直渐变（逐行，PIL 无原生渐变）
                grad = Image.new("RGB", (bw, bh))
                gd = ImageDraw.Draw(grad)
                for yy in range(bh):
                    t = yy / max(1, bh - 1)
                    gd.line((0, yy, bw, yy), fill=mix(top, bottom, t))
                img.paste(grad, (int(round(x0 * ss)), int(round(y0 * ss))), mask)
                # 数值（柱顶上方；pad_top 已给足空间）
                if value_font is not None:
                    ty = max(theme.lpx(8), y0 - theme.lpx(9)) * ss
                    d.text((int((x0 + x1) / 2 * ss), int(ty)), str(value),
                           font=value_font,
                           fill=text_c if is_today else muted_c, anchor="ms")
            # 星期标签
            f = small_font if is_today else tiny_font
            if f is not None:
                d.text((int((x0 + x1) / 2 * ss), int((base_y + theme.lpx(12)) * ss)),
                       label, font=f,
                       fill=accent if is_today else muted_c, anchor="ms")
        try:
            photo = ImageTk.PhotoImage(img.resize((cw, ch), Image.LANCZOS))
        except Exception:  # noqa: BLE001
            self._draw_fallback()
            return
        self._photo = photo                    # 必须持引用，否则被 GC
        self.create_image(0, 0, anchor="nw", image=photo)

    def _draw_fallback(self) -> None:
        """PIL/字体不可用时的旧画法（Tk 图元，有锯齿但保功能）。"""
        cw = self.winfo_width()
        if cw <= 1:
            cw = self._cw
        ch = self._ch
        pad_top = theme.lpx(24)
        pad_bottom = theme.lpx(22)
        n = len(self._data)
        gap = theme.lpx(12)
        bar_w = max(theme.lpx(12), (cw - gap * (n + 1)) / n)
        max_val = max([v for _, v, _ in self._data] + [1])
        usable = ch - pad_top - pad_bottom
        stub = theme.lpx(6)
        for i, (label, value, is_today) in enumerate(self._data):
            x0 = gap + i * (bar_w + gap)
            x1 = x0 + bar_w
            base_y = ch - pad_bottom
            round_rect(self, x0, base_y - stub, x1, base_y, theme.lpx(3),
                       fill=theme.c("track"), outline="")
            if value > 0:
                h = max(stub, usable * (value / max_val))
                y0 = base_y - h
                top = theme.c("accent") if is_today else theme.c("accent_soft")
                bottom = theme.c("orange") if is_today else theme.c("accent")
                seg = 10
                for s in range(seg):
                    t0, t1 = s / seg, (s + 1) / seg
                    color = theme.mix(top, bottom, t0)
                    yy0 = y0 + h * t0
                    yy1 = y0 + h * t1 + 1
                    radius = min(theme.lpx(8), bar_w / 2.4) if s == 0 else theme.lpx(2)
                    round_rect(self, x0, yy0, x1, yy1, radius, fill=color, outline="")
                self.create_text((x0 + x1) / 2, max(theme.lpx(8), y0 - theme.lpx(9)),
                                 text=str(value),
                                 fill=theme.c("text") if is_today else theme.c("text_muted"),
                                 font=theme.tkfont_spec("tiny_bold" if is_today else "tiny"))
            self.create_text((x0 + x1) / 2, base_y + theme.lpx(12), text=label,
                             fill=theme.c("accent") if is_today else theme.c("text_muted"),
                             font=theme.tkfont_spec("small_bold" if is_today else "small"))


# --------------------------------------------------------------------------
# 空状态插画
# --------------------------------------------------------------------------
class EmptyIllustration(AutoRedrawCanvas):
    """温暖插画风空状态：太阳 + 两朵线条云 + 地平线。

    全部用代码绘制，不引入任何图片资源 —— 打包体积里省下的每一个字节，
    都会体现在"绿色版能不能塞进 U 盘"这件事上。

    所有坐标都按**实测画布尺寸**算，不写死像素：卡片被拉宽时太阳要跟着
    居中，而不是孤零零留在左边（需求 ②：布局必须跟尺寸走）。
    """

    W, H = theme.EMPTY_ILLUS_W, theme.EMPTY_ILLUS_H

    def __init__(self, master: tk.Misc, width: int = 0, height: int = 0,
                 bg: str = theme.LIGHT["card"]) -> None:
        self._cw = theme.lpx(width or self.W)
        self._ch = theme.lpx(height or self.H)
        super().__init__(master, width=self._cw, height=self._ch, bg=bg,
                         highlightthickness=0, bd=0)
        self._bg = bg
        self._init_auto_redraw()
        self._draw()

    def refresh_theme(self) -> None:
        self._bg = theme.c("card")
        self.configure(bg=self._bg)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        soft = theme.c("accent_soft")
        accent = theme.c("accent")
        orange = theme.c("orange")
        line = theme.mix(theme.c("gold_soft"), theme.c("orange"), 0.55)

        # 以实测尺寸为基准（构造期取不到就退回设计尺寸），并把内容缩放到装得下
        w = self.winfo_width()
        if w <= 1:
            w = self._cw
        h = self.winfo_height()
        if h <= 1:
            h = self._ch
        k = min(w / self._cw, h / self._ch) if self._cw and self._ch else 1.0
        k = max(0.5, min(1.6, k))
        cx = w / 2
        cy = h * 0.42

        # ---- 太阳 ----
        r = 20 * k
        self.create_oval(cx - r - 9 * k, cy - r - 9 * k,
                         cx + r + 9 * k, cy + r + 9 * k, fill=soft, outline="")
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=accent, outline="")
        for i in range(8):
            ang = math.radians(i * 45 + 22.5)
            r0, r1 = r + 13 * k, r + 22 * k
            self.create_line(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0,
                             cx + math.cos(ang) * r1, cy + math.sin(ang) * r1,
                             fill=orange, width=max(1, int(2 * k)), capstyle="round")

        # ---- 云朵（线条风：只有描边，没有填充）----
        self._cloud(cx + 62 * k, cy - 12 * k, 0.78 * k, line)
        self._cloud(cx - 24 * k, cy + 34 * k, 0.56 * k,
                    theme.mix(line, theme.c("accent_soft"), 0.35))

        # ---- 地平线：一段柔和的弧，暗示"还在路上" ----
        self.create_arc(cx - 130 * k, cy + 52 * k, cx + 130 * k, cy + 134 * k,
                        start=200, extent=140, style="arc",
                        outline=theme.mix(orange, soft, 0.5), width=max(1, int(2 * k)))

    def _cloud(self, cx: float, cy: float, scale: float, color: str) -> None:
        """画一朵云：三个上半圆弧 + 一条底边拼出轮廓。

        为什么不用三个整圆：整圆会互相穿帮，露出内部的交叉弧线；
        用 ``style="arc"`` 只画上半圈，再接一条底线，轮廓才是干净的。
        """
        w = 30 * scale
        h = 17 * scale
        width = max(1, int(1.5 * max(0.6, scale)))
        for offset, radius in ((-0.52, 0.62), (0.0, 0.98), (0.52, 0.66)):
            r = h * radius
            bx = cx + offset * w
            self.create_arc(bx - r, cy - r, bx + r, cy + r,
                            start=0, extent=180, style="arc",
                            outline=color, width=width)
        self.create_line(cx - w - h * 0.5, cy, cx + w + h * 0.55, cy,
                         fill=color, width=width, capstyle="round")


# --------------------------------------------------------------------------
# 顶部导航切换器（替代原来的底部悬浮药丸）
# --------------------------------------------------------------------------
class TopNavSwitcher(tk.Canvas):
    """标题栏里的极简「图标 + 文字」页面切换器。

    为什么把底部的悬浮药丸换掉
    --------------------------
    56px 高的药丸 + 12px 底距 + 6px 投影 ≈ 74px 的纵向占用，在 470px 高的
    窗口里接近 1/6；而且它是**悬浮**的，内容区还得额外留出同样多的底部内边距
    （否则最后一张任务卡会被压在药丸下面点不到）。挪到标题栏之后：

    * 纵向**零额外占用**（标题栏本来就有 30px 的空白区）；
    * 不再需要 ``place`` 相对定位，普通 grid 布局即可；
    * 内容区可以一路贴到窗口底边。

    为什么这里**必须**自绘 Canvas、不能用 CTkButton
    ----------------------------------------------
    实测（见 ``tools`` 里的控件尺寸探针）：``CTkButton`` 一旦带 ``image``，
    它的**请求宽度就有一个内部下限**，显式给的 ``width`` 压不下去 ——
    scale 1.0 下"图标 + 两个汉字"至少要 59 逻辑像素，三个切换项就是 177，
    加标题栏的品牌名与窗口按钮会直接超出最小窗宽（310）。
    自绘之后每项宽度 = ``上左内边距 + 图标 + 间隙 + 实测文字宽``，
    三项合计 136 逻辑像素，留有余量。
    """

    def __init__(self, master: tk.Misc, items: List[Tuple[str, str, str]],
                 command: Callable[[str], None], bg: str = "") -> None:
        """``items``：``[(key, label, icon_key), ...]``。

        ``bg``：画布底色。默认取窗口底色（``theme.c("bg")``）——
        1.5.23 修的就是"底色写死 card(#FFFFFF)、顶栏其实是 #FAF7F2，
        于是整块导航背后一圈白边"。
        """
        self._all_items: List[Tuple[str, str, str]] = list(items)
        self._compact = False               # 窄窗模式：隐藏非关键项（1.5.18）
        self._items: List[Tuple[str, str, str]] = list(items)
        self._command = command
        self._current = ""
        self._hover = ""
        self._photos: Dict[tuple, object] = {}      # 整项位图缓存（1.5.23）
        self._bg = bg or theme.c("bg")
        # 每项宽度先按**逻辑像素**算好，落画布时再统一过 lpx()
        self._w_logic = [
            theme.fit_width(label, theme.SWITCH_FONT,
                            icon=theme.SWITCH_ICON, pad_x=theme.SWITCH_PAD_X)
            for _key, label, _icon in self._items
        ]
        self._gap = theme.lpx(theme.SWITCH_GAP)
        total = sum(theme.lpx(v) for v in self._w_logic)
        total += self._gap * max(0, len(self._items) - 1)
        self._h = theme.lpx(theme.SWITCH_H)
        super().__init__(master, width=total, height=self._h,
                         bg=self._bg, highlightthickness=0, bd=0,
                         takefocus=1,
                         cursor="hand2")
        self.bind("<Button-1>", self._on_click)
        self.bind("<Left>", self._on_key)
        self.bind("<Right>", self._on_key)
        self.bind("<Home>", self._on_key)
        self.bind("<End>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.bind("<space>", self._on_key)
        self.bind("<FocusIn>", lambda _e: self._draw(), add="+")
        self.bind("<FocusOut>", lambda _e: self._draw(), add="+")
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(""))
        self._draw()
        self._remeasure()

    def full_width_logic(self) -> float:
        """完整（非紧凑）导航的总宽，**逻辑像素**（PIL 口径，判定用）。"""
        total = sum(
            theme.fit_width(label, theme.SWITCH_FONT,
                            icon=theme.SWITCH_ICON, pad_x=theme.SWITCH_PAD_X)
            for _key, label, _icon in self._all_items)
        return total + theme.SWITCH_GAP * max(0, len(self._all_items) - 1)

    def set_compact(self, compact: bool) -> None:
        """窄窗紧凑模式（1.5.18）：隐藏非关键项，只留关键导航不重叠。

        窗口缩到放不下完整导航时，"光景"先让路（统计页等加宽窗口再进），
        保证"新建 / 任务 / 设置"始终完整可点。状态没变就不重建（防抖要求：
        截断/重排必须有尺寸未变跳过）。紧凑期间若当前页恰好是被隐藏的项，
        高亮暂时消失但页面本身不动，加宽窗口后自动恢复。
        """
        if compact == getattr(self, "_compact", False):
            return
        self._compact = compact
        hide = theme.NAV_COMPACT_HIDE
        self._items = [it for it in self._all_items
                       if not (compact and it[0] == hide)]
        self._remeasure()

    def _remeasure(self) -> None:
        """用**渲染同源**的 Tk 字体重新量宽，重设画布宽度（1.5.13）。

        ⚠️ 为什么必须再量一遍：``__init__`` 里的 ``_w_logic`` 走
        ``fonts.measure``（PIL/FreeType 口径），而 ``_draw`` 里的文字用
        ``theme.tkfont_spec`` 交给 **Tk/GDI** 渲染。两个口径对同一串汉字
        给出的宽度有累计偏差 —— 每项差 1-2px，三项下来"设置"的尾巴就被
        画布右缘裁掉（1.5.12 起预览图顶栏"设"字断半截的真因）。
        这里用与 ``create_text`` 完全相同的 Tk 字体对象量出**物理像素**宽，
        反推每项宽度与画布总宽，量宽/渲染从此同源。
        """
        try:
            from tkinter import font as _tkfont
            fam, size, weight = theme.tkfont_spec(theme.SWITCH_FONT)
            f = _tkfont.Font(root=self, family=fam, size=size, weight=weight)
            text_px = [f.measure(label) for _k, label, _i in self._items]
        except Exception:  # noqa: BLE001
            return
        pad = theme.lpx(theme.SWITCH_PAD_X)
        icon_px = theme.lpx(theme.SWITCH_ICON)
        gap_px = theme.lpx(2)
        # 每项：左内边距 + 图标 + 图文间隙 + 文字 + 右内边距（与 _draw 对齐）
        self._w_px = [pad + icon_px + gap_px + w + pad for w in text_px]
        total = sum(self._w_px) + self._gap * max(0, len(self._items) - 1)
        try:
            self.configure(width=max(1, int(total)))
        except Exception:  # noqa: BLE001
            pass
        self._draw()

    def _item_w(self, i: int) -> float:
        """第 i 项的**物理像素**宽：优先 Tk 实测值，退回 PIL 口径。"""
        px = getattr(self, "_w_px", None)
        if px is not None and i < len(px):
            return px[i]
        return theme.lpx(self._w_logic[i])

    # ------------------------------------------------------------------
    def _icon(self, icon_key: str, active: bool):
        """取图标：选中态用**白色实心**图标压在柔橘底上，未选中用原色。"""
        cached = self._photos.get((icon_key, active))
        if cached is not None:
            return cached
        size = theme.lpx(theme.SWITCH_ICON)
        photo = None
        try:
            from .. import icons
            if active:
                photo = icons.get_tinted(icon_key, size, theme.c("on_nav"))
            else:
                photo = icons.get(icon_key, size)
        except Exception:  # noqa: BLE001
            photo = None
        self._photos[(icon_key, active)] = photo
        return photo

    def _icon_name(self, icon_key: str, active: bool) -> str:
        try:
            from .. import icons
            if icon_key.startswith("nav_today"):
                # 「今日」按昼夜取太阳 / 月牙（夜里不该出现太阳）
                return icons.today_icon(filled=active)
        except Exception:  # noqa: BLE001
            pass
        return f"{icon_key}_on" if active else icon_key

    def _draw(self) -> None:
        """整项位图化（1.5.23）。

        旧实现：药丸走 Tk ``create_polygon``（四角硬阶梯），文字走
        ``create_text``（GDI/ClearType，笔画两侧有橙青彩边）—— 浅底上看着
        就是"发糊"；画布底色又写死 ``card``(#FFFFFF)，与顶栏的窗口底色
        #FAF7F2 差一格，于是整块导航背后一圈**白边**。
        现在每项一次 PIL 合成（药丸 + 图标 + 文字，4× 超采样 → LANCZOS），
        底色取顶栏真实底色，白边与彩边一起消失。
        """
        self.delete("all")
        h = self.winfo_height()
        if h <= 1:
            h = self._h
        x = 0.0
        for i in range(len(self._items)):
            w = self._item_w(i)
            photo = self._item_photo(i)
            if photo is not None:
                self.create_image(x, 0, anchor="nw", image=photo)
            else:
                self._draw_item_vector(i, x, w, h)     # PIL 缺失时降级
            x += w + self._gap

    def _item_photo(self, i: int):
        """第 i 项的位图（``PhotoImage``，带缓存）。"""
        _key, label, icon_key = self._items[i]
        active = (self._items[i][0] == self._current)
        hovered = (self._items[i][0] == self._hover)
        w = max(2, int(round(self._item_w(i))))
        cache_key = (i, label, icon_key, active, hovered, w, self._h, self._bg,
                     theme.c("accent"), theme.c("accent_hover"),
                     theme.c("text_muted"))
        photo = self._photos.get(cache_key)
        if photo is not None:
            return photo
        image = self._item_image(i, w, active, hovered)
        if image is None:
            return None
        try:
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(image, master=self)
        except Exception:  # noqa: BLE001
            return None
        self._photos[cache_key] = photo        # 持引用防 GC
        return photo

    def _item_image(self, i: int, w: int, active: bool, hovered: bool):
        """合成单项目位图：圆角药丸 + 图标 + 文字（逻辑像素 w × SWITCH_H）。"""
        try:
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return None
        from .. import fonts, icons

        ss = theme.NAV_BITMAP_SS
        h = self._h
        img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        if active:
            fill = theme.c("accent_hover") if hovered else theme.c("accent")
        elif hovered:
            fill = theme.mix(self._bg, theme.c("accent"), theme.NAV_HOVER_MIX)
        else:
            fill = ""
        if fill:
            draw.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1],
                                   radius=min(w * ss, h * ss) / 2, fill=fill)

        _key, label, icon_key = self._items[i]
        pad = theme.lpx(theme.SWITCH_PAD_X)
        icon_px = theme.lpx(theme.SWITCH_ICON)
        gap_px = theme.lpx(theme.NAV_ICON_GAP)
        name = self._icon_name(icon_key, active)
        icon_img = (icons.get_pil_tinted(name, icon_px * ss, theme.c("on_nav"))
                    if active else icons.get_pil(name, icon_px * ss))
        if icon_img is None:
            icon_img = icons.get_pil(icon_key, icon_px * ss)
        if icon_img is not None:
            img.paste(icon_img,
                      (int(pad * ss), max(0, int((h * ss - icon_img.height) / 2))),
                      icon_img)
        font = fonts.pil_font(theme.SWITCH_FONT, supersample=ss)
        if font is not None and label:
            color = theme.c("on_nav") if active else theme.c("text_muted")
            draw.text((int((pad + icon_px + gap_px) * ss), h * ss / 2), label,
                      font=font, fill=color, anchor="lm")
        return img.resize((w, h), Image.LANCZOS)

    def _draw_item_vector(self, i: int, x: float, w: float, h: float) -> None:
        """降级绘制（PIL 不可用时）：Tk 图元拼药丸 + Tk 文字。"""
        key, label, icon_key = self._items[i]
        active = (key == self._current)
        pad = theme.lpx(theme.SWITCH_PAD_X)
        icon_px = theme.lpx(theme.SWITCH_ICON)
        gap_px = theme.lpx(theme.NAV_ICON_GAP)
        spec = theme.tkfont_spec(theme.SWITCH_FONT)
        r = h / 2
        if active:
            fill = (theme.c("accent_hover") if key == self._hover
                    else theme.c("accent"))
            round_rect(self, x, 0, x + w, h, r, fill=fill, outline="")
        elif key == self._hover:
            round_rect(self, x, 0, x + w, h, r,
                       fill=theme.mix(self._bg, theme.c("accent"),
                                      theme.NAV_HOVER_MIX),
                       outline="")
        color = theme.c("on_nav") if active else theme.c("text_muted")
        photo = self._icon(self._icon_name(icon_key, active), active)
        if photo is not None:
            self.create_image(x + pad + icon_px / 2, h / 2, image=photo)
            self._photos[("_last", i)] = photo
        self.create_text(x + pad + icon_px + gap_px, h / 2, text=label,
                         fill=color, anchor="w", font=spec)

    # ------------------------------------------------------------------
    def _hit(self, x: int) -> int:
        pos = 0.0
        for i in range(len(self._items)):
            w = self._item_w(i)
            if pos <= x <= pos + w:
                return i
            pos += w + self._gap
        return -1

    def _on_click(self, event) -> None:
        try:
            self.focus_set()
        except Exception:  # noqa: BLE001
            pass
        idx = self._hit(event.x)
        if idx < 0:
            return
        key = self._items[idx][0]
        self.set_current(key)
        self._command(key)

    def _on_key(self, event) -> str:
        if not self._items:
            return "break"
        keys = [item[0] for item in self._items]
        try:
            index = keys.index(self._current)
        except ValueError:
            index = 0
        if event.keysym in ("Left", "Right", "Home", "End"):
            if event.keysym == "Left":
                index = (index - 1) % len(keys)
            elif event.keysym == "Right":
                index = (index + 1) % len(keys)
            elif event.keysym == "Home":
                index = 0
            else:
                index = len(keys) - 1
            key = keys[index]
            self.set_current(key, animate=False)
            self._command(key)
        elif event.keysym in ("Return", "space") and self._current:
            self._command(self._current)
        return "break"

    def _set_hover(self, key: str) -> None:
        if key == self._hover:
            return
        self._hover = key
        self._draw()

    def _on_motion(self, event) -> None:
        idx = self._hit(event.x)
        self._set_hover(self._items[idx][0] if idx >= 0 else "")

    # ------------------------------------------------------------------
    def set_current(self, key: str, animate: bool = True) -> None:
        """切换选中项。``animate`` 只为签名兼容保留（这里没有渐变动画）。"""
        if key == self._current:
            return
        self._current = key
        self._draw()

    def current(self) -> str:
        return self._current

    def refresh_theme(self) -> None:
        """换肤后重取图标并重上色。

        图标与文字都烘在**整项位图**里（底色预乘过），主题切换后必须
        重新出图 —— 只改画布背景的话，深色下还是浅色主题的配色。
        画布底色取窗口底色（顶栏底色），不再写死 card（1.5.23 白边的根因）。
        """
        self._photos.clear()
        self._bg = theme.c("bg")
        try:
            self.configure(bg=self._bg)
        except Exception:  # noqa: BLE001
            pass
        self._draw()


# --------------------------------------------------------------------------
# 淡出辅助（删除卡片时的退场动画）
# --------------------------------------------------------------------------
def _as_pair(value) -> Tuple[str, str]:
    """把 CTk 的颜色取值统一成 ``(浅色, 深色)``。"""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return str(value[0]), str(value[1])
    return str(value), str(value)


def fade_out(widget: tk.Misc, steps: int = 10, interval: int = 20,
             on_done: Optional[Callable[[], None]] = None,
             target: str = "bg") -> None:
    """让一个控件淡出后退场。

    为什么用"插值到页面底色"而不是真透明度：tkinter 的控件没有 alpha 通道，
    唯一的办法是按帧把背景色/文字色朝页面底色插值（Canvas 也是同一套思路）。
    200ms（10 帧 × 20ms）刚好够看出"淡出"，又不会让人等。
    """
    targets = []          # [(widget, attr, (light_from, dark_from))]
    for candidate, attr in ((widget, "fg_color"),):
        try:
            targets.append((candidate, attr, _as_pair(candidate.cget(attr))))
        except Exception:  # noqa: BLE001
            pass
    for child in widget.winfo_children():
        try:
            targets.append((child, "text_color", _as_pair(child.cget("text_color"))))
        except Exception:  # noqa: BLE001
            continue

    light_to = theme.LIGHT.get(target, theme.LIGHT["bg"])
    dark_to = theme.DARK.get(target, theme.DARK["bg"])

    def step(i: int) -> None:
        if not widget.winfo_exists():
            if on_done:
                on_done()
            return
        t = i / steps
        for target_widget, attr, (light_from, dark_from) in targets:
            try:
                target_widget.configure(**{
                    attr: (theme.mix(light_from, light_to, t),
                           theme.mix(dark_from, dark_to, t)),
                })
            except Exception:  # noqa: BLE001
                pass
        if i < steps:
            widget.after(interval, step, i + 1)
        elif on_done:
            on_done()

    step(0)


def pulse(widget: tk.Misc, steps: int = 6, interval: int = 25,
          highlight: str = "accent") -> None:
    """输入框提交后的"轻弹一下"反馈。

    为什么不做真正的缩放：CTk/Tk 控件没有 transform，改 font/height 会让整行重排、
    看起来是"跳"而不是"弹"。所以改用**边框色的一次柔和脉冲**（≈150ms），
    视觉上是"呼吸一下"，既有反馈又不打断操作节奏。

    ``steps × interval ≈ 150ms``，正好在"能感知"和"不拖慢"之间。
    """
    try:
        original = widget.cget("border_color")
    except Exception:  # noqa: BLE001
        return

    def pair_from(value) -> Tuple[str, str]:
        return _as_pair(value)

    light_from, dark_from = pair_from(original)
    light_peak = theme.LIGHT.get(highlight, light_from)
    dark_peak = theme.DARK.get(highlight, dark_from)

    def step(i: int) -> None:
        if not widget.winfo_exists():
            return
        # 0 -> 1 -> 0 的三角波
        half = max(1, steps // 2)
        t = (i / half) if i <= half else max(0.0, (steps - i) / half)
        try:
            widget.configure(border_color=(
                theme.mix(light_from, light_peak, t),
                theme.mix(dark_from, dark_peak, t),
            ))
        except Exception:  # noqa: BLE001
            return
        if i < steps:
            widget.after(interval, step, i + 1)
        else:
            try:
                widget.configure(border_color=original)
            except Exception:  # noqa: BLE001
                pass

    step(0)


# --------------------------------------------------------------------------
# 浮层提示（Toast）
# --------------------------------------------------------------------------
class Toast(tk.Toplevel):
    """精确贴合内容的 Canvas 提示卡，无投影留边和 DPI 几何补偿。"""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.transient(master.winfo_toplevel())
        self.configure(bg=theme.c("toast_border"))
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0,
                                bg=theme.c("toast_border"))
        self.canvas.pack(fill="both", expand=True)
        self._photo = None
        self._image = None
        self._action_box = None
        self._action_command: Optional[Callable[[], None]] = None
        self._job: Optional[str] = None
        self._region_job: Optional[str] = None
        self._watch_job: Optional[str] = None
        self.canvas.bind("<ButtonRelease-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.bind("<Escape>", lambda _e: self.hide())

    def show(self, message: str, duration: int = theme.TOAST_DURATION_MS,
             anchor: Optional[tk.Misc] = None, celebration: bool = False,
             action_label: str = "",
             action_command: Optional[Callable[[], None]] = None) -> None:
        from PIL import ImageTk
        from .window_shape import attach_native_owner

        self.hide()
        host = self.master
        if (not host.winfo_exists() or not host.winfo_viewable()
                or not getattr(host, "is_foreground", lambda: True)()):
            return
        host.update_idletasks()
        host_w, host_h = max(1, host.winfo_width()), max(1, host.winfo_height())
        if host_w <= 1 or host_h <= 1:
            return
        edge = theme.lpx(theme.TOAST_EDGE_PAD)
        width_limit = min(theme.lpx(theme.TOAST_MAX_W), max(1, host_w - edge * 2))
        action_label = action_label if action_command else ""
        image, (width, height), action_box = toast_image(
            message, width_limit, celebration, action_label)
        self._image, self._action_box = image, action_box
        self._action_command = action_command if action_label else None
        self.configure(bg=theme.c("toast_border"))
        self.canvas.configure(width=width, height=height, bg=theme.c("toast_border"))
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        x = (host_w - width) // 2
        y = host_h - theme.lpx(theme.TOAST_BOTTOM_OFFSET) - height
        if anchor is not None:
            try:
                if anchor.winfo_exists():
                    ax = anchor.winfo_rootx() - host.winfo_rootx()
                    ay = anchor.winfo_rooty() - host.winfo_rooty()
                    x = ax + anchor.winfo_width() + edge
                    if x + width > host_w - edge:
                        x = (host_w - width) // 2
                    # 优先在完成卡上方反馈，不盖住刚勾选的任务和下方卡片。
                    y = ay - height - edge
                    if y < edge:
                        y = ay + anchor.winfo_height() + edge
            except tk.TclError:
                pass
        x = max(edge, min(x, host_w - edge - width))
        y = max(edge, min(y, host_h - edge - height))
        self.geometry(f"{width}x{height}{host.winfo_rootx() + x:+d}{host.winfo_rooty() + y:+d}")
        # 在映射之前裁好窗口形状，首帧也不会闪出矩形角。
        apply_rounded_region(self, theme.lpx(theme.TOAST_RADIUS), size=(width, height))
        self.deiconify()
        self.update_idletasks()
        attach_native_owner(self, host)
        self.attributes("-topmost", bool(getattr(host, "store", None) and
                        host.store.settings.get("always_on_top", False)))
        self.lift()
        self._apply_native_round_region_to_window()
        self._region_job = self.after(theme.POPUP_SHAPE_DELAY_MS,
                                      self._apply_native_round_region_to_window)
        self._job = self.after(duration, self.hide)
        self._watch_job = self.after(theme.FLOAT_WATCH_MS, self._watch_owner)

    def _watch_owner(self) -> None:
        self._watch_job = None
        try:
            foreground = getattr(self.master, "is_foreground", lambda: True)()
            if not self.master.winfo_viewable() or not foreground:
                self.hide()
                return
            self._watch_job = self.after(theme.FLOAT_WATCH_MS, self._watch_owner)
        except tk.TclError:
            self.hide()

    def _inside_action(self, event) -> bool:
        if self._action_box is None:
            return False
        x1, y1, x2, y2 = self._action_box
        return x1 <= event.x <= x2 and y1 <= event.y <= y2

    def _motion(self, event) -> None:
        self.canvas.configure(cursor="hand2" if self._inside_action(event) else "")

    def _click(self, event) -> None:
        if self._inside_action(event):
            self._run_action()

    def _run_action(self) -> None:
        callback = self._action_command
        self.hide()
        if callback is not None:
            callback()

    def _apply_native_round_region_to_window(self) -> None:
        self._region_job = None
        if self.winfo_exists() and self.winfo_viewable():
            apply_rounded_region(self, theme.lpx(theme.TOAST_RADIUS))

    def hide(self) -> None:
        for attr in ("_job", "_region_job", "_watch_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
            setattr(self, attr, None)
        self.withdraw()
        self._action_command = None


# --------------------------------------------------------------------------
# 说明：原「庆祝光晕」CelebrationOverlay 已由 ui/particles.py 的 ParticleLayer 取代。
# 旧实现调用 self.lift() 触发 TclError（tk.Canvas 把 lift 重载成 tag_raise），
# 异常被 celebrate() 的 try/except 吞掉 —— 表现为"只弹治愈文案、看不到动画"。
# 粒子层改用 tkraise() 并通过事件循环里的可断言状态对外暴露，避免再被静默吞掉。
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 滚轮路由（1.5.26）
# --------------------------------------------------------------------------
# 为什么不由 CTk 那套继续管：``CTkScrollableFrame.__init__`` 每建一个实例就
# ``bind_all("<MouseWheel>", self._mouse_wheel_all, add=True)``，而它的判定
# 完全依赖 ``event.widget``（"事件落在哪个控件上"）——
#
#   * 事件落在**滚动条**上直接被拒（CTk 显式排除 CTkScrollbar）；
#   * 事件被送给**焦点窗口/根窗口**而不是指针下的控件时（Windows 在某些
#     系统设置下就是按焦点派发 WM_MOUSEWHEEL 的），也在拒绝之列 ——
#     表现就是"滚轮完全没反应，只能拖滚动条"；
#   * 页面重建（``show_page``/``refresh`` 都会 destroy 重建滚动容器）每来一次
#     就往 all 链上多挂一条它的处理器，一次会话能堆到几百条。
#
# 所以这里改成**只装一条**应用级路由：先把指针位置认出来（先看事件目标所在的
# 控件树，再看指针坐标下的控件，最后退化到"谁的可视矩形包含这个点"），
# 再交给最近的滚动容器滚。滚动条上、卡片空隙里、盖在列表上的浮层下面都能滚。
_LIVE_SCROLLS: "weakref.WeakSet" = weakref.WeakSet()
_ROUTER_VAR = "shiguang_wheel_router"


def _purge_ctk_wheel_all(widget: tk.Misc) -> int:
    """摘掉 all 链上 CTk 自己注册的 ``_mouse_wheel_all``（返回摘掉几条）。

    ``bind_all`` 是**解释器级**的：每次页面重建都会多一条，且指向已销毁的
    容器。我们的路由已经接管了滚轮，这些残骸只会让每个滚轮事件多跑几十次
    Python 调用，顺手清干净。
    """
    tkapp = widget.tk
    try:
        script = tkapp.call("bind", "all", "<MouseWheel>")
    except Exception:  # noqa: BLE001
        return 0
    if not script:
        return 0
    keep, dead = [], []
    for line in str(script).split("\n"):
        (dead if "_mouse_wheel_all" in line else keep).append(line)
    if not dead:
        return 0
    new = "\n".join(keep)
    try:
        tkapp.call("bind", "all", "<MouseWheel>", new if new.strip() else "")
    except Exception:  # noqa: BLE001
        pass
    for line in dead:
        found = re.search(r"\[(\d+_mouse_wheel_all)", line)
        if found:
            try:
                widget.deletecommand(found.group(1))
            except Exception:  # noqa: BLE001
                pass
    return len(dead)


def _detach_ctk_bar_wheel(bar) -> None:
    """摘掉 ``CTkScrollbar`` 自己绑的滚轮处理器（1.5.27）。

    ``CTkScrollbar._mouse_scroll_event`` 是**控件级**绑定（绑在它内部的
    ``_canvas`` 上），按 Tk 的绑定顺序它跑在 ``all`` 链**之前**，而且不返回
    ``"break"`` —— 于是"路由再滚一次"必然跟上：悬停在滚动条上会变成
    CTk 的 ``-delta/40``（约 3px）+ 路由的 20px，两套力度叠加。
    既然悬停滚动条也该由统一路由按 20px/格处理，就把 CTk 这条一并摘掉。

    （``CTkSlider`` 有同样的绑定，但本工程没有用 slider；若将来引入，
    不要对它调用本函数。）
    """
    canvas = getattr(bar, "_canvas", None)
    if canvas is None:
        return
    seqs = ["<MouseWheel>"]
    if not sys.platform.startswith("win"):
        seqs += ["<Button-4>", "<Button-5>"]
    for seq in seqs:
        try:
            canvas.unbind(seq)           # 不带 funcid = 去掉该序列上的全部脚本
        except Exception:  # noqa: BLE001
            pass


def install_wheel_router(widget: tk.Misc) -> bool:
    """给当前解释器装**唯一一条**滚轮路由，返回本次是否真的装上。

    ``bind_all`` 的作用域是 Tcl 解释器（不是某个窗口），所以用解释器级的
    Tcl 变量做幂等标记 —— 同一个进程里连续创建多个 App（截图工具就是这么
    干的）时，每个新解释器都会重新装一次。
    """
    tkapp = widget.tk
    first = False
    try:
        first = not tkapp.getvar(_ROUTER_VAR)
    except Exception:  # noqa: BLE001
        first = True
    _purge_ctk_wheel_all(widget)
    if not first:
        return False
    try:
        tkapp.setvar(_ROUTER_VAR, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        top = widget.winfo_toplevel()
        top.bind_all("<MouseWheel>", _route_wheel, add="+")
        if not sys.platform.startswith("win"):
            # X11 的滚轮是 Button-4/5 两个按钮事件
            top.bind_all("<Button-4>", _route_wheel, add="+")
            top.bind_all("<Button-5>", _route_wheel, add="+")
    except Exception:  # noqa: BLE001
        return False
    return True


def _frame_of_widget(widget) -> "Optional[ThinScrollFrame]":
    """沿控件树向上找最近的滚动容器（含它自己的滚动条/内层 frame）。"""
    seen = 0
    node = widget
    while node is not None and seen < 40:
        seen += 1
        for frame in list(_LIVE_SCROLLS):
            try:
                if (node is frame or node is frame._parent_canvas
                        or node is frame._parent_frame):
                    return frame
            except Exception:  # noqa: BLE001
                continue
        node = getattr(node, "master", None)
    return None


def _frame_at_point(x: int, y: int, toplevel=None) -> "Optional[ThinScrollFrame]":
    """几何兜底：谁的可视矩形包含这个点。

    用来穿透"盖在列表上、但本身不是滚动容器"的东西（提示条、动画层……）——
    只按控件树找的话，指针在这种覆盖层上就找不到下面的列表了。
    ``toplevel`` 用来排除别的窗口（浮层是自己一个 Toplevel，
    指针在它上面时不该去滚主窗口里的列表）。
    """
    for frame in list(_LIVE_SCROLLS):
        try:
            if toplevel is not None and frame.winfo_toplevel() is not toplevel:
                continue
            for w in (frame._parent_canvas, frame._bar):
                if w is None or not w.winfo_ismapped():
                    continue
                rx, ry = w.winfo_rootx(), w.winfo_rooty()
                if rx <= x < rx + w.winfo_width() and ry <= y < ry + w.winfo_height():
                    return frame
        except Exception:  # noqa: BLE001
            continue
    return None


def wheel_delta(event) -> int:
    """把任意平台的滚轮事件折算成**有符号的 delta**：正 = 上滚、负 = 下滚。

    ⚠️ 这里踩过一个致命坑（1.5.26 的"只能向下滚"）：Tk 在 ``<MouseWheel>``
    事件里把 ``event.num`` 填成**字符串** ``'??'``（不是 0，也不是 4）。
    于是 ``if event.num:`` 恒为**真**、``event.num == 4`` 恒为**假** —— 判平台
    的分支被整个跳过，方向信息（``delta``）根本没被读到，只剩一句常量正向步长。
    合成 ``event_generate("<MouseWheel>", delta=...)`` 同样给 ``'??'``，所以
    "投递一次、看有没有动"的探针会**假通过**（动了，但只会往一个方向动）。

    正确判据：**先看 delta，num 只在 delta 缺省时兜底**，且 num 一律按
    ``int`` 比较（``num == 4``），绝不写 ``if num:``。
    """
    delta = getattr(event, "delta", 0) or 0
    if delta:
        return delta                 # 正=上、负=下（Windows / macOS / 触控板同号）
    num = getattr(event, "num", 0)
    if num == 4:                     # X11：Button-4 = 上滚
        return 120
    if num == 5:                     # X11：Button-5 = 下滚
        return -120
    return 0


def _route_wheel(event):
    """应用级滚轮路由：把事件落到"指针位置下面"的那个滚动容器上。"""
    if is_resizing():
        # 缩放手势中布局是冻结的（ResizeGate），这时滚动只会打架
        return None
    frame = _frame_of_widget(getattr(event, "widget", None))
    x = getattr(event, "x_root", None)
    y = getattr(event, "y_root", None)
    if frame is None and x is not None and y is not None:
        under = None
        try:
            under = event.widget.winfo_toplevel().winfo_containing(x, y)
        except Exception:  # noqa: BLE001
            under = None
        frame = _frame_of_widget(under)
        if frame is None and under is not None:
            try:
                frame = _frame_at_point(x, y, under.winfo_toplevel())
            except Exception:  # noqa: BLE001
                frame = None
    if frame is None:
        return None                      # 不在我们的滚动区里 → 让别的处理器接手
    try:
        handled = frame.scroll_wheel(event)
    except Exception:  # noqa: BLE001
        handled = False
    return "break" if handled else None


# --------------------------------------------------------------------------
# 滚动容器（需求 35 起；1.5.24「常态可见」；1.5.25「动态显隐 + 半透明柔橘」）
# --------------------------------------------------------------------------
class ThinScrollFrame(ctk.CTkScrollableFrame):
    """内容装不下时才出现的 6px 半透明柔橘滚动条。

    历史沿革
    --------
    * 需求 35：4px + 常态用**页面底色**画滑块（视觉上等于没有），只有鼠标进入
      滚动区才换柔橘 —— 用户报"太细、看不出来"；
    * 1.5.24：8px、常态暖棕、悬停柔橘，常驻显示 —— 用户报"有点粗、突兀"；
    * 1.5.25：**收窄到 6px（视觉 4px）**，配色改成半透明柔橘（把品牌柔橘按
      45% 掺进页面底色；Tk 画布图元没有 alpha，"半透明"只能这样与已知底色
      预乘模拟），并且**内容装得下时整条隐藏**。

    动态显隐怎么判
    --------------
    ``need``  = 内容自然高度 = 内层 frame 的 ``winfo_reqheight()``；
    ``avail`` = 可视高度 = ``_parent_canvas.winfo_height()``。

    没有用 ``yview() != (0, 1)`` 之类的"滚动位置法"：那要等画布重算完
    scrollregion 才准，而且在隐藏状态下反而恒为 "装得下"。直接量两个高度
    更直白，也更好在探针里断言。

    ⚠️ 隐藏/显示会改变画布宽度（回收 6px），内容跟着重排，于是又触发一次
    ``<Configure>``。为此加了两个保险：

    * ``SCROLLBAR_HIDE_HYST`` 迟滞 —— 已显示时要"短 8px"才收回，掐掉
      临界高度上的反复横跳；
    * 评估**合并到 ``after_idle`` 单次执行**（``_bar_job`` 去重）＋
      重入守卫，一轮里最多翻转一次，不构成重绘循环。
    """

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        # 关闭 CTk 的圆角/描边，滚动条自己重画
        kwargs.setdefault("fg_color", "transparent")
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("scrollbar_fg_color", "transparent")
        kwargs.setdefault("scrollbar_button_color", theme.pair("scroll_thumb"))
        kwargs.setdefault("scrollbar_button_hover_color",
                          theme.pair("scroll_thumb_hover"))
        super().__init__(master, **kwargs)

        self._bar = getattr(self, "_scrollbar", None)
        self._hover_job: Optional[str] = None
        self._hovering = False
        # 1.5.27：触控板小 delta 的像素余量（<1px 的部分留到下一次一起结算）
        self._wheel_residual = 0.0
        # ---- 动态显隐状态 ----
        self._bar_shown = True          # CTk 建好时是显示着的
        self._bar_job: Optional[str] = None
        self._bar_busy = False
        # 1.5.26：登记进滚轮路由的名单，并把 CTk 那条（会随页面重建堆积的）
        # all 绑定摘掉 —— 滚轮从此走 widgets._route_wheel 这条唯一路径。
        _LIVE_SCROLLS.add(self)
        install_wheel_router(self)
        self._apply_style()
        _detach_ctk_bar_wheel(self._bar)   # 1.5.27：滚动条上不许再滚第二次
        self._bind_visibility()
        # 鼠标进出**整块滚动区**时强调滑块（6px 的条子本身太窄，很难命中）
        parent_canvas = getattr(self, "_parent_canvas", None)
        for w in (self, parent_canvas):
            if w is None:
                continue
            try:
                w.bind("<Enter>", self._on_enter, add="+")
                w.bind("<Leave>", self._on_leave, add="+")
            except Exception:  # noqa: BLE001
                pass
        self._schedule_visibility()

    # ------------------------------------------------------------------
    # 样式
    # ------------------------------------------------------------------
    def _apply_style(self) -> None:
        """把宽度 / 形状 / 常态配色一次性压到内部滚动条上。"""
        bar = self._bar
        if bar is None:
            return
        try:
            bar.configure(width=theme.SCROLLBAR_WIDTH,
                           corner_radius=theme.SCROLLBAR_RADIUS,
                           border_spacing=theme.SCROLLBAR_SPACING)
        except Exception:  # noqa: BLE001
            pass
        # 旧版 CTk 的滚动条是 Canvas，内部矩形靠这两个方法重算（6.0.0 已无，
        # 留着做低版本兼容：缺了就跳过）
        for meth in ("_set_scrollbar_size", "_set_scrollbar_position"):
            fn = getattr(bar, meth, None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
        self._paint(self._hovering)

    def _paint(self, hover: bool) -> None:
        key = "scroll_thumb_hover" if hover else "scroll_thumb"
        bar = self._bar
        if bar is None:
            return
        try:
            bar.configure(button_color=theme.pair(key))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 动态显隐
    # ------------------------------------------------------------------
    @property
    def bar_shown(self) -> bool:
        """滚动条当前是否可见（探针/预览场景按它断言）。"""
        return self._bar_shown

    def _bar_grid_opts(self) -> Dict[str, Any]:
        """复刻 CTkScrollableFrame._create_grid 里给滚动条的格位参数。

        自己记一份参数而不是 ``grid_info()`` 回读 —— 后者把 ``padx`` 回成
        字符串（如 ``"0 1"``），再喂回 ``grid()`` 语义会走样。
        """
        cr = self._parent_frame.cget("corner_radius")
        bw = self._parent_frame.cget("border_width")
        try:
            pady = self._apply_widget_scaling(float(cr) + float(bw))
        except Exception:  # noqa: BLE001
            pady = 0
        return {"row": 1, "column": 1, "sticky": "nsew",
                "padx": (0, self._border_width + 1), "pady": pady}

    def _bind_visibility(self) -> None:
        """内容高度或容器高度一变就重判一次显隐。"""
        canvas = getattr(self, "_parent_canvas", None)
        for w in (self, canvas):
            if w is None:
                continue
            try:
                w.bind("<Configure>", self._schedule_visibility, add="+")
            except Exception:  # noqa: BLE001
                pass

    def _schedule_visibility(self, _event=None) -> None:
        if self._bar_job is not None:
            return
        try:
            self._bar_job = self.after_idle(self._sync_visibility)
        except Exception:  # noqa: BLE001
            self._bar_job = None

    def _sync_visibility(self) -> None:
        """按"内容是否装得下"决定滚动条显隐。"""
        self._bar_job = None
        if self._bar_busy or self._bar is None:
            return
        self._bar_busy = True
        try:
            canvas = getattr(self, "_parent_canvas", None)
            if canvas is None:
                return
            avail = canvas.winfo_height()
            if avail <= 1:                      # 还没完成首次布局，别下结论
                return
            need = self.winfo_reqheight()
            hyst = theme.lpx(theme.SCROLLBAR_HIDE_HYST)
            if self._bar_shown:
                # 已显示：要"确确实实短一截"才收回，防止临界高度横跳
                should = need > avail - hyst
            else:
                should = need > avail
            if should != self._bar_shown:
                self._set_bar_shown(should)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._bar_busy = False

    def _set_bar_shown(self, shown: bool) -> None:
        bar = self._bar
        if bar is None or shown == self._bar_shown:
            return
        try:
            if shown:
                bar.grid(**self._bar_grid_opts())
            else:
                bar.grid_remove()
        except Exception:  # noqa: BLE001
            return
        self._bar_shown = shown

    # ------------------------------------------------------------------
    # 悬停强调
    # ------------------------------------------------------------------
    def _on_enter(self, _e=None) -> None:
        if self._hover_job:
            try:
                self.after_cancel(self._hover_job)
            except Exception:  # noqa: BLE001
                pass
        self._hover_job = self.after(theme.SCROLLBAR_HOVER_MS,
                                     lambda: self._set_hover(True))

    def _on_leave(self, _e=None) -> None:
        if self._hover_job:
            try:
                self.after_cancel(self._hover_job)
            except Exception:  # noqa: BLE001
                pass
        self._hover_job = self.after(theme.SCROLLBAR_IDLE_MS,
                                     lambda: self._set_hover(False))

    def _set_hover(self, hover: bool) -> None:
        self._hover_job = None
        if hover == self._hovering:
            return
        self._hovering = hover
        self._paint(hover)

    # ------------------------------------------------------------------
    # 滚轮（由 widgets._route_wheel 统一调用）
    # ------------------------------------------------------------------
    def scroll_wheel(self, event) -> bool:
        """按滚轮事件滚动自己；**滚动了（或已接管）才返回 True**（决定要不要 break）。

        方向口径（1.5.27 修）
        -------------------
        ``step = -delta / SCROLL_WHEEL_DIVISOR``：
        Windows 上拨 ``delta>0`` → step 为**负** → ``yview_scroll`` 负方向量 →
        视口上移、列表上滚；下拨对称。**严禁 abs**，也**严禁**在 delta 缺省时
        退回常量正数步长 —— 那正是"无论怎么拨都往下滚"的成因（见 wheel_delta）。

        触控板（delta 很小、事件很密）不做阈值截断：把不足 1px 的余量留在
        ``self._wheel_residual`` 里累积，下一次连同新值一起结算。这样"向上拨"
        永远攒出**负**步、"向下拨"永远攒出**正**步，不会被归一成同一个方向。
        """
        canvas = getattr(self, "_parent_canvas", None)
        if canvas is None:
            return False
        try:
            if canvas.yview() == (0.0, 1.0):
                self._wheel_residual = 0.0
                return False                 # 内容装得下，滚无可滚
        except Exception:  # noqa: BLE001
            return False
        delta = wheel_delta(event)
        if not delta:
            return False
        want = -delta / theme.SCROLL_WHEEL_DIVISOR + self._wheel_residual
        step = int(want)                     # 向零截断 —— 保留符号是关键
        self._wheel_residual = want - step
        if step == 0:
            # 命中滚动区却不足 1px：仍然吃掉事件（返回 True → 路由 break），
            # 免得再被别的处理器按相反方向处理一遍。
            return True
        try:
            # ⚠️ Canvas 的 yview_scroll 只认 "units"/"pages" —— "pixels" 是
            # Text/Listbox/Entry 的写法，喂给 Canvas 会抛
            # ``TclError: bad argument "pixels": must be units or pages``
            # （异常被吞 → 表现为"滚轮毫无反应"）。本画布 yscrollincrement=1，
            # 所以 1 unit == 1 像素，units 就是像素口径。
            canvas.yview_scroll(step, "units")
        except Exception:  # noqa: BLE001
            return False
        return True

    def refresh_theme(self) -> None:
        """换肤后重设滑块配色（浅/深两档颜色都要重取）。"""
        self._apply_style()
        self._schedule_visibility()
