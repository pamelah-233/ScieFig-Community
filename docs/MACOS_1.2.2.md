## macOS 1.2.2 / Apple Silicon

本地验收阶段：安装包已纳入构建流程，GitHub Mac 下载暂不公开，待用户确认本机版本后发布。

与本页 Windows 1.2.2 并列提供，基于同一公开 ScieFig-Community 源码和修复。本版本无机器码激活、许可证校验入口或额外验证工具；安装后直接运行。

| 下载 | 用途 |
| --- | --- |
| `ScieFig_1.2.2_macOS_arm64.pkg` | 双击进入 macOS 标准安装程序，安装到 Applications。 |
| `ScieFig_1.2.2_macOS_arm64.dmg` | 打开后拖动 ScieFig 到 Applications，也可替换外接盘上已有的安装版。 |
| `ScieFig_1.2.2_macOS_source.zip` | 此次 Mac 构建的固定源码，标签 `macos-v1.2.2`。Windows 原有源码附件与安装包保持原样。 |

支持 Apple Silicon（M 系列），构建目标 macOS 14 及以上；不是 Intel 安装包。软件内置 Python、Qt、科学计算库、Cellpose worker 和六套离线模型，不要求另装 Python 或下载模型。CPU 与 Apple GPU（MPS）可选，结果记录实际使用设备；GPU 不可用时明确提示，不静默改用 CPU。

### 与 Windows 修复的对照

- 接入逐图像／逐方法参数和结果恢复、测量历史加载／删除，以及历史记录与主画布预览同步。
- 接入各模块恢复默认值，默认快照独立于当前工程；分析忙碌时禁用重置，保留标定和原始数据。
- 接入测量任务切换与重入处理、窄侧栏按钮换行、紧凑堆栈高度和嵌套表单布局修复；保留 Mac 字体、暖灰界面和逐像素表格滚动。
- 接入异步任务结果处置与陈旧结果检查；已提交分析固定使用当次选择的设备，避免执行途中切换 CPU/GPU 改变结果来源。
- 接入原图／叠加预览、空画布提示、荧光通道状态和 Composer 操作区修复。
- 接入真实启动阶段反馈与启动失败清理。Windows 冻结版 Qt 退出崩溃规避仅在 Windows 执行；Mac 保留正常 Qt 清理，并单独验证冻结版启动／退出。
- 设备反馈测试在 Mac 使用 MPS，补充 TRUEFAD 的 MPS 设备传递和不匹配拒绝检查。
- 修正 Mac 英文最窄侧栏下通道背景校正复选框越界、测量任务按钮退化成单列挤占参数区的问题；保留全部操作入口和原有信号。
- 修正实际安装版切换语言后风格工作台缩放按钮恢复文字导致裁切的问题，沿用统一图标并更新本地化提示。

详细核查依据见 [Mac 对照记录](https://github.com/pamelah-233/ScieFig-Community/blob/main/docs/MACOS_1.2.2_AUDIT.md)。云端发布前执行 Mac 相关回归、六模型 CPU/MPS 冒烟、无激活冻结版启动／退出、签名及安装包内容检查；不表示全部图片和算法精度都已验收。

### 安装说明与边界

升级前保存工程并退出旧版，在原位置替换，避免保留多个启动图标。PKG 默认安装到系统 Applications；此前安装在外接盘的用户宜使用 DMG 替换原位置。

**无需激活不等于 Apple 公证。** 当前为 ad-hoc 应用签名、未进行 Developer ID 签名和 Apple 公证，PKG 也未作 Developer ID Installer 签名；系统可能显示安全或未识别开发者提示。不能保证所有 Mac 都能无系统提示安装，本版不附带绕过系统保护的脚本。第三方许可和模型说明随安装包提供。

---

The macOS package uses the same public 1.2.2 fixes, with MPS replacing CUDA. It requires no device activation or separate verification utility. PKG provides the standard installer; DMG supports replacing an existing external-disk installation. Python, runtime dependencies and six offline models are bundled. Apple Silicon/macOS 14+ only. The application is ad-hoc signed, not Developer ID signed or notarized; macOS security prompts may still appear.
