from tests.test_project_recovery_ui import (
    ROOT, _app, _attach_recovery, _close_window, _dirty, _wait_idle,
)
from core.models.project import Project
from ui.main_window import MainWindow
import threading


def test_edit_during_write_requires_next_snapshot(tmp_path, monkeypatch):
    app = _app()
    window = MainWindow(resources_dir=ROOT / 'resources')
    controller = _attach_recovery(window, tmp_path)
    release = threading.Event()
    original_write = controller.store.write_snapshot

    def delayed(snapshot):
        release.wait(5)
        return original_write(snapshot)

    monkeypatch.setattr(controller.store, 'write_snapshot', delayed)
    try:
        _dirty(window, 'before')
        controller.maybe_autosave()
        assert controller._write_active
        _dirty(window, 'after')
        release.set()
        assert _wait_idle(app, controller)
        monkeypatch.setattr(controller.store, 'write_snapshot', original_write)
        controller.maybe_autosave()
        assert _wait_idle(app, controller)
        loaded = Project.load_sfig(controller.store.draft_path(window.project.project_uuid))
        try:
            assert loaded.title == 'after'
        finally:
            loaded.dispose()
    finally:
        release.set()
        _wait_idle(app, controller)
        _close_window(app, window)
