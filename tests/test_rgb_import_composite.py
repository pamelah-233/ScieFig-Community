from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QGraphicsPixmapItem

from core.models import FluorescencePanel, Project
from core.plotting.fluoro_render import render_fluorescence_qimage
from core.utils.i18n import set_language, tr
from ui.main_window import MainWindow
from ui.panels.fluorescence_image_export import (
    COMPOSITION_CHANNELS,
    COMPOSITION_MERGE,
    SOURCE_ORIGINAL,
    FluorescenceImageExportDialog,
    render_fluorescence_export_payloads,
)
from ui.panels.fluorescence_workspace import FluorescenceWorkspace
from ui.widgets.fluoro_canvas import FluorescenceCanvas


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

USER_OVERLAY = Path(os.environ.get("SCIEFIG_TEST_OVERLAY", "tests/fixtures/private-overlay-not-provided.png"))
RESOURCES_DIR = Path(__file__).resolve().parents[1] / "resources"


def _qimage_rgba(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = converted.bits()
    ptr.setsize(converted.sizeInBytes())
    return np.frombuffer(ptr, dtype=np.uint8).reshape(converted.height(), converted.width(), 4).copy()


def _write_mixed_rgb_png(path: Path, *, alpha: bool = False) -> Path:
    rgb = np.zeros((24, 32, 4 if alpha else 3), dtype=np.uint8)
    rgb[2:8, 2:10, 0] = 220
    rgb[2:8, 2:10, 1] = 40
    rgb[2:8, 2:10, 2] = 18
    rgb[12:20, 16:28, 0] = 12
    rgb[12:20, 16:28, 1] = 190
    rgb[12:20, 16:28, 2] = 210
    rgb[4:6, 20:26, 0] = 40
    rgb[4:6, 20:26, 1] = 180
    rgb[4:6, 20:26, 2] = 70
    if alpha:
        rgb[:, :, 3] = 255
        rgb[0:4, 0:4, 3] = 80
        Image.fromarray(rgb, mode="RGBA").save(path)
    else:
        Image.fromarray(rgb, mode="RGB").save(path)
    return path


def _write_single_signal_rgb_tiff(path: Path) -> Path:
    data = np.zeros((16, 20, 3), dtype=np.uint8)
    data[4:12, 5:14, 0] = 186
    tifffile.imwrite(path, data, metadata={"axes": "YXS"}, photometric="rgb")
    return path


def _write_count_rgba_png(path: Path) -> Path:
    rgba = np.zeros((32, 32, 4), dtype=np.uint8)
    rgba[:, :, 3] = 255
    rgba[4:8, 4:8, 0] = 255
    rgba[20:24, 20:24, 2] = 255
    rgba[12:16, 12:16, 0] = 255
    rgba[12:16, 12:16, 1] = 255
    rgba[12:16, 12:16, 2] = 255
    rgba[12:16, 12:16, 3] = 0
    Image.fromarray(rgba, mode="RGBA").save(path)
    return path


def _write_cyx_tiff(path: Path, channels: int) -> Path:
    data = np.zeros((channels, 16, 20), dtype=np.uint16)
    for index in range(channels):
        data[index, 2 + index : 8 + index, 3 + index : 9 + index] = 1000 + index * 400
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _source_rgba(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA"))


def _assert_original_rgb(rendered: QImage, source_rgba: np.ndarray) -> None:
    actual = _qimage_rgba(rendered)
    assert actual.shape[:2] == source_rgba.shape[:2]
    assert np.array_equal(actual[:, :, :3], source_rgba[:, :, :3])
    if int(source_rgba.shape[2]) >= 4:
        assert np.array_equal(actual[:, :, 3], source_rgba[:, :, 3])


def _wait_for_import(window: MainWindow, timeout: float = 15.0) -> None:
    app = QApplication.instance()
    assert app is not None
    deadline = time.monotonic() + timeout
    while window._analysis_job_service.is_busy("import:fluoro"):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for fluoro import")
        time.sleep(0.005)
    app.processEvents()


def _close_window(window: MainWindow) -> None:
    window._clean_project_fingerprint = window._project_fingerprint()
    window.close()
    window.deleteLater()
    _APP.processEvents()


def test_mixed_rgb_and_rgba_default_to_original_composite(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureRGB")
    rgb_path = _write_mixed_rgb_png(tmp_path / "mixed.png")
    rgba_path = _write_mixed_rgb_png(tmp_path / "mixed_alpha.png", alpha=True)

    rgb_image = panel.import_image(rgb_path)
    rgb_uid = str(rgb_image["uid"])
    assert rgb_image["channel_interpretation"]["mode"] == "rgb_composite"
    assert [channel["name"] for channel in rgb_image["channels"]] == ["Combined intensity"]
    assert "Alpha" not in [channel["name"] for channel in rgb_image["channels"]]
    assert rgb_image["view_mode"] == "merge"
    assert rgb_image["layout_mode"] == "merge_only"
    assert panel.image_data(rgb_uid).shape[0] in {3, 4}
    _assert_original_rgb(panel._render_image_pixels(rgb_image), _source_rgba(rgb_path))

    panel.style["preset"] = "Cell"
    _assert_original_rgb(panel._render_image_pixels(rgb_image), _source_rgba(rgb_path))
    panel.style["preset"] = "Science"
    _assert_original_rgb(panel._render_image_pixels(rgb_image), _source_rgba(rgb_path))
    panel.style["preset"] = "Nature"
    _assert_original_rgb(panel._render_image_pixels(rgb_image), _source_rgba(rgb_path))

    rgba_image = panel.import_image(rgba_path)
    assert rgba_image["channel_interpretation"]["mode"] == "rgb_composite"
    assert [channel["name"] for channel in rgba_image["channels"]] == ["Combined intensity"]
    _assert_original_rgb(panel._render_image_pixels(rgba_image), _source_rgba(rgba_path))
    with pytest.raises(ValueError, match="cannot be split"):
        panel.set_view_mode(str(rgba_image["uid"]), "split")


@pytest.mark.skipif(not USER_OVERLAY.is_file(), reason="user overlay.png sample is not present")
def test_user_overlay_png_keeps_original_rgb_and_size() -> None:
    panel = FluorescencePanel(panel_id="FigureOverlay")
    image = panel.import_image(USER_OVERLAY)
    source = _source_rgba(USER_OVERLAY)
    rendered = panel._render_image_pixels(image)
    assert (rendered.width(), rendered.height()) == (source.shape[1], source.shape[0])
    assert image["channel_interpretation"]["mode"] == "rgb_composite"
    assert len(image["channels"]) == 1
    _assert_original_rgb(rendered, source)
    panel.style["preset"] = "Cell"
    _assert_original_rgb(panel._render_image_pixels(image), source)


def test_force_split_is_exactly_rgb_components_and_restore_repeats(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureRGB")
    path = _write_mixed_rgb_png(tmp_path / "toggle.png", alpha=True)
    source = _source_rgba(path)
    image = panel.import_image(path)
    uid = str(image["uid"])
    measurements = [{"measurement_uuid": "keep-me", "channel_index": 0}]
    image["measurements"] = list(measurements)

    first = panel.force_split_rgb_samples(uid)
    names = [channel["name"] for channel in first["channels"]]
    assert names == ["Red", "Green", "Blue"]
    assert "Alpha" not in names
    assert len(first["channels"]) == 3
    assert first["channel_interpretation"]["mode"] == "forced_rgb_split"
    assert first["view_mode"] == "split"
    data = panel.image_data(uid)
    assert data.shape[0] >= 3
    assert np.array_equal(np.stack([data[0], data[1], data[2]], axis=-1), source[:, :, :3])
    merge = render_fluorescence_qimage(data, first, list(first["channels"]), view_mode="merge")
    _assert_original_rgb(merge, source)
    assert image["measurements"] == measurements

    restored = panel.restore_auto_detected_rgb_signal(uid)
    assert restored["channel_interpretation"]["mode"] == "rgb_composite"
    assert [channel["name"] for channel in restored["channels"]] == ["Combined intensity"]
    _assert_original_rgb(panel._render_image_pixels(restored), source)
    assert restored["measurements"] == measurements

    panel.force_split_rgb_samples(uid)
    panel.restore_auto_detected_rgb_signal(uid)
    panel.force_split_rgb_samples(uid)
    again = panel.restore_auto_detected_rgb_signal(uid)
    assert len(again["channels"]) == 1
    assert again["channel_interpretation"]["mode"] == "rgb_composite"
    _assert_original_rgb(panel._render_image_pixels(again), source)


def test_native_cyx_and_microscopy_suffixes_are_not_rgb_composite(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureNative")
    three = panel.import_image(_write_cyx_tiff(tmp_path / "native3.tif", 3))
    four = panel.import_image(_write_cyx_tiff(tmp_path / "native4.tif", 4))
    assert three["channel_interpretation"]["mode"] == "native_channels"
    assert four["channel_interpretation"]["mode"] == "native_channels"
    assert len(three["channels"]) == 3
    assert len(four["channels"]) == 4
    assert three["view_mode"] == "merge"
    assert three["layout_mode"] == "merge_split"

    fake_czi = np.zeros((3, 8, 8), dtype=np.uint16)
    fake_nd2 = np.zeros((4, 8, 8), dtype=np.uint16)
    _, _, czi_mode = panel._interpret_imported_rgb_samples(Path("scene.czi"), fake_czi, ["Ch1", "Ch2", "Ch3"])
    _, _, nd2_mode = panel._interpret_imported_rgb_samples(Path("stack.nd2"), fake_nd2, ["A", "B", "C", "D"])
    assert czi_mode["mode"] == "native_channels"
    assert nd2_mode["mode"] == "native_channels"

    rgb_tiff = panel.import_image(_write_single_signal_rgb_tiff(tmp_path / "rgb_photo.tif"))
    assert rgb_tiff["channel_interpretation"]["mode"] == "rgb_composite"
    assert len(rgb_tiff["channels"]) == 1


def test_shared_render_export_and_canvas_use_original_rgb(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureRGB")
    path = _write_mixed_rgb_png(tmp_path / "shared.png", alpha=True)
    source = _source_rgba(path)
    image = panel.import_image(path)
    data = panel.image_data(str(image["uid"]))
    panel_pixels = panel._render_image_pixels(image)
    shared = render_fluorescence_qimage(data, image, list(image["channels"]))
    export_payloads = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_MERGE
    )
    _assert_original_rgb(panel_pixels, source)
    _assert_original_rgb(shared, source)
    _assert_original_rgb(export_payloads[0][1], source)

    channel_payloads = render_fluorescence_export_payloads(
        panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_CHANNELS
    )
    assert len(channel_payloads) == 1
    assert all("alpha" not in token.lower() for token, _image in channel_payloads)

    dialog = FluorescenceImageExportDialog(allow_separate_channels=False)
    assert dialog.merge_radio.isChecked()
    assert not dialog.channels_radio.isEnabled()

    canvas = FluorescenceCanvas()
    canvas.set_panel(panel)
    canvas.refresh()
    pixmap_item = next(item for item in canvas.scene.items() if isinstance(item, QGraphicsPixmapItem))
    preview = pixmap_item.pixmap().toImage()
    sample = preview.pixelColor(max(1, preview.width() // 4), max(1, preview.height() // 4))
    assert sample.red() > sample.blue()


def test_mainwindow_async_import_button_and_inverse(tmp_path: Path) -> None:
    path = _write_mixed_rgb_png(tmp_path / "ui_mixed.png", alpha=True)
    source = _source_rgba(path)
    window = MainWindow(resources_dir=RESOURCES_DIR)
    window.set_module("fluoro")
    window.import_fluorescence_images([str(path)])
    _wait_for_import(window)
    workspace = window.fluoro_workspace
    panel = window._active_fluoro_panel()
    image = panel.active_image()
    assert image is not None
    assert image["channel_interpretation"]["mode"] == "rgb_composite"
    assert len(workspace._channel_widgets) <= 1
    assert not workspace.view_mode_combo.isEnabled()
    assert workspace.force_split_rgb_button.isEnabled()
    _assert_original_rgb(panel._render_image_pixels(image), source)

    workspace.force_split_rgb_button.click()
    _APP.processEvents()
    split = panel.active_image()
    assert split is not None
    assert [channel["name"] for channel in split["channels"]] == ["Red", "Green", "Blue"]
    assert workspace.force_split_rgb_button.text() == tr("restore_auto_detection")
    assert workspace.view_mode_combo.isEnabled()

    workspace.force_split_rgb_button.click()
    _APP.processEvents()
    restored = panel.active_image()
    assert restored is not None
    assert restored["channel_interpretation"]["mode"] == "rgb_composite"
    _assert_original_rgb(panel._render_image_pixels(restored), source)
    assert workspace.force_split_rgb_button.text() == tr("force_split_rgb")
    _close_window(window)


def test_roundtrip_cache_rebuild_and_combined_intensity_count(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureRGB")
    path = _write_mixed_rgb_png(tmp_path / "count.png")
    source = _source_rgba(path)
    image = panel.import_image(path)
    uid = str(image["uid"])
    expected = panel.image_data(uid).copy()
    counts = panel.count_channel_objects(uid, [0], method="otsu", min_area_px=4)
    assert counts
    assert counts[0]["channel_name"] == "Combined intensity"
    assert int(counts[0]["count"]) >= 1
    plane = np.max(expected[:3], axis=0)
    assert plane.shape == expected.shape[1:]

    panel._image_cache.pop(uid, None)
    rebuilt = panel.image_data(uid)
    assert rebuilt is not None
    assert np.array_equal(rebuilt, expected)
    _assert_original_rgb(panel._render_image_pixels(panel.active_image()), source)

    project = Project(title="RGB composite roundtrip")
    project.add_panel(panel)
    archive = tmp_path / "rgb_composite.sfig"
    project.save_sfig(archive)
    path.unlink()
    restored = Project.load_sfig(archive)
    restored_panel = next(item for item in restored.panels if isinstance(item, FluorescencePanel))
    restored_image = restored_panel.active_image()
    assert restored_image is not None
    assert restored_image["channel_interpretation"]["mode"] == "rgb_composite"
    assert np.array_equal(restored_panel.image_data(uid), expected)
    _assert_original_rgb(restored_panel._render_image_pixels(restored_image), source)


def test_legacy_auto_single_signal_stays_readable_and_restore_is_sensible(tmp_path: Path) -> None:
    path = _write_single_signal_rgb_tiff(tmp_path / "legacy_red.tif")
    panel = FluorescencePanel(panel_id="FigureLegacy")
    image = panel.import_image(path)
    uid = str(image["uid"])
    image["channel_interpretation"] = {
        "mode": "auto_single_signal",
        "source_sample_count": 3,
        "source_sample_indices": [0],
    }
    image["channels"] = image["channels"][:1]
    image["view_mode"] = "single"
    image["layout_mode"] = "merge_only"
    panel._cache_image_array(uid, np.max(panel.image_data(uid)[:3], axis=0, keepdims=True))
    loaded = panel.image_data(uid)
    assert loaded is not None
    assert loaded.shape[0] == 1
    forced = panel.force_split_rgb_samples(uid)
    assert len(forced["channels"]) == 3
    restored = panel.restore_auto_detected_rgb_signal(uid)
    assert restored["channel_interpretation"]["mode"] == "rgb_composite"
    assert len(restored["channels"]) == 1


def test_force_split_wording_explains_components_are_not_biological_channels() -> None:
    set_language("en")
    assert "biological" in tr("force_split_rgb_tooltip").lower() or "not recovered" in tr("force_split_rgb_tooltip").lower()
    set_language("zh")
    assert "生物" in tr("force_split_rgb_tooltip") or "不是" in tr("force_split_rgb_tooltip")
    set_language("en")


def test_explicit_split_merge_respects_manual_edits_and_restore(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="manual-components")
    path = _write_mixed_rgb_png(tmp_path / "manual.png", alpha=True)
    source = _source_rgba(path)
    image = panel.import_image(path)
    uid = image["uid"]
    panel.force_split_rgb_samples(uid)
    image = panel.active_image()
    before = render_fluorescence_export_payloads(panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_MERGE)[0][1]
    _assert_original_rgb(before, source)
    panel.update_channel(uid,0,visible=False)
    edited = render_fluorescence_export_payloads(panel, image, source=SOURCE_ORIGINAL, composition=COMPOSITION_MERGE)[0][1]
    values = _qimage_rgba(edited)
    assert values[5,5,0] == 0
    assert values[5,5,3] == source[5,5,3]
    assert not np.array_equal(values,source)
    panel.restore_auto_detected_rgb_signal(uid)
    _assert_original_rgb(panel._render_image_pixels(panel.active_image()),source)


def test_rgb_interpretation_change_marks_saved_analysis_stale(tmp_path: Path) -> None:
    from core.measurement_freshness import result_freshness
    panel = FluorescencePanel(panel_id="freshness")
    image = panel.import_image(_write_mixed_rgb_png(tmp_path/"freshness.png"))
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    before = workspace._current_analysis_inputs_for("roi-intensity")
    panel.force_split_rgb_samples(image["uid"])
    workspace.refresh()
    after = workspace._current_analysis_inputs_for("roi-intensity")
    assert result_freshness(before,after) == "stale"
    panel.restore_auto_detected_rgb_signal(image["uid"])
    workspace.refresh()
    assert workspace._current_analysis_inputs_for("roi-intensity") == before
    workspace.close()


def test_composite_rejects_merge_split_and_hides_display_phantoms(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureRGB")
    image = panel.import_image(_write_mixed_rgb_png(tmp_path / "layout.png", alpha=True))
    uid = str(image["uid"])
    with pytest.raises(ValueError, match="split"):
        panel.set_layout_mode(uid, "merge_split")
    panel.set_layout_mode(uid, "batch_grid")
    assert image["layout_mode"] == "batch_grid"
    panel.set_layout_mode(uid, "custom")
    assert image["layout_mode"] == "custom"
    panel.set_layout_mode(uid, "merge_only")
    assert image["layout_mode"] == "merge_only"

    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    assert workspace._layout_scale_bar_target_ids(panel.active_image(), False) == ["merge_only:merge"]
    assert workspace._channel_widgets == []
    hint_texts = [widget.text() for widget in workspace.channel_controls.findChildren(workspace.channel_layout.itemAt(0).widget().__class__) if hasattr(widget, "text")]
    assert any(tr("rgb_composite_mode_hint") in str(text) for text in hint_texts)
    assert workspace.measure_channel_combo.findText(tr("combined_intensity")) >= 0
    merge_split_row = workspace.layout_mode_combo.findData("merge_split")
    assert not workspace.layout_mode_combo.model().item(merge_split_row).isEnabled()
    for value in ("merge_only", "batch_grid", "custom"):
        row = workspace.layout_mode_combo.findData(value)
        assert workspace.layout_mode_combo.model().item(row).isEnabled()

    panel.force_split_rgb_samples(uid)
    workspace.refresh()
    assert workspace.layout_mode_combo.model().item(merge_split_row).isEnabled()
    assert len(workspace._channel_widgets) == 3
    assert workspace._layout_scale_bar_target_ids(panel.active_image(), False) == [
        "merge_split:merge",
        "merge_split:channel:0",
        "merge_split:channel:1",
        "merge_split:channel:2",
    ]
    workspace.close()
    workspace.deleteLater()
    _APP.processEvents()


def test_rgb_and_native_tiff_save_roundtrip(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureRGB")
    rgba_path = _write_mixed_rgb_png(tmp_path / "save_rgba.png", alpha=True)
    source = _source_rgba(rgba_path)
    rgb_image = panel.import_image(rgba_path)
    rgb_image["metadata"]["pixel_size_um"] = 0.25
    rgb_image["metadata"]["pixel_size_x_um"] = 0.25
    rgb_image["metadata"]["pixel_size_y_um"] = 0.25
    rgb_saved = panel.save_image_data_as_ome_tiff(str(rgb_image["uid"]), tmp_path / "saved_rgb.ome.tif")
    with tifffile.TiffFile(rgb_saved) as tif:
        page = tif.pages[0]
        photometric = str(getattr(page.photometric, "name", page.photometric)).upper()
        assert int(page.samplesperpixel) == 4
        assert "RGB" in photometric
        assert "MINISBLACK" not in photometric
    reimported = panel.import_image(rgb_saved)
    assert reimported["channel_interpretation"]["mode"] == "rgb_composite"
    assert len(reimported["channels"]) == 1
    assert panel.image_data(str(reimported["uid"])).shape[0] == 4
    _assert_original_rgb(panel._render_image_pixels(reimported), source)
    assert float(reimported["metadata"].get("pixel_size_um") or 0) == pytest.approx(0.25)

    native = panel.import_image(_write_cyx_tiff(tmp_path / "native4.tif", 4))
    native_data = panel.image_data(str(native["uid"])).copy()
    native_saved = panel.save_image_data_as_ome_tiff(str(native["uid"]), tmp_path / "saved_native.ome.tif")
    with tifffile.TiffFile(native_saved) as tif:
        page = tif.pages[0]
        photometric = str(getattr(page.photometric, "name", page.photometric)).upper()
        assert int(page.samplesperpixel) == 1
        assert "MINISBLACK" in photometric
    native_again = panel.import_image(native_saved)
    assert native_again["channel_interpretation"]["mode"] == "native_channels"
    assert len(native_again["channels"]) == 4
    assert np.array_equal(panel.image_data(str(native_again["uid"])), native_data)


def test_combined_intensity_counts_red_and_blue_not_transparent(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="FigureRGB")
    path = _write_count_rgba_png(tmp_path / "count_spots.png")
    image = panel.import_image(path)
    uid = str(image["uid"])
    cached = panel.image_data(uid).copy()
    assert int(cached[0, 13, 13]) == 255
    assert int(cached[3, 13, 13]) == 0
    counts = panel.count_channel_objects(uid, [0], method="otsu", min_area_px=4)
    assert int(counts[0]["count"]) == 2
    assert counts[0]["channel_name"] == "Combined intensity"
    assert np.array_equal(panel.image_data(uid), cached)

    forced = panel.force_split_rgb_samples(uid)
    for preset in ("Nature", "Cell"):
        panel.style["preset"] = preset
        payloads = render_fluorescence_export_payloads(
            panel, forced, source=SOURCE_ORIGINAL, composition=COMPOSITION_CHANNELS
        )
        by_token = {token: image for token, image in payloads}
        assert len(payloads) == 3
        red = _qimage_rgba(by_token["ch0_Red"])
        blue = _qimage_rgba(by_token["ch2_Blue"])
        assert red[6, 6, 0] > 200 and red[6, 6, 1] < 40 and red[6, 6, 2] < 40
        assert red[22, 22, 0] < 40 and red[22, 22, 1] < 40 and red[22, 22, 2] < 40
        assert blue[22, 22, 2] > 200 and blue[22, 22, 0] < 40 and blue[22, 22, 1] < 40
        assert int(red[13, 13, 3]) == 0
        assert int(blue[13, 13, 3]) == 0
        assert int(red[6, 6, 3]) == 255
