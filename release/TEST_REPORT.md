# v2.4.0 测试报告

测试日期：2026-08-23

- Python 单元与系统测试：39/39 通过。
- 当前源码 macOS 界面自测：42/42 通过，最小原始字号为 12px，并按系统 DPI 校验有效字号。
- GitHub Actions macOS 构建：Intel x64 与 Apple Silicon arm64 原生任务全部通过；两个 APP 的界面、架构和签名检查均通过。运行编号：`32626445415`。
- GitHub Actions Windows 构建：Windows x64 原生 EXE 启动及界面自测 42/42 通过。运行编号：`32626447146`。
- 下载后的 macOS x64 发行包再次执行界面自测：42/42 通过。
- macOS x64 与 arm64 APP 各检查 69 个 Mach-O 文件，分别仅包含 `x86_64` 与 `arm64` 架构；代码签名结构通过。
- macOS ZIP 未携带构建机的隔离属性；README 已说明首次启动方式。
- Windows 文件确认为 PE32+ x86-64 GUI 可执行文件，运行无需另装 Python。
- GitHub 源码 ZIP 解压后测试：39/39 通过，无超过 20MB 的源码文件。
- 四个发行文件的 SHA-256 校验全部通过，校验表采用 LF 换行，可在 Windows、macOS 和 Linux 使用。
- 仓库内容未包含账号文件、真实 Cookie、访问令牌、构建缓存或临时目录。
