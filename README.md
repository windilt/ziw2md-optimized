# ziw2md-optimized

# 📝 WizNote to Markdown (ziw2md)

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)]()

一个零依赖、高健壮性的 Python 脚本，用于将 **WizNote（为知笔记）** 导出的 `.ziw` 文件批量转换为标准、干净的 **Markdown (`.md`)** 文件。

完美解决官方导出或第三方工具常见的**附件丢失/覆盖、中文乱码、代码块破裂、列表缩进错乱、XSS 注入**等痛点。

## ✨ 核心特性

- **📦 零依赖**：完全基于 Python 标准库（`html.parser`, `zipfile`, `pathlib` 等），无需 `pip install` 任何第三方包。
- **🔒 附件隔离与安全**：
  - 为每篇笔记生成独立的附件目录（如 `笔记名_files/`），彻底解决同目录多笔记导致的 `index_files` 互相覆盖问题。
  - 自动处理 URL 编码（`%E5%9B%BE...` -> `图片.png`）与 ZIP 内非标准路径分隔符（兼容 `\` 与 `/`）。
  - 自动过滤 `javascript:` / `vbscript:` 等危险协议，防止生成的 Markdown 存在 XSS 风险。
- **🌍 智能编码探测**：
  - 自动识别 UTF-8 (含 BOM)、UTF-16 (LE/BE)、GBK/GB18030，完美兼容老旧 Windows 系统导出的乱码文件。
  - 智能修复 ZIP 压缩包内中文文件名的 `cp437` 解码乱码（Mojibake）问题。
- **🎨 极致的 Markdown 语义还原**：
  - **动态代码围栏**：根据代码内容中的反引号数量，自动生成更长的 ` ``` ` 或 ` ```` `，防止代码块意外截断。
  - **嵌套容器支持**：完美处理 `引用 > 列表 > 引用` 等多层嵌套的缩进与 `>` 前缀，防止 Lazy Continuation 吞噬正文。
  - **表格降级与保护**：支持 GFM 标准表格；对单元格内的复杂块级标签（如 `<p>`, `<ul>`）和嵌套表格进行智能扁平化降级，并自动转义管道符 `|`。
  - **防误判机制**：自动转义行首的 `#`、`-`、`1.` 以及 Setext 标题下划线（`===` / `---`），防止普通文本被误解析为标题或列表。
- **📄 YAML Front Matter**：自动提取笔记标题、创建时间、修改时间，生成标准的 YAML 元数据头，无缝接入 Obsidian / Hugo / Hexo 等静态站点生成器。

## 🚀 快速开始

### 环境要求
- Python 3.8 或更高版本

### 运行方式
无需安装，直接下载 `ziw2md.py` 并运行：

```bash
# 1. 转换当前目录下的所有 .ziw 文件（输出到 ./ziw2md_output）
python ziw2md.py

# 2. 指定源目录（输出到源目录同级的 ziw2md_output）
python ziw2md.py /path/to/wiznote_exports

# 3. 分别指定源目录和输出目录
python ziw2md.py /path/to/source /path/to/output
