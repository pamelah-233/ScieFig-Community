"""The compact style UI keeps real source/edit/export behavior and stable geometry."""
import sys
from copy import deepcopy
from pathlib import Path
from time import monotonic

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog

from main import load_qss
from ui.platform_support import configure_application
from ui.widgets.style_lab_workspace import StyleLabWorkspace

pytestmark = pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='shared studio style workbench')
ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=45):
    end = monotonic() + timeout
    while not predicate() and monotonic() < end:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from ui.local_style_extraction import app as style_app
    configure_application(APP)
    APP.setStyleSheet(load_qss(ROOT / 'resources'))
    monkeypatch.setattr(style_app, 'CACHE_DIR', tmp_path / 'cache')
    w = StyleLabWorkspace(preset_registry_path=tmp_path / 'styles.json')
    w.resize(944, 650)
    w.show()
    w.local_extraction._confirm_replace = lambda *args: True
    QTest.qWait(60)
    yield w
    w.shutdown_local_extraction()
    w.close()


def test_two_rows_all_actions_and_stable_work_area(workspace):
    e = workspace.local_extraction
    size = workspace.size()
    actions = [e.btn_open, e.btn_extract, e.name_edit, e.btn_library, e.btn_apply,
               e.btn_load, e.btn_saved, e.btn_export, e.btn_save, e.btn_cancel]
    for action in actions:
        assert action.isVisible()
        rect = action.rect()
        rect.moveTopLeft(action.mapTo(workspace, QPoint(0, 0)))
        assert workspace.rect().contains(rect), (action.objectName(), getattr(action, 'text', lambda: '')(), rect)
    assert e.top.height() <= 42
    source_y = e.source_board.canvas.mapTo(workspace, QPoint(0, 0)).y()
    preview_y = e.preview_board.view_host.mapTo(workspace, QPoint(0, 0)).y()
    assert abs(source_y - preview_y) <= 1
    assert source_y <= 80
    assert e.source_board.page_spin.isVisible()
    assert e.source_board.region_combo.isVisible()
    before = e.preview_board.view_host.geometry()
    grouped = [key for _, keys in e.studio_ui.GROUPS for key in keys]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(e.token_editor.rows)
    for index, (_, keys) in enumerate(e.studio_ui.GROUPS):
        e.studio_ui.tabs.button(index).click()
        QTest.qWait(10)
        assert all(e.token_editor.rows[key].isVisible() for key in keys)
        assert e.preview_board.view_host.geometry() == before
        assert workspace.size() == size
    for view in (e.preview_board.btn_view_base, e.preview_board.btn_view_compare, e.preview_board.btn_view_styled):
        view.click()
        QTest.qWait(10)
        assert workspace.size() == size
    e.btn_b.click()
    e.btn_stage2.click()
    QTest.qWait(20)
    assert e.preview_board.isVisible() and e.token_editor.isVisible()
    e.btn_stage1.click()
    QTest.qWait(20)
    assert e.source_board.isVisible() and workspace.size() == size
    e.btn_a.click()
    assert e.source_board.isVisible() and e.preview_board.isVisible() and e.token_editor.isVisible()


def test_real_pdf_extract_edit_reset_save_load_and_export(workspace, tmp_path, monkeypatch):
    from core.style_preset_resolver import clear_runtime_active_style_preset
    e = workspace.local_extraction
    source = ROOT / 'tests/fixtures/local_style_extraction/legend10.pdf'
    e._open_path(str(source))
    wait_until(lambda: not e._jobs)
    assert e.source_path == str(source)
    assert not e.source_board.canvas._pixmap.isNull()
    e.btn_extract.click()
    wait_until(lambda: not e._jobs and not e._preview_timer.isActive())
    assert e.draft is not None, e.action_msg.text()
    assert len(e.draft['tokens']['series_colors']['value']) >= 10
    assert Path(e.preview_paths['styled_png']).is_file()
    assert Path(e.preview_paths['styled_svg']).is_file()
    original = deepcopy(e.draft)
    e.studio_ui.tabs.button(1).click()
    row = e.token_editor.rows['font_size_pt']
    row.editor.setValue(13.25)
    assert e.draft['tokens']['font_size_pt']['status'] == 'user_edited'
    assert e.draft['tokens']['font_size_pt']['value'] == 13.25
    row.reset_btn.click()
    assert e.draft['tokens']['font_size_pt']['value'] == original['tokens']['font_size_pt']['value']
    row.editor.setValue(12.5)
    wait_until(lambda: not e._jobs and not e._preview_timer.isActive())
    assert e.draft['tokens']['series_colors'] == original['tokens']['series_colors']
    e.name_edit.setText('Native style QA')
    e.btn_library.click()
    record = workspace._preset_registry.list_presets()[0]
    assert record.display_name == 'Native style QA'
    assert record.extraction_profile['local_extraction']['tokens']['font_size_pt']['value'] == 12.5
    e.btn_apply.click()
    clear_runtime_active_style_preset()
    target = tmp_path / 'style.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(target), ''))
    e.btn_save.click()
    assert target.is_file()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(target), ''))
    e.btn_load.click()
    wait_until(lambda: not e._jobs and not e._preview_timer.isActive())
    assert e.draft['name'] == 'Native style QA'
    assert e.draft['tokens']['font_size_pt']['value'] == 12.5
    for kind in ('png', 'svg'):
        output = tmp_path / ('styled.' + kind)
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, p=output, **kw: (str(p), ''))
        e._export(kind)
        assert output.read_bytes() == Path(e.preview_paths['styled_' + kind]).read_bytes()


def test_narrow_toolbars_keep_complete_click_targets_and_restore_labels(workspace):
    e=workspace.local_extraction
    for width in (1100,820,944,820,1100):
        workspace.resize(width,650)
        QTest.qWait(30)
        assert workspace.width()==width
        for bar,buttons in ((e.studio_ui.action_row,e.studio_ui.action_buttons),
                            (e.studio_ui.preview_header,e.studio_ui.preview_buttons)):
            for button,label in buttons:
                rect=button.rect();rect.moveTopLeft(button.mapTo(bar,QPoint()))
                assert bar.rect().contains(rect),(width,label,rect,bar.rect())
                assert button.width()>=button.minimumSizeHint().width(),(label,button.width(),button.minimumSizeHint())
                assert button.toolTip() and button.accessibleName()
                assert button.text()==('' if button.property('styleIconOnly') else label)
        for board,controls in ((e.source_board,e.source_board.canvas.zoom_controls),(e.preview_board,e.preview_board.zoom_controls)):
            workspace.retranslate()
            assert not controls.fit_button.text()
            assert controls.fit_button.toolTip() and not controls.fit_button.icon().isNull()
            header=e.studio_ui.source_header if board is e.source_board else e.studio_ui.preview_header
            for button in (controls.fit_button,controls.zoom_out_button,controls.zoom_in_button):
                rect=button.rect();rect.moveTopLeft(button.mapTo(header,QPoint()))
                assert header.rect().contains(rect),(width,rect,header.rect())
        assert e.btn_open.text()=='打开来源' if width==1100 else True
