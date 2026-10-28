"""A-layout: shared navigation and existing parameters/actions, not new analysis."""
import os
import sys
from copy import deepcopy
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QFileDialog, QFrame, QLabel, QScrollArea, QVBoxLayout

from core.utils.i18n import set_language
from ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def window(desktop_screen):
    for font in ('arial.ttf','msyh.ttc'):
        QFontDatabase.addApplicationFont(f'C:/Windows/Fonts/{font}')
    set_language('zh')
    win = MainWindow(ROOT / 'resources')
    win.project_recovery.timer.stop()
    win.project_recovery._alive = False
    win._confirm_destructive_action = lambda *a: True
    win.setFixedSize(1366, 820)
    win.show()
    APP.processEvents()
    yield win
    win.close()
    APP.processEvents()
    set_language('en')


@pytest.mark.parametrize('module', ['qpcr', 'wb', 'fluoro', 'ihc', 'brightfield'])
def test_steps_reuse_the_existing_buttons_on_the_title_row(window, module):
    window.set_module(module)
    APP.processEvents()
    ui = window.workflow_ui
    original = window.fluoro_workspace.step_buttons if module == 'fluoro' else getattr(window.data_panel, f'{module}_step_buttons')
    bar = ui.bars[module]
    assert bar.isVisible()
    assert all(button.parentWidget() is bar for button in original)
    assert window.v9_shell.stagebar.isAncestorOf(bar)
    assert window.v9_shell.stagebar.height() <= 68
    original[1].click()
    APP.processEvents()
    assert original[1].isChecked()
    assert all(button.isVisible() for button in original)
    assert len({button.mapTo(window, QPoint()).y() for button in original}) == 1


@pytest.mark.parametrize('module', ['qpcr', 'wb', 'fluoro', 'ihc', 'brightfield'])
def test_primary_actions_are_outside_the_scrolling_parameter_area(window, module):
    window.set_module(module)
    APP.processEvents()
    footer = window.workflow_ui.footers[module]
    assert footer.isVisible()
    assert not any(scroll.isAncestorOf(footer) for scroll in window.findChildren(QScrollArea))
    rect = footer.primary.rect()
    assert window.rect().contains(footer.primary.mapTo(window, rect.topLeft()))
    assert window.rect().contains(footer.primary.mapTo(window, rect.bottomRight()))


def test_footer_calls_existing_import_once_and_tracks_enabled_state(window, monkeypatch):
    window.set_module('wb')
    window.data_panel.set_wb_step(0)
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', lambda *a, **kw: ([], ''))
    source = window.data_panel.wb_import_strip_button
    calls = QSignalSpy(source.clicked)
    APP.processEvents()
    footer = window.workflow_ui.footers['wb']
    footer.primary.click()
    assert len(calls) == 1
    source.setEnabled(False)
    APP.processEvents()
    assert not footer.primary.isEnabled()


def test_fluoro_tasks_keep_parameters_and_do_not_run_analysis(window):
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.set_step(2)
    ws.roi_x.setValue(17)
    ws.roi_fixed_background_spin.setValue(23.5)
    ws.count_diameter_spin.setValue(51)
    ws.count_sensitivity_spin.setValue(1.3)
    ws.ai_sensitivity_spin.setValue(1.2)
    before = [deepcopy(ws.roi_analysis_settings()), deepcopy(ws.count_analysis_settings()), deepcopy(ws.ai_analysis_settings())]
    attempts = deepcopy(window._analysis_attempts)
    combo = ws.measure_task_combo
    for mode in ('spot_count', 'line_profile', 'colocalization', 'ai_assisted', 'roi_intensity'):
        combo.setCurrentIndex(combo.findData(mode))
        APP.processEvents()
        assert ws.measure_mode == mode
        assert ws.measure_mode_buttons[mode].isChecked()
    assert [ws.roi_analysis_settings(), ws.count_analysis_settings(), ws.ai_analysis_settings()] == before
    assert window._analysis_attempts == attempts
    assert not window._analysis_job_service.has_busy_jobs()


