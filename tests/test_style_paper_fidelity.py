"""Regressions found while transferring published PDF styles to real qPCR data."""
import pytest
import fitz
import pandas as pd
from matplotlib.colors import to_hex

from core.style_preset_registry import StylePresetRegistry
from ui.local_style_extraction.integration import save_extracted_style
from ui.local_style_extraction.pdf_native import extract_pdf_signals


def paper_panel():
    doc = fitz.open()
    page = doc.new_page(width=300, height=240)
    # Page furniture outside the selected panel must not become chart spines.
    page.draw_rect(fitz.Rect(5, 5, 295, 235), width=.25)
    page.draw_line((60, 180), (60, 50), width=.9)
    page.draw_line((60, 180), (240, 180), width=.9)
    for x, y, color in [(100, 115, (0.6, .2, .7)), (160, 90, (.2, .6, .3)), (220, 65, (.1, .2, .6))]:
        page.draw_rect(fitz.Rect(x-12, y, x+12, 180), color=(0, 0, 0), fill=(.9, .9, .9), width=.3)
        for dx, dy in [(-4, -2), (0, 2), (4, -4)]:
            page.draw_rect(fitz.Rect(x+dx-1.5, y+dy-1.5, x+dx+1.5, y+dy+1.5), color=None, fill=color)
        shape = page.new_shape()
        shape.draw_line((x, y-6), (x, y+6))
        shape.draw_line((x-3, y-6), (x+3, y-6))
        shape.draw_line((x-3, y+6), (x+3, y+6))
        shape.finish(color=(0, 0, 0), width=.3)
        shape.commit()
    page.insert_text((44, 180), '0', fontsize=7.1372, fontname='hebo')
    page.insert_text((38, 80), '100', fontsize=7.1372, fontname='hebo')
    page.insert_text((28, 170), 'Expression', fontsize=6.2451, fontname='hebo', rotate=90)
    return doc, page, fitz.Rect(20, 35, 250, 210)


def test_external_border_does_not_become_top_and_right_spines():
    doc, page, clip = paper_panel()
    s = extract_pdf_signals(page, clip)
    assert s['show_top_spine'] is False
    assert s['show_right_spine'] is False
    assert s['axis_line_width'] == pytest.approx(.9)
    doc.close()


def test_opaque_pdf_panel_background_hides_earlier_paths():
    doc=fitz.open();page=doc.new_page(width=300,height=240)
    for y in (60,80,100,120,140):
        page.draw_line((25,y),(245,y),width=.4)
    page.draw_rect(fitz.Rect(20,30,250,210),color=None,fill=(1,1,1))
    page.draw_line((60,180),(60,50),width=.9)
    page.draw_line((60,180),(240,180),width=.9)
    s=extract_pdf_signals(page,fitz.Rect(20,35,250,210))
    assert s['axis_line_width'] == pytest.approx(.9)
    assert s['show_top_spine'] is False
    doc.close()


def test_hidden_axis_primitives_are_checked_against_visible_page():
    doc=fitz.open();page=doc.new_page(width=300,height=240)
    for y in (60,80,100,120,140):
        page.draw_line((25,y),(245,y),width=.4)
    pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,20,20),False);pix.clear_with(255)
    page.insert_image(fitz.Rect(20,30,250,210),pixmap=pix)
    page.draw_line((60,180),(60,50),width=.9)
    page.draw_line((60,180),(240,180),width=.9)
    s=extract_pdf_signals(page,fitz.Rect(20,35,250,210))
    assert s['axis_line_width'] == pytest.approx(.9)
    doc.close()


def test_square_marker_geometry_and_separate_bar_palette():
    doc, page, clip = paper_panel()
    s = extract_pdf_signals(page, clip)
    assert s['point_shape'] == 'square'
    assert s['point_size'] == pytest.approx(3)
    assert s['bar_fill_color'] == '#E5E5E5' or s['bar_fill_color'] == '#E6E6E6'
    assert s['bar_use_series_colors'] is False
    assert len(s['series_colors']) == 3
    assert s['series_colors'][0].startswith('#99')  # leftmost purple, not tallest blue
    assert s['point_line_width'] == 0
    assert s['sem_line_width'] == pytest.approx(.3)
    assert s['sem_cap_size'] == pytest.approx(3)
    doc.close()


