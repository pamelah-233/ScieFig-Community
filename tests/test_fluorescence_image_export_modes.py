from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import numpy as np
import tifffile
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from core.models import FluorescencePanel
from core.utils.i18n import set_language, tr
from ui.panels.fluorescence_image_export import (
    COMPOSITION_CHANNELS,
    COMPOSITION_MERGE,
    SOURCE_ORIGINAL,
    SOURCE_OVERLAY,
    FluorescenceImageExportDialog,
    export_fluorescence_images,
    fluorescence_export_path,
    prompt_and_export_fluorescence_image,
    render_fluorescence_export_payloads,
)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def test_unadjusted_export_after_rgb_split_has_all_original_components(tmp_path):
    from core.plotting.fluoro_render import render_fluorescence_qimage
    rgb = np.zeros((20, 24, 3), np.uint8)
    rgb[2:12, 3:13] = [180, 90, 40]
    path = tmp_path / 'rgb.tif'
    tifffile.imwrite(path, rgb, photometric='rgb')
    panel = FluorescencePanel(panel_id='split-initial')
    image = panel.import_image(path)
    panel.force_split_rgb_samples(image['uid'])
    initial = deepcopy(image['channels'])
    panel.update_channel(image['uid'], 1, display_min=50, display_max=100,
                         pseudocolor='#FF00FF')
    payloads = render_fluorescence_export_payloads(
        panel, image, source='unadjusted', composition='both')
    assert len(payloads) == 4
    for (_, exported), channel in zip(payloads[1:], initial):
        assert exported == render_fluorescence_qimage(
            panel.image_data(image['uid']), image, initial,
            view_mode='single', channel=channel)
    assert image['channels'][1]['pseudocolor'] == '#FF00FF'


def _rgb(image: QImage, x: int, y: int) -> tuple[int, int, int]:
    color = image.pixelColor(x, y)
    return color.red(), color.green(), color.blue()


def _write_split_tiff(path: Path) -> Path:
    data = np.zeros((2, 16, 20), dtype=np.uint16)
    data[0, 2:6, 2:6] = 1000
    data[1, 10:14, 14:18] = 1000
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _panel(tmp_path: Path) -> tuple[FluorescencePanel, dict]:
    panel = FluorescencePanel(panel_id="export-modes")
    panel.import_image(_write_split_tiff(tmp_path / "split.tif"))
    image = panel.active_image()
    assert image is not None
    channels = list(image.get("channels", []))
    channels[0]["name"] = "DAPI"
    channels[0]["pseudocolor"] = "#FF0000"
    channels[0]["display_min"] = 0
    channels[0]["display_max"] = 1000
    channels[1]["name"] = "FITC"
    channels[1]["pseudocolor"] = "#00FF00"
    channels[1]["display_min"] = 0
    channels[1]["display_max"] = 1000
    image["channels"] = channels
    image["fluorescence_ai_assisted_measurements"] = [
        {
            "ai_measurement_uuid": "overlay-0",
            "channel_index": 0,
            "ai_object_count": 1,
            "object_table": [
                {
                    "object_id": 1,
                    "contour_points_px": [
                        {"x": 1, "y": 1},
                        {"x": 8, "y": 1},
                        {"x": 8, "y": 8},
                        {"x": 1, "y": 8},
                    ],
                    "bbox_x_min": 1,
                    "bbox_y_min": 1,
                    "bbox_x_max": 8,
                    "bbox_y_max": 8,
                }
            ],
        },
        {
            "ai_measurement_uuid": "overlay-1",
            "channel_index": 1,
            "ai_object_count": 1,
            "object_table": [
                {
                    "object_id": 2,
                    "contour_points_px": [
                        {"x": 0, "y": 0},
                        {"x": 3, "y": 0},
                        {"x": 3, "y": 3},
                        {"x": 0, "y": 3},
                    ],
                    "review_contours_px": [
                        [
                            (14, 10),
                            (18, 10),
                            (18, 14),
                            (14, 14),
                        ]
                    ],
                    "bbox_x_min": 14,
                    "bbox_y_min": 10,
                    "bbox_x_max": 18,
                    "bbox_y_max": 14,
                }
            ],
        },
    ]
    return panel, image


