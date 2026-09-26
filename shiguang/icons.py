# -*- coding: utf-8 -*-
"""自绘矢量风格图标系统。

为什么不用 emoji（需求 14）
--------------------------
1. **颜色不可控**。emoji 由系统字体渲染，颜色由字体决定 —— ``📅`` 永远是系统配色，
   落不进"柔橘 #E89A4A"这种品牌色。要求里的配色方案用 emoji 根本实现不了。
2. **尺寸与基线不可控**。emoji 在不同字体下视觉大小差很多，且带内置 padding，
   左边靠齐时会对不齐。
3. **渲染依赖系统**。缺少对应字体时出现豆腐块。

所以全部改为 **PIL ImageDraw 绘制 → PhotoImage 常驻内存复用**。

画法约定（需求 17：整层暖色渐变保证风格统一）
--------------------------------------------
* 统一超采样 4x 再 LANCZOS 缩小，边缘平滑（tkinter 不做抗锯齿）；
* 每个图标自下而上叠一层**暖色半透明渐变**（顶部偏柔橘、底部偏金），
  这样即使底色不同，整套图标也有统一的"暖"感；
* 轮廓统一用圆头线（``ImageDraw.line(..., joint="curve")`` + 端点补圆），
  避免尖角；
* 尺寸档位固定为 18 / 16 / 14（需求 13/16），按需生成并缓存。

缓存策略
--------
``_CACHE[(name, size, mode)] -> tk.PhotoImage``。PhotoImage 必须被持有引用，
否则会被 GC 掉导致图标变空白 —— 缓存在模块级正好解决这个问题。
"""

from __future__ import annotations

import logging
import math
import tkinter as tk
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk

try:                                    # Pillow 是硬依赖，但不该在导入期就炸掉整个应用
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk
    _PIL_OK = True
except Exception:                       # noqa: BLE001
    Image = ImageChops = ImageDraw = ImageFilter = ImageTk = None  # type: ignore[assignment]
    _PIL_OK = False

log = logging.getLogger("shiguang.icons")

# ---- 尺寸档位（需求 13/16）----
SIZE_MAIN = 18        # 常规图标
SIZE_GROUP = 16       # 分组标题图标
SIZE_SMALL = 14       # 紧凑场景
SIZES = (SIZE_MAIN, SIZE_GROUP, SIZE_SMALL)

SUPERSAMPLE = 4


def ctk_source_px(size: int) -> int:
    """``CTkImage`` 的源图该按多少**物理像素**出图。

    ``CTkImage`` 上屏时会把源图 resize 到 ``size × widget_scaling``。
    源图给逻辑尺寸就等于让它放大 —— 边缘全是硬块（"像素感"的来源）。
    这里按缩放系数出图，那一步就退化成 1:1。
    """
    try:
        from . import theme
        scale = theme.scale()
    except Exception:  # noqa: BLE001
        scale = 1.0
    scale = scale if scale and scale > 0 else 1.0
    return max(int(size), int(round(size * scale)))

# ---- 品牌暖色（需求 15）----
ORANGE = "#E89A4A"        # 柔橘
GOLD = "#E8B84A"          # 暖阳金
DEEP_GOLD = "#D4B872"     # 淡金
VERMILION = "#E0553F"     # 番茄钟朱红
RED = "#C0392B"           # 优先级高
BLUE = "#6B93C4"          # 编辑：柔和蓝
SALMON = "#C97B6B"        # 删除：柔和红
BROWN = "#6B5F52"         # 更多选项：深灰棕
WARM_BROWN = "#B08D57"    # 分组·工作：袋身暖棕
WARM_CLASP = "#D4B887"    # 分组·工作：搭扣淡金
SOFT_BLUE = "#7BA3C9"     # 分组·学习：封面柔和蓝
PAGE_BLUE = "#A8C4DE"     # 分组·学习：书页浅蓝
GRASS = "#8FB887"         # 分组·生活：主叶草绿
GRASS_LIGHT = "#C2DDBE"   # 分组·生活：浅叶
LEAF = "#5C9E4A"          # 番茄叶子

# 内阴影强度（需求 17：右下叠半透明黑、左上叠半透明白，做出微立体感）
INNER_SHADOW_DARK = 0.16  # 右下角黑色叠加比例
INNER_SHADOW_LIGHT = 0.22 # 左上角白色叠加比例

# 分组图标候选（key -> 中文名），供"更换图标"浮层使用。
# 需求 26 指定的 12 个：公文包 / 书本 / 叶子 / 太阳 / 星星 / 咖啡 / 灯泡 / 心 / 旗 / 铃 / 笔 / 房子。
# 数组顺序 = 浮层网格的排布顺序（4 列 × 3 行）。
GROUP_ICON_CHOICES: List[Tuple[str, str]] = [
    ("grp_work", "公文包"),
    ("grp_study", "书本"),
    ("grp_life", "叶子"),
    ("grp_sun", "太阳"),
    ("grp_star", "星星"),
    ("grp_tea", "咖啡"),
    ("grp_idea", "灯泡"),
    ("grp_heart", "心"),
    ("grp_flag", "旗"),
    ("grp_bell", "铃"),
    ("grp_pen", "笔"),
    ("grp_home", "房子"),
]

#: 分组图标键的合法集合（写入前的白名单；不在里面的值会被拒绝）
GROUP_ICON_KEYS: Tuple[str, ...] = tuple(k for k, _ in GROUP_ICON_CHOICES)

#: 名字 → 键，便于按中文名反查（测试与旧数据迁移用）
GROUP_ICON_BY_NAME: Dict[str, str] = {v: k for k, v in GROUP_ICON_CHOICES}

#: 键 → 展示名（浮层悬停提示 / 测试断言用）
GROUP_ICON_LABELS: Dict[str, str] = dict(GROUP_ICON_CHOICES)

_CACHE: Dict[Tuple[str, int, str], tk.PhotoImage] = {}


def _rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _warm_overlay(draw, size: int, strength: float = 0.18) -> None:
    """叠一层自上而下的暖色渐变（柔橘 → 金），统一整套图标的调性。

    用逐行画横线实现渐变（PIL 的 ImageDraw 没有原生渐变填充）。
    只在**已有的不透明像素**上叠加，所以不会把图标画成方块。
    """
    top = _rgb(ORANGE)
    bottom = _rgb(GOLD)
    for y in range(size):
        t = y / max(1, size - 1)
        color = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
            int(255 * strength),
        )
        draw.line((0, y, size, y), fill=color)


def _new_canvas(size: int):
    return Image.new("RGBA", (size, size), (0, 0, 0, 0))


def _inner_shadow(img, dark: float = INNER_SHADOW_DARK,
                  light: float = INNER_SHADOW_LIGHT):
    """给图形叠一层"内阴影"，做出微立体感（需求 17）。

    做法不是模糊，而是**方向性着色**：造两张纯色图，一张黑一张白，各自用
    对角渐变当遮罩（右下偏黑、左上偏白），再用图形自身的 alpha 裁一刀，
    最后叠回原图。这样只有"图形内部"被染色，边缘不会脏。

    为什么不直接 ``ImageFilter.GaussianBlur`` 做真内阴影：
    22px 的图标上，模糊半径小于 1px，视觉上等于没做；反而会让
    描边细节糊掉。对角着色在小尺寸下才有可辨识的立体感。
    """
    if not _PIL_OK:
        return img
    try:
        size = img.size[0]
        alpha = img.getchannel("A")

        # 左上 → 右下 的对角渐变（0 在左上，1 在右下）
        diag = Image.new("L", (size, size))
        px = diag.load()
        denom = max(1, (size - 1) * 2)
        for y in range(size):
            for x in range(size):
                px[x, y] = int(255 * ((x + y) / denom))

        def shade(rgb, weight: float):
            """把 rgb 按 weight 铺满整张画布，再按对角渐变决定每处的浓度。"""
            layer = Image.new("RGB", (size, size), rgb)
            mask = diag.point(lambda v, w=weight: int(v * w))
            return layer, mask

        dark_layer, dark_mask = shade((0, 0, 0), dark)
        light_layer, light_mask = shade((255, 255, 255), light)

        out = img.copy()
        # 右下：黑色（遮罩 = 对角渐变 × 图形 alpha）
        out.paste(dark_layer, (0, 0),
                  ImageChops.multiply(dark_mask, alpha))
        # 左上：白色（遮罩 = 反相对角渐变 × 图形 alpha）
        out.paste(light_layer, (0, 0),
                  ImageChops.multiply(ImageChops.invert(diag).point(
                      lambda v: int(v * light)), alpha))
        return out
    except Exception:  # noqa: BLE001
        return img


