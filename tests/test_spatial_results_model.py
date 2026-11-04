from types import SimpleNamespace

from PyQt6.QtCore import QPersistentModelIndex, Qt
from PyQt6.QtWidgets import QTabWidget, QWidget

from ui.widgets.multiplex_spatial_results import SpatialResultsDialog, _SpatialResultModel


def test_spatial_model_sorts_numbers_and_preserves_selected_cell(qt_application):
    rows = [
        dict(object_id=10, phenotype='CD68+', distance=10.0),
        dict(object_id=2, phenotype='CD68+', distance=None),
        dict(object_id=1, phenotype='CD68+', distance=2.0),
    ]
    model = _SpatialResultModel(rows, {'CD68+': '#3ED995'}, None)
    selected = QPersistentModelIndex(model.index(0, 0))
    model.sort(0, Qt.SortOrder.AscendingOrder)
    assert [model.index(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(3)] == [1, 2, 10]
    assert selected.row() == 2
    assert selected.data(Qt.ItemDataRole.UserRole) == 10
    model.sort(2, Qt.SortOrder.DescendingOrder)
    assert [model.index(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(3)] == [10, 1, 2]
    assert selected.row() == 0
    assert [row['object_id'] for row in rows] == [10, 2, 1]


def test_spatial_model_keeps_formatting_colors_and_empty_results(qt_application):
    rows = [
        dict(object_id=1, phenotype='CD68+', include_in_statistics=True, values={'CD68+': 2}, distance=None),
        dict(object_id=2, phenotype='CD68+', include_in_statistics=False, values={}, distance=1.23456789),
    ]
    model = _SpatialResultModel(rows, {'CD68+': '#3ED995'}, None)
    assert model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole).color().name() == '#3ed995'
    assert model.index(1, 0).data(Qt.ItemDataRole.ForegroundRole).color().name() == '#777777'
    assert model.index(0, 3).data() == '{"CD68+": 2}'
    assert model.index(0, 4).data() == '—'
    assert model.index(1, 4).data() == '1.23457'
    assert not model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
    empty = _SpatialResultModel([], {}, None)
    assert empty.rowCount() == empty.columnCount() == 0
    empty.sort(0, Qt.SortOrder.AscendingOrder)


def test_spatial_dialog_loads_tabs_once_and_clicks_sorted_cell(qt_application, monkeypatch):
    import sys
    from ui.platform_support import configure_application
    configure_application(qt_application)
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda kind, error, tb: errors.append(error))
    cells = [dict(object_id=10, phenotype='CD68+', overlay_color='#3ED995', target_count_in_radius=1),
             dict(object_id=2, phenotype='CD68+', overlay_color='#3ED995', target_count_in_radius=None)]
    result = dict(summary={'length_unit': 'px'}, object_table=cells,
                  phenotypes=[dict(phenotype='CD68+', cells=2, fraction=1.0)], nearest_distances=[])
    loaded, selected = [], []

    def cell_rows(current):
        assert current is result
        loaded.append(True)
        return cells

    parent = QWidget()
    controller = SimpleNamespace(window=parent, current_result=lambda: result, summary_text=lambda current: 'Spatial results',
                                 group_rows=lambda: [], cell_rows=cell_rows, select_cell=selected.append)
    dialog = SpatialResultsDialog(controller)
    try:
        dialog.show()
        qt_application.processEvents()
        tabs = dialog.findChild(QTabWidget)
        assert tabs.count() == 5
        assert dialog.tables['spatial_phenotypes'].model().rowCount() == 1
        assert not loaded
        assert all(tabs.widget(index).model() is None for index in range(1, 5))
        tabs.setCurrentIndex(1)
        qt_application.processEvents()
        table = dialog.tables['spatial_cells']
        table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        table.clicked.emit(table.model().index(0, 0))
        assert selected == [2]
        tabs.setCurrentIndex(2)
        qt_application.processEvents()
        assert dialog.tables['spatial_neighbors'].model().rowCount() == 1
        tabs.setCurrentIndex(3)
        tabs.setCurrentIndex(4)
        tabs.setCurrentIndex(1)
        qt_application.processEvents()
        assert len(loaded) == 1
        assert all(tabs.widget(index).model() is not None for index in range(5))
        assert not errors
    finally:
        dialog.close()
        parent.close()


