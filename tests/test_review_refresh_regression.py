from copy import deepcopy

import numpy as np
from PyQt6.QtCore import Qt

from core.measurement_review import ObjectReviewSession
from core.measurement_review_adapter import apply_review_to_panel
from tests.test_batch_analysis import fluorescence_panel
from tests.test_microscopy_workflow_controller import app, window


def test_zero_object_ai_review_clears_older_run_overlay_and_selects_reviewed_summary(window):
    panel = fluorescence_panel()
    try:
        for _ in range(2):
            records = panel.run_ai_assisted_analysis('image-0', [0], settings={'min_object_area_px': 5})
        panel.set_active_image('image-0')
        prior_run = deepcopy(panel.analysis['fluorescence_ai_assisted_measurements'][0])
        pixels = np.array(panel.image_data('image-0'), copy=True)
        workspace = window.fluoro_workspace
        workspace.set_panel(panel)
        workspace.set_step(2)
        workspace.measure_mode_buttons['ai_assisted'].click()
        workspace.refresh()
        assert len(workspace.canvas._measurement_overlay_items) > 0

        original = deepcopy(records[0])
        review = ObjectReviewSession(original, pixels.shape[1:])
        review.delete_objects([row['object_id'] for row in review.objects])
        updated = review.updated_record()
        apply_review_to_panel(panel, original, updated)
        window.microscopy_workflow.refresh('fluorescence', updated)

        assert updated['ai_object_count'] == 0
        assert panel.analysis['fluorescence_ai_assisted_measurements'][0] == prior_run
        assert workspace.ai_results_table.rowCount() == len(prior_run['object_table'])
        assert workspace.canvas._measurement_overlay_items == []
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in workspace.ai_summary_stats_table.selectedItems()}
        assert selected_ids == {updated['ai_measurement_uuid']}
        assert np.array_equal(panel.image_data('image-0'), pixels)
    finally:
        panel.dispose()


def test_fluoro_table_select_activates_record_image_before_overlay(window):
    panel = fluorescence_panel()
    try:
        pixels_a = np.array(panel.image_data('image-0'), copy=True)
        count_b = panel.count_channel_objects('image-1', [0], method='manual', manual_value=100, min_area_px=5)[0]
        panel.set_active_image('image-0')
        workspace = window.fluoro_workspace
        workspace.set_panel(panel)
        workspace.set_step(2)
        workspace.measure_mode = 'spot_count'
        workspace.refresh()
        assert panel.data['active_image_uid'] == 'image-0'
        workspace.count_table.blockSignals(True)
        workspace.count_table.setRowCount(1)
        from PyQt6.QtWidgets import QTableWidgetItem
        item = QTableWidgetItem('other-image')
        item.setData(Qt.ItemDataRole.UserRole, count_b['count_uuid'])
        workspace.count_table.setItem(0, 0, item)
        workspace.count_table.selectRow(0)
        workspace.count_table.blockSignals(False)
        workspace._select_count_from_results()
        assert panel.data['active_image_uid'] == 'image-1'
        assert workspace.canvas._measurement_overlay_items != []
        selected = {item.data(Qt.ItemDataRole.UserRole) for item in workspace.count_table.selectedItems()}
        assert count_b['count_uuid'] in selected
        assert np.array_equal(panel.image_data('image-0'), pixels_a)
    finally:
        panel.dispose()
