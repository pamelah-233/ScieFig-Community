import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication
from core.utils.i18n import set_language
from ui.controllers.analysis_job_service import AnalysisJobService


class PendingPool:
    def start(self, worker):
        self.worker = worker


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'stale', 'callback_failed'])
def test_job_reports_real_outcome_after_callback(outcome):
    app = QApplication.instance() or QApplication([])
    pool = PendingPool()
    service = AnalysisJobService(thread_pool=pool)
    events = []
    service.job_finished.connect(lambda key, state, error: events.append((state, error)))
    def callback(result):
        if outcome == 'callback_failed':
            raise ValueError('render failed')
    service.start(key='nuclei_fluoro', work=lambda: 1, on_success=callback,
                  on_error=lambda error: None, is_current=lambda: outcome != 'stale')
    assert service.is_busy('nuclei_fluoro') and not events
    if outcome == 'failed':
        service._handle_failure('nuclei_fluoro', ValueError('failed'))
    else:
        service._handle_success('nuclei_fluoro', 1)
    assert events[0][0] == ('failed' if outcome == 'callback_failed' else outcome)
    assert not service.is_busy('nuclei_fluoro')
    service.shutdown()


def test_footer_immediately_shows_running_and_keeps_feedback_scoped_to_image_and_task(tmp_path):
    import numpy as np
    import tifffile
    from ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    set_language('zh')
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    window._analysis_job_service._thread_pool = PendingPool()
    try:
        window.set_module('fluoro')
        panel = window._active_fluoro_panel()
        for index in range(2):
            path = tmp_path / f'{index}.tif'
            tifffile.imwrite(path, np.ones((16, 16), np.uint16))
            panel.import_image(path)
        window.fluoro_workspace.set_panel(panel, refresh=True)
        window.fluoro_workspace.set_step(2)
        window.measurement_tasks['fluoro'].select('dapi')
        window.workflow_ui.sync()
        service = window._analysis_job_service
        service.start(key='nuclei_fluoro', work=lambda: 1, on_success=lambda value: None,
                      on_error=lambda error: None, is_current=lambda: True)
        footer = window.workflow_ui.footers['fluoro']
        assert '正在' in footer.primary.text()
        assert not footer.primary.isEnabled()
        assert not footer.progress.isHidden() and footer.progress.maximum() == 0
        window.workflow_ui.sync()
        assert '正在' in footer.primary.text(), 'workflow updates must not erase busy state'
        saved_uid = panel.data['active_image_uid']
        panel.data['active_image_uid'] = panel.images()[0]['uid']
        window.analysis_feedback.sync()
        assert footer.progress.isHidden()
        panel.data['active_image_uid'] = saved_uid
        window.analysis_feedback.sync()
        assert not footer.progress.isHidden()
        service._handle_success('nuclei_fluoro', 1)
        assert footer.progress.isHidden()
        assert '完成' in footer.job_status.text()
        window.measurement_tasks['fluoro'].select('spot_count')
        window.workflow_ui.sync()
        assert footer.job_status.isHidden()
    finally:
        window._capture_clean_project_baseline()
        window.close()
        app.processEvents()
