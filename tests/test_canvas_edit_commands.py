from copy import deepcopy
import pytest

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

from core.models import Project, ExternalImagePanel, QPCRPanel
from ui.controllers.composer_edit_controller import ComposerEditController
from ui.controllers.layout_edit_controller import LayoutEditController
from ui.widgets.composer_canvas import ComposerCanvas
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas

APP = QApplication.instance() or QApplication([])


def test_composer_copy_cut_paste_keep_source_reference_and_single_history_step():
    project = Project(title='clipboard')
    panel = ExternalImagePanel(panel_id='source')
    project.add_panel(panel)
    canvas = ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(panel.panel_uuid)
    canvas.add_text_annotation('H2O')
    canvas.add_shape_annotation('arrow')
    controller = ComposerEditController(canvas)
    controller.reset()
    for item in canvas.scene.items():
        if item.data(0) in {'panel_ref', 'annotation'}:
            item.setSelected(True)
    before = deepcopy(controller._snapshot())
    assert hasattr(controller, 'handle_command')
    assert controller.handle_command('copy')
    assert controller.handle_command('paste')
    figure = canvas.current_figure()
    assert len(figure.panel_refs) == 2
    assert all(ref.panel_uid == panel.panel_uuid for ref in figure.panel_refs)
    assert len(project.panels) == 1
    assert len({a.annotation_uid for a in figure.annotations}) == len(figure.annotations)
    assert controller.undo_stack.count() == 1
    after = deepcopy(controller._snapshot())
    controller.undo()
    assert controller._snapshot() == before
    controller.redo()
    assert controller._snapshot() == after
    assert controller.handle_command('cut')
    assert len(canvas.current_figure().panel_refs) == 1
    controller.undo()
    assert controller._snapshot() == after
    canvas.close()


def test_source_clipboard_survives_refresh_and_undo_and_reuses_existing_history():
    window = QMainWindow()
    panel = QPCRPanel(panel_id='source')
    canvas = QPCRLayoutCanvas()
    canvas.set_panel(panel)
    text = canvas.add_text('H2O')
    text.apply_text_range_style(1, 1, {'vertical_align': 'subscript'})
    shape = canvas.add_shape('arrow')
    canvas.persist_layout_edits()
    controller = LayoutEditController(window)
    controller.set_target('qpcr', panel, canvas)
    text.setSelected(True)
    shape.setSelected(True)
    before = deepcopy(panel.data['qpcr_layout'])
    analysis = deepcopy(panel.analysis)
    assert hasattr(controller, 'handle_command')
    controller.handle_command('copy')
    controller.handle_command('paste')
    assert len(panel.data['qpcr_layout']['texts']) == 2
    assert len(panel.data['qpcr_layout']['shapes']) == 2
    assert panel.data['qpcr_layout']['texts'][1]['text_payload']['runs'][0]['vertical_align'] == 'subscript'
    assert len(controller.undo_states) == 1
    canvas.refresh()
    assert len([i for i in canvas.scene.items() if i.data(0) == 'qpcr_layout_text']) == 2
    controller.undo()
    assert panel.data['qpcr_layout']['texts'] == before['texts']
    assert panel.data['qpcr_layout']['shapes'] == before['shapes']
    controller.redo()
    assert len(panel.data['qpcr_layout']['texts']) == 2
    assert panel.analysis == analysis
    controller.set_target()
    canvas.close()
    window.close()


def test_source_mixed_drag_is_one_undo_and_geometry_is_persisted():
    from test_canvas_object_interaction import drag
    window = QMainWindow()
    canvas = QPCRLayoutCanvas()
    canvas.resize(760, 570)
    panel = QPCRPanel(panel_id='drag')
    canvas.set_panel(panel)
    text = canvas.add_text('Move')
    shape = canvas.add_shape('rectangle')
    text.setPos(70, 60)
    shape.setPos(70, 140)
    canvas.persist_layout_edits()
    controller = LayoutEditController(window)
    controller.set_target('qpcr', panel, canvas)
    before = deepcopy(panel.data['qpcr_layout'])
    canvas.show()
    APP.processEvents()
    text.setSelected(True)
    shape.setSelected(True)
    drag(canvas, (95, 72), (130, 95))
    assert text.isSelected() and shape.isSelected()
    assert panel.data['qpcr_layout']['texts'][0]['x'] > 90
    assert len(controller.undo_states) == 1
    controller.undo()
    assert panel.data['qpcr_layout']['texts'] == before['texts']
    assert panel.data['qpcr_layout']['shapes'] == before['shapes']
    controller.set_target()
    canvas.close()
    window.close()


