from PyQt6.QtWidgets import QApplication
from core.models import Project,ExternalImagePanel
from ui.widgets.composer_canvas import ComposerCanvas
from ui.controllers.composer_edit_controller import ComposerEditController

_APP=QApplication.instance() or QApplication([])


def test_page_grid_restores_and_history_does_not_cross_pages():
    canvas=ComposerCanvas();canvas.set_project(Project(title='pages'))
    controller=ComposerEditController(canvas);controller.reset()
    first=canvas.current_figure().figure_uid
    canvas.set_layout_preset('2x3')
    canvas.add_page()
    second=canvas.current_figure().figure_uid
    canvas.set_layout_preset('2x2')
    canvas.set_active_page(first)
    assert canvas.layout_model.preset=='2x3'
    controller.add_annotation('text')
    controller.undo()
    assert canvas.current_figure().figure_uid==first
    assert not canvas.current_figure().annotations
    canvas.set_active_page(second)
    assert canvas.layout_model.preset=='2x2'
    canvas.close()


def test_real_qpcr_uppercase_error_mode_can_render_in_composer():
    from test_layout_chart_adapter import _qpcr_panel
    from core.chart_payload import qpcr_live_plot_payload
    payload=qpcr_live_plot_payload(_qpcr_panel())
    assert payload['error_mode']=='sem'


def test_zoom_keeps_pointer_scene_position_after_pending_layout_events():
    from PyQt6.QtCore import QPoint,QLineF
    from PyQt6.QtTest import QTest
    canvas=ComposerCanvas()
    canvas.set_project(Project(title='zoom'))
    canvas.resize(900,650)
    canvas.show()
    _APP.processEvents()
    QTest.qWait(100)
    point=QPoint(230,200)
    before=canvas.view.mapToScene(point)
    canvas.zoom_by(1.5,anchor=point)
    QTest.qWait(100)
    after=canvas.view.mapToScene(point)
    assert QLineF(before,after).length()<3
    canvas.close()


def test_free_canvas_can_place_more_assets_than_grid_cells():
    project=Project(title='free canvas')
    canvas=ComposerCanvas()
    canvas.set_project(project)
    for index in range(6):
        panel=ExternalImagePanel(panel_id=f'asset-{index}')
        project.add_panel(panel)
        canvas.place_panel(panel.panel_uuid)
    assert len(canvas.current_figure().panel_refs)==6
    assert len({ref.ref_uid for ref in canvas.current_figure().panel_refs})==6
    canvas.close()


def test_page_size_is_physical_mm_and_does_not_resize_existing_physical_objects():
    canvas=ComposerCanvas()
    canvas.set_project(Project(title='page'))
    controller=ComposerEditController(canvas)
    controller.reset()
    controller.set_page(210,297,'A4')
    ann=canvas.add_text_annotation('Label')
    before=(ann.x_mm,ann.y_mm)
    controller.set_page(297,210,'A4 landscape')
    assert canvas._export_page_size_mm()==(297,210)
    assert (ann.x_mm,ann.y_mm)==before
    canvas.close()


def test_physical_page_exports_have_requested_size(tmp_path):
    import xml.etree.ElementTree as ET
    from PyQt6.QtGui import QImage
    from PyQt6.QtPdf import QPdfDocument
    import pytest
    canvas=ComposerCanvas()
    canvas.set_project(Project(title='physical export'))
    controller=ComposerEditController(canvas)
    controller.reset()
    controller.set_page(50,30)
    canvas.export_figure(tmp_path/'page.png',dpi=300)
    image=QImage(str(tmp_path/'page.png'))
    assert (image.width(),image.height())==(591,354)
    canvas.export_figure(tmp_path/'page.pdf',dpi=300)
    pdf=QPdfDocument(None)
    pdf.load(str(tmp_path/'page.pdf'))
    # QPageSize quantizes custom PDF paper to integer points.
    assert pdf.pagePointSize(0).width()==pytest.approx(50/25.4*72,abs=.5)
    pdf.close()
    canvas.export_figure(tmp_path/'page.svg')
    root=ET.parse(tmp_path/'page.svg').getroot()
    assert root.attrib['width']=='50mm' and root.attrib['height']=='30mm'
    canvas.close()


def test_controller_format_is_undoable_and_preserves_other_positions():
    canvas=ComposerCanvas()
    canvas.set_project(Project(title='undo'))
    controller=ComposerEditController(canvas)
    controller.reset()
    ann=controller.add_annotation('text')
    controller.apply_style({'font_size_pt':12.5})
    assert ann.properties['font_size_pt']==12.5
    controller.undo()
    assert canvas.current_figure().annotations[0].properties['font_size_pt']==8
    controller.redo()
    assert canvas.current_figure().annotations[0].properties['font_size_pt']==12.5
    canvas.close()
