"""Sidebar tables fit their form; wide tables drag continuously by pixels."""
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (QApplication, QAbstractItemView, QHeaderView, QStyle,
                            QStyleOptionSlider, QStyleOptionViewItem, QTableWidget,
                            QTableWidgetItem, QToolTip)

from core.utils.i18n import set_language
from main import load_qss
from ui.main_window import MainWindow
from ui.platform_support import configure_application

pytestmark = pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='shared studio table presentation')
APP = QApplication.instance() or QApplication([])


@pytest.fixture
def window(desktop_screen):
    existing_tables = {widget for widget in APP.allWidgets() if isinstance(widget, QTableWidget)}
    configure_application(APP)
    previous = APP.styleSheet()
    APP.setStyleSheet(load_qss(Path(__file__).resolve().parents[1] / 'resources'))
    w = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    w._qa_existing_tables = existing_tables
    w.project_recovery.timer.stop()
    w.project_recovery._alive = False
    w._confirm_destructive_action = lambda *args: True
    w.resize(1024, 740)
    w.show()
    QTest.qWait(150)
    yield w
    w.close()
    APP.processEvents()
    APP.setStyleSheet(previous)
    set_language('en')


def test_late_created_wide_table_tracks_scrollbar_in_pixels(window):
    table = QTableWidget(30, 10)
    table.resize(360, 240)
    for col in range(10):
        table.setColumnWidth(col, 120)
        table.setItem(0, col, QTableWidgetItem(f'column {col}'))
    table.show()
    QTest.qWait(50)
    assert table.horizontalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    bar = table.horizontalScrollBar()
    assert bar.hasTracking() and bar.maximum() > 500
    bar.setValue(7)
    assert table.horizontalHeader().offset() == 7
    bar.setValue(0)
    option = QStyleOptionSlider()
    option.initFrom(bar)
    option.orientation = Qt.Orientation.Horizontal
    option.minimum = bar.minimum()
    option.maximum = bar.maximum()
    option.sliderPosition = bar.sliderPosition()
    option.sliderValue = bar.value()
    option.pageStep = bar.pageStep()
    option.singleStep = bar.singleStep()
    handle = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, QStyle.SubControl.SC_ScrollBarSlider, bar)
    start = handle.center()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
    values = []
    for dx in range(2, 62, 2):
        QTest.mouseMove(bar, start + QPoint(dx, 0), 2)
        APP.processEvents()
        values.append(bar.value())
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=start + QPoint(60, 0))
    assert values == sorted(values)
    assert len(set(values)) > 15
    assert any(value % 120 for value in values)
    table.close()


def test_short_brightfield_forms_stay_at_top_and_task_titles_do_not_repeat(window):
    from PyQt6.QtWidgets import QGroupBox
    w=window;w.resize(1440,1040);w.v9_shell.set_right_panel_width(360)
    for language in ('zh','en','zh'):
        w.switch_language(language)
        w.set_module('brightfield');w.set_brightfield_workflow_step(2)
        deck=w.measurement_tasks['brightfield']
        for task in ('central','he','myotube','cells','area','fiber','central'):
            deck.select(task);QTest.qWait(25)
            host=w.data_panel.brightfield_context_stack.widget(2)
            assert deck.task_title.y()<=8
            assert not any(host.layout().itemAt(i).spacerItem() for i in range(host.layout().count()))
            context=w.data_panel.brightfield_context_stack.parentWidget()
            if task in ('he','central','myotube'):
                assert w.data_panel.brightfield_context_stack.y()<=context.layout().contentsMargins().top()+8,(language,task,w.data_panel.brightfield_context_stack.geometry())
            workflow=w.brightfield_workspace.workflow_group
            assert workflow.parentWidget().layout().stretch(workflow.parentWidget().layout().indexOf(workflow))==0
        w.set_module('fluoro');w.fluoro_workspace.set_step(2)
        w.measurement_tasks['fluoro'].select('spot_count');QTest.qWait(25)
        page=w.fluoro_workspace.spot_count_params_panel
        groups=page.findChildren(QGroupBox,options=Qt.FindChildOption.FindDirectChildrenOnly)
        assert groups and all(not group.title() for group in groups)


