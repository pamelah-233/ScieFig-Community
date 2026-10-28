import xml.etree.ElementTree as ET
from PyQt6.QtWidgets import QApplication, QGraphicsScene

_APP = QApplication.instance() or QApplication([])


def test_qpcr_inline_svg_uses_shared_font_and_persists_rich_style():
    from ui.widgets.qpcr_layout_canvas import QPCRLayoutPlotItem
    from ui.widgets.shared_svg_text import SvgEditableTextItem
    svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80" viewBox="0 0 200 80"><text x="100" y="30" style="font-size:8.5px;text-anchor:middle;fill:#123456">H2O</text></svg>'
    saved={}
    scene=QGraphicsScene()
    plot=QPCRLayoutPlotItem(svg, {'plot_uuid':'p','x':0,'y':0,'width':400,'height':160}, lambda i,v:saved.update({str(i):v}))
    scene.addItem(plot)
    from ui.widgets.shared_layout_shapes import SharedResizeHandle
    assert isinstance(plot.handles['se'],SharedResizeHandle)
    item=plot._svg_text_items[0]
    assert isinstance(item,SvgEditableTextItem)
    assert item.display_font_size_pt==8.5
    item.apply_text_range_style(1,1,{'vertical_align':'subscript'})
    _APP.processEvents()
    assert any(r['start']==1 and r.get('vertical_align')=='subscript' for r in saved['0']['text_payload']['runs'])
    assert svg_text_entries(plot._export_svg_bytes)[0].text=='H2O'
    item=plot._svg_text_items[0]
    item.apply_text_style({'font_size_pt':11.5,'text_color':'#AA2233'})
    _APP.processEvents()
    assert saved['0']['style']['font_size_pt']==11.5
    assert svg_text_entries(plot._export_svg_bytes)[0].style['color']=='#AA2233'
    scene.clear()
from core.utils.svg_text import apply_svg_text_overrides,normalize_svg_text_overrides,svg_text_entries


def test_svg_style_and_rich_text_override_roundtrip_without_position_changes():
    svg=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80"><text x="20" y="30" style="font-size:10px;fill:#111111">H2O</text></svg>'
    override={'0':{'style':{'font_family':'Arial','font_size_pt':8.5,'color':'#CC2233'},
        'text_payload':{'plain_text':'H2O','runs':[{'start':1,'length':1,'vertical_align':'subscript'}]}}}
    assert normalize_svg_text_overrides(override)['0']['style']['font_size_pt']==8.5
    root=ET.fromstring(apply_svg_text_overrides(svg,override))
    text=next(e for e in root.iter() if e.tag.endswith('text'))
    assert text.attrib['x']=='20' and text.attrib['y']=='30'
    assert '8.5px' in text.attrib['style'] and '#CC2233' in text.attrib['style']
    assert any('sub' in e.attrib.get('style','') for e in text)
    entry=svg_text_entries(apply_svg_text_overrides(svg,override))[0]
    assert entry.text=='H2O' and entry.style['font_size_pt']==8.5


def test_composer_svg_edits_are_inline_and_reference_local(tmp_path):
    from core.models import Project,ExternalImagePanel
    from ui.widgets.composer_canvas import ComposerCanvas,ComposerPanelItem
    from ui.widgets.shared_svg_text import SvgEditableTextItem
    source=tmp_path/'plot.svg'
    original=b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><text x="20" y="30">Original</text></svg>'
    source.write_bytes(original)
    panel=ExternalImagePanel(panel_id='svg')
    panel.data['image_path']=str(source)
    panel.data['is_vector']=True
    project=Project(title='independent')
    project.add_panel(panel)
    canvas=ComposerCanvas();canvas.set_project(project)
    canvas.place_panel(panel.panel_uuid);canvas.place_panel(panel.panel_uuid)
    items=[i for i in canvas.scene.items() if isinstance(i,ComposerPanelItem)]
    assert len(items)==2
    assert any(isinstance(i,SvgEditableTextItem) for i in canvas.scene.items())
    first,second=items
    first.apply_svg_text_override(0,text='Local')
    assert source.read_bytes()==original
    assert first.ref.chart_text_overrides['svg']['0']=='Local'
    assert second.ref.chart_text_overrides=={}
    export=canvas._plot_payload_bytes_for_ref(panel,first.ref,'svg',200,80,300)
    assert svg_text_entries(export)[0].text=='Local'
    canvas.close();_APP.processEvents()


def test_rotated_svg_label_keeps_its_anchor_at_the_svg_baseline():
    from PyQt6.QtCore import QPointF,QLineF
    from ui.widgets.qpcr_layout_canvas import QPCRLayoutPlotItem
    svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><text x="20" y="40" transform="rotate(-90 20 40)" style="font-size:10px;text-anchor:middle">Y axis</text></svg>'
    scene=QGraphicsScene();plot=QPCRLayoutPlotItem(svg,{'plot_uuid':'r','width':200,'height':80})
    scene.addItem(plot)
    item=plot._svg_text_items[0]
    anchor=item.mapToParent(QPointF(item.textWidth()*item._anchor,item._ascent))
    assert QLineF(anchor,QPointF(20*item.binding.sx,40*item.binding.sy)).length()<.01
    scene.clear()


def test_svg_multiline_override_exports_separate_lines():
    svg=b'<svg xmlns="http://www.w3.org/2000/svg"><text x="20" y="30" style="font-size:10px">Title</text></svg>'
    result=apply_svg_text_overrides(svg,{'0':{'text_payload':{'plain_text':'First\nSecond','runs':[],'line_spacing':1.2}}})
    root=ET.fromstring(result)
    lines=[e for e in root.iter() if e.tag.endswith('tspan') and e.get('x')=='20']
    assert len(lines)==2
    assert float(lines[1].get('y'))>float(lines[0].get('y'))
    assert svg_text_entries(result)[0].text=='First\nSecond'


def test_dragging_rotated_svg_label_moves_its_rotation_pivot_too():
    svg=b'<svg xmlns="http://www.w3.org/2000/svg"><text x="20" y="40" transform="rotate(-90 20 40)">Y axis</text></svg>'
    result=apply_svg_text_overrides(svg,{'0':{'x':25,'y':48}})
    element=next(e for e in ET.fromstring(result).iter() if e.tag.endswith('text'))
    assert element.get('transform')=='rotate(-90 25 48)'


def test_clearing_svg_label_does_not_reindex_the_remaining_labels():
    svg=b'<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="10">First</text><text x="0" y="30">Second</text></svg>'
    cleared=apply_svg_text_overrides(svg,{'0':''})
    assert svg_text_entries(cleared)[0].index==1
    edited=apply_svg_text_overrides(cleared,{'1':'Changed'})
    assert svg_text_entries(edited)[0].text=='Changed'
