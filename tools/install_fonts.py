# -*- coding: utf-8 -*-
"""把字体装进**当前用户**（不需要管理员权限），并广播 WM_FONTCHANGE。

为什么需要这个工具
------------------
拾光的字体栈（``fonts.py``）优先用 HarmonyOS Sans SC / MiSans / 得意黑 / Inter，
但这些字体 Windows 默认都不带。用户机器上缺它们时，``fonts.family()`` 会静默
顺位到 Noto Sans SC —— 能用，但和设计稿的观感差一截；更麻烦的是 Noto Sans SC
装的是**可变字体**，默认实例是 Thin（wght=100），任何不显式设轴的渲染器都会
把它画成极细的笔形（看起来就是"字体发虚"）。

做法与资源管理器的"为当前用户安装"一致，只是全命令行、可重复执行：

1. 复制到 ``%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts``；
2. 在 ``HKCU\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts`` 登记
   ``"<字体全名> (TrueType)" -> <字体文件全路径>``；
   ❗值必须是**全路径**。写成裸文件名时 Tk/GDI 看不到这个字体
   （实测：同一目录下的 ``华文楷体`` 用全路径登记才出现），
   Explorer 自己写的也是全路径。
3. 广播 ``WM_FONTCHANGE``，已在运行的进程据此重读字体表。

双名登记
--------
注册名的用途只是给控制台/资源管理器看，GDI 实际读的是字体文件里的 name 表。
但本项目的 ``fonts._font_file()`` 是**按族名去注册名里找文件**的，所以注册名
必须包含族名。中文族名（如得意黑的族名就是「得意黑」）与英文全名（Smiley Sans
Oblique）对不上，于是这里**两个名字都登记一条**，指向同一个文件 —— GDI 按文件
去重，不会产生重复字体。

用法
----
    python tools/install_fonts.py a.ttf b.otf          # 装指定文件
    python tools/install_fonts.py --zip pack.zip       # 解开压缩包，装里面全部字体
    python tools/install_fonts.py --list               # 只看会装什么，不落盘
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import zipfile
from typing import Iterable, List, Tuple

REG_SUB = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")
HWND_BROADCAST = 0xFFFF
WM_FONTCHANGE = 0x001D
SMTO_ABORTIFHUNG = 0x0002

# 常见字体的"族名 -> 建议来源"，装字体时打印出来方便照抄。
# 不联网、不自动下载 —— 只想说清"该去哪儿拿"，下载仍由用户自己决定。
SUGGESTED = {
    "HarmonyOS Sans SC": "华为 HarmonyOS 设计资源（免费商用，静态多字重）",
    "MiSans": "小米 MiSans 官网（免费商用）",
    "得意黑 / Smiley Sans": "github.com/atelier-anchor/smiley-sans",
    "Inter": "github.com/rsms/inter",
    "Source Han Sans SC / 思源黑体": "github.com/adobe-fonts/source-han-sans",
}


def font_dir() -> str:
    return os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Windows", "Fonts")


def registry_names(path: str) -> List[str]:
    """该字体应该登记的注册名（英文全名 + 本地化族名，各一条）。"""
    try:
        from PIL import ImageFont

        family, style = ImageFont.truetype(path, 12).getname()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"读不出字体名：{exc!r}") from exc
    family = (family or "").strip()
    style = (style or "").strip()
    base = family if style.lower() in ("", "regular", "normal", "book") \
        else f"{family} {style}"
    names = [f"{base} (TrueType)"]
    # 本地化族名：中文界面下 Tk 报的是「得意黑」而不是「Smiley Sans Oblique」，
    # 注册名里不带它，PIL 侧的族名->文件映射就会落空。
    local = _localized_family(path)
    if local and local != family:
        extra = local if style.lower() in ("", "regular", "normal", "book") \
            else f"{local} {style}"
        if f"{extra} (TrueType)" not in names:
            names.append(f"{extra} (TrueType)")
    return names


def _localized_family(path: str) -> str:
    """读 name 表里的**本地化**族名（拿不到返回空串）。

    为什么要自己解 name 表：PIL 的 ``getname()`` 只给英文名（Smiley Sans），
    而 Tk/GDI 报的是本地化名（得意黑）—— ``fonts._font_file()`` 是按族名去
    注册名里找文件的，不把本地化名也登记一条，PIL 那条通道就永远解析不到。
    不依赖 fontTools：直接按 sfnt 头 + 表目录 + name 表记录读，二十来行。
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if len(head) < 12:
                return ""
            num_tables = int.from_bytes(head[4:6], "big")
            tables = {}
            for _ in range(num_tables):
                entry = fh.read(16)
                if len(entry) < 16:
                    break
                tag = entry[:4]
                offset = int.from_bytes(entry[8:12], "big")
                length = int.from_bytes(entry[12:16], "big")
                tables[tag] = (offset, length)
            if b"name" not in tables:
                return ""
            offset, _length = tables[b"name"]
            fh.seek(offset)
            name = fh.read(6)
            count = int.from_bytes(name[2:4], "big")
            string_off = int.from_bytes(name[4:6], "big")
            records = fh.read(count * 12)
            best = ""
            for i in range(count):
                rec = records[i * 12:(i + 1) * 12]
                if len(rec) < 12:
                    break
                platform = int.from_bytes(rec[0:2], "big")
                lang = int.from_bytes(rec[4:6], "big")
                name_id = int.from_bytes(rec[6:8], "big")
                length = int.from_bytes(rec[8:10], "big")
                off = int.from_bytes(rec[10:12], "big")
                if name_id != 1 or platform != 3:
                    continue
                fh.seek(offset + string_off + off)
                raw = fh.read(length)
                try:
                    text = raw.decode("utf-16-be").strip()
                except Exception:  # noqa: BLE001
                    continue
                if not text or text.isascii():
                    continue
                # 0x0804 = 简体中文，优先
                if lang == 0x0804:
                    return text
                best = best or text
            return best
    except Exception:  # noqa: BLE001
        return ""


