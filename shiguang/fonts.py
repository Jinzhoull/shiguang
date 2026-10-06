# -*- coding: utf-8 -*-
"""字体体系：字体栈探测、DPI 自适应字号、行高计算、语义角色表。

为什么单独开一个模块
--------------------
之前字体逻辑散在 ``theme.py`` 里，只有一句 ``next((f for f in _PREFERRED if f in available))``，
字号则是**硬编码在 UI 代码里**的散装数字（``font=(theme.family(), 9)``、
``ctk.CTkFont(family=..., size=17)`` 之类）。后果有三个：

1. 想调整某处字号得全局 grep，改一处漏三处；
2. 高 DPI（150%）下 Tk 的 ``tk scaling`` 会再乘一遍，字号变成"薛定谔的大小"；
3. 换字体后 text 被裁 —— 因为行高是拍脑袋定的，没有跟 ``font.metrics()`` 对齐。

本模块把这些收敛成三件事：
* ``init(root)``   —— 探测可用字体族 + 读 DPI 缩放，全局只调一次；
* ``font(role)``   —— 按语义角色取字体（字号已经过 DPI 归一）；
* ``line_height(role)`` —— 用 ``metrics()`` 算精确行高，别硬编码。

字号与 DPI
----------
需求 7 要求"以 14px 为基准乘缩放系数"。实现上有两个坑：

* **不要调 ``ctk.set_widget_scaling()``**。customtkinter 通过 ``ScalingTracker``
  自己就会读显示器 DPI 并缩放控件尺寸（连同字号）。我们再显式设一遍，
  缩放会被**应用两次** —— 150% 显示器上 28px 的图标按钮会变成 63px，
  整个窗口被撑成 8000px 宽（实测踩过，窗口直接大到看不见）。
  所以这里的 ``_scale`` **只用于我们自己算的字号与 Canvas 图元**，
  控件尺寸的缩放完全交给 CTk。
* **把 ``tk scaling`` 锁成 1.0**。Tk 原生控件（Canvas 上的 text、tk.Menu）
  会按 ``tk scaling`` 缩放字号，如果不锁，我们乘过一次之后它又乘一次。

结论：一个变量（``_scale``）负责"我们画的东西"，CTk 负责"CTk 控件"，
两者互不干涉，行为可预测、可断言。
"""

from __future__ import annotations

import logging
import os
import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk

_LOG = logging.getLogger("shiguang.fonts")
FALLBACK_FONT_FILE = "msyh.ttc"

# --------------------------------------------------------------------------
# 字体栈
# --------------------------------------------------------------------------
# 中文（正文/标题）：按"美观度 + 可用性"排序。
# HarmonyOS Sans SC 是需求点名要的，但 Windows 默认不带，需要用户自己装；
# 装了就自动用上，没装就顺位到微软雅黑 —— 不会因为缺字体而回退到宋体那种灾难。
CJK_STACK: List[str] = [
    "HarmonyOS Sans SC",
    "HarmonyOS Sans",
    "MiSans",
    "MiSans VF",
    "OPPO Sans",
    "Alibaba PuHuiTi",
    "Source Han Sans SC",
    "Noto Sans SC",
    "思源黑体",
    "Sarasa Gothic SC",
    "更纱黑体",
    "PingFang SC",
    "苹方-简",
    "Microsoft YaHei UI",
    "微软雅黑 UI",
    "Microsoft YaHei",
    "微软雅黑",
    "Hiragino Sans GB",
    "Segoe UI",
]

# 标题：优先"有个性"的中文黑体，做标题比正文更有辨识度。
# 需求 5 提到"如果有更美的字体优先用（如得意黑做标题）"——得意黑是开源字体，
# 用户装了就用，没装就退回中文栈首位，风格不会崩。
TITLE_STACK: List[str] = [
    "Smiley Sans",
    "得意黑",
    "HarmonyOS Sans SC",
    "MiSans",
    "Alibaba PuHuiTi",
    "Source Han Sans SC",
    "Noto Sans SC",
    "Microsoft YaHei UI",
    "微软雅黑 UI",
    "Microsoft YaHei",
    "Segoe UI",
]

