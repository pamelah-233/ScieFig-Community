import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QApplication
from core.models import Project
from ui.widgets.composer_canvas import ComposerCanvas
from ui.controllers.composer_edit_controller import ComposerEditController

APP=QApplication.instance() or QApplication([])


@pytest.fixture
def canvas():
    widget=ComposerCanvas();widget.set_project(Project(title='bounds'))
    control=ComposerEditController(widget);control.reset();control.set_page(100,80)
    yield widget
    widget.close();APP.processEvents()


@pytest.mark.parametrize('kind',['text','rectangle','arrow'])
def test_preflight_warns_for_partly_outside_annotations(canvas,kind):
    if kind=='text':canvas.add_text_annotation('Overflow',QPointF(95*4,20*4))
    else:canvas.add_shape_annotation(kind,QPointF(95*4,40*4),width_mm=24,height_mm=16)
    assert any('outside' in m['message'] for m in canvas.preflight_export(300))


def test_preflight_uses_rotated_text_bounds(canvas):
    annotation=canvas.add_text_annotation('Rotated',QPointF(1*4,20*4))
    annotation.properties.update(width_mm=30,height_mm=15,rotation_deg=90)
    canvas.render()
    assert any('outside' in m['message'] for m in canvas.preflight_export(300))


def test_preflight_does_not_count_hidden_annotations_or_selection_handles(canvas):
    annotation=canvas.add_shape_annotation('rectangle',QPointF(95*4,40*4),width_mm=24,height_mm=16)
    annotation.visible=False
    inside=canvas.add_text_annotation('Inside',QPointF(20*4,20*4))
    canvas.render()
    item=next(i for i in canvas.scene.items() if i.data(0)=='annotation' and i.data(1)==inside.annotation_uid)
    item.setSelected(True)
    assert not any('outside' in m['message'] for m in canvas.preflight_export(300))
