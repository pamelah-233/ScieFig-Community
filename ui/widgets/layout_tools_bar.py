"""Compact shared commands in the existing canvas row, not a new workflow header."""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QMenu, QInputDialog, QMessageBox
from ui.controllers.layout_tools_controller import label


class LayoutToolsBar(QWidget):
    def __init__(self, tools):
        super().__init__()
        self.tools = tools
        row = QHBoxLayout(self); row.setContentsMargins(0,0,0,0); row.setSpacing(4)
        self.properties = QToolButton(); self.properties.setText(label('属性','Properties'))
        self.properties.clicked.connect(tools.properties_requested.emit)
        row.addWidget(self.properties)
        self.buttons = {}
        kinds=[('arrange','排列','Arrange'),('group','组合','Group'),
                              ('style','格式','Style'),('view','视图','View')]
        if not hasattr(tools.owner,'undo_stack'):kinds.insert(0,('insert','添加','Add'))
        for kind,zh,en in kinds:
            button = QToolButton(); button.setText(label(zh,en)); button.setFixedHeight(26)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(button); menu.setToolTipsVisible(True)
            menu.aboutToShow.connect(lambda k=kind,m=menu:self.populate(k,m))
            button.setMenu(menu); row.addWidget(button); self.buttons[kind] = button
        self.properties.setFixedHeight(26)

    def retranslate(self):
        for kind,zh,en in [('insert','添加','Add'),('arrange','排列','Arrange'),('group','组合','Group'),
                           ('style','格式','Style'),('view','视图','View')]:
            if kind in self.buttons:self.buttons[kind].setText(label(zh,en))
        self.tools.sync_summary()

    def create_inline_panel(self):
        if not hasattr(self,'inline_panel'):
            from ui.widgets.layout_tools_panel import LayoutToolsPanel
            self.inline_panel=LayoutToolsPanel(self)
        for button in self.buttons.values():button.hide()
        self.inline_panel.refresh()
        return self.inline_panel

    def action(self, menu, command, zh, en, shortcut=''):
        action = menu.addAction(label(zh,en)+('  '+shortcut if shortcut else ''))
        enabled, reason = self.tools.available(command)
        action.setEnabled(enabled); action.setToolTip('' if enabled else reason)
        action.setData(command); action.triggered.connect(lambda:self.tools.execute(command))
        return action

    def populate(self, kind, menu):
        menu.clear()
        if kind=='insert':
            for shape,zh,en in [('text','文字','Text'),('rectangle','矩形','Rectangle'),('ellipse','椭圆','Ellipse'),
                                 ('line','直线','Line'),('arrow','箭头','Arrow')]:
                menu.addAction(label(zh,en)).triggered.connect(lambda checked=False,k=shape:self.tools.insert_requested.emit(k))
        elif kind == 'arrange':
            for command,zh,en in [('align_left','左对齐','Align left'),('align_hcenter','水平居中','Center horizontally'),
                ('align_right','右对齐','Align right'),('align_top','顶对齐','Align top'),
                ('align_vcenter','垂直居中','Center vertically'),('align_bottom','底对齐','Align bottom'),
                ('distribute_horizontal','水平等间距','Distribute horizontally'),
                ('distribute_vertical','垂直等间距','Distribute vertically'),('same_size','统一尺寸','Same size')]:
                self.action(menu,command,zh,en)
            menu.addSeparator()
            aspect=menu.addAction(label('锁定图片宽高比（Shift 临时切换）','Keep image ratio (Shift toggles temporarily)'))
            items=self.tools.selected()
            images=[i for i in items if self.tools.is_image(i)]
            aspect.setCheckable(True); aspect.setEnabled(bool(images) and len(images)==len(items))
            aspect.setChecked(bool(images) and all(self.tools.image_aspect_locked(i) for i in images))
            aspect.triggered.connect(self.tools.set_image_aspect)
        elif kind == 'group':
            self.action(menu,'group','组合','Group','Ctrl+G')
            self.action(menu,'ungroup','取消组合','Ungroup','Ctrl+Shift+G')
            hint = menu.addAction(label('Alt 点击：单独选择组内对象','Alt-click: select an individual member')); hint.setEnabled(False)
        elif kind == 'style':
            self.action(menu,'copy_style','复制格式','Copy style','Ctrl+Alt+C')
            self.action(menu,'paste_style','应用格式','Apply style','Ctrl+Alt+V')
            menu.addSeparator()
            save = menu.addAction(label('保存为标注样式…','Save annotation style…'))
            save.setEnabled(self.tools.available('copy_style')[0]); save.triggered.connect(self.save_style)
            apply = menu.addMenu(label('应用标注样式','Apply annotation style'))
            remove = menu.addMenu(label('删除标注样式','Delete annotation style'))
            records = self.tools.presets()
            apply.setEnabled(bool(records)); remove.setEnabled(bool(records))
            for name,value in sorted(records.items()):
                action = apply.addAction(name)
                action.setEnabled(bool(self.tools.selected()) and all(self.tools.style_kind(i)==value['kind'] for i in self.tools.selected()))
                action.triggered.connect(lambda checked=False,n=name:self.tools.apply_preset(n))
                remove.addAction(name).triggered.connect(lambda checked=False,n=name:self.tools.remove_preset(n))
        elif kind == 'view':
            for mode,zh,en in [('page','页面居中','Center page'),('all','显示全部对象','Show all objects'),
                                ('selection','定位选中对象','Locate selection')]:
                action = menu.addAction(label(zh,en)); action.setData(mode)
                action.setEnabled(mode!='selection' or bool(self.tools.selected()))
                action.triggered.connect(lambda checked=False,m=mode:self.tools.frame_view(m))

    def save_style(self):
        name, ok = QInputDialog.getText(self,label('保存标注样式','Save annotation style'),label('名称','Name'))
        if not ok or not name.strip(): return
        name = name.strip()
        if name in self.tools.presets() and QMessageBox.question(self,label('覆盖样式','Replace style'),
                label('同名样式已存在，是否覆盖？','A style with this name exists. Replace it?')) != QMessageBox.StandardButton.Yes:
            return
        if not self.tools.save_preset(name):
            self.tools.message.emit(label('样式未保存，请检查当前选择或设置文件的写入权限。',
                                           'Style was not saved. Check the selection and settings write access.'))