def test_marker_palette_ignores_overlaid_error_line_colors():
    doc, page, clip = paper_panel()
    # An error line crosses one marker; it is not an additional series color.
    page.draw_line((98,117),(102,117),width=1.,color=(0,0,0))
    s=extract_pdf_signals(page,clip)
    assert len(s['series_colors']) == 3
    doc.close()


def test_pdf_font_sizes_are_not_rounded_and_weight_is_preserved():
    doc, page, clip = paper_panel()
    s = extract_pdf_signals(page, clip)
    assert s['tick_label_size_pt'] == pytest.approx(7.1372, abs=1e-4)
    assert s['axis_label_size_pt'] == pytest.approx(6.2451, abs=1e-4)
    assert s['font_weight'] == 'bold'
    assert s['legend_size_pt'] is None  # no legend was identified
    doc.close()


def test_marker_diameter_is_converted_only_at_production_boundary(tmp_path):
    draft = {'name': 'square markers', 'source': {}, 'tokens': {
        'point_size': {'status': 'detected', 'value': 3.0, 'unit': 'pt'},
        'point_shape': {'status': 'detected', 'value': 'square'},
    }}
    saved = save_extracted_style(draft, StylePresetRegistry(tmp_path/'styles.json'))
    assert saved.plot_style['point_size'] == 9.0
    assert saved.extraction_profile['local_extraction']['tokens']['point_size']['value'] == 3.0


def test_saved_qpcr_style_reaches_all_axes_artists_and_exports(tmp_path):
    from core.plotting.qpcr_plot import build_qpcr_figure, save_qpcr_figure
    frame = pd.DataFrame({'target': ['L']*4, 'group': ['NC','NC','D','D'],
                          'fold_change': [1.,1.2,2.,2.4], 'mean_fold_change': [1.1,1.1,2.2,2.2],
                          'error': [.1,.1,.2,.2]})
    style = {'font_family':'Times New Roman', 'font_weight':'bold', 'font_size_pt':7.1,
             'tick_label_size_pt':6.2, 'axis_label_size_pt':7.8, 'axis_line_width':.4,
             'label_color':'#314152','background_color':'#F1F2F3', 'show_top_spine':True,
             'show_right_spine':True, 'point_size':9., 'point_shape':'square',
             'use_series_colors':True, 'bar_use_series_colors':False,
             'series_colors':['#9933B3','#33994D'], 'bar_fill_color':'#E6E6E6',
             'gridline_visible':True, 'gridline_color':'#CCDDCC'}
    fig = build_qpcr_figure(frame, {}, style=style, chart_kind='bar_dot')
    ax = fig.axes[0]
    assert to_hex(fig.get_facecolor()).upper() == '#F1F2F3'
    assert to_hex(ax.get_facecolor()).upper() == '#F1F2F3'
    assert all(to_hex(b.get_facecolor()).upper() == '#E6E6E6' for b in ax.patches)
    fig.canvas.draw()
    for t in ax.get_xticklabels()+ax.get_yticklabels():
        assert t.get_fontfamily() == ['Times New Roman']
        assert t.get_fontweight() == 'bold'
        assert t.get_fontsize() == pytest.approx(6.2)
        assert to_hex(t.get_color()).upper() == '#314152'
    assert all(sp.get_linewidth() == .4 for sp in ax.spines.values())
    assert any(line.get_visible() for line in ax.get_ygridlines())
    for suffix in ('png', 'pdf', 'svg'):
        save_qpcr_figure(fig, tmp_path/f'figure.{suffix}', format=suffix)
        assert (tmp_path/f'figure.{suffix}').stat().st_size > 100


def test_saved_source_metadata_keeps_page_and_crop(tmp_path):
    draft={'name':'paper', 'source': {'kind':'pdf','path':'paper.pdf','page_index':18,
           'page_count':37,'rect':[.1,.2,.3,.4], 'page_size':{'width':612,'height':792,'unit':'pt'}},
           'tokens':{}}
    record=save_extracted_style(draft,StylePresetRegistry(tmp_path/'styles.json'))
    assert record.source_metadata['source_page_count'] == 37
    assert record.extraction_profile['local_extraction']['source']['rect'] == [.1,.2,.3,.4]