def test_advanced_roi_and_scale_controls_remain_accessible_with_values_intact(window):
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.set_step(2)
    ws.roi_x.setValue(31)
    ws.roi_fixed_background_spin.setValue(72)
    advanced = ws.roi_advanced_section
    assert advanced.isAncestorOf(ws.roi_x)
    assert advanced.isAncestorOf(ws.roi_fixed_background_spin)
    assert not advanced.expanded
    advanced.setExpanded(True)
    ws.scale_display_section.setExpanded(True)
    APP.processEvents()
    assert ws.roi_x.isVisible()
    assert not ws.scale_bar_font_combo.isVisible(), 'Scale editing belongs to import/Layout, not measurement'
    assert ws.roi_x.value() == 31
    ws.set_step(0)
    APP.processEvents()
    assert ws.scale_bar_font_combo.isVisible()
    assert ws.roi_fixed_background_spin.value() == 72
    advanced.setExpanded(False)
    advanced.setExpanded(True)
    APP.processEvents()
    assert ws.roi_x.value() == 31


def test_fluoro_footer_arms_the_original_drawing_tool(window):
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.set_step(2)
    APP.processEvents()
    calls = QSignalSpy(ws.draw_square_button.clicked)
    window.workflow_ui.footers['fluoro'].primary.click()
    assert len(calls) == 1


def test_footer_previous_and_next_click_the_original_step_buttons(window):
    window.set_module('wb')
    window.data_panel.set_wb_step(3)
    APP.processEvents()
    footer = window.workflow_ui.footers['wb']
    footer.previous.click()
    APP.processEvents()
    assert window.data_panel.wb_step_buttons[2].isChecked()
    footer.following.click()
    APP.processEvents()
    assert window.data_panel.wb_step_buttons[3].isChecked()


def test_changing_language_and_module_does_not_reset_task_or_analysis_values(window):
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.set_step(2)
    ws._set_measure_mode('spot_count')
    ws.count_diameter_spin.setValue(55)
    set_language('en')
    ws.retranslate()
    window.workflow_ui.sync()
    APP.processEvents()
    assert ws.measure_task_combo.currentData() == 'spot_count'
    assert '\n' not in ws.measure_task_combo.currentText()
    assert ws.count_diameter_spin.value() == 55
    window.set_module('wb')
    window.set_module('fluoro')
    APP.processEvents()
    assert ws.measure_mode == 'spot_count'
    assert ws.count_diameter_spin.value() == 55


def test_labels_do_not_paint_dark_rectangles_on_workbench_surfaces():
    previous = APP.styleSheet()
    APP.setStyleSheet((ROOT / 'resources/sciefig.qss').read_text(encoding='utf-8'))
    panel = QFrame()
    panel.setObjectName('V9RightWorkbenchGroup')
    layout = QVBoxLayout(panel)
    label = QLabel('测量通道')
    label.setObjectName('SectionLabel')
    label.setFixedSize(180, 30)
    layout.addWidget(label)
    panel.show()
    APP.processEvents()
    pix = panel.grab().toImage()
    pos = label.mapTo(panel, QPoint(label.width() - 4, label.height() - 4))
    try:
        assert pix.pixelColor(pos) == pix.pixelColor(8, panel.height() - 8)
    finally:
        panel.close()
        APP.setStyleSheet(previous)


def test_steps_are_content_sized_instead_of_equal_width_tiles(window):
    window.set_module('wb')
    APP.processEvents()
    buttons = window.workflow_ui.buttons['wb']
    assert all(button.width() <= button.sizeHint().width() + 8 for button in buttons)


def test_navigation_inherits_title_row_background(window):
    previous = APP.styleSheet()
    APP.setStyleSheet((ROOT / 'resources/sciefig.qss').read_text(encoding='utf-8'))
    window.set_module('wb')
    APP.processEvents()
    stage = window.v9_shell.stagebar
    nav = window.workflow_ui.navigation
    pixels = stage.grab().toImage()
    position = nav.mapTo(stage, QPoint(nav.width() - 4, nav.height() - 4))
    try:
        assert pixels.pixelColor(position) == pixels.pixelColor(2, 10)
    finally:
        APP.setStyleSheet(previous)


