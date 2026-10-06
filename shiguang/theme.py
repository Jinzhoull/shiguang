# -*- coding: utf-8 -*-
"""主题系统：配色、字体、深浅色切换。

用法约定
--------
* 传给 customtkinter 控件的颜色一律用 ``pair("key")``（形如 ``("#FAF7F2", "#2D2A26")``），
  这样调用 ``ctk.set_appearance_mode()`` 时控件会自动换色，无需手工刷新。
* 需要在 Canvas 上手工绘制时用 ``c("key")`` 取"当前模式"下的单色值。
* 主题切换后，Canvas 内容（光环、柱状图）需要重绘 —— 由 App 统一触发整页重建。
"""

from __future__ import annotations

from typing import Dict, Tuple

import customtkinter as ctk

from . import fonts as _fonts   # 字体体系（字体栈 / 字号层级 / DPI 自适应 / 行高）

# --------------------------------------------------------------------------
# 配色
# --------------------------------------------------------------------------

LIGHT: Dict[str, str] = {
    "bg": "#FAF7F2",          # 暖白纸张底
    "bg_alt": "#F3EDE3",
    "card": "#FFFFFF",        # 卡片
    # 悬停底色（1.5.24：#FBF6EE → #F4EAD7）。旧值几乎等于白卡上的"没变"，
    # 和 card_done 的暖米色只差 8 个色阶 —— 两个状态在屏幕上分不出来。
    "card_hover": "#F4EAD7",
    # 已完成卡片底色（1.5.24：暖米 #F7F2E9 → 淡绿 #E3F0DB）。
    # 旧值走"和背景同色系的暖调"，与卡片的暖白、悬停的暖米同属一族，三态全
    # 挤在 5% 的亮度差里；改成绿系后与暖色系在**色相**上直接分家（≈40° → ≈97°），
    # 一眼就能扫出"哪些已经完成了"。
    "card_done": "#E3F0DB",
    # 已完成卡片的悬停底色（1.5.24 新增）：悬停时不能退回暖米 card_hover ——
    # 那样"已完成"的绿底一悬停就消失，等于状态提示被鼠标盖掉。同色相压深一档。
    "card_done_hover": "#D6EACB",
    "border": "#EDE4D6",
    # 可交互字段的轮廓比卡片分隔线更清楚：搜索、文本输入和下拉框使用这档，
    # 普通信息卡仍用上面的轻边线，避免整页变成一格一格的重框。
    "control_border": "#D8C6AE",
    # 窗口最外圈描边（1.5.2 新增）。主窗口是 overrideredirect + 纯白卡片，
    # 在白色/浅灰的桌面或别的软件窗口上完全糊在一起 —— 用户报"分不出边界"。
    # 取值理由：暖白底 #FAF7F2 相对亮度 0.93、卡片纯白 1.0，两者与任何浅色
    # 桌面的亮度差都可能是 0；这里用品牌柔橘压深一档（相对亮度 0.42，
    # 对白底对比度 2.3:1），既保证"看得见边界"，又不至于变成一圈高饱和描边。
    "window_border": "#E0A868",
    "text": "#4A4036",        # 深灰棕，避免纯黑
    "text_muted": "#8C8174",
    # text_done 是**通用**的"弱化提示色"（设置页说明、浮层占位、未设置日期…），
    # 不是"已完成任务"专用，所以 1.5.24 不动它，另开 task_done_text。
    "text_done": "#BDB2A4",
    # 已完成任务的标题/元信息色（1.5.24）：深绿，压在淡绿卡底上 5.2:1。
    "task_done_text": "#3E6E3B",
    "accent": "#E8B84A",      # 暖阳金
    "accent_hover": "#DCA93A",
    "accent_soft": "#F7E6BB",
    "accent_faint": "#FDF6E4",
    "orange": "#E89A4A",      # 柔橘
    "orange_hover": "#D98B3C",
    "gold_soft": "#F6EFD9",
    "pri_high": "#C9553D",    # 朱红
    "pri_mid": "#E89A4A",     # 暖橙
    "pri_low": "#E4C97E",     # 淡金
    "danger": "#C9553D",
    "ghost": "#F1EAE0",
    "ghost_hover": "#E9E1D4",
    "close_hover": "#F6E3DF",
    "knob": "#FFFFFF",        # 开关旋钮（必须与轨道形成对比，否则深色下看不清）
    "ring": "#E5DAC8",
    "track": "#F0E8DA",
    "shadow": "#E8DFD1",
    # ---- 滚动条（1.5.25）----
    # 三代演化：
    #   · 需求 35：4px，常态色 = 页面底色（等于隐形）——用户报"太细、看不出来"；
    #   · 1.5.24：8px，常态暖棕 #D2BE9C（对暖白底 1.7:1）——用户报"有点粗、突兀"；
    #   · 1.5.25：6px（视觉 4px），常态改成**半透明柔橘**——把品牌柔橘按 45%
    #     掺进页面底色得到 #F2CDA6（对 #FAF7F2 只有 1.4:1）。Tk 画布图元不支持
    #     alpha 通道，"半透明"只能靠**与已知底色预乘**来模拟：滑块本来就落在
    #     页面底色（窗口底色）上，混出来的就是它半透明的样子。
    "scroll_thumb": "#F2CDA6",        # 常态滑块 = mix(bg, accent, 0.45)，对底 1.4:1
    "scroll_thumb_hover": "#EDB67C",  # 悬停 = mix(bg, accent, 0.70)：比常态实一档，不用满饱和
    # ---- 截止日期 ----
    "due_text": "#8A7F70",    # 未到期：深灰棕次色
    "due_soon": "#E89A4A",    # 今日到期：柔橘
    "due_over": "#C0392B",    # 已逾期：朱红
    # 朱红底上的前景色（逾期徽章文字）。徽章走 PIL 绘制，取色用 `theme.c()`，
    # 所以这一档必须同时进 LIGHT/DARK 两个字典，只留元组常量会取到兜底黑色。
    "on_danger": "#FFFFFF",
    # 提醒条底色（**饱和底色**，1.5.10~1.5.28 的口径）。
    # ❗1.5.29 起提醒条改成"柔和橘红渐层底 + 深红字"，这两档只作为**强调色**
    # 保留（图标描边、旧代码兜底），横幅底衬请用下面 banner_*_top/bottom/border。
    "banner_over": "#C0392B",   # 白字 5.44:1
    "banner_soon": "#A9662A",   # 白字 4.54:1（柔橘本身 #E89A4A 只有 2.2:1，必须压深）
    "banner_detail_text": "#FFF1E3",
    # ---- 逾期 / 今日到期横幅（1.5.29 重绘）----
    # 底色从"纯朱红/纯赭"改成**上下两道的柔和橘红渐层**：纯平的大块饱和红在
    # 暖白纸感界面里像一块贴纸，且白字 5.4:1 的对比过了头、看着"喊"。
    # 新口径：浅底 + 深字，头部与明细同色系分层（标题最重、日期最轻）。
    "banner_over_top": "#FCE8E1",     # 逾期：上道（更浅）
    "banner_over_bottom": "#F6D3C7",  # 逾期：下道（压深一档，形成层次）
    "banner_over_border": "#E9B3A3",  # 逾期：极细描边
    "banner_over_text": "#A62E1F",    # 逾期：标题/强调文字（对下道 5.06:1）
    "banner_soon_top": "#FCEEDA",     # 今日到期：上道
    "banner_soon_bottom": "#F8E0BC",  # 今日到期：下道
    "banner_soon_border": "#E9C894",
    "banner_soon_text": "#8A5A1E",    # 今日到期：标题/强调文字（4.66:1）
    "banner_item_date": "#6B594C",    # 暖灰日期：在下层渐色上仍超过 4.5:1
    # 横幅左侧图标（1.5.29）：琥珀黄填充 + 深橘描边 + 白感叹号。
    # 浅底上"白描边空心三角"太单薄，实心暖黄才有警示的分量。
    "banner_icon_fill": "#F5B93F",
    "banner_icon_glow": "#FBD57F",    # 三角内左上角的微弱高光
    "banner_icon_edge": "#B0651A",    # 深橘描边（同时对浅底和黄填充都能画出轮廓）
    "banner_icon_mark": "#6D4214",   # 深琥珀标记：黄底上 4.88:1，细 Logo 仍清晰
    # ---- 悬停提示（1.5.29）----
    # 从"纯白方框"改成米白卡：底色比 card 暖一档，压柔橘细描边，
    # 字色仍用深灰棕（不用纯黑）。
    "tooltip_bg": "#FDF8F0",
    "tooltip_border": "#E0A868",
    "tooltip_text": "#4A4036",
}

