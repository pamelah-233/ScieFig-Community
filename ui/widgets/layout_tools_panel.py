"""First-level layout commands, using the existing shared command controller."""
from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QGridLayout,QHBoxLayout,QPushButton,QLabel,QComboBox,QCheckBox,QSizePolicy
from ui.controllers.layout_tools_controller import label


class LayoutToolsPanel(QWidget):
    def __init__(self,bar):
        super().__init__();self.bar=bar;self.tools=bar.tools
        self.setProperty('workflowPlain',True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(6)
        self.commands={};self.labels=[]
        for zh,en,commands in (
            ('排列与组合','Arrange and group',[
                ('align_left','左对齐','Align left'),('align_right','右对齐','Align right'),
                ('align_top','顶对齐','Align top'),('align_bottom','底对齐','Align bottom'),
                ('align_hcenter','水平居中','Center X'),('align_vcenter','垂直居中','Center Y'),
                ('distribute_horizontal','水平等距','H spacing'),('distribute_vertical','垂直等距','V spacing'),
                ('same_size','统一尺寸','Same size'),('group','组合','Group'),('ungroup','取消组合','Ungroup')]),
            ('格式与视图','Style and view',[
                ('copy_style','复制格式','Copy style'),('paste_style','应用格式','Paste style')])):
            title=QLabel();root.addWidget(title);self.labels.append((title,zh,en))
            grid=QGridLayout();grid.setSpacing(6)
            for index,(command,cn,eng) in enumerate(commands):
                button=QPushButton();button.setMinimumWidth(0)
                button.clicked.connect(lambda checked=False,c=command:self.tools.execute(c))
                grid.addWidget(button,index//2,index%2);self.commands[command]=button;self.labels.append((button,cn,eng))
            root.addLayout(grid)
        self.aspect=QCheckBox();self.aspect.clicked.connect(self.tools.set_image_aspect);root.addWidget(self.aspect)
        self.labels.append((self.aspect,'锁定图片宽高比','Keep image ratio'))
        views=QGridLayout()
        for index,(mode,cn,eng) in enumerate((('page','页面居中','Center page'),('all','显示全部','Show all'),('selection','定位选中','Locate selection'))):
            button=QPushButton();button.setMinimumWidth(0)
            button.clicked.connect(lambda checked=False,m=mode:self.tools.frame_view(m));views.addWidget(button,index//2,index%2)
            self.labels.append((button,cn,eng))
        root.addLayout(views)
        self.presets=QComboBox();self.presets.setMinimumWidth(0);root.addWidget(self.presets)
        actions=QHBoxLayout()
        for cn,eng,callback in (('保存样式','Save style',self.save),('应用样式','Apply',self.apply),('删除样式','Delete',self.remove)):
            button=QPushButton();button.setMinimumWidth(0);button.clicked.connect(callback)
            actions.addWidget(button);self.labels.append((button,cn,eng))
        root.addLayout(actions);self.refresh()

    def refresh(self):
        for widget,cn,eng in self.labels:widget.setText(label(cn,eng))
        for command,button in self.commands.items():
            enabled,reason=self.tools.available(command);button.setEnabled(enabled);button.setToolTip(reason or '')
        items=self.tools.selected();images=[item for item in items if self.tools.is_image(item)]
        blocker=QSignalBlocker(self.aspect)
        self.aspect.setEnabled(bool(images) and len(images)==len(items))
        self.aspect.setChecked(bool(images) and all(self.tools.image_aspect_locked(item) for item in images))
        del blocker
        names=sorted(self.tools.presets())
        if names!=[self.presets.itemText(i) for i in range(self.presets.count())]:
            selected=self.presets.currentText();self.presets.clear();self.presets.addItems(names)
            index=self.presets.findText(selected)
            if index>=0:self.presets.setCurrentIndex(index)

    def save(self):self.bar.save_style();self.refresh()
    def apply(self):
        if self.presets.currentText():self.tools.apply_preset(self.presets.currentText())
    def remove(self):
        if self.presets.currentText():self.tools.remove_preset(self.presets.currentText());self.refresh()
