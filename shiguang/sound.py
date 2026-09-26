# -*- coding: utf-8 -*-
"""播放提示音。

音频**合成**在 :mod:`assets_gen`（纯 Python 生成柔和和弦 WAV），
本模块只负责"放"，并按平台选择播放方式：

* Windows：``winsound``（异步播放，不阻塞界面）
* macOS：``afplay``
* Linux：``paplay``

任何一步失败都退化为系统提示音，绝不因为放不出声就中断主流程。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from .assets_gen import CHIME_WAV, ensure_chime


def play(what: str = "chime", enabled: bool = True) -> None:
    """播放提示音（异步，不阻塞 UI 线程）。"""
    if not enabled:
        return
    path = ensure_chime() if what == "chime" else ensure_chime()
    _play_file(path)


def _play_file(path: Optional[Path]) -> None:
    if path is None or not Path(path).exists():
        return
    try:
        if sys.platform.startswith("win"):
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            return
        cmd = ["afplay", str(path)] if sys.platform == "darwin" else ["paplay", str(path)]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        _fallback_beep()


def _fallback_beep() -> None:
    try:
        if sys.platform.startswith("win"):
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
            return
        sys.stdout.write("\a")
    except Exception:  # noqa: BLE001
        pass


__all__ = ["play", "ensure_chime", "CHIME_WAV"]