def test_fluoro_task_precedes_secondary_tools_and_uses_full_names(window):
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.set_step(2)
    APP.processEvents()
    assert not ws.mode_combo.isVisible()
    assert not window.analysis_state.bars['fluorescence'].isVisible()
    assert not window.microscopy_workflow.batch_buttons['fluorescence'].isVisible()
    assert ws.measure_task_combo.currentText() == 'ROI 强度测量'
    assert ws.active_image_group.maximumHeight() <= 90
    deck = window.measurement_tasks['fluoro']
    assert deck.stack.currentWidget() is deck.pages['roi_intensity']
    assert deck.pages['roi_intensity'].isAncestorOf(ws.roi_advanced_section)


def test_layout_opens_appearance_without_resetting_measurement_parameters(window):
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.roi_x.setValue(13)
    ws.set_step(4)
    APP.processEvents()
    assert ws.scale_display_section.expanded
    assert ws.roi_x.value() == 13


@pytest.mark.parametrize('module', ['wb', 'fluoro'])
def test_existing_primary_action_is_not_duplicated_in_the_scroll_area(window, module):
    window.set_module(module)
    if module == 'wb':
        window.data_panel.set_wb_step(3)
        source = window.data_panel.wb_density_measure_button
    else:
        window.fluoro_workspace.set_step(2)
        window.fluoro_workspace._set_measure_mode('spot_count')
        source = window.fluoro_workspace.count_button
    APP.processEvents()
    footer = window.workflow_ui.footers[module]
    assert footer.isVisible()
    assert not source.isVisible() or footer.isAncestorOf(source)


def test_advanced_settings_are_reachable_from_the_current_task(window, request):
    previous_style = APP.styleSheet()
    request.addfinalizer(lambda: APP.setStyleSheet(previous_style))
    APP.setStyleSheet((ROOT / 'resources/sciefig.qss').read_text(encoding='utf-8'))
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.set_step(2)
    APP.processEvents()
    # Dedicated task pages supersede the old fixed-heading settings menu.
    assert not window.workflow_ui.headings['fluoro'].isVisible()
    before = deepcopy(ws.roi_analysis_settings())
    ws.roi_advanced_section.toggle.click()
    APP.processEvents()
    assert ws.roi_advanced_section.expanded
    assert ws.roi_analysis_settings() == before
    scroll = ws.workflow_panel.findChild(QScrollArea)
    for _ in range(8):
        APP.processEvents()
    scroll.ensureWidgetVisible(ws.roi_x)
    for _ in range(8):
        APP.processEvents()
    assert scroll.viewport().rect().contains(ws.roi_x.mapTo(scroll.viewport(), ws.roi_x.rect().center()))
    ws.secondary_tools_section.toggle.click()
    APP.processEvents()
    assert ws.secondary_tools_section.expanded
    assert ws.mode_combo.isVisible()


@pytest.mark.parametrize('language', ['zh', 'en'])
@pytest.mark.parametrize('module', ['qpcr', 'wb', 'fluoro', 'ihc', 'brightfield'])
def test_workflow_height_uses_current_step_not_hidden_pages(window, module, language):
    previous_style = APP.styleSheet()
    APP.setStyleSheet((ROOT / 'resources/sciefig.qss').read_text(encoding='utf-8'))
    try:
        set_language(language)
        window.set_module(module)
        workspace = window.fluoro_workspace if module == 'fluoro' else window.data_panel
        stack = workspace.context_stack if module == 'fluoro' else getattr(workspace, f'{module}_context_stack')
        scroll = window.workflow_ui.panels[module].findChild(QScrollArea)
        for width in (350, 520):
            shell = window.v9_shell
            shell.body_splitter.moveSplitter(sum(shell.body_splitter.sizes()) - width, 2)
            for button in window.workflow_ui.buttons[module]:
                button.click()
                for _ in range(8):
                    APP.processEvents()
                page = stack.currentWidget()
                expected = page.heightForWidth(stack.width()) if page.hasHeightForWidth() else page.sizeHint().height()
                assert stack.heightForWidth(stack.width()) == expected, (module, stack.currentIndex(), width)
                assert stack.height() <= max(expected, scroll.viewport().height()) + 24, (module, stack.currentIndex(), width)
    finally:
        APP.setStyleSheet(previous_style)


