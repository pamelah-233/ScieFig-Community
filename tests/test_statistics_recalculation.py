from types import SimpleNamespace
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest


def history_panel(collection,measurements):
    return SimpleNamespace(analysis={collection:measurements},images=lambda:[{'uid':'img','source_name':'img','group':'A'}])


@pytest.mark.parametrize('module,collection,metric,key',[
    ('ihc','ihc_measurements','Positive Area %','positive_area_percent'),
    ('brightfield','brightfield_measurements','Area Fraction %','area_fraction_percent'),
])
def test_same_image_recalculation_uses_latest_result(module,collection,metric,key):
    from core.ihc_statistics import collect_ihc_stat_values
    from core.brightfield_statistics import collect_brightfield_stat_values
    measurements=[dict(image_uid='img',measurement_uuid=str(i),params={'threshold':v},results={key:v}) for i,v in enumerate((10.,30.))]
    rows={'ihc':collect_ihc_stat_values,'brightfield':collect_brightfield_stat_values}[module](history_panel(collection,measurements),metric)
    assert len(rows)==1
    assert rows[0]['value']==30


def test_brightfield_excluded_latest_does_not_restore_old_numbers():
    from core.brightfield_statistics import collect_brightfield_stat_values
    ms=[dict(image_uid='img',measurement_uuid='old',results={'object_count':2}),
        dict(image_uid='img',measurement_uuid='new',results={'object_count':5},include_in_statistics=False)]
    assert collect_brightfield_stat_values(history_panel('brightfield_measurements',ms),'Segmented Region Count')==[]


def test_he_fiber_repeated_run_is_not_another_sample():
    from core.brightfield_statistics import collect_brightfield_ai_stat_values
    ms=[dict(image_uid='img',measurement_uuid=str(i),results={'ai_object_count':v}) for i,v in enumerate((2,5))]
    rows=collect_brightfield_ai_stat_values(history_panel('brightfield_ai_object_count_measurements',ms))
    assert len(rows)==1 and rows[0]['ai_object_count']==5


def test_wb_recalculation_keeps_latest_lane_value():
    from core.wb_statistics import collect_wb_stat_values
    runs=[dict(measurements=[dict(strip_id='strip',strip_label='Target',lanes=[dict(lane_index=0,lane_label='A',fold=v)])]) for v in (1.,3.)]
    rows=collect_wb_stat_values(runs)
    assert len(rows)==1 and rows[0]['value']==3.


def test_two_group_kruskal_uses_kruskal_not_t_test():
    from core.statistics import compute_group_statistics
    from scipy.stats import kruskal
    a,b=[1,2,3,40],[5,6,7,8]
    rows=[dict(lane_label=g,value=v) for g,vs in [('A',a),('B',b)] for v in vs]
    result=compute_group_statistics(rows,{},'A','Kruskal-Wallis')
    assert result['comparisons'][0]['p_value']==pytest.approx(kruskal(a,b).pvalue)


def test_paired_test_without_pair_ids_does_not_assume_row_order():
    from core.statistics import compute_group_statistics
    rows=[dict(lane_label=g,value=v) for g,vs in [('A',[1,2,4]),('B',[8,6,7])] for v in vs]
    result=compute_group_statistics(rows,{},'A','Paired t-test')
    assert result['comparisons'][0]['p_value'] is None
    assert result.get('warnings')


def test_nonfinite_values_do_not_inflate_sample_size():
    from core.statistics import compute_group_statistics
    rows=[dict(lane_label='A',value=v) for v in (1.,2.,float('inf'))]
    result=compute_group_statistics(rows,{},'A','Unpaired t-test')
    assert result['groups'][0]['n']==2
    assert result.get('warnings')


def test_qpcr_explicitly_cleared_groups_do_not_reuse_old_overrides():
    from pathlib import Path
    from core.models import QPCRPanel
    panel=QPCRPanel(panel_id='qpcr-clear')
    panel.import_qpcr_file(Path(__file__).resolve().parents[1]/'sample_data/qpcr_phase2_sample.csv',reference_gene='GAPDH',control_group='siCtrl')
    sample=next(r['sample'] for r in panel.data['raw_rows'] if str(r['sample']).startswith('siSPNS1'))
    panel.reanalyze(group_overrides={sample:'Custom'})
    panel.reanalyze(group_overrides={})
    assert panel.analysis.get('group_overrides')=={}


def test_distinct_legacy_roi_geometries_are_not_collapsed():
    from core.measurement_history import latest_measurements
    rows=[dict(image_uid='img',roi={'x':x,'y':0,'w':10,'h':10}) for x in (0,20)]
    assert len(latest_measurements(rows))==2


def test_explicit_pairs_are_matched_by_id_not_display_order():
    from core.statistics import compute_group_statistics
    from scipy.stats import ttest_rel
    rows=[dict(lane_label='A',pair_id=i,value=v) for i,v in enumerate((1,3,6))]
    rows += [dict(lane_label='B',pair_id=i,value=v) for i,v in ((2,10),(0,3),(1,4))]
    result=compute_group_statistics(rows,{},'A','Paired t-test')
    assert result['comparisons'][0]['p_value']==pytest.approx(ttest_rel([1,3,6],[3,4,10]).pvalue)