def test_merge_and_per_channel_export_content_names_and_dialog_cancel(tmp_path):
    panel, image = _panel(tmp_path)
    source_h, source_w = panel.image_data(str(image["uid"])).shape[1:]

    merge_payloads = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_MERGE
    )
    channel_payloads = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_CHANNELS
    )
    overlay_merge = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_OVERLAY, composition=COMPOSITION_MERGE
    )
    overlay_channels = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_OVERLAY, composition=COMPOSITION_CHANNELS
    )

    assert len(merge_payloads) == 1
    assert merge_payloads[0][0] == "merge"
    assert (merge_payloads[0][1].width(), merge_payloads[0][1].height()) == (source_w, source_h)
    assert sum(_rgb(merge_payloads[0][1], 3, 3)) > sum(_rgb(merge_payloads[0][1], 0, 0))
    assert sum(_rgb(merge_payloads[0][1], 16, 12)) > sum(_rgb(merge_payloads[0][1], 0, 0))

    tokens = [token for token, _image in channel_payloads]
    assert tokens == ["ch0_DAPI", "ch1_FITC"]
    assert all((exported.width(), exported.height()) == (source_w, source_h) for _token, exported in channel_payloads)
    assert sum(_rgb(channel_payloads[0][1], 3, 3)) > sum(_rgb(channel_payloads[0][1], 16, 12))
    assert sum(_rgb(channel_payloads[1][1], 16, 12)) > sum(_rgb(channel_payloads[1][1], 3, 3))
    assert _rgb(channel_payloads[0][1], 16, 12) != _rgb(channel_payloads[1][1], 16, 12)

    assert _rgb(overlay_merge[0][1], 1, 1) != _rgb(merge_payloads[0][1], 1, 1)
    assert _rgb(overlay_channels[0][1], 1, 1) != _rgb(channel_payloads[0][1], 1, 1)
    assert (overlay_merge[0][1].width(), overlay_merge[0][1].height()) == (source_w, source_h)

    merge_base = tmp_path / "sample.png"
    written_merge = export_fluorescence_images(
        panel, image, merge_base, source=SOURCE_ORIGINAL, composition=COMPOSITION_MERGE
    )
    assert written_merge == [merge_base]
    channel_base = tmp_path / "sample_channels.png"
    written_channels = export_fluorescence_images(
        panel, image, channel_base, source=SOURCE_ORIGINAL, composition=COMPOSITION_CHANNELS
    )
    assert [path.name for path in written_channels] == ["sample_channels_ch0_DAPI.png", "sample_channels_ch1_FITC.png"]
    assert fluorescence_export_path(channel_base, "ch0_DAPI", COMPOSITION_CHANNELS).name == "sample_channels_ch0_DAPI.png"
    assert not (tmp_path / "cancelled.png").exists()


def test_per_channel_overlay_keeps_disjoint_and_reviewed_contours(tmp_path):
    panel, image = _panel(tmp_path)
    original = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_CHANNELS
    )
    overlay = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_OVERLAY, composition=COMPOSITION_CHANNELS
    )
    ch0_original, ch1_original = original[0][1], original[1][1]
    ch0_overlay, ch1_overlay = overlay[0][1], overlay[1][1]
    assert _rgb(ch0_overlay, 1, 1) != _rgb(ch0_original, 1, 1)
    assert _rgb(ch0_overlay, 14, 10) == _rgb(ch0_original, 14, 10)
    assert _rgb(ch1_overlay, 14, 10) != _rgb(ch1_original, 14, 10)
    assert _rgb(ch1_overlay, 1, 1) == _rgb(ch1_original, 1, 1)
    assert _rgb(ch1_overlay, 0, 0) == _rgb(ch1_original, 0, 0)


def test_channel_overwrite_confirms_generated_filenames(tmp_path):
    panel, image = _panel(tmp_path)
    existing = tmp_path / "sample_ch0_DAPI.png"
    existing.write_bytes(b"old-channel")
    asked: list[Path] = []

    def confirm(targets):
        asked.extend(Path(path) for path in targets)
        return False

    written = export_fluorescence_images(
        panel,
        image,
        tmp_path / "sample",
        source=SOURCE_ORIGINAL,
        composition=COMPOSITION_CHANNELS,
        confirm_overwrite=confirm,
    )
    assert written == []
    assert existing.read_bytes() == b"old-channel"
    assert [path.name for path in asked] == ["sample_ch0_DAPI.png", "sample_ch1_FITC.png"]
    assert not (tmp_path / "sample_ch1_FITC.png").exists()


