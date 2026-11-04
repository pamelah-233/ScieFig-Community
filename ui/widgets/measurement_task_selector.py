"""Persistent two-column task buttons; selection never starts analysis."""
from PyQt6.QtCore import QEvent,pyqtSignal,Qt
from PyQt6.QtWidgets import QWidget,QGridLayout,QButtonGroup,QPushButton,QSizePolicy


class MeasurementTaskSelector(QWidget):
    currentIndexChanged=pyqtSignal(int)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('MeasurementTaskSelector')
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Maximum)
        self.buttons={};self._keys=[];self._index=-1
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,0,0,0);self.grid.setSpacing(6)
        self.group=QButtonGroup(self);self.group.setExclusive(True)

    def addItem(self,text,value,button=None):
        index=len(self._keys);self._keys.append(value)
        button=button or QPushButton(text);button.setText(text);button.setCheckable(True)
        button.setObjectName('MeasurementTaskButton');button.setMinimumWidth(0)
        button.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(34);button.setMaximumHeight(16777215);button.installEventFilter(self)
        self.grid.addWidget(button,index//2,index%2);self.group.addButton(button,index);self.buttons[value]=button
        button.clicked.connect(lambda checked=False,key=value:self.setCurrentIndex(self.findData(key)))
        if self._index<0:self.setCurrentIndex(0)

    def currentIndex(self):return self._index
    def currentData(self):return self._keys[self._index] if 0<=self._index<len(self._keys) else None
    def currentText(self):return self.itemText(self._index)
    def count(self):return len(self._keys)
    def findData(self,key):return self._keys.index(key) if key in self._keys else -1
    def itemData(self,index):return self._keys[index] if 0<=index<len(self._keys) else None
    def itemText(self,index):return self.buttons[self._keys[index]].text() if 0<=index<len(self._keys) else ''
    def setItemText(self,index,text):
        if 0<=index<len(self._keys):self.buttons[self._keys[index]].setText(text)

    def setCurrentIndex(self,index):
        if not 0<=index<len(self._keys):return
        changed=index!=self._index;self._index=index
        self.buttons[self._keys[index]].setChecked(True)
        if changed:self.currentIndexChanged.emit(index)

    def reorder(self,keys):
        current=self.currentData();self._keys=list(keys)
        for index,key in enumerate(self._keys):
            button=self.buttons[key];self.grid.removeWidget(button);self.grid.addWidget(button,index//2,index%2)
        self._index=self.findData(current)

    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.Resize,QEvent.Type.LayoutRequest,QEvent.Type.Show) and hasattr(self,'grid'):
            self._layout_buttons()
        return result

    def _layout_buttons(self):
        from PyQt6.QtCore import QRect
        width=self.contentsRect().width();columns=2
        available=max(1,(width-6*(columns-1))//columns)
        height=max([34]+[button.fontMetrics().boundingRect(QRect(0,0,max(1,available-24),1000),
            Qt.TextFlag.TextWordWrap,button.text()).height()+16 for button in self.buttons.values()])
        for i,key in enumerate(self._keys):
            button=self.buttons[key];position=(i//columns,i%columns)
            if self.grid.getItemPosition(self.grid.indexOf(button))[:2]!=position:
                self.grid.removeWidget(button);self.grid.addWidget(button,*position)
            if button.height()!=height:button.setFixedHeight(height)

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Paint and watched in self.buttons.values():
            from PyQt6.QtWidgets import QStyleOptionButton,QStylePainter,QStyle
            option=QStyleOptionButton();watched.initStyleOption(option);title=option.text;option.text=''
            painter=QStylePainter(watched);painter.drawControl(QStyle.ControlElement.CE_PushButton,option)
            rect=watched.style().subElementRect(QStyle.SubElement.SE_PushButtonContents,option,watched).adjusted(4,1,-4,-1)
            from PyQt6.QtGui import QPalette
            watched.style().drawItemText(painter,rect,Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,
                option.palette,watched.isEnabled(),title,QPalette.ColorRole.ButtonText)
            return True
        return super().eventFilter(watched,event)
