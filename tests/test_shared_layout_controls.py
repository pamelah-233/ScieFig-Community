from PyQt6.QtWidgets import QApplication
from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_controls import SharedLayoutControls

_APP = QApplication.instance() or QApplication([])


def test_format_controls_emit_only_changed_field_and_show_mixed_sizes():
    controls = SharedLayoutControls()
    items = [SharedTextItem(LayoutObject(str(i), "text", i*30, 10, 100, 40, text="Value",
             style=LayoutObjectStyle(font_size_pt=size))) for i, size in enumerate([8.5,12])]
    events=[]
    controls.style_requested.connect(events.append)
    controls.set_selection(items)
    assert events == []
    assert controls.font_size.value() == 0
    controls.font_size.setValue(9.5)
    assert events == [{"font_size_pt":9.5}]
    assert [item.layout_object.x for item in items] == [0,30]
    controls.close()


def test_toolbar_format_keeps_text_range_after_focus_moves_to_controls():
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QFocusEvent
    from ui.widgets.shared_layout_controls import apply_selection_style
    item=SharedTextItem(LayoutObject('formula','text',0,0,100,40,text='H2O'))
    item.begin_edit()
    cursor=item.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(2,cursor.MoveMode.KeepAnchor)
    item.setTextCursor(cursor)
    item.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut,Qt.FocusReason.OtherFocusReason))
    apply_selection_style([item],{'vertical_align':'superscript'})
    runs=item.to_layout_object().to_dict()['metadata']['text']['runs']
    assert any(r['start']==1 and r['length']==1 and r['vertical_align']=='superscript' for r in runs)