DARK: Dict[str, str] = {
    "bg": "#2B2825",          # 需求 4：更暖的深色底
    "bg_alt": "#332F2A",
    "card": "#36322D",        # 需求 4：卡片
    "card_hover": "#443E36",  # 1.5.24：与 card / card_done 拉开一档
    # 深色档的"淡绿"只能靠色相表达（深色下再加亮度就不是"完成"而是"高亮"了）。
    # 比卡片**更深更绿**，读作"沉下去、归档了"；色相 33° → 99°。
    "card_done": "#26331F",
    "card_done_hover": "#33402F",   # 1.5.24：已完成 + 悬停
    "border": "#45403A",
    "control_border": "#62564A",
    # 深色下同理：深灰底 #2B2825 与深色桌面/深色编辑器几乎同色，
    # 用暖橘压深（对底对比度 2.1:1）做窗口边界。
    "window_border": "#6E5638",
    "text": "#EFE8DE",
    "text_muted": "#A79E92",
    "text_done": "#8A8177",
    "task_done_text": "#8FBF86",   # 1.5.24：已完成任务文字（深色档提亮的绿）
    "accent": "#E8B84A",
    "accent_hover": "#F0C55C",
    "accent_soft": "#4A3F2A",
    "accent_faint": "#3A3427",
    "orange": "#E89A4A",
    "orange_hover": "#F0A85C",
    "gold_soft": "#3E3729",
    "pri_high": "#D96A54",
    "pri_mid": "#E89A4A",
    "pri_low": "#E4C97E",
    "danger": "#D96A54",
    "ghost": "#3F3A35",
    "ghost_hover": "#4A443E",
    "close_hover": "#4A2E28",
    "knob": "#F2E9D8",
    "ring": "#4A443C",
    "track": "#413B35",
    "shadow": "#26231F",      # 需求 4：投影黑（深色下用更深的底，避免发灰）
    # ---- 滚动条（1.5.25，浅色档同理；深色底上的"半透明"取更低的掺入比）----
    "scroll_thumb": "#684C31",        # = mix(bg, orange, 0.32)，对深色底 1.8:1（原 2.4:1）
    "scroll_thumb_hover": "#A06F3C",  # = mix(bg, orange, 0.62)
    # ---- 截止日期（深色下整体提亮，保证对比度）----
    "due_text": "#A79E92",
    "due_soon": "#F0A85C",
    "due_over": "#E8756A",
    "on_danger": "#3A2320",   # 浅橙红底上的暖棕近黑（白字只有 2.9:1）
    # 深色下同色值即可：白字在深底上天然更清晰，实测 6.62:1 / 4.54:1
    "banner_over": "#A93226",   # 白字 6.62:1
    "banner_soon": "#A9662A",   # 白字 4.54:1
    "banner_detail_text": "#FFEDDD",   # 展开明细正文（暖调米白，同浅色档口径）
    # ---- 逾期 / 今日到期横幅（1.5.29；深色档同理，只是整体压暗、文字提亮）----
    "banner_over_top": "#4E2A22",
    "banner_over_bottom": "#3D1F19",
    "banner_over_border": "#7E4034",
    "banner_over_text": "#F6BCA9",    # 深底上的浅橘红（≈8:1）
    "banner_soon_top": "#4A3820",
    "banner_soon_bottom": "#3A2B18",
    "banner_soon_border": "#7D5F35",
    "banner_soon_text": "#F2C98C",
    "banner_item_date": "#B6A692",    # 明细日期：暖灰弱化（深底上仍 ≈5:1）
    "banner_icon_fill": "#F5B93F",
    "banner_icon_glow": "#FBD57F",
    "banner_icon_edge": "#8A4D12",
    "banner_icon_mark": "#3A2A14",    # 深色档把感叹号压深，压在亮黄上更清楚
    # ---- 悬停提示（1.5.29）----
    "tooltip_bg": "#332C26",
    "tooltip_border": "#6E5C48",
    "tooltip_text": "#EFE8DE",
}

MODE_MAP = {"system": "System", "light": "Light", "dark": "Dark"}
MODE_LABEL = {"system": "跟随系统", "light": "浅色", "dark": "深色"}


# 组件语义色：菜单与提示统一在这里取色，不在绘制模块另立调色板。
for palette, menu_hover in ((LIGHT, "#FDF0E0"), (DARK, "#463829")):
    palette.update({
        "menu_hover": menu_hover,
        "on_nav": "#FFFFFF",
        "toast_bg": palette["tooltip_bg"],
        "toast_text": palette["tooltip_text"],
        "toast_border": palette["orange"],
        "particle_spark": "#F5D76E",
        "particle_fade": "#FCEABB",
    })

# --------------------------------------------------------------------------
# 几何与间距（需求 2/3/24/32 —— 集中管理，UI 代码不许出现裸数字）
# --------------------------------------------------------------------------
RADIUS_CARD = 10          # 卡片统一圆角（14 -> 10，配合紧凑布局）
RADIUS_MENU = 12
RADIUS_CHIP = 12

# 页面内边距：主界面是"桌面小插件"，左右不再留 18px 的舒服边距，
# 统一收到 10 —— 窗口本身只有 344 宽，边距每多 8px 就等于少一行字。
PAGE_PAD_X = 10
PAGE_GAP = 6              # 页面内区块之间的垂直间距

# ---- 拖拽缩放防抖（1.5.16）----
# 拖边框时 <Configure> 每帧都发（约 60 次/秒）。所有"量宽 → 重排/重绘"
# 的响应（横幅位图、wraplength 修正、任务行截断）统一延迟这么多毫秒再执行，
# 回调入口先 after_cancel 上一次 —— 用户停手的那一帧才真正重算，拖动过程中
# 只显示旧内容被裁切（AutoRedrawCanvas 的既有哲学）。太小（<40）等于没防抖，
# 太大（>250）停手后有明显"追上来"的迟滞感。
RESIZE_DEBOUNCE_MS = 80

