# v2.4.0 测试报告

测试日期：2026-08-23

- Python 单元与系统测试：39/39 通过（Python 3.9 与 3.14）。
- Windows 源码界面自测：42/42 通过。
- macOS x64 构建及发行包界面自测：40/40 通过。
- macOS x64 APP：76 个 Mach-O 文件全部为 x86_64，代码签名结构通过。
- macOS arm64 APP：64 个 Mach-O 文件全部为 arm64，代码签名结构通过。
- GitHub macOS 工作流：分别在 `macos-15-intel` 与 `macos-15` 上执行原生构建、架构检查和 40 项界面自测。
- Windows EXE：确认为 PE32+ x86-64 GUI 可执行文件；无需系统 Python，启动与界面自测 42/42 通过。
- Windows 登录窗口关闭后重建、Cookie 根域选择、Cookie 同步重试均通过。
- Windows 界面已移除渲染引擎标识，查询配置区无横向溢出。
- 四个发行文件：SHA-256 校验全部通过。
- GitHub 源码包：39/39 测试通过，无超过 20 MB 的源码文件。
- 已移除第二套浏览器运行时、相关构建脚本、依赖和发行文件。

当前测试机为 Intel Mac，因此 arm64 APP 在本机执行架构、签名和全包 Mach-O 检查；M 系列原生运行测试由 GitHub Actions arm64 runner 执行。