@pytest.mark.parametrize('kind',['ihc','brightfield_ai'])
def test_stat_controls_show_current_run_and_keep_manual_group(kind):
    from PyQt6.QtWidgets import QApplication
    from ui.panels.placeholder_panel import DataInputPanel
    app=QApplication.instance() or QApplication([]);data=DataInputPanel()
    old=dict(image_uid='img',image_name='image',measurement_uuid='old',group='A',include_in_statistics=True)
    new={**old,'measurement_uuid':'new'}
    populate=data.populate_ihc_stats_controls if kind=='ihc' else lambda ms:data.populate_brightfield_stats_controls(ms,'ai_object_count')
    table=data.ihc_stats_group_table if kind=='ihc' else data.brightfield_stats_group_table
    populate([old]);table.item(0,1).setText('Custom')
    populate([old,new])
    assert table.rowCount()==1
    assert table.item(0,1).text()=='Custom'
    data.close()


def test_wb_controls_keep_reference_group_after_refresh():
    from PyQt6.QtWidgets import QApplication
    from ui.panels.placeholder_panel import DataInputPanel
    app=QApplication.instance() or QApplication([]);data=DataInputPanel()
    run=dict(measurements=[dict(strip_id='s',lanes=[dict(lane_index=i,lane_label=g,fold=1.) for i,g in enumerate(('A','B'))])])
    data.populate_wb_stats_controls([run]);data.wb_stats_reference_group_combo.setCurrentText('B')
    data.populate_wb_stats_controls([run,run])
    assert data.wb_stats_reference_group_combo.currentText()=='B'
    data.close()


def test_same_method_fluorescence_recalculation_uses_latest_count():
    from core.fluoro_statistics import collect_fluoro_stat_values
    rows=[dict(image_uid='img',channel_index=0,count=v,method='otsu',threshold=20) for v in (1,3)]
    result=collect_fluoro_stat_values(history_panel('object_counts',rows),'object_count','count')
    assert len(result)==1 and result[0]['value']==3


def test_fluorescence_statistics_only_include_selected_rows():
    from core.fluoro_statistics import collect_fluoro_stat_values,compute_fluoro_statistics
    panel=SimpleNamespace(analysis={'object_counts':[dict(image_uid=uid,channel_index=0,count=2,method='otsu') for uid in ('a','b')]},images=lambda:[dict(uid=u,source_name=u) for u in ('a','b')])
    rows=collect_fluoro_stat_values(panel,'object_count','count')
    result=compute_fluoro_statistics(panel,'object_count','count',{rows[0]['sample_group_key']:'A'},'A','Unpaired t-test')
    assert len(result['plot_data'])==1


@pytest.mark.parametrize('module',['ihc','brightfield'])
def test_all_unchecked_stat_samples_do_not_reappear(module):
    from pathlib import Path
    from PyQt6.QtWidgets import QApplication
    from ui.main_window import MainWindow
    app=QApplication.instance() or QApplication([]);window=MainWindow(Path(__file__).resolve().parents[1]/'resources')
    try:
        window.set_module(module)
        panel=window._active_ihc_panel() if module=='ihc' else window._active_brightfield_panel()
        ms=[dict(image_uid='img',image_name='image',measurement_uuid='m',group='A',results={'positive_area_percent':20.,'area_fraction_percent':20.})]
        panel.analysis[module+'_measurements']=ms
        getattr(window.data_panel,'populate_'+module+'_stats_controls')(ms)
        table=getattr(window.data_panel,module+'_stats_group_table')
        table.cellWidget(0,2).setChecked(False)
        getattr(window,'run_'+module+'_statistics')()
        assert getattr(window.data_panel,module+'_stats_result_table').rowCount()==0
    finally:
        window._confirm_destructive_action=lambda *args:True
        window.close()


def test_fluorescence_distribution_does_not_duplicate_recalculated_objects():
    from core.fluoro_statistics import collect_fluoro_distribution_rows
    rows=[dict(image_uid='img',channel_index=0,ai_panel_context_hash='same',ai_measurement_uuid=str(i),object_area_distribution_px=values) for i,values in enumerate(([10.,20.],[30.]))]
    panel=history_panel('fluorescence_ai_assisted_measurements',rows)
    assert collect_fluoro_distribution_rows(panel,'ai_assisted')[0]['values']==[30.]
    rows.append({**rows[-1],'ai_measurement_uuid':'zero','object_area_distribution_px':[]})
    assert collect_fluoro_distribution_rows(panel,'ai_assisted')==[]


def test_history_scope_uses_active_roi_not_the_whole_roi_list():
    from core.measurement_history import latest_measurements
    items=[dict(roi_uuid=key,type='rectangle',coordinates_px={'x':x,'y':0,'w':10,'h':10}) for key,x in [('a',0),('b',20)]]
    rows=[dict(image_uid='img',roi={'mode':'selected_roi','items':items,'active_item_uuid':key}) for key in ('a','b')]
    assert len(latest_measurements(rows))==2