# ---- 拖拽中冻结（1.5.17）----
# 1.5.16 的 80ms 防抖解决了"每帧重绘吃满主线程"，但拖拽过程本身仍有
# "系统硬拉伸旧画面 + 各控件 after 任务零星追改"的双重渲染感。1.5.17 引入
# 全局"拖拽中"判定（widgets.ResizeGate）：
#   * 相邻两次 <Configure> 间隔小于 DRAG_TICK_MS → 判定正在拖拽，**冻结**
#     所有量宽重排/位图重建（横幅只做廉价拉伸，任务行/统计页什么都不做）；
#   * 事件流安静超过 QUIET_MS → 判定松手，一次性跑完所有注册的 settle 重绘。
RESIZE_DRAG_TICK_MS = 50   # 事件间隔阈值（毫秒）：连续两次 < 此值 = 拖拽中
RESIZE_QUIET_MS = 100      # 事件流安静阈值（毫秒）：安静这么久 = 松手

CARD_PAD_X = 10           # 卡片内边距（16 -> 10）
CARD_PAD_Y = 8
GROUP_GAP = 8             # 分组卡片间距（14 -> 8）

TASK_MIN_HEIGHT = 30      # 任务卡片最小高度（52 -> 30：单行标题 20 + 上下内边距）
TASK_TITLE_TOP = 0        # 任务标题距卡片顶部（紧凑行里不再额外让位）
TASK_ROW_GAP = 2          # 同组内任务卡片间距
TASK_PAD_Y = 5            # 任务卡片上下内边距（12 -> 5）
TASK_STRIPE_W = 2         # 左侧逾期色条宽（3 -> 2）
TASK_CHECK = 16           # 状态图标（勾选框）画布边长（22 -> 16，逻辑像素）
# ---- 任务状态图标（1.5.6 重做）----
# 未完成 / 已完成都是**同一枚柔橘细线圆环**，无填充、无实心色块；
# 仅以环内有无对勾区分。线宽、外径比全部走这里，UI 里不许出现裸数字。
TASK_CHECK_DIAM = 0.76            # 圆环外径 / 画布边长
TASK_CHECK_RING_W = 1.4           # 圆环线宽（逻辑像素）
TASK_CHECK_RING_W_HOVER = 1.8     # 悬停态略加粗
TASK_CHECK_MARK_W = 1.6           # 对勾线宽（逻辑像素）
TASK_ICON = 14            # 右侧动作图标（日历/番茄/更多）的图标尺寸
TASK_ICON_BTN = 20        # 右侧动作按钮的点击热区（26 -> 20）

# ---- 行内截止日期 / 逾期徽章（1.5.8 对齐修正）----
# 顺序是「逾期 09/25 07:21」：徽章在**时间前面**，整块右对齐。
# 反过来写（时间在前）时，徽章会把时间顶离右边缘 —— 有没有逾期，
# 两条任务的时间就不在同一个右边界上，多行看过去参差不齐。
TASK_DUE_TAG_GAP = 4      # 逾期徽章与时间文本之间的间隙（逻辑像素，要"紧贴"）
TASK_DUE_TAG_H = 13       # 逾期徽章高（逻辑像素）
TASK_DUE_TAG_RADIUS = 5   # 逾期徽章圆角
TASK_DUE_TAG_PAD_X = 5    # 徽章内左右留白（"逾期"两字 20px + 10 = 30）
# 行内元信息（徽章 / 日期）的**统一画布高**（1.5.9）：两个都在 PIL 里按
# "参考行框居中"排版，画布高一致 ⇒ 基线天然对齐。此前一个 13、一个靠行高
# 撑开，pack 居中之后差半个像素，看着像"没对齐"。
TASK_META_H = 16
# 行内元信息块（徽章 + 时间）的**右内边距**（1.5.14）：此前是 0，时间文本
# 直接贴在卡片右缘（"逾期 09/25 13:35"即将溢出）。与右侧动作区的右内边距
# （CARD_PAD_X - 2）取同一口径，悬停与否时间都落在同一条右边界上。
TASK_META_PAD_RIGHT = CARD_PAD_X - 2
TASK_META_PAD_LEFT = 4    # 与左侧标题之间保留的最小间隙

# ---- 右侧动作区紧凑模式（1.5.15）----
# 卡片实测宽（逻辑像素）低于 TASK_COMPACT_CARD_W 时自动隐藏"日历/番茄"两个
# 图标、只留"⋯"，把宽度让给"逾期徽章 + 时间 + 标题"；高于 TASK_EXPAND_CARD_W
# 时恢复三个图标。两个阈值留 10px 迟滞，避免在临界宽度来回切换抖动。
# 实测：最小窗口（320）下卡片 ≈295 < 305 → 紧凑；默认窗口（344）下 ≈319 > 315
# → 完整。
TASK_COMPACT_CARD_W = 305
TASK_EXPAND_CARD_W = 315

# ---- 任务落位提示（1.5.7）----
# 勾选完成后卡片要"沉"到已完成区。归位本身是**一次布局**（不逐帧重排 ——
# 那正是 1.5.4 修掉的抖动），紧接着给这张卡做一次底色柔光脉冲，让视线跟得上。
# 只动颜色、不动尺寸，因此不会触发任何重排。
TASK_SETTLE_STEPS = 6     # 脉冲帧数
TASK_SETTLE_INTERVAL = 28 # 每帧间隔（ms）-> 6 × 28 ≈ 170ms
TASK_SETTLE_COLOR = "accent_soft"   # 脉冲起始底色（渐隐回卡片自身的底色）

# ---- 顶部导航切换器（替代原来的底部悬浮药丸）----
# 底部 56px 高的悬浮药丸在 470 高的窗口里占掉 1/8，而且还要为它的投影
# 额外留一条底部内边距。改成顶部右侧的"图标 + 文字"小切换器后，
# 纵向占用从 68px（56 + 12）降到 22px，且与日期行同高，不额外占行。
SWITCH_H = 22             # 切换器整体高度
SWITCH_ICON = 13          # 切换器图标
SWITCH_FONT = "tiny"      # 切换器文字字号角色（10px）
SWITCH_PAD_X = 5          # 单项左右内边距（6 -> 5：最小窗宽 310 下省 6px）
SWITCH_GAP = 2            # 项与项之间的间距
SWITCH_RADIUS = 11        # 选中态药丸圆角（= SWITCH_H / 2）
SWITCH_ANIM_MS = 160      # 选中切换渐变时长
# 顶栏导航整项位图化（1.5.23）：药丸 + 图标 + 文字一次 PIL 合成，
# 消掉 Tk 图元的硬阶梯圆角与 GDI 文字的 ClearType 彩边（"发糊"），
# 同时把画布底色从写死的 card(#FFFFFF) 改回窗口底色（白边的根因）。
NAV_BITMAP_SS = 4         # 位图超采样倍率（与图标/文字同一套 4× 口径）
NAV_ICON_GAP = 2          # 图标与文字之间的间隙（原为硬编码 lpx(2)）
NAV_HOVER_MIX = 0.16      # 未选中项悬停底色 = mix(窗口底色, 暖阳金, 该比例)

# 窄窗紧凑导航：仅在顶栏确实放不下完整导航时隐藏"光景"。
# 最小窗口（320 逻辑像素）实测仍有足够空间，因此安全余量应很小；
# 旧值 72 把"光景"无故隐藏了。
NAV_COMPACT_HIDE = "stats"     # 紧凑模式下隐藏的导航项 key（光景页）
NAV_COMPACT_MARGIN = 8         # 实测最小窗仍能容纳完整导航；只保留小幅字体余量

