# ScieFig

ScieFig 是一款面向科研的桌面工具，帮助你从实验数据和显微图像出发，完成分析、绘图和组版。

## 能做什么

- **qPCR 和 Western Blot**：整理数据、进行统计并生成图表。
- **荧光、IHC 和明场**：浏览图像、设置比例尺、测量信号和面积，使用 Cellpose 辅助分割与计数，并检查叠加结果。
- **组版和风格**：把图片与图表放在同一画布上，调整文字、图形和样式，导出最终图版。
- 支持中文和英文界面，以及可用时的 CPU / GPU 模型运算。

## 获取软件

Windows 安装包将在 [版本发布页](https://github.com/pamelah-233/ScieFig-Community/releases)提供。新版 1.2.2 无需机器码激活。功能展示图片稍后补充。

## 从源码运行

使用 Python 3.14 安装 `requirements.txt` 中的依赖，然后在项目目录运行 `python main.py`。安装、模型环境及示例数据见[入门指南](docs/GETTING_STARTED.md)。图像分割和测量结果请结合原图和校准信息核对。

## 联系作者

希望软件主要用于非商业用途。**这只是作者的倡议，不限制许可证允许的使用和再分发。**

如需定制功能或反馈问题，可在 [GitHub Issues](https://github.com/pamelah-233/ScieFig-Community/issues) 联系作者；请不要在公开问题中上传敏感数据。

源码许可：[AGPL-3.0](LICENSE) · [第三方组件与模型说明](THIRD_PARTY_NOTICES.md)
