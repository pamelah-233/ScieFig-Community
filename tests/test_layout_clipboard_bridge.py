import os
from copy import deepcopy
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PyQt6.QtWidgets import QApplication,QMainWindow
from core.models import Project,QPCRPanel,ExternalImagePanel
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas
from ui.widgets.composer_canvas import ComposerCanvas
from ui.controllers.layout_edit_controller import LayoutEditController
from ui.controllers.composer_edit_controller import ComposerEditController
from ui.widgets.layout_clipboard import write_layout_clipboard

APP=QApplication.instance() or QApplication([])


@pytest.fixture
def editors():
    project=Project(title='bridge')
    panel=QPCRPanel(panel_id='source')
    project.add_panel(panel)
    window=QMainWindow()
    source=QPCRLayoutCanvas();source.set_panel(panel)
    editing=LayoutEditController(window);editing.set_target('qpcr',panel,source)
    composer=ComposerCanvas();composer.set_project(project)
    control=ComposerEditController(composer);control.reset()
    yield panel,source,editing,composer,control
    editing.set_target();source.close();composer.close();window.close();APP.processEvents()


def test_source_annotation_roundtrip_to_composer_and_back_is_undoable(editors):
    panel,source,editing,composer,control=editors
    text=source.add_text('H2O');text.apply_text_range_style(1,1,{'vertical_align':'subscript','font_size_pt':14})
    shape=source.add_shape('arrow');shape.apply_shape_style({'stroke_width':3,'stroke_dash':'dash','fill_alpha':.4})
    source.persist_layout_edits()
    text.setSelected(True);shape.setSelected(True)
    protected=deepcopy(panel.analysis)
    editing.handle_command('copy');control.handle_command('paste')
    figure=composer.current_figure()
    assert len(figure.annotations)==2
    t=next(a for a in figure.annotations if a.type=='text')
    s=next(a for a in figure.annotations if a.type=='shape')
    assert t.properties['content']=='H2O' and t.properties['font_size_pt']==10
    assert t.properties['text']['runs'][0]['vertical_align']=='subscript'
    assert t.properties['width_mm']==pytest.approx(text.layout_object.width/4)
    assert s.properties['shape_type']=='arrow' and s.properties['stroke_dash']=='dash'
    assert s.properties['fill_alpha']==102
    assert control.undo_stack.count()==1
    control.undo();assert not composer.current_figure().annotations
    control.redo();assert len(composer.current_figure().annotations)==2
    control.handle_command('copy');editing.handle_command('paste')
    assert len(panel.data['qpcr_layout']['texts'])==2
    assert len(panel.data['qpcr_layout']['shapes'])==2
    assert panel.data['qpcr_layout']['texts'][-1]['text_payload']['runs'][0]['vertical_align']=='subscript'
    editing.undo();assert len(panel.data['qpcr_layout']['texts'])==1
    assert panel.analysis==protected


def test_unsupported_source_assets_report_failure_without_partial_paste(editors):
    _,_,_,composer,control=editors
    write_layout_clipboard({'kind':'source','entries':[{'kind':'plot','record':{'plot_uuid':'x'}}]})
    messages=[]
    control.paste_rejected.connect(messages.append)
    control.handle_command('paste')
    assert messages
    assert not composer.current_figure().annotations
    assert control.undo_stack.count()==0


def test_composer_source_reference_cannot_be_pasted_as_an_annotation(editors):
    panel,_,editing,composer,control=editors
    asset=ExternalImagePanel(panel_id='asset');composer.project.add_panel(asset)
    composer.place_panel(asset.panel_uuid)
    ref=next(i for i in composer.scene.items() if i.data(0)=='panel_ref');ref.setSelected(True)
    control.handle_command('copy')
    before=deepcopy(panel.data.get('qpcr_layout',{}))
    editing.handle_command('paste')
    assert editing.window.statusBar().currentMessage()
    assert panel.data.get('qpcr_layout',{})==before
