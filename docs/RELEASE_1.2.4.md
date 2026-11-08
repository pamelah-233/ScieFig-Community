# ScieFig 1.2.4

- **ND2 导入修复**：在读取器关闭前让像素数组脱离文件缓冲，修复读完 ND2 后卡住或崩溃的问题；Windows 安装包补齐旧版 ND2 所需的 JPEG 2000 解码器。
- **软件更新提示**：启动后在后台检查 GitHub 正式发布。有更高版本且已上传本平台安装包时，欢迎窗口和状态栏提供下载页入口。离线或检查失败时继续正常工作，不自动下载或安装。

- **ND2 import fix:** Retains image pixels before closing the reader, avoiding a freeze or crash after importing an ND2 image. Windows packages include the JPEG 2000 decoder needed for legacy ND2 files.
- **Update notice:** Checks for stable GitHub releases in the background after startup. When a newer installer for the current platform is available, the welcome dialog and status bar link to its download page. ScieFig continues to work offline and never downloads or installs an update automatically.
