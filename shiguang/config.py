# -*- coding: utf-8 -*-
"""全局配置：路径解析、默认设置、应用元信息。

设计要点
--------
1. 数据目录遵循各平台惯例（Windows 用 %APPDATA%，macOS 用 Application Support，
   Linux 用 XDG_DATA_HOME），并允许通过环境变量 ``SHIGUANG_DATA_DIR`` 覆盖，
   方便绿色版把数据放在 U 盘或程序同目录。
2. 运行期生成的资源（图标、提示音）统一落到数据目录下的 ``assets/``，
   这样打包时不必内置二进制资源，PyInstaller 配置更简单、体积更小。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

from . import __app_name__, __app_name_en__, __slogan__, __version__

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------


def data_dir() -> Path:
    """返回数据目录（自动创建）。"""
    env = os.environ.get("SHIGUANG_DATA_DIR")
    if env:
        path = Path(env).expanduser()
    elif sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        path = Path(base) / __app_name_en__
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / __app_name_en__
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
        path = Path(base) / "shiguang"
    path.mkdir(parents=True, exist_ok=True)
    return path


def assets_dir() -> Path:
    """运行期生成资源目录（图标 / 提示音）。"""
    path = data_dir() / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_file() -> Path:
    """主数据文件（JSON）。"""
    return data_dir() / "data.json"


def log_file() -> Path:
    return data_dir() / "shiguang.log"


def resource_dir() -> Path:
    """源码/打包环境下静态资源根目录（当前未强依赖，保留给扩展用）。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# 默认数据
# --------------------------------------------------------------------------

DEFAULT_GROUPS = [
    {"id": "grp_work", "name": "工作", "icon": "💼", "order": 0, "collapsed": False},
    {"id": "grp_study", "name": "学习", "icon": "📖", "order": 1, "collapsed": False},
    {"id": "grp_life", "name": "生活", "icon": "🌿", "order": 2, "collapsed": False},
]

DEFAULT_SETTINGS: Dict[str, Any] = {
    # 外观
    "theme": "system",            # system | light | dark
    "font_family": "",            # 空 = 自动挑选系统柔和字体
    # 窗口
    "always_on_top": False,
    "close_to_tray": True,        # 关闭按钮 -> 最小化到托盘
    # 窗口几何：本轮（需求 12）改由 "window_geometry" 承担，只在用户**停止
    # 拖拽 500ms 后**写入，且写前校验"别小于最小尺寸、别跑到屏幕外"。
    # 旧的 "geometry" 键保留：老数据文件里只有它，读的时候当兜底。
    "geometry": "",               # （旧键，只读）"420x640+120+80"
    "window_geometry": "",        # "440x700+520+160"
    # 快捷键
    "hotkey_enabled": True,
    "hotkey": "Ctrl+Shift+T",
    # 番茄钟
    "pomodoro_minutes": 25,
    "break_minutes": 5,
    "sound_enabled": True,
    # 提醒（截止日期相关）
    "due_notify": True,           # 到期发 Windows 系统通知
    "due_banner": True,           # 主页显示"逾期 / 今日到期"提醒条
    "due_particles": True,        # 全部完成时的粒子特效
    # ⚠️ 1.5.3 起"提前多久提醒"改由**每条任务**的 remind_offset 决定
    #    （见 models.REMIND_OPTIONS，默认提前 15 分钟）。
    #    due_soon_minutes 因此不再参与提醒判定，保留只为读旧配置文件不报错。
    "due_soon_minutes": 30,       # （旧键，已失效）
    "due_just_minutes": 5,        # 提醒判定窗口下端（分钟）：放过期后这一小段
    # 一次性提示
    "tray_tip_shown": False,      # 是否已经提示过"拾光已藏到托盘"
    # 引导
    "onboarded": False,
    # 默认分组（快速添加用）
    "default_group": "grp_work",
}

# 数据版本：
#   2 = 任务新增 due_date 字段（旧文件可无损读取，缺字段即"没有截止日期"）
#   3 = 任务新增 remind_offset 字段（缺字段按默认提前量，显式 null = 不提醒）
#   4 = 新增 focus_history / total_focus（每日专注记录，1.5.11；
#       旧文件缺字段按 0 处理，无需迁移）
DATA_VERSION = 4


def default_data() -> Dict[str, Any]:
    """全新用户的初始数据结构。

    ``group_icons`` 必须**每次新建一个空字典**，不能在 ``DEFAULT_SETTINGS``
    里放一个字面量 ``{}`` —— 那是一个共享对象，``dict(DEFAULT_SETTINGS)``
    只做浅拷贝，两个 Store 实例会改到同一份映射（测试里立刻互相污染）。
    """
    settings = dict(DEFAULT_SETTINGS)
    settings["group_icons"] = {}      # {group_id: icon_key}，见 store.set_group_icon
    return {
        "version": DATA_VERSION,
        "settings": settings,
        "groups": [dict(g) for g in DEFAULT_GROUPS],
        "tasks": [],
        # history: {"2026-09-20": 5}  —— 按天累计完成数，删除任务也不会丢历史
        "history": {},
        "total_completed": 0,
        # focus_history: {"2026-09-25": 3} —— 按天累计完成的专注（番茄）数；
        # total_focus 是全量计数器（不随任务删除/清空而丢失）
        "focus_history": {},
        "total_focus": 0,
    }