# 西文/数字：Inter 的等宽数字（tnum）最整齐，没有就顺位到系统无衬线。
LATIN_STACK: List[str] = [
    "Inter",
    "Inter Display",
    "Segoe UI Variable",
    "Segoe UI",
    "Helvetica Neue",
    "Arial",
]

# 数字/等宽场景：统计数字要竖排对齐，等宽字体比比例字体整齐得多。
MONO_STACK: List[str] = [
    "Inter",
    "JetBrains Mono",
    "Cascadia Mono",
    "Consolas",
    "Segoe UI",
]

EMOJI_STACK: List[str] = ["Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"]

# 微软雅黑有个讨厌的毛病：字号很小时 hinting 会糊。
# 下面这些别名在 Tk 里注册为**独立字体族名**，但绑定的还是雅黑 —— 用带 "UI" 的
# 优先（雅黑 UI 的笔画更细，小字号下反而更清楚）。
_FALLBACK = "TkDefaultFont"


# --------------------------------------------------------------------------
# 角色表：逻辑 px（设计稿 @100%），DPI 缩放在这里之后统一乘
# --------------------------------------------------------------------------
# role -> (size_px, weight, slant, family_kind, overstrike)
#   family_kind: "cjk" 正文/界面 | "title" 大标题 | "latin" 数字 | "mono" 等宽
ROLES: Dict[str, Tuple[int, str, str, str, bool]] = {
    # ---- 字号层级（**逻辑像素**，与设计稿 @100% 一致）----
    # 页面大标题   20px Bold
    "page_title":  (20, "bold",   "roman", "title", False),
    # 统计大数字   24px Bold 等宽
    "stat_number": (24, "bold",   "roman", "mono",  False),
    # 紧凑统计条里的数字（也覆盖历史别名 "number"）。
    # ❗历史 bug：ROLES 里从来没有 "number" 这个键，而 task_page / stats_page 都在
    #   调 theme.font("number")。fonts._resolved 用的是 ROLES.get(key, ROLES["body"])，
    #   于是它一直**静默回落到 body** —— 统计数字和正文一样大，
    #   而 stats_page 的 font("number", -4) 更是只有 9px。层级看着是"倒的"。
    #   补上真实定义，顺带把"静默回落"这条路堵死。
    "number":      (20, "bold",   "roman", "mono",  False),
    # 任务标题     14px Regular
    "task_title":  (14, "normal", "roman", "cjk",   False),
    "task_title_done": (14, "normal", "roman", "cjk", True),   # + 删除线
    # 截止日期元信息 12px Regular
    "meta":        (12, "normal", "roman", "cjk",   False),
    "meta_bold":   (12, "bold",   "roman", "cjk",   False),
    "meta_strike": (12, "normal", "roman", "cjk",   True),
    # 分组标题     14px Bold（与任务标题同尺寸，靠粗细分层级）
    "group_title": (14, "bold",   "roman", "cjk",   False),
    # 切换器/导航   10px（顶部行空间紧张，单独一档）
    "nav":         (10, "bold",   "roman", "cjk",   False),
    "nav_idle":    (10, "normal", "roman", "cjk",   False),
    # 按钮文字     12px
    "button":      (12, "normal", "roman", "cjk",   False),
    "button_bold": (12, "bold",   "roman", "cjk",   False),
    # 空状态       13px Regular
    "empty":       (13, "normal", "roman", "cjk",   False),
    # 统计标签     11px
    "stat_label":  (11, "normal", "roman", "cjk",   False),
    # 正文（对话框输入等）
    "body":        (13, "normal", "roman", "cjk",   False),
    "body_done":   (13, "normal", "roman", "cjk",   True),
    # 小字/辅助
    "small":       (12, "normal", "roman", "cjk",   False),
    "small_bold":  (12, "bold",   "roman", "cjk",   False),
    "small_strike": (12, "normal", "roman", "cjk",   True),
    # 极小字（顶部行、徽章、提示）
    "tiny":        (10, "normal", "roman", "cjk",   False),
    "tiny_bold":   (10, "bold",   "roman", "cjk",   False),
    "tiny_strike": (10, "normal", "roman", "cjk",   True),
    # 番茄计时数字（等宽，秒数跳动时不抖）
    "timer":       (12, "bold",   "roman", "mono",  False),
    # 品牌标语
    "slogan":      (10, "normal", "roman", "cjk",   False),
    # 极小徽章文字（分组计数徽章）
    "badge":       (10, "normal", "roman", "mono",  False),
}