# ---- 设置页开关旋钮（1.5.11 收敛）----
# 旋钮**不能**直接用 knob（纯白）—— 卡片也是纯白，旋钮会整个糊进卡片
# （sound_switch 就踩过：选中态只剩一坨黄色）。暖奶油色比卡片略深，
# 再配 accent_soft 描边把旋钮从轨道/卡片里分离出来。
SWITCH_KNOB = ("#FFF8EC", "#4A443C")          # 旋钮底色（暖奶油 / 深色档）
SWITCH_KNOB_HOVER = ("#FFFFFF", "#5A5145")    # 旋钮悬停色

# ---- 设置页快捷键"已保存"反馈（1.5.12）----
HOTKEY_SAVED_MS = 1500    # 边框柔橘 + "✓ 已保存" 的停留时长

# ---- 设置页"专注时长"选择芯片行（1.5.11，替代 CTkOptionMenu）----
# 项目禁用原生下拉（CTkOptionMenu 的展开面板是 tkinter.Menu，圆角/边框
# 归系统画、无法定制）。设置页空间有限，用"一排药丸芯片 + 选中态 accent"
# 的就地选择，交互与新建浮层的分组芯片一致。
CHOICE_H = 24              # 芯片高度（逻辑像素，CTk 属性传逻辑值）
CHOICE_PAD_X = 10          # 芯片文字左右内边距
CHOICE_GAP = 6             # 芯片间距
CHOICE_RADIUS = RADIUS_CHIP

# ---- 滚动条（需求 35 / 1.5.24 调粗 / 1.5.25 收窄 + 动态显隐）----
# 1.5.25 两件事：
#   ① 收窄：8px → 6px，配色降对比（见 LIGHT/DARK 的 scroll_thumb）。
#   ② **动态显隐**：内容装得下时整条（连同它占的 6px 栏）一起隐藏，
#      装不下才出现 —— 见 ui/widgets.py::ThinScrollFrame。
# ⚠️ CTkScrollbar 的 corner_radius 不是"圆角半径"，而是**滑块距两侧的缩进**
#    （滑块横向跨度 = 宽度 - 2×corner_radius）。所以它必须 < 宽度/2，
#    否则滑块会被缩成 0 宽 —— 消失。
#    实际观感由两个量决定（Tk polygon 用 joinstyle=ROUND + 描边画滑块）：
#      · 视觉宽度 = 宽度 - 2×SPACING      ← 与 RADIUS 无关
#      · 视觉圆角 = RADIUS - SPACING      ← 想要"药丸"就让它 ≈ 视觉宽度/2
#    6 - 2×1 = 4px 宽、3 - 1 = 2px 圆角 = 4px 药丸（正圆端）。
#    另注：DrawEngine 默认把宽度向下取到**偶数**，奇数宽会被悄悄改小 1px。
SCROLLBAR_WIDTH = 6       # 滚动条总宽（逻辑像素）→ 视觉 4px
SCROLLBAR_RADIUS = 3      # = 宽度/2：让圆角恰好等于视觉宽度的一半（真药丸）
SCROLLBAR_SPACING = 1     # 滑块四周留白（1px），让滑块"浮"在轨道上
SCROLLBAR_HIDE_HYST = 8   # 显隐迟滞（逻辑像素）：已显示时要"短这么多"才收回，
                          # 避免恰好在临界高度上反复出现/消失
SCROLLBAR_HOVER_MS = 90    # 指针进入滚动区后转柔橘的延迟
SCROLLBAR_IDLE_MS = 240    # 指针离开后回常态色的延迟
SCROLL_WHEEL_DIVISOR = 6.0  # 滚轮 delta → 像素：一格 120 / 6 = 20px。
                            # **符号必须原样保留**（Windows delta>0=上滚），
                            # 步长 = -delta / SCROLL_WHEEL_DIVISOR。
SCROLL_ROLL_UNITS = 20     # 一格对应的像素数（= 120 / SCROLL_WHEEL_DIVISOR）。
                           # X11 的 Button-4/5 没有 delta，归一成 ±120 后走同一条
                           # 换算；画布 yscrollincrement=1，所以 units 也是像素。

# ---- 缩放指针（1.5.26）----
# 光标是"沿控件树向上继承"的：给某个容器写了 `cursor=...`，它**所有**没显式
# 设过 cursor 的子控件都会跟着变 —— 只在"当前指针下的控件"上写、却不在离开时
# 对称复位，就会把祖先写脏，子控件于是永久顶着缩放箭头（见 app._on_root_motion）。
# 因此：窗口级的硬复位时机都集中到这几个常量控制。
CURSOR_RESET_DEBOUNCE_MS = 120   # <Configure>/<Map> 触发的复位去抖窗口

# ---- 逾期 / 今日到期提醒条（1.5.10 整条位图化）----
# 1.5.9 用"CTkFrame 底衬 + 四角抗锯齿补丁"失败：CTk 的 polygon 圆角与 PIL
# 不逐像素重合（毛刺），渐入动画两条渲染路径不同步（残影）；1.5.9 末改
# "canvas 位图底衬 + 文字控件叠加"也失败 —— CTkLabel(fg_color="transparent")
# 的**伪透明**会拿"父容器色"不透明地铺满，把底衬中部盖掉。所以 1.5.10 起图标、
# 文字、箭头、明细**全部画进同一张 PIL 位图**（4× 超采样 + LANCZOS），横幅里
# 不再放任何 CTk 控件，从根上杜绝两条渲染路径的层序与颜色同步问题。
BANNER_RADIUS = 14        # 提醒条圆角（逻辑像素；绘制前先夹 min(r, h//2, w//2)）
BANNER_HEAD_H = 36        # 折叠态高度（逻辑像素）
BANNER_PAD_X = 12         # 图标距左 / 箭头距右（逻辑像素）
BANNER_ICON = 14          # 左侧图标边长
BANNER_TEXT_GAP = 8       # 文字与图标的间距
BANNER_ARROW = 10         # 右侧展开箭头边长
BANNER_DETAIL_TOP = 8     # 明细首行距头部的间距（1.5.23：6 → 8，留出分隔线）
BANNER_DETAIL_LEAD = 11   # 明细行间距（1.5.29：8 → 11，配合"标题 + 日期"分层排版）
BANNER_DETAIL_MAX = 6     # 明细最多显示条数（更多以"…"收尾）
BANNER_DETAIL_INDENT = 6  # 明细相对头部内边距的额外缩进（1.5.23 新增）
BANNER_DETAIL_FONT_DELTA = 1   # 明细字号相对 tiny(10px) 的增量 → 11px
BANNER_DETAIL_LINE_H = 20      # 拖拽预览里一行的近似高度（正式绘制按字体度量算）
BANNER_DIVIDER_MIX = 0.26      # 头/明细之间那条细分隔线的白度（向底色插值）
# ---- 横幅分层与图标（1.5.29）----
BANNER_BODY_INSET = 2          # 描边内侧再压 1px 本体的留白（配合 BANNER_BORDER_W）
BANNER_BORDER_W = 1            # 极细描边宽度（逻辑像素）
BANNER_SPLIT = 0.56            # 上下两道的分界：占**头部高度**的比例（不是整条高度，
                               # 否则展开明细后分界线会跑到列表中间）
