from pathlib import Path

import pytest
from PyQt6.QtGui import QFont

from core.utils import i18n
from main import load_qss


def test_brightfield_task_page_survives_module_refresh_without_showing_old_page(empty_window):
    from PyQt6.QtTest import QTest
    w=empty_window
    w.set_module('brightfield');w.workflow_ui.buttons['brightfield'][2].click()
    deck=w.measurement_tasks['brightfield']
    for width in (240,500,300):
        w.v9_shell.set_right_panel_width(width)
        for task in ('cells','fiber','he','central','myotube','area','fiber'):
            deck.select(task);w.set_module('viewer');w.set_module('brightfield');QTest.qWait(20)
            assert deck.stack.currentWidget() is deck.pages[task]
            assert [deck.stack.widget(i) for i in range(deck.stack.count()) if deck.stack.widget(i).isVisible()]==[deck.pages[task]],task


def test_brightfield_measurement_uses_same_content_width_as_fluorescence(empty_window):
    from PyQt6.QtTest import QTest
    w=empty_window
    for width in (240,300,500):
        widths={}
        for module,task in (('fluoro','dapi'),('brightfield','he'),('brightfield','cells')):
            w.set_module(module);w.workflow_ui.buttons[module][2].click()
            w.v9_shell.set_right_panel_width(width);deck=w.measurement_tasks[module];deck.select(task)
            QTest.qWait(30)
            field=deck.controllers[task].controls.method if task in deck.controllers else w.data_panel.brightfield_cellpose_strategy_combo
            widths[(module,task)]=field.width()
        assert max(widths.values())-min(widths.values())<=8,widths


def test_preset_actions_remain_readable_when_sidebar_resizes(empty_window):
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from ui.widgets.workflow_presentation import WorkflowDisclosure
    w=empty_window
    for language in ('zh','en'):
        w.switch_language(language)
        for module,task in (('fluoro','roi_intensity'),('brightfield','cells'),('ihc',None)):
            w.set_module(module);w.workflow_ui.buttons[module][2 if task else 1].click()
            if task:w.measurement_tasks[module].select(task)
            for section in w.workflow_ui.panels[module].findChildren(WorkflowDisclosure):
                if section.toggle.isVisible():section.setExpanded(True)
            bar=w.analysis_state.bars['fluorescence' if module=='fluoro' else module]
            for width in (240,500,240):
                w.v9_shell.set_right_panel_width(width);QTest.qWait(30)
                for _ in range(5):QApplication.processEvents()
                for button in (bar.apply_button,bar.save_button,bar.manage_button):
                    assert button.width()>=button.fontMetrics().horizontalAdvance(button.text()),(language,module,button.text(),button.width())
                assert bar.preset_combo.geometry().bottom()<bar.apply_button.mapTo(bar,bar.apply_button.rect().topLeft()).y(),(language,module,width)
            w.v9_shell.set_right_panel_width(500);QTest.qWait(20)


def test_plain_action_row_reflows_and_retains_click_connections(qt_application):
    from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton
    from PyQt6.QtTest import QTest
    from ui.widgets.responsive_action_grid import adapt_action_rows,ResponsiveActionGrid
    host=QWidget();column=QVBoxLayout(host);row=QHBoxLayout();column.addLayout(row)
    clicks=[]
    for text in ('Apply current settings','Save named preset'):
        button=QPushButton(text);button.clicked.connect(lambda checked=False,value=text:clicks.append(value));row.addWidget(button)
    adapt_action_rows(host);host.show()
    grid=host.findChild(ResponsiveActionGrid).layout()
    for width in (220,600,220):
        host.resize(width,200);QTest.qWait(20)
        assert grid.getItemPosition(1)[:2]==((1,0) if width==220 else (0,1))
        first,second=(grid.itemAt(i).widget() for i in range(2))
        assert not first.geometry().intersects(second.geometry())
        first.click()
    assert clicks==['Apply current settings']*3
    host.close()