# 兼容旧角色名（theme.font 时代的叫法），迁移期不必改所有调用点。
ALIASES: Dict[str, str] = {
    "title": "page_title",
    "h2": "group_title",
}

# 需求 5：需要拉开字距的角色（Tk 没有 letter-spacing，只能靠调用方在文本里
# 插空格或自己控制 —— 这里只登记"设计意图"，供文档与测试引用）。
LETTER_SPACING: Dict[str, float] = {
    "page_title": -0.3,
    "stat_number": -1.0,
    "stat_label": 0.5,
    "group_title": 0.3,
}

# 需求 6 指定的行高（逻辑 px）。给的是"设计意图值"，实际用 metrics 校准。
TARGET_LINE_HEIGHT: Dict[str, int] = {
    "task_title": 18,
    "group_title": 18,
    "page_title": 26,
    "stat_number": 30,
    "number": 26,
    "meta": 16,
    "small": 16,
    "tiny": 14,
}

BASE_SIZE = 14      # 需求 7：DPI 归一基准
BASE_DPI = 96.0


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------
_families: set[str] = set()
_family_cache: Dict[str, str] = {}
_font_cache: Dict[str, ctk.CTkFont] = {}
_scale: float = 1.0
_ready: bool = False


def _detect_scale(root: Optional[tk.Misc]) -> float:
    """探测显示器缩放系数。

    优先用 Windows 的 ``GetDpiForWindow``：它反映的是**窗口所在那块屏**的 DPI，
    比 ``tk scaling`` 准确 —— 多显示器混合缩放（笔记本 150% + 外接 100%）时，
    ``tk scaling`` 取的是主屏，窗口拖到副屏会错得离谱。
    """
    scale = 0.0
    if root is not None:
        try:
            import ctypes

            hwnd = root.winfo_id()
            # 进程已经是 per-monitor DPI aware，这里拿到的是真实 DPI
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi and dpi > 0:
                scale = dpi / BASE_DPI
        except Exception:  # noqa: BLE001
            scale = 0.0
    if scale <= 0.0:
        try:
            scale = float(tkfont.families.__self__.tk.call("tk", "scaling"))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            scale = 1.0
    # 夹到合理区间：0.75 ~ 3.0，防止异常 DPI 把界面炸掉
    return max(0.75, min(3.0, scale))


