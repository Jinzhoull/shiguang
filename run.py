#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拾光 Shiguang —— 启动入口。

用法::

    python run.py                 # 正常启动
    python run.py --version       # 查看版本
    python run.py --data-dir      # 打印数据目录
    python run.py --selftest      # 不打开界面，跑一遍核心逻辑自检
    python run.py --screenshot    # 渲染主界面并另存为 PNG（用于预览/回归）
    python run.py --quick-add "写周报:工作:高"   # 命令行快速添加一条任务
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _print_help() -> None:
    print(__doc__)


def main() -> int:
    from shiguang import __app_name__, __slogan__, __version__

    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        _print_help()
        return 0
    if "--version" in argv or "-v" in argv:
        print(f"{__app_name__} v{__version__} —— {__slogan__}")
        return 0
    if "--data-dir" in argv:
        from shiguang.config import data_dir

        print(data_dir())
        return 0
    if "--diag" in argv:
        from shiguang.app import _enable_dpi_awareness, diagnostics

        _enable_dpi_awareness()      # 先设置再读取，否则量到的是"未感知"状态
        for key, value in diagnostics().items():
            print(f"{key:22} = {value}")
        return 0
    if "--selftest" in argv:
        from tests.test_core import run

        return run()
    if "--screenshot" in argv:
        from tools.screenshot import capture

        idx = argv.index("--screenshot")
        out = argv[idx + 1] if len(argv) > idx + 1 and not argv[idx + 1].startswith("-") else ""
        return capture(out)
    if "--quick-add" in argv:
        idx = argv.index("--quick-add")
        if len(argv) > idx + 1:
            from shiguang.models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MID
            from shiguang.store import Store

            raw = argv[idx + 1]
            parts = [p.strip() for p in raw.split(":")]
            title = parts[0]
            group_name = parts[1] if len(parts) > 1 else ""
            priority_name = parts[2] if len(parts) > 2 else "中"
            store = Store()
            group_id = ""
            for grp in store.groups:
                if grp.name == group_name:
                    group_id = grp.id
                    break
            if not group_id and group_name:
                group_id = store.add_group(group_name).id
            task = store.add_task(title, group_id,
                                  {"高": PRIORITY_HIGH, "中": PRIORITY_MID, "低": PRIORITY_LOW}
                                  .get(priority_name, PRIORITY_MID))
            store.save()
            print(f"已添加：{task.title}（{task.priority_name}）")
            return 0

    from shiguang.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())