def test_model_choices_precede_tuning_controls(window):
    from PyQt6.QtWidgets import QFormLayout
    w=window;w.set_module('brightfield');w.set_brightfield_workflow_step(2)
    deck=w.measurement_tasks['brightfield'];d=w.data_panel
    for task,choice in (('he',w.nuclei_controllers['brightfield'].controls.method),
                        ('myotube',w.myotube_controllers['brightfield'].controls.method),
                        ('cells',d.brightfield_cellpose_strategy_combo),('fiber',d.brightfield_he_fiber_method_combo)):
        deck.select(task);QTest.qWait(25)
        page=deck.pages[task]
        assert choice.isVisible()
        choice_top=choice.mapTo(page,QPoint()).y()
        for field in page.findChildren(QFormLayout):
            for row in range(field.rowCount()):
                item=field.itemAt(row,QFormLayout.ItemRole.FieldRole)
                if item and item.widget() and item.widget().isVisibleTo(page):
                    assert item.widget().mapTo(page,QPoint()).y()>=choice_top
        assert deck.shared.y()>deck.stack.y()
    w.set_module('fluoro');w.fluoro_workspace.set_step(2)
    f=w.fluoro_workspace;deck=w.measurement_tasks['fluoro']
    for task,choice in (('dapi',w.nuclei_controllers['fluoro'].controls.method),
                        ('spot_count',f.count_algorithm_combo),('ai_assisted',f.ai_backend_combo)):
        deck.select(task);QTest.qWait(25)
        page=deck.pages[task]
        top=choice.mapTo(page,QPoint()).y()
        for field in (page.findChildren(QFormLayout)):
            for row in range(field.rowCount()):
                item=field.itemAt(row,QFormLayout.ItemRole.FieldRole)
                if item and item.widget() and item.widget().isVisibleTo(page):
                    assert item.widget().mapTo(page,QPoint()).y()>=top


def test_model_status_is_live_and_no_extra_load_button_remains(window):
    from PyQt6.QtWidgets import QPushButton
    from ui.widgets.model_selection_hint import ModelSelectionHint
    w=window;d=w.data_panel;f=w.fluoro_workspace
    pairs=[(f.count_algorithm_combo,f.count_model_hint),
           (f.ai_backend_combo,f.ai_model_hint),
           (d.brightfield_he_fiber_method_combo,d.brightfield_he_fiber_model_hint),
           (d.brightfield_cellpose_strategy_combo,d.brightfield_cellpose_model_hint)]
    for module in ('brightfield','fluoro'):
        pairs.extend([(w.nuclei_controllers[module].controls.method,w.nuclei_controllers[module].controls.model_hint),
                      (w.myotube_controllers[module].controls.method,w.myotube_controllers[module].controls.model_hint)])
    for language in ('zh','en'):
        w.switch_language(language)
        for combo,hint in pairs:
            for index in range(combo.count()):
                combo.setCurrentIndex(index);QTest.qWait(5)
                assert combo.currentText() in hint.text()
                assert hint.objectName()=='DimLabel' and hint.wordWrap() and hint.toolTip()
                assert ('下次分析' if language=='zh' else 'Next analysis') in hint.text()
        for module in ('brightfield','fluoro'):
            w.set_module(module);w.workflow_ui.buttons[module][2].click()
            deck=w.measurement_tasks[module]
            for task in deck.pages:
                deck.select(task);QTest.qWait(5)
                for button in deck.panel.findChildren(QPushButton):
                    if button.isVisibleTo(deck.panel):
                        assert button.property('i18nKey') not in ('nuclei_load_recipe','membrane_load_reference','he_fiber_reference_load')
                for hint in deck.pages[task].findChildren(ModelSelectionHint):
                    if hint.isVisibleTo(deck.pages[task]):assert hint.height()>=hint.heightForWidth(hint.width())


