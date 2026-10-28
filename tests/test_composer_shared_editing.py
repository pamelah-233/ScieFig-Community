from PyQt6.QtWidgets import QApplication
from core.models import Project
from ui.widgets.composer_canvas import ComposerCanvas, ComposerTextItem
from ui.widgets.shared_layout_text import SharedTextItem

_APP = QApplication.instance() or QApplication([])


def test_composer_uses_shared_text_and_preserves_fractional_style_and_position():
    canvas = ComposerCanvas()
    canvas.set_project(Project(title="editing"))
    annotation = canvas.add_text_annotation("Alpha\nBeta")
    annotation.properties["font_size_pt"] = 8.5
    canvas.render()
    item = next(item for item in canvas.scene.items() if isinstance(item, ComposerTextItem))
    assert isinstance(item, SharedTextItem)
    before = (annotation.x_mm, annotation.y_mm)
    item.apply_text_style({"font_size_pt": 9.5, "text_color": "#CC2233"})
    assert annotation.properties["font_size_pt"] == 9.5
    assert annotation.properties["color"] == "#CC2233"
    assert (annotation.x_mm, annotation.y_mm) == before
    assert item.document().blockCount() == 2
    canvas.close()


def test_grid_change_keeps_reference_identity_and_local_edits():
    from core.models import ExternalImagePanel
    project=Project(title='grid')
    panel=ExternalImagePanel(panel_id='asset')
    project.add_panel(panel)
    canvas=ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(panel.panel_uuid)
    ref=canvas.current_figure().panel_refs[0]
    ref.chart_kind='box'
    ref.border=True
    uid=ref.ref_uid
    canvas.add_text_annotation('Keep me')
    canvas.set_layout_preset('2x3')
    actual=canvas.current_figure().panel_refs[0]
    assert actual.ref_uid==uid
    assert actual.chart_kind=='box' and actual.border
    assert any(a.properties.get('content')=='Keep me' for a in canvas.current_figure().annotations)
    canvas.close()


def test_panel_label_is_independent_editable_and_can_reattach():
    from core.models import ExternalImagePanel
    from core.models.composer import ComposerFigure
    project=Project(title='labels')
    panel=ExternalImagePanel(panel_id='asset')
    project.add_panel(panel)
    canvas=ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(panel.panel_uuid)
    ref=canvas.current_figure().panel_refs[0]
    label=canvas.label_for_reference(ref.ref_uid)
    assert label is not None and label.properties['follow_panel']
    item=next(i for i in canvas.scene.items() if isinstance(i,ComposerTextItem) and i.annotation is label)
    item.setPos(140,160)
    assert not label.properties['follow_panel']
    assert label.x_mm==35 and label.y_mm==40
    item.setPlainText('Custom')
    item._sync_text()
    assert not ref.label_auto
    ref.x_mm += 20
    canvas.reference_changed()
    assert label.x_mm==35
    assert canvas.set_label_follow(label.annotation_uid,True)
    canvas._renumber_auto_labels()
    assert ref.figure_label=='Custom'
    restored=ComposerFigure.from_dict(canvas.current_figure().to_dict())
    assert any(a.properties.get('content')=='Custom' for a in restored.annotations)
    canvas.close()


def test_composer_shapes_use_shared_geometry_and_styles():
    from ui.widgets.shared_layout_shapes import SharedShapeItem
    from ui.widgets.composer_canvas import ComposerShapeItem
    canvas=ComposerCanvas()
    canvas.set_project(Project(title='shapes'))
    ann=canvas.add_shape_annotation('rectangle')
    item=next(i for i in canvas.scene.items() if isinstance(i,ComposerShapeItem))
    assert isinstance(item,SharedShapeItem)
    item.apply_shape_style({'fill_alpha':.25,'stroke_dash':'dash'})
    item.apply_geometry(8,9,26,14,repaint=False)
    assert ann.properties['fill_alpha']==64
    assert ann.properties['stroke_dash']=='dash'
    assert ann.x_mm==8 and ann.properties['width_mm']==26
    canvas.close()