@pytest.fixture
def empty_window(qt_application, desktop_screen):
    from ui.main_window import MainWindow

    previous_style = qt_application.styleSheet()
    previous_language = i18n.get_language()
    resources = Path(__file__).resolve().parents[1] / 'resources'
    qt_application.setStyleSheet(load_qss(resources))
    window = MainWindow(resources)
    window.project_recovery.timer.stop()
    window.project_recovery._alive = False
    window._confirm_destructive_action = lambda *args: True
    window.v9_shell.model_device_controller.shutdown()
    window.show()
    yield window
    window.close()
    i18n.set_language(previous_language)
    qt_application.setStyleSheet(previous_style)


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_module_empty_canvases_have_localized_titles_and_specific_instructions(empty_window, language):
    w = empty_window
    w.switch_language(language)
    mismatches = []
    for module in ('qpcr', 'ihc', 'brightfield', 'viewer', 'wb', 'fluoro'):
        w.set_module(module)
        expected_title = i18n.tr(f'module_{module}_canvas')
        body_key = {'wb': 'wb_canvas_import', 'fluoro': 'fluoro_canvas_import_hint', 'viewer': 'viewer_empty'}.get(module, f'canvas_{module}_empty')
        expected_body = i18n.tr(body_key)
        if module in ('wb', 'fluoro'):
            canvas = w.wb_canvas if module == 'wb' else w.fluoro_workspace.canvas
            hints = [item for item in canvas.scene.items() if hasattr(item, 'toPlainText')]
            actual = [(getattr(item, 'title', lambda: '')(), item.toPlainText()) for item in hints]
            if (expected_title, expected_body) not in actual:
                mismatches.append((module, actual))
        else:
            canvas = w.preview_canvas if module == 'qpcr' else (
                w.image_browser_workspace.canvas if module == 'viewer' else getattr(w, module + '_workspace').canvas)
            if (canvas.title.text(), canvas.body.text(), canvas.body.isHidden()) != (expected_title, expected_body, False):
                mismatches.append((module, canvas.title.text(), canvas.body.text(), canvas.body.isHidden()))
    assert not mismatches, mismatches

    # Translate in place, including inactive canvases; never reset titles to qPCR.
    other = 'en' if language == 'zh' else 'zh'
    w.switch_language(other)
    for module in ('ihc', 'brightfield'):
        canvas = getattr(w, module + '_workspace').canvas
        assert canvas.title.text() == i18n.tr(f'module_{module}_canvas')
        assert canvas.body.text() == i18n.tr(f'canvas_{module}_empty')


def test_qpcr_title_preserves_lowercase_q_and_layout_hint_translates_in_place(empty_window, qt_application):
    w = empty_window
    for language in ('zh', 'en', 'zh'):
        w.switch_language(language)
        w.set_module('qpcr')
        qt_application.processEvents()
        title = w.v9_shell.right_panel_title
        assert title.text().startswith('qPCR')
        assert title.font().capitalization() == QFont.Capitalization.MixedCase
        w.qpcr_layout_workspace.canvas.refresh()
        hints = [item for item in w.qpcr_layout_workspace.canvas.scene.items() if hasattr(item, 'toPlainText')]
        assert any(item.toPlainText() == i18n.tr('canvas_qpcr_layout_empty') for item in hints)
        w.switch_language('en' if language == 'zh' else 'zh')
        assert any(item.toPlainText() == i18n.tr('canvas_qpcr_layout_empty') for item in hints)


@pytest.mark.parametrize('language', ['zh', 'en'])
def test_loaded_channel_forms_fit_narrow_sidebar_and_scroll_to_last_control(empty_window, qt_application, tmp_path, language):
    import numpy as np
    import tifffile
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QLabel, QAbstractButton, QAbstractSpinBox, QScrollArea, QWidget

    w = empty_window
    path = tmp_path / 'three-channels.tif'
    tifffile.imwrite(path, np.arange(3*48*64, dtype=np.uint16).reshape(3,48,64),
                     metadata={'axes': 'CYX'}, photometric='minisblack')
    panel = w._active_fluoro_panel()
    panel.import_image(path)
    w.switch_language(language)
    w.set_module('fluoro')
    ws = w.fluoro_workspace
    ws.set_step(1)
    w.resize(1500, 900)
    failures = []
    for index, width in enumerate((640, 300, 240, 420, 240, 640, 300)):
        ws.set_step(2)
        w.measurement_tasks['fluoro'].select(('dapi', 'myotube', 'ai_assisted')[index % 3])
        w.set_module('brightfield')
        w.v9_shell.set_right_panel_width(width)
        w.set_module('fluoro')
        ws.set_step(1)
        for _ in range(12):
            qt_application.processEvents()
        page = ws.context_stack.currentWidget()
        for group in ws._display_processing_controls:
            group.strength.setValue(index * 5)
            group.background.setChecked(index % 2 == 0)
            controls = [child for child in group.findChildren(QWidget) if child.parentWidget() is group
                        and isinstance(child, (QLabel, QAbstractButton, QAbstractSpinBox)) and not child.isHidden()]
            for index, first in enumerate(controls):
                if not group.rect().contains(first.geometry()):
                    failures.append((width, 'outside group', type(first).__name__, first.geometry(), group.size()))
                if isinstance(first, QAbstractButton) and first.text():
                    if first.fontMetrics().horizontalAdvance(first.text()) > first.contentsRect().width():
                        failures.append((width, 'clipped label', first.text(), first.size()))
                for second in controls[index+1:]:
                    if first.geometry().intersects(second.geometry()):
                        failures.append((width, 'overlap', first.geometry(), second.geometry()))
            bottom = group.mapTo(page, group.rect().bottomRight()).y()
            if bottom >= page.height():
                failures.append((width, 'clipped page', bottom, page.height(), ws.context_stack.heightForWidth(ws.context_stack.width())))
        scroll = ws.workflow_panel.findChild(QScrollArea)
        last = ws._display_processing_controls[-1].reset_button
        scroll.ensureWidgetVisible(last)
        for _ in range(4):
            qt_application.processEvents()
        if not scroll.viewport().rect().contains(last.mapTo(scroll.viewport(), last.rect().center())):
            failures.append((width, 'unreachable last channel', scroll.verticalScrollBar().value(), scroll.verticalScrollBar().maximum()))
    assert not failures, failures[:25]