def test_wb_interface_and_canvas_hints_follow_language_without_editing_user_text(window):
    from PyQt6.QtWidgets import QLabel,QGroupBox
    from core.utils.i18n import tr,set_language
    w=window;w.set_module('wb');canvas=w.wb_canvas
    canvas.render()
    user_item=canvas.scene.addText('My protein / custom English label')
    position=user_item.pos()
    for language in ('zh','en','zh'):
        w.switch_language(language);QTest.qWait(20)
        assert user_item.toPlainText()=='My protein / custom English label'
        assert user_item.pos()==position
        hints=[item for item in canvas.scene.items() if getattr(item,'_wb_ui_i18n_key',None)]
        assert any(item.toPlainText()==tr('wb_canvas_import') for item in hints)
        for step in range(7):
            w.workflow_ui.buttons['wb'][step].click();QTest.qWait(15)
            page=w.data_panel.wb_context_stack.widget(step)
            for group in page.findChildren(QGroupBox):
                key=group.property('i18nTitleKey')
                if key:assert group.title()==tr(key)
                assert group.title() not in ('Image Properties','Strip Metadata','Integrity') if language=='zh' else True
            keys=('wb_crop_instruction','wb_anchor_instruction','wb_stats_instruction')
            for label in page.findChildren(QLabel):
                if label.property('i18nKey') in keys:assert label.text()==tr(label.property('i18nKey'))
        w.workflow_ui.buttons['wb'][0].click();QTest.qWait(15)
        # A new empty canvas gets the current language too, not just an item
        # that happened to be present when the language was switched.
        canvas.render()
        assert any(getattr(item,'_wb_ui_i18n_key',None)=='wb_canvas_import' and item.toPlainText()==tr('wb_canvas_import') for item in canvas.scene.items())
        user_item=canvas.scene.addText('My protein / custom English label');position=user_item.pos()


def test_three_column_tables_fit_without_losing_values_or_selection(window):
    data = window.data_panel
    targets = [('qpcr', 1, data.qpcr_group_table), ('wb', 4, data.wb_stats_group_table),
               ('ihc', 2, data.ihc_stats_group_table), ('brightfield', 3, data.brightfield_stats_group_table),
               ('fluoro', 3, window.fluoro_workspace.fluoro_stats_group_table), ('wb', 6, data.wb_export_item_table)]
    for language in ['en', 'zh']:
        window.switch_language(language)
        for width in [240, 300]:
            window.v9_shell.set_right_panel_width(width)
            for module, step, table in targets:
                window.set_module(module)
                window.workflow_ui.buttons[module][step].click()
                QTest.qWait(30)
                assert table.isVisible()
                assert table.horizontalScrollBar().maximum() == 0, (language, width, module, table.objectName(), table.viewport().width(), [table.columnWidth(i) for i in range(3)])
                assert sum(table.columnWidth(i) for i in range(3)) <= table.viewport().width()
    data.populate_qpcr_group_table([{'sample': 'Ctrl_01', 'group': 'Ctrl'}, {'sample': 'Treat_01', 'group': 'Treat'}])
    data.qpcr_group_table.item(0, 1).setText('control')
    data.qpcr_group_table.cellWidget(1, 2).setChecked(False)
    window.v9_shell.set_right_panel_width(240)
    QTest.qWait(30)
    window.v9_shell.set_right_panel_width(300)
    QTest.qWait(30)
    assert data.qpcr_group_table.item(0, 1).text() == 'control'
    assert not data.qpcr_group_table.cellWidget(1, 2).isChecked()
    table = data.wb_export_item_table
    assert table.item(0, 1).data(Qt.ItemDataRole.UserRole) == 'wb_layout'
    data.set_wb_export_item_checked('wb_layout', False)
    assert not table.cellWidget(0, 0).isChecked()
    parent = table.parentWidget()
    assert table.x() == data.wb_export_to_composer_button.x() == 0
    assert parent.title() == '导出项目'
    assert 96 <= table.height() <= 184


