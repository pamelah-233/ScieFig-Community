"""One compact, delta-only formatting surface for every layout editor."""
from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QFontComboBox,
    QDoubleSpinBox, QToolButton, QPushButton, QComboBox, QColorDialog, QLabel)
from core.utils.i18n import get_language
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem


def _text_formats(item):
    """Read effective Qt formats, including the currently selected rich-text range."""
    cursor=item.textCursor()
    start,end=0,max(0,item.document().characterCount()-1)
    if item.is_editing and cursor.hasSelection():
        start,end=cursor.selectionStart(),cursor.selectionEnd()
    elif item.retained_text_selection:
        start,length=item.retained_text_selection
        end=start+length
    elif item.is_editing:
        start=end=cursor.position()
    formats=[]
    block=item.document().begin()
    while block.isValid():
        iterator=block.begin()
        while not iterator.atEnd():
            fragment=iterator.fragment()
            if fragment.isValid() and fragment.position()<end and fragment.position()+fragment.length()>start:
                formats.append(fragment.charFormat())
            iterator+=1
        block=block.next()
    if not formats:
        local=QTextCursor(item.document())
        local.setPosition(start)
        formats=[local.charFormat()]
    base=item.layout_object.style
    factor=getattr(item,'_font_factor',1)
    result=[]
    for fmt in formats:
        families=fmt.fontFamilies()
        vertical=fmt.verticalAlignment()
        result.append(dict(
            font_family=families[0] if families else base.font_family,
            font_size_pt=round((fmt.fontPointSize() or base.font_size_pt)/factor,8),
            bold=fmt.fontWeight()>=QFont.Weight.Bold,
            italic=fmt.fontItalic(),underline=fmt.fontUnderline(),
            text_color=fmt.foreground().color().name() if fmt.foreground().style()!=Qt.BrushStyle.NoBrush else QColor(base.text_color).name(),
            vertical_align={QTextCharFormat.VerticalAlignment.AlignSuperScript:'superscript',
                            QTextCharFormat.VerticalAlignment.AlignSubScript:'subscript'}.get(vertical,'normal'),
        ))
    return result