def init(root: Optional[tk.Misc] = None, preferred: str = "") -> Dict[str, str]:
    """在 Tk root 创建之后调用一次。

    返回选中的字体族映射（便于日志与自检打印）。重复调用是安全的。
    """
    global _families, _scale, _ready

    try:
        _families = set(tkfont.families(root)) if root is not None else set(tkfont.families())
    except Exception:  # noqa: BLE001
        _families = set()

    _scale = _detect_scale(root)

    # ---- 把 tk scaling 锁成 1，避免"我们乘一次、Tk 再乘一次"的平方缩放 ----
    if root is not None:
        try:
            root.tk.call("tk", "scaling", 1.0)
        except Exception:  # noqa: BLE001
            pass

    # ⚠ 这里**故意不调** ctk.set_widget_scaling / set_window_scaling。
    # CTk 的 ScalingTracker 自己就会按显示器 DPI 缩放控件，再设一遍会缩放两次，
    # 150% 屏上按钮会变成 63px、窗口被撑到 8000px 宽（实测踩过）。详见模块头注释。

    _family_cache.clear()
    _font_cache.clear()

    picked = {
        "cjk": _pick(CJK_STACK, preferred),
        "title": _pick(TITLE_STACK, preferred),
    }
    # 西文与等宽：只在真的存在时用，否则跟着中文字体走（雅黑/思源里的西文也不差）
    picked["latin"] = _pick(LATIN_STACK, "")
    picked["mono"] = _pick(MONO_STACK, "")
    if picked["latin"] == _FALLBACK:
        picked["latin"] = picked["cjk"]
    if picked["mono"] == _FALLBACK:
        picked["mono"] = picked["latin"]
    picked["emoji"] = _pick(EMOJI_STACK, "", fallback="")

    _ready = True
    _LOG.info("字体族：%s  DPI 缩放：%.2f", picked, _scale)
    return picked


def _pick(stack: List[str], preferred: str = "", fallback: str = _FALLBACK) -> str:
    if preferred and preferred in _families:
        return preferred
    for name in stack:
        if name in _families:
            return name
    return fallback


def ready() -> bool:
    """字体是否已初始化（测试可在未建窗口时用它降级）。"""
    return _ready


def scale() -> float:
    """当前 DPI 缩放系数（1.0 = 100%）。"""
    return _scale


def px(logical: float) -> float:
    """把逻辑像素换算成当前 DPI 下的实际像素（Canvas 绘制用）。"""
    return float(logical) * _scale


def ipx(logical: float) -> int:
    return int(round(float(logical) * _scale))


def dpi() -> int:
    return int(round(_scale * BASE_DPI))


# --------------------------------------------------------------------------
# 取字体
# --------------------------------------------------------------------------
def family(kind: str = "cjk") -> str:
    """取某类字体的族名。未初始化时按 common 栈现探一次，不至于返回空串。"""
    if kind in _family_cache:
        return _family_cache[kind]
    if not _families:
        try:
            _families.update(tkfont.families())
        except Exception:  # noqa: BLE001
            pass
    stack = {
        "cjk": CJK_STACK, "title": TITLE_STACK,
        "latin": LATIN_STACK, "mono": MONO_STACK, "emoji": EMOJI_STACK,
    }.get(kind, CJK_STACK)
    name = _pick(stack, "")
    if kind == "latin" and name == _FALLBACK:
        name = family("cjk")
    _family_cache[kind] = name
    return name


def _resolved(role: str) -> Tuple[str, int, str, bool]:
    """role -> (family, **设计稿逻辑字号**, weight, overstrike)。

    ⚠️ 这里**不能**再乘 ``_scale``。ROLES 里的字号是"设计稿 @100% 的逻辑像素"，
    而 ``ctk.CTkFont`` 自己会按控件缩放系数把它转成**像素**字号
    （``_apply_font_scaling`` → ``-abs(size * scaling)``）。早期实现先乘了一遍
    ``_scale``，CTk 又乘一遍 —— 于是 150% 屏上 13px 的任务标题实际渲染成 30px
    （1.5×2.25），整界面都显得"胖一圈"，正是用户说的"元素过大 / 手机端风格"。
    Canvas 侧（``tkfont_spec``）由本模块自己补上像素换算，两边一致。
    """
    key = ALIASES.get(role, role)
    size, weight, _slant, kind, overstrike = ROLES.get(key, ROLES["body"])
    return family(kind), max(8, int(size)), weight, overstrike


