# 开发指南

## 项目分层

- `shiguang/models.py`：任务与分组的数据模型和日期解析。
- `shiguang/store.py`：本地 JSON 持久化、备份与损坏恢复。
- `shiguang/stats.py`、`reminder.py`、`pomodoro.py`：统计、到期提醒和专注计时逻辑。
- `shiguang/app.py`：主窗口、系统服务和页面生命周期编排。
- `shiguang/ui/`：可复用控件及任务、统计、设置等页面。
- `shiguang/theme.py`、`fonts.py`、`icons.py`：主题、字号/DPI 和自绘图标的统一入口。
- `tests/test_core.py`：不依赖界面的核心逻辑测试。
- `tools/screenshot.py`：使用独立演示数据生成界面预览；不会改动日常使用的数据目录。
- `tools/install_fonts.py`：Windows 可选字体安装辅助工具。

## 本地开发

```bash
python -m pip install -r requirements.txt
python run.py
python tests/test_core.py
```

生成界面预览图：

```bash
python tools/screenshot.py --all
```

预览输出在 `docs/preview/`，演示数据在 `.preview-data/`；两者都由工具生成并已加入 `.gitignore`。

## UI 修改约定

- 所有 Tk 控件都在主线程操作。后台线程需要更新界面时，通过应用的主线程队列投递回调。
- 配色、间距、圆角和字号集中在 `theme.py`、`fonts.py` 中维护，避免页面各自硬编码。
- CustomTkinter 控件的尺寸使用逻辑像素；裸 Tk/Canvas 的绘制和布局尺寸使用 `theme.lpx()` 换算。`winfo_*()` 返回的实测值已是物理像素。
- 新图标在 `icons.py` 中绘制和注册。`assets/icons/` 是构建时导出的预览资源，不是运行时依赖。
- 统计口径放在 `stats.py`，页面只读取统一结果。
- 回归检查先运行核心测试；改动绘制或布局后，再用预览工具检查目标界面。

## 构建

```bash
python -m pip install pyinstaller
python build.py --name 拾光
```

绿色版输出到 `dist/拾光/`；单文件构建使用 `python build.py --onefile --name 拾光`。`build/`、`dist/` 都是可再生成产物，不提交到源码仓库。