def test_all_workbench_tables_readable_in_both_languages(window):
    # Inactive workflow pages are kept detached until their module is opened.
    tables = [widget for widget in APP.allWidgets()
              if isinstance(widget, QTableWidget) and widget not in window._qa_existing_tables]
    assert len(tables) >= 25
    for table in tables:
        table.ensurePolished()
    for language in ('en', 'zh'):
        window.switch_language(language)
        QTest.qWait(50)
        for table in tables:
            assert hasattr(table, '_readable_presentation'), table.objectName()
            assert table.alternatingRowColors()
            assert table.verticalHeader().defaultSectionSize() >= 30
            assert table.horizontalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
            if table.columnCount() > 3:
                for col in range(table.columnCount()):
                    if table.isColumnHidden(col):
                        continue
                    assert table.columnWidth(col) >= 96, (language, table.objectName(), col)
                    assert table.horizontalHeader().sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
    # Existing specialized widths for method/parameters/image remain usable.
    assert window.fluoro_workspace.count_table.columnWidth(6) >= 300


def test_late_dialog_table_preserves_editing_roles_and_full_text(window):
    from ui.dialogs.statistics_pairing_dialog import StatisticsPairingDialog
    from ui.dialogs.object_review_dialog import ObjectReviewDialog
    from tests.test_measurement_review import fluorescence_record
    from PyQt6.QtGui import QImage

    rows = [{'sample_key': 'Ctrl_01', 'sample_label': 'A long sample label for complete text inspection'}]
    dialog = StatisticsPairingDialog(rows, {'Ctrl_01': 'control'}, {}, window)
    dialog.show()
    review = ObjectReviewDialog(fluorescence_record(), QImage(40, 30, QImage.Format.Format_RGB32), language='en')
    review.show()
    QTest.qWait(50)
    table = dialog.table
    assert hasattr(table, '_readable_presentation')
    assert not table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable
    assert table.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable
    table.item(0, 2).setText('pair-A')
    assert list(dialog.pair_ids().values()) == ['pair-A']
    assert review.table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
    assert review.table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert hasattr(review.table, '_readable_presentation')
    dialog.close()
    review.close()

    table = QTableWidget(1, 4)
    table.setHorizontalHeaderLabels(['Sample with a long name', 'Value', 'Other', 'Metadata'])
    text = 'A lengthy sample <reference> & metadata preserved in full'
    table.setItem(0, 0, QTableWidgetItem(text))
    numeric = QTableWidgetItem('0.0000012300')
    numeric.setData(Qt.ItemDataRole.UserRole, {'source': 'exact'})
    table.setItem(0, 1, numeric)
    table.resize(320, 160)
    table.show()
    QTest.qWait(50)
    option = QStyleOptionViewItem()
    table.itemDelegate().initStyleOption(option, table.model().index(0, 1))
    assert option.displayAlignment & Qt.AlignmentFlag.AlignRight
    assert numeric.text() == '0.0000012300'
    assert numeric.data(Qt.ItemDataRole.UserRole) == {'source': 'exact'}
    assert numeric.data(Qt.ItemDataRole.TextAlignmentRole) is None
    table.resize(1100, 160)
    QTest.qWait(20)
    table.setHorizontalHeaderLabels(['Sample with a long name', 'Value', 'Other', 'Metadata'])
    QTest.qWait(20)
    table.resize(320, 160)
    QTest.qWait(20)
    assert table.columnWidth(3) <= 240
    index = table.model().index(0, 0)
    position = table.visualRect(index).center()
    event = QHelpEvent(QEvent.Type.ToolTip, position, table.viewport().mapToGlobal(position))
    assert APP.sendEvent(table.viewport(), event)
    assert 'A lengthy sample &lt;reference&gt; &amp; metadata preserved in full' in QToolTip.text()
    QToolTip.hideText()
    table.close()