def _ellipse_img(size: int, box, fill):
    """画一个抗锯齿椭圆（需求 14：用 ellipse() 而不是矩形切角）。

    超采样在这里已经由 ``_render_pil`` 保证（4x 画布 → LANCZOS 缩到目标），
    所以本函数只管在**大画布**上把形状画对，不需要自己再做一遍抗锯齿。
    """
    layer = _new_canvas(size)
    ImageDraw.Draw(layer).ellipse(box, fill=_rgb(fill) + (255,))
    return layer


def _rounded_rect(draw, box, radius: int, fill=None, outline=None, width=1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _line(draw, points, color, width: int, rounded: bool = True) -> None:
    """画一条线；rounded=True 时在两端补圆点，做出圆头效果。"""
    draw.line(points, fill=color, width=width, joint="curve")
    if rounded:
        r = width / 2
        for (x, y) in (points[0], points[-1]):
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


# --------------------------------------------------------------------------
# 各图标的绘制函数：入参为超采样后的 size（s），返回无渐变的 RGBA 图
# --------------------------------------------------------------------------
def _draw_check(size: int):
    """勾选框：柔橘→金渐变圆角方 + 白色对勾。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    pad = size * 0.10
    _rounded_rect(d, (pad, pad, size - pad, size - pad), int(size * 0.30),
                  fill=_rgb(ORANGE) + (255,))
    # 对角渐变（左上柔橘 → 右下金）
    top, bot = _rgb(ORANGE), _rgb(GOLD)
    grad = _new_canvas(size)
    gd = ImageDraw.Draw(grad)
    for i in range(size):
        t = i / max(1, size - 1)
        color = (int(top[0] + (bot[0] - top[0]) * t),
                 int(top[1] + (bot[1] - top[1]) * t),
                 int(top[2] + (bot[2] - top[2]) * t), 200)
        gd.line((i, 0, i, size), fill=color)
    mask = _new_canvas(size)
    md = ImageDraw.Draw(mask)
    _rounded_rect(md, (pad, pad, size - pad, size - pad), int(size * 0.30),
                  fill=(255, 255, 255, 255))
    img.paste(grad, (0, 0), mask)
    # 对勾
    w = max(2, int(size * 0.13))
    _line(d, [(size * 0.30, size * 0.52), (size * 0.44, size * 0.67),
              (size * 0.71, size * 0.35)], (255, 255, 255, 255), w)
    return img


def _draw_calendar(size: int, color: str = ORANGE):
    """日历：描边圆角矩形 + 两个挂环 + 一条横线。

    ``color`` 可传：默认柔橘（需求 15），提醒条上用纯白（压在饱和底色上）。
    """
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(color) + (255,)
    w = max(1, int(size * 0.075))
    box = (size * 0.14, size * 0.22, size * 0.86, size * 0.86)
    _rounded_rect(d, box, int(size * 0.16), outline=color, width=w)
    # 挂环
    for x in (size * 0.34, size * 0.66):
        _line(d, [(x, size * 0.12), (x, size * 0.29)], color, w)
    # 表头横线
    y = size * 0.42
    _line(d, [(size * 0.16, y), (size * 0.84, y)], color, w, rounded=False)
    # 三个小点，暗示"日期"
    dot_r = size * 0.045
    for x in (size * 0.33, size * 0.5, size * 0.67):
        d.ellipse((x - dot_r, size * 0.60 - dot_r, x + dot_r, size * 0.60 + dot_r),
                  fill=color)
    return img


def _draw_pomodoro(size: int):
    """番茄钟：朱红圆角番茄 + 两片小叶子（需求 15）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    body = _rgb(VERMILION) + (255,)
    # 番茄主体（略扁的圆）
    d.ellipse((size * 0.13, size * 0.26, size * 0.87, size * 0.92), fill=body)
    # 顶部高光，做出圆润感
    d.ellipse((size * 0.28, size * 0.36, size * 0.52, size * 0.54),
              fill=_rgb("#F08070") + (150,))
    # 两片叶子
    leaf = _rgb(LEAF) + (255,)
    d.polygon([(size * 0.50, size * 0.27),
               (size * 0.34, size * 0.13),
               (size * 0.45, size * 0.29)], fill=leaf)
    d.polygon([(size * 0.50, size * 0.27),
               (size * 0.66, size * 0.13),
               (size * 0.55, size * 0.29)], fill=leaf)
    # 果柄
    _line(d, [(size * 0.50, size * 0.19), (size * 0.50, size * 0.30)], leaf,
          max(1, int(size * 0.07)))
    return img


def _draw_more(size: int):
    """更多选项：三个竖直圆点，深灰棕。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(BROWN) + (255,)
    r = size * 0.085
    cx = size / 2
    for cy in (size * 0.24, size * 0.5, size * 0.76):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    return img


def _draw_edit(size: int):
    """编辑：柔和蓝铅笔。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(BLUE) + (255,)
    w = max(2, int(size * 0.12))
    _line(d, [(size * 0.28, size * 0.72), (size * 0.70, size * 0.30)], color, w)
    # 笔尖
    d.polygon([(size * 0.20, size * 0.80), (size * 0.26, size * 0.62),
               (size * 0.38, size * 0.74)], fill=color)
    # 底横线
    _line(d, [(size * 0.24, size * 0.88), (size * 0.78, size * 0.88)], color,
          max(1, int(size * 0.07)))
    return img


def _draw_delete(size: int):
    """删除：柔和红垃圾桶。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(SALMON) + (255,)
    w = max(1, int(size * 0.085))
    # 桶身
    _rounded_rect(d, (size * 0.26, size * 0.32, size * 0.74, size * 0.86),
                  int(size * 0.10), outline=color, width=w)
    # 桶盖
    _line(d, [(size * 0.17, size * 0.28), (size * 0.83, size * 0.28)], color, w)
    # 提手
    _line(d, [(size * 0.40, size * 0.28), (size * 0.40, size * 0.18),
              (size * 0.60, size * 0.18), (size * 0.60, size * 0.28)], color, w)
    # 两条内纹
    for x in (size * 0.42, size * 0.58):
        _line(d, [(x, size * 0.44), (x, size * 0.74)], color,
              max(1, int(size * 0.06)))
    return img


def _draw_move(size: int):
    """移动到分组：向右箭头。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(ORANGE) + (255,)
    w = max(2, int(size * 0.10))
    _line(d, [(size * 0.18, size * 0.50), (size * 0.72, size * 0.50)], color, w)
    d.polygon([(size * 0.68, size * 0.34), (size * 0.86, size * 0.50),
               (size * 0.68, size * 0.66)], fill=color)
    return img


def _draw_plus(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(ORANGE) + (255,)
    w = max(2, int(size * 0.11))
    c = size / 2
    _line(d, [(c, size * 0.20), (c, size * 0.80)], color, w)
    _line(d, [(size * 0.20, c), (size * 0.80, c)], color, w)
    return img


def _draw_chevron(size: int, up: bool = False, color: str = BROWN,
                  width_ratio: float = 0.11, direction: str = "down"):
    """V 形箭头。

    ``direction``：``"down"`` / ``"up"`` / ``"right"``。
    几处用到它：任务卡片的折叠按钮（朝下）、分组卡片折叠后（朝右）、
    提醒条的展开指示（展开后朝上）。方向和颜色都要能传，所以做成一个函数。
    """
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(color) + (255,)
    w = max(2, int(size * width_ratio))
    if direction == "up" or up:
        pts = [(size * 0.30, size * 0.62), (size * 0.50, size * 0.40),
               (size * 0.70, size * 0.62)]
    elif direction == "right":
        pts = [(size * 0.40, size * 0.28), (size * 0.62, size * 0.50),
               (size * 0.40, size * 0.72)]
    else:
        pts = [(size * 0.30, size * 0.40), (size * 0.50, size * 0.62),
               (size * 0.70, size * 0.40)]
    _line(d, pts, c, w)
    return img


def _draw_star(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(GOLD) + (255,)
    cx = cy = size / 2
    outer, inner = size * 0.40, size * 0.17
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        r = outer if i % 2 == 0 else inner
        pts.append((cx + math.cos(ang) * r, cy + math.sin(ang) * r))
    d.polygon(pts, fill=color)
    return img


def _draw_toast_sparkle(size: int):
    """完成提示专用的四芒星：高对比填色和细描边，缩小时仍清晰。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    # 用 4x 超采样画出柔和的尖芒和内收弧度，避免依赖系统字体字形。
    outer = size * 0.45
    inner = size * 0.12
    pts = []
    for i in range(8):
        angle = -math.pi / 2 + i * math.pi / 4
        radius = outer if i % 2 == 0 else inner
        pts.append((cx + math.cos(angle) * radius,
                    cy + math.sin(angle) * radius))
    gold = _rgb(GOLD) + (255,)
    outline = _rgb(ORANGE) + (255,)
    d.polygon(pts, fill=gold, outline=outline, width=max(1, int(size * 0.035)))
    return img


def _draw_leaf(size: int, color: str = GRASS):
    """两片叶子（生活分组）—— 1.5.12 重绘为细线描边风。

    保留"后浅前深"的双叶构图，但叶子从实心椭圆改为**纺锤轮廓 + 中脉**：
    细线勾边、内部只铺薄纱，留白多、与整套线性图标统一。
    """
    img = _new_canvas(size)
    s = size
    stroke = max(2, int(s * 0.06))
    cx = cy = s / 2.0
    ang = 42.0
    main_c = _rgb(color) + (255,)
    d = ImageDraw.Draw(img)

    def leaf(cx_: float, cy_: float, length: float, width: float,
             line_c, fill_a: int) -> None:
        loop = _leaf_outline_points(length, width)
        cos_t, sin_t = _leaf_rot(ang)
        pts = [(cx_ + lx * cos_t - ly * sin_t,
                cy_ + lx * sin_t + ly * cos_t) for lx, ly in loop]
        if fill_a:
            d.polygon(pts, fill=_rgb(color) + (fill_a,))
        _line(d, pts + [pts[0]], line_c, stroke)

    # 后叶（浅色细线，偏左上）
    leaf(cx - s * 0.13, cy - s * 0.10, s * 0.56, s * 0.14,
         _rgb(GRASS_LIGHT) + (235,), 40)
    # 前叶（主色，偏右下）+ 中脉
    leaf(cx + s * 0.09, cy + s * 0.07, s * 0.62, s * 0.16, main_c, 48)
    a = math.radians(ang)
    _line(d,
          [(cx + s * 0.09 - math.cos(a) * s * 0.24,
            cy + s * 0.07 - math.sin(a) * s * 0.24),
           (cx + s * 0.09 + math.cos(a) * s * 0.24,
            cy + s * 0.07 + math.sin(a) * s * 0.24)],
          _rgb(GRASS_LIGHT) + (190,), max(1, int(s * 0.04)))
    return img


def _leaf_outline_points(length: float, width: float, n: int = 26):
    """纺锤叶局部坐标点集（长轴沿 x，未旋转）。"""
    up, dn = [], []
    for i in range(n + 1):
        t = i / n
        lx = (t - 0.5) * length
        ly = width * math.sin(math.pi * t)
        up.append((lx, ly))
        dn.append((lx, -ly))
    return up + dn[::-1]


def _leaf_rot(angle_deg: float):
    return math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))


def _draw_briefcase(size: int, color: str = WARM_BROWN):
    """公文包（工作分组）—— 1.5.12 重绘为**细线描边**风。

    用户反馈旧实心版"质感简陋、缺乏细节"。新画法与任务状态图标（SunCheck
    细线圆环）同一语言：圆头细线 + 极淡的同色填充给体量 + 大留白：
    * 提手：淡金细线，圆头转折；
    * 袋身：暖棕细线圆角矩形，内部铺 ~8% 同色填充（不是实心色块）；
    * 搭扣：一枚淡金小圆点，点睛即可，不再画整条搭扣带。
    竖直范围 0.26 → 0.78，重心居中，四周留白均匀。
    （`color` 参数保留：历史调用点会传 ``WARM_BROWN``，语义不变。）
    """
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    stroke = max(2, int(s * 0.06))
    body = _rgb(color) + (255,)
    clasp = _rgb(WARM_CLASP) + (255,)
    faint = _rgb(color) + (44,)          # ≈17% alpha 的同色体量
    # 提手（先画，被袋身描边压住下端）
    _line(d, [(s * 0.37, s * 0.42), (s * 0.37, s * 0.27),
              (s * 0.63, s * 0.27), (s * 0.63, s * 0.42)],
          clasp, stroke)
    # 袋身：淡填充 + 细描边
    _rounded_rect(d, (s * 0.17, s * 0.40, s * 0.83, s * 0.78),
                  int(s * 0.09), fill=faint, outline=body, width=stroke)
    # 中央搭扣点
    r = s * 0.042
    d.ellipse((s * 0.5 - r, s * 0.545 - r, s * 0.5 + r, s * 0.545 + r),
              fill=clasp)
    return img


def _draw_book(size: int, color: str = SOFT_BLUE):
    """摊开的书（学习分组）—— 1.5.12 重绘为细线描边风。

    左右两页只铺一层浅蓝薄纱（≈27% alpha）暗示"摊开的页面"，
    轮廓用柔和蓝细线沿页缘走一圈（圆头转折），中缝一条淡金细线。
    竖直范围 0.20 → 0.78，重心 0.49。
    """
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    stroke = max(2, int(s * 0.06))
    line_c = _rgb(color) + (255,)
    page_faint = _rgb(PAGE_BLUE) + (70,)
    # 左右页薄纱填充（先铺，描边最后压上去）
    d.polygon([(s * 0.15, s * 0.29), (s * 0.485, s * 0.22),
               (s * 0.485, s * 0.76), (s * 0.15, s * 0.70)], fill=page_faint)
    d.polygon([(s * 0.85, s * 0.29), (s * 0.515, s * 0.22),
               (s * 0.515, s * 0.76), (s * 0.85, s * 0.70)], fill=page_faint)
    # 左页轮廓：底外 → 顶外 → 中缝顶 → 中缝底 → 合口
    _line(d, [(s * 0.15, s * 0.70), (s * 0.15, s * 0.29), (s * 0.485, s * 0.22),
              (s * 0.485, s * 0.76), (s * 0.15, s * 0.70)], line_c, stroke)
    # 右页轮廓
    _line(d, [(s * 0.85, s * 0.70), (s * 0.85, s * 0.29), (s * 0.515, s * 0.22),
              (s * 0.515, s * 0.76), (s * 0.85, s * 0.70)], line_c, stroke)
    # 书脊（淡金，压在两页之间）
    _line(d, [(s * 0.50, s * 0.22), (s * 0.50, s * 0.76)],
          _rgb(WARM_CLASP) + (255,), max(2, int(s * 0.05)))
    return img


def _draw_home(size: int):
    """房子（居家分组）：暖棕屋顶 + 淡金色门。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    roof = _rgb(WARM_BROWN) + (255,)
    d.polygon([(s * 0.50, s * 0.14), (s * 0.92, s * 0.48), (s * 0.08, s * 0.48)],
              fill=roof)
    _rounded_rect(d, (s * 0.20, s * 0.46, s * 0.80, s * 0.86),
                  max(1, int(s * 0.06)), fill=roof)
    _rounded_rect(d, (s * 0.42, s * 0.60, s * 0.58, s * 0.86),
                  max(1, int(s * 0.04)), fill=_rgb(WARM_CLASP) + (255,))
    return img


def _draw_sun(size: int):
    """太阳（可与"今天"呼应）：金色圆盘 + 柔橘光芒。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    cx = cy = s / 2.0
    for i in range(8):
        ang = math.radians(i * 45 + 22.5)
        r0, r1 = s * 0.31, s * 0.45
        _line(d, [(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0),
                  (cx + math.cos(ang) * r1, cy + math.sin(ang) * r1)],
              _rgb(ORANGE) + (255,), max(1, int(s * 0.085)))
    r = s * 0.26
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgb(GOLD) + (255,))
    return img


def _draw_heart(size: int):
    """心形（柔性任务）：参数方程描点，避免两块圆 + 三角拼出硬接口。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    cx, cy = s / 2.0, s * 0.46
    k = s * 0.026
    pts = []
    for i in range(72):
        t = math.radians(i * 5)
        x = 16 * math.sin(t) ** 3
        y = (13 * math.cos(t) - 5 * math.cos(2 * t)
             - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * k, cy - y * k))
    d.polygon(pts, fill=_rgb("#D98A7A") + (255,))
    return img


def _draw_flag(size: int):
    """旗子（里程碑）：暖棕旗杆 + 柔和蓝旗面。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    _line(d, [(s * 0.26, s * 0.16), (s * 0.26, s * 0.86)],
          _rgb(WARM_BROWN) + (255,), max(1, int(s * 0.075)))
    d.polygon([(s * 0.30, s * 0.20), (s * 0.82, s * 0.30),
               (s * 0.70, s * 0.42), (s * 0.82, s * 0.54),
               (s * 0.30, s * 0.56)], fill=_rgb(SOFT_BLUE) + (255,))
    return img


def _draw_bell(size: int):
    """铃铛（提醒）：金色钟体 + 淡金钟锤。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    body = _rgb(GOLD) + (255,)
    d.polygon([(s * 0.22, s * 0.68), (s * 0.26, s * 0.44),
               (s * 0.50, s * 0.20), (s * 0.74, s * 0.44),
               (s * 0.78, s * 0.68)], fill=body)
    d.ellipse((s * 0.18, s * 0.60, s * 0.82, s * 0.76), fill=body)
    d.ellipse((s * 0.42, s * 0.76, s * 0.58, s * 0.90),
              fill=_rgb(WARM_CLASP) + (255,))
    d.ellipse((s * 0.45, s * 0.10, s * 0.55, s * 0.20),
              fill=_rgb(WARM_CLASP) + (255,))
    return img


def _draw_pen(size: int):
    """笔（笔记）：暖棕笔杆 + 淡金笔尖。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    s = size
    d.polygon([(s * 0.70, s * 0.14), (s * 0.86, s * 0.30),
               (s * 0.36, s * 0.80), (s * 0.20, s * 0.64)],
              fill=_rgb(WARM_BROWN) + (255,))
    d.polygon([(s * 0.30, s * 0.74), (s * 0.20, s * 0.64),
               (s * 0.14, s * 0.86)], fill=_rgb(WARM_CLASP) + (255,))
    return img


def _draw_target(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(RED) + (255,)
    w = max(1, int(size * 0.09))
    r = size * 0.38
    cx = cy = size / 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=c, width=w)
    d.ellipse((cx - r * 0.45, cy - r * 0.45, cx + r * 0.45, cy + r * 0.45),
              outline=c, width=w)
    d.ellipse((cx - r * 0.13, cy - r * 0.13, cx + r * 0.13, cy + r * 0.13), fill=c)
    return img


def _draw_bulb(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(GOLD) + (255,)
    r = size * 0.30
    cx, cy = size / 2, size * 0.42
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c)
    _rounded_rect(d, (cx - size * 0.13, size * 0.66, cx + size * 0.13, size * 0.86),
                  int(size * 0.05), fill=_rgb(BROWN) + (255,))
    return img


def _draw_palette(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(ORANGE) + (255,)
    d.ellipse((size * 0.10, size * 0.14, size * 0.90, size * 0.86), fill=c)
    for (px, py, col) in ((0.34, 0.38, RED), (0.58, 0.32, SOFT_BLUE),
                          (0.68, 0.56, GRASS), (0.42, 0.62, GOLD)):
        r = size * 0.07
        d.ellipse((size * px - r, size * py - r, size * px + r, size * py + r),
                  fill=_rgb(col) + (255,))
    return img


def _draw_music(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(SOFT_BLUE) + (255,)
    w = max(2, int(size * 0.09))
    _line(d, [(size * 0.40, size * 0.72), (size * 0.40, size * 0.24),
              (size * 0.76, size * 0.16), (size * 0.76, size * 0.64)], c, w)
    r = size * 0.11
    d.ellipse((size * 0.29, size * 0.72 - r, size * 0.29 + 2 * r, size * 0.72 + r), fill=c)
    d.ellipse((size * 0.65, size * 0.64 - r, size * 0.65 + 2 * r, size * 0.64 + r), fill=c)
    return img


def _draw_sport(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(GRASS) + (255,)
    w = max(2, int(size * 0.10))
    _line(d, [(size * 0.22, size * 0.42), (size * 0.78, size * 0.58)], c, w)
    r = size * 0.14
    for (px, py) in ((0.26, 0.40), (0.74, 0.60)):
        d.ellipse((size * px - r, size * py - r, size * px + r, size * py + r),
                  outline=c, width=max(1, int(size * 0.07)))
    return img


def _draw_tea(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(WARM_BROWN) + (255,)
    w = max(1, int(size * 0.08))
    _rounded_rect(d, (size * 0.20, size * 0.42, size * 0.72, size * 0.82),
                  int(size * 0.10), outline=c, width=w)
    _line(d, [(size * 0.72, size * 0.50), (size * 0.86, size * 0.55),
              (size * 0.86, size * 0.68), (size * 0.72, size * 0.74)], c, w)
    for x in (size * 0.38, size * 0.52):
        _line(d, [(x, size * 0.32), (x + size * 0.04, size * 0.20)], c,
              max(1, int(size * 0.06)))
    return img


def _draw_moon(size: int):
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(SOFT_BLUE) + (255,)
    d.ellipse((size * 0.18, size * 0.12, size * 0.82, size * 0.88), fill=c)
    d.ellipse((size * 0.36, size * 0.04, size * 1.00, size * 0.80),
              fill=(0, 0, 0, 0))
    # 用背景色抠出月牙（画透明需要用 mask）
    return img


def _draw_nav_moon(size: int, filled: bool = False):
    """今日（夜间）：月牙（选中=实心柔橘，未选中=描边）。

    用 mask 相减裁出月牙：先在 mask 上画一个满圆，再叠一个偏移的
    "减圆"（填 0），得到的 mask 就是月牙形状；再拿它去贴前景色。
    直接 ``fill=(0,0,0,0)`` 在 alpha 合成里是"叠加"而不是"挖掉"，
    所以必须走 mask。
    """
    img = _new_canvas(size)
    color = _rgb(ORANGE) + (255,)

    # 月牙遮罩：满圆 - 偏右上的圆
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((size * 0.14, size * 0.10, size * 0.86, size * 0.90), fill=255)
    md.ellipse((size * 0.42, size * 0.00, size * 1.14, size * 0.82), fill=0)

    if filled:
        # 实心：把月牙直接贴成柔橘
        layer = Image.new("RGBA", (size, size), color)
        img.paste(layer, (0, 0), mask)
        return img

    # 描边：在遮罩上画一圈"月牙轮廓"。
    # 做法：把遮罩整体放大一点点再与原遮罩相减是唯一稳的路；
    # 这里改用更直观的方式 —— 直接在遮罩边界上用 MinFilter 腐蚀出内芯，
    # 两者相减即得均匀的月牙描边。
    inner = mask.filter(ImageFilter.MinFilter(3))
    ring = ImageChops.subtract(mask, inner)
    # MinFilter 在低分辨率下可能过细，做一次膨胀保证可见
    ring = ring.filter(ImageFilter.MaxFilter(3))
    layer = Image.new("RGBA", (size, size), color)
    img.paste(layer, (0, 0), ring)
    return img


# 注：早期还有一个 ``_draw_dot``（圆点 + 光晕）给优先级圆点用，本轮已删。
# 那个圆点只有 8px、又带一圈半透明光晕，在任务卡片右侧看着就是"一粒多余的
# 橙色像素点"（跟旁边的番茄钟图标连在一起，很像没画完的占位符），
# 而且它表达的信息（优先级）在编辑对话框里已经有了完整呈现，卡片上不必再点一次。


# --------------------------------------------------------------------------
# 窗口控制图标（需求 21）
# --------------------------------------------------------------------------
def _draw_minimize(size: int):
    """最小化：一条柔橘横线（hover 时按钮会加深底色）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(ORANGE) + (255,)
    w = max(2, int(size * 0.11))
    y = size * 0.54
    _line(d, [(size * 0.22, y), (size * 0.78, y)], c, w)
    return img


def _draw_pin(size: int, filled: bool = True):
    """置顶图钉（需求 21：开=柔橘填充 / 关=描边）。

    画法参考真实图钉的侧视剪影，三段式：
      1. **帽沿**——顶部一条横向扁椭圆（宽而薄）；
      2. **颈**——从帽沿向下收窄的梯形；
      3. **针**——细长竖线，末端收尖。

    早期版本把"帽"画成一个大圆、下面直接接针，小尺寸下看着像棒棒糖／
    扫码牌。现在帽沿压扁到约 18% 高度、颈留出过渡，才读得出是图钉。
    填充版用柔橘实心，描边版只画轮廓 —— 光靠颜色深浅区分"开/关"在
    小尺寸下太弱，形状实虚才是可靠信号。
    """
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(ORANGE)
    w = max(1, int(size * 0.09))
    # 1) 帽沿：扁椭圆（宽 52% / 高 18%），位置略靠上
    cap = (size * 0.24, size * 0.12, size * 0.76, size * 0.30)
    if filled:
        d.ellipse(cap, fill=color + (255,))
    else:
        d.ellipse(cap, outline=color + (255,), width=w)
    # 2) 颈：从帽沿下缘向下收窄的梯形
    neck = [(size * 0.36, size * 0.26), (size * 0.64, size * 0.26),
            (size * 0.56, size * 0.60), (size * 0.44, size * 0.60)]
    if filled:
        d.polygon(neck, fill=color + (255,))
    else:
        d.polygon(neck, outline=color + (255,))
    # 3) 针：细长竖线，从颈底伸到接近底边
    _line(d, [(size * 0.50, size * 0.56), (size * 0.50, size * 0.90)],
          color + (255,), max(1, int(size * 0.07)))
    return img


def _draw_maximize(size: int):
    """最大化：一个描边方框（标准 Windows 最大化符号）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(BROWN) + (255,)
    w = max(2, int(size * 0.10))
    box = (size * 0.24, size * 0.24, size * 0.76, size * 0.76)
    d.rectangle(box, outline=c, width=w)
    return img


def _draw_restore(size: int):
    """还原：两个叠放的方框（标准 Windows 还原符号）。

    画法：后面一个只露右上角的两条边，前面一个完整描边 ——
    只画两个完整方框在小尺寸下会糊成一团，露角才读得出"层叠"。
    """
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(BROWN) + (255,)
    w = max(2, int(size * 0.10))
    back = (size * 0.34, size * 0.20, size * 0.80, size * 0.66)
    front = (size * 0.20, size * 0.34, size * 0.66, size * 0.80)
    # 后框：只画上半 + 右半（露角）
    d.line([(back[0], back[3]), (back[0], back[1]), (back[2], back[1]),
            (back[2], back[3])], fill=c, width=w, joint="curve")
    # 前框：完整描边
    d.rectangle(front, outline=c, width=w)
    return img


def _draw_close(size: int):
    """关闭：× 形（hover 时按钮变朱红，由 UI 层控制）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb(BROWN) + (255,)
    w = max(2, int(size * 0.10))
    _line(d, [(size * 0.30, size * 0.30), (size * 0.70, size * 0.70)], c, w)
    _line(d, [(size * 0.70, size * 0.30), (size * 0.30, size * 0.70)], c, w)
    return img


def _draw_close_hover(size: int):
    """关闭按钮的 hover 态：朱红 ×。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    c = _rgb("#C97B6B") + (255,)
    w = max(2, int(size * 0.10))
    _line(d, [(size * 0.30, size * 0.30), (size * 0.70, size * 0.70)], c, w)
    _line(d, [(size * 0.70, size * 0.30), (size * 0.30, size * 0.70)], c, w)
    return img


# --------------------------------------------------------------------------
# 底部导航图标（需求 33：图标 20px，选中态填充柔橘）
# --------------------------------------------------------------------------
def _draw_nav_today(size: int, filled: bool = False):
    """今日：太阳（选中=实心柔橘+光芒，未选中=描边）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(ORANGE)
    r = size * 0.24
    cx = cy = size / 2
    if filled:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (255,))
    else:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color + (255,),
                  width=max(1, int(size * 0.085)))
    # 8 道光芒
    w = max(1, int(size * 0.075))
    for i in range(8):
        ang = math.radians(i * 45)
        r0, r1 = r + size * 0.09, r + size * 0.20
        _line(d, [(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0),
                  (cx + math.cos(ang) * r1, cy + math.sin(ang) * r1)],
              color + (255,), w, rounded=False)
    return img


