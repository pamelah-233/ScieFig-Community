import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
from core.image_browser import discover_image_files
from ui.panels.image_browser_workspace import ImageBrowserWorkspace

APP=QApplication.instance() or QApplication([])


def test_recursive_discovery_is_optional_and_retains_duplicate_names(tmp_path):
    for relative in ['root.png','a/same.tif','b/same.tif','b/deep/image.JPG','b/no.txt']:
        file=tmp_path/relative;file.parent.mkdir(parents=True,exist_ok=True);file.touch()
    assert [p.name for p in discover_image_files(tmp_path)]==['root.png']
    assert [p.relative_to(tmp_path).as_posix() for p in discover_image_files(tmp_path,recursive=True)]==[
        'a/same.tif','b/deep/image.JPG','b/same.tif','root.png']


def test_checkbox_reloads_folder_and_saved_browser_state(tmp_path):
    for relative in ['a/same.png','b/same.png','root.png']:
        file=tmp_path/relative;file.parent.mkdir(parents=True,exist_ok=True)
        image=QImage(30,20,QImage.Format.Format_RGB32);image.fill(0xffffff);image.save(str(file))
    ws=ImageBrowserWorkspace();ws.open_folder(tmp_path)
    assert ws.thumbnail_list.count()==1
    ws.include_subfolders_checkbox.setChecked(True)
    assert ws.thumbnail_list.count()==3
    assert {ws.thumbnail_list.item(i).text().replace('\\','/') for i in range(3)}=={'root.png','a/same.png','b/same.png'}
    state=ws.serialize_browser_scale_states()
    assert state['include_subfolders'] is True
    other=ImageBrowserWorkspace();other.restore_browser_scale_states(state)
    assert other.include_subfolders_checkbox.isChecked() and other.thumbnail_list.count()==3
    ws.include_subfolders_checkbox.setChecked(False)
    assert ws.thumbnail_list.count()==1
    for obj in (ws,other):obj.thread_pool.waitForDone(3000);obj.close()
    APP.processEvents()


def test_hiding_subfolders_does_not_discard_their_calibration(tmp_path):
    from core.image_browser import ScaleBarState
    path=tmp_path/'child'/'image.png';path.parent.mkdir()
    image=QImage(30,20,QImage.Format.Format_RGB32);image.fill(0xffffff);image.save(str(path))
    ws=ImageBrowserWorkspace();ws.include_subfolders_checkbox.setChecked(True);ws.open_folder(tmp_path)
    key=str(path);ws._scale_bar_states[key]=ScaleBarState(length_um=25,visible=True)
    ws._manual_calibrations[key]={'um_per_px_x':.25,'um_per_px_y':.25}
    ws.include_subfolders_checkbox.setChecked(False)
    state=ws.serialize_browser_scale_states()
    assert key in state['images']
    ws.include_subfolders_checkbox.setChecked(True)
    assert ws._scale_bar_states[key].length_um==25 and ws._scale_bar_states[key].visible
    assert ws._manual_calibrations[key]['um_per_px_x']==.25
    ws.thread_pool.waitForDone(3000);ws.close();APP.processEvents()
