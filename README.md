# 百度指数查询工具

当前版本：`v2.4.5`

Windows 和 macOS 提供图形界面，Linux 提供命令行工具。查询结果可在软件内查看，并导出为 Excel 可直接打开的 CSV 文件。

## 主要功能

- 批量输入关键词，一行一个。
- 按开始日期和结束日期查询；图形界面支持日、周、月、年周期，命令行聚合另支持季度和全区间。
- 选择全国、省份或城市，支持选择全部省份、选择全部城市和清空选择。
- 查询搜索指数：PC+移动、PC 端、移动端、资讯指数。
- 查询品牌指数：品牌指数、品牌搜索指数、品牌资讯指数、品牌互动指数。
- 在软件内完成百度账号登录并同步 Cookie。
- 保存多个账号，支持顺序轮换或随机切换。
- 设置请求间隔，并显示倒计时、任务进度和运行日志。
- 长日期范围按日级时间段查询，避免将服务端周级采样误标为逐日数据；年均值由日级数据计算。
- 支持暂停、继续和停止查询。
- 中断后保存已获取结果及查询进度；当前窗口支持继续未完成的任务。
- 某个地区或日期段没有数据时记录该条件并继续；有数据、无数据和失败分别统计，缺失不会填成 0。
- 自定义导出目录；缺失值显示为 `—`，数值零显示为 `0`。
- Linux 命令行支持搜索指数查询、地区检索和已有 CSV 聚合转换。

## 下载

在 [GitHub Releases](https://github.com/Ecow0ker/baidu-index-tool/releases) 下载对应文件：

| 系统 | 处理器 | 文件 |
|---|---|---|
| Windows | 64 位 Intel/AMD | `baidu-index-tool-v2.4.5-windows-x64.exe` |
| macOS | Intel | `baidu-index-tool-v2.4.5-macos-x64.zip` |
| macOS | Apple Silicon（M 系列） | `baidu-index-tool-v2.4.5-macos-arm64.zip` |
| Windows、macOS、Linux | Python 源码 | `baidu-index-tool-v2.4.5-source.zip` |

Windows 下载 EXE 后可直接运行。macOS 解压 ZIP 后运行“百度指数查询工具.app”。

若日志显示 `10001: request block`，表示服务端拦截了当前请求。软件会保存已获取的结果并停止发送后续请求。请稍后在软件内确认网页查询恢复正常，再点击“继续查询”；可增加请求间隔。已保存账号数量不代表这些账号的 Cookie 均有效。

请求间隔覆盖趋势接口、解码参数接口及重试。跨多年的查询包含多个时间段，实际请求数会多于地区数量，预计耗时随日期范围增加。

表格显示最近 2,000 条原始日级记录；CSV 保存全部结果，并按所选周期计算日均值。缺失日期不作为 0 参与均值计算。

导出写入失败时仍尝试保存查询进度；当前窗口点击“继续查询”可重试导出，已完成的查询不重复请求。关闭主窗口会停止后续请求。

macOS 首次启动若提示来源限制，可右键 APP 选择“打开”；也可执行：

```bash
xattr -dr com.apple.quarantine /Applications/百度指数查询工具.app
```

## 图形界面使用

1. 打开“账号中心”，点击“打开登录窗口”。
2. 在软件登录窗口完成登录，返回账号中心并点击“同步 Cookie”。
3. 在“查询任务”中填写关键词和日期，选择地区、指数类型与指标。
4. 设置提取间隔和导出目录，点击“开始查询”。
5. 查询过程中可查看倒计时、进度和日志，也可暂停或停止任务。
6. 完成后在结果表查看数据，或打开导出目录查看 CSV 文件。

账号 Cookie 和账号切换设置保存在本机应用数据目录，不写入项目文件。请勿将个人 Cookie 提交到 GitHub。

## 查询报错时

- 日志显示“无数据”表示服务端没有返回该条件的指数序列，不等于指数为 0。软件会继续后续任务，并在导出目录的 `查询进度.json` 中记录地区、指标及日期段；全部无数据时不生成虚假的数据 CSV。
- Windows/macOS 的 HTTPS 使用系统证书信任库，证书和域名验证始终开启；不依赖构建电脑上的证书路径。
- 若提示“HTTPS 证书验证失败”，检查系统日期；使用 HTTPS 代理时，其证书需要已受操作系统信任。此错误发生在接口请求阶段，不代表账号登录失效。
- “已保存账号”表示本机有 Cookie 记录，不表示账号仍处于登录状态。若日志提示“登录信息未通过校验”，在软件内重新登录并同步 Cookie。
- `接口状态 10001` 是查询接口的通用失败状态，日志会保留状态和返回信息，便于区分账号登录失效与请求频率限制。
- 若明确提示“请求频率受限”，稍后再试，并适当调大提取间隔。

## 从源码运行图形界面

### macOS

```bash
python3 -m venv .webviewvenv
.webviewvenv/bin/python -m pip install -r requirements-webview.txt
PYTHONPATH=src .webviewvenv/bin/python run_webview_gui.py
```

macOS 也可双击 `macOS_启动图形界面.command`。

### Windows

```powershell
py -3.12 -m venv .webviewvenv
.\.webviewvenv\Scripts\python.exe -m pip install -r requirements-webview.txt
$env:PYTHONPATH = "src"
.\.webviewvenv\Scripts\python.exe run_webview_gui.py
```

Windows 也可双击 `Windows_启动图形界面.pyw`。

## Linux 命令行

查询搜索指数并导出 CSV：

```bash
./Linux_命令行.sh query \
  --keyword 女性 --keyword 妇女 \
  --start 2025-01-01 --end 2025-01-31 \
  --area 北京 --cookie-file ./cookie.txt \
  --aggregate monthly --output ./data/北京-2025-01.csv
```

关键词较多时，可使用 UTF-8 文本文件：

```bash
./Linux_命令行.sh query \
  --keywords-file ./keywords.txt \
  --start 2025-01-01 --end 2025-01-31 \
  --area 全国 --cookie-file ./cookie.txt
```

查询地区名称和 area ID：

```bash
./Linux_命令行.sh regions 广东
```

将已有日级 CSV 聚合为周、月、季或年数据：

```bash
./Linux_命令行.sh convert ./日级数据目录 \
  --aggregate yearly --method mean --output ./年度日均.csv
```

Cookie 也可通过环境变量提供：

```bash
export BAIDU_INDEX_COOKIE='COOKIE'
```

## 构建发行文件

macOS Intel：

```bash
TARGET_ARCH=x64 ./build_macos.sh
```

macOS Apple Silicon：

```bash
TARGET_ARCH=arm64 ./build_macos.sh
```

Windows x64：

```powershell
.\build_windows.ps1
```

GitHub Actions 构建配置位于 `.github/workflows/`。

## 测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 run_webview_gui.py --demo --ui-self-test-output ./ui-test.json
```

测试覆盖账号窗口、Cookie 同步、多账号切换、地区选择、搜索指数、品牌指数、请求间隔、暂停/停止、结果显示和 CSV 导出。

## 联系

欢迎关注微信公众号“经实研读”，或加入QQ群：610645081
