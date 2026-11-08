# ScieFig 1.2.4

## Windows

打包源码 / Build source: [Windows 1.2.4](https://github.com/pamelah-233/ScieFig-Community/tree/6238fb4ba2a12ea80c9810715bc2f0deee35da3b)

| 下载 / Download | 说明 / Details |
| --- | --- |
| [Windows 完整安装包 / Full installer](https://github.com/pamelah-233/ScieFig-Community/releases/download/community-v1.2.4/ScieFig_1.2.4_Windows_Setup.exe) | 包含离线模型、CPU 与 NVIDIA GPU 运行库 / Includes offline models, CPU and NVIDIA GPU runtimes |
| [Windows CPU 基础包 / CPU installer](https://github.com/pamelah-233/ScieFig-Community/releases/download/community-v1.2.4/ScieFig_1.2.4_Windows_CPU_Setup.exe) | 包含离线模型与 CPU 运行库 / Includes offline models and CPU runtime |
| [Windows 纯英文 CPU 包 / English CPU installer](https://github.com/pamelah-233/ScieFig-Community/releases/download/community-v1.2.4/ScieFig_1.2.4_Windows_English_CPU_Setup.exe) | 纯英文界面，包含离线模型与 CPU 运行库 / English-only interface, offline models and CPU runtime |
| [现有 GPU 补充包 / Existing GPU component](https://github.com/pamelah-233/ScieFig-Community/releases/download/community-v1.2.2/ScieFig_GPU_cellpose3_torch212_win64_v1.zip) | 供两个 CPU 包加装 NVIDIA GPU 加速 / Adds NVIDIA GPU support to the CPU packages |

Windows x64。运行安装程序后可直接启动，无需激活。<br>
Windows x64. Run the installer and launch ScieFig. No activation is required.

## macOS

打包源码 / Build source: [macOS 1.2.4](https://github.com/pamelah-233/ScieFig-Community/tree/a1b12f210979b2d7f075bafb567c60cffaaa6a4d)

| 下载 / Download | 说明 / Details |
| --- | --- |
| [macOS DMG](https://github.com/pamelah-233/ScieFig-Community/releases/download/community-v1.2.4/ScieFig_1.2.4_macOS_arm64.dmg) | Apple Silicon · macOS 14+ · 包含运行库与离线模型 / Runtimes and offline models included |

打开 DMG，将 ScieFig 拖入「应用程序」。无需激活。可在应用中选择 CPU 或 Apple GPU。<br>
Open the DMG and drag ScieFig to Applications. No activation is required. Select CPU or Apple GPU in the app.

## 本次更新 / Update

### ND2 导入稳定性 / ND2 import stability

修复部分 ND2 图像读完后卡住或崩溃的问题。Windows 安装包补齐旧版 ND2 所需的 JPEG 2000 解码器。<br>
Fixed a freeze or crash when opening some ND2 images. Windows installers now include the JPEG 2000 decoder needed by legacy ND2 files.

### 软件更新提示 / Update notice

启动后在后台检查正式版本。有较新的本平台安装包时，欢迎窗口和状态栏提供下载页入口；离线时仍可正常使用。<br>
ScieFig checks for a newer stable release in the background after startup. If it includes an installer for your platform, the welcome dialog and status bar link to the download page. The app continues to work offline.

## 验证 / Verification

- [Windows 构建 / Windows build](https://github.com/pamelah-233/ScieFig-Community/actions/runs/42439418397)：检查两类 ND2 的实际像素、三个安装包的安装和启动，并验证从 1.2.2 CPU 版原位升级。 / Validated pixel data from modern and legacy ND2 samples, installation and startup for three packages, and an in-place upgrade from the 1.2.2 CPU edition.
- [macOS 构建 / macOS build](https://github.com/pamelah-233/ScieFig-Community/actions/runs/42439417133)：运行 ND2 回归，检查打包应用启动、签名和 DMG 完整性。 / Ran ND2 regressions and verified packaged startup, signature and DMG integrity.

[使用说明 / Getting started](https://github.com/pamelah-233/ScieFig-Community/blob/main/docs/GETTING_STARTED.md) · [1.2.3 历史发布与图解 / 1.2.3 release and illustrated workflow](https://github.com/pamelah-233/ScieFig-Community/releases/tag/community-v1.2.3)
