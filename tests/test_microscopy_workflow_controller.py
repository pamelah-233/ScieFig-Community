import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(app):
    from ui.main_window import MainWindow
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1]/'resources')
    yield window
    window.close()


def test_shared_entry_points_and_actual_current_batch_options(window):
    controller = window.microscopy_workflow
    assert set(controller.review_buttons) == {'fluorescence','brightfield','ihc'}
    assert set(controller.batch_buttons) == {'fluorescence','brightfield','ihc'}
    window.fluoro_workspace.measure_mode = 'spot_count'
    method, options = controller.batch_spec('fluorescence')
    assert method == 'count_channel_objects'
    assert options['algorithm'] == window.fluoro_workspace.count_algorithm_combo.currentData()
    window.data_panel.brightfield_measure_type_combo.setCurrentIndex(0)
    method, options = controller.batch_spec('brightfield')
    assert method == 'measure_bright_round_cells'
    assert options['roi']['mode'] == 'whole_image'


def test_batch_rejects_non_count_fluoro_mode_instead_of_silently_switching(window):
    window.fluoro_workspace.measure_mode = 'line_profile'
    with pytest.raises(ValueError):
        window.microscopy_workflow.batch_spec('fluorescence')


def test_batch_waits_for_existing_module_job(window, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    messages=[]
    monkeypatch.setattr(window._analysis_job_service,'is_busy',lambda key:key=='import:fluoro')
    monkeypatch.setattr(QMessageBox,'information',lambda *args:messages.append(args))
    window.microscopy_workflow.batch('fluorescence')
    assert messages


def test_review_defaults_to_selected_eligible_without_picker(window, monkeypatch):
    from copy import deepcopy
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox
    from tests.test_batch_analysis import fluorescence_panel
    from tests.test_measurement_review import fluorescence_record
    import ui.dialogs.object_review_dialog as review_dialog

    picked = []
    opened = []
    notices = []
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: picked.append(True) or ('', False))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args))

    class FakeDialog:
        def exec(self):
            opened.append(True)
            return QDialog.DialogCode.Rejected

        session = type('S', (), {'original': {}})()
        applied_record = {}

    monkeypatch.setattr(review_dialog.ObjectReviewDialog, 'from_measurement', classmethod(lambda cls, *args, **kwargs: FakeDialog()))
    panel = fluorescence_panel()
    try:
        first = fluorescence_record()
        first['image_uid'] = 'image-0'
        first['ai_measurement_uuid'] = 'run-a'
        second = deepcopy(first)
        second['ai_measurement_uuid'] = 'run-b'
        second['channel_index'] = 1
        panel.analysis['fluorescence_ai_assisted_measurements'] = [first, second]
        panel.images()[0]['fluorescence_ai_assisted_measurements'] = [first, second]
        panel.set_active_image('image-0')
        workspace = window.fluoro_workspace
        workspace.set_panel(panel)
        workspace.set_step(2)
        workspace.refresh()
        for row in range(workspace.ai_summary_stats_table.rowCount()):
            item = workspace.ai_summary_stats_table.item(row, 0)
            if item and str(item.data(Qt.ItemDataRole.UserRole) or '') == 'run-b':
                workspace.ai_summary_stats_table.selectRow(row)
                break
        window.microscopy_workflow.review('fluorescence')
        assert opened
        assert not picked
    finally:
        panel.dispose()


def test_ihc_aggregate_review_stays_no_objects(window, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog, QMessageBox
    from core.models.ihc_panel import IHCPanel

    picked = []
    notices = []
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: picked.append(True) or ('', False))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args[2] if len(args) > 2 else args))
    panel = window._active_ihc_panel()
    panel.data['ihc_images'] = [{'uid': 'image-1', 'source_name': 'a.png', 'measurements': []}]
    panel.data['active_image_uid'] = 'image-1'
    measurement = {'measurement_uuid': 'ihc-agg', 'image_uid': 'image-1', 'results': {'positive_area_percent': 12.0}}
    panel.analysis['ihc_measurements'] = [measurement]
    window.data_panel.ihc_results_table.blockSignals(True)
    window.data_panel.populate_ihc_measurements([measurement], selected_measurement_uuid='ihc-agg')
    window.data_panel.ihc_results_table.blockSignals(False)
    window.microscopy_workflow.review('ihc')
    assert notices
    assert not picked
