# 百度指数跨平台查询工具 2.4.0

> 欢迎关注微信公众号“经实研读”，或加入QQ群：610645081

这是按原 Windows 程序的静态分析结果重新实现的 Python 跨平台版本。Linux 保留命令行入口。

## 功能

- 关键词、日期、全国/省份/城市选择。
- 搜索指数、资讯指数和品牌综合/搜索/资讯/互动指数。
- 日级及周、月、季、年聚合。
- 请求间隔倒计时、暂停、停止、进度和日志。
- 软件内账号登录、Cookie 同步和多账号轮换。
- Excel 兼容 CSV 导出；缺失值显示 `—`，数值零显示 `0`。

逆向证据、函数地址和实现映射见 [REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md)。

## 下载文件

| 系统 | 处理器 | GitHub Release 文件 |
|---|---|---|
| macOS | Intel | `baidu-index-tool-v2.4.0-macos-x64.zip` |
| macOS | Apple Silicon（M 系列） | `baidu-index-tool-v2.4.0-macos-arm64.zip` |
| Windows | 64 位 Intel/AMD | `baidu-index-tool-v2.4.0-windows-x64.exe` |

命名采用 GitHub 软件常见的 `macos-x64` 与 `macos-arm64` 架构后缀。macOS 解压后运行 APP；Windows 直接运行 EXE。

macOS 首次启动若提示来源限制，可右键 APP 选择“打开”；也可执行：

```bash
xattr -dr com.apple.quarantine /Applications/百度指数查询工具.app
```

## 开发运行

```bash
python3 -m venv .webviewvenv
.webviewvenv/bin/python -m pip install -r requirements-webview.txt
PYTHONPATH=src .webviewvenv/bin/python run_webview_gui.py
```

Windows 也可双击 `Windows_启动图形界面.pyw`，macOS 可双击 `macOS_启动图形界面.command`。

### Linux CLI

```bash
./Linux_命令行.sh query \
  --keyword 女性 --keyword 妇女 \
  --start 2025-01-01 --end 2025-01-31 \
  --area 北京 --cookie-file ./cookie.txt \
  --aggregate monthly --output ./data/北京-2025-01.csv
```

```bash
./Linux_命令行.sh regions 广东
./Linux_命令行.sh convert ./日级数据目录 \
  --aggregate yearly --method mean --output ./年度日均.csv
```

## 构建

### macOS Intel

```bash
TARGET_ARCH=x64 ./build_macos.sh
```

输出：`dist/macos-x64/百度指数查询工具.app`。

### macOS M 系列

```bash
TARGET_ARCH=arm64 ./build_macos.sh
```

输出：`dist/macos-arm64/百度指数查询工具.app`。在 Intel Mac 上，脚本会使用系统 universal2 Python 交叉生成原生 arm64 APP；在 M 系列 Mac 上直接原生构建。

GitHub Actions 使用：

- `macos-15-intel` 构建 x64。
- `macos-15` 构建 arm64。

工作流文件：`.github/workflows/build-macos.yml`。

### Windows

Windows PowerShell：

```powershell
.\build_windows.ps1
```

在 macOS 上交叉生成 Windows x64 EXE：

```bash
./build_windows_python_releases.sh
```

输出：`dist/baidu-index-tool-v2.4.0-windows-x64.exe`。

### 完整发行包

完成三种二进制构建后运行：

```bash
./package_python_releases.sh
```

发行文件位于 `release/`，并生成 SHA-256 校验表和 GitHub 源码 ZIP。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 run_webview_gui.py --demo --auto-close-ms 3000
```

测试覆盖请求间隔、品牌四指数、Cookie 同步、地区选择、界面文字、关于弹窗、暂停/停止、导出和日志。