class SharedLayoutControls(QWidget):
    style_requested = pyqtSignal(dict)
    add_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._syncing = False
        self._items = []
        root = QVBoxLayout(self)
        root.setContentsMargins(8,8,8,8)
        root.setSpacing(8)
        self.insert_row = QHBoxLayout()
        self._insert_buttons = []
        for kind, label in [('text','T'),('rectangle','□'),('ellipse','○'),('line','╱'),('arrow','↗')]:
            button = QPushButton(label)
            button.setMaximumWidth(42)
            button.clicked.connect(lambda checked=False,k=kind:self.add_requested.emit(k))
            self.insert_row.addWidget(button)
            self._insert_buttons.append((button,kind))
        root.addLayout(self.insert_row)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        self.text_group = QWidget()
        text = QFormLayout(self.text_group)
        text.setContentsMargins(0,0,0,0)
        self.font_family = QFontComboBox()
        self.font_family.setMinimumWidth(0)
        self.font_family.currentFontChanged.connect(lambda f:self._emit('font_family',f.family()))
        self.font_size = self._number(.1,144,.5,'font_size_pt')
        self.font_size.setSpecialValueText('—')
        self.font_size.setSuffix(' pt')
        text.addRow('Font',self.font_family)
        text.addRow('Size',self.font_size)
        styles = QHBoxLayout()
        self.toggles = {}
        for key,label in [('bold','B'),('italic','I'),('underline','U')]:
            button=QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.clicked.connect(lambda value,k=key:self._emit(k,value))
            styles.addWidget(button)
            self.toggles[key]=button
        self.text_color=QPushButton('●')
        self.text_color.clicked.connect(lambda:self._choose_color('text_color'))
        styles.addWidget(self.text_color)
        text.addRow(styles)
        self.baseline=QComboBox()
        for label,value in [('Normal','normal'),('Superscript','superscript'),('Subscript','subscript')]:
            self.baseline.addItem(label,value)
        self.baseline.activated.connect(lambda:self._emit('vertical_align',self.baseline.currentData()))
        text.addRow('Baseline',self.baseline)
        self.alignment=QComboBox()
        for value in ['left','center','right','justify']:
            self.alignment.addItem(value,value)
        self.alignment.activated.connect(lambda:self._emit('alignment',self.alignment.currentData()))
        text.addRow('Align',self.alignment)
        self.line_spacing=self._number(1,3,.1,'line_spacing')
        text.addRow('Spacing',self.line_spacing)
        root.addWidget(self.text_group)
        self.shape_group=QWidget()
        shape=QFormLayout(self.shape_group)
        shape.setContentsMargins(0,0,0,0)
        self.stroke_width=self._number(.1,20,.25,'stroke_width')
        self.stroke_width.setSuffix(' pt')
        shape.addRow('Stroke',self.stroke_width)
        self.stroke_color=QPushButton('Color')
        self.stroke_color.clicked.connect(lambda:self._choose_color('stroke_color'))
        shape.addRow('Line',self.stroke_color)
        self.dash=QComboBox()
        for value in ['solid','dash','dot']:
            self.dash.addItem(value,value)
        self.dash.activated.connect(lambda:self._emit('stroke_dash',self.dash.currentData()))
        shape.addRow('Dash',self.dash)
        self.fill_color=QPushButton('Color')
        self.fill_color.clicked.connect(lambda:self._choose_color('fill_color'))
        shape.addRow('Fill',self.fill_color)
        self.fill_alpha=self._number(0,1,.05,'fill_alpha')
        shape.addRow('Opacity',self.fill_alpha)
        root.addWidget(self.shape_group)
        root.addStretch()
        self.retranslate()
        self.set_selection([])

    def _number(self,low,high,step,key):
        spin=QDoubleSpinBox()
        mixed_value=0 if low>0 else -step
        spin.setRange(mixed_value,high)
        spin.setProperty('mixedValue',mixed_value)
        spin.setSpecialValueText('—')
        spin.setSingleStep(step)
        spin.valueChanged.connect(lambda value:self._emit(key,value) if value>=low else None)
        return spin

    def _emit(self,key,value):
        if not self._syncing and not (key=='font_size_pt' and value<=0):
            self.style_requested.emit({key:value})

    def _choose_color(self,key):
        button=getattr(self,key)
        color=QColorDialog.getColor(QColor(button.property('current_color') or '#333333'),self)
        if color.isValid():
            self._emit(key,color.name())

    def set_selection(self,items):
        for item in self._items:
            if isinstance(item,SharedTextItem):
                try:item.format_selection_changed.disconnect(self._refresh_format)
                except (TypeError,RuntimeError):pass
        self._items=[item for item in items if isinstance(item,(SharedTextItem,SharedShapeItem))]
        for item in self._items:
            if isinstance(item,SharedTextItem):item.format_selection_changed.connect(self._refresh_format)
        self._refresh_format()

    @staticmethod
    def _show_number(widget,values):
        mixed=len(values)!=1
        widget.setProperty('mixed',mixed)
        widget.setValue(widget.property('mixedValue') if mixed else next(iter(values)))

    @staticmethod
    def _show_choice(widget,values):
        widget.setPlaceholderText('—')
        widget.setCurrentIndex(widget.findData(next(iter(values))) if len(values)==1 else -1)

    def _refresh_format(self):
        if self._syncing:return
        texts=[i for i in self._items if isinstance(i,SharedTextItem)]
        shapes=[i for i in self._items if isinstance(i,SharedShapeItem)]
        blockers=[QSignalBlocker(widget) for widget in (
            self.font_family,self.font_size,*self.toggles.values(),self.baseline,
            self.alignment,self.line_spacing,self.stroke_width,self.fill_alpha,self.dash)]
        self._syncing=True
        self.text_group.setVisible(bool(texts))
        self.shape_group.setVisible(bool(shapes))
        self.text_group.setEnabled(any(not i.layout_object.locked for i in texts))
        self.shape_group.setEnabled(any(not i.layout_object.locked for i in shapes))
        self.hint.setVisible(not self._items)
        if texts:
            formats=[fmt for item in texts for fmt in _text_formats(item)]
            self._show_number(self.font_size,{fmt['font_size_pt'] for fmt in formats})
            families={fmt['font_family'] for fmt in formats}
            if len(families)==1:self.font_family.setCurrentFont(QFont(next(iter(families))))
            else:self.font_family.setCurrentIndex(-1)
            for key,button in self.toggles.items():
                values={fmt[key] for fmt in formats}
                button.setChecked(len(values)==1 and True in values)
                button.setProperty('mixed',len(values)>1)
                button.setToolTip('混合 / Mixed' if len(values)>1 else key)
            colors={fmt['text_color'] for fmt in formats}
            self._show_color(self.text_color,colors)
            self._show_choice(self.baseline,{fmt['vertical_align'] for fmt in formats})
            metadata=[dict(item.layout_object.metadata.get('text',{})) for item in texts]
            self._show_choice(self.alignment,{m.get('alignment','left') for m in metadata})
            self._show_number(self.line_spacing,{float(m.get('line_spacing',1)) for m in metadata})
        if shapes:
            self._show_color(self.stroke_color,{i.layout_object.style.stroke_color for i in shapes})
            self._show_color(self.fill_color,{i.layout_object.style.fill_color for i in shapes})
            self._show_number(self.stroke_width,{i.layout_object.style.stroke_width for i in shapes})
            self._show_number(self.fill_alpha,{float(i.layout_object.metadata.get('fill_alpha',1)) for i in shapes})
            self._show_choice(self.dash,{i.layout_object.metadata.get('stroke_dash','solid') for i in shapes})
        self._syncing=False
        del blockers

    @staticmethod
    def _show_color(button,colors):
        value=next(iter(colors)) if len(colors)==1 else None
        button.setText(value or '—')
        button.setProperty('current_color',value)
        button.setToolTip(value or '混合 / Mixed')

    def retranslate(self):
        zh=get_language()=='zh'
        text_labels=['字体','字号','基线','对齐','行距'] if zh else ['Font','Size','Baseline','Align','Spacing']
        for widget,label in zip([self.font_family,self.font_size,self.baseline,self.alignment,self.line_spacing],text_labels):
            self.text_group.layout().labelForField(widget).setText(label)
        for widget,label in zip([self.stroke_width,self.stroke_color,self.dash,self.fill_color,self.fill_alpha],
                                ['线宽','描边','线型','填充','不透明度'] if zh else ['Width','Stroke','Dash','Fill','Opacity']):
            self.shape_group.layout().labelForField(widget).setText(label)
        for index,label in enumerate(['正常','上标','下标'] if zh else ['Normal','Superscript','Subscript']):
            self.baseline.setItemText(index,label)
        for index,label in enumerate(['左对齐','居中','右对齐','两端对齐'] if zh else ['Left','Center','Right','Justify']):
            self.alignment.setItemText(index,label)
        for index,label in enumerate(['实线','虚线','点线'] if zh else ['Solid','Dash','Dot']):
            self.dash.setItemText(index,label)
        self.hint.setText('选择文字或图形以编辑；双击文字可直接输入。' if zh else 'Select text or shapes. Double-click text to edit inline.')
        for button,kind in self._insert_buttons:
            button.setToolTip(({'text':'文字','rectangle':'矩形','ellipse':'椭圆','line':'直线','arrow':'箭头'}[kind] if zh else kind))