def test_postscript_font_name_resolves_to_installed_family():
    from ui.local_style_extraction.fonthelpers import readable_family
    assert readable_family('ABCDEF+Arial-BoldMT') == 'Arial'


def test_legacy_draft_remains_loadable_with_new_optional_tokens(tmp_path):
    from ui.local_style_extraction.engine import load_style,save_style,_blank_tokens
    from ui.local_style_extraction.demo_common import SCHEMA,MIN_KEYS
    draft={'schema':SCHEMA,'tokens':{k:v for k,v in _blank_tokens().items() if k in MIN_KEYS}}
    save_style(draft,'old',str(tmp_path/'old.json'))
    restored=load_style(str(tmp_path/'old.json'))
    assert restored['tokens']['sem_line_width']['status'] == 'unknown'


def test_legacy_saved_library_draft_can_be_edited_without_file_migration():
    from ui.local_style_extraction.engine import edit_token,_blank_tokens
    from ui.local_style_extraction.demo_common import SCHEMA,MIN_KEYS
    old={'schema':SCHEMA,'tokens':{k:v for k,v in _blank_tokens().items() if k in MIN_KEYS}}
    edited=edit_token(old,'font_size_pt',9.)
    assert edited['resolved_style']['font_size_pt']==9.
    assert 'sem_line_width' in edited['fallback_keys']
    extended=edit_token(edited,'sem_line_width',.3)
    assert extended['tokens']['sem_line_width']['status']=='user_edited'
    assert extended['resolved_style']['sem_line_width']==.3


def test_extraction_preview_preserves_bar_and_error_tokens():
    from matplotlib.figure import Figure
    from ui.local_style_extraction.preview import _style_from_draft
    from ui.local_style_extraction.chart_types import draw_grouped_on_ax
    from ui.local_style_extraction.demo_common import SCHEMA
    draft={'schema':SCHEMA,'resolved_style':{'font_weight':'bold','bar_use_series_colors':False,
           'bar_fill_color':'#E6E6E6','bar_line_width':.3,'point_line_width':0.,
           'sem_line_width':.3,'sem_cap_size':3.,'sem_cap_thick':.3}}
    style=_style_from_draft(draft)
    assert style['bar_use_series_colors'] is False
    fig=Figure(); ax=fig.add_subplot()
    draw_grouped_on_ax(ax,'bar_dot',style,data={'groups':[{'label':'A','values':[1.,2.,3.]},
                                                       {'label':'B','values':[2.,3.,4.]}]})
    bars=[p for p in ax.patches if p.get_height()>0]
    assert all(to_hex(p.get_facecolor()).upper()=='#E6E6E6' for p in bars)
    assert any(type(c).__name__=='ErrorbarContainer' for c in ax.containers)
    assert ax.get_yticklabels()[0].get_fontweight() == 'bold'