@pytest.mark.parametrize('module', ['ihc', 'brightfield'])
def test_histology_image_copy_creates_layout_instance_without_copying_measurements(module):
    from test_layout_chart_adapter import _ihc_panel, _bf_panel
    from ui.panels.ihc_workspace import IHCWorkspace
    from ui.panels.brightfield_workspace import BrightfieldWorkspace
    from ui.layout_adapters.ihc_layout_adapter import ihc_layout_objects, ihc_canvas_spec
    from ui.layout_adapters.brightfield_layout_adapter import brightfield_layout_objects, brightfield_canvas_spec
    factory, workspace_type, objects, spec = ((_ihc_panel, IHCWorkspace, ihc_layout_objects, ihc_canvas_spec)
        if module == 'ihc' else (_bf_panel, BrightfieldWorkspace, brightfield_layout_objects, brightfield_canvas_spec))
    window = QMainWindow()
    panel = factory()
    workspace = workspace_type(QWidget())
    workspace.panel = panel
    canvas = workspace.layout_canvas
    canvas.set_layout_objects(spec(panel), objects(panel), svg_text_changed_callback=workspace.persist_layout_edits)
    controller = LayoutEditController(window)
    controller.set_target(module, panel, canvas, workspace)
    controller._persist()
    APP.clipboard().clear()
    item = next(i for i in canvas.layout_items() if i.layout_object.object_type in {'image', 'plot'})
    item.setSelected(True)
    analysis = deepcopy(panel.analysis)
    before = deepcopy(panel.data[f'{module}_layout']['outputs'])
    controller.handle_command('copy')
    controller.handle_command('paste')
    records = panel.data[f'{module}_layout']['outputs']
    assert len(records) == len(before)+1
    assert records[-1]['instance_uid']
    assert len({obj.object_id for obj in objects(panel)}) == len(objects(panel))
    assert panel.analysis == analysis
    assert len(controller.undo_states) == 1
    controller.undo()
    assert panel.data[f'{module}_layout']['outputs'] == before
    controller.set_target()
    workspace.close()
    window.close()


