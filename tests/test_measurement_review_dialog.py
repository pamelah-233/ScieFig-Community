from copy import deepcopy
from importlib.util import find_spec
import os

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox

from tests.test_measurement_review import fluorescence_record, square


@pytest.fixture
def app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    return QApplication.instance() or QApplication([])


def dialog(app, record=None, language='en'):
    assert find_spec('ui.dialogs.object_review_dialog') is not None, 'Object review dialog is missing'
    from ui.dialogs.object_review_dialog import ObjectReviewDialog
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor(35, 48, 62))
    review = ObjectReviewDialog(record or fluorescence_record(), image, language=language)
    review.show()
    app.processEvents()
    return review


def row_for(review, identifier):
    for row in range(review.table.rowCount()):
        if str(review.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) == str(identifier):
            return row
    raise AssertionError(f'No table row for object {identifier}')


def click_polygon(review, points):
    for x, y in points:
        QTest.mouseClick(review.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=review.canvas.mapFromScene(QPointF(x, y)))
    QTest.mouseClick(review.buttons['finish'], Qt.MouseButton.LeftButton)


def test_sortable_table_and_contour_selection_link_by_object_id(app):
    review = dialog(app)
    review.table.sortItems(2, Qt.SortOrder.AscendingOrder)
    review.table.selectRow(row_for(review, 9))
    app.processEvents()
    assert review.canvas.selected_object_ids() == [9]
    review.canvas.select_objects([4])
    app.processEvents()
    assert review.selected_object_ids() == [4]
    assert len(review.canvas.object_items) == 2


def test_actual_polygon_clicks_add_object_then_undo_redo_and_apply(app):
    original = fluorescence_record()
    before = deepcopy(original)
    review = dialog(app, original)
    QTest.mouseClick(review.buttons['add'], Qt.MouseButton.LeftButton)
    click_polygon(review, square(20, 10, 3))
    assert len(review.session.objects) == 3
    identifier = review.selected_object_ids()[0]
    measured = review.session.object(identifier)
    assert measured['area_px'] == 16
    assert review.table.rowCount() == 3
    assert review.apply_button.isEnabled()
    QTest.mouseClick(review.buttons['undo'], Qt.MouseButton.LeftButton)
    assert review.table.rowCount() == 2
    QTest.mouseClick(review.buttons['redo'], Qt.MouseButton.LeftButton)
    assert review.session.object(identifier) == measured
    assert original == before
    QTest.mouseClick(review.apply_button, Qt.MouseButton.LeftButton)
    assert review.result() == review.DialogCode.Accepted
    assert review.applied_record['ai_object_count'] == 3
    assert original == before


def test_cancel_after_delete_leaves_record_untouched(app):
    original = fluorescence_record()
    before = deepcopy(original)
    review = dialog(app, original)
    review.table.selectRow(row_for(review, 4))
    QTest.mouseClick(review.buttons['delete'], Qt.MouseButton.LeftButton)
    assert len(review.session.objects) == 1
    QTest.mouseClick(review.button_box.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    assert review.applied_record is None
    assert review.result() == review.DialogCode.Rejected
    assert original == before


def test_cancel_button_closes_even_with_unfinished_drawing(app):
    review = dialog(app)
    QTest.mouseClick(review.buttons['add'], Qt.MouseButton.LeftButton)
    QTest.mouseClick(review.canvas.viewport(), Qt.MouseButton.LeftButton,
                     pos=review.canvas.mapFromScene(QPointF(20, 10)))
    QTest.mouseClick(review.button_box.button(QDialogButtonBox.StandardButton.Cancel), Qt.MouseButton.LeftButton)
    assert not review.isVisible()
    assert review.applied_record is None


def test_overlay_outline_remains_visible_over_dark_microscopy_pixels(app):
    review = dialog(app)
    color = review.canvas.object_items['4'].pen().color()
    assert color.lightnessF() >= .6


def test_actual_replace_split_and_merge_buttons_update_live_table(app):
    review = dialog(app)
    review.table.selectRow(row_for(review, 4))
    QTest.mouseClick(review.buttons['replace'], Qt.MouseButton.LeftButton)
    click_polygon(review, square(2, 3, 6))
    assert review.session.object(4)['area_px'] == 49
    QTest.mouseClick(review.buttons['split'], Qt.MouseButton.LeftButton)
    for x, y in ((5, 1), (5, 15)):
        QTest.mouseClick(review.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=review.canvas.mapFromScene(QPointF(x, y)))
    children = review.selected_object_ids()
    assert len(children) == 2
    assert review.table.rowCount() == 3
    QTest.mouseClick(review.buttons['merge'], Qt.MouseButton.LeftButton)
    assert review.table.rowCount() == 2
    assert review.session.object(review.selected_object_ids()[0])['area_px'] == 49


def test_invalid_drawing_reports_error_without_losing_work(app):
    review = dialog(app)
    review.table.selectRow(row_for(review, 4))
    QTest.mouseClick(review.buttons['split'], Qt.MouseButton.LeftButton)
    for x, y in ((25, 1), (25, 20)):
        QTest.mouseClick(review.canvas.viewport(), Qt.MouseButton.LeftButton,
                         pos=review.canvas.mapFromScene(QPointF(x, y)))
    assert 'cross' in review.status_label.text().lower()
    assert review.table.rowCount() == 2
    assert not review.apply_button.isEnabled()


def test_chinese_labels_missing_geometry_warning_and_keyboard_cancel_draft(app):
    record = {'type': 'spot_count', 'count_uuid': 'spots', 'detected_spots': [{'centroid_x': 4., 'centroid_y': 5.}]}
    review = dialog(app, record, language='zh')
    assert '对象复核' in review.windowTitle()
    assert review.buttons['add'].text() == '添加多边形'
    assert '轮廓不可用' in review.summary_label.text()
    review.table.selectRow(0)
    assert not review.buttons['split'].isEnabled()
    QTest.mouseClick(review.buttons['add'], Qt.MouseButton.LeftButton)
    QTest.keyClick(review.canvas, Qt.Key.Key_Escape)
    assert review.isVisible()
    assert review.canvas.mode == 'select'
    QTest.keyClick(review, Qt.Key.Key_Escape)
    assert not review.isVisible()


def test_dialog_grab_contains_source_and_renders_at_desktop_sizes(app, tmp_path):
    review = dialog(app)
    for width in (800, 1280):
        review.resize(width, 700)
        app.processEvents()
        rendered = review.grab().toImage()
        assert not rendered.isNull()
        assert review.canvas.viewport().width() > 150
        assert review.table.viewport().width() > 150
    assert rendered.save(str(tmp_path / 'object-review-dialog.png'))


def test_factory_reports_aggregate_limitation_before_loading_source(app):
    from core.models import IHCPanel
    assert find_spec('ui.dialogs.object_review_dialog') is not None, 'Object review dialog is missing'
    from ui.dialogs.object_review_dialog import ObjectReviewDialog
    with pytest.raises(ValueError, match='aggregate'):
        ObjectReviewDialog.from_measurement(IHCPanel(panel_id='IHC'), {'results': {'object_count': 25}})