BANNER_BAND_H = 7              # 上道→下道过渡带的高度（逐行插值，越大越柔）
BANNER_GRAD_STEPS = 10         # 上道向下道的过渡层数（层数越多越平滑）
BANNER_ICON_EDGE_W = 1.4       # 图标描边宽度（逻辑像素）
BANNER_BULLET = 5              # 明细项目符号：圆润小方块边长
BANNER_BULLET_RADIUS = 1       # 方块圆角
BANNER_BULLET_GAP = 7          # 符号与标题的间距
BANNER_TITLE_GAP = 8           # 标题与日期之间的间距
BANNER_DATE_FONT_DELTA = -1    # 日期比明细正文再小一档（11 → 10px）

# ---- 无阴影提示卡（Canvas 位图 + 原生圆角窗口，不使用颜色键）----
BITMAP_SS = 4
TOOLTIP_RADIUS = 8        # 圆角
TOOLTIP_PAD_X = 10        # 文字左右内边距
TOOLTIP_PAD_Y = 6         # 文字上下内边距
TOOLTIP_BORDER_W = 1      # 描边宽度（逻辑像素）
TOOLTIP_MIN_W = 40        # 极短文案也不至于缩成一个小点
TOOLTIP_EDGE_PAD = 6
FLOAT_WATCH_MS = 120
POPUP_SHAPE_DELAY_MS = 30
TOAST_RADIUS = 14
TOAST_BORDER_W = 1
TOAST_ICON = 18
TOAST_PAD_X = 16
TOAST_PAD_Y = 10
TOAST_GAP = 8
TOAST_ACTION_W = 50
TOAST_ACTION_H = 24
TOAST_MAX_W = 320
TOAST_EDGE_PAD = 10
TOAST_BOTTOM_OFFSET = 116
TOAST_DURATION_MS = 2600
TOAST_MAX_LINES = 3
PARTICLE_MAX = 30
PARTICLE_FRAME_MS = 16
PARTICLE_DURATION_MS = 1500

# ---- 自绘标题栏（原生标题栏完全隐藏，窗口控制只此一套）----
TITLE_HEIGHT = 30         # 标题栏高度（40 -> 30）
TITLE_ICON = 13           # 品牌太阳图标
TITLE_BTN = 22            # 最小化 / 最大化 / 关闭按钮
TITLE_SIDE_PAD = 9        # 标题栏左右内边距

# ---- 分组标题行（紧凑：行高 28 / 图标 16 / 箭头 14）----
GROUP_HEADER_HEIGHT = 28
GROUP_ICON = 16           # 分组图标（自绘暖色，1.5.12 细线重绘）
GROUP_CHEVRON = 14        # 折叠箭头 ▾（12 -> 14：可发现性）
GROUP_CHEVRON_ANIM_MS = 150   # 折叠箭头旋转动画时长（只转图标，不动布局）
GROUP_CHEVRON_STEPS = 5       # 旋转帧数（22.5°/帧，与 icons 量化档一致）
GROUP_ICON_TEXT_GAP = 5   # 图标与名称间距
GROUP_BADGE_GAP = 4       # 名称与数量徽章间距
GROUP_CHEVRON_GAP = 2     # 折叠箭头与分组图标间距
GROUP_BADGE_W = 30        # 任务数徽章宽（42 -> 30）
GROUP_BADGE_H = 14        # 任务数徽章高（18 -> 14）
GROUP_BTN = 20            # 分组标题行右侧的 ＋ / ⋯ 按钮（26 -> 20）

# ---- 窗口尺寸（桌面小插件比例：默认 344×470，最小 320×400）----
# 最小尺寸是**逻辑像素**；overrideredirect 窗口没有系统边框，缩放由 app.py
# 自己在窗口四边/四角画一层透明热区实现（见 RESIZE_BAND）。
# 1.5.13 把下限从 310×380 抬到 320×400：按任务行"全元素同屏"实测阈值
# （色条+勾选框+标题下限+逾期徽章+时间+三个动作图标 ≈ 296 逻辑像素）加余量，
# 从源头保证最窄时标题仍有一段可读宽度、右侧图标区永不重叠。
MIN_WINDOW_W = 320
MIN_WINDOW_H = 400
RESIZE_BAND = 5           # 缩放热区宽度（逻辑像素），足够好点又不挡内容
RESIZE_BAND_CORNER = 10   # 四角热区（斜向缩放）边长

# 窗口最外圈描边宽度（逻辑像素，1.5.2 新增）。
# 做法：主窗口 ``fg_color`` 改成 ``window_border`` 色，四个区块（标题栏 /
# 顶部行 / 番茄横幅 / 内容）各向内缩这么多 —— 露出来的那一圈就是边框。
# 不用 DWM 的 ``DWMWA_BORDER_COLOR``：那是 Windows 11 才有的，而且
# ``overrideredirect`` 窗口没有非客户区，画不出来。
# 1 逻辑像素在 150% 屏上 = 2 物理像素，用户要的"1px-2px"正好落在区间内。
WINDOW_BORDER = 1

# 首次启动（无存档）的"最佳默认尺寸"：逻辑 344×470，居中屏幕。
# 上一版 440×700 是"手机端 App"的思路（单列、大留白），在桌面上像一块
# 竖着贴的告示板。桌面小插件要的是"放在角落一直看得见"：一屏 3 个分组、
# 每个分组几行任务就够，多出来的靠滚动。
DEFAULT_WINDOW_W = 344
DEFAULT_WINDOW_H = 470

# 布局代次（layout revision）。每次"默认排版发生结构性改变"就 +1。
# 作用：存档里存的是上一版的窗口尺寸（比如 440×700），而新版默认是
# 344×470 —— 直接沿用旧尺寸会让用户以为改动没生效。启动时若代次不一致，
# 就丢弃存档几何、回到新默认值。
LAYOUT_REV = 2

# 窗口几何落盘策略（需求 12）
GEOMETRY_SAVE_DELAY = 500   # 停止拖拽/缩放 500ms 后才写 config，避免每帧写盘
GEOMETRY_MIN_VISIBLE = 120  # 至少要有这么大一块落在可用区里，否则视为"存档已失效"

# 缩放期间的"昂贵重绘"节流（需求 13）：
# 拖边框时每一帧都重算药丸投影/渐变柱状图会让画面抖成一片残影。
# 缩放中只做布局，停手 200ms 后再补一次全量重绘。
RESIZE_REDRAW_DELAY = 200

# ---- 新建任务浮层（替代底部常驻输入框；本轮改为紧凑型）----
# ❗尺寸上限的硬约束：浮层**必须**完整落在主窗口内。
# 上一版 POPUP_WIDTH=320 / POPUP_MAX_W=460，含投影最宽 468 —— 而主窗口最小
# 只有 380、默认 440，于是"生活"分组被裁在窗口外（用户报的图1/图2）。
# 现在把上限收到 MIN_WINDOW_W - 左右各 8 的安全边距 = 364，
# 保证最小窗口下也装得下、不越界。
POPUP_WIDTH = 268         # 设计宽度（内容不足时的下限）
POPUP_RADIUS = 9
POPUP_GAP = 5             # 浮层与锚点按钮的垂直间距
POPUP_SHADOW = 4          # 投影层数（向外渐隐的描边环）
POPUP_CHIP_H = 22         # 分组 chip 高度
POPUP_CHIP_GAP = 4        # chip 之间的间距
POPUP_CHIP_ICON = 14      # chip 内的分组图标
POPUP_CHIP_FONT = "tiny"  # chip 文字字号角色（10px，集中管理，不硬编码）
POPUP_ROW_GAP = 4         # 各行之间的间距
POPUP_EDGE_PAD = 8        # 浮层与屏幕/窗口边缘的安全间隙
POPUP_ENTRY_H = 24        # 输入行高度（含日历按钮）
POPUP_BTN_W = 52          # 底部 确认/取消 按钮宽
POPUP_BTN_H = 22          # 底部按钮高