def test_fluoro_layout_appearance_follows_preview_without_empty_page_spacer(window):
    window.set_module('fluoro')
    ws = window.fluoro_workspace
    ws.set_step(3)
    for _ in range(8):
        APP.processEvents()
    ws.set_step(4)
    for _ in range(8):
        APP.processEvents()
    page = ws.context_stack.currentWidget()
    last = page.layout().itemAt(page.layout().count() - 1)
    assert last.spacerItem() is not None
    assert last.geometry().height() <= 24
    scroll = ws.workflow_panel.findChild(QScrollArea)
    preview_bottom = ws.preview_layout_button.mapTo(scroll.widget(), ws.preview_layout_button.rect().bottomLeft()).y()
    appearance_top = ws.scale_display_section.mapTo(scroll.widget(), QPoint()).y()
    assert appearance_top - preview_bottom <= 48


def test_current_step_height_grows_for_wrapped_content_at_narrow_width():
    from PyQt6.QtWidgets import QWidget
    from ui.panels.fluorescence_workspace import CurrentPageStackedWidget
    stack = CurrentPageStackedWidget(compact=True)
    page = QWidget()
    layout = QVBoxLayout(page)
    label = QLabel('A long explanation that needs to wrap in the narrow workflow sidebar. ' * 18)
    label.setWordWrap(True)
    layout.addWidget(label)
    stack.addWidget(page)
    stack.resize(180, 1000)
    stack.show()
    for _ in range(8):
        APP.processEvents()
    try:
        expected = page.heightForWidth(stack.width())
        assert expected > page.sizeHint().height()
        assert stack.maximumHeight() >= expected
    finally:
        stack.close()


@pytest.mark.parametrize('module', ['viewer', 'composer'])
def test_canvas_only_modules_remove_the_title_only_stagebar(window, module):
    window.set_module(module)
    for _ in range(8):
        APP.processEvents()
    shell = window.v9_shell
    assert not shell.stagebar.isVisible()
    assert window.canvas_stack.mapTo(shell.stage_area, QPoint()).y() == 0
    shell.collapse_rail()
    assert shell.rail_reopen_button.isVisible()
    shell.rail_reopen_button.click()
    assert not shell.stagebar.isVisible()
    if module == 'viewer':
        shell.collapse_right_panel()
        assert shell.right_show_button.isVisible()
        shell.right_show_button.click()
        assert not shell.stagebar.isVisible()
    window.set_module('fluoro')
    APP.processEvents()
    assert shell.stagebar.isVisible()
    assert shell.stagebar.isAncestorOf(window.workflow_ui.navigation)