def test_prompt_dialog_cancel_writes_nothing(tmp_path, monkeypatch):
    from ui.panels import fluorescence_image_export as export_mod

    panel, image = _panel(tmp_path)
    monkeypatch.setattr(export_mod, "choose_fluorescence_image_export", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        export_mod.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("file dialog should not open")),
    )
    written = prompt_and_export_fluorescence_image(None, panel, image)
    assert written is None
    assert list(tmp_path.glob("*.png")) == []


def test_export_dialog_cn_en_choice_and_cancel():
    set_language("en")
    dialog = FluorescenceImageExportDialog(has_original=True, has_overlay=True, default_overlay=False)
    dialog.retranslate()
    assert dialog.windowTitle() == tr("export_image_title")
    assert dialog.original_radio.text() == tr("export_current_display")
    assert dialog.overlay_radio.text() == tr("export_current_overlay")
    assert dialog.merge_radio.text() == tr("export_merged_image")
    assert dialog.channels_radio.text() == tr("export_separate_channel_images")
    assert dialog.display_hint.text() == tr("export_image_display_hint")
    assert "lossless" not in dialog.display_hint.text().lower() or "not a lossless" in dialog.display_hint.text().lower()
    set_language("zh")
    dialog.retranslate()
    assert dialog.merge_radio.text() == "合并图"
    assert dialog.channels_radio.text() == "按通道分别导出"
    assert "TIFF" in dialog.display_hint.text()
    dialog.reject()
    assert dialog.exec_choice() is None
    set_language("en")
    accepted = FluorescenceImageExportDialog(has_original=True, has_overlay=True, default_overlay=True)
    accepted.overlay_radio.setChecked(True)
    accepted.channels_radio.setChecked(True)
    accepted.accept()
    assert accepted.exec_choice() == (SOURCE_OVERLAY, COMPOSITION_CHANNELS)
    accepted.close()
    dialog.close()
    _APP.processEvents()


def test_export_current_edit_and_unadjusted_choice_apply_to_merge_and_channels(tmp_path):
    panel,image=_panel(tmp_path)
    image['channels'][0]['display_processing']={'strength':50,'denoise':0,'background_radius':0}
    image['channels'][0]['display_max']=2000
    image['channels'][1]['display_processing']={'strength':-30,'denoise':0}
    before=deepcopy(image);raw=panel.image_data(image['uid']).copy()
    from core.plotting.fluoro_render import render_fluorescence_qimage
    for composition in (COMPOSITION_MERGE,COMPOSITION_CHANNELS):
        edited=render_fluorescence_export_payloads(panel,image,source=SOURCE_ORIGINAL,composition=composition)
        unadjusted=render_fluorescence_export_payloads(panel,image,source='unadjusted',composition=composition)
        assert edited[0][1]!=unadjusted[0][1]
        expected=render_fluorescence_qimage(raw,image,panel._styled_channels_for_render(image['channels'],image),
            view_mode='merge' if composition==COMPOSITION_MERGE else 'single',
            channel=None if composition==COMPOSITION_MERGE else image['channels'][0])
        assert edited[0][1]==expected
    assert image==before
    np.testing.assert_array_equal(raw,panel.image_data(image['uid']))


def test_export_both_compositions_writes_unique_files_and_checks_all_overwrites(tmp_path):
    panel,image=_panel(tmp_path)
    payloads=render_fluorescence_export_payloads(panel,image,source=SOURCE_ORIGINAL,composition='both')
    assert [token for token,_ in payloads]==['merge','ch0_DAPI','ch1_FITC']
    seen=[]
    written=export_fluorescence_images(panel,image,tmp_path/'both.png',source=SOURCE_ORIGINAL,
        composition='both',confirm_overwrite=lambda paths:seen.extend(paths) or True)
    assert [p.name for p in written]==['both_merge.png','both_ch0_DAPI.png','both_ch1_FITC.png']
    assert seen==written and all(p.exists() for p in written)
    assert all(not QImage(str(p)).isNull() for p in written)


def test_export_dialog_exposes_current_edit_and_both_without_measurement_result():
    dialog=FluorescenceImageExportDialog(has_original=True,has_overlay=False)
    assert hasattr(dialog,'unadjusted_radio') and hasattr(dialog,'both_radio')
    dialog.both_radio.click();dialog.accept()
    assert dialog.exec_choice()==(SOURCE_ORIGINAL,'both')
    dialog.unadjusted_radio.click();dialog.accept()
    assert dialog.exec_choice()==('unadjusted','both')
    dialog.close()