# 浮层的描边与投影（本轮新增：与暖白页面对比太弱，边界看不出来）
# 问题量化：卡片 #FFFFFF 亮度 255，页面 #FAF7F2 亮度 247.3 —— **只差 7.7**，
# 加上原来的投影把 65%~85% 都混向了页面色 → 视觉上"白底叠白底"，
# 用户看不出浮层边界在哪。
# 修法：①加一圈描边（#D5C3AC 与卡片亮度差 57.8，是原来的 7.5 倍）；
#       ②投影加深（少混页面色、加大偏移）。
POPUP_BORDER = ("#D5C3AC", "#6B6156")   # 描边色（浅色 / 深色）
POPUP_BORDER_W = 1                       # 描边宽度（逻辑像素）
# 内容区距"描边内边缘"的呼吸位（1.5.9）。它必须是**四边相同**的一份内衬 ——
# 原来内容窗口用 "画布高 − 投影×2" 起算，比卡片本体多伸出去 1px，正好把
# 下边框整条盖掉（用户报的"底部圆角边框没显示出来"）。
POPUP_BODY_INSET = 2
POPUP_SHADOW_TINT = 0.18                 # 投影最内层混向页面的比例（越小越深）
POPUP_SHADOW_TINT_OUT = 0.72             # 最外层混向页面的比例（渐隐到近乎看不见）

# 浮层的设计高度：**由上往下逐行累加**，而不是写死一个数。
# ❗上一版写死 76，是"输入行(32) + 分组行(40)"的两行口径。本轮新增了第三行
# （确认/取消按钮），76 就不再成立 —— 实测三行内容 84 逻辑像素 > 76，
# 于是输入框和 chip 条被**自己的卡片边缘裁掉**（截图里 placeholder 只剩半行）。
# 现在改成派生值：任何一行的高度/间距改了，这里自动跟着变，不会再脱节。
POPUP_HEIGHT = (
    POPUP_RADIUS                      # 卡片上内边距
    + POPUP_ENTRY_H                   # 第一行：常驻输入框
    + POPUP_ROW_GAP
    + POPUP_CHIP_H                    # 第二行：分组标签条
    + POPUP_ROW_GAP
    + POPUP_BTN_H                     # 第三行：确认 / 取消
    + POPUP_ROW_GAP + POPUP_RADIUS    # 卡片下内边距
)
# 柔橘饱和底上的前景色：浅色模式用白、深色模式用近黑（柔橘在深色下偏亮，
# 白字对比度不够）。浮层的选中 chip、主按钮共用这一对，别再各写一份。
ON_ACCENT = ("#FFFFFF", "#2D2A26")
POPUP_ON_ORANGE = ON_ACCENT
# 朱红底（due_over）上的前景色：浅色模式的朱红很深，白字 5.4:1；
# 深色模式的 due_over 提亮成 #E8756A（浅橙红），再配白字只剩 2.9:1 ——
# 徽章上的"逾期"两个字基本糊在一起。深色档换暖棕近黑，实测 4.98:1。
ON_DANGER = (LIGHT["on_danger"], DARK["on_danger"])
# chips 行的宽度上限。超过就横向滚动 —— 从前这里是"按字数估算宽度"，
# 估偏了就把第三个分组直接裁掉（截图里只剩"工作/学习"就是这个原因）。
# 现在改成按**实测 reqwidth** 定浮层宽度，这里只兜住上限。
# 上限 = 主窗口最小宽 - 两侧安全间隙（保证不溢出主窗口）。
POPUP_MAX_W = MIN_WINDOW_W - POPUP_EDGE_PAD * 2   # 310 - 16 = 294
POPUP_FADE_OUT = 4        # 收起时的淡出帧数（150ms 左右）
POPUP_FADE_OUT_INTERVAL = 24
POPUP_BLUR_GRACE = 150    # 失焦后延迟多少毫秒才判定"真的离开了"
# 刚弹出后的"免失焦"窗口（毫秒）。必须 > 0，否则会偶发丢提交：
# 浮层打开的头几拍，焦点要在 主窗口 → 浮层 → 输入框 之间交接，
# 任何一次交接都会发 <FocusOut>，延迟 POPUP_BLUR_GRACE 后一判就是"焦点不在
# 浮层里"，浮层当场开始淡出（_closing=True）——而此刻用户刚按下回车，
# submit() 看见 _closing 直接 return，**任务静默丢失**。
POPUP_OPEN_GRACE = 450
POPUP_SAFE_ZONE = 16      # 指针在这个外扩范围内也算"还在浮层上"，防误收

# 前台看门狗（1.5.1 新增，修"切到别的应用后浮层还挂在最上层"）
# -----------------------------------------------------------------
# 只靠 <FocusOut> 不可靠：浮层是 overrideredirect 顶层窗口，某些切换路径下
# 焦点事件根本不会送到它（用户原来遇到的正是"Alt+Tab 走了，浮层还在"）。
# 所以再排一路**轮询**：每 POPUP_WATCH_MS 问一次系统"前台窗口是不是本进程的"，
# 连续 POPUP_WATCH_STRIKES 次都不是才收 —— 单次判定会在 Alt+Tab 的中间态抖动，
# 连续两次（≈600ms）既稳又不会让人觉得"赖着不走"。
POPUP_WATCH_MS = 300
POPUP_WATCH_STRIKES = 2

# ---- 图标选择浮层（需求 26：更换分组图标）----
ICON_PICK_COLS = 4          # 每行几个
ICON_PICK_CELL = 38         # 单元格边长
ICON_PICK_GAP = 8           # 单元格间距
ICON_PICK_PAD = 14          # 浮层内边距
ICON_PICK_RADIUS = 12
ICON_PICK_TOP_GAP = 8       # 标题与网格的间距
ICON_PICK_ROW_GAP = 8       # 网格行间距

# ---- 截止日期选择器（本轮：整体压紧，贴合主界面的小卡片调性）----
# 上一版日历格子 38×30、内边距 18，整个弹窗在 150% 屏上接近 750px 高，
# 比主窗口还壮观。这里全部收紧一档：格子 28×22、内边距 14、圆角 8，
# 让弹窗落在 300×430 逻辑像素附近。
DUE_PAD_X = 14            # 弹窗左右内边距
DUE_PAD_TOP = 14
DUE_PAD_BOTTOM = 12
DUE_CELL_W = 28           # 日历格子宽
DUE_CELL_H = 24           # 日历格子高
DUE_CELL_RADIUS = 7
DUE_CELL_GAP = 1          # 格子间距
DUE_HEAD_H = 30           # 月份切换行高度
DUE_NAV_W = 26            # ◀ ▶ 按钮尺寸
DUE_NAV_COL_W = 52        # 顶部栏左右列 minsize（1.5.21）：≥「‹ 日历」贴合宽。
                          # 等权只均分**多余**空间，左右列请求宽不同（左有返回
                          # 按钮、右空）时列宽仍不等 → 标题偏一个按钮宽。
                          # 左右列同 minsize 同权重 → 恒等宽，标题绝对居中。
