from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import tifffile
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.models import ExternalImagePanel, FluorescencePanel, QPCRPanel, WesternBlotPanel


SAMPLE_QPCR = Path(__file__).resolve().parents[1] / "sample_data" / "qpcr_phase2_sample.csv"
_APP: QApplication | None = None


def _ensure_app() -> QApplication:
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _write_image(path: Path, width: int = 120, height: int = 80) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#88aa44"))
    assert image.save(str(path))
    return path


def _write_fluoro(path: Path) -> Path:
    data = np.zeros((2, 32, 48), dtype=np.uint16)
    data[0, 6:18, 8:22] = 1600
    data[1, 14:28, 24:40] = 4200
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def test_qpcr_panel_render_preview_and_vector_export() -> None:
    _ensure_app()
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE_QPCR, reference_gene="GAPDH", control_group="siCtrl")

    preview = panel.render_preview(40, 30, dpi=120)
    svg = panel.render_export(40, 30, dpi=300, format="svg")

    assert not preview.isNull()
    assert b"<svg" in svg
    assert b"Relative mRNA expression" in svg


def test_external_image_panel_render_preview_and_raster_export(tmp_path) -> None:
    _ensure_app()
    image_path = _write_image(tmp_path / "external.png")
    panel = ExternalImagePanel(panel_id="Figure1D")
    panel.import_image(image_path)

    preview = panel.render_preview(30, 20, dpi=120)
    png = panel.render_export(30, 20, dpi=300, format="png")

    assert not preview.isNull()
    assert png.startswith(b"\x89PNG")


def test_wb_panel_render_preview_and_raster_export(tmp_path) -> None:
    _ensure_app()
    image_path = _write_image(tmp_path / "wb.png", width=160, height=90)
    panel = WesternBlotPanel(panel_id="Figure1B")
    panel.import_full_blot(image_path)
    panel.set_crop_roi(10, 10, 80, 40, protein="SPNS1")

    preview = panel.render_preview(45, 30, dpi=120)
    png = panel.render_export(45, 30, dpi=300, format="png")

    assert not preview.isNull()
    assert png.startswith(b"\x89PNG")


def test_fluorescence_panel_render_preview_and_raster_export(tmp_path) -> None:
    _ensure_app()
    image_path = _write_fluoro(tmp_path / "cells.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)

    preview = panel.render_preview(45, 30, dpi=120)
    png = panel.render_export(45, 30, dpi=300, format="png")

    assert not preview.isNull()
    assert png.startswith(b"\x89PNG")
