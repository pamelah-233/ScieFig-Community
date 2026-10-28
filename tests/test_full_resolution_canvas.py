from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from PIL import Image
from PyQt6.QtWidgets import QApplication


_TEST_APP = QApplication.instance() or QApplication([])

from PyQt6.QtCore import QPointF, QSize  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QGraphicsPixmapItem, QLabel  # noqa: E402

from core.models import BrightfieldPanel, ExternalImagePanel, FluorescencePanel, IHCPanel, Project, WesternBlotPanel


NATIVE_SIZE = (1600, 1200)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_raster(path: Path, size: tuple[int, int] = NATIVE_SIZE) -> Path:
    width, height = size
    y, x = np.indices((height, width))
    array = np.zeros((height, width, 3), dtype=np.uint8)
    array[..., 0] = (x % 251).astype(np.uint8)
    array[..., 1] = (y % 241).astype(np.uint8)
    array[..., 2] = ((x // 17 + y // 19) % 2 * 255).astype(np.uint8)
    image = Image.fromarray(array, mode="RGB")
    save_kwargs = {"lossless": True} if path.suffix.lower() == ".webp" else {}
    image.save(path, **save_kwargs)
    return path


def _write_fluorescence_tiff(path: Path) -> Path:
    data = np.zeros((2, NATIVE_SIZE[1], NATIVE_SIZE[0]), dtype=np.uint16)
    y, x = np.indices(data.shape[1:])
    data[0] = ((x * 13 + y * 7) % 4096).astype(np.uint16)
    data[1] = ((x * 5 + y * 17) % 4096).astype(np.uint16)
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _write_ome_tiff(path: Path) -> Path:
    data = np.zeros((2, NATIVE_SIZE[1], NATIVE_SIZE[0]), dtype=np.uint16)
    data[0, 200:1000, 100:700] = 4095
    data[1, 200:1000, 900:1500] = 4095
    tifffile.imwrite(path, data, metadata={"axes": "CYX", "Channel": {"Name": ["DAPI", "GFP"]}})
    return path


def _write_qimage(path: Path, width: int = 1600, height: int = 800) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#efefef"))
    assert image.save(str(path))
    return path


def _visible_pixmap_items(canvas) -> list[QGraphicsPixmapItem]:
    return [item for item in canvas.scene.items() if isinstance(item, QGraphicsPixmapItem) and not item.pixmap().isNull()]


def test_fluorescence_merge_single_split_layout_keep_native_backing_and_mapper(tmp_path: Path) -> None:
    _app()
    from ui.widgets.fluoro_canvas import FluorescenceCanvas, ImageCoordinateMapper

    panel = FluorescencePanel(panel_id="full-resolution-fluorescence")
    image_path = _write_fluorescence_tiff(tmp_path / "full-resolution-fluorescence.tif")
    try:
        image = panel.import_image(image_path)
        image["layout_mode"] = "merge_split"
        canvas = FluorescenceCanvas()
        canvas.set_panel(panel)

        for view_mode in ("merge", "single", "split"):
            image["view_mode"] = view_mode
            canvas.set_step(1)
            pixmaps = _visible_pixmap_items(canvas)
            assert pixmaps
            assert all(item.pixmap().size() == QSize(*NATIVE_SIZE) for item in pixmaps)
            assert any(item.scale() < 1.0 for item in pixmaps)

        image["view_mode"] = "merge"
        canvas.set_step(1)
        mapper = ImageCoordinateMapper(canvas._image_items[0], (NATIVE_SIZE[1], NATIVE_SIZE[0]))
        scene_point = mapper.image_px_to_scene_pos((800.0, 600.0))
        image_point = mapper.scene_pos_to_image_px(scene_point)
        assert image_point == pytest.approx((800.0, 600.0), abs=1.0)

        canvas.set_step(4)
        layout_pixmaps = _visible_pixmap_items(canvas)
        assert layout_pixmaps
        assert all(item.pixmap().size() == QSize(*NATIVE_SIZE) for item in layout_pixmaps)
        assert any(
            item.parentItem() is not None
            and item.parentItem().rect().width() < item.pixmap().width()
            for item in layout_pixmaps
        )
    finally:
        image_path.unlink(missing_ok=True)


def test_wb_layout_strip_backing_remains_native_crop_dimensions(tmp_path: Path) -> None:
    _app()
    from ui.widgets.wb_canvas import WBCanvas

    source_path = _write_qimage(tmp_path / "full-resolution-wb.png")
    panel = WesternBlotPanel(panel_id="full-resolution-wb")
    panel.import_full_blot(source_path)
    panel.set_crop_roi(0, 0, 400, 200, protein="A")
    panel.set_crop_roi(0, 250, 800, 200, protein="B")
    panel.set_band_configuration(
        [{"protein": "A", "mw_kda": "50"}, {"protein": "B", "mw_kda": "25"}],
        ["Ctrl", "Drug"],
    )

    canvas = WBCanvas()
    canvas.set_panel(panel)
    pixmaps = [item for item in canvas._group.childItems() if isinstance(item, QGraphicsPixmapItem)]
    assert sorted((item.pixmap().width(), item.pixmap().height()) for item in pixmaps) == [(400, 200), (800, 200)]
    assert any(item.scale() > 1.0 for item in pixmaps)


@pytest.mark.parametrize("suffix", [".png", ".tif", ".jpg", ".bmp", ".webp", ".ome.tif", ".ome.tiff"])
def test_composer_external_raster_canvas_is_native_but_thumbnail_is_bounded(tmp_path: Path, suffix: str) -> None:
    _app()
    from ui.widgets.composer_canvas import ComposerCanvas

    source_path = (
        _write_ome_tiff(tmp_path / f"source{suffix}")
        if suffix in {".ome.tif", ".ome.tiff"}
        else _write_raster(tmp_path / f"source{suffix}")
    )
    panel = ExternalImagePanel(panel_id=f"external-{suffix[1:]}")
    panel.import_image(source_path)
    canvas = ComposerCanvas()

    pixmap = canvas._panel_pixmap(panel, 820, 615)
    thumbnail = canvas.panel_thumbnail(panel)

    assert pixmap is not None
    assert pixmap.size() == QSize(*NATIVE_SIZE)
    assert thumbnail is not None
    assert thumbnail.width() <= 96
    assert thumbnail.height() <= 64
    assert len(canvas._full_raster_cache) <= canvas.FULL_RASTER_CACHE_LIMIT
    assert len(canvas._pixmap_cache) <= canvas.PREVIEW_CACHE_LIMIT


def test_composer_native_raster_cache_is_bounded_without_scaling_active_sources(tmp_path: Path) -> None:
    _app()
    from ui.widgets.composer_canvas import ComposerCanvas

    canvas = ComposerCanvas()
    for index in range(canvas.FULL_RASTER_CACHE_LIMIT + 2):
        source_path = _write_raster(tmp_path / f"cache-source-{index}.png")
        panel = ExternalImagePanel(panel_id=f"cache-{index}")
        panel.import_image(source_path)
        pixmap = canvas._panel_pixmap(panel, 820, 615)
        assert pixmap is not None
        assert pixmap.size() == QSize(*NATIVE_SIZE)

    assert len(canvas._full_raster_cache) == canvas.FULL_RASTER_CACHE_LIMIT
    assert all(key[0].endswith(".png") for key in canvas._full_raster_cache)


@pytest.mark.parametrize("suffix", [".png", ".ome.tif"])
def test_composer_export_uses_native_external_raster_without_preview(tmp_path: Path, monkeypatch, suffix: str) -> None:
    _app()
    from ui.widgets.composer_canvas import ComposerCanvas

    source_path = (
        _write_ome_tiff(tmp_path / f"export-source{suffix}")
        if suffix == ".ome.tif"
        else _write_raster(tmp_path / f"export-source{suffix}")
    )
    panel = ExternalImagePanel(panel_id="export-native-raster")
    panel.import_image(source_path)
    project = Project(title="Native Composer Export")
    project.add_panel(panel)
    figure = project.ensure_active_composer_figure()
    figure.canvas.update({"width_mm": 25.4, "height_mm": 19.05})
    canvas = ComposerCanvas()
    canvas.set_project(project)
    canvas.layout_model.width_mm = 25.4
    canvas.layout_model.height_mm = 19.05
    canvas.layout_model.rebuild_empty_cells()
    canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(0, 0))
    reference = figure.panel_refs[0]
    reference.x_mm = 0.0
    reference.y_mm = 0.0
    reference.width_mm = 25.4
    reference.height_mm = 19.05
    canvas.reference_changed()

    def fail_preview(*_args, **_kwargs):
        raise AssertionError("Composer export must use the native external raster path")

    monkeypatch.setattr(panel, "render_preview", fail_preview)
    output = tmp_path / "native-export.png"
    canvas.export_figure(output, dpi=300)

    with Image.open(output) as exported:
        assert exported.size[0] > 0 and exported.size[1] > 0
        assert len(exported.convert("RGB").getcolors(maxcolors=1_000_000) or []) > 1


def test_composer_ome_canvas_thumbnail_and_export_preserve_multichannel_merge(tmp_path: Path, monkeypatch) -> None:
    _app()
    from ui.widgets.composer_canvas import ComposerCanvas

    source_path = _write_ome_tiff(tmp_path / "two-patterns.ome.tif")
    panel = ExternalImagePanel(panel_id="ome-multichannel-merge")
    panel.import_image(source_path)
    canvas = ComposerCanvas()

    canvas_pixmap = canvas._panel_pixmap(panel, 820, 615)
    thumbnail = canvas.panel_thumbnail(panel, width=160, height=120)
    assert canvas_pixmap is not None
    assert canvas_pixmap.size() == QSize(*NATIVE_SIZE)
    assert thumbnail is not None
    canvas_image = canvas_pixmap.toImage()
    thumbnail_image = thumbnail.toImage()
    canvas_blue = canvas_image.pixelColor(400, 600)
    canvas_green = canvas_image.pixelColor(1200, 600)
    thumbnail_blue = thumbnail_image.pixelColor(40, 60)
    thumbnail_green = thumbnail_image.pixelColor(120, 60)
    assert canvas_blue.blue() > canvas_blue.green() > canvas_blue.red()
    assert canvas_green.green() > canvas_green.blue() > canvas_green.red()
    assert thumbnail_blue.blue() > thumbnail_blue.green() > thumbnail_blue.red()
    assert thumbnail_green.green() > thumbnail_green.blue() > thumbnail_green.red()

    project = Project(title="OME Merge Export")
    project.add_panel(panel)
    figure = project.ensure_active_composer_figure()
    figure.canvas.update({"width_mm": 25.4, "height_mm": 19.05})
    canvas.set_project(project)
    canvas.layout_model.width_mm = 25.4
    canvas.layout_model.height_mm = 19.05
    canvas.layout_model.rebuild_empty_cells()
    canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(0, 0))
    reference = figure.panel_refs[0]
    reference.x_mm = 0.0
    reference.y_mm = 0.0
    reference.width_mm = 25.4
    reference.height_mm = 19.05
    canvas.reference_changed()

    def fail_preview(*_args, **_kwargs):
        raise AssertionError("OME export must use the native-size merged image")

    monkeypatch.setattr(panel, "render_preview", fail_preview)
    output = tmp_path / "ome-merged-export.png"
    canvas.export_figure(output, dpi=300)
    with Image.open(output) as exported:
        rgb = np.asarray(exported.convert("RGB"))
    blue_pixels = (rgb[..., 2] > rgb[..., 1]) & (rgb[..., 1] > rgb[..., 0])
    green_pixels = (rgb[..., 1] > rgb[..., 2]) & (rgb[..., 2] > rgb[..., 0])
    assert int(blue_pixels.sum()) > 100
    assert int(green_pixels.sum()) > 100