DUE_TIME_W = 42           # 时/分"字段"按钮宽（1.5.2：原来是 60 宽的下拉框，
                          # 展开后 24 项铺满屏幕；现在只是两个数字按钮）
DUE_TIME_H = 24           # 时/分字段按钮高
DUE_WEEKDAY_H = 16        # 星期表头行高（时间模式下该行被收起，靠 row minsize 兜住）
DUE_PICK_CELL_H = 24      # 提醒选项格高（与日历格子等高 → 切模式弹窗不跳）
# 时/分滚轮选择器（1.5.20：小时 24 格 / 分钟 60 格的按钮网格废弃，改滚轮）
DUE_WHEEL_ITEM_H = 30     # 滚轮单行高（选中行）
DUE_WHEEL_VISIBLE = 5     # 可见行数（中间选中 + 上下各 2 行渐隐）
DUE_WHEEL_SNAP_MS = 140   # 滚轮停下后多久吸附到最近一行
DUE_QUICK_H = 24          # 今天/明天/后天/清除 按钮高度
DUE_QUICK_W = 52          # 快捷按钮宽度（不给宽度时 CTkButton 默认 140，
                          # 几个并排会把弹窗撑到 700+ 逻辑像素宽！）
DUE_BTN_W = 74            # 取消/确定按钮宽
DUE_BTN_H = 28
DUE_ROW_GAP = 8           # 各区块之间的垂直间距
DUE_MIN_W = 300           # 日期选择器的居中下限宽（通用对话框是 340，它更窄）
# 提醒选择器（1.5.3 新增，位于"时间"行下方；1.5.5 从下拉改为"字段 + 就地网格"）
# 「提醒」字段按钮宽：按最长文案「提前15分钟」在 small(12px) 下实测 82
# 逻辑像素（fit_width(..., pad_x=10)）。改文案要同步复测这个值。
DUE_REMIND_W = 82
DUE_REMIND_COLS = 2         # 选项网格列数：7 项 → 2 列 × 4 行，落在 6 行网格区里
DUE_REMIND_CELL_W = 126     # 选项格子宽（2 列 126 + 间距 ≈ 254，窄于日历区 272）
# ❗提醒选择器**不是下拉**（1.5.5）：CTk 的 DropdownMenu 继承原生 `tkinter.Menu`，
# 圆角/边框/阴影全由 Windows 画（实测 `borderwidth=6` 且无法去除），深浅色只能改
# bg/fg 两三个选项 —— 与暖米色体系天然不兼容。改成与时/分完全一致的
# "字段按钮 + 就地网格"，零新增浮层、零尺寸增长，也不会和弹窗的 grab_set 打架。
# 视觉口径：格子底=弹窗底色（浅米），文字=text（深灰棕），悬停=accent_soft（柔橘），
# 选中=accent —— 全部复用日历格子的 `_cell()`，无边框、无阴影、统一圆角。

# ---- 删除/确认弹窗（1.5.3：整体压紧，跟桌面小插件的比例对齐）----
# 上一版直接用系统标题栏 + CTkToplevel：标题栏高度由系统定（改不动），
# 宽度又跟着正文 wraplength 走 —— 344×470 的主窗口上弹出 340 宽的确认框，
# 删一条任务和整个应用一样宽。现在改成自绘标题栏，宽度锁在 268。
CONFIRM_W = 268           # 设计宽度（需求区间 260–280 的中间）
CONFIRM_MIN_W = 260       # 需求下限
CONFIRM_MAX_W = 280       # 需求上限
CONFIRM_RADIUS = 10
CONFIRM_PAD_X = 12
CONFIRM_PAD_TOP = 8
CONFIRM_PAD_BOTTOM = 10
CONFIRM_ICON = 16         # 标题栏品牌太阳图标（需求：缩小至 16px）
CONFIRM_TITLE_GAP = 6     # 图标与标题的间距
CONFIRM_CLOSE = 16        # 右上角关闭按钮
CONFIRM_MSG_GAP_TOP = 6   # 标题栏 -> 提示语
CONFIRM_MSG_GAP_BOTTOM = 10
CONFIRM_BTN_H = 24        # 底部按钮高（小尺寸圆角按钮）
CONFIRM_BTN_MIN_W = 46    # 按钮宽度下限（"取消"这类两字按钮）
CONFIRM_BTN_PAD_X = 9     # 按钮左右内边距（按文字宽算贴合宽度用）
CONFIRM_BTN_GAP = 6       # 取消 / 确定 之间的间距

# 模态对话框居中时的下限尺寸（逻辑像素）。原先是写死在 dialogs._center_on 里的
# 340 / 180 —— 紧凑化之后日期选择器只需要 ~306 宽，被那 340 硬撑开会白白多出
# 一圈空档（日历格子被拉散）。改成主题常量，各对话框可以用 MIN_DIALOG_W 覆盖。
DIALOG_MIN_W = 340
DIALOG_MIN_H = 180

# 投影：tkinter 没有原生阴影，用"卡片外一圈渐隐的浅色描边"近似。
# 需求 2 给的参数（offset_y=2 / blur=12 / #E8DFD2 / alpha=120）无法直接表达，
# 这里存的是插值用的"阴影→背景"两端色，供 Canvas 逐圈画。
SHADOW_STEPS = 4

# ---- 空状态（需求 ④：插画 + 文案的垂直节奏）----
# 文案本身在 shiguang/strings.py 统一管理（全代码库只留一处定义）。
EMPTY_PAD_TOP = 12         # 卡片顶 → 插画
EMPTY_ICON_GAP = 8         # 插画 → 主文案
EMPTY_TEXT_GAP = 3         # 主文案 → 副文案
EMPTY_CTA_TOP = 10         # 副文案 → 按钮
EMPTY_PAD_BOTTOM = 14      # 按钮 → 卡片底
EMPTY_SIDE_PAD = 12        # 文案折行时左右各留的边距
EMPTY_ILLUS_W = 150        # 空状态插画画布（逻辑像素）
EMPTY_ILLUS_H = 96
# 空状态文案的折行宽度（逻辑像素）。**必须有值**：CTkLabel 默认不折行，
# 窗口被拖窄时整句话会直接溢出卡片（截图里"文字被裁"就是这么来的）。
# 取 268 是"最小窗宽 310 − 两侧 10 页面内边距 − 卡片内边距"的量级。
EMPTY_WRAP = 268


# 菜单、图标面板和任务动画的既有样式参数。
MENU_WIDTH = 180
MENU_ROW_HEIGHT = 34
MENU_ICON_SIZE = 18
MENU_ICON_TEXT_GAP = 8
MENU_PAD_X = 12
MENU_RADIUS = 12
MENU_SEP_HEIGHT = 9
MENU_MAX_LABEL_CHARS = 8
MENU_FADE_STEPS = 6
MENU_FADE_INTERVAL = 16
MENU_WATCH_INTERVAL = 120
MENU_WATCH_MISS_LIMIT = 2
MENU_SAFE_ZONE = 14
MENU_MOVE_TOLERANCE = 6
ICON_PICKER_COLS = 4
ICON_PICKER_CELL = 44
ICON_PICKER_GAP = 8
ICON_PICKER_PAD = 12
ICON_PICKER_RADIUS = 14
ICON_PICKER_HEADER = 30
ICON_PICKER_ICON_IN_CELL = 20
ICON_PICKER_ICON_CENTER_Y = 14
ICON_PICKER_LABEL_BOTTOM = 3
ICON_PICKER_FADE_STEPS = 6
ICON_PICKER_FADE_INTERVAL = 16
ICON_PICKER_WATCH_INTERVAL = 140
ICON_PICKER_WATCH_MISS_LIMIT = 2
ICON_PICKER_WATCH_GRACE_TICKS = 4
ICON_PICKER_SAFE_ZONE = 14
QUICK_ADD_FADE_STEPS = 5
QUICK_ADD_FADE_INTERVAL = 20
TASK_FADE_STEPS = 6
TASK_FADE_INTERVAL = 25
TASK_TITLE_MIN_WRAP = 72
TASK_ACTIONS_PAD_X = 4
GROUP_CHIP_DRAG_TOLERANCE = 4
ONBOARDING_SUN_SIZE = 108
TASK_DRAG_THRESHOLD = 5
BANNER_FADE_STEPS = 45
BANNER_FADE_INTERVAL = 40
BANNER_BITMAP_SS = BITMAP_SS
TASK_CHECK_PATH = ((-0.135, 0.012), (-0.041, 0.113), (0.150, -0.105))