def test_fluoro_drag_and_clipboard_use_layout_history_not_measurements(tmp_path):
    from PyQt6.QtGui import QImage
    from core.models import FluorescencePanel
    from ui.widgets.fluoro_canvas import FluorescenceCanvas
    from test_canvas_object_interaction import drag
    image=QImage(64,48,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
    path=tmp_path/'signal.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='fluoro');panel.import_image(path)
    canvas=FluorescenceCanvas();canvas.resize(800,600);canvas.set_panel(panel);canvas.set_step(4)
    text=canvas.add_layout_text('Move',x=60,y=50)
    shape=canvas.add_layout_shape('rectangle');shape.setPos(60,140)
    window=QMainWindow();controller=LayoutEditController(window)
    controller.set_target('fluoro',panel,canvas)
    canvas.show();APP.processEvents()
    text.setSelected(True);shape.setSelected(True)
    before=canvas._capture_layout_snapshot()
    canvas._layout_undo_stack.clear()
    analysis=deepcopy(panel.analysis)
    drag(canvas,(90,65),(125,90))
    assert text.isSelected() and shape.isSelected()
    assert len(canvas._layout_undo_stack)==1
    controller.undo()
    assert canvas._capture_layout_snapshot()==before
    for item in canvas.scene.items():
        if item.data(0) in {'fluoro_layout_text','fluoro_layout_shape'}:item.setSelected(True)
    controller.handle_command('copy');controller.handle_command('paste')
    assert len(panel.active_image()['layout_texts'])==2
    controller.undo()
    assert len(panel.active_image()['layout_texts'])==1
    assert panel.analysis==analysis
    controller.set_target();canvas.close();window.close()


def test_fluoro_image_paste_is_an_instance_and_cut_does_not_remove_source(tmp_path):
    from PyQt6.QtGui import QImage
    from core.models import FluorescencePanel
    from ui.widgets.fluoro_canvas import FluorescenceCanvas
    image=QImage(64,48,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
    path=tmp_path/'image.png';image.save(str(path))
    panel=FluorescencePanel(panel_id='f');panel.import_image(path)
    panel.active_image()['layout_mode']='merge_only'
    canvas=FluorescenceCanvas();canvas.set_panel(panel);canvas.set_step(4)
    window=QMainWindow();controller=LayoutEditController(window);controller.set_target('fluoro',panel,canvas)
    item=next(i for i in canvas.scene.items() if i.data(0)=='fluoro_image_merge')
    item.setSelected(True)
    controller.handle_command('copy');controller.handle_command('paste')
    assert len([i for i in canvas.scene.items() if i.data(0)=='fluoro_image_merge' and i.isVisible()])==2
    assert len(panel.images())==1
    controller.handle_command('cut')
    assert len([i for i in canvas.scene.items() if i.data(0)=='fluoro_image_merge' and i.isVisible()])==1
    assert len(panel.images())==1
    controller.undo()
    assert len([i for i in canvas.scene.items() if i.data(0)=='fluoro_image_merge' and i.isVisible()])==2
    controller.set_target();canvas.close();window.close()


def test_wb_free_annotation_drag_and_paste_use_existing_history():
    from pathlib import Path
    from core.models import WesternBlotPanel
    from ui.main_window import MainWindow
    from test_canvas_object_interaction import drag
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    panel=WesternBlotPanel(panel_id='wb')
    panel.data['wb_layout']={'enabled':True}
    window._active_wb_panel=lambda:panel
    window.active_module='wb'
    canvas=window.wb_canvas;canvas.set_panel(panel);canvas.view.set_mode('move')
    text=canvas.add_free_annotation('text');text.setPos(70,60)
    shape=canvas.add_free_annotation('rectangle');shape.setPos(70,140)
    controller=window.layout_edit_controller;controller.set_target('wb',panel,canvas)
    canvas.resize(800,600);canvas.show();APP.processEvents()
    text.setSelected(True);shape.setSelected(True)
    before=deepcopy(panel.data['wb_layout']['free_annotations'])
    window.wb_history.clear()
    drag(canvas,(100,75),(140,95))
    assert text.isSelected() and shape.isSelected()
    assert len(window.wb_history.undo_stack)==1
    controller.undo()
    assert panel.data['wb_layout']['free_annotations']==before
    for item in canvas.scene.items():
        if item.data(0)=='wb_free_annotation':item.setSelected(True)
    controller.handle_command('copy');controller.handle_command('paste')
    assert len(panel.data['wb_layout']['free_annotations'])==4
    controller.undo()
    assert len(panel.data['wb_layout']['free_annotations'])==2
    controller.set_target();window.close();APP.processEvents()


def test_source_shortcuts_after_toolbar_focus_and_text_edit_are_isolated():
    from ui.widgets.layout_clipboard import LAYOUT_MIME
    window=QMainWindow();canvas=QPCRLayoutCanvas();window.setCentralWidget(canvas)
    panel=QPCRPanel(panel_id='keys');canvas.set_panel(panel);item=canvas.add_text('Text')
    controller=LayoutEditController(window);controller.set_target('qpcr',panel,canvas)
    window.show();APP.processEvents();item.setSelected(True)
    canvas.zoom_controls.fit_button.setFocus()
    APP.clipboard().clear()
    QTest.keyClick(canvas.zoom_controls.fit_button,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
    assert APP.clipboard().mimeData().hasFormat(LAYOUT_MIME)
    QTest.keyClick(canvas.zoom_controls.fit_button,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier)
    assert len(panel.data['qpcr_layout']['texts'])==2
    item=next(i for i in canvas.scene.items() if i.data(0)=='qpcr_layout_text')
    item.begin_edit();canvas.view.setFocus()
    QTest.keyClick(canvas.view,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(canvas.view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
    assert APP.clipboard().text()=='Text'
    assert not APP.clipboard().mimeData().hasFormat(LAYOUT_MIME)
    controller.set_target();window.close();APP.processEvents()


def test_cut_does_not_silently_drop_unsupported_selected_objects():
    from PyQt6.QtWidgets import QGraphicsRectItem, QGraphicsItem
    window=QMainWindow();canvas=QPCRLayoutCanvas();panel=QPCRPanel(panel_id='cut')
    canvas.set_panel(panel);text=canvas.add_text('Keep')
    other=QGraphicsRectItem(0,0,10,10)
    other.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
    canvas.scene.addItem(other);other.setSelected(True);text.setSelected(True)
    controller=LayoutEditController(window);controller.set_target('qpcr',panel,canvas)
    controller.handle_command('cut')
    assert len(panel.data['qpcr_layout']['texts'])==1
    assert other.scene() is canvas.scene
    controller.set_target();canvas.close();window.close()


def test_source_history_includes_scale_bar_state():
    window=QMainWindow();canvas=QPCRLayoutCanvas();panel=QPCRPanel(panel_id='history')
    panel.data['qpcr_layout']={'scale_bars':[{'enabled':True}],'texts':[],'shapes':[]}
    canvas.set_panel(panel)
    controller=LayoutEditController(window);controller.set_target('qpcr',panel,canvas)
    assert controller._snapshot()['scale_bars']==[{'enabled':True}]
    controller.set_target();canvas.close();window.close()


def test_external_plain_text_pastes_as_one_undoable_text_object():
    APP.clipboard().setText('First line\n第二行')
    canvas=ComposerCanvas();canvas.set_project(Project(title='paste text'))
    controller=ComposerEditController(canvas);controller.reset()
    controller.handle_command('paste')
    assert len(canvas.current_figure().annotations)==1
    assert canvas.current_figure().annotations[0].properties['content']=='First line\n第二行'
    controller.undo();assert not canvas.current_figure().annotations
    canvas.close()
    window=QMainWindow();source=QPCRLayoutCanvas();panel=QPCRPanel(panel_id='text')
    source.set_panel(panel);editing=LayoutEditController(window);editing.set_target('qpcr',panel,source)
    editing.handle_command('paste')
    assert panel.data['qpcr_layout']['texts'][0]['text']=='First line\n第二行'
    editing.undo();assert not panel.data.get('qpcr_layout',{}).get('texts')
    editing.set_target();source.close();window.close()
