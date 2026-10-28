from PyQt6.QtWidgets import QApplication
from core.models import QPCRPanel
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem

_APP=QApplication.instance() or QApplication([])


def test_source_svg_style_is_included_in_layout_undo():
    from PyQt6.QtWidgets import QMainWindow
    from test_layout_chart_adapter import _qpcr_panel
    from ui.controllers.layout_edit_controller import LayoutEditController
    from ui.widgets.shared_svg_text import SvgEditableTextItem
    from copy import deepcopy
    window=QMainWindow();panel=_qpcr_panel();canvas=QPCRLayoutCanvas();canvas.set_panel(panel)
    controller=LayoutEditController(window);controller.set_target('qpcr',panel,canvas)
    before=deepcopy(panel.data['qpcr_layout']['plots'])
    item=next(i for i in canvas.scene.items() if isinstance(i,SvgEditableTextItem))
    item.setSelected(True)
    controller.apply_style({'font_size_pt':13.5})
    assert panel.data['qpcr_layout']['plots']!=before
    controller.undo()
    assert panel.data['qpcr_layout']['plots']==before
    controller.set_target();canvas.close();window.close();_APP.processEvents()


def test_source_inline_text_commit_has_its_own_undo_step():
    from PyQt6.QtWidgets import QMainWindow
    from PyQt6.QtGui import QTextCursor
    from ui.controllers.layout_edit_controller import LayoutEditController
    window=QMainWindow();panel=QPCRPanel(panel_id='undo');canvas=QPCRLayoutCanvas();canvas.set_panel(panel)
    item=canvas.add_text('Before')
    controller=LayoutEditController(window);controller.set_target('qpcr',panel,canvas)
    item.begin_edit();cursor=item.textCursor();cursor.select(QTextCursor.SelectionType.Document);cursor.insertText('After');item.setTextCursor(cursor);item.finish_edit()
    _APP.processEvents()
    assert panel.data['qpcr_layout']['texts'][0]['text']=='After'
    controller.undo()
    assert panel.data['qpcr_layout']['texts'][0]['text']=='Before'
    controller.set_target();canvas.close();window.close();_APP.processEvents()


def test_histology_adapters_keep_shared_rich_text_and_shape_fields():
    from copy import deepcopy
    from test_layout_chart_adapter import _ihc_panel,_bf_panel
    from ui.layout_adapters.ihc_layout_adapter import ihc_layout_objects,apply_ihc_layout_objects
    from ui.layout_adapters.brightfield_layout_adapter import brightfield_layout_objects,apply_brightfield_layout_objects
    for factory,key,read,write in [(_ihc_panel,'ihc_layout',ihc_layout_objects,apply_ihc_layout_objects),(_bf_panel,'brightfield_layout',brightfield_layout_objects,apply_brightfield_layout_objects)]:
        panel=factory()
        before=deepcopy(panel.analysis)
        panel.data[key]['texts']=[{'text_uuid':'rich','text':'H2O','x':5,'y':8,'font_size':8.5}]
        panel.data[key]['shapes']=[{'shape_uuid':'arrow','shape_type':'arrow','x':10,'y':20,'width':50,'height':20}]
        objects=read(panel)
        text=SharedTextItem(next(o for o in objects if o.object_type=='text'))
        text.apply_text_range_style(1,1,{'vertical_align':'subscript'})
        shape=SharedShapeItem(next(o for o in objects if o.metadata.get('shape_uuid')=='arrow'))
        shape.apply_shape_style({'stroke_dash':'dash','fill_alpha':.25})
        updates={text.layout_object.object_id:text.to_layout_object(),shape.layout_object.object_id:shape.layout_object}
        write(panel,[updates.get(o.object_id,o) for o in objects],append_audit=False)
        restored=read(panel)
        t=next(o for o in restored if o.object_type=='text')
        s=next(o for o in restored if o.metadata.get('shape_uuid')=='arrow')
        assert t.metadata['text']['runs'][0]['vertical_align']=='subscript'
        assert s.metadata['stroke_dash']=='dash' and s.metadata['fill_alpha']==.25
        assert panel.analysis==before


def test_histology_deleted_text_does_not_reappear_after_writeback():
    from test_layout_chart_adapter import _ihc_panel
    from ui.layout_adapters.ihc_layout_adapter import ihc_layout_objects,apply_ihc_layout_objects,ihc_canvas_spec
    from ui.widgets.layout_editor_canvas import LayoutEditorCanvas
    panel=_ihc_panel()
    panel.data['ihc_layout']['texts']=[{'text_uuid':'remove','text':'Remove me','x':10,'y':10}]
    canvas=LayoutEditorCanvas()
    canvas.set_layout_objects(ihc_canvas_spec(panel),ihc_layout_objects(panel))
    item=next(i for i in canvas.layout_items() if i.layout_object.object_type=='text')
    item.setSelected(True)
    canvas.delete_selected()
    apply_ihc_layout_objects(panel,canvas.layout_objects())
    assert panel.data['ihc_layout']['texts']==[]
    canvas.close()


