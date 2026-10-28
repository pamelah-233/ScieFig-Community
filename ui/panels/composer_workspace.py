"""Canvas-first Composer workspace. Tools are owned here, not by the main window."""
from pathlib import Path
from PyQt6.QtCore import Qt,QPoint,QSize,pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QBoxLayout,QFormLayout,QPushButton,
    QToolButton,QLabel,QComboBox,QDoubleSpinBox,QCheckBox,QDialog,QScrollArea,
    QListWidget,QListWidgetItem,QMessageBox,QGraphicsView,QSizePolicy,QButtonGroup,QMenu,QTabWidget)
from core.models import ExternalImagePanel,QPCRPanel
from core.utils.i18n import get_language
from core.utils.image_formats import COMPOSER_IMAGE_SUFFIXES,dropped_image_paths
from core.models.composer import LAYOUT_PRESETS
from ui.widgets.composer_canvas import ComposerCanvas,ComposerTextItem,MM_TO_PX
from ui.widgets.composer_asset_shelf import ComposerAssetShelf
from ui.widgets.shared_layout_controls import SharedLayoutControls
from ui.widgets.shared_layout_drawing import LayoutDrawTool
from ui.widgets.composer_tool_icons import composer_tool_icon
from ui.controllers.composer_edit_controller import ComposerEditController
from ui.controllers.layout_tools_controller import label

PAGE_PRESETS=[('双栏示例',180,150),('单栏示例',90,120),('A4 纵向',210,297),
              ('A4 横向',297,210),('16:9',254,142.875),('4:3',200,150),('1:1',180,180)]

_EN={'选择':'Select','至少选择三个未锁定对象':'Select at least three unlocked objects',
    '页面设置':'Page settings','页面':'Page','添加页面':'Add page','删除当前页面':'Delete current page',
    '素材':'Assets','属性 / 图层':'Properties / Layers','专注模式':'Focus mode','退出专注':'Exit focus',
    '撤销':'Undo','重做':'Redo','T 文字':'T Text','矩形':'Rectangle','椭圆':'Ellipse','直线':'Line','箭头':'Arrow',
    '添加图形':'Draw shape','左对齐':'Align left','顶对齐':'Align top','等间距':'Distribute','网格':'Grid',
    '整理':'Arrange grid','参考线':'Guides','平移':'Pan','素材库':'Assets','导入图片':'Import images',
    '移除素材':'Remove asset','对象属性 / 图层':'Properties / Layers','未选择对象':'No selection',
    '宽 / mm':'Width / mm','高 / mm':'Height / mm','旋转':'Rotation','锁定图片宽高比':'Keep image aspect ratio',
    '角标跟随图片':'Label follows image','图层':'Layers','锁定 / 解锁':'Lock / Unlock','删除':'Delete',
    '置顶':'Bring to front','置底':'Send to back','统一尺寸':'Same size','水平居中':'Center horizontally',
    '右对齐':'Align right','垂直居中':'Center vertically','底对齐':'Align bottom','水平等间距':'Distribute horizontally',
    '垂直等间距':'Distribute vertically','排列':'Arrange','附带源数据包':'Include source package',
    '导出 DPI':'Export DPI','导出预检':'Export preflight','自定义':'Custom','页面类型':'Page type','宽':'Width','高':'Height',
    '页面设置（与排版网格独立）':'Page settings (independent of grid)','横 / 竖互换':'Swap orientation','应用':'Apply',
    '双栏示例':'Double-column example','单栏示例':'Single-column example','A4 纵向':'A4 portrait','A4 横向':'A4 landscape',
    '单/双栏为示例，请以期刊要求为准。\n换页面不拉伸图片；旧工程按原导出尺寸转换到毫米。':
    'Column sizes are examples; follow your journal requirements.\nImages are not stretched. Legacy objects retain their exported size.',
    'Ctrl＋滚轮缩放 · 双击文字编辑 · F2 编辑 · Ctrl＋Enter 完成':'Ctrl+wheel zoom · Double-click / F2 to edit text · Ctrl+Enter to finish',
    '在画布拖动创建；Shift 约束方向或比例，Esc 取消。':'Drag to create; Shift constrains direction or ratio, Esc cancels.',
    '双击文字编辑 · Ctrl＋Enter 完成 · 属性中调整格式':'Double-click to edit · Ctrl+Enter to finish · Format in Properties'}


def _text(value):
    return _EN.get(value,value) if get_language()=='en' else value