def font(role: str = "body", size_delta: int = 0) -> ctk.CTkFont:
    """按语义角色取字体对象（带缓存；同一角色只创建一次 Tk 字体）。

    ``size_delta`` 是**逻辑像素**的增减，跟 ``_scale`` 一起交给 CTk 处理。
    """
    key = f"{role}:{size_delta}:{round(_scale, 3)}"
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    fam, size, weight, overstrike = _resolved(role)
    obj = ctk.CTkFont(
        family=fam,
        size=max(8, size + size_delta),
        weight=weight,
        overstrike=overstrike,
    )
    _font_cache[key] = obj
    return obj


def tkfont_spec(role: str = "body") -> Tuple[str, int, str]:
    """给 Canvas ``create_text`` 用的 ``(family, size, weight)`` 三元组。

    Canvas 不接受 CTkFont 对象，必须用裸元组 —— 为了让 Canvas 上的文字
    跟控件字号体系保持一致，统一从这里取，禁止在绘制代码里手写数字。

    ⚠️ 字号取**负数**（Tk 的"像素"口径）。正数在 Tk 里是"点"，会被 DPI 再乘
    一次（本机 ``tk scaling`` ≈ 2.0），10px 的切换器文字会渲染成 20px。
    """
    fam, size, weight, _ = _resolved(role)
    return (fam, -max(8, int(round(size * (_scale or 1.0)))), weight)


def raw_spec(role: str = "body") -> Tuple[str, int, str, str, bool]:
    """完整规格，供测试断言。"""
    fam, size, weight, overstrike = _resolved(role)
    return (fam, size, weight, ROLES.get(ALIASES.get(role, role), ROLES["body"])[2], overstrike)


# --------------------------------------------------------------------------
# 行高（需求 8：用 metrics 算，不硬编码）
# --------------------------------------------------------------------------
_line_cache: Dict[str, int] = {}


def line_height(role: str = "body") -> int:
    """精确行高（**逻辑像素**）。

    为什么不能硬编码：换字体后 ascent/descent 完全不同。微软雅黑 14px 的
    linespace 是 20，而 Source Han Sans 同样 14px 可能是 24 —— 按 20 去给
    容器留高度，思源用户就会看到文字被裁掉下半截。

    这里用 ``font.metrics()["linespace"]``（= ascent + descent，Tk 自己算的）
    作为下限，再与设计意图值取较大者：既保证不裁字，也不至于让紧凑的雅黑
    撑出过多空白。
    """
    key = f"{role}:{round(_scale, 3)}"
    cached = _line_cache.get(key)
    if cached is not None:
        return cached
    natural = 0
    try:
        pf = _pixel_font(role)
        if pf is not None:
            natural = int(pf.metrics().get("linespace", 0))     # 物理像素
    except Exception:  # noqa: BLE001
        natural = 0
    if natural <= 0:
        # 拿不到 metrics（极端情况）：退回字号 * 1.45 的经验值（物理像素）
        natural = int(round(
            ROLES.get(ALIASES.get(role, role), ROLES["body"])[0] * _scale * 1.45))
    target_key = ALIASES.get(role, role)
    target = TARGET_LINE_HEIGHT.get(target_key)
    physical = max(natural, int(round(target * _scale))) if target else natural
    value = max(1, int(round(physical / _scale))) if _scale else physical
    _line_cache[key] = value
    return value


_measure_cache: Dict[str, "tkfont.Font"] = {}


def _pixel_font(role: str = "body", size_delta: int = 0) -> Optional["tkfont.Font"]:
    """量宽/算行高专用：**与控件真正在用的字体完全一致**的那个 Tk 字体。

    为什么不能直接拿 ``font(role)``（CTkFont）量：``CTkFont.measure()`` /
    ``.metrics()`` 反映的是它**未缩放**的那一档字号，而控件拿到的是
    ``-abs(size * widget_scaling)`` 的**像素**字号（本机 14px 的任务标题实际是
    ``-21``）。两者差一个缩放系数，量出来的宽度只有真实值的 1/1.5
    （实测 9 个汉字：真宽 189 物理像素，CTkFont 只报 126）。

    所以这里按 CTk 的规则自己造一个负数（像素）字号字体，量与渲染同源；
    再由调用方除回 ``_scale`` 变成逻辑像素。负数在 Tk 里就是"像素"，
    正数会被 ``tk scaling`` 再乘一次 —— 这个坑本项目踩过两轮。
    """
    key = f"{role}:{size_delta}:{round(_scale, 3)}"
    cached = _measure_cache.get(key)
    if cached is not None:
        return cached
    fam, size, weight, _ = _resolved(role)
    px = max(8, int(round((size + size_delta) * (_scale or 1.0))))
    try:
        obj = tkfont.Font(family=fam, size=-px, weight=weight)
    except Exception:  # noqa: BLE001
        return None
    _measure_cache[key] = obj
    return obj


