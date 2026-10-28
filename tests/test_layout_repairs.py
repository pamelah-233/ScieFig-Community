"""Reproduce the confirmed layout integration defects at real UI entry points."""
import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QImage, QWheelEvent
from PyQt6.QtWidgets import QApplication, QScrollArea
from ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window():
    widget = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    widget.project_recovery.timer.stop()
    widget.project_recovery._alive = False
    widget.show()
    widget.show_qpcr_layout()
    APP.processEvents()
    widget._sync_shared_layout_editor()
    yield widget
    widget._capture_clean_project_baseline()
    widget.close()
    APP.processEvents()


def add_text(window):
    window.add_qpcr_layout_text()
    APP.processEvents()
    return next(i for i in window.qpcr_layout_canvas.scene.selectedItems() if i.data(0) == 'qpcr_layout_text')


def test_source_history_survives_module_switch_and_keeps_redo(window):
    c = window.layout_edit_controller
    c.add('rectangle', QRectF(40, 40, 120, 80), {})
    APP.processEvents()
    before = deepcopy(window._active_qpcr_panel().data['qpcr_layout'])
    window.set_module('wb')
    APP.processEvents()
    window.set_module('qpcr')
    window.show_qpcr_layout()
    APP.processEvents()
    window._sync_shared_layout_editor()
    c.undo()
    assert window._active_qpcr_panel().data['qpcr_layout'].get('shapes', []) == []
    window.set_module('viewer')
    APP.processEvents()
    window.set_module('qpcr')
    window.show_qpcr_layout()
    APP.processEvents()
    window._sync_shared_layout_editor()
    c.redo()
    assert window._active_qpcr_panel().data['qpcr_layout']['shapes'] == before['shapes']


def test_qpcr_existing_text_controls_apply_size_and_geometry(window):
    item = add_text(window)
    tools = window.qpcr_layout_workspace
    tools.text_size_spin.setValue(22)
    tools.text_x_spin.setValue(333)
    tools.text_y_spin.setValue(222)
    tools.text_rotation_spin.setValue(30)
    tools.apply_text_style()
    assert item.layout_object.style.font_size_pt == 22
    assert (item.pos().x(), item.pos().y(), item.rotation()) == (333, 222, 30)
    window.qpcr_layout_canvas.refresh()
    restored = next(i for i in window.qpcr_layout_canvas.scene.items() if i.data(0) == 'qpcr_layout_text')
    assert restored.layout_object.style.font_size_pt == 22
    assert (restored.pos().x(), restored.pos().y(), restored.rotation()) == (333, 222, 30)


def test_qpcr_existing_shape_controls_show_current_style(window):
    window.add_qpcr_layout_shape()
    shape = next(i for i in window.qpcr_layout_canvas.scene.items() if i.data(0) == 'qpcr_layout_shape')
    shape.apply_shape_style({'stroke_width': 7})
    shape.setPos(130, 140)
    window.qpcr_layout_canvas.scene.clearSelection()
    shape.setSelected(True)
    assert window.qpcr_layout_workspace.shape_stroke_width_spin.value() == 7
    assert window.qpcr_layout_workspace.shape_x_spin.value() == 130


def test_qpcr_visible_plot_apply_does_not_apply_hidden_annotation_controls(window):
    item = add_text(window)
    item.apply_text_style({'font_size_pt': 19, 'color': '#2468AC'})
    before = deepcopy(item.to_layout_object().to_dict())
    tools = window.qpcr_layout_workspace
    tools.text_size_spin.setValue(30)
    tools.text_color_combo.setEditText('#FF0000')
    tools.apply_button.click()
    assert item.to_layout_object().to_dict() == before


@pytest.mark.parametrize('language', ['zh','en'])
def test_composer_shared_tools_fit_inside_workspace(window,language):
    from core.utils.i18n import get_language,set_language
    previous=get_language()
    try:
        set_language(language)
        window.set_module('composer')
        workspace=window.composer_workspace
        workspace.retranslate()
        window.resize(1280,900)
        APP.processEvents()
        button=workspace.canvas.zoom_controls.zoom_in_button
        right=button.mapTo(workspace,button.rect().topRight()).x()
        assert right<workspace.width()
        assert workspace.canvas.zoom_controls.width()>=workspace.canvas.zoom_controls.minimumSizeHint().width()
    finally:set_language(previous)


@pytest.mark.parametrize('kind', ['text', 'shape'])
def test_qpcr_insert_selects_only_the_new_object(window, kind):
    old = add_text(window)
    if kind == 'text':
        window.qpcr_layout_workspace.add_text_button.click()
    else:
        window.qpcr_layout_workspace.add_shape_button.click()
    APP.processEvents()
    selected = window.qpcr_layout_canvas.scene.selectedItems()
    assert len(selected) == 1
    assert selected[0] is not old