def apply_selection_style(items,delta):
    """Format the focused text range or selected objects; never their positions."""
    for item in items:
        if not isinstance(item,(SharedTextItem,SharedShapeItem)) or item.layout_object.locked:
            continue
        if isinstance(item,SharedShapeItem):
            item.apply_shape_style(delta)
            continue
        cursor=item.textCursor()
        retained=item.retained_text_selection
        char_delta={key:value for key,value in delta.items() if key not in {'alignment','line_spacing'}}
        paragraph={key:delta[key] for key in ('alignment','line_spacing') if key in delta}
        block=QTextCursor(item.document()) if paragraph and (cursor.hasSelection() and item.is_editing or retained or 'vertical_align' in delta) else None
        if block is not None:block.beginEditBlock()
        range_applied=False
        if item.is_editing and cursor.hasSelection() and char_delta:
            item.apply_text_range_style(cursor.selectionStart(),cursor.selectionEnd()-cursor.selectionStart(),char_delta)
            range_applied=True
        elif retained and char_delta:
            item.apply_text_range_style(retained[0],retained[1],char_delta)
            range_applied=True
        elif 'vertical_align' in delta:
            item.apply_text_range_style(0,len(item.toPlainText()),delta)
            range_applied=True
        else:
            item.apply_text_style(delta)
        if range_applied and paragraph:
            item.apply_paragraph_style(paragraph)
        if block is not None:block.endEditBlock()
