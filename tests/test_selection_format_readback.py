"""Selecting an annotation reads its style; it must never format the document."""
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QTextCursor
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from ui.widgets.editable_text_item import EditableMovableTextItem
from ui.widgets.shared_layout_controls import SharedLayoutControls
from ui.widgets.shared_layout_text import SharedTextItem, SharedTextLayout

APP=QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')


def legacy_label(text='DAPI',color='#ffffff'):
    item=EditableMovableTextItem(text,lambda *_:None,lambda *_:None)
    item.setDefaultTextColor(QColor(color))
    font=QFont('Arial');font.setPointSizeF(7.5);item.setFont(font)
    return item


def test_native_font_and_color_setters_update_the_live_document():
    item=legacy_label()
    cursor=QTextCursor(item.document());cursor.setPosition(1)
    assert cursor.charFormat().foreground().color().name()=='#ffffff'
    assert cursor.charFormat().fontPointSize()==7.5


def test_base_style_setters_preserve_rich_runs_and_active_text_edit():
    item=SharedTextItem(LayoutObject('formula','text',0,0,120,40,text='H2O'))
    item.apply_text_range_style(1,1,{'vertical_align':'subscript','color':'#ff5500','bold':True})
    item.begin_edit()
    cursor=item.textCursor();cursor.setPosition(2);item.setTextCursor(cursor)
    font=QFont('Arial');font.setPointSizeF(12.5)
    item.setFont(font);item.setDefaultTextColor(QColor('#83bbf0'))
    assert item.is_editing and item.textCursor().position()==2
    obj=item.to_layout_object()
    run=next(r for r in obj.metadata['text']['runs'] if r['start']==1)
    assert run['color']=='#ff5500' and run['vertical_align']=='subscript' and run['bold']
    cursor=QTextCursor(item.document());cursor.setPosition(0)
    assert cursor.charFormat().foreground().color().name()=='#83bbf0'
    assert cursor.charFormat().fontPointSize()==12.5


def test_plain_text_replacement_keeps_inherited_color_when_format_is_read():
    item=legacy_label()
    font=item.font();font.setBold(True);font.setItalic(True);item.setFont(font)
    item.setPlainText('GFP')
    document=SharedTextLayout.document_for(item.to_layout_object())
    cursor=QTextCursor(document);cursor.setPosition(1)
    assert cursor.charFormat().foreground().color().name()=='#ffffff'
    assert cursor.charFormat().fontPointSize()==7.5
    assert cursor.charFormat().fontWeight()>=QFont.Weight.Bold.value
    assert cursor.charFormat().fontItalic()


@pytest.mark.parametrize('text',['DAPI','50 µm'])
def test_selecting_image_annotation_preserves_rendered_color_and_size(text):
    item=legacy_label(text)
    before=SharedTextLayout.render_to_image(item.layout_object)
    controls=SharedLayoutControls();events=[]
    controls.style_requested.connect(events.append)
    controls.set_selection([item])
    after=SharedTextLayout.render_to_image(item.layout_object)
    assert events==[]
    assert before==after
    assert controls.font_size.value()==7.5
    controls.close()


def test_shared_properties_show_current_custom_color_without_applying_it():
    controls=SharedLayoutControls();events=[]
    controls.style_requested.connect(events.append)
    item=SharedTextItem(LayoutObject('label','text',0,0,120,30,text='DAPI',
        style=LayoutObjectStyle(font_family='Arial',font_size_pt=8.5,text_color='#83bbf0')))
    controls.set_selection([item])
    assert controls.text_color.text().lower()=='#83bbf0'
    assert events==[]
    controls.close()


def test_shared_selection_does_not_emit_widget_format_change_signals():
    controls=SharedLayoutControls();events=[]
    controls.font_size.valueChanged.connect(events.append)
    controls.set_selection([SharedTextItem(LayoutObject('a','text',0,0,100,30,text='A',
        style=LayoutObjectStyle(font_size_pt=8.5)))])
    assert events==[]
    controls.close()


@pytest.fixture
def fluoro_window(tmp_path):
    from ui.main_window import MainWindow
    data=np.zeros((2,48,64),dtype=np.uint16);data[:,12:30,20:40]=1000
    path=tmp_path/'channels.tif'
    tifffile.imwrite(path,data,metadata={'axes':'CYX','Channel':{'Name':['DAPI','GFP']}},photometric='minisblack')
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window.set_module('fluoro')
    panel=window._active_fluoro_panel();panel.import_image(path)
    image=panel.active_image();image['layout_mode']='merge_split';image['channels'][0]['name']='DAPI'
    image['layout_scale_bars']=[{'target_id':'merge_split:channel:0','enabled':True,'manual_um_per_px':1,
        'template':{'label_length_um':10,'font_size':8.5,'text_color':'#ffffff','line_color':'#ffffff'}}]
    window.fluoro_workspace.set_panel(panel);window.fluoro_workspace.set_step(4)
    yield window,window.fluoro_workspace,panel
    window.close();APP.processEvents()


@pytest.mark.parametrize('kind',['channel','scale_bar'])
def test_fluoro_selection_reads_existing_font_and_color_into_workflow(fluoro_window,kind):
    window,workspace,panel=fluoro_window
    label=next(i for i in workspace.canvas.scene.items() if isinstance(i,EditableMovableTextItem)
               and (i.toPlainText()=='DAPI' if kind=='channel' else i.label_type=='fluoro_scale_bar'))
    label.setDefaultTextColor(QColor('#83bbf0'))
    font=QFont('Arial');font.setPointSizeF(8.5);label.setFont(font)
    before=SharedTextLayout.render_to_image(label.layout_object)
    history=len(workspace.canvas._layout_undo_stack)
    window.show();APP.processEvents()
    point=workspace.canvas.view.mapFromScene(label.mapToScene(label.boundingRect().center()))
    QTest.mouseClick(workspace.canvas.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
    APP.processEvents()
    assert label.isSelected()
    assert SharedTextLayout.render_to_image(label.layout_object)==before
    assert workspace.layout_text_size_spin.value()==8.5
    assert workspace.layout_text_color_combo.currentData().lower()=='#83bbf0'
    assert workspace.layout_font_combo.currentFont().family()=='Arial'
    assert len(workspace.canvas._layout_undo_stack)==history


def test_fluoro_single_field_edit_does_not_overwrite_other_selected_formats(fluoro_window):
    _,workspace,_=fluoro_window
    one=workspace.canvas.add_layout_text('One',font_size=8,color='#ffffff')
    two=workspace.canvas.add_layout_text('Two',font_size=12,color='#83bbf0')
    one.setSelected(True);two.setSelected(True);APP.processEvents()
    workspace.layout_text_bold.setChecked(True)
    assert one.layout_object.style.bold and two.layout_object.style.bold
    assert one.layout_object.style.font_size_pt==8
    assert two.layout_object.style.font_size_pt==12
    assert one.layout_object.style.text_color.lower()=='#ffffff'
    assert two.layout_object.style.text_color.lower()=='#83bbf0'