def _draw_nav_stats(size: int, filled: bool = False):
    """光景：三根渐变柱子（选中=实心柔橘，未选中=描边）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(ORANGE)
    w = max(1, int(size * 0.09))
    bars = [(0.22, 0.60), (0.42, 0.34), (0.62, 0.46)]
    bw = size * 0.14
    for x, top in bars:
        box = (size * x, size * top, size * x + bw, size * 0.82)
        if filled:
            _rounded_rect(d, box, int(bw * 0.34), fill=color + (255,))
        else:
            _rounded_rect(d, box, int(bw * 0.34), outline=color + (255,), width=w)
    return img


def _draw_nav_settings(size: int, filled: bool = False):
    """设置：圆 + 齿轮齿（选中=实心柔橘）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    color = _rgb(ORANGE)
    cx = cy = size / 2
    r_out = size * 0.30
    # 齿轮齿
    for i in range(8):
        ang = math.radians(i * 45)
        rr = size * 0.13
        px = cx + math.cos(ang) * r_out * 1.16
        py = cy + math.sin(ang) * r_out * 1.16
        d.ellipse((px - rr, py - rr, px + rr, py + rr), fill=color + (255,))
    if filled:
        d.ellipse((cx - r_out, cy - r_out, cx + r_out, cy + r_out), fill=color + (255,))
    else:
        d.ellipse((cx - r_out, cy - r_out, cx + r_out, cy + r_out),
                  fill=(0, 0, 0, 0), outline=color + (255,),
                  width=max(1, int(size * 0.09)))
    # 中心孔
    r_in = size * 0.12
    d.ellipse((cx - r_in, cy - r_in, cx + r_in, cy + r_in),
              fill=(0, 0, 0, 0) if filled else (0, 0, 0, 0))
    return img


