# Windows 1.2.2 → macOS 对照

变更依据：私有库 Windows 1.2.2 提交 `9274714b1436a6575e9568a834db52b10fa0eec4` 及其累计提交说明，向前对照 macOS 1.2.1 提交 `1207f2372beb7c76a76257ddfae5eeb7d59a8cc3`。发布基线为公开仓库 `450d4db742fc0d8c1a4afc4271a9f7fb2123facd`。

逐路径比对确认：公开基线的全部 `core/`、`ui/` 业务文件、Cellpose worker、Mac worker 依赖与私有库 1.2.2 相同；资源代码相同，激活说明为社区版文案。私有库没有公开基线遗漏的业务模块。公开 `main.py` 已移除旧授权入口，并追加冻结版退出及发布冒烟处理，保留这些开源版差异。私有发布说明中的界面状态次数和 RTX 4060 实测是 Windows 历史记录，不当作本次 Mac 验证结果；不发布私有研究素材、演示工程或旧授权构建入口。

| 问题／更新 | Mac 适用性与处理 | 验证文件 |
| --- | --- | --- |
| 图像／方法切换后参数、结果与当前任务错配 | 相同 Qt 控件及状态控制器，Mac 也适用；采用共享状态恢复和重入处理 | test_analysis_state_restore.py |
| 测量历史操作及主图未跟随结果 | 共享结果身份和预览控制器，Mac 也适用 | test_measurement_history_panel.py、test_measurement_main_preview.py |
| 恢复默认值误用当前工程状态 | 共享初始化快照及忙碌状态保护 | test_module_defaults.py |
| GPU 仅显示选择、未确认运行环境；执行中换设备 | 使用同一异步探测及分析设备作用域；Mac 实际设备为 MPS，不是 CUDA | test_model_device_feedback.py、test_model_device_selection.py、test_shared_cellpose_backend.py |
| 窄栏按钮挤压、隐藏页影响高度、表单错位 | 使用共享响应式操作区、当前页尺寸和表单实现，Mac 字体下单独执行 | test_ui_repairs_20260908.py、test_composer_a_controls.py、test_macos_workflow_tables.py、test_macos_compact_toolbar.py |
| 异步任务陈旧结果／资源处置 | 与操作系统无关，同样采用共享修复 | test_analysis_async.py |
| 空画布、预览模式、通道状态和标定传递 | 共享画布与控制器实现，Mac 同样适用 | test_canvas_empty_copy.py、test_measurement_provenance.py、test_rgb_import_composite.py |
| 启动无反馈及失败时清理 | 共享真实阶段启动页，Mac 单独执行 | test_startup_splash.py；冻结 app 启动和正常退出 |
| Windows 冻结版 QApplication 析构崩溃 | Windows 专用 `os._exit` 不适用于 Mac；保留平台限制，Mac 走正常 Qt 清理 | main.py 平台分支；Mac 冻结 app 启动／退出 |
| 社区版无需机器码 | 公开 main 入口不创建 LicenseManager；授权单元测试保留用于历史模块，不代表启动要求激活 | test_public_source.py、test_startup_splash.py；冻结 app 启动 |

发布由 macos-15 arm64 runner 执行上述相关检查和六模型 CPU/MPS 调用；安装程序与源码从同一提交生成。合成图像冒烟只确认模型调用、设备及输出结构，不代表准确率评估。Apple 系统安装安全提示与软件激活无关，未公证限制需在发布页明确。

本机 Mac 复测额外发现英文通道页在 240 像素侧栏下“Background correction”复选框越界；精简为“Background”，提示保留完整含义，原操作和信号不变。原有中英文窄栏往返测试两项通过。设备测试同时按当前平台选择 MPS/CUDA，避免把 Windows CPU 默认值或 CUDA 可选项当作 Mac 前提。

同一 240 像素侧栏下，Mac 英文测量任务按钮因内边距差 6 像素退化为十行，挤占参数滚动区；将任务选择区左右内边距各收紧 4 像素，恢复双列且保留所有一级任务入口。核计数滚动后连续切换任务的中英文窄宽栏检查通过。六套离线模型在本机 CPU/MPS 共 12 组调用通过，返回设备与选择一致。

顶栏在宽窗口切换语言后再缩窄时，Qt 会短暂保留文字按钮的旧最小宽度；密度更新后立即提交布局最小值，避免窗口被上一种语言的宽度撑开。原有图标、文字提示、操作连接及中英文窄宽往返两项检查通过。

当前流程仅提交源码、生成构建产物；正式 release 上传需手动选择 publish，须待用户完成本机验收后执行。

实际冻结安装版的中文风格工作台检查发现：递归翻译恢复“全图”文字，但按钮已按统一图标定宽。风格页翻译完成后调用两组缩放控件自己的翻译方法，维持图标并更新提示；窄宽工具栏往返复测通过。实际安装版同时确认 GPU 探测进入“已连接”，没有激活对话框。
