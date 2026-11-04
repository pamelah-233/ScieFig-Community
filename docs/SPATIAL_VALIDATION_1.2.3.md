# 1.2.3 空间 Demo：图片与功能验证

日期：2026-11-03。本报告区分读取／运算正确性和生物学分割准确性。

## 组织切片参数复测（最新，2026-11-03）

新增 [S-BIAD634](https://www.ebi.ac.uk/biostudies/BioImages/studies/S-BIAD634) 的 Ganglioneuroblastoma 组织切片原图及专家逐核标签，研究来源为 [Kromp 等，Scientific Data 2020](https://doi.org/10.1038/s41597-020-00608-w)。[EBI 数据清单](https://uk1s3.embassy.ebi.ac.uk/bia-integrator-data/pages/S-BIAD634-ai.html)明确关联原图与 groundtruth TIFF，按发布标签 ID 评分，不使用 visualized PNG 或只标部分单核的 singlecell mask。

十个视野的实际标签共 2771 个非零核实例。先固定开发视野 0、1、6；另外七个视野 2、3、4、7、8、9、10 只用于最终比较。它们来自同一组织切片，**不是跨患者划分**；预训练数据重叠未排查，因此只作开发比较。`plan.json` 在首轮评分前写入，候选配置固定后再评估预留视野。

比较四种配置：cyto3／25 px／原始输入，cyto3／25 px／128 px 局部归一化，cyto3／40 px／128 px 局部归一化，nuclei／25 px／原始输入；分别比较最大核面积 2000 和 5000 px²，最小面积均为 8 px²。保持模型默认 1–99 百分位归一化、flow threshold=0.4、cellprob threshold=0、200 次迭代。使用现有 CPU 推理运行库，未训练或替换权重。

开发视野平均 F1 最优为 **核专用 nuclei、直径 25 px、面积 8–5000 px²、原始输入**，开发集 macro F1=0.84995。固定该配置后，在七个预留视野的 1998 个标注核上比较：

| 指标 | 之前的 cyto3 参考 | 本次组织参考 |
|---|---:|---:|
| TP / FP / FN | 1698 / 481 / 300 | 1764 / 368 / 234 |
| 汇总实例 F1，IoU≥0.5 | 0.81302 | 0.85424 |
| 逐图 F1 均值 | 0.75140 | 0.81573 |
| 最差单图 F1 | 0.58824 | 0.73103 |
| 逐图二值 Dice 均值 | 0.76935 | 0.81390 |

补充诊断：每张评估图按标注核的平均原始强度取最低四分位，共 502 个弱核；在同一 IoU 判定下，匹配率从 423/502（84.26%）变为 445/502（88.65%）。按“至少覆盖两个真值核各 20%、各至少 20 像素”的固定诊断口径，疑似粘连预测从 104 降至 100；疑似过分割真值核从 11 降至 5。这些是诊断数量，不是经人工确认的错误分类。弱核和接触核仍需复核；一个预留视野 F1 略有下降，不应宣称所有图像均改善。

原图宽高保持不变：1225×914、1360×1024 或 550×430。部分发布 TIFF 为三个完全相同的 uint8 灰度样本通道，逐像素确认相同后取其中一个做核检测；不把它们解释成三个独立染色 marker。所有强度测量仍使用原始数据。显示对比图可以调整显示对比度，不能据此改变定量输入。

已将多标记入口的显式“核分割：加载参考参数”改为载入本次组织参考值；保留配置文件中的 `previous_reference`，不改已保存结果，不自动运行或覆盖当前分割。读取的是开发版 `_internal/resources/analysis_presets/dapi_multimarker_reference_v1_20261103.json`，已验证冻结程序中的真实控件得到 method=cellpose_nuclei、diameter=25、min=8、max=5000、input=raw。

读取本批原图时发现本地冻结运行库只有 imagecodecs `_shared.pyd`，缺少实际 LZW 解码实现。补入与原 imagecodecs **2026.10.10 同版本**的 `_imcd.pyd` 和 `_shared_cython.pyd`，保留 BSD-3-Clause notice；开发版共享读取器已能读取该 LZW TIFF，输出原生 CYX、原始宽高且 downsample=1。用独立 Pillow 解码比较像素完全一致，并验证 uint16 LZW 无损编解码。`requirements.txt` 与 Windows 构建 hidden imports 同步补齐，避免重建时丢失组件；未修改独立安装版或 Cellpose 模型运行库。

复查文件：`.local-runtime/spatial-validation/sources/tissue-annotated/manifest.json` 与 `dataset/` 保留原图、标签、源 URL；`results/tissue-tuning/` 包含计划、逐图模型输出、真实推理 metadata、开发／评估分数和汇总，以及 `weak-and-touching-diagnostics.json`、`development-field6-comparison.png`。可复运行 `packaging/validate_tissue_nuclei.py`，不会下载数据或训练权重。

下文 BBBC039 成绩和 cyto3 参考参数属于前一轮培养细胞开发记录，保留以追踪变更；它们不与本次组织数据混合汇总。

## 后续接入与参数优化（同日追加）

多标记分析已接入主程序“Fluoro／荧光 → Measure／测量 → Multi-marker / spatial／多标记 / 空间邻域”。原始 Demo 的固定阈值不再作为此正式测量任务的核分割依据；以下保留原 Demo 失败记录，便于复查。

任务复用同一图片的已保存核分割，支持可变数量的独立 marker 通道。外扩按最近核归属，强度直接测量原始通道，不按 65535 归一化；使用现有画布、原图／叠加切换、测量历史与独立结果表。CSV 包含逐细胞原始均值、表型、邻居数、带单位坐标／距离、表型汇总、ROI 与源分割参数。源核几何、标定或使用的 ROI 改变后，旧结果禁止作为当前结果导出。

参数选择限定在 BBBC039 官方 validation 列表的前五张，比较四组：nuclei 直径 17／25／35 px，cyto3 直径 25 px；最小面积 8、最大面积 2000 px²；原始输入，默认 Cellpose 百分位归一化。最佳配置为 **cyto3、25 px、8–2000 px²**，验证集平均实例 F1=0.96649。将配置固定后，运行官方全部 50 张 test：

| 项目 | 结果 |
|---|---:|
| 实例匹配 | IoU ≥ 0.5，一对一 |
| TP / FP / FN | 5340 / 85 / 380 |
| 汇总实例 F1（micro） | 0.95828 |
| 逐图实例 F1 均值（macro） | 0.95598 |
| 二值 Dice 均值 | 0.96864 |
| 最差单图实例 F1 | 0.68966 |

这轮是既有 Cellpose 预训练权重的模型／推理参数选择，**未训练新权重**；未排查预训练数据与 BBBC039 的重叠，成绩仅作开发复测，不能当作独立泛化成绩，也不能外推为组织染色准确率。仅取五张验证图选择参数，样本覆盖有限。参考参数需按实际像素尺度调整，尤其需要复核弱核和紧密接触核。

MCMICRO cycle 07 的原生组织图块完成真实模型推理，并通过统一主界面后台任务完成多标记计算：1565 个面积过滤后的核对象，1521 个计入、44 个按源记录边缘规则排除；原画布叠加 1565 个轮廓。测试阈值 CD11B=1500、SMA=3000、CD16=1000 只为检查组合输出，不是经验证的生物学阳性阈值。无该组织核／表型真值，因此不报告其准确率。验证过结果历史、逐细胞／表型／汇总 CSV、源核修改后的失效状态，以及后台禁用控件时保持参数快照不变。

“核分割：加载参考参数”会打开现有核测量任务并填入可编辑参考值；不会自动执行。先核分割并复核，再回到多标记任务选择该结果。旧导入页的 Demo 入口已移除，独立 Demo 快捷方式保留为早期原型。

新增本地记录：`results/model-tuning/validation-sweep.json`、`selected.json`、`test-results.json`、`tissue-labels.npy`，以及 `results/integrated-spatial-measurement.png` 和 `integrated-spatial*.csv`（均位于 `.local-runtime/spatial-validation/` 下）。

## 结论

- **尺寸读取与运算检查通过**：开发版已更新分析核心、Demo 界面、共享读取器和应用入口。支持格式内不要求固定宽高，不把预览尺寸作为定量尺寸。
- **默认核分割准确性未通过**：BBBC039 官方 50 张 held-out test 图共 5720 个标注核，默认阈值检测 0 个。固定阈值连通域不能作为可靠的密集组织分割方案。
- MCMICRO 与 AI 图没有本次可用的细胞／表型标准答案，不能据此报告分割准确率、表型准确率或空间统计的生物学有效性。

## 论文配套原图

| 来源 | 本地数据 | 本次检查 | 结果及边界 |
|---|---|---|---|
| [BBBC039v1](https://bbbc.broadinstitute.org/BBBC039)，Caicedo 等核分割研究配套数据 | 全部原图、标注和官方训练／验证／测试划分 | 官方全部 50 张 test；原生 696×520、uint16、Hoechst DNA 单通道；空缺 marker 置零，绝不当作多标记组织图 | 5720 个标注核；默认阈值 0.16、除数 65535：TP=0、FP=0、FN=5720，实例 F1=0，平均二值 Dice=0。未使用测试集调参。此数据为培养细胞，补充验证核分割，不代替组织验证。 |
| [MCMICRO exemplar-001](https://mcmicro.org/datasets/)，[Schapiro 等，2021](https://doi.org/10.1038/s41592-021-01308-y) | 原始 ZIP、3 个 OME-TIFF 循环、markers.csv、照明参考文件（保留在 ZIP） | 肺腺癌组织 TMA，3 循环×6 图块，每块独立 4 通道，原生 1280×1080、uint16；OME 标定约 0.65 µm/px | 18 个原生图块均完成分割与数值核对，每块输出 233–2016 个连通域。此数量是算法输出，**不是真实细胞计数**。 |

MCMICRO 标记表：cycle 06 = DNA／ELANE／CD57／CD45；cycle 07 = DNA／CD11B／SMA／CD16；cycle 08 = DNA／ECAD／FOXP3／NCAM。同循环通道一起读取；未把未配准的不同循环直接叠加。TIFF 内未给出这些生物学名称的字段，界面验证时按配套 markers.csv 手动填入名称。

BBBC039 标注按[作者解码示例](https://gist.github.com/jccaicedo/15e811722fca51e3ae90e8b43057f075)：取 PNG 第一通道，对相同非零颜色分别做全连通标记。实例以 IoU≥0.5 一对一匹配计算 TP／FP／FN。其低有效信号范围不等于 uint16 容器的满量程；本次保留原始值，未自动拉伸后再宣称默认参数通过。

## ImageGen 荧光测试图

使用内置 ImageGen 模式，每图独立生成。原图和最终提示词保存在 `.local-runtime/spatial-validation/ai/`。IHC／H&E 生成图已移出本次验证集，存于独立 excluded-brightfield 目录，未计入任何结果。

| 文件 | 实际尺寸（宽×高） | 场景 |
|---|---|---|
| ai01-mixed-if.png | 1254×1254 | 多色荧光、亮度变化与局部细胞团 |
| ai02-sparse-if.png | 1536×1024 | 稀疏培养细胞，作为低密度对照 |
| ai03-dense-multiplex.png | 1254×1254 | 密集组织、多色重叠与接触核 |
| ai06-large-dense-tissue-if.png | 1672×941 | 宽视野密集组织、免疫细胞团和串色 |
| ai07-autofluorescence-tissue.png | 1024×1536 | 拥挤组织、自发荧光、折叠和不均匀染色 |

部分提示词请求 2K／4K，但工具实际输出尺寸如表；没有上采样冒充高分辨率原图。五张图均按**实际原生尺寸**参与鲁棒性检查。

AI 输出是 8-bit RGB 合成图，没有独立荧光通道、物理标定和标注真值。验证器仅将 B／G／R 数值映射为三个代理通道，第四通道置零，转换为等比例 uint16；只检查数值与稳定性，不将代理颜色称为 CD 标记，也不将其测量距离标作 µm。输出 44–1516 个连通域；不据此评价真实细胞数量或生物学准确性。

## 运算和尺寸检查

- 对 MCMICRO 和 AI 图逐例抽取最多 20 个细胞，用直接像素索引计算均值和核质心；用独立逐点距离计算最近邻、半径邻居数，和核心输出核对。保留核像素归属一致性检查。全部通过。
- 另外使用 **3840×2160 的确定性数组**验证原生大数组运行：10336 个已知独立矩形对象全部检出，当前机器分析核心约 1.23 秒。这不是 ImageGen 图，也不是接触细胞的准确率或整张病理切片内存测试。时间不包含读盘、界面绘制或空间置换。
- WB／荧光／IHC／明场／Browser／Composer／Style lab 各检查 17×1、1×31、340×173、1024×1536、8200×4400，35 个组合通过。检验原图宽高；预览仍可缩放。尺寸验证不计算文件哈希。
- 空间 Demo 使用开发版 EXE 中的模块，经实际 TIFF 文件选择入口读取 MCMICRO cycle 07 图块：1280×1080、0.65 µm/px；验证 ROI、显示场景尺寸、可编辑 marker、CSV 原始数据来源及坐标单位。无标定时切换为 px，CSV 也使用 px 字段。
- 核心单元检查覆盖原有合成样本、非方形原生图、ROI 边界、同类细胞自身排除、重合质心与单个细胞；共享读图检查包含单像素宽高和带样本轴的图像。

## 开发版修改及使用

桌面“ScieFig 开发版”和“ScieFig 空间分析 Demo”仍指向本项目 `.local-runtime/ScieFig/ScieFig_R10.exe`。已更新对应冻结模块，不依赖 EXE 自动加载源码。未改动 `D:/ScieFig` 独立安装版。

在 Demo 左上角选择“载入 4 通道 TIFF（核通道在首位）”。当前 Demo 数据契约仍为四个独立 uint16 通道：第一通道为核，后三通道为 marker；这属于通道／数据类型要求，图片宽高没有固定值。普通 RGB 合成图应在图片浏览或荧光模块读取，不能作为四个独立原始标记通道。

取消 Qt／Pillow 的通用大图配额；共享读取器保留单像素 X／Y 轴；Demo 的画面、ROI、标尺和导出跟随原图尺寸。邻域使用 cKDTree，避免分配 N×N 全距离数组。界面默认合成样本仍为 512×512，仅是默认样本尺寸。

审阅发现的 256×256 旧 ONNX 明场后端是模型内部推理尺寸，不是图片导入尺寸限制；此次没有擅自更换其模型或声称修复它的降采样准确性。各模块现有缩略图和排版目标输出尺寸也不作为原始读取限制。

不设软件固定宽高并不等于已实现无限尺寸或全切片流式分析。可用内存、解码器能力仍构成实际边界；本次没有验证 100GB 级 WSI、跨循环配准、光谱解混、组织自发荧光消除或真实细胞边界与表型准确性。

## 本地可复查文件

- `.local-runtime/spatial-validation/sources/`：公开原始 ZIP 与 OME-TIFF；原始文件保留。
- `.local-runtime/spatial-validation/ai/prompts.json`：最终荧光生成提示词。
- `.local-runtime/spatial-validation/results/validation.json`：逐图参数、输出、分割分数和时间。
- 同目录 `bbbc039.csv`、`mcmicro.csv`、`imagegen.csv`、`dimension-checks.json`：逐项检查结果。
- 同目录 `development-demo-real-tissue.png` 与 `development-demo-real-tissue-cells.csv`：开发版真实组织视图和导出。
- `packaging/validate_spatial_demo.py`：可重复运行的图像验证器；沿用官方 test 列表，不下载、不调参。

这些大文件和本地运行库不提交 Git。验证结论是“读取和指定计算检查通过，默认弱信号核分割失败”，不是新分析功能已获得生物学验证。