def invalidate() -> None:
    """主题/缩放变化后清缓存（DPI 变更时由 App 调用）。"""
    _font_cache.clear()
    _line_cache.clear()
    _family_cache.clear()
    _measure_cache.clear()
    _pil_cache.clear()


# --------------------------------------------------------------------------
# PIL / FreeType 字体：文字也要抗锯齿
# --------------------------------------------------------------------------
# 为什么还需要一条 PIL 通道
# --------------------------
# Tk 的 Canvas 文字走 GDI，小字号下默认是**亚像素（ClearType）**渲染：
# 放大截图能看到笔画左右两侧的橙/青色描边，笔画边缘也没有灰度过渡；
# CTk 控件的圆角底衬又是 ``create_polygon`` 拼的，边缘是硬阶梯。
# 用户看到的就是"逾期标签和日期文字模糊、有锯齿"。
#
# 解决方式和图标完全一致：PIL **4× 超采样 → LANCZOS 缩小 → 带 alpha 位图**
# 贴到 Canvas 上。PIL/FreeType 走**灰度**抗锯齿，没有彩边。
#
# 族名 -> 字体文件
# ----------------
# ``ImageFont.truetype`` 只认文件名。Windows 把 ``"Microsoft YaHei (TrueType)"``
# 这样的注册名映射到 ``msyh.ttc``（分别在 HKLM 与 HKCU 两个 ``...\CurrentVersion\Fonts``
# 下，用户级安装写在 HKCU），按族名匹配即可。``.ttc`` 字体集合还要挑出族名
# 对得上的那一档（``getname()``）。
#
# ⚠️ 可变字体（VF）的坑（本机实测）
# --------------------------------
# 本机装的 Noto Sans SC 是 ``NotoSansSC-VF.ttf``，它的 **默认实例是 Thin
# （wght=100）**。不显式设轴的话 FreeType 画出来是极细的笔形 —— 看起来就是
# "字体发虚、像没渲染完"。所以拿到 VF 后必须 ``set_variation_by_axes``
# 把 Weight 拨到 400 / 700。装了 HarmonyOS Sans SC 这类**静态多字重**字体
# 之后这条路自然就不走了（``get_variation_axes()`` 返回空）。
_PIL_CACHE: Dict[Tuple[str, bool, int], object] = {}
_REG_FONT_LIST: Optional[List[Tuple[str, str]]] = None

# 注册名里出现这些词 = 不是"常规"字重（用于挑 Regular）
_PIL_NON_REGULAR = ("bold", "italic", "oblique", "light", "black", "semibold",
                    "semilight", "thin", "medium", "demibold", "extrabold",
                    "extralight", "heavy")

# 常规/加粗在可变字体上的目标字重
_PIL_WEIGHT = {False: 400, True: 700}


def _system_font_dir() -> str:
    return os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _user_font_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or ""
    return os.path.join(base, "Microsoft", "Windows", "Fonts")