def test_loading_saved_style_restores_source_and_current_name(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from ui.widgets.style_lab_workspace import StyleLabWorkspace
    app=QApplication.instance() or QApplication([])
    source=tmp_path/'source.pdf'
    doc=fitz.open(); doc.new_page(); doc.save(source); doc.close()
    workspace=StyleLabWorkspace(preset_registry_path=tmp_path/'styles.json',render_initial_preview=False)
    workspace.open_local_extraction()
    editor=workspace.local_extraction
    monkeypatch.setattr(editor,'_confirm_replace',lambda _:True)
    restored=[]
    monkeypatch.setattr(editor,'_reload_source_visual',lambda *args:restored.append(args))
    try:
        record=save_extracted_style({'name':'old','source':{'path':str(source),'page_index':0,
                                    'rect':[.1,.2,.3,.4]},'tokens':{}},workspace._preset_registry)
        workspace._preset_registry.rename_preset(record.preset_id,'renamed')
        workspace._load_saved_style(record.preset_id)
        assert editor.draft['name']=='renamed'
        assert restored==[(str(source),0,[.1,.2,.3,.4])]
    finally:
        workspace.shutdown_local_extraction();workspace.close()


def test_qpcr_draws_supplied_comparisons_when_control_is_last():
    from core.plotting.qpcr_plot import build_qpcr_figure
    frame=pd.DataFrame({'target':['L']*3,'group':['10','D','NC'],'fold_change':[3.,.3,1.],
                        'mean_fold_change':[3.,.3,1.],'error':[0.,0.,0.]})
    stats={'targets':{'L':{'comparisons':[{'group1':'NC','group2':'10','label':'n/a'},
                                        {'group1':'NC','group2':'D','label':'n/a'}]}}}
    fig=build_qpcr_figure(frame,stats,chart_kind='bar_dot')
    pairs=[set(line.get_xdata()) for line in fig.axes[0].lines if len(line.get_xdata())==4]
    assert {0,2} in pairs and {1,2} in pairs


def test_plain_bar_preview_uses_extracted_uniform_fill():
    from matplotlib.figure import Figure
    from ui.local_style_extraction.preview import _draw_bar,_FontResolver
    fig=Figure();ax=fig.add_subplot()
    _draw_bar(ax,{'bar_use_series_colors':False,'bar_fill_color':'#EDEBEB','bar_line_width':.3},_FontResolver())
    assert all(to_hex(p.get_facecolor()).upper()=='#EDEBEB' for p in ax.patches)
    assert all(p.get_linewidth()==.3 for p in ax.patches)


def test_native_draft_does_not_report_unused_raster_font_warning():
    from ui.local_style_extraction.engine import _build_tokens
    from ui.local_style_extraction.demo_common import empty_signals
    n=empty_signals();n.update(native_text=True,font_family='Arial')
    n['methods']['font_family']='pdf_native'
    r=empty_signals();r['warnings']=['Raster typography is relative box height only; Font identity unknown.']
    tokens,warnings=_build_tokens(n,r,'plot')
    assert not any('Font identity unknown' in w for w in warnings)


def test_restored_geometry_evidence_keeps_estimated_status():
    from ui.local_style_extraction.app import infer_origin_status
    assert infer_origin_status({'status':'user_edited','method':'pdf_geometry'})=='estimated'
    assert infer_origin_status({'status':'user_edited','method':'pdf_rendered'})=='detected'


def test_axis_box_shape_and_tick_geometry_transfer_without_changing_values():
    doc,page,clip=paper_panel()
    for y in (80,120,160):page.draw_line((58.5,y),(60,y),width=.9)
    for x in (100,160,220):page.draw_line((x,180),(x,181.5),width=.9)
    s=extract_pdf_signals(page,clip)
    assert s['axes_box_aspect'] == pytest.approx(130/180)
    assert s['tick_length'] == pytest.approx(1.5)
    assert s['x_tick_length'] == pytest.approx(1.5)
    assert s['tick_width'] == pytest.approx(.9)
    from matplotlib.figure import Figure
    from core.plotting.style_presets import apply_plot_axes_style
    ax=Figure().add_subplot()
    apply_plot_axes_style(ax,{'axes_box_aspect':s['axes_box_aspect'],'x_tick_length':1.5})
    assert ax.get_box_aspect()==pytest.approx(130/180)
    assert ax.xaxis.majorTicks[0].tick1line.get_markersize()==1.5
    doc.close()


def test_label_rotation_reaches_renderers_without_overriding_explicit_qpcr_choice():
    doc,page,clip=paper_panel()
    for x in (100,160,220):
        point=fitz.Point(x,202)
        page.insert_text(point,'AAA',fontsize=7,morph=(point,fitz.Matrix(45)))
    assert extract_pdf_signals(page,clip)['x_tick_rotation']==pytest.approx(45)
    doc.close()
    from core.plotting.qpcr_plot import build_qpcr_figure
    from core.plotting.style_presets import apply_plot_axes_style
    from matplotlib.figure import Figure
    ax=Figure().add_subplot();ax.set_xticks([0,1],['A','B'])
    apply_plot_axes_style(ax,{'x_tick_rotation':45})
    assert all(t.get_rotation()==45 and t.get_ha()=='right' for t in ax.get_xticklabels())
    frame=pd.DataFrame({'target':['L']*2,'group':['NC','D'],'fold_change':[1.,2.],
                        'mean_fold_change':[1.,2.],'error':[0.,0.]})
    inherited=build_qpcr_figure(frame,{},style={'x_tick_rotation':45})
    assert inherited.axes[0].get_xticklabels()[0].get_rotation()==45
    overridden=build_qpcr_figure(frame,{},style={'x_tick_rotation':45},x_rotation=0)
    assert overridden.axes[0].get_xticklabels()[0].get_rotation()==0

