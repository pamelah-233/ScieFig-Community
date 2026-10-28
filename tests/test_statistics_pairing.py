from copy import deepcopy
from types import SimpleNamespace

import pytest


def test_pair_ids_follow_stable_samples_not_display_order():
    from core.statistics_pairing import attach_pair_ids, save_pair_ids
    from core.statistics import compute_group_statistics
    panel = SimpleNamespace(analysis={})
    rows = [dict(sample_key=k, value=v) for k, v in [('b2',8),('a1',2),('b1',5),('a2',3)]]
    original = deepcopy(rows)
    save_pair_ids(panel, 'counts', {'a1':'1', 'b1':'1', 'a2':'2', 'b2':'2'})
    paired = attach_pair_ids(panel, 'counts', rows)
    result = compute_group_statistics(paired, dict(a1='A',a2='A',b1='B',b2='B'), 'A', 'Paired t-test')
    assert result['comparisons'][0]['p_value'] == pytest.approx(.15595826075)
    assert rows == original
    assert attach_pair_ids(panel, 'other', rows) == original
    assert {r['sample_key']:r['pair_id'] for r in result['plot_data']} == panel.analysis['statistics_pair_ids']['counts']


def test_blank_duplicate_or_missing_pair_ids_never_produce_p_value():
    from core.statistics import compute_group_statistics
    for ids in (['', '2', '', '2'], ['1','1','1','2'], ['1','2','1','3']):
        rows = [dict(lane_label=g, pair_id=i, value=v) for g,i,v in zip(['A','A','B','B'],ids,[1,3,4,8])]
        result = compute_group_statistics(rows, {}, 'A', 'Paired t-test')
        assert result['comparisons'][0]['p_value'] is None
        assert 'statistics_pairing_required' in result['warnings']


def test_pairing_dialog_sorts_by_identity_and_cancels_without_mutation():
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from ui.dialogs.statistics_pairing_dialog import StatisticsPairingDialog
    app = QApplication.instance() or QApplication([])
    saved = {'a':'1','b':'2'}
    dialog = StatisticsPairingDialog([dict(sample_key='a', lane_label='Z'),dict(sample_key='b',lane_label='A')],
                                     {'a':'A','b':'B'},saved)
    dialog.table.sortItems(0, Qt.SortOrder.AscendingOrder)
    assert dialog.pair_ids() == saved
    dialog.table.item(0,2).setText('3')
    assert dialog.pair_ids()['b'] == '3'
    dialog.reject()
    assert saved == {'a':'1','b':'2'}


def test_pairing_survives_metric_changes_and_partial_edits():
    from core.statistics_pairing import attach_pair_ids, save_pair_ids, sample_identity
    panel=SimpleNamespace(analysis={})
    count=dict(sample_key='image:count',sample_group_key='image:0')
    area=dict(sample_key='image:area',sample_group_key='image:0')
    save_pair_ids(panel,'counts',{sample_identity(count):'animal1','excluded':'animal2'})
    assert attach_pair_ids(panel,'counts',[area])[0]['pair_id'] == 'animal1'
    save_pair_ids(panel,'counts',{'image:0':'animal3'})
    assert panel.analysis['statistics_pair_ids']['counts']['excluded'] == 'animal2'
    save_pair_ids(panel,'counts',{'image:0':''})
    assert 'image:0' not in panel.analysis['statistics_pair_ids']['counts']