@pytest.mark.parametrize('module',['qpcr','wb','fluoro','ihc','brightfield'])
def test_workflow_navigation_shares_row_with_zoom_without_duplicate_canvas_row(window,module):
    window.set_module(module)
    for _ in range(8):APP.processEvents()
    shell=window.v9_shell
    assert shell.stagebar.height() <= (42 if sys.platform in {'darwin', 'win32'} else 38)
    navigation=window.workflow_ui.navigation
    assert shell.stagebar.isAncestorOf(navigation)
    canvas = (window.preview_canvas if module=='qpcr' else window.wb_canvas if module=='wb' else
              window.fluoro_workspace.canvas if module=='fluoro' else getattr(window,module+'_workspace').canvas)
    controls=canvas.zoom_controls
    assert shell.stagebar.isAncestorOf(controls)
    assert abs(controls.mapTo(shell.stagebar,QPoint()).y()-navigation.mapTo(shell.stagebar,QPoint()).y())<=6
    assert all(b.isVisible() for b in window.workflow_ui.buttons[module])
    controls.fit_button.click()
    before=canvas._view_controller.zoom_factor
    controls.zoom_in_button.click()
    assert canvas._view_controller.zoom_factor>=before
    assert not canvas._view_controller.fit_mode
    shell.collapse_right_panel()
    assert shell.right_show_button.isVisible()
    assert shell.global_toolbar.isAncestorOf(shell.right_show_button)
    assert shell.stagebar.height() <= (42 if sys.platform in {'darwin', 'win32'} else 38)
    shell.right_show_button.click()
    assert not shell.is_right_panel_collapsed()
    window.set_module('viewer')
    for _ in range(8):APP.processEvents()
    assert controls.parentWidget() is canvas
    window.set_module(module)
    for _ in range(8):APP.processEvents()
    assert shell.stagebar.isAncestorOf(controls)


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_composer_commands_use_compact_rows_without_hiding_common_controls(window, language):
    set_language(language)
    window.set_module('composer')
    ws = window.composer_workspace
    ws.retranslate()
    for _ in range(8):
        APP.processEvents()
    for control in (ws.add_page_button, ws.delete_page_button,
                    ws.canvas.zoom_controls.contains_button, ws.controller.tools.bar.properties):
        assert control.isVisible()
    assert ws.canvas.mapTo(ws, QPoint()).y() <= 72
    controls = ws.canvas.zoom_controls
    assert controls.width() >= controls.minimumSizeHint().width()
    assert ws.rect().contains(ws.focus_button.mapTo(ws, ws.focus_button.rect().bottomRight()))
    for button in (ws.page_button,ws.assets_button):
        assert button.width() <= button.sizeHint().width() + 8
    from PyQt6.QtWidgets import QBoxLayout
    for width in (1280,1920,1280):
        window.setFixedSize(width,900)
        for _ in range(8):APP.processEvents()
        required=ws.page_controls.minimumSizeHint().width()+controls.minimumSizeHint().width()+4
        if ws.focus_button.parentWidget() is ws.page_controls:
            required-=ws.focus_button.sizeHint().width()+4
        available=ws.width()-ws.focus_button.sizeHint().width()-4
        expected=QBoxLayout.Direction.LeftToRight if required<=available else QBoxLayout.Direction.TopToBottom
        assert ws.command_layout.direction()==expected
        assert controls.width()>=controls.minimumSizeHint().width()


def test_composer_selection_options_keep_language_values_and_stay_on_toolbar(window):
    from core.utils.i18n import tr
    window.set_module('composer')
    ws=window.composer_workspace
    controls=ws.canvas.zoom_controls
    controls.contains_button.setChecked(True)
    set_language('en');ws.retranslate()
    assert controls.marquee_button.text()==tr('canvas_force_marquee')
    bar=ws.controller.tools.bar
    menu=bar.buttons['view'].menu()
    assert controls.contains_button.isVisible()
    for _ in range(2):
        bar.populate('view',menu)
        menu.popup(ws.mapToGlobal(QPoint(50,50)))
        APP.processEvents()
        assert controls.contains_button.isVisible()
        assert controls.contains_button.isChecked()
        menu.hide()
    controls.snap_button.setChecked(True)
    assert ws.canvas.view.object_interaction.snap_enabled


def test_composer_page_grid_and_guide_controls_remain_on_toolbar(window):
    window.set_module('composer')
    ws=window.composer_workspace
    APP.processEvents()
    assert ws.grid_combo.isVisible() and ws.guides.isVisible()
    assert ws.add_page_button.isVisible() and ws.delete_page_button.isVisible()
    ws.add_page_button.click();APP.processEvents()
    assert len(window.project.composer_figures)==2
    ws.delete_page_button.click();APP.processEvents()
    assert len(window.project.composer_figures)==1
    ws.guides.setChecked(False)
    assert not ws.canvas.guides_visible


def test_composer_bottom_hint_does_not_take_a_separate_canvas_row(window):
    window.set_module('composer')
    for _ in range(8):APP.processEvents()
    ws=window.composer_workspace
    bottom=ws.canvas.mapTo(ws,ws.canvas.rect().bottomRight()).y()
    assert ws.height()-bottom<=4
    ws.activate_tool('text')
    assert 'Shift' in window.statusBar().currentMessage()
    assert 'Ctrl' in window.status_label.toolTip()
    window.set_module('wb')
    ws.status_changed.emit('hidden Composer message')
    assert window.statusBar().currentMessage()!='hidden Composer message'

