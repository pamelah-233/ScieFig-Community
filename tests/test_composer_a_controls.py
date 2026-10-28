from pathlib import Path

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsView

from core.models import Project
from ui.main_window import MainWindow
from ui.panels.composer_workspace import ComposerWorkspace

_APP = QApplication.instance() or QApplication([])


def test_composer_has_collapsible_properties_sidebar_and_restores_other_modules(desktop_screen):
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / 'resources')
    window._confirm_destructive_action = lambda *args: True
    window.setFixedSize(1600,1000)
    window.set_module('qpcr')
    window.v9_shell.restore_right_panel()
    window.set_module('composer')
    window.show(); _APP.processEvents()
    shell = window.v9_shell
    shell.restore_right_panel()
    shell._sync_control_visibility()
    shell._apply_splitter_sizes()
    _APP.processEvents()
    assert not shell.right_panel.isHidden()
    assert shell.right_show_button.isHidden()
    workspace=window.composer_workspace
    assert shell.right_panel.isAncestorOf(workspace.format_controls)
    shell.collapse_right_panel();_APP.processEvents()
    assert shell.right_show_button.isVisible()
    shell.right_show_button.click();_APP.processEvents()
    assert not shell.is_right_panel_collapsed()
    assert workspace.format_controls.isVisible()
    # First-level arrange/style commands intentionally sit between text format
    # and geometry. Check adjacent visible sections, not across those commands.
    previous=workspace.format_controls
    body=workspace.inspector_body
    for index in range(body.indexOf(previous)+1,body.indexOf(workspace.geometry)+1):
        widget=body.itemAt(index).widget()
        if widget is None or widget.isHidden():continue
        gap=widget.mapTo(workspace.sidebar,QPoint()).y()-(previous.mapTo(workspace.sidebar,QPoint()).y()+previous.height())
        assert 0<=gap<=12
        previous=widget
    assert workspace.selection_label.height()<=workspace.selection_label.sizeHint().height()+4
    workspace.show_assets();_APP.processEvents()
    assert shell.right_panel.isAncestorOf(workspace.assets)
    assert workspace.assets.isVisible()
    assert not workspace.asset_dialog.isVisible()
    shell.collapse_right_panel()
    workspace.show_inspector();_APP.processEvents()
    assert not shell.is_right_panel_collapsed()
    assert workspace.format_controls.isVisible()
    assert not workspace.inspector_dialog.isVisible()
    workspace.toggle_focus();_APP.processEvents()
    assert shell.is_right_panel_collapsed()
    assert workspace.focus_button.isVisible()
    workspace.toggle_focus();_APP.processEvents()
    assert not shell.is_right_panel_collapsed()
    window.set_module('qpcr'); _APP.processEvents()
    assert not shell.right_panel.isHidden()
    assert not shell.is_right_panel_collapsed()
    window.set_module('stylelab')
    window.set_module('composer')
    window.set_module('qpcr')
    assert not shell.is_right_panel_collapsed()
    shell.collapse_right_panel()
    window.set_module('composer'); window.set_module('qpcr')
    assert shell.is_right_panel_collapsed()
    window.close(); _APP.processEvents()


def test_left_tools_create_shapes_and_return_to_selection_after_draw_or_escape():
    workspace = ComposerWorkspace(); workspace.set_project(Project(title='tools'))
    workspace.resize(1100, 760); workspace.show(); _APP.processEvents()
    assert set(workspace.tool_buttons) == {'select', 'pan', 'text', 'rectangle', 'ellipse', 'line', 'arrow'}
    assert workspace.tool_rail.geometry().right() < workspace.canvas.geometry().left()
    workspace.tool_buttons['pan'].click()
    assert workspace.canvas.view.dragMode() == QGraphicsView.DragMode.ScrollHandDrag
    workspace.tool_buttons['rectangle'].click()
    assert workspace.draw.kind == 'rectangle'
    view = workspace.canvas.view
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(280, 230))
    QTest.mouseMove(view.viewport(), QPoint(420, 310))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(420, 310))
    _APP.processEvents()
    assert workspace.canvas.current_figure().annotations[-1].properties['shape_type'] == 'rectangle'
    assert workspace.tool_buttons['select'].isChecked()
    assert view.dragMode() == QGraphicsView.DragMode.RubberBandDrag
    workspace.tool_buttons['text'].click()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert workspace.draw.kind is None
    assert workspace.tool_buttons['select'].isChecked()
    workspace.close(); _APP.processEvents()


def test_distribute_menu_has_both_axes_and_requires_three_unlocked_objects():
    workspace = ComposerWorkspace(); workspace.set_project(Project(title='spacing'))
    assert not workspace.distribute_button.isEnabled()
    annotations = []
    for x, y in [(0, 0), (20, 50), (80, 90)]:
        annotations.append(workspace.controller.add_annotation('rectangle', QRectF(x*4, y*4, 40, 40)))
    for item in workspace.canvas.scene.items():
        if item.data(0) == 'annotation': item.setSelected(True)
    assert workspace.distribute_button.isEnabled()
    workspace.distribute_actions['horizontal'].trigger()
    assert [a.x_mm for a in annotations] == [0, 40, 80]
    assert [a.y_mm for a in annotations] == [0, 50, 90]
    workspace.distribute_actions['vertical'].trigger()
    assert [a.y_mm for a in annotations] == [0, 45, 90]
    annotations[0].locked = True
    workspace.canvas.render(); workspace.sync_selection()
    assert not workspace.distribute_button.isEnabled()
    workspace.close(); _APP.processEvents()
