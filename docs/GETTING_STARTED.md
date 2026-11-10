# ScieFig 使用说明 / Getting started

## 下载与安装 / Download and install

**当前正式版 / Current releases: [Windows 1.2.5](https://github.com/pamelah-233/ScieFig-Community/releases/tag/windows-v1.2.5) · [macOS 1.2.5](https://github.com/pamelah-233/ScieFig-Community/releases/tag/macos-v1.2.5)**

- **Windows x64** — [完整安装包 / Full installer](https://github.com/pamelah-233/ScieFig-Community/releases/download/windows-v1.2.5/ScieFig_1.2.5_Windows_Setup.exe) · [CPU 基础包 / CPU installer](https://github.com/pamelah-233/ScieFig-Community/releases/download/windows-v1.2.5/ScieFig_1.2.5_Windows_CPU_Setup.exe) · [纯英文 CPU 包 / English CPU installer](https://github.com/pamelah-233/ScieFig-Community/releases/download/windows-v1.2.5/ScieFig_1.2.5_Windows_English_CPU_Setup.exe)。完整包已含 NVIDIA GPU 运行库。CPU 包可从发布页获取兼容的 GPU 补充包。 / The full installer includes NVIDIA GPU support. The release page links to the compatible GPU component for the CPU installers.
- **macOS 14+ · Apple Silicon** — [下载 1.2.5 DMG / Download the 1.2.5 DMG](https://github.com/pamelah-233/ScieFig-Community/releases/download/macos-v1.2.5/ScieFig_1.2.5_macOS_arm64.dmg)。打开 DMG，将 ScieFig 拖入「应用程序」。如果已有安装，在原位置替换。 / Open the DMG and drag ScieFig into Applications. Replace any existing copy in place.

安装包无需另装 Python 或激活。 / The installers require neither a separate Python installation nor activation.

[1.2.3 历史版本 / Previous release](https://github.com/pamelah-233/ScieFig-Community/releases/tag/community-v1.2.3)

## 源码运行 / Running from source

### 中文

下面是默认分支的源码运行说明。Windows 1.2.5 与 macOS 1.2.5 的打包源码请查阅发布页中各平台的源码入口。

1. 安装 Python 3.14，创建虚拟环境。
2. 安装依赖：`python -m pip install -r requirements.txt`。
3. 运行：`python main.py`。
4. qPCR 可先导入 `sample_data/qpcr_phase2_sample.csv`。这是标明 Demo 的合成数据，
   用于熟悉导入、分组、统计、Layout 和导出操作。
5. 显微图像先检查像素标定，再选择功能和方法。分割后使用原图／叠加和逐对象
   校对确认结果，统计前检查每张图是否作为正确的独立重复。

普通图像处理、统计和排版使用主 Python 环境。离线神经模型通过单独 worker
运行；只有模型推理受 CPU/GPU 选择影响。公开源码候选包含三份已授权微调权重与 TRUEFAD 转换权重，原始 cyto3/nuclei
另按打包文档中的固定上游来源取得。请先阅读 `packaging/models/PUBLIC_MODEL_STATUS.json`，不要把私有安装目录里的
模型直接重新上传，也不要将缺少模型误认为算法自动切换到别的方法。

Style PDF/图像处理在本地运行。OCR 需提供 Tesseract 与相应 traineddata；开发
环境路径为 `build/local_style_runtime/tesseract`。对应运行库和模型齐备后才可
按 `packaging/README.md` 打包。公开完整安装包还需核验实际依赖声明与安装结果。

Windows worker 使用单独的 Python 3.12.10 环境和
`packaging/requirements-cellpose-windows.txt`；主程序继续用 Python 3.14。
构建 worker 的入口为 `packaging/build_cellpose.ps1`，其中三份微调权重和
TRUEFAD 权重已经随公开源码候选提供。原始两份权重使用固定来源：

- cyto3: https://huggingface.co/spaces/mouseland/cellpose/resolve/b97a37a6237a937c862ad6b6a0ab4ca7adf0925e/cyto3
- nuclei: https://raw.githubusercontent.com/kevinjohncutler/omnipose-models/7730bdbf94d2e92d6f73b929c35d8870015eb682/nucleitorch_0

将两份文件路径分别传给 `-ModelPath` 和 `-NucleiModelPath`。模型副本应保留
`docs/MODEL_LICENSE_REVIEW.md` 及 `LICENSES` 中的模型声明；此处不替代上游数据权利说明。

真实数据回归为显式选择：`SCIEFIG_TEST_IHC_TIFF` 和 `SCIEFIG_TEST_OVERLAY`
可指向自己有权使用的本地文件；不设置时相关真实数据测试跳过。

### English

These instructions apply to the default branch. See the release page for the
Windows 1.2.5 and macOS 1.2.5 build source references.

- Create a Python 3.14 virtual environment and install `requirements.txt`.
- Run `python main.py`; use the labelled synthetic qPCR CSV for a first run.
- Confirm calibration and review saved contours before interpreting measurements.
- Neural inference uses a separately prepared worker and licensed model weights.
  The source candidate includes four approved local weights. Stock cyto3/nuclei
  are acquired separately from the pinned upstream sources. Data-rights notes remain applicable.
- Read `THIRD_PARTY_NOTICES.md`, the model-status manifest, and
  `docs/OPEN_SOURCE_READINESS.md` before distributing a build.

