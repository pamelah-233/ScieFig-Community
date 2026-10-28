import os
from copy import deepcopy
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtCore import QRectF, QSettings
from PyQt6.QtWidgets import QApplication, QMainWindow
from core.models import Project, QPCRPanel
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas
from ui.widgets.composer_canvas import ComposerCanvas
from ui.controllers.layout_edit_controller import LayoutEditController
from ui.controllers.composer_edit_controller import ComposerEditController

APP = QApplication.instance() or QApplication([])


@pytest.fixture(params=['source', 'composer'])
def editor(request, tmp_path):
    window = QMainWindow()
    panel = QPCRPanel(panel_id='tools')
    project = Project(title='tools'); project.add_panel(panel)
    if request.param == 'source':
        canvas = QPCRLayoutCanvas(); canvas.set_panel(panel)
        owner = LayoutEditController(window); owner.set_target('qpcr', panel, canvas)
    else:
        canvas = ComposerCanvas(); canvas.set_project(project)
        owner = ComposerEditController(canvas); owner.reset()
    if hasattr(owner, 'tools'):
        owner.tools.settings = QSettings(str(tmp_path/'annotation-styles.ini'), QSettings.Format.IniFormat)
    def add(kind, x):
        if request.param == 'source':
            owner.add(kind, QRectF(x, 40, 140, 50), {})
        else:
            owner.add_annotation(kind, QRectF(x, 40, 140, 50))
        APP.processEvents()
        item = canvas.scene.selectedItems()[0]
        if hasattr(item, 'finish_edit'): item.finish_edit()
        return item
    yield owner, canvas, panel, project, add
    if request.param == 'source': owner.set_target()
    canvas.close(); window.close(); APP.processEvents()


def select(canvas, *items):
    canvas.scene.clearSelection()
    for item in items: item.setSelected(True)


def test_format_brush_changes_only_style_and_can_undo(editor):
    owner, canvas, panel, _, add = editor
    first = add('text', 30)
    first.apply_text_style({'font_size_pt': 19, 'text_color': '#123456', 'bold': True})
    select(canvas, first)
    assert owner.tools.copy_style()
    second = add('text', 260)
    original = second.to_layout_object()
    protected = deepcopy(panel.analysis)
    assert owner.tools.paste_style()
    changed = second.to_layout_object()
    from ui.widgets.shared_layout_controls import _text_formats
    assert all(fmt['font_size_pt'] == 19 and fmt['text_color'] == '#123456' for fmt in _text_formats(second))
    assert (changed.x, changed.y, changed.width, changed.height, changed.text) == (
        original.x, original.y, original.width, original.height, original.text)
    assert panel.analysis == protected
    owner.undo()
    styles = [i.layout_object.style.font_size_pt for i in canvas.scene.items() if hasattr(i, 'toPlainText') and i.toPlainText() == original.text]
    assert original.style.font_size_pt in styles


def test_presets_are_annotation_only_and_persistent(editor):
    owner, canvas, _, _, add = editor
    first = add('arrow', 30)
    first.apply_shape_style({'stroke_width': 4, 'stroke_dash': 'dot', 'fill_alpha': .25})
    assert owner.tools.save_preset('Arrow example')
    assert 'Arrow example' in owner.tools.presets()
    second = add('rectangle', 260)
    before = second.layout_object
    assert owner.tools.apply_preset('Arrow example')
    assert second.layout_object.style.stroke_width == 4
    assert second.layout_object.metadata['stroke_dash'] == 'dot'
    assert second.layout_object.metadata['shape_kind'] == before.metadata['shape_kind']
    assert second.layout_object.x == before.x and second.layout_object.width == before.width
    assert owner.tools.remove_preset('Arrow example')
    assert not owner.tools.presets()


@pytest.mark.parametrize('spacing',[1.,1.5])
def test_format_brush_copies_paragraph_format_with_character_format(editor,spacing):
    owner,canvas,_,_,add=editor
    first=add('text',30)
    first.apply_text_style({'font_size_pt':16,'alignment':'center','line_spacing':spacing})
    assert owner.tools.copy_style()
    second=add('text',260)
    second.apply_text_style({'line_spacing':2})
    assert owner.tools.paste_style()
    payload=second.to_layout_object().metadata['text']
    assert payload['alignment']=='center'
    assert payload['line_spacing']==spacing