@pytest.mark.parametrize('surface', ['source', 'composer'])
@pytest.mark.parametrize('field', ['format', 'geometry'])
def test_property_window_wheel_scrolls_without_editing(window, surface, field):
    if surface == 'source':
        add_text(window)
        c = window.layout_edit_controller
        c.show()
        dialog, spin = c._docked_scroll, c.controls.font_size
        if field=='geometry':spin=c.geometry_fields['x']
        window.resize(1000,500)
    else:
        window.set_module('composer')
        ws = window.composer_workspace
        ws.controller.add_annotation('text')
        ws.show_inspector()
        dialog, spin = ws.sidebar, ws.format_controls.font_size
        if field=='geometry':spin=ws.geometry_fields['x_mm']
        window.resize(1000,500)
    dialog.resize(320, 250)
    APP.processEvents()
    scroll = dialog if isinstance(dialog,QScrollArea) else dialog.findChild(QScrollArea)
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    value = spin.value()
    point = spin.rect().center()
    wheel = QWheelEvent(QPointF(point), QPointF(spin.mapToGlobal(point)), QPoint(), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    APP.sendEvent(spin, wheel)
    assert bar.value() > 0
    assert spin.value() == value


@pytest.mark.parametrize('module',['ihc','brightfield'])
def test_histology_history_survives_leaving_layout(window,tmp_path,module):
    image=QImage(64,48,QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.white)
    path=tmp_path/'source.png'
    assert image.save(str(path))
    window.set_module(module)
    panel=getattr(window,f'_active_{module}_panel')()
    panel.import_image(path)
    show=getattr(window,f'show_{module}_layout')
    show();APP.processEvents();window._sync_shared_layout_editor()
    c=window.layout_edit_controller
    c.add('rectangle',QRectF(40,40,120,80),{})
    APP.processEvents()
    assert len(panel.data[f'{module}_layout']['shapes'])==1
    window.set_module('viewer');APP.processEvents()
    window.set_module(module);show();APP.processEvents();window._sync_shared_layout_editor()
    c.undo()
    assert not panel.data[f'{module}_layout'].get('shapes')


def test_source_history_does_not_attach_to_new_panel_with_same_uuid(window):
    from core.models import QPCRPanel
    panel=window._active_qpcr_panel()
    c=window.layout_edit_controller
    c.add('rectangle',QRectF(40,40,120,80),{})
    APP.processEvents()
    replacement=QPCRPanel(panel_id='replacement',panel_uuid=panel.panel_uuid)
    window.qpcr_layout_canvas.set_panel(replacement)
    c.set_target('qpcr',replacement,window.qpcr_layout_canvas)
    assert c.panel is replacement
    assert not c.undo_states


def test_qpcr_current_custom_colors_are_not_replaced_by_old_defaults(window):
    item=add_text(window)
    item.apply_text_style({'text_color':'#123456'})
    window.qpcr_layout_canvas.scene.clearSelection();item.setSelected(True)
    tools=window.qpcr_layout_workspace
    assert tools.text_color_combo.currentText().upper()=='#123456'
    tools.apply_text_style()
    assert item.layout_object.style.text_color.upper()=='#123456'


def test_qpcr_legacy_geometry_controls_do_not_move_locked_text(window):
    item=add_text(window)
    item.set_flags_for_locked(True)
    before=item.pos(),item.rotation()
    tools=window.qpcr_layout_workspace
    tools.text_x_spin.setValue(500);tools.text_rotation_spin.setValue(45)
    tools.apply_text_style()
    assert (item.pos(),item.rotation())==before


@pytest.mark.parametrize('count', [1, 2, 5])
def test_qpcr_new_target_plots_start_in_non_overlapping_rows(count):
    from core.models import QPCRPanel
    panel = QPCRPanel(panel_id='initial-placement')
    panel.data['processed_data'] = {'fold_change': [{'target': f'Target{i}'} for i in range(count)]}
    protected = deepcopy(panel.data['processed_data'])
    records = panel.layout_plot_records()
    bounds = [QRectF(r['x'], r['y'], r['width_px'], r['height_px']) for r in records]
    assert all(not a.intersects(b) for i, a in enumerate(bounds) for b in bounds[i+1:])
    assert all(r['width_px'] == 520 and r['height_px'] == 360 for r in records)
    if count > 1:
        assert records[0]['y'] == records[1]['y']
    if count > 2:
        assert records[2]['y'] > records[0]['y']
    before = deepcopy(records)
    assert panel.layout_plot_records() == before
    assert panel.data['processed_data'] == protected


def test_qpcr_new_target_avoids_existing_resized_plot_without_rearranging_it():
    from core.models import QPCRPanel
    panel = QPCRPanel(panel_id='append-placement')
    # Existing target comes last in the analysis order: reserve it before placing new targets.
    panel.data['processed_data'] = {'fold_change': [{'target': name} for name in ['NewA', 'NewB', 'Existing']]}
    existing = {'plot_uuid': 'manual', 'plot_type': 'qpcr_target', 'target': 'Existing', 'enabled': True,
                'x': -50., 'y': -20., 'width_px': 1200, 'height_px': 800,
                'style': {'bar_edge_color': '#123456'}, 'svg_text_overrides': {'1': 'keep'}}
    panel.data['qpcr_layout'] = {'plots': [deepcopy(existing)]}
    records = panel.layout_plot_records()
    assert records[-1] == existing
    bounds = [QRectF(r['x'], r['y'], r['width_px'], r['height_px']) for r in records]
    assert all(not a.intersects(b) for i, a in enumerate(bounds) for b in bounds[i+1:])


def test_qpcr_saved_overlapping_positions_are_not_silently_rearranged():
    from core.models import QPCRPanel
    panel = QPCRPanel(panel_id='saved-placement')
    panel.data['processed_data'] = {'fold_change': [{'target': name} for name in ['A', 'B']]}
    records = panel.layout_plot_records()
    records[1].update(x=records[0]['x'], y=records[0]['y'])
    before = deepcopy(records)
    assert panel.layout_plot_records() == before