# 图标品牌调色板；图形路径保留在 icons.py。
ICON_PALETTE = {
    "orange": "#E89A4A",
    "gold": "#E8B84A",
    "deep_gold": "#D4B872",
    "vermilion": "#E0553F",
    "red": "#C0392B",
    "blue": "#6B93C4",
    "salmon": "#C97B6B",
    "brown": "#6B5F52",
    "warm_brown": "#B08D57",
    "warm_clasp": "#D4B887",
    "soft_blue": "#7BA3C9",
    "page_blue": "#A8C4DE",
    "grass": "#8FB887",
    "grass_light": "#C2DDBE",
    "leaf": "#5C9E4A",
    "tomato_highlight": "#F08070",
    "heart_light": "#D98A7A"
}
ICON_SIZE_MAIN = 18
ICON_SIZE_GROUP = 16
ICON_SIZE_SMALL = 14
ICON_INNER_SHADOW_DARK = 0.16
ICON_INNER_SHADOW_LIGHT = 0.22


def is_dark() -> bool:
    """当前是否深色（供 Canvas 绘制判断）。"""
    return ctk.get_appearance_mode().lower() == "dark"


def pair(key: str) -> Tuple[str, str]:
    """(浅色, 深色) 颜色对，直接喂给 CTk 控件。"""
    return (LIGHT.get(key, "#000000"), DARK.get(key, "#FFFFFF"))


def c(key: str) -> str:
    """当前模式下的实际颜色值。"""
    return (DARK if is_dark() else LIGHT).get(key, "#000000")


def priority_color(priority: int) -> str:
    return {0: c("pri_high"), 1: c("pri_mid"), 2: c("pri_low")}.get(priority, c("pri_mid"))


def priority_color_pair(priority: int) -> Tuple[str, str]:
    key = {0: "pri_high", 1: "pri_mid", 2: "pri_low"}.get(priority, "pri_mid")
    return pair(key)


def mix(color_a: str, color_b: str, t: float) -> str:
    """在两个十六进制颜色之间线性插值，t=0 取 a，t=1 取 b。

    tkinter 的 Canvas 不支持透明度，所以"淡出"效果用向背景色插值来模拟。
    """
    t = max(0.0, min(1.0, t))
    a = color_a.lstrip("#")
    b = color_b.lstrip("#")
    try:
        ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    except ValueError:
        return color_a
    return "#%02x%02x%02x" % (
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


# --------------------------------------------------------------------------
# 字体（转发到 shiguang.fonts 统一管理）
# --------------------------------------------------------------------------
# 字体栈、字号层级、DPI 自适应、行高计算全部收敛到 ``shiguang.fonts``。
# 这里只留一层薄转发，理由有两条：
#   1. 历史调用点有 100+ 处 ``theme.font("small")``，一次性改完风险大；
#   2. 字体是"主题"的一部分，从 theme 取读起来更自然。
# 新增代码请直接用 ``fonts.font(role)`` / ``fonts.tkfont_spec(role)``。
#
# 旧角色名 title/h2 由 fonts.ALIASES 映射到新角色；number 已是 ROLES 里的真实键。


def init_fonts(root=None, preferred: str = "") -> str:
    """在 Tk root 创建后调用一次。返回选中的中文字体族名。"""
    picked = _fonts.init(root, preferred)
    return picked.get("cjk", "TkDefaultFont")


def family(kind: str = "cjk") -> str:
    """取字体族名。``kind``：cjk / title / latin / mono / emoji。"""
    return _fonts.family(kind)


def font(role: str = "body", size_delta: int = 0) -> ctk.CTkFont:
    """按语义角色取字体（转发到 fonts.font，字号已做 DPI 自适应）。"""
    return _fonts.font(role, size_delta)


def font_size(role: str = "body") -> int:
    """取某个角色的**逻辑**字号（px）。

    用途是"按文字宽度反推控件宽度"这类场景 —— 比如浮层里分组 chip 的
    贴合宽度。别拿它去设控件字体（那是 :func:`font` 的事）。
    """
    return _fonts.ROLES[_fonts.ALIASES.get(role, role)][0]


def fit_width(text: str, role: str = "small", icon: int = 0,
              pad_x: int = 6, gap: int = 3) -> int:
    """按内容算控件的**贴合宽度**（逻辑像素），直接喂给 CTk 的 ``width``。

    存在的理由只有一个：``CTkButton`` 不给 ``width`` 时默认 **140 逻辑像素**
    （不是"贴合内容"）。一行里排 N 个这种按钮，N×140 会直接把容器撑爆 ——
    本项目已经在两处被它坑过：浮层的分组 chip 三个要 705 物理像素（可视区
    只有 489，「生活」被推出视野）、日期选择器的 5 个快捷按钮撑到 700px 宽。

    参数里的 ``icon`` 只在控件带图标时给（``compound="left"`` 的图文按钮）；
    纯文字按钮留 0。返回值已经包含左右内边距与图标/文字之间的间距。
    """
    try:
        text_w = _fonts.measure(role, text)
    except Exception:  # noqa: BLE001
        text_w = len(text) * font_size(role)
    extra = (icon + gap) if icon else 0
    return int(pad_x * 2 + extra + text_w + 1)


def tkfont_spec(role: str = "body"):
    """Canvas ``create_text`` 用的 ``(family, size, weight)``。"""
    return _fonts.tkfont_spec(role)


def line_height(role: str = "body") -> int:
    """精确行高（metrics 计算，不要硬编码）。"""
    return _fonts.line_height(role)


def scale() -> float:
    """当前 DPI 缩放系数。"""
    return _fonts.scale()


def lpx(logical: float) -> int:
    """把**布局尺寸**换算成当前 DPI 下的实际像素。

    为什么布局也要换算：customtkinter 的 ``geometry()`` 会把窗口尺寸乘上
    ``window_scaling``，而 Tk 层面的控件是按物理像素布局的。150% 屏上你写的
    ``420x640`` 窗口，``winfo_width()`` 实际是 **630** —— 控件拿到 630px 的空间，
    但 ``wraplength=176`` 这类写死的布局提示还是按 420 的思路给的，
    结果文本过早折行、元素之间出现大片空白（截图里最明显的就是这个）。

    规律：**凡是"以像素为单位参与布局"的数字都要过这里** —— wraplength、
    Canvas 尺寸、固定宽高、间距。字号不用（``font()`` 内部已处理）。
    """
    return _fonts.ipx(logical)