def test_format_brush_respects_selected_text_range_and_keeps_editing(editor):
    from PyQt6.QtGui import QTextCursor
    from ui.widgets.shared_layout_controls import _text_formats
    owner,canvas,_,_,add=editor
    first=add('text',30)
    first.apply_text_style({'alignment':'center','line_spacing':1.5})
    first.apply_text_range_style(1,1,{'font_size_pt':18,'color':'#cc2222','vertical_align':'subscript'})
    first.begin_edit();cursor=first.textCursor();cursor.setPosition(1)
    cursor.setPosition(2,QTextCursor.MoveMode.KeepAnchor);first.setTextCursor(cursor)
    assert owner.tools.copy_style()
    first.finish_edit()
    second=add('text',260);base=second.layout_object.style.font_size_pt
    second.begin_edit();cursor=second.textCursor();cursor.setPosition(2)
    cursor.setPosition(3,QTextCursor.MoveMode.KeepAnchor);second.setTextCursor(cursor)
    assert owner.tools.paste_style()
    assert second.is_editing
    assert _text_formats(second)[0]['font_size_pt']==18
    owner.undo()
    assert _text_formats(second)[0]['font_size_pt']==base
    assert second.to_layout_object().metadata['text']['alignment']=='left'
    assert second.to_layout_object().metadata['text']['line_spacing']==1
    owner.redo()
    assert _text_formats(second)[0]['font_size_pt']==18
    assert second.to_layout_object().metadata['text']['alignment']=='center'
    cursor=second.textCursor();cursor.setPosition(0)
    cursor.setPosition(1,QTextCursor.MoveMode.KeepAnchor);second.setTextCursor(cursor)
    assert _text_formats(second)[0]['font_size_pt']==base
    second.finish_edit()


def test_incompatible_style_does_not_modify_document(editor):
    owner, canvas, _, _, add = editor
    add('text', 30); assert owner.tools.copy_style()
    shape = add('rectangle', 260)
    before = shape.layout_object.to_dict()
    assert not owner.tools.paste_style()
    assert shape.layout_object.to_dict() == before


def test_groups_persist_and_undo_as_one_command(editor, tmp_path):
    from core.models import Project
    owner, canvas, panel, project, add = editor
    first = add('text', 30); first_id = first.layout_object.object_id
    second = add('arrow', 260)
    first = next(i for i in canvas.view.object_interaction.items() if getattr(getattr(i, 'layout_object', None), 'object_id', None) == first_id)
    select(canvas, first, second)
    assert owner.tools.group_selection()
    groups = deepcopy(owner.tools.groups())
    assert len(groups) == 1
    assert set(owner.tools.group_members([first])) == {first, second}
    path = tmp_path/'groups.sfig'; project.save_sfig(path)
    restored = Project.load_sfig(path)
    if hasattr(owner, 'panel'):
        assert restored.get_panel(panel.panel_uuid).data['qpcr_layout']['layout_groups'] == groups
    else:
        assert restored.ensure_active_composer_figure().canvas['layout_groups'] == groups
    owner.undo(); assert not owner.tools.groups()
    owner.redo(); assert owner.tools.groups() == groups
    for item in canvas.view.object_interaction.items(): item.setSelected(True)
    assert owner.tools.ungroup_selection()
    assert not owner.tools.groups()


def test_group_with_locked_member_cannot_be_partially_moved(editor):
    owner, canvas, _, _, add = editor
    first = add('text', 30); first_id = first.layout_object.object_id
    second = add('arrow', 260)
    first = next(i for i in canvas.view.object_interaction.items() if getattr(getattr(i, 'layout_object', None), 'object_id', None) == first_id)
    select(canvas, first, second); assert owner.tools.group_selection()
    second.set_flags_for_locked(True)
    assert owner.tools.group_members([first]) == []


