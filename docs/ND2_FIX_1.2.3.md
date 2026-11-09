# ScieFig 1.2.3 · ND2 导入修复 / ND2 import hotfix

仍使用版本号 `1.2.3`。本次修复适用于 macOS 和 Windows。

- 修复部分 Nikon ND2 在导入后使程序退出的问题。此前首帧可能引用文件映射，文件关闭后继续访问像素会导致崩溃。现在在文件打开期间整理轴顺序；若结果仍引用原缓冲区，则复制该帧。继续只读取原有的首个位置／时间点／Z 平面，保留像素、位深、通道及标定。
- 确保安装包带有旧式 ND2 所需的 JPEG 2000 解码器。损坏或截断的测试文件会提示导入失败，之后仍可导入其他图像。
- Mac ARM64 实测：60 项相关回归测试通过；打包环境中完成 500 次重复读取和 61 次界面导入尝试。覆盖普通、无损压缩与旧式 ND2，以及 CZI、TIFF、OME-TIFF、JPEG、PNG、BMP、WebP、SVG；包含 6144 × 4096 的四通道 16 位图像。10 个损坏或截断的文件在界面中均触发可恢复的错误。
- 重新安装当前 1.2.3 修复包即可；应用版本号和项目格式没有变化。

The application stays at version `1.2.3`. The reader now owns its ND2 pixels before the file mapping is closed, preserving first-frame import behavior, bit depth, channels and calibration. The packages include the JPEG 2000 decoder used by legacy ND2. The macOS build passed the import regression suite and repeated mixed-format reads, including corrupt inputs and a large multichannel OME-TIFF.

ND2 fixtures: [nd2 reader test data](https://github.com/tlambert03/nd2/releases/tag/test-data). CZI fixture: [IDR0011 sample](https://downloads.openmicroscopy.org/images/Zeiss-CZI/idr0011/).
