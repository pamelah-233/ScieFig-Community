from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtGui import QColor, QImage
import pytest

from core.models import BrightfieldPanel, ExternalImagePanel, FluorescencePanel, IHCPanel, WesternBlotPanel
from ui.panels.placeholder_panel import dropped_file_paths
from core.utils.image_formats import BRIGHTFIELD_IMAGE_SUFFIXES, COMPOSER_IMAGE_SUFFIXES, IHC_IMAGE_SUFFIXES, WB_IMAGE_SUFFIXES


def _write_raster(path: Path) -> Path:
    image = QImage(48, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("#33aaee"))
    assert image.save(str(path))
    return path


def _write_svg(path: Path) -> Path:
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="32"><rect width="48" height="32" fill="#33aaee"/></svg>',
        encoding="utf-8",
    )
    return path


def _mime_for(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def test_fluorescence_import_accepts_common_image_formats(tmp_path) -> None:
    for suffix in [".png", ".jpg", ".svg"]:
        path = _write_svg(tmp_path / f"cells{suffix}") if suffix == ".svg" else _write_raster(tmp_path / f"cells{suffix}")
        panel = FluorescencePanel(panel_id=f"Fluoro{suffix}")
        image = panel.import_image(path)
        data = panel.image_data(image["uid"])

        assert data is not None
        assert data.shape[1] > 0
        assert data.shape[2] > 0


def test_composer_external_import_uses_fluorescence_image_format_set(tmp_path) -> None:
    path = _write_svg(tmp_path / "external.svg")
    panel = ExternalImagePanel(panel_id="External")

    panel.import_image(path)
    preview = panel.render_preview(20, 15)

    assert panel.data["source_format"] == "SVG"
    assert not preview.isNull()
    assert dropped_file_paths(_mime_for(path), COMPOSER_IMAGE_SUFFIXES) == [str(path)]


def test_wb_import_rejects_svg_but_accepts_raster_drag_filter(tmp_path) -> None:
    svg_path = _write_svg(tmp_path / "not_raw_blot.svg")
    png_path = _write_raster(tmp_path / "raw_blot.png")
    panel = WesternBlotPanel(panel_id="Figure1B")

    with pytest.raises(ValueError):
        panel.import_full_blot(svg_path)

    assert dropped_file_paths(_mime_for(svg_path), WB_IMAGE_SUFFIXES) == []
    assert dropped_file_paths(_mime_for(png_path), WB_IMAGE_SUFFIXES) == [str(png_path)]


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".bmp", ".tif", ".webp"])
def test_ihc_import_accepts_common_raster_formats(tmp_path, suffix: str) -> None:
    path = _write_raster(tmp_path / f"ihc{suffix}")
    panel = IHCPanel(panel_id=f"IHC{suffix}")

    record = panel.import_image(path, group="Tumor")

    assert record["uid"] == panel.data["active_image_uid"]
    assert record["source_path"] == str(path)
    assert record["source_name"] == path.name
    assert record["source_hash"]
    assert record["group"] == "Tumor"
    expected_format = "TIFF" if suffix == ".tif" else path.suffix.lower().lstrip(".").upper()
    assert record["metadata"] == {
        "width_px": 48,
        "height_px": 32,
        "pixel_size_um": None,
        "magnification": None,
        "source_format": expected_format,
        "n_channels": 3,
        "pixel_size_unit": "um",
        "bit_depth": 8,
        "axes": "CYX",
        "work_data_downsample": 1,
        "io_warnings": [],
    }
    assert record["preprocessing"] == {
        "mode": "h_dab_deconvolution",
        "channel": "dab",
        "params": {},
    }
    assert record["roi"] == {"mode": "whole_image", "items": [], "active_item_uuid": None}
    assert record["measurements"] == []
    assert record["non_destructive"] is True
    assert record["imported_at"]
    assert panel.active_image() == record


@pytest.mark.parametrize("suffix", [".ome.tif", ".ome.tiff"])
def test_ihc_import_accepts_ome_tiff_raster_names(tmp_path, suffix: str) -> None:
    path = tmp_path / f"ihc{suffix}"
    image = QImage(48, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("#8a4a1c"))
    assert image.save(str(path), "TIFF")
    panel = IHCPanel(panel_id=f"IHC{suffix}")

    record = panel.import_image(path, group="Tumor")

    assert record["source_name"] == path.name
    assert record["metadata"]["width_px"] == 48
    assert record["metadata"]["height_px"] == 32
    assert record["metadata"]["source_format"] == "TIFF"
    assert dropped_file_paths(_mime_for(path), IHC_IMAGE_SUFFIXES) == [str(path)]


def test_ihc_import_rejects_missing_and_vector_sources(tmp_path) -> None:
    panel = IHCPanel(panel_id="IHCReject")
    missing_path = tmp_path / "missing.png"
    svg_path = _write_svg(tmp_path / "ihc.svg")

    with pytest.raises(FileNotFoundError):
        panel.import_image(missing_path)

    with pytest.raises(ValueError):
        panel.import_image(svg_path)


def test_ihc_preview_and_export_use_active_raster_image(tmp_path) -> None:
    path = _write_raster(tmp_path / "ihc_preview.png")
    panel = IHCPanel(panel_id="IHCPreview")
    panel.import_image(path, group="Ungrouped")

    preview = panel.render_preview(20, 20, dpi=150)
    export_pdf = panel.render_export(20, 20, dpi=150, format="pdf")
    export_svg = panel.render_export(20, 20, dpi=150, format="svg")

    assert not preview.isNull()
    assert preview.width() > 0
    assert preview.height() > 0
    assert preview.pixelColor(0, 0) == QColor("white")
    assert export_pdf.startswith(b"\x89PNG\r\n\x1a\n")
    assert export_svg.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".bmp", ".tif", ".webp"])
def test_brightfield_import_accepts_common_raster_formats(tmp_path, suffix: str) -> None:
    path = _write_raster(tmp_path / f"brightfield{suffix}")
    panel = BrightfieldPanel(panel_id=f"Brightfield{suffix}")

    record = panel.import_image(path, group="Control", stain_type="H&E")

    assert record["uid"] == panel.data["active_image_uid"]
    assert record["source_path"] == str(path)
    assert record["source_name"] == path.name
    assert record["source_hash"]
    assert record["group"] == "Control"
    assert record["stain_type"] == "H&E"
    assert record["metadata"]["width_px"] == 48
    assert record["metadata"]["height_px"] == 32
    assert record["metadata"]["source_format"] == ("TIFF" if suffix == ".tif" else path.suffix.lower().lstrip(".").upper())
    assert record["metadata"]["n_channels"] == 3
    assert dropped_file_paths(_mime_for(path), BRIGHTFIELD_IMAGE_SUFFIXES) == [str(path)]


@pytest.mark.parametrize("suffix", [".ome.tif", ".ome.tiff"])
def test_brightfield_import_accepts_ome_tiff_raster_names(tmp_path, suffix: str) -> None:
    path = tmp_path / f"brightfield{suffix}"
    image = QImage(48, 32, QImage.Format.Format_RGB32)
    image.fill(QColor("#c64828"))
    assert image.save(str(path), "TIFF")
    panel = BrightfieldPanel(panel_id=f"Brightfield{suffix}")

    record = panel.import_image(path, group="Control", stain_type="Oil Red O")

    assert record["source_name"] == path.name
    assert record["stain_type"] == "Oil Red O"
    assert record["metadata"]["source_format"] == "TIFF"
    assert dropped_file_paths(_mime_for(path), BRIGHTFIELD_IMAGE_SUFFIXES) == [str(path)]
