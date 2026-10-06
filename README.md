# Meng_CSVEditor

用 PyQt5 编写的轻量 CSV / TSV 编辑器，适合游戏配置表及日常表格数据编辑。

## 源码核验

本目录原本保留的三个 Python 文件已与原始 `Meng_CSVEditor.exe` 核验：使用相同的 Python 3.13.9 编译，三个模块共 127 个代码对象完全一致。详情和原始 EXE 哈希见 [核验报告](recovery/RECOVERY_REPORT.md)。

- `recovery/existing-source/` 保存改进前的原始源码。
- 项目根目录的 `csv_editor.py`、`csv_model.py`、`csv_commands.py` 是本轮改进版。
- 原始 EXE 可从 [v1.0.0 Release](https://github.com/Eb-Ling/Meng_CSVEditor/releases/tag/v1.0.0) 下载；新构建输出为 `dist/Meng_CSVEditor.exe`。

## 功能及本轮改进

- 网格编辑、多行单元格、行列插入删除、查找替换、最近文件。
- 清爽的浅色工作台：图标工具栏、文件标题与保存状态、柔和的选中高亮、细网格和交替行底色。小窗口自动简化布局，支持高 DPI 显示；保存设置、查找替换窗口使用一致的样式。
- 长内容在表格中使用简洁预览，悬停可查看内容，双击进入完整的多行编辑。
- 保存前提交正在编辑的单元格；保存失败或取消另存为时，继续保留当前文档和未保存状态。
- 保存使用同目录临时文件，完成写入后替换目标文件，避免写入失败把原文件截断。
- “全部替换”支持仅当前列，并作为一次可撤销操作；不再错误清除未保存标记。
- 删除行列、粘贴增加行列均可完整撤销，空表也能重新插入行列。
- 点击列标题排序；复制、粘贴、查找按当前显示位置操作。排序用于浏览，保存保留数据模型的行序。
- 剪贴板使用带引号的 TSV，正确处理单元格里的 Tab、引号和换行，支持移动列标题后的复制粘贴。
- 自动识别常见分隔符及 UTF-8、GBK / GB18030 等编码；能够打开带 UTF-8 / UTF-16 / UTF-32 BOM 的文件。
- 在“设置 → 保存设置...”中切换“保存时写入 BOM”，默认关闭，并记住选择。保存和另存为均使用这个设置；状态栏显示开关、编码和分隔符。
- 关闭 BOM 时，带 BOM 的 UTF-8 保存为普通 UTF-8，UTF-16 / UTF-32 保存为 UTF-8 无 BOM。开启时，UTF-8 / UTF-16 / UTF-32 写入对应 BOM，UTF-16 / UTF-32 保留原字节序；GBK / GB18030 不添加 BOM。保留记录换行符、末尾换行状态及各行实际字段数量。
- 编辑器随文本扩展，限制在表格可见区域内，超长内容可滚动；退出编辑恢复行高。
- 边界审查补强了损坏设置、过期编辑器、无效模型参数、撤销命令及保存失败处理；未捕获的 Python 回调异常会显示错误窗口并记录本地日志。覆盖范围及实际限制见 [边界审查报告](BOUNDARY_AUDIT.md)。

分隔符和无 BOM 编码的识别使用规则推断，打开后可在状态栏检查结果。混合记录换行符会统一为检测到的主要格式；CSV 引号样式可能规范化，数据内容保持不变。另存为 `.csv` 使用逗号，另存为 `.tsv` 使用 Tab。GBK / GB18030 沿用原编码；Unicode 文件按上述规则保存为无 BOM 格式。

## 快捷键

| 快捷键 | 操作 |
| --- | --- |
| `Ctrl+N` / `Ctrl+O` | 新建 / 打开 |
| `Ctrl+S` / `Ctrl+Shift+S` | 保存 / 另存为 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` | 复制 / 剪切 / 粘贴 |
| `Ctrl+F` / `Ctrl+H` | 查找 / 替换 |
| `Ctrl+I` / `Ctrl+Shift+I` | 在上方 / 下方插入行 |
| `Ctrl+J` / `Ctrl+Shift+J` | 在左侧 / 右侧插入列 |
| `Tab`（单元格编辑中） | 插入换行 |
| `Ctrl+Enter` / `Esc`（编辑中） | 提交 / 取消单元格编辑 |

## 运行源码

需要 Python 3.10 或更新版本。改进版在 Windows、Python 3.12.14、PyQt5 5.15.11 下验证。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe csv_editor.py
# 也可以直接打开指定文件
.\.venv\Scripts\python.exe csv_editor.py "D:\data\config.csv"
```

## 测试与打包

双击 `build.bat`，或运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

脚本建立项目虚拟环境、安装固定版本依赖、运行回归测试，然后按 `Meng_CSVEditor.spec` 打包为单文件 Windows EXE。测试失败时停止打包。已有虚拟环境会被复用。Python 未加入 PATH 时，可传入完整路径：

```powershell
.\build.ps1 -PythonExecutable "C:\Python313\python.exe"
```

单独运行测试：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$env:QT_QPA_PLATFORM = $null
```

源码及打包后的程序均支持 `--smoke-test`：启动窗口后自动退出，用于验证基本启动流程。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\dist\Meng_CSVEditor.exe --smoke-test
$env:QT_QPA_PLATFORM = $null
```

生成只含源码、测试、打包配置及核验材料的 ZIP：

```powershell
.\.venv\Scripts\python.exe package_source.py
```

输出位于项目上一级目录 `Meng_CSVEditor_source_improved.zip`，不包含 exe、虚拟环境或第三方运行库。`.gitignore` 已排除这些生成文件，便于把项目源码提交到 GitHub。

本轮边界验证：158 项单元及回归测试全部通过，包含 800 轮 CSV 随机往返、1000 步混合撤销重做及 70 轮排序/移动列 GUI 操作；Windows 单文件 EXE 已重新构建并通过离屏启动检查（退出码 0）。详情见 [审查报告](BOUNDARY_AUDIT.md) 和 `recovery/boundary-tests.log`。

## 目录

| 文件 | 用途 |
| --- | --- |
| `csv_editor.py` | 窗口、编辑委托、剪贴板、文件操作及入口 |
| `csv_model.py` | CSV 读写、格式信息及表格模型 |
| `csv_commands.py` | 单元格、批量操作及行列撤销重做 |
| `ui_theme.py` | 配色、字体、控件样式及矢量图标 |
| `crash_reporter.py` | Qt 回调异常提示与轮转错误日志 |
| `BOUNDARY_AUDIT.md` | 边界审查、故障注入及验证范围 |
| `assets/app.ico` | Windows EXE 图标 |
| `tests/` | 文件读写、模型、撤销重做及 GUI 回归测试 |
| `requirements*.txt` | 运行及打包依赖 |
| `build.ps1` / `build.bat` / `Meng_CSVEditor.spec` | Windows 打包 |
| `recovery/` | 原始源码备份、静态提取脚本及核验记录 |

MIT License，见 [LICENSE](LICENSE)。