def _registered_fonts() -> List[Tuple[str, str]]:
    """``[(注册名, 文件名), ...]``（HKLM + HKCU，只读一次）。"""
    global _REG_FONT_LIST
    if _REG_FONT_LIST is not None:
        return _REG_FONT_LIST
    out: List[Tuple[str, str]] = []
    try:
        import winreg

        sub = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, sub)
                count = winreg.QueryInfoKey(key)[1]
                for i in range(count):
                    name, value, _type = winreg.EnumValue(key, i)
                    out.append((str(name), str(value)))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        out = []
    _REG_FONT_LIST = out
    return out


def _font_file(family: str, bold: bool) -> Optional[str]:
    """按族名找字体文件（找不到返回 ``None``，调用方降级）。

    命中优先级：注册名以 ``"<族名> ("`` 开头 > 只是包含族名；同为加粗时
    ``"… Bold"`` 精确命中 > ``"… SemiBold / ExtraBold"``。
    （``"semibold"`` 里含 ``"bold"``，不排序的话 24px 的统计数字会被
    挑中 SemiBold —— 实测顺序取决于注册表枚举顺序，不稳定。）
    """
    fam = family.strip().lower()
    if not fam:
        return None
    found: List[Tuple[int, int, str]] = []
    for name, value in _registered_fonts():
        low = name.lower()
        if fam not in low:
            continue
        if not (low.endswith("(truetype)") or low.endswith("(opentype)")):
            continue
        tail = low[len(fam):]
        for suffix in ("(truetype)", "(opentype)"):
            if tail.endswith(suffix):
                tail = tail[: -len(suffix)]
        tail = tail.strip()
        if bold:
            if "bold" not in low or "italic" in low or "oblique" in low:
                continue
            weight_rank = 0 if tail == "bold" else 1
        else:
            if any(word in low for word in _PIL_NON_REGULAR):
                continue
            weight_rank = 0
        if os.path.isabs(value):
            candidates = [value]
        else:
            candidates = [os.path.join(_system_font_dir(), value),
                          os.path.join(_user_font_dir(), os.path.basename(value))]
        for path in candidates:
            if os.path.exists(path):
                exact = low.startswith(f"{fam} (")
                found.append((0 if exact else 1, weight_rank, path))
                break
    if not found:
        return None
    found.sort(key=lambda item: (item[0], item[1]))
    return found[0][2]


def _apply_variation(font, bold: bool) -> None:
    """可变字体：把 Weight 轴拨到常规/加粗。

    不拨的话拿到的是**默认实例** —— Noto Sans SC 的默认实例是 Thin，
    1px 的笔画会细到几乎看不见。
    """
    try:
        axes = font.get_variation_axes()
    except Exception:  # noqa: BLE001
        return
    if not axes:
        return
    values: List[float] = []
    for axis in axes:
        name = axis.get("name", b"")
        if isinstance(name, (bytes, bytearray)):
            name = name.decode("utf-8", "ignore")
        low = str(name).lower()
        if "weight" in low or "wght" in low:
            want = _PIL_WEIGHT[bool(bold)]
            lo = float(axis.get("minimum", 100))
            hi = float(axis.get("maximum", 900))
            values.append(max(lo, min(hi, want)))
        else:
            values.append(float(axis.get("default", 0)))
    try:
        font.set_variation_by_axes(values)
    except Exception:  # noqa: BLE001
        pass


