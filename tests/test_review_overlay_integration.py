import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from PyQt6.QtWidgets import QApplication, QGraphicsScene
from PyQt6.QtGui import QImage, QPixmap, QColor


def test_brightfield_overlay_shows_all_reviewed_components():
    from core.image_analysis.bright_round_cells import render_bright_round_cell_overlay
    first = [[5,40],[15,40],[15,50],[5,50]]
    second = [[60,60],[70,60],[70,70],[60,70]]
    row = dict(id=1, contour_px=first, review_contours_px=[first,second], equivalent_diameter_um=10)
    overlay = render_bright_round_cell_overlay(np.zeros((100,100,3),np.uint8), {'results':[row]})
    assert overlay[60,65].max() > 0


def test_fluoro_overlay_shows_all_reviewed_components():
    from ui.widgets.fluoro_canvas import FluorescenceCanvas, ImageCoordinateMapper
    app = QApplication.instance() or QApplication([])
    canvas = FluorescenceCanvas()
    image = QImage(100,100,QImage.Format.Format_RGB32)
    image.fill(0)
    item = canvas.scene.addPixmap(QPixmap.fromImage(image))
    mapper = ImageCoordinateMapper(item,image_shape=(100,100))
    paths = [[[5,5],[15,5],[15,15],[5,15]],[[60,60],[70,60],[70,70],[60,70]]]
    row = dict(review_contours_px=paths,contour_points_px=[dict(x=x,y=y) for x,y in paths[0]])
    overlay = canvas._ai_object_overlay_item(mapper,row,QColor('yellow'))
    assert overlay.boundingRect().right() >= 70
    canvas.close()


def test_uncalibrated_reviewed_cell_table_does_not_invent_physical_values():
    from core.measurement_review import ObjectReviewSession
    from ui.panels.placeholder_panel import DataInputPanel
    from ui.widgets.measurement_excel_export import EXCEL_VALUE_ROLE
    app = QApplication.instance() or QApplication([])
    session = ObjectReviewSession({'measurement_uuid':'m', 'results':[], 'summary':{},'image_uid':'i'}, (100,100))
    session.add_polygon([[10,10],[20,10],[20,20],[10,20]])
    record = session.updated_record()
    panel = DataInputPanel()
    panel.populate_bright_round_cell_results(record)
    assert panel.brightfield_bright_round_cell_results_table.item(0,2).data(EXCEL_VALUE_ROLE) is None
    assert 'bright_round_cell_shape_reviewed' not in panel.brightfield_bright_round_cell_results_table.item(0,1).text()
    panel.close()
