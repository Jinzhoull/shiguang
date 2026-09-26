# -*- coding: utf-8 -*-
"""打包脚本：把拾光打成可执行文件。

用法::

    python build.py                # 绿色版（--onedir，解压即用，启动快）
    python build.py --onefile      # 单文件版（适合做安装包）
    python build.py --clean        # 先清掉 build/dist 再打包
    python build.py --console      # 保留控制台（排查启动问题用）

产物位置：``dist/拾光/`` 或 ``dist/拾光.exe``

体积控制思路
------------
* 不内置图标/音频等二进制资源（运行期用纯 Python 生成），减少包体；
* 用 --exclude-module 砍掉完全用不到的科学计算/图形库；
* 默认 onedir：启动比 onefile 快很多（onefile 每次启动都要解包到临时目录），
  体积也更直接可读。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "拾光"

# 这些库本项目一个都用不到，候选包体积却很大
EXCLUDES = [
    "numpy", "scipy", "pandas", "matplotlib", "cv2", "ipython", "jupyter",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "pytest", "setuptools", "pip", "wheel",
    "PIL.ImageQt", "PIL.ImageShow", "tkinter.test", "test", "unittest",
    "pystray._xorg", "pystray._gtk", "pystray._darwin", "pystray._appindicator",
    # --- 体积大头，实测本项目不需要 ---
    "PIL._avif",      # AVIF 编解码器，单文件 7.7MB
    "PIL._webp",      # WebP 编解码器
    "ssl", "_ssl", "_hashlib",   # 会拖进 libssl/libcrypto 共 9.3MB；本软件不联网
]


def _version_file(workpath: Path = None) -> Path:
    """生成 Windows 可执行文件的版本信息（右键属性里能看到）。

    返回**绝对路径** —— PyInstaller 会把它当相对路径再拼一次 workpath（见 main()）。
    """
    base = Path(workpath).resolve() if workpath else (ROOT / "build")
    path = base / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from shiguang import __app_name__, __slogan__, __version__

    try:
        from PyInstaller.utils.win32.versioninfo import (
            FixedFileInfo, StringFileInfo, StringStruct, StringTable,
            VarFileInfo, VarStruct, VSVersionInfo,
        )

        parts = [int(x) for x in __version__.split(".")]
        while len(parts) < 4:
            parts.append(0)
        info = VSVersionInfo(
            ffi=FixedFileInfo(filevers=tuple(parts), prodvers=tuple(parts),
                              mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                              subtype=0x0, date=(0, 0)),
            kids=[
                StringFileInfo([StringTable("080404B0", [
                    StringStruct("CompanyName", __app_name__),
                    StringStruct("FileDescription", f"{__app_name__} · {__slogan__}"),
                    StringStruct("FileVersion", __version__),
                    StringStruct("InternalName", "shiguang"),
                    StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                    StringStruct("ProductName", __app_name__),
                    StringStruct("ProductVersion", __version__),
                ])]),
                VarFileInfo([VarStruct("Translation", [0x0804, 1200])]),
            ],
        )
        # 必须用 str() 而不是 repr()：repr 会带上 `versioninfo.` 前缀，
        # PyInstaller 反序列化时会 NameError（这里踩过坑）
        path.write_text(str(info), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001
        return path


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="打包拾光")
    parser.add_argument("--onefile", action="store_true", help="打成单个 exe")
    parser.add_argument("--console", action="store_true", help="保留控制台窗口")
    parser.add_argument("--clean", action="store_true", help="打包前清理 build/dist")
    parser.add_argument("--name", default=APP_NAME, help="可执行文件名")
    parser.add_argument("--workpath", default="", help="中间产物目录（默认项目内 build/）")
    parser.add_argument("--distpath", default="", help="产物目录（默认项目内 dist/）")
    args = parser.parse_args()

    # ❗必须转成**绝对路径**。PyInstaller 会把 `--version-file` / `--specpath` 的
    # 相对路径按它自己的 workpath 再拼一次，传 "build\1.4.2" 会得到
    # "build\1.4.2\build/1.4.2/version_info.txt" 这种双重路径 → FileNotFoundError。
    # 默认值是 ROOT 下的子目录，本来就是绝对的；只有显式传相对路径时会踩。
    workpath = Path(args.workpath).resolve() if args.workpath else ROOT / "build"
    distpath = Path(args.distpath).resolve() if args.distpath else ROOT / "dist"

    if args.clean:
        for folder in (workpath, distpath):
            shutil.rmtree(folder, ignore_errors=True)

    from shiguang.assets_gen import ensure_icon

    png, ico = ensure_icon()

    # 需求 16：图标目录 assets/icons/ 随构建重新生成（18/16/14 各尺寸 PNG）。
    # 运行期图标由 icons.py 内存绘制，这里的导出产物用于排查与外部复用，
    # 不参与打包（PyInstaller 只会带上 run.py 实际 import 到的东西）。
    try:
        from shiguang.icons import export_png
        n_icons = export_png(ROOT / "assets" / "icons")
        print(f"[build] 已导出 {n_icons} 个图标 PNG 到 assets/icons/")
    except Exception as exc:  # noqa: BLE001
        print(f"[build] 图标导出跳过：{exc}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "run.py"),
        "--name", args.name,
        "--onedir" if not args.onefile else "--onefile",
        "--noconfirm",
        "--distpath", str(distpath),
        "--workpath", str(workpath),
        "--specpath", str(workpath),
        "--collect-data", "customtkinter",
        "--hidden-import", "pystray._win32",
        "--hidden-import", "darkdetect",
        "--icon", str(ico),
        "--version-file", str(_version_file(workpath)),
    ]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    if not args.console:
        cmd.append("--noconsole")
    if sys.platform != "win32":
        cmd = [c for c in cmd if c not in ("--version-file", str(_version_file()))
               and c != "--icon" and c != str(ico)]

    print("执行:", " ".join(cmd[:8]), "...")
    import subprocess

    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print("打包失败")
        return result.returncode

    # ---------------- 体积报告 ----------------
    # ❗`target` 必须基于 `distpath`，不能写死 `ROOT / "dist"`。
    # 1.5.4 踩过：用 `--distpath dist/_new` 构建时，这里读的是默认 `dist/` 下
    # **上一次的旧产物**，照样打印出"合计 25.3 MB、预算通过 ✓" —— 看着像新包
    # 已就位，实际新包在 `dist/_new/拾光/`，收尾时差点把新旧搞反。
    # 另外记着 PyInstaller 的 `--distpath X` 是在 X **之下**再建 `<name>/`。
    print("\n=== 打包结果 ===")
    if args.onefile:
        target = distpath / f"{args.name}.exe"
        size = target.stat().st_size if target.exists() else 0
        print(f"{target}  {human(size)}")
    else:
        target = distpath / args.name
        size = dir_size(target) if target.exists() else 0
        exe = target / f"{args.name}.exe"
        print(f"{target}  合计 {human(size)}")
        if exe.exists():
            print(f"  主程序 {human(exe.stat().st_size)}")
    budget = 50 * 1024 * 1024
    print(f"预算 50 MB -> {'通过 ✓' if size <= budget else f'超出 {human(size - budget)} ✗'}")
    print(f"\n绿色版：直接把 dist/{args.name} 整个文件夹拷走即可运行（图标：{png.name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
