"""Inspect effective formatting without applying defaults to the document."""
import os
from copy import deepcopy
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication
from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from ui.widgets.shared_layout_controls import SharedLayoutControls
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem

APP = QApplication.instance() or QApplication([])


def text_item():
    return SharedTextItem(LayoutObject('formula','text',0,0,140,45,text='H2O',
                          style=LayoutObjectStyle(font_size_pt=10,text_color='#111111')))


def select_range(item, start, end):
    cursor = item.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    item.setTextCursor(cursor)


def test_selected_text_range_shows_effective_format_without_writes():
    item = text_item()
    item.apply_text_range_style(1,1,{'font_size_pt':18,'color':'#ff0000','vertical_align':'subscript','underline':True})
    item.begin_edit()
    select_range(item,1,2)
    before = deepcopy(item.to_layout_object().to_dict())
    controls = SharedLayoutControls()
    changes = []
    controls.style_requested.connect(changes.append)
    controls.set_selection([item])
    assert controls.font_size.value() == 18
    assert controls.text_color.property('current_color') == '#ff0000'
    assert controls.baseline.currentData() == 'subscript'
    assert controls.toggles['underline'].isChecked()
    assert changes == []
    assert item.to_layout_object().to_dict() == before
    controls.close()


def test_format_controls_refresh_when_text_selection_moves():
    item = text_item()
    item.apply_text_range_style(1,1,{'font_size_pt':18,'color':'#ff0000'})
    item.begin_edit()
    select_range(item,0,1)
    controls = SharedLayoutControls()
    controls.set_selection([item])
    assert controls.font_size.value() == 10
    select_range(item,1,2)
    assert controls.font_size.value() == 18
    controls.close()


def test_mixed_text_ranges_are_not_presented_as_base_style():
    item = text_item()
    item.apply_text_range_style(1,1,{'font_size_pt':18,'color':'#ff0000','vertical_align':'superscript'})
    item.begin_edit()
    select_range(item,0,3)
    controls = SharedLayoutControls()
    controls.set_selection([item])
    assert controls.font_size.property('mixed')
    assert controls.text_color.property('current_color') is None
    assert controls.baseline.currentIndex() == -1
    controls.close()


def test_mixed_shapes_display_mixed_width_alpha_dash_without_writes():
    items = [SharedShapeItem(LayoutObject(str(i),'shape',0,0,80,40,
              style=LayoutObjectStyle(stroke_width=width),metadata={'fill_alpha':alpha,'stroke_dash':dash}))
              for i,(width,alpha,dash) in enumerate([(1,.2,'solid'),(5,.8,'dash')])]
    controls = SharedLayoutControls()
    changes = []
    controls.style_requested.connect(changes.append)
    controls.set_selection(items)
    assert controls.stroke_width.property('mixed')
    assert controls.fill_alpha.property('mixed')
    assert controls.dash.currentIndex() == -1
    assert changes == []
    controls.set_selection([items[1]])
    assert controls.stroke_width.value() == 5 and not controls.stroke_width.property('mixed')
    assert controls.fill_alpha.value() == .8
    assert controls.dash.currentData() == 'dash'
    controls.close()