def pil_font(role: str = "body", size_delta: int = 0, supersample: int = 1,
             bold: Optional[bool] = None):
    """按语义角色取 PIL/FreeType 字体；字号 = **物理像素 × supersample**。

    取不到（族名对不上文件、PIL 缺失、未初始化）一律返回 ``None``，
    调用方必须能降级 —— 字体探测失败不该让界面崩掉。
    """
    if not _ready:
        return None
    try:
        fam, size, weight, _ = _resolved(role)
    except Exception:  # noqa: BLE001
        return None
    if bold is None:
        bold = weight == "bold"
    px = max(6, int(round((size + size_delta) * (_scale or 1.0) * max(1, supersample))))
    key = (fam, bool(bold), px)
    cached = _PIL_CACHE.get(key)
    if cached is not None:
        return cached
    path = _font_file(fam, bool(bold))
    if path is None and bold:
        # 可变字体族（如 Noto Sans SC）在注册表里**只有一条**记录，
        # 没有独立的 "… Bold (TrueType)"。退回常规文件，靠 Weight 轴加粗。
        path = _font_file(fam, False)
    if not path:
        return None
    try:  # 延迟导入：无头环境 / 未装 Pillow 时不该在 import 期炸
        from PIL import ImageFont
    except Exception:  # noqa: BLE001
        return None
    chosen = None
    for index in range(12):          # .ttc 集合：挑族名对得上的那一档
        try:
            obj = ImageFont.truetype(path, px, index=index)
        except Exception:  # noqa: BLE001
            break
        if chosen is None:
            chosen = obj
        try:
            name = obj.getname()[0]
        except Exception:  # noqa: BLE001
            name = ""
        if str(name).strip().lower() == fam.strip().lower():
            chosen = obj
            break
    if chosen is None:
        return None
    _apply_variation(chosen, bool(bold))
    _PIL_CACHE[key] = chosen
    return chosen


def pil_family_file(role: str = "body") -> Optional[str]:
    """该角色实际会用的字体文件（诊断用）。"""
    try:
        fam, _size, weight, _ = _resolved(role)
    except Exception:  # noqa: BLE001
        return None
    return _font_file(fam, weight == "bold")


def has_pil_font(role: str = "body") -> bool:
    """该角色能否拿到 PIL 字体（自检用）。"""
    return pil_font(role) is not None


def measure(role: str = "body", text: str = "", size_delta: int = 0) -> int:
    """文本在指定角色下的宽度（**逻辑像素**）。

    为什么需要它：CTkButton 的默认宽度是 140px，如果不显式收窄，按钮会一直
    在文字后面留一大截空白 —— 分组标题按钮就是这样把"2/5"徽章推到了很远的
    地方（窄窗口下整行看着很散）。把 ``width`` 设成实测文字宽，行内元素才会
    自然贴合。

    ⚠️ 必须用 ``font(role)`` 返回的那个 **CTkFont 对象**来量 —— 那才是控件
    真正在用的字体。早期实现另建了一个 ``tkfont.Font(size=<缩放后的字号>)``，
    而 Tk 的**正数**字号是"点"、会被 ``tk scaling``（本机 ≈ 2.0）再乘一次，
    量出来是真实宽度的**两倍**（实测 8 个汉字在 task_title 下：真宽 117、
    量出 234）。后果：分组标题按钮宽一倍、任务标题的折行宽度被算成"一行放
    不下"于是白折一行、控件请求宽度普遍虚胖。

    单位约定：CTkFont 量出来的是**物理**像素，而 CTk 控件的 ``width`` 收
    **逻辑**像素，所以这里除回 ``_scale``。调用方拿到就能直接赋给 ``width``，
    不要再自己乘/除缩放（这个双单位坑在本项目已经踩过不止一次）。
    """
    try:
        tk_font = _pixel_font(role, size_delta)
        if tk_font is None:
            return 0
        physical = tk_font.measure(text)
    except Exception:  # noqa: BLE001
        return 0
    if _scale:
        return max(1, int(round(physical / _scale)))
    return int(physical)


def describe() -> Dict[str, object]:
    """自检信息：给 ``--selftest`` 与冻结冒烟用。"""
    return {
        "ready": _ready,
        "scale": round(_scale, 3),
        "dpi": dpi(),
        "families": {k: family(k) for k in ("cjk", "title", "latin", "mono", "emoji")},
        "roles": {
            r: {"family": raw_spec(r)[0], "size": raw_spec(r)[1],
                "weight": raw_spec(r)[2], "overstrike": raw_spec(r)[4],
                "line_height": line_height(r)}
            for r in ("page_title", "stat_number", "task_title", "meta",
                      "group_title", "nav", "button", "empty", "stat_label")
        },
    }