def _draw_flame(size: int):
    """缕光：一簇小火苗（用于统计页的"缕光"标签）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    d.polygon([(size * 0.50, size * 0.10), (size * 0.76, size * 0.52),
               (size * 0.72, size * 0.80), (size * 0.28, size * 0.80),
               (size * 0.24, size * 0.52)], fill=_rgb(ORANGE) + (255,))
    d.polygon([(size * 0.50, size * 0.40), (size * 0.64, size * 0.66),
               (size * 0.36, size * 0.66)], fill=_rgb(GOLD) + (255,))
    return img


def _draw_warning(size: int):
    """警示：圆角三角 + 感叹号（提醒条用，白色）。"""
    img = _new_canvas(size)
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    d.polygon([(size * 0.50, size * 0.10), (size * 0.94, size * 0.86),
               (size * 0.06, size * 0.86)], fill=white)
    bar = _rgb("#C0392B") + (255,)
    _line(d, [(size * 0.50, size * 0.36), (size * 0.50, size * 0.62)], bar,
          max(2, int(size * 0.10)))
    r = size * 0.055
    d.ellipse((size * 0.50 - r, size * 0.72 - r, size * 0.50 + r, size * 0.72 + r),
              fill=bar)
    return img


# 图标注册表：name -> 绘制函数（入参是超采样尺寸）
_PAINTERS = {
    "check": _draw_check,
    "calendar": _draw_calendar,
    "pomodoro": _draw_pomodoro,
    "more": _draw_more,
    "edit": _draw_edit,
    "delete": _draw_delete,
    "move": _draw_move,
    "plus": _draw_plus,
    "chevron": _draw_chevron,
    "chevron_up": lambda s: _draw_chevron(s, up=True),
    "chevron_right": lambda s: _draw_chevron(s, direction="right"),
    "chevron_white": lambda s: _draw_chevron(s, color="#FFFFFF", width_ratio=0.13),
    "chevron_up_white": lambda s: _draw_chevron(s, up=True, color="#FFFFFF",
                                                width_ratio=0.13),
    "calendar_white": lambda s: _draw_calendar(s, color="#FFFFFF"),
    "star": _draw_star,
    "toast_sparkle": _draw_toast_sparkle,
    "home": _draw_home,
    "goal": _draw_target,
    "idea": _draw_bulb,
    "art": _draw_palette,
    "music": _draw_music,
    "sport": _draw_sport,
    "tea": _draw_tea,
    "night": _draw_moon,
    "grp_work": lambda s: _draw_briefcase(s, WARM_BROWN),
    "grp_study": lambda s: _draw_book(s, SOFT_BLUE),
    "grp_life": lambda s: _draw_leaf(s, GRASS),
    "grp_sun": _draw_sun,
    "grp_heart": _draw_heart,
    "grp_flag": _draw_flag,
    "grp_bell": _draw_bell,
    "grp_pen": _draw_pen,
    # 以下键已不在"更换图标"候选里，但历史数据可能存着，保留绘制能力
    "grp_home": _draw_home,
    "grp_goal": _draw_target,
    "grp_idea": _draw_bulb,
    "grp_art": _draw_palette,
    "grp_music": _draw_music,
    "grp_sport": _draw_sport,
    "grp_tea": _draw_tea,
    "grp_night": _draw_moon,
    "grp_star": _draw_star,
    # ---- 窗口控制（需求 21）----
    "minimize": _draw_minimize,
    "maximize": _draw_maximize,
    "restore": _draw_restore,
    "pin": lambda s: _draw_pin(s, True),
    "pin_off": lambda s: _draw_pin(s, False),
    "close": _draw_close,
    "close_hover": _draw_close_hover,
    # ---- 底部导航（需求 33）----
    "nav_today": lambda s: _draw_nav_today(s, False),
    "nav_today_on": lambda s: _draw_nav_today(s, True),
    "nav_moon": lambda s: _draw_nav_moon(s, False),
    "nav_moon_on": lambda s: _draw_nav_moon(s, True),
    "nav_stats": lambda s: _draw_nav_stats(s, False),
    "nav_stats_on": lambda s: _draw_nav_stats(s, True),
    "nav_settings": lambda s: _draw_nav_settings(s, False),
    "nav_settings_on": lambda s: _draw_nav_settings(s, True),
    # ---- 杂项 ----
    "flame": _draw_flame,
    "warning": _draw_warning,
}

# 把旧数据里的 emoji 图标映射到新的自绘图标
EMOJI_TO_KEY = {
    "💼": "grp_work", "📖": "grp_study", "🌿": "grp_life",
    "🏠": "grp_home", "🎯": "grp_goal", "💡": "grp_idea",
    "🎨": "grp_art", "🎵": "grp_music", "🏃": "grp_sport",
    "🍵": "grp_tea", "🌙": "grp_night", "⭐": "grp_star",
    "📅": "calendar", "⏱": "pomodoro", "🍅": "pomodoro",
    "✏️": "edit", "▾": "chevron", "▸": "chevron", "＋": "plus", "+": "plus",
}

# 分组图标 key -> 展示名（GROUP_ICON_CHOICES 已在上面定义）


def resolve_key(value: str) -> Optional[str]:
    """把任意"图标标识"归一到注册表键名。

    历史数据里 ``group.icon`` 存的是 emoji（``"💼"``），而老的按钮
    ``text`` 传的也可能是 ``"▾"`` 这类符号。两者都不是注册表键，
    直接拿去查会静默返回 ``None``（图标消失且不报错）。

    归一顺序：
      1. 本身就是注册表键 → 原样返回；
      2. 命中 ``EMOJI_TO_KEY`` → 返回映射后的键；
      3. 都不命中 → 返回 ``None``（调用方自行兜底）。
    """
    if not value:
        return None
    if value in _PAINTERS:
        return value
    return EMOJI_TO_KEY.get(value)


def is_night(now=None, dark=None) -> bool:
    """当前是否该显示"月牙"而不是"太阳"。

    两个条件**取或**：

    1. **时间进入夜间**（18:00 ~ 次日 06:00）—— 视觉呼应，也避免深夜打开
       应用时被一轮太阳刺到；
    2. **应用处于深色模式** —— 用户明确要求"黑夜模式把太阳换成月亮"。
       深色主题配太阳在观感上是矛盾的（一轮发光的太阳压在 #2B2825 上很跳），
       换成月牙后整条导航的色温才统一。

    :param dark: 手动指定是否深色模式；``None`` 表示读当前外观模式。
    """
    if dark is None:
        try:
            from .theme import is_dark as _is_dark
            dark = _is_dark()
        except Exception:  # noqa: BLE001
            dark = False
    if dark:
        return True
    import datetime as _dt
    t = now or _dt.datetime.now()
    return t.hour >= 18 or t.hour < 6


def today_icon(filled: bool = False, now=None, dark=None) -> str:
    """按昼夜/深浅色返回"今日"导航项的图标键。"""
    night = is_night(now, dark)
    if night:
        return "nav_moon_on" if filled else "nav_moon"
    return "nav_today_on" if filled else "nav_today"


def _render_pil(name: str, size: int, dark: bool, fade: float = 1.0):
    """绘制并返回 PIL Image（供 PhotoImage 与 CTkImage 两条路复用）。

    :param fade: ``0.0~1.0`` 的可见度。tkinter 控件没有 alpha 通道，
        所以"淡入淡出"只能靠**降低图标自身的不透明度**来模拟 ——
        把原图与卡片底色按比例混合，效果等价于整张图变淡。
        1.0 表示完全可见。
    """
    if not _PIL_OK:
        return None
    painter = _PAINTERS.get(name)
    if painter is None:
        return None

    big = size * SUPERSAMPLE
    try:
        img = painter(big)
    except Exception as exc:  # noqa: BLE001
        log.info("图标 %s 绘制失败：%s", name, exc)
        return None

    # 统一暖色渐变（只叠在已有像素上）
    try:
        overlay = _new_canvas(big)
        _warm_overlay(ImageDraw.Draw(overlay), big,
                      strength=0.22 if dark else 0.16)
        # 关键：必须用底图的 alpha 当遮罩，否则整块方形画布都会被染上
        # 一层淡橘色 —— 图标就变成了"橘色小方块"，贴在白色药丸上非常显眼
        # （截图里三个导航图标背后各有一个浅橘方块，就是这个原因）。
        mask = img.getchannel("A")
        masked = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
        masked.paste(overlay, (0, 0), mask)
        img = Image.alpha_composite(img, masked)
    except Exception:  # noqa: BLE001
        pass

    # 分组图标额外叠一层内阴影（需求 17：微立体感）。
    # 只给 grp_ 前缀的图标做 —— 勾选框/日历这类"功能性图标"要的是扁平清晰，
    # 立体感反而会削弱它们在密集列表里的辨识度。
    if name.startswith("grp_"):
        img = _inner_shadow(img)

    # 淡出：整体乘 alpha（PIL 的 putalpha 会覆盖，这里用点乘保留原 alpha 形状）
    if fade < 1.0:
        try:
            fade = max(0.0, min(1.0, fade))
            alpha = img.getchannel("A").point(lambda v: int(v * fade))
            img.putalpha(alpha)
        except Exception:  # noqa: BLE001
            pass

    return img.resize((size, size), Image.LANCZOS)


def _render(name: str, size: int, dark: bool) -> Optional["tk.PhotoImage"]:
    """绘制一张图标并转成 PhotoImage（Canvas 用）。"""
    img = _render_pil(name, size, dark)
    if img is None:
        return None
    return _to_photo(img)


def _to_photo(img) -> Optional["tk.PhotoImage"]:
    """PIL Image -> tk.PhotoImage。"""
    if ImageTk is not None:
        try:
            return ImageTk.PhotoImage(img)
        except Exception as exc:  # noqa: BLE001
            log.info("ImageTk 转换失败，改用 PPM：%s", exc)
    # 退路：用 PPM 手工构造（不依赖 ImageTk 的 Tk 集成）
    try:
        data = img.convert("RGB").tobytes()
        w, h = img.size
        ppm = b"P6\n%d %d\n255\n" % (w, h) + data
        return tk.PhotoImage(data=ppm, format="PPM")
    except Exception as exc:  # noqa: BLE001
        log.info("图标转为 PhotoImage 失败：%s", exc)
        return None


def get_pil(name: str, size: int, dark: Optional[bool] = None):
    """Return an RGBA icon at a physical-pixel size for offscreen painting."""
    key = resolve_key(name)
    if key is None:
        return None
    if dark is None:
        dark = _is_dark()
    return _render_pil(key, max(1, int(size)), bool(dark))


def get_pil_tinted(name: str, size: int, color: str = "#FFFFFF",
                   dark: Optional[bool] = None):
    """取一张换成指定颜色的 **RGBA 图标**（物理像素，离线绘制用）。

    与 :func:`get_pil` 的区别只有着色：Canvas 位图合成（如顶栏导航整条
    位图化）需要先拿 PIL 图再 tint，而 ``get_tinted`` 只返回 ``PhotoImage``。
    """
    key = resolve_key(name)
    if key is None:
        return None
    if dark is None:
        dark = _is_dark()
    img = _render_pil(key, max(1, int(size)), bool(dark))
    return _tint_pil(img, color) if img is not None else None


def get_pil_chevron_rotated(size: int, angle: float, color: str = "",
                            dark: Optional[bool] = None):
    """Return the same chevron artwork used by the CTk group header."""
    if dark is None:
        dark = _is_dark()
    image = _render_pil("chevron", ctk_source_px(size), bool(dark))
    if image is None:
        return None
    step = round(max(0.0, min(90.0, angle)) / _CHEV_ROT_QUANT) * _CHEV_ROT_QUANT
    if step:
        image = image.rotate(step, resample=Image.BICUBIC)
    return _tint_pil(image, color) if color else image


def get_ctk(name: str, size: int = SIZE_MAIN, dark: Optional[bool] = None):
    """取一张 ``CTkImage``（给 CTk 控件用，可正确参与 HighDPI 缩放）。

    为什么需要这个：``tk.PhotoImage`` 是固定像素图，customtkinter 在
    ``set_widget_scaling`` 生效时无法把它放大 —— 150% 缩放下图标会显得偏小。
    ``CTkImage`` 内部持有 PIL Image 并按缩放因子重新渲染，才是正确做法。
    Canvas 绘制仍然用 ``get()``（CTkImage 不能画在 Canvas 上）。

    ⚠️ 源图必须按**物理像素**渲染（本轮的"像素橙色点"根因）
    -----------------------------------------------------
    ``CTkImage`` 的显示尺寸是：``源图.resize(逻辑尺寸 × widget_scaling)``
    （见 customtkinter 的 ``_get_scaled_light_photo_image``）。如果我们给的
    源图只有**逻辑**尺寸（比如 18px），它就要把它**放大**到 27px 才能上屏 ——
    放大后的图标边缘全是硬块，看起来就是"一粒粒像素"。
    所以这里按 ``size × 缩放系数`` 出图，让 CTkImage 那一步变成 1:1（或缩小），
    边缘才是干净的。
    """
    if not name or not _PIL_OK:
        return None
    key_name = EMOJI_TO_KEY.get(name, name)
    if dark is None:
        dark = _is_dark()
    cache_key = (key_name, int(size), "dark" if dark else "light", "ctk")
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        pil = _render_pil(key_name, ctk_source_px(size), bool(dark))
        if pil is None:
            return None
        image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
    except Exception as exc:  # noqa: BLE001
        log.info("CTkImage 构造失败（%s）：%s", name, exc)
        return None
    _CACHE[cache_key] = image
    return image


def get_ctk_faded(name: str, size: int = SIZE_MAIN, fade: float = 1.0,
                  dark: Optional[bool] = None):
    """取一张按 ``fade`` 调过透明度的 ``CTkImage``（用于 hover 淡入淡出）。

    ``fade`` 量化到 1/12 一档再缓存，避免每帧都重新绘制 —— 6 帧动画
    只会产生 6 张缓存图，不会无限增长。
    """
    if not name or not _PIL_OK:
        return None
    if dark is None:
        dark = _is_dark()
    step = round(max(0.0, min(1.0, fade)) * 12) / 12.0
    if step >= 1.0:
        return get_ctk(name, size, dark)
    key_name = EMOJI_TO_KEY.get(name, name)
    cache_key = (key_name, int(size), "dark" if dark else "light",
                 "fade", step)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        pil = _render_pil(key_name, int(size), bool(dark), fade=step)
        if pil is None:
            return None
        image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
    except Exception as exc:  # noqa: BLE001
        log.info("CTkImage 淡出构造失败（%s）：%s", name, exc)
        return None
    _CACHE[cache_key] = image
    return image


def _tint_pil(img, color: str):
    """把图标的颜色整体换成 ``color``，只保留原来的 alpha 形状。

    用于"选中态 chip"：柔橘底上要白图标。不能靠调 ``text_color`` ——
    那是文字的颜色，图片的颜色是烘进像素里的。
    """
    if img is None or not _PIL_OK:
        return img
    try:
        solid = Image.new("RGBA", img.size, _rgb(color) + (255,))
        solid.putalpha(img.getchannel("A"))
        return solid
    except Exception:  # noqa: BLE001
        return img


def get_ctk_tinted(name: str, size: int = SIZE_MAIN, color: str = "#FFFFFF",
                   dark: Optional[bool] = None):
    """取一张换成指定颜色的 ``CTkImage``（带缓存）。"""
    if not name or not _PIL_OK:
        return None
    key_name = EMOJI_TO_KEY.get(name, name)
    if dark is None:
        dark = _is_dark()
    cache_key = (key_name, int(size), "dark" if dark else "light", "tint", color)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        pil = _render_pil(key_name, ctk_source_px(size), bool(dark))
        pil = _tint_pil(pil, color)
        if pil is None:
            return None
        image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
    except Exception as exc:  # noqa: BLE001
        log.info("CTkImage 着色失败（%s）：%s", name, exc)
        return None
    _CACHE[cache_key] = image
    return image


_CHEV_ROT_QUANT = 22.5    # 旋转角度量化档（150ms/5帧 = 22.5°/帧，缓存不膨胀）


def get_ctk_chevron_rotated(size: int, angle: float, color: str = "",
                            dark: Optional[bool] = None):
    """取一张**旋转了 ``angle`` 度**的折叠箭头 ``CTkImage``（1.5.12）。

    分组折叠/展开的 150ms 旋转动画逐帧取图：``angle=0`` 朝下（▾，展开态），
    ``angle=90`` 朝右（▸，折叠态），中间角度由 PIL ``rotate``（逆时针）插值
    —— 朝下的箭头逆时针转 90° 正好指向右侧。角度量化到 22.5° 一档再缓存，
    一整次动画只产生 5 张图，不会每帧重绘。
    """
    if not _PIL_OK:
        return None
    if dark is None:
        dark = _is_dark()
    step = round(max(0.0, min(90.0, angle)) / _CHEV_ROT_QUANT) * _CHEV_ROT_QUANT
    cache_key = ("chevron_rot", int(size), "dark" if dark else "light",
                 step, color)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        pil = _render_pil("chevron", ctk_source_px(size), bool(dark))
        if pil is None:
            return None
        if step:
            pil = pil.rotate(step, resample=Image.BICUBIC)
        if color:
            pil = _tint_pil(pil, color)
        image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
    except Exception as exc:  # noqa: BLE001
        log.info("旋转箭头构造失败（%s）：%s", angle, exc)
        return None
    _CACHE[cache_key] = image
    return image


def get_tinted(name: str, size: int = SIZE_MAIN, color: str = "#FFFFFF",               dark: Optional[bool] = None):
    """取一张**换成指定颜色**的 ``tk.PhotoImage``（Canvas 用，带缓存）。

    与 :func:`get_ctk_tinted` 的分工：那个给 CTk 控件（返回 ``CTkImage``），
    这个给裸 ``tk.Canvas``（返回 ``PhotoImage``）—— 图标选择浮层里"选中"那一格
    是柔橘实心底，图标必须转成白色才看得见，而 Canvas 画不了 ``CTkImage``。

    ``size`` 请传**物理像素**（Canvas 坐标系就是物理像素）。
    """
    if not name or not _PIL_OK:
        return None
    key_name = EMOJI_TO_KEY.get(name, name)
    if dark is None:
        dark = _is_dark()
    # 缓存键必须和 get_ctk_tinted 的区分开：那边存的是 CTkImage，
    # 这边存的是 PhotoImage，键一撞就会把 CTkImage 从 Canvas 里画（必然失败）。
    cache_key = (key_name, int(size), "dark" if dark else "light",
                 "tint-photo", color)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    photo = None
    try:
        pil = _render_pil(key_name, int(size), bool(dark))
        if pil is not None:
            pil = _tint_pil(pil, color)
            if pil is not None:
                photo = _to_photo(pil)
    except Exception as exc:  # noqa: BLE001
        log.info("图标着色出图失败（%s）：%s", name, exc)
        return None
    if photo is not None:
        _CACHE[cache_key] = photo
    return photo


def get(name: str, size: int = SIZE_MAIN, dark: Optional[bool] = None):
    """取一张图标（带缓存）。name 可以是图标键，也可以是旧数据里的 emoji。

    返回 ``tk.PhotoImage``；不可用时返回 ``None``（调用方应能容忍无图标）。
    """
    if not name:
        return None
    key_name = EMOJI_TO_KEY.get(name, name)
    if dark is None:
        dark = _is_dark()
    cache_key = (key_name, int(size), "dark" if dark else "light")
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    photo = _render(key_name, int(size), bool(dark))
    if photo is not None:
        _CACHE[cache_key] = photo
    return photo


def _is_dark() -> bool:
    try:
        from . import theme
        return theme.is_dark()
    except Exception:  # noqa: BLE001
        return False


def invalidate() -> None:
    """主题切换时清缓存（深/浅色图标要重画）。"""
    _CACHE.clear()


def visual_centroid(name: str, size: int = 64,
                    dark: bool = False) -> Optional[Tuple[float, float]]:
    """返回图标**不透明像素**的质心，归一化到 0~1（左上角是 (0,0)）。

    为什么需要它：需求 ③ 要求分组图标"垂直居中"。肉眼判断 22px 的小图很不
    可靠，而"画布尺寸一样"并不等于"看起来居中"—— 上一版公文包的提手偏上、
    袋身偏下，画布是 22×22 但视觉重心落在 0.55 处，一排放三个分组就露馅。
    有了这个函数，测试可以断言 ``0.42 <= cy <= 0.58``。

    返回 ``None`` 表示图标绘制失败（调用方应跳过该断言而不是误报）。
    """
    if not _PIL_OK:
        return None
    key_name = EMOJI_TO_KEY.get(name, name)
    img = _render_pil(key_name, size, dark)
    if img is None or img.size[0] <= 0:
        return None
    alpha = img.getchannel("A")
    total = 0.0
    sx = sy = 0.0
    px = alpha.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            v = px[x, y]
            if v:
                total += v
                sx += x * v
                sy += y * v
    if total <= 0:
        return None
    return (sx / total / w, sy / total / h)


#: 取不到分组图标时的兜底键（叶子最中性，不暗示任何业务含义）
DEFAULT_GROUP_ICON = "grp_life"


def preload(sizes=SIZES) -> int:
    """预生成常用图标，避免首帧闪烁。返回成功生成的张数。"""
    count = 0
    for name in ("check", "calendar", "pomodoro", "more", "edit", "delete", "move"):
        for size in sizes:
            if get(name, size) is not None:
                count += 1
    return count


def export_png(folder, sizes=SIZES) -> int:
    """把全部图标导出为 PNG（构建时调用，产物存 assets/icons/）。

    需求 16 要求图标目录随包生成；运行期用内存缓存，导出仅用于构建与排查。
    """
    if not _PIL_OK:
        return 0
    from pathlib import Path

    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, painter in _PAINTERS.items():
        for size in sizes:
            try:
                big = size * SUPERSAMPLE
                img = painter(big)
                overlay = _new_canvas(big)
                _warm_overlay(ImageDraw.Draw(overlay), big, strength=0.16)
                img = Image.alpha_composite(img, overlay).resize((size, size),
                                                                 Image.LANCZOS)
                img.save(out / f"{name}-{size}.png", "PNG")
                count += 1
            except Exception as exc:  # noqa: BLE001
                log.info("导出图标 %s@%d 失败：%s", name, size, exc)
    return count