def test_compact_sidebar_tracks_late_nested_content_height(qt_application):
    from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea
    from ui.widgets.current_page_stack import CurrentPageStackedWidget

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    column = QVBoxLayout(body)
    stack = CurrentPageStackedWidget(compact=True)
    page = QWidget()
    page_layout = QVBoxLayout(page)
    nested = QWidget()
    nested_layout = QVBoxLayout(nested)
    label = QLabel('Parameters')
    nested_layout.addWidget(label)
    page_layout.addWidget(nested)
    stack.addWidget(page)
    column.addWidget(stack)
    column.addStretch()
    scroll.setWidget(body)
    scroll.resize(280, 400)
    scroll.show()
    for required in (800, 120, 1100, 200):
        label.setMinimumHeight(required)
        for _ in range(10):
            qt_application.processEvents()
        assert stack.height() >= page.minimumSizeHint().height()
        assert label.mapTo(stack, label.rect().bottomRight()).y() < stack.height()
        assert stack.height() <= page.minimumSizeHint().height() + 24
    scroll.close()


def test_scrolled_nuclear_workbench_switches_without_blank_gap(empty_window, qt_application, tmp_path):
    import numpy as np
    import tifffile
    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest
    from ui.widgets.workflow_presentation import WorkflowDisclosure

    w = empty_window
    path = tmp_path / 'nuclear.tif'
    tifffile.imwrite(path, np.ones((2,64,80),np.uint8),metadata={'axes':'CYX'},photometric='minisblack')
    panel = w._active_fluoro_panel()
    panel.import_image(path)
    w.set_module('fluoro')
    ws = w.fluoro_workspace
    ws.set_step(2)
    deck = w.measurement_tasks['fluoro']
    w.resize(1450,850)
    for language in ('zh','en'):
        w.switch_language(language)
        for width in (360,240,500):
            w.v9_shell.set_right_panel_width(width)
            for task in ('fusion','count_ratio','ai_assisted','myotube','roi_intensity'):
                deck.select('dapi')
                for section in deck.stack.currentWidget().findChildren(WorkflowDisclosure):
                    section.setExpanded(True)
                ws.measure_task_combo.buttons['dapi'].click()
                deck.controllers['dapi'].controls.classify_signal.setChecked(True)
                QTest.qWait(5)
                for _ in range(5):qt_application.processEvents()
                deck.scroll.verticalScrollBar().setValue(deck.scroll.verticalScrollBar().maximum())
                qt_application.processEvents()
                offset=deck.scroll.verticalScrollBar().value()
                deck.selector.buttons['dapi'].click()
                for _ in range(5):qt_application.processEvents()
                assert abs(deck.scroll.verticalScrollBar().value()-offset)<=1,(language,width,task,offset,deck.scroll.verticalScrollBar().maximum())
                deck.select(task)
                QTest.qWait(10)
                for _ in range(5):qt_application.processEvents()
                current=deck.stack.currentWidget()
                assert current is deck.pages[deck.current_task()]
                assert current.width()==deck.stack.contentsRect().width()
                assert all(not page.isVisibleTo(deck.stack) for page in set(deck.pages.values()) if page is not current)
                gap = deck.scroll.mapTo(deck.panel,QPoint()).y() - deck.selector_wrap.geometry().bottom()
                top = deck.task_title.mapTo(deck.scroll.viewport(),QPoint()).y()
                chain=[];node=deck.task_title
                while node is not None and node is not deck.scroll:
                    chain.append((type(node).__name__,node.objectName(),node.geometry().getRect(),node.minimumHeight(),node.maximumHeight()))
                    node=node.parentWidget()
                assert gap <= 16,(language,width,task,'external gap',gap)
                assert 0 <= top <= 80,f'{language} {width} {task} top={top} scroll={deck.scroll.verticalScrollBar().value()} chain={chain}'
                assert deck.scroll.viewport().height() >= 80,f'{language} {width} {task} viewport={deck.scroll.viewport().size()} selector={deck.selector.size()} buttons={[(x.text(),x.sizeHint().width()) for x in deck.selector.buttons.values()]}'
