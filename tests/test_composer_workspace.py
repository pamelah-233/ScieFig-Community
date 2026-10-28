from PyQt6.QtWidgets import QApplication
from core.models import Project
from ui.panels.composer_workspace import ComposerWorkspace

_APP=QApplication.instance() or QApplication([])


def test_draw_arrow_keeps_direction_and_does_not_open_inspector():
    from PyQt6.QtCore import QRectF
    workspace=ComposerWorkspace();workspace.set_project(Project(title='draw'))
    workspace.add_annotation('arrow')
    assert workspace.draw.kind=='arrow'
    workspace.draw.created.emit('arrow',QRectF(40,50,80,40),{'x1':80,'y1':0,'x2':0,'y2':40})
    annotation=workspace.canvas.current_figure().annotations[-1]
    assert annotation.properties['x1']==20 and annotation.properties['x2']==0
    assert not workspace.inspector_dialog.isVisible()
    workspace.close()


def test_composer_a_has_large_stage_closed_drawers_and_page_controls():
    workspace=ComposerWorkspace()
    workspace.set_project(Project(title='workspace'))
    workspace.resize(1280,800)
    workspace.show()
    _APP.processEvents()
    assert not workspace.asset_dialog.isVisible()
    assert not workspace.inspector_dialog.isVisible()
    assert workspace.canvas.view.width()>1100
    assert workspace.canvas.view.height()>600
    workspace.show_inspector()
    assert workspace.inspector_dialog.isVisible()
    workspace.close()


def test_layer_selection_does_not_delete_the_clicked_row():
    from PyQt6 import sip
    workspace=ComposerWorkspace()
    workspace.set_project(Project(title='layers'))
    workspace.controller.add_annotation('text')
    workspace.sync_layers()
    row=workspace.layer_list.item(0)
    workspace._select_layer(row)
    assert not sip.isdeleted(row)
    workspace.close()


def test_loaded_project_layers_are_ready_without_opening_inspector():
    from core.models.composer import AnnotationObject
    workspace=ComposerWorkspace()
    workspace.set_project(Project(title='empty'))
    loaded=Project(title='loaded')
    loaded.ensure_active_composer_figure()
    loaded.composer_figures[0].annotations.append(AnnotationObject(type='text',properties={'content':'Loaded label'}))
    workspace.set_project(loaded)
    assert workspace.layer_list.count()==1
    assert workspace.layer_list.item(0).text()=='Loaded label'
    workspace.close()


def test_workspace_controls_switch_language_without_recreating_canvas():
    from core.utils.i18n import set_language
    set_language('en')
    workspace=ComposerWorkspace();workspace.set_project(Project(title='languages'))
    assert workspace.undo_button.text()=='Undo'
    assert workspace.assets_button.text()=='Assets'
    assert any(getattr(item,'toPlainText',lambda:'' )()=='Drop Panel Object' for item in workspace.canvas.scene.items())
    view=workspace.canvas.view
    set_language('zh');workspace.retranslate()
    assert workspace.undo_button.text()=='撤销'
    assert workspace.assets_button.text()=='素材'
    assert any(getattr(item,'toPlainText',lambda:'' )()=='拖入图形对象' for item in workspace.canvas.scene.items())
    assert workspace.canvas.view is view
    workspace.close();set_language('en')


def test_main_window_ctrl_z_reaches_composer_without_wb_shortcut_conflict():
    from pathlib import Path
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from ui.main_window import MainWindow
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window._confirm_destructive_action=lambda *args:True
    window.set_module('composer');window.show();window.activateWindow()
    workspace=window.composer_workspace
    workspace.controller.add_annotation('text')
    workspace.canvas.view.setFocus();_APP.processEvents()
    QTest.keyClick(workspace.canvas.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
    _APP.processEvents()
    assert not workspace.canvas.current_figure().annotations
    window.close();_APP.processEvents()


def test_source_tools_are_not_offered_for_an_uninitialized_histology_layout():
    from pathlib import Path
    from ui.main_window import MainWindow
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window._confirm_destructive_action=lambda *args:True
    window.ihc_workspace.current_step=3
    window.brightfield_workspace.current_step=4
    assert window._source_layout_context('ihc') is None
    assert window._source_layout_context('brightfield') is None
    window.close();_APP.processEvents()


def test_main_window_opens_composer_workspace_and_restores_layout_controls():
    from pathlib import Path
    from ui.main_window import MainWindow
    window=MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    window._confirm_destructive_action=lambda *args:True
    window.set_module('composer')
    assert window.canvas_stack.currentWidget() is window.composer_workspace
    assert not window.v9_shell.is_right_panel_collapsed()
    window.set_module('qpcr')
    assert window.chart_kind_controls.parent() is window.v9_shell.right_panel
    window.close();_APP.processEvents()