def test_table_style_handles_unloaded_views_and_preserves_widget_values(qt_application):
    from PyQt6.QtWidgets import QTableView, QTableWidget, QTableWidgetItem
    from ui.widgets.table_presentation import readable_table
    unloaded = QTableView()
    readable_table(unloaded)
    assert not hasattr(unloaded, '_readable_presentation')
    unloaded.setModel(_SpatialResultModel([dict(object_id=1, value=1.25)], {}, unloaded))
    readable_table(unloaded)
    unloaded._readable_presentation.size_columns()
    assert unloaded.model().index(0, 1).data() == '1.25'
    widget = QTableWidget(1, 4)
    widget.setHorizontalHeaderLabels(['Sample', 'Value', 'Other', 'Metadata'])
    value = QTableWidgetItem('0.0000012300')
    value.setData(Qt.ItemDataRole.UserRole, {'source': 'exact'})
    widget.setItem(0, 1, value)
    readable_table(widget)
    widget._readable_presentation.size_columns()
    assert value.text() == '0.0000012300'
    assert value.data(Qt.ItemDataRole.UserRole) == {'source': 'exact'}
    unloaded.close()
    widget.close()


def test_completed_workflow_refresh_allocates_wrapped_summary(qt_application):
    from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout
    from ui.widgets.current_page_stack import CurrentPageStackedWidget
    from ui.widgets.workflow_presentation import refresh_workflow_scroll_geometry
    window = QWidget()
    window.resize(280, 260)
    layout = QVBoxLayout(window)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    layout.addWidget(scroll)
    body = QWidget()
    body_layout = QVBoxLayout(body)
    stack = CurrentPageStackedWidget(compact=True)
    body_layout.addWidget(stack)
    page = QWidget()
    page_layout = QVBoxLayout(page)
    summary = QLabel('Ready')
    summary.setWordWrap(True)
    details = QLabel('Cell details')
    page_layout.addWidget(summary)
    page_layout.addWidget(details)
    stack.addWidget(page)
    scroll.setWidget(body)
    window.show()
    qt_application.processEvents()
    summary.setText('Long marker name and completed cell statistics. ' * 40)
    refresh_workflow_scroll_geometry(page)
    assert summary.height() >= summary.heightForWidth(summary.width())
    assert details.y() > summary.geometry().bottom()
    assert stack.height() >= page.heightForWidth(stack.width())
    window.close()


def test_measurement_selector_keeps_two_columns_in_narrow_sidebars(qt_application):
    from PyQt6.QtCore import QRect
    from ui.widgets.measurement_task_selector import MeasurementTaskSelector
    titles = ('Phenotypes and neighbors', 'Channel colocalization', '多标记表型与邻域', '通道信号共定位')
    selector = MeasurementTaskSelector()
    for index, title in enumerate(titles):
        selector.addItem(title, index)
    selector.show()
    try:
        for width in (200, 230, 300):
            selector.setFixedWidth(width)
            for _ in range(8): qt_application.processEvents()
            for index, button in selector.buttons.items():
                position = selector.grid.getItemPosition(selector.grid.indexOf(button))
                assert position[:2] == (index // 2, index % 2)
                available = max(1, button.width() - 24)
                required = button.fontMetrics().boundingRect(QRect(0, 0, available, 1000), Qt.TextFlag.TextWordWrap, button.text())
                assert button.height() >= required.height() + 16
            assert len({button.height() for button in selector.buttons.values()}) == 1
    finally:
        selector.close()
