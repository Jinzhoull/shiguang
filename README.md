# 拾光（Shiguang）

> 拾起每一天，不负好时光。

拾光是一款轻量级桌面任务管理工具。把今天想做的事记下来，完成一项，就收藏一缕阳光。

当前版本：**1.5.32**（版本号以 `shiguang/__init__.py` 为准）

## 功能

- 按「工作、学习、生活」等分组管理任务，支持拖动排序、截止时间和到期提醒。
- 可以按标题或备注搜索任务，也能按到期时间和完成状态筛选；误删后可立即撤销。
- 提醒支持稍后再提醒；数据损坏时尽量保留可读取内容，并留存原文件副本。
- 使用番茄计时专注，并查看每日、本周和累计统计。
- 支持浅色、深色主题、全局快捷键和系统托盘。
- 数据保存在本机；应用运行时不上传任务数据。

## 运行

需要 Python 3.10 或更新版本。

```bash
python -m pip install -r requirements.txt
python run.py
```

核心逻辑检查：

```bash
python tests/test_core.py
```

生成界面预览图：

```bash
python tools/screenshot.py --all
```

预览图写入 `docs/preview/`，演示数据单独写入 `.preview-data/`，不会覆盖日常使用的数据。

## 打包

```bash
python -m pip install pyinstaller
python build.py --name 拾光
```

默认生成绿色版 `dist/拾光/`。使用 `python build.py --onefile --name 拾光` 可生成单文件版。`build/` 和 `dist/` 均为可再生成的本地产物，不提交到源码仓库。

## 项目结构

```text
shiguang/       应用逻辑、数据层、主题、图标和界面
  ui/           页面与可复用控件
tests/          核心逻辑检查
tools/          界面预览和开发辅助工具
docs/           开发指南与项目文档
assets/icons/   构建时导出的图标预览，不是运行时依赖
run.py          应用入口
build.py        PyInstaller 打包脚本
```

更多模块说明和 UI 修改约定见 [开发指南](docs/DEVELOPMENT.md)。

## 数据目录

- Windows：`%APPDATA%\Shiguang\`
- macOS：`~/Library/Application Support/Shiguang/`
- Linux：`$XDG_DATA_HOME/shiguang/`（未设置时为 `~/.local/share/shiguang/`）

可设置 `SHIGUANG_DATA_DIR` 指定其他数据目录。