class ComposerWorkspace(QWidget):
    status_changed=pyqtSignal(str)
    import_requested=pyqtSignal()
    files_import_requested=pyqtSignal(list)
    remove_asset_requested=pyqtSignal(str)
    focus_changed=pyqtSignal(bool)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('ComposerWorkspace')
        self.setAcceptDrops(True)
        self.canvas=ComposerCanvas()
        self.controller=ComposerEditController(self.canvas)
        self.installEventFilter(self.controller)
        self.draw=LayoutDrawTool(self.canvas.view)
        self.draw.created.connect(self._finish_drawing)
        self._syncing=False
        self._focus_mode=False
        self._layer_signature=None
        root=QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(2)
        header=QHBoxLayout();header.setContentsMargins(0,0,0,0);header.setSpacing(4)
        self.header_layout=header
        self.command_bar=QWidget()
        self.command_layout=QBoxLayout(QBoxLayout.Direction.LeftToRight,self.command_bar)
        self.command_layout.setContentsMargins(0,0,0,0);self.command_layout.setSpacing(4)
        self.page_controls=QWidget()
        toolbar=QHBoxLayout(self.page_controls)
        toolbar.setContentsMargins(0,0,0,0);toolbar.setSpacing(4)
        self.command_layout.addWidget(self.page_controls)
        self.page_button=self._button('页面设置',self.show_page_settings)
        self.page_button.setSizePolicy(QSizePolicy.Policy.Maximum,QSizePolicy.Policy.Fixed)
        self.pages_combo=QComboBox()
        self.pages_combo.setMaximumWidth(110)
        self.pages_combo.activated.connect(lambda:self.canvas.set_active_page(self.pages_combo.currentData()))
        toolbar.addWidget(self.pages_combo)
        self.add_page_button=self._button('+',self.canvas.add_page)
        self.add_page_button.setToolTip('添加页面')
        self.delete_page_button=self._button('−',self.canvas.delete_current_page)
        self.delete_page_button.setToolTip('删除当前页面')
        for button in (self.add_page_button,self.delete_page_button):
            button.setFixedWidth(28);toolbar.addWidget(button)
        toolbar.addWidget(self.page_button)
        self.assets_button=self._button('素材',self.show_assets)
        self.assets_button.setSizePolicy(QSizePolicy.Policy.Maximum,QSizePolicy.Policy.Fixed)
        toolbar.addWidget(self.assets_button)
        self.focus_button=self._button('专注模式',self.toggle_focus)
        header.addWidget(self.command_bar,1)
        header.addWidget(self.focus_button,0,Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)
        self.undo_button=self._button('撤销',self.controller.undo)
        self.redo_button=self._button('重做',self.controller.redo)
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.redo_button)
        self.distribute_button=QToolButton(self)
        self.distribute_button.hide()  # Distribution is in the shared Arrange menu.
        self.distribute_button.setObjectName('ComposerDistributeButton')
        self.distribute_button.setProperty('composerLabel','等间距')
        self.distribute_button.setText(_text('等间距'))
        self.distribute_button.setProperty('composerTip','至少选择三个未锁定对象')
        self.distribute_button.setToolTip(_text('至少选择三个未锁定对象'))
        self.distribute_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        distribution=QMenu(self.distribute_button)
        self.distribute_actions={}
        for direction,label in [('horizontal','水平等间距'),('vertical','垂直等间距')]:
            action=distribution.addAction(_text(label))
            action.triggered.connect(lambda checked=False,d=direction:self.controller.perform('等间距',lambda:self.canvas.distribute_selected(d)))
            self.distribute_actions[direction]=action
        self.distribute_button.setMenu(distribution)
        self.grid_combo=QComboBox()
        self.grid_combo.addItems(list(LAYOUT_PRESETS))
        self.grid_combo.activated.connect(lambda:self.controller.set_grid(self.grid_combo.currentText()))
        self.guides=QCheckBox('参考线')
        self.guides.setChecked(True)
        self.guides.toggled.connect(self.canvas.set_guides_visible)
        self.canvas.zoom_controls.layout().setContentsMargins(0,0,0,0)
        self.command_layout.addWidget(self.canvas.zoom_controls,1)
        toolbar.addWidget(QLabel('网格'))
        toolbar.addWidget(self.grid_combo)
        toolbar.addWidget(self._button('整理',self.controller.arrange_to_grid))
        toolbar.addWidget(self.guides)
        toolbar.addStretch(1)
        stage=QHBoxLayout();stage.setContentsMargins(0,0,0,0);stage.setSpacing(5)
        self.tool_rail=QWidget();self.tool_rail.setObjectName('ComposerToolRail');self.tool_rail.setFixedWidth(48)
        tools=QVBoxLayout(self.tool_rail);tools.setContentsMargins(5,6,5,6);tools.setSpacing(5)
        self.tool_group=QButtonGroup(self);self.tool_group.setExclusive(True)
        self.tool_buttons={}
        for kind,label in [('select','选择'),('pan','平移'),('text','T 文字'),('rectangle','矩形'),('ellipse','椭圆'),('line','直线'),('arrow','箭头')]:
            button=self._tool_button(kind,label)
            button.setCheckable(True);self.tool_group.addButton(button)
            button.clicked.connect(lambda checked=False,k=kind:self.activate_tool(k))
            tools.addWidget(button);self.tool_buttons[kind]=button
        self.tool_buttons['select'].setChecked(True)
        self.draw.mode_changed.connect(lambda kind:self.tool_buttons[kind or 'select'].setChecked(True))
        tools.addStretch()
        self.delete_button=self._tool_button('delete','删除')
        self.delete_button.clicked.connect(lambda:self.controller.perform('删除',self.canvas.delete_selected))
        tools.addWidget(self.delete_button)
        stage.addWidget(self.tool_rail);stage.addWidget(self.canvas,1)
        root.addLayout(stage,1)
        self.controller.paste_rejected.connect(self.status_changed.emit)
        self.asset_dialog=self._dialog('素材库',330)
        self.assets=ComposerAssetShelf()
        self.assets.setMaximumHeight(16777215)
        self.assets.setWrapping(True)
        self.asset_dialog.layout().addWidget(self.assets,1)
        asset_actions=QHBoxLayout()
        self.import_button=self._button('导入图片',self.import_requested.emit)
        asset_actions.addWidget(self.import_button)
        asset_actions.addWidget(self._button('移除素材',self._request_remove_asset))
        self.asset_dialog.layout().addLayout(asset_actions)
        self.assets.itemDoubleClicked.connect(lambda item:self.place_asset(str(item.data(Qt.ItemDataRole.UserRole))))
        self.assets.files_dropped.connect(self.files_import_requested)
        self.canvas.view.panel_dropped.connect(self.place_asset)
        self.inspector_dialog=self._dialog('对象属性 / 图层',320)
        self.inspector_dialog.setProperty('parameterScrollSurface',True)
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        content=QWidget()
        body=QVBoxLayout(content)
        body.setContentsMargins(14,0,14,0)
        self.selection_label=QLabel('未选择对象')
        body.addWidget(self.selection_label)
        self.format_controls=SharedLayoutControls()
        self.format_controls.layout().setContentsMargins(0,0,0,0)
        self.format_controls.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        for button,_ in self.format_controls._insert_buttons:button.hide()
        self.format_controls.style_requested.connect(self.controller.apply_style)
        body.addWidget(self.format_controls)
        self.geometry=QWidget()
        geometry_form=QFormLayout(self.geometry)
        geometry_form.setContentsMargins(0,0,0,0)
        self.geometry_fields={}
        for label,key in [('X / mm','x_mm'),('Y / mm','y_mm'),('宽 / mm','width_mm'),('高 / mm','height_mm')]:
            spin=QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setRange(-5000 if key in {'x_mm','y_mm'} else .1,5000)
            spin.valueChanged.connect(lambda value,k=key:self._geometry_change(k,value))
            geometry_form.addRow(label,spin)
            self.geometry_fields[key]=spin
        body.addWidget(self.geometry)
        self.rotation_spin=QDoubleSpinBox()
        self.rotation_spin.setRange(-360,360)
        self.rotation_spin.setSuffix('°')
        self.rotation_spin.valueChanged.connect(self._rotation_change)
        geometry_form.addRow('旋转',self.rotation_spin)
        self.aspect=QCheckBox('锁定图片宽高比')
        self.aspect.toggled.connect(self._aspect_change)
        body.addWidget(self.aspect)
        self.label_follow=QCheckBox('角标跟随图片')
        self.label_follow.toggled.connect(self._label_follow_change)
        body.addWidget(self.label_follow)
        self.layer_list=QListWidget()
        self.layer_list.setMaximumHeight(170)
        self.layer_list.itemClicked.connect(self._select_layer)
        self.layer_list.itemChanged.connect(self._toggle_layer_visible)
        body.addWidget(QLabel('图层'))
        body.addWidget(self.layer_list)
        actions=QHBoxLayout()
        actions.addWidget(self._button('锁定 / 解锁',lambda:self.controller.perform('锁定',self.canvas.toggle_lock_selected)))
        body.addLayout(actions)
        actions2=QHBoxLayout()
        actions2.addWidget(self._button('置顶',lambda:self.controller.perform('图层',lambda:self.canvas.layer_selected('front'))))
        actions2.addWidget(self._button('置底',lambda:self.controller.perform('图层',lambda:self.canvas.layer_selected('back'))))
        body.addLayout(actions2)
        self.same_size_button=self._button('统一尺寸',lambda:self.controller.perform('统一尺寸',self.canvas.make_selected_same_size))
        body.addWidget(self.same_size_button)
        arrangement=QHBoxLayout()
        self.arrange_combo=QComboBox()
        for label,mode in [('左对齐','left'),('水平居中','hcenter'),('右对齐','right'),('顶对齐','top'),('垂直居中','vcenter'),('底对齐','bottom')]:
            self.arrange_combo.addItem(label,mode)
        arrangement.addWidget(self.arrange_combo)
        arrangement.addWidget(self._button('排列',self._arrange))
        body.addLayout(arrangement)
        export_form=QFormLayout()
        self.dpi_combo=QComboBox()
        self.dpi_combo.addItems(['300','600','1200'])
        self.dpi_combo.setCurrentText('600')
        self.source_check=QCheckBox('附带源数据包')
        export_form.addRow('导出 DPI',self.dpi_combo)
        export_form.addRow(self.source_check)
        body.addLayout(export_form)
        scroll.setWidget(content)
        self.inspector_dialog.layout().addWidget(scroll)
        self.inspector_body=body
        self.preflight_button=self._button('导出预检',self.canvas.preflight_requested.emit)
        self.controller.tools.properties_requested.connect(self.show_inspector)
        self.controller.tools.message.connect(self.status_changed.emit)
        body.addWidget(self.preflight_button)
        self.canvas.scene.selectionChanged.connect(self.sync_selection)
        self.canvas.pages_changed.connect(self.sync_pages)
        self.canvas.document_changed.connect(self.sync_layers)
        self.controller.undo_group.canUndoChanged.connect(self.undo_button.setEnabled)
        self.controller.undo_group.canRedoChanged.connect(self.redo_button.setEnabled)
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        self.retranslate()

    def _button(self,label,callback):
        button=QPushButton(_text(label))
        button.setProperty('composerLabel',label)
        button.clicked.connect(callback)
        return button

    def _tool_button(self,kind,label):
        button=QToolButton()
        button.setObjectName('ComposerTool_'+kind)
        button.setFixedSize(36,36);button.setIconSize(QSize(24,24))
        button.setIcon(composer_tool_icon(kind))
        button.setProperty('composerTip',label)
        button.setToolTip(_text(label));button.setAccessibleName(_text(label))
        return button

    def activate_tool(self,kind):
        if kind not in {'select','pan'}:
            self.add_annotation(kind)
            return
        self.controller.flush_edits()
        self.draw.cancel()
        self.canvas.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if kind=='pan' else QGraphicsView.DragMode.RubberBandDrag)
        self.tool_buttons[kind].setChecked(True)

    def _dialog(self,title,width):
        dialog=QDialog(self,Qt.WindowType.Tool)
        dialog.setWindowTitle(title)
        dialog.resize(width,620)
        dialog.setLayout(QVBoxLayout())
        dialog.layout().setContentsMargins(8,8,8,8)
        return dialog

    def set_project(self,project):
        changed=project is not self.canvas.project
        self.controller.flush_edits()
        self.canvas.set_project(project)
        if changed:self.controller.reset()
        self.sync_pages()
        self.refresh_assets()
        self.sync_selection()

    def refresh_assets(self,project=None):
        project=project or self.canvas.project
        if project is None:return
        current=self.assets.currentItem()
        selected=current.data(Qt.ItemDataRole.UserRole) if current else None
        self.assets.clear()
        for panel in project.panels:
            thumbnail=self.canvas.panel_thumbnail(panel)
            if thumbnail is None and not isinstance(panel,(ExternalImagePanel,QPCRPanel)):continue
            name=str(panel.data.get('raw_source') or Path(str(panel.data.get('image_path',''))).name or panel.panel_id) if isinstance(panel,ExternalImagePanel) else f'{panel.panel_id} | {panel.panel_type}'
            item=QListWidgetItem(QIcon(thumbnail) if thumbnail is not None else QIcon(),name)
            item.setData(Qt.ItemDataRole.UserRole,panel.panel_uuid)
            item.setToolTip(f'{name}\n{panel.source_module}')
            self.assets.addItem(item)
            if panel.panel_uuid==selected:self.assets.setCurrentItem(item)

    def selected_asset_uuid(self):
        item=self.assets.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def attach_chart_controls(self,controls):
        self.inspector_body.insertWidget(2,controls)

    def dock_sidebar(self,shell):
        self._sidebar_shell=shell
        if not hasattr(self,'sidebar'):
            flat=self.controller.tools.bar.create_inline_panel()
            self.inspector_body.insertWidget(self.inspector_body.indexOf(self.format_controls)+1,flat)
            self.sidebar=QTabWidget()
            self.sidebar.setProperty('parameterScrollSurface',True)
            self.sidebar.setMinimumWidth(0)
            scroll=self.inspector_dialog.findChild(QScrollArea)
            self.inspector_body.addStretch(1)
            self.inspector_dialog.layout().removeWidget(scroll)
            self.sidebar.addTab(scroll,_text('属性 / 图层'))
            assets_page=QWidget();layout=QVBoxLayout(assets_page)
            layout.setContentsMargins(8,8,8,8)
            source=self.asset_dialog.layout()
            while source.count():
                item=source.takeAt(0)
                if item.widget() is not None:layout.addWidget(item.widget(),1)
                elif item.layout() is not None:layout.addLayout(item.layout())
            self.sidebar.addTab(assets_page,_text('素材'))
            self.asset_dialog.hide();self.inspector_dialog.hide()
            from ui.widgets.workflow_forms import readable_forms
            from ui.widgets.responsive_action_grid import adapt_action_rows
            readable_forms(self.sidebar);adapt_action_rows(self.sidebar)
        return self.sidebar

    def _request_remove_asset(self):
        uid=self.selected_asset_uuid()
        if uid:self.remove_asset_requested.emit(uid)

    def place_asset(self,uid,scene_pos=None):
        self.controller.perform('放置素材',lambda:self.canvas.place_panel(uid,scene_pos))
        self.sync_layers()

    def show_assets(self):
        if hasattr(self,'sidebar'):
            self._sidebar_shell.restore_right_panel()
            self.sidebar.setCurrentIndex(1)
            return
        self.asset_dialog.move(self.mapToGlobal(QPoint(10,45)))
        self.asset_dialog.show()
        self.asset_dialog.raise_()

    def show_inspector(self):
        self.sync_selection()
        if hasattr(self,'sidebar'):
            self._sidebar_shell.restore_right_panel()
            self.sidebar.setCurrentIndex(0)
            return
        self.inspector_dialog.move(self.mapToGlobal(QPoint(max(0,self.width()-340),45)))
        self.inspector_dialog.show()
        self.inspector_dialog.raise_()

    def add_annotation(self,kind):
        self.activate_tool('select')
        self.inspector_dialog.hide()
        self.draw.arm(kind)
        self.status_changed.emit(_text('在画布拖动创建；Shift 约束方向或比例，Esc 取消。'))

    def _finish_drawing(self,kind,rect,endpoints):
        self.draw.cancel()
        annotation=self.controller.add_annotation(kind,rect,endpoints)
        self.canvas.view.setFocus()
        if kind=='text':
            item=next((i for i in self.canvas.scene.items() if isinstance(i,ComposerTextItem) and i.annotation is annotation),None)
            if item is not None:item.begin_edit()
        self.status_changed.emit(_text('双击文字编辑 · Ctrl＋Enter 完成 · 属性中调整格式'))

    def sync_selection(self):
        if self.canvas.updating_scene:return
        self._syncing=True
        items=self.canvas.scene.selectedItems()
        eligible=sum(not record.locked for record in self.canvas._selected_records())
        self.distribute_button.setEnabled(eligible>=3)
        for action in self.distribute_actions.values():action.setEnabled(eligible>=3)
        self.format_controls.set_selection(items)
        self.selection_label.setText((f'{len(items)} object(s) selected' if get_language()=='en' else f'已选择 {len(items)} 个对象') if items else _text('未选择对象'))
        single=items[0] if len(items)==1 else None
        ref=getattr(single,'ref',None)
        ann=getattr(single,'annotation',None)
        self.geometry.setEnabled(ref is not None or ann is not None)
        if ref is not None:
            values={k:getattr(ref,k) for k in self.geometry_fields}
        elif ann is not None:
            values={'x_mm':ann.x_mm,'y_mm':ann.y_mm,'width_mm':ann.properties.get('width_mm',55),'height_mm':ann.properties.get('height_mm',20)}
        else:values={}
        for key,value in values.items():self.geometry_fields[key].setValue(value)
        self.rotation_spin.setEnabled(ann is not None and not ann.locked)
        self.rotation_spin.setValue(float(ann.properties.get('rotation_deg',0)) if ann else 0)
        self.aspect.setVisible(ref is not None)
        if ref is not None:self.aspect.setChecked(ref.aspect_locked)
        self.label_follow.setVisible(ann is not None and ann.properties.get('role')=='panel_label')
        if ann is not None:self.label_follow.setChecked(ann.properties.get('follow_panel',False))
        self._syncing=False
        self.sync_layers()
        self._layout_command_row()

    def _geometry_change(self,key,value):
        if not self._syncing:self.controller.move_resize({key:value})

    def _rotation_change(self,value):
        if self._syncing:return
        items=self.canvas.scene.selectedItems()
        if len(items)!=1 or not hasattr(items[0],'annotation') or items[0].annotation.locked:return
        def change():
            item=items[0]
            item.setRotation(value)
            item.annotation.properties['rotation_deg']=value
            self.canvas.annotation_changed()
        self.controller.perform('旋转文字 / 图形',change)

    def _arrange(self):
        mode=self.arrange_combo.currentData()
        self.controller.perform('排列',lambda:self.canvas.align_selected(mode))

    def _aspect_change(self,value):
        if self._syncing:return
        items=self.canvas.scene.selectedItems()
        if len(items)==1 and hasattr(items[0],'ref'):
            self.controller.perform('宽高比',lambda:setattr(items[0].ref,'aspect_locked',value))

    def _label_follow_change(self,value):
        if self._syncing:return
        items=self.canvas.scene.selectedItems()
        if len(items)==1 and hasattr(items[0],'annotation'):
            self.controller.perform('角标关联',lambda:self.canvas.set_label_follow(items[0].annotation.annotation_uid,value))

    def sync_layers(self):
        if self.canvas.project is None:return
        figure=self.canvas.current_figure()
        self.delete_page_button.setEnabled(len(self.canvas.project.composer_figures)>1)
        signature=tuple((r.ref_uid,r.visible,r.locked,r.panel_uid) for r in figure.panel_refs)+tuple((a.annotation_uid,a.visible,a.locked,str(a.properties.get('content',a.type))) for a in figure.annotations)
        if signature==self._layer_signature:return
        self._layer_signature=signature
        scroll=self.layer_list.verticalScrollBar().value()
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for ref in reversed(figure.panel_refs):
            panel=self.canvas.project.get_panel(ref.panel_uid)
            self._layer(ref.ref_uid,'panel_ref',panel.panel_id if panel else '图片',ref.visible,ref.locked)
        for ann in reversed(figure.annotations):
            name=ann.properties.get('content') if ann.type=='text' else ann.properties.get('shape_type','图形')
            self._layer(ann.annotation_uid,'annotation',name,ann.visible,ann.locked)
        self.layer_list.blockSignals(False)
        self.layer_list.verticalScrollBar().setValue(scroll)

    def _layer(self,uid,kind,name,visible,locked):
        item=QListWidgetItem(('🔒 ' if locked else '')+str(name).replace('\n',' ')[:35])
        item.setData(Qt.ItemDataRole.UserRole,(kind,uid))
        item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        self.layer_list.addItem(item)

    def _select_layer(self,item):
        kind,uid=item.data(Qt.ItemDataRole.UserRole)
        self.canvas.scene.clearSelection()
        target=next((i for i in self.canvas.scene.items() if i.data(0)==kind and i.data(1)==uid),None)
        if target is not None:target.setSelected(True)

    def _toggle_layer_visible(self,item):
        kind,uid=item.data(Qt.ItemDataRole.UserRole)
        figure=self.canvas.current_figure()
        values=figure.panel_refs if kind=='panel_ref' else figure.annotations
        target=next((v for v in values if getattr(v,'ref_uid',None)==uid or getattr(v,'annotation_uid',None)==uid),None)
        if target is None:return
        def change():
            target.visible=item.checkState()==Qt.CheckState.Checked
            self.canvas.annotation_changed()
            self.canvas.render()
        self.controller.perform('显隐',change)

    def sync_pages(self):
        if self.canvas.project is None:return
        self.delete_page_button.setEnabled(len(self.canvas.project.composer_figures)>1)
        self.pages_combo.blockSignals(True)
        self.pages_combo.clear()
        figure=self.canvas.current_figure()
        for page in self.canvas.project.composer_figures:self.pages_combo.addItem(page.figure_id,page.figure_uid)
        self.pages_combo.setCurrentIndex(self.pages_combo.findData(figure.figure_uid))
        self.pages_combo.blockSignals(False)
        width,height=self.canvas._export_page_size_mm()
        self.page_button.setText(_text('页面'))
        self.page_button.setToolTip(f'{_text("页面设置")} · {width:g} × {height:g} mm')
        self.grid_combo.setCurrentText(self.canvas.layout_model.preset)

    def show_page_settings(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('页面设置（与排版网格独立）')
        form=QFormLayout(dialog)
        presets=QComboBox()
        presets.addItem('自定义',None)
        for label,w,h in PAGE_PRESETS:presets.addItem(label,(w,h))
        width=QDoubleSpinBox();height=QDoubleSpinBox()
        for spin in (width,height):spin.setRange(10,2000);spin.setDecimals(3);spin.setSuffix(' mm')
        w,h=self.canvas._export_page_size_mm();width.setValue(w);height.setValue(h)
        def choose():
            value=presets.currentData()
            if value:width.setValue(value[0]);height.setValue(value[1])
        presets.activated.connect(choose)
        form.addRow('页面类型',presets);form.addRow('宽',width);form.addRow('高',height)
        def swap():
            w,h=width.value(),height.value();width.setValue(h);height.setValue(w)
        form.addRow(self._button('横 / 竖互换',swap))
        note=QLabel('单/双栏为示例，请以期刊要求为准。\n换页面不拉伸图片；旧工程按原导出尺寸转换到毫米。')
        note.setWordWrap(True);form.addRow(note)
        form.addRow(self._button('应用',dialog.accept))
        self._translate_widgets(dialog)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.controller.set_page(width.value(),height.value(),presets.currentText())

    def toggle_focus(self):
        self._focus_mode=not self._focus_mode
        if hasattr(self,'_sidebar_shell'):
            if self._focus_mode:
                self._focus_sidebar_collapsed=self._sidebar_shell.is_right_panel_collapsed()
                self._sidebar_shell.collapse_right_panel()
            elif not self._focus_sidebar_collapsed:
                self._sidebar_shell.restore_right_panel()
        self.command_bar.setVisible(not self._focus_mode)
        self.tool_rail.setVisible(not self._focus_mode)
        if self._focus_mode:self.asset_dialog.hide();self.inspector_dialog.hide()
        self._sync_command_buttons()
        self._layout_command_row()
        self.focus_changed.emit(self._focus_mode)

    def _sync_command_buttons(self):
        for button, text in (
            (self.undo_button,'撤销'),(self.redo_button,'重做'),
            (self.focus_button,'退出专注' if self._focus_mode else '专注模式'),
        ):
            button.setText(_text(text))
            button.setToolTip(_text(text));button.setAccessibleName(_text(text))
            button.setFixedHeight(28)
            button.setSizePolicy(QSizePolicy.Policy.Maximum,QSizePolicy.Policy.Fixed)

    def _layout_command_row(self):
        focus_width=self.focus_button.sizeHint().width()+4
        focus_in_page=self.focus_button.parentWidget() is self.page_controls
        available=self.width()-focus_width
        page_width=self.page_controls.minimumSizeHint().width()-(focus_width if focus_in_page else 0)
        required=page_width+self.canvas.zoom_controls.minimumSizeHint().width()+4
        direction=QBoxLayout.Direction.LeftToRight if required<=available else QBoxLayout.Direction.TopToBottom
        move_into_page=direction==QBoxLayout.Direction.TopToBottom and not self._focus_mode
        if move_into_page!=focus_in_page:
            self.header_layout.removeWidget(self.focus_button)
            self.page_controls.layout().removeWidget(self.focus_button)
            if move_into_page:self.page_controls.layout().addWidget(self.focus_button)
            else:self.header_layout.addWidget(self.focus_button,0,Qt.AlignmentFlag.AlignTop)
            self.focus_button.show()
        if self.command_layout.direction()!=direction:self.command_layout.setDirection(direction)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._layout_command_row()

    def export_dpi(self):return int(self.dpi_combo.currentText())
    def export_include_source(self):return self.source_check.isChecked()

    def interaction_hint(self):
        return _text('Ctrl＋滚轮缩放 · 双击文字编辑 · F2 编辑 · Ctrl＋Enter 完成')

    def show_preflight(self):
        messages=self.canvas.preflight_export(dpi=self.export_dpi())
        if not hasattr(self,'preflight_dialog'):
            self.preflight_dialog=self._dialog('导出预检 / Export preflight',540)
            self.preflight_list=QListWidget()
            self.preflight_list.setWordWrap(True)
            self.preflight_list.itemDoubleClicked.connect(self._locate_preflight)
            self.preflight_dialog.layout().addWidget(self.preflight_list)
            locate=QPushButton(label('定位所选问题对象','Locate selected issue'))
            locate.clicked.connect(lambda:self._locate_preflight(self.preflight_list.currentItem()))
            self.preflight_dialog.layout().addWidget(locate)
        self.preflight_list.clear()
        for message in messages:
            item=QListWidgetItem(f"[{message.get('severity','info')}] {message['message']}")
            item.setData(Qt.ItemDataRole.UserRole,message.get('object_uid'))
            self.preflight_list.addItem(item)
        if not messages:self.preflight_list.addItem(label('未发现导出问题。','No export issues found.'))
        self.preflight_dialog.show();self.preflight_dialog.raise_()
        return messages

    def _locate_preflight(self,item):
        uid=item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not uid:
            self.status_changed.emit(label('此提示不对应单个对象。','This message does not refer to a single object.'))
            return
        self.canvas.scene.clearSelection()
        target=next((i for i in self.canvas.view.object_interaction.items() if self.controller.tools.item_key(i)==uid),None)
        if target is not None:
            target.setSelected(True)
            self.controller.tools.frame_view('selection')
        else:self.status_changed.emit(label('对象已移除，请重新运行预检。','Object removed. Run preflight again.'))

    def hideEvent(self,event):
        self.draw.cancel()
        self.asset_dialog.hide();self.inspector_dialog.hide()
        if hasattr(self,'preflight_dialog'):self.preflight_dialog.hide()
        super().hideEvent(event)

    def dragEnterEvent(self,event):
        if dropped_image_paths(event.mimeData(),COMPOSER_IMAGE_SUFFIXES):event.acceptProposedAction()
        else:super().dragEnterEvent(event)

    def dropEvent(self,event):
        paths=dropped_image_paths(event.mimeData(),COMPOSER_IMAGE_SUFFIXES)
        if paths:
            self.files_import_requested.emit(paths)
            event.acceptProposedAction()
        else:super().dropEvent(event)

    def closeEvent(self,event):
        self.draw.cancel()
        self.controller.flush_edits()
        self.asset_dialog.close();self.inspector_dialog.close()
        super().closeEvent(event)

    def retranslate(self):
        self.format_controls.retranslate()
        self.controller.tools.bar.retranslate()
        self.canvas.retranslate()
        self._translate_widgets(self)
        if hasattr(self,'sidebar'):
            self._translate_widgets(self.sidebar)
            self.sidebar.setTabText(0,_text('属性 / 图层'));self.sidebar.setTabText(1,_text('素材'))
        self._sync_command_buttons()
        for direction,label in [('horizontal','水平等间距'),('vertical','垂直等间距')]:
            self.distribute_actions[direction].setText(_text(label))
        self.distribute_button.setMinimumWidth(self.distribute_button.fontMetrics().horizontalAdvance(_text('等间距'))+28)
        for button in self.tool_buttons.values():button.setAccessibleName(button.toolTip())
        self.sync_pages()
        self.sync_selection()
        self._layout_command_row()

    @staticmethod
    def _translate_widgets(root):
        for widget in [root,*root.findChildren(QWidget)]:
            if isinstance(widget,(QPushButton,QToolButton,QLabel,QCheckBox)):
                original=widget.property('composerLabel') or widget.text()
                if original in _EN:
                    widget.setProperty('composerLabel',original)
                    widget.setText(_text(original))
            if isinstance(widget,QComboBox):
                for index in range(widget.count()):
                    original=widget.itemData(index,Qt.ItemDataRole.UserRole+1) or widget.itemText(index)
                    if original in _EN:
                        widget.setItemData(index,original,Qt.ItemDataRole.UserRole+1)
                        widget.setItemText(index,_text(original))
            for field,getter,setter in [('composerTip',widget.toolTip,widget.setToolTip),('composerTitle',widget.windowTitle,widget.setWindowTitle)]:
                original=widget.property(field) or getter()
                if original in _EN:
                    widget.setProperty(field,original);setter(_text(original))