def test_group_mouse_selection_and_alt_individual_selection(editor):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    owner, canvas, _, _, add = editor
    first = add('text', 30); first_id = first.layout_object.object_id
    second = add('arrow', 260)
    first = next(i for i in canvas.view.object_interaction.items() if getattr(getattr(i, 'layout_object', None), 'object_id', None) == first_id)
    select(canvas, first, second); assert owner.tools.group_selection()
    canvas.resize(900, 650); canvas.show(); APP.processEvents(); canvas.fit_to_canvas(); APP.processEvents()
    select(canvas)
    pos = canvas.view.mapFromScene(first.sceneBoundingRect().center())
    QTest.mouseClick(canvas.view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert set(owner.tools.selected()) == {first, second}
    select(canvas)
    QTest.mouseClick(canvas.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, pos)
    assert owner.tools.selected() == [first]


def test_group_clipboard_makes_independent_pasted_group(editor):
    owner, canvas, _, _, add = editor
    first = add('text', 30); first_id = first.layout_object.object_id
    second = add('arrow', 260)
    first = next(i for i in canvas.view.object_interaction.items() if getattr(getattr(i, 'layout_object', None), 'object_id', None) == first_id)
    select(canvas, first, second); assert owner.tools.group_selection()
    before = deepcopy(owner.tools.groups())
    owner.handle_command('copy'); owner.handle_command('paste')
    assert len(owner.tools.groups()) == 2
    new = next(group for uid, group in owner.tools.groups().items() if uid not in before)
    assert len(new) == 2 and not set(new).intersection(next(iter(before.values())))
    assert set(new) == {owner.tools.item_key(i) for i in owner.tools.selected()}
    owner.undo(); assert owner.tools.groups() == before


def test_shared_arrange_is_atomic_and_has_selection_requirements(editor):
    owner, canvas, _, _, add = editor
    first = add('rectangle', 30); first_id = first.layout_object.object_id
    second = add('rectangle', 260)
    first = next(i for i in canvas.view.object_interaction.items() if getattr(getattr(i, 'layout_object', None), 'object_id', None) == first_id)
    select(canvas, first)
    assert not owner.tools.available('align_left')[0]
    assert owner.tools.available('align_left')[1]
    select(canvas, first, second)
    assert owner.tools.available('align_left')[0]
    assert not owner.tools.available('distribute_horizontal')[0]
    assert owner.tools.arrange('align_left')
    assert first.sceneBoundingRect().left() == pytest.approx(second.sceneBoundingRect().left())
    owner.undo()
    positions = [i.x() for i in canvas.view.object_interaction.items()]
    assert any(abs(x-260)<.01 for x in positions)


def test_view_location_never_changes_page_or_object_geometry(editor):
    owner, canvas, _, _, add = editor
    item = add('rectangle', 2000)
    select(canvas, item)
    canvas.resize(900, 650); canvas.show(); APP.processEvents(); canvas.fit_to_canvas(); APP.processEvents()
    page = QRectF(canvas._view_controller.backend.content_rect())
    before = item.layout_object.to_dict()
    assert owner.tools.frame_view('selection')
    center = canvas.view.mapFromScene(item.sceneBoundingRect().center())
    assert (center-canvas.view.viewport().rect().center()).manhattanLength() < 5
    assert item.layout_object.to_dict() == before
    assert canvas._view_controller.backend.content_rect() == page
    assert owner.tools.frame_view('all')
    assert owner.tools.frame_view('page')
    assert item.layout_object.to_dict() == before


def test_source_and_composer_expose_the_same_context_menu(editor):
    owner, canvas, _, _, add = editor
    add('rectangle', 30)
    menu = owner.tools.context_menu()
    commands = {action.data() for action in menu.actions() if action.data()}
    assert {'copy', 'cut', 'paste', 'delete', 'properties', 'group', 'ungroup',
            'copy_style', 'paste_style', 'locate', 'front', 'back', 'lock'} <= commands
    menu.close()


def test_composer_page_history_is_retained_per_page():
    project = Project(title='page history')
    canvas = ComposerCanvas(); canvas.set_project(project)
    owner = ComposerEditController(canvas); owner.reset()
    first_uid = canvas.current_figure().figure_uid
    owner.add_annotation('rectangle', QRectF(20, 20, 80, 60))
    canvas.add_page()
    second_uid = canvas.current_figure().figure_uid
    assert first_uid != second_uid
    owner.add_annotation('arrow', QRectF(20, 20, 80, 60))
    canvas.set_active_page(first_uid)
    owner.undo()
    assert canvas.current_figure().figure_uid == first_uid
    assert not canvas.current_figure().annotations
    canvas.set_active_page(second_uid)
    assert len(canvas.current_figure().annotations) == 1
    owner.undo(); assert not canvas.current_figure().annotations
    canvas.set_active_page(first_uid)
    owner.redo(); assert len(canvas.current_figure().annotations) == 1
    canvas.close()


def test_preflight_exposes_object_identifier_for_location():
    project = Project(title='preflight location')
    canvas = ComposerCanvas(); canvas.set_project(project)
    annotation = canvas.add_text_annotation('Overflow')
    annotation.x_mm = canvas.current_figure().canvas['width_mm']-1
    canvas.render()
    warning = next(m for m in canvas.preflight_export() if 'annotation outside canvas' in m['message'])
    assert warning['object_uid'] == annotation.annotation_uid
    canvas.close()


def test_image_resize_keeps_ratio_and_shift_allows_free_resize():
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QPixmap
    from core.models.layout_editor import LayoutObject
    from ui.widgets.layout_editor_items import LayoutEditorImageProxyItem
    item = LayoutEditorImageProxyItem(LayoutObject('image','image',10,20,200,100),QPixmap(200,100))
    handle = item.handles['se']
    class Event:
        def __init__(self, point, modifiers=Qt.KeyboardModifier.NoModifier): self.point,self.mods=point,modifiers
        def scenePos(self): return self.point
        def modifiers(self): return self.mods
        def accept(self): pass
    handle.mousePressEvent(Event(QPointF(210,120)))
    handle.mouseMoveEvent(Event(QPointF(310,140)))
    assert item.layout_object.width/item.layout_object.height == pytest.approx(2)
    handle.mouseMoveEvent(Event(QPointF(310,140),Qt.KeyboardModifier.ShiftModifier))
    assert item.layout_object.width == pytest.approx(300)
    assert item.layout_object.height == pytest.approx(120)


def test_lock_and_layer_survive_scene_rebuild_and_are_undoable(editor):
    owner, canvas, _, _, add = editor
    item = add('rectangle', 30)
    key = owner.tools.item_key(item)
    assert owner.tools.toggle_lock()
    if hasattr(owner,'_refresh'):owner._refresh()
    else:canvas.render()
    restored = next(i for i in canvas.view.object_interaction.items() if owner.tools.item_key(i)==key)
    assert restored.layout_object.locked
    select(canvas, restored); assert owner.tools.toggle_lock()
    owner.tools.change_layer('front')
    restored = next(i for i in canvas.view.object_interaction.items() if owner.tools.item_key(i)==key)
    z = restored.zValue()
    if hasattr(owner,'_refresh'):owner._refresh()
    else:canvas.render()
    restored = next(i for i in canvas.view.object_interaction.items() if owner.tools.item_key(i)==key)
    assert restored.zValue()==z


def test_toolbar_teardown_does_not_leave_live_selection_slot(editor):
    from PyQt6.QtCore import QEvent
    owner, _, _, _, add = editor
    add('rectangle',30)
    owner.tools.bar.deleteLater()
    APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    owner.tools.sync_summary()
    owner.tools.bind()
    assert owner.tools.bar.properties.text()


@pytest.mark.parametrize('module',['wb','fluoro'])
def test_native_annotation_lock_survives_rebuild(tmp_path,module):
    from pathlib import Path
    from ui.main_window import MainWindow
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window.project_recovery.timer.stop();window.project_recovery._alive=False
    try:
        window.set_module(module)
        panel=getattr(window,f'_active_{module}_panel')()
        if module=='wb':
            panel.data.setdefault('wb_layout',{})['enabled']=True
            canvas=window.wb_canvas;canvas.set_panel(panel);canvas.set_mode('move')
            workspace=None
        else:
            from PyQt6.QtGui import QImage
            from PyQt6.QtCore import Qt
            image=QImage(80,60,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
            path=tmp_path/'fluoro.png';assert image.save(str(path))
            panel.import_image(path)
            workspace=window.fluoro_workspace;workspace.set_panel(panel);workspace.set_step(4)
            canvas=workspace.canvas
        controller=window.layout_edit_controller
        controller.set_target(module,panel,canvas,workspace)
        controller.add('rectangle',QRectF(20,20,80,60),{})
        assert controller.tools.toggle_lock()
        controller._refresh()
        shape=next(i for i in canvas.scene.items() if i.data(0) in {'wb_free_annotation','fluoro_layout_shape'})
        assert shape.layout_object.locked
    finally:
        window._capture_clean_project_baseline();window.close();APP.processEvents()


@pytest.mark.parametrize('module',['ihc','brightfield'])
def test_histology_groups_are_not_overwritten_by_cached_layout_root(tmp_path,module):
    from pathlib import Path
    from PyQt6.QtGui import QImage
    from PyQt6.QtCore import Qt
    from ui.main_window import MainWindow
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window.project_recovery.timer.stop();window.project_recovery._alive=False
    try:
        window.set_module(module)
        panel=getattr(window,f'_active_{module}_panel')()
        image=QImage(80,60,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
        path=tmp_path/'image.png';assert image.save(str(path));panel.import_image(path)
        getattr(window,f'show_{module}_layout')();APP.processEvents();window._sync_shared_layout_editor()
        controller=window.layout_edit_controller
        controller.add('rectangle',QRectF(20,20,80,60),{})
        controller.add('arrow',QRectF(140,20,80,60),{})
        for item in controller.tools.canvas.view.object_interaction.items():
            if controller.tools.style_kind(item)=='shape':item.setSelected(True)
        assert controller.tools.group_selection()
        groups=deepcopy(controller.tools.groups())
        assert len(groups)==1
        controller._refresh()
        assert controller.tools.groups()==groups
    finally:
        window._capture_clean_project_baseline();window.close();APP.processEvents()


def test_tool_bar_language_switch_matches_existing_canvas_controls(editor):
    from core.utils.i18n import get_language,set_language
    owner,canvas,_,_,_=editor
    previous=get_language()
    try:
        set_language('zh');canvas.zoom_controls.retranslate()
        assert owner.tools.bar.properties.text().startswith('属性')
        assert owner.tools.bar.buttons['view'].text()=='视图'
        set_language('en');canvas.zoom_controls.retranslate()
        assert owner.tools.bar.properties.text().startswith('Properties')
        assert owner.tools.bar.buttons['view'].text()=='View'
    finally:set_language(previous)


def test_qpcr_plot_layer_and_lock_survive_rebuild():
    from test_layout_chart_adapter import _qpcr_panel
    panel=_qpcr_panel();canvas=QPCRLayoutCanvas();canvas.set_panel(panel)
    window=QMainWindow();owner=LayoutEditController(window);owner.set_target('qpcr',panel,canvas)
    try:
        plot=canvas.layout_plot_items()[0];uid=plot.data(1);plot.setSelected(True)
        assert owner.tools.toggle_lock()
        canvas.refresh();plot=next(i for i in canvas.layout_plot_items() if i.data(1)==uid)
        assert plot.layout_object.locked
        before=deepcopy(next(r for r in panel.data['qpcr_layout']['plots'] if r['plot_uuid']==uid))
        plot.setSelected(True);canvas.apply_plot_style_to_selection({'font_size_pt':22},width_px=700)
        assert next(r for r in panel.data['qpcr_layout']['plots'] if r['plot_uuid']==uid)==before
        plot=next(i for i in canvas.layout_plot_items() if i.data(1)==uid)
        plot.setSelected(True);assert owner.tools.toggle_lock()
        owner.tools.change_layer('front')
        expected=next(i for i in canvas.layout_plot_items() if i.data(1)==uid).zValue()
        canvas.refresh()
        assert next(i for i in canvas.layout_plot_items() if i.data(1)==uid).zValue()==expected
    finally:
        owner.set_target();canvas.close();window.close();APP.processEvents()


def test_qpcr_same_size_respects_each_plots_aspect_lock():
    from test_layout_chart_adapter import _qpcr_panel
    panel=_qpcr_panel()
    panel.data['qpcr_layout']['plots'][1].update(width_px=300,height_px=300)
    canvas=QPCRLayoutCanvas();canvas.set_panel(panel)
    window=QMainWindow();owner=LayoutEditController(window);owner.set_target('qpcr',panel,canvas)
    try:
        plots=canvas.layout_plot_items()
        ratios={p.data(1):p.rect().width()/p.rect().height() for p in plots}
        for plot in plots:plot.setSelected(True)
        assert owner.tools.arrange('same_size')
        assert len({round(p.rect().width(),2) for p in plots})==1
        for plot in plots:
            assert plot.rect().width()/plot.rect().height()==pytest.approx(ratios[plot.data(1)])
    finally:
        owner.set_target();canvas.close();window.close();APP.processEvents()


def test_common_context_menu_keeps_module_specific_actions_accessible(editor,monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QContextMenuEvent
    from PyQt6.QtWidgets import QGraphicsRectItem,QGraphicsItem,QMenu
    _,canvas,_,_,_=editor
    image=QGraphicsRectItem(0,0,100,80);image.setPos(30,30)
    image.setData(0,'wb_layout_image_group');image.setData(1,'native-image')
    image.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,True)
    canvas.scene.addItem(image);canvas.resize(900,650);canvas.show();APP.processEvents();canvas.fit_to_canvas()
    seen=[];advanced_actions=[]
    def choose_advanced(menu,*args):
        action=next((a for a in menu.actions() if a.data()=='module_advanced'),None)
        advanced_actions.append(action)
        return action
    monkeypatch.setattr(QMenu,'exec',choose_advanced)
    monkeypatch.setattr(type(canvas.view),'contextMenuEvent',lambda self,event:seen.append(True))
    pos=canvas.view.mapFromScene(image.sceneBoundingRect().center())
    event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,pos,canvas.view.viewport().mapToGlobal(pos))
    APP.sendEvent(canvas.view.viewport(),event)
    assert advanced_actions and advanced_actions[0] is not None
    assert seen