def install(path: str, dry_run: bool = False) -> List[str]:
    import winreg

    names = registry_names(path)
    base = os.path.basename(path)
    target = os.path.join(font_dir(), base)
    if dry_run:
        return names
    os.makedirs(font_dir(), exist_ok=True)
    # 已经在字体目录里（含同尺寸的旧副本）就不要复制 —— GDI 会锁住已加载的
    # 字体文件，覆盖会抛 WinError 32（"另一个程序正在使用此文件"）。
    # 这时只需要补登记名，所以复制失败也不该中断。
    same = (os.path.abspath(path) == os.path.abspath(target)
            or (os.path.exists(target)
                and os.path.getsize(target) == os.path.getsize(path)))
    if not same:
        shutil.copy2(path, target)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_SUB, 0,
                            winreg.KEY_SET_VALUE) as key:
        for name in names:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, target)
    return names


def broadcast() -> None:
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_FONTCHANGE, 0, 0, SMTO_ABORTIFHUNG, 3000, None)
    except Exception:  # noqa: BLE001
        pass


def iter_zip(path: str) -> Iterable[str]:
    """解出压缩包里的字体文件，返回解压后的路径。"""
    out = os.path.join(os.path.dirname(os.path.abspath(path)), "_unzipped")
    os.makedirs(out, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        for member in zf.namelist():
            if member.lower().endswith(FONT_EXT):
                zf.extract(member, out)
                yield os.path.join(out, member)


def _collect(argv: List[str]) -> Tuple[List[str], List[str]]:
    files: List[str] = []
    zips: List[str] = []
    for arg in argv:
        if arg.lower().endswith(".zip"):
            zips.append(arg)
        else:
            files.append(arg)
    for path in zips:
        files.extend(iter_zip(path))
    return files, zips


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--list":
        print("建议来源（免费商用）：")
        for name, src in SUGGESTED.items():
            print(f"  {name:<32} {src}")
        return 0
    dry = "--dry-run" in argv
    files, _zips = _collect([a for a in argv if not a.startswith("--")])
    if not files:
        print("没有可安装的字体文件")
        return 1
    ok = 0
    for path in files:
        if not path.lower().endswith(FONT_EXT) or not os.path.exists(path):
            print(f"跳过：{path}")
            continue
        try:
            names = install(path, dry_run=dry)
            ok += 1
            print(f"{'[试运行] ' if dry else ''}已安装 {os.path.basename(path)}"
                  f"  ->  {', '.join(names)}")
        except Exception as exc:  # noqa: BLE001
            print(f"失败：{os.path.basename(path)} -> {exc!r}")
    if not dry:
        broadcast()
        print(f"完成 {ok} 个字体；已广播 WM_FONTCHANGE（新开的进程即可用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
