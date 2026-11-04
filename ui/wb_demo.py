"""Synthetic WB workflow example in the normal application, without user files."""
from pathlib import Path
import os
import numpy as np
from PyQt6.QtGui import QImage
from core.utils.i18n import tr
from core.constants import WB_STEP_CROP


def prepare_demo(window):
    folder = Path(os.environ.get('LOCALAPPDATA', Path.home()))/'ScieFig/demos/wb-1.2.3'
    folder.mkdir(parents=True, exist_ok=True)
    y,x = np.mgrid[:450,:900]
    rng = np.random.default_rng(123)
    image = 232 + 2*np.sin(x/250) + rng.normal(0,.7,x.shape)
    strengths = ((55,80,120,65,105,145),(105,110,100,115,108,103),(95,100,90,105,95,85))
    names = ('p-ERK', 'ERK', 'GAPDH')
    for cy, values in zip((85,215,345), strengths):
        for cx, strength in zip(np.arange(6)*130+120, values):
            profile = np.exp(-((x-cx)/26)**4/2 - ((y-cy)/5.2)**2/2)
            image -= strength*profile
    pixels = np.ascontiguousarray(np.clip(image,0,255).astype(np.uint8))
    source = folder/'synthetic-wb-full.png'
    qimage = QImage(pixels.data,900,450,pixels.strides[0],QImage.Format.Format_Grayscale8).copy()
    if not qimage.save(str(source)): raise OSError('Could not save WB demo image')
    window.set_module('wb'); panel = window._active_wb_panel()
    panel.raw_source_path = source
    panel.data.update(image_path=str(source), image_data=dict(original_source=source.name, original_format='PNG',
        bit_depth=8,width_px=900,height_px=450), crops=[], band_strips=[dict(strip_id='strip_1',image_path=str(source),
        original_source=source.name,protein_name='Synthetic full blot',crop_roi=dict(x=0,y=0,w=900,h=450),adjustments={})],
        annotations=dict(lane_labels=['Ctrl 1','Ctrl 2','Ctrl 3','Treat 1','Treat 2','Treat 3'],lane_anchors=[],marker_anchors=[]),
        adjustments={}, layout=dict(image_x=0,image_y=0), wb_layout=dict(enabled=False,loading_control_index=2))
    for cy, name in zip((85,215,345),names): panel.set_crop_roi(55,cy-25,780,50,protein=name)
    panel.data['wb_crop_preview'] = True
    d = window.data_panel; d.wb_lane_count_spin.setValue(6)
    d.wb_lane_edit.setText('Ctrl 1, Ctrl 2, Ctrl 3, Treat 1, Treat 2, Treat 3')
    window._refresh_wb_ui(panel); window._populate_wb_density_controls(panel)
    for spin, value in ((d.wb_x_spin,55),(d.wb_y_spin,320),(d.wb_w_spin,780),(d.wb_h_spin,50)):
        spin.setValue(value)
    d.wb_protein_edit.setText('GAPDH')
    window.run_wb_densitometry(); d.set_wb_step(WB_STEP_CROP)
    window.wb_canvas.set_mode('move'); window.wb_canvas.fit_to_canvas()
    window.setWindowTitle(tr('wb_demo_title')); window.statusBar().showMessage(tr('wb_demo_hint'))
    return panel