def test_qpcr_shared_text_and_shape_survive_refresh_with_formatting():
    panel=QPCRPanel(panel_id='q')
    canvas=QPCRLayoutCanvas()
    canvas.set_panel(panel)
    text=canvas.add_text('H2O')
    assert isinstance(text,SharedTextItem)
    text.apply_text_range_style(1,1,{'vertical_align':'subscript','bold':True})
    from ui.layout_adapters.qpcr_layout_adapter import qpcr_layout_objects
    assert next(o for o in qpcr_layout_objects(panel) if o.object_type=='text').metadata['text']['runs'][0]['vertical_align']=='subscript'
    text.update_geometry(30,40,180,60)
    shape=canvas.add_shape('arrow')
    assert isinstance(shape,SharedShapeItem)
    shape.apply_shape_style({'stroke_dash':'dash','fill_alpha':.3})
    canvas.persist_layout_edits()
    canvas.refresh()
    text=next(i for i in canvas.scene.items() if i.data(0)=='qpcr_layout_text')
    assert text.layout_object.width==180
    assert text.to_layout_object().metadata['text']['runs'][0]['vertical_align']=='subscript'
    shape=next(i for i in canvas.scene.items() if i.data(0)=='qpcr_layout_shape')
    assert shape.shape_kind=='arrow' and shape.layout_object.metadata['stroke_dash']=='dash'
    canvas.close()


def test_fluorescence_shared_annotations_persist_without_changing_measurements(tmp_path):
    from PyQt6.QtGui import QImage
    from core.models import FluorescencePanel
    from ui.widgets.fluoro_canvas import FluorescenceCanvas
    from copy import deepcopy
    image=QImage(64,48,QImage.Format.Format_RGB32);image.fill(0xFF808080)
    path=tmp_path/'signal.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='f');panel.import_image(path)
    canvas=FluorescenceCanvas();canvas.set_panel(panel);canvas.set_step(4)
    before=deepcopy(panel.analysis)
    text=canvas.add_layout_text('H2O')
    assert isinstance(text,SharedTextItem)
    text.apply_text_range_style(1,1,{'vertical_align':'subscript'})
    shape=canvas.add_layout_shape('arrow')
    assert isinstance(shape,SharedShapeItem)
    shape.apply_shape_style({'stroke_dash':'dash','fill_alpha':.4})
    canvas.refresh()
    text=next(i for i in canvas.scene.items() if i.data(0)=='fluoro_layout_text')
    shape=next(i for i in canvas.scene.items() if i.data(0)=='fluoro_layout_shape')
    assert text.to_layout_object().metadata['text']['runs'][0]['vertical_align']=='subscript'
    assert shape.layout_object.metadata['stroke_dash']=='dash'
    assert panel.analysis==before
    canvas.close()


def test_wb_domain_text_uses_shared_rich_format_and_keeps_statistics():
    from pathlib import Path
    from copy import deepcopy
    from PyQt6.QtGui import QFont
    from ui.main_window import MainWindow
    from test_layout_chart_adapter import _wb_panel
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window._confirm_destructive_action=lambda *args:True
    panel=_wb_panel();panel.data.pop('image_data',None)
    panel.data['wb_layout']['bands'][0]['protein']='H2O'
    window._active_wb_panel=lambda:panel
    window.active_module='wb'
    window.wb_canvas.set_panel(panel)
    before=deepcopy(panel.data['wb_statistics'])
    try:
        item=window.wb_canvas._editable_text('H2O',('protein',0))
        window.wb_canvas.scene.addItem(item)
        item.setSelected(True)
        item.apply_text_range_style(1,1,{'vertical_align':'subscript','bold':True})
        window.apply_wb_selected_text_style({'font_size_pt':11.5})
        stored=panel.data['wb_layout']['text_styles']['protein']['items']['0']['text_payload']
        assert stored['runs'][0]['vertical_align']=='subscript'
        assert item.layout_object.style.font_size_pt==11.5
        reopened=window.wb_canvas._editable_text('H2O',('protein',0))
        window.wb_canvas._apply_text_style(reopened,'protein',0,QFont('Arial',10))
        assert reopened.to_layout_object().metadata['text']['runs'][0]['vertical_align']=='subscript'
        assert panel.data['wb_statistics']==before
    finally:
        window.close();_APP.processEvents()


def test_wb_free_annotations_survive_scene_and_adapter_roundtrip():
    from core.models import WesternBlotPanel
    from ui.widgets.wb_canvas import WBCanvas
    from ui.layout_adapters.wb_layout_adapter import wb_layout_objects,apply_wb_layout_objects
    panel=WesternBlotPanel(panel_id='wb')
    panel.data['wb_layout']={'enabled':True}
    canvas=WBCanvas();canvas.set_panel(panel)
    item=canvas.add_free_annotation('arrow')
    item.apply_shape_style({'stroke_dash':'dash','fill_alpha':.25})
    item.update_geometry(10,20,150,60)
    objects=wb_layout_objects(panel)
    assert any(o.metadata.get('wb_kind')=='free_annotation' for o in objects)
    apply_wb_layout_objects(panel,objects)
    canvas.render()
    item=next(i for i in canvas.scene.items() if i.data(0)=='wb_free_annotation')
    assert item.shape_kind=='arrow' and item.layout_object.width==150
    assert item.layout_object.metadata['stroke_dash']=='dash'
    canvas.close()