def test_preview_ihc_and_brightfield_keep_native_sources_across_zoom() -> None:
    app = _app()
    from ui.panels.brightfield_workspace import BrightfieldWorkspace
    from ui.panels.ihc_workspace import IHCWorkspace
    from ui.widgets.preview_canvas import PreviewCanvas

    source = QPixmap(*NATIVE_SIZE)
    source.fill(QColor("#204060"))
    overlay = QPixmap(*NATIVE_SIZE)
    overlay.fill(QColor("#602040"))

    preview = PreviewCanvas()
    preview.resize(900, 700)
    preview.show()
    preview.set_pixmap(source, title="Original")
    app.processEvents()
    initial_display_size = preview.image_label.pixmap().size()
    preview.zoom_by(2.0)
    app.processEvents()
    assert preview._source_pixmap.size() == source.size()
    assert preview.image_label.pixmap().size() != initial_display_size
    preview.close()

    for workspace_cls, original_title, overlay_title in (
        (IHCWorkspace, "IHC original", "IHC overlay"),
        (BrightfieldWorkspace, "Brightfield original", "Brightfield overlay"),
    ):
        workspace = workspace_cls(QLabel("context"))
        workspace.canvas.set_pixmap(source, title=original_title)
        workspace.canvas.set_pixmap(overlay, title=overlay_title)
        workspace.canvas.show_original_preview()
        assert workspace.canvas._original_pixmap.size() == source.size()
        assert workspace.canvas._overlay_pixmap.size() == overlay.size()
        assert workspace.canvas._source_pixmap.size() == source.size()
        workspace.canvas.show_overlay_preview()
        assert workspace.canvas._source_pixmap.size() == overlay.size()
        workspace.close()
