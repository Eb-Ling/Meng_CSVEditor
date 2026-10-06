# Meng CSVEditor 源码核验报告

日期：2026-10-05。

## 结论

工作目录原本就包含 `csv_editor.py`、`csv_model.py`、`csv_commands.py` 三个源码文件。以原 EXE 内部字节码为依据，使用相同的 Python **3.13.9** 编译这三个文件的原始备份，逐层比较全部 **127 个代码对象**，结果完全一致。

因此这三份源码已经对应用户提供的 EXE，不需要通过反编译重新猜测实现。保存在 `existing-source/` 中的是开始改进前的现有源码备份；项目根目录同名源码可以在此基线上继续改进。备份没有被功能改进覆盖。

此结论指编译后的代码完全一致。注释及部分源码格式不会保存在 Python 字节码中，不能仅凭 EXE 证明原始文本逐字一致；本项目已有可读源码，因此原有注释可以直接保留。

## 原始 EXE

| 项目 | 值 |
| --- | --- |
| 文件 | `Meng_CSVEditor.exe` |
| 大小 | 54,791,570 字节 |
| SHA-256 | `10a366bd2d16614e3dab28b5632bdbaa4239d28c5d71742c5a8608c2b7eae933` |
| 打包形式 | PyInstaller 单文件 CArchive，内部含 `PYZ.pyz` |
| Python 主次版本 | CArchive cookie 中为 `313` |
| Python 精确版本 | `python313.dll` 的 PE `FileVersion` 和 `ProductVersion` 均为 `3.13.9` |
| 字节码 magic | `f3 0d 0d 0a` |

原 EXE 保持不变。检查过程没有启动原 EXE，也没有执行提取出的应用代码。

## 核验方法

1. `inspect_archive.py` 使用 Python 标准库读取 EXE 尾部 PyInstaller cookie 和目录表，按确定的应用模块名称提取数据，并用 zlib 解压。
2. 读取主入口 `csv_editor` 的 marshal 代码对象，以及 `PYZ.pyz` 中的 `csv_model`、`csv_commands`。仅解析字节码数据，不导入应用模块。
3. 静态读取提取的 `python313.dll` 的 Windows PE 版本资源，确定精确版本为 Python 3.13.9。读取资源时只调用 Windows 自带版本查询 API，不加载目标 DLL。
4. 从 [Python 官方 3.13.9 发布页](https://www.python.org/downloads/release/python-3139/) 下载免安装 x64 运行库，使用它编译 `existing-source/` 中的原始源码备份。
5. `compare_source.py` 递归比较全部代码对象的字节码、常量、函数及类名称、参数、局部及自由变量、标志、栈大小、文件名、行号表、异常表，所有字段均一致。

运行库下载地址：`https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip`。下载 ZIP 的 SHA-256 为 `91d828c2da3a029b41699e918674a0cb379c02cf20dab9c501306885f837402a`。

## 结果

| 原始源码备份 | 比较代码对象数 | 结果 | 源码 SHA-256 |
| --- | ---: | --- | --- |
| `existing-source/csv_editor.py` | 76 | 全部完全一致 | `adb89bab065f6e7d4d7f2b16d1ba7d52206f08c62efa84fe7d8a6c25163c99a8` |
| `existing-source/csv_model.py` | 22 | 全部完全一致 | `370b62dc8ac89f72f421a51ae6457a2adbb9aab8f462272ed0fa31b114fdbd50` |
| `existing-source/csv_commands.py` | 29 | 全部完全一致 | `fb0b0ae8480165b4eec15f02ff42b8419f4292f60f6202f8c872ed5540031c7f` |

机器可读证据见 `source_comparison.json`：三个模块的 `code_objects_equal` 均为 `true`，`differences` 均为空列表。`archive_manifest.json` 记录原 EXE 哈希、192 个打包条目、应用字节码哈希和运行库版本资源。

## 文件说明与复现

- `existing-source/`：原始可读源码备份，尚未应用本轮改进。
- `extracted/csv_editor`、`extracted/csv_model`、`extracted/csv_commands`：从原 EXE 提取的原始 marshal 数据。
- `extracted/*.pyc`：在上述应用 marshal 数据前加标准 16 字节 Python 3.13 pyc 头后的文件，时间戳和源文件长度置零，供静态检查。
- `*.shipped-code.json`：从 EXE 内代码对象生成的递归结构记录，含字节码及元数据。
- `inspect_archive.py`、`compare_source.py`：用于复核的标准库脚本。

从项目根目录使用 Python 3.13.9 运行：

```powershell
& .\recovery\runtime3139\python.exe .\recovery\inspect_archive.py --runtime-metadata
& .\recovery\runtime3139\python.exe .\recovery\compare_source.py
```

也可以使用自行安装的 Python 3.13.9 替换上述解释器路径。`--runtime-metadata` 仅在 Windows 上需要读取 DLL 版本时使用；不加此参数也可提取应用字节码。复现需要把原 EXE 保留在项目根目录。

源码交付包不需要收录下载的 Python 运行库、运行库 ZIP、提取的第三方 DLL 或完整 `PYZ.pyz`；应用模块字节码、原始源码备份、报告及脚本足以保存本次核验结果。
