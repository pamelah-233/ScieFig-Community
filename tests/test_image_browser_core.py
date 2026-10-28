from __future__ import annotations

import importlib
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from PyQt6.QtCore import QRectF, QSize
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication


def _browser_core():
    try:
        return importlib.import_module("core.image_browser")
    except ModuleNotFoundError as exc:
        pytest.fail(f"browser core is not implemented yet: {exc}")


def _qimage_bytes(image: QImage) -> bytes:
    normalized = image.convertToFormat(QImage.Format.Format_RGB888)
    bits = normalized.bits()
    bits.setsize(normalized.sizeInBytes())
    return bytes(bits)


def _qimage_rgb_array(image: QImage) -> np.ndarray:
    normalized = image.convertToFormat(QImage.Format.Format_RGB888)
    bits = normalized.bits()
    bits.setsize(normalized.sizeInBytes())
    raw = np.frombuffer(bytes(bits), dtype=np.uint8)
    return raw.reshape(normalized.height(), normalized.bytesPerLine())[:, : normalized.width() * 3].reshape(
        normalized.height(), normalized.width(), 3
    )


def _ensure_qapplication() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_discover_image_files_is_sorted_and_non_recursive(tmp_path: Path) -> None:
    core = _browser_core()
    folder = tmp_path / "images"
    child = folder / "child"
    folder.mkdir()
    child.mkdir()
    for name in ("d.png", "b.nd2", "a.czi", "c.ome.tif", "ignore.txt"):
        (folder / name).write_bytes(b"placeholder")
    (child / "nested.png").write_bytes(b"placeholder")

    paths = core.discover_image_files(folder)

    assert [path.name for path in paths] == ["a.czi", "b.nd2", "c.ome.tif", "d.png"]
    assert all(path.parent == folder for path in paths)


def test_load_browser_image_returns_native_rgb_composite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    core = _browser_core()
    path = tmp_path / "cells.czi"
    path.write_bytes(b"fake-czi")
    source = np.zeros((3, 12, 16), dtype=np.uint16)
    source[0, 1, 2] = 65535
    expected = core.ImageImportResult(
        analysis_data=source,
        metadata={"source_format": "CZI", "pixel_size_um": 0.5},
        source_hash="sha256:" + "a" * 64,
        channel_names=["DAPI", "GFP", "RFP"],
    )
    monkeypatch.setattr(core, "read_microscopy_image", lambda _path: expected)

    image = core.load_browser_image(path)

    assert isinstance(image, core.BrowserImage)
    assert image.path == path
    assert image.rgb.shape == (12, 16, 3)
    assert image.rgb.dtype == np.uint8
    assert image.size == (16, 12)
    assert image.metadata["pixel_size_um"] == 0.5
    assert image.channel_names == ["DAPI", "GFP", "RFP"]


def test_rgb_to_qimage_owns_a_copy_and_thumbnail_never_upscales(tmp_path: Path) -> None:
    core = _browser_core()
    source = np.zeros((20, 40, 3), dtype=np.uint8)
    source[3, 4] = [11, 22, 33]

    image = core.rgb_to_qimage(source)
    source[3, 4] = [200, 201, 202]
    small = core.BrowserImage(tmp_path / "small.png", source, {}, [])
    thumbnail = core.thumbnail_qimage(small, QSize(176, 132))

    assert image.size() == QSize(40, 20)
    assert image.pixelColor(4, 3) == QColor(11, 22, 33)
    assert thumbnail.size() == QSize(40, 20)


def test_thumbnail_is_bounded_for_large_native_rgb(tmp_path: Path) -> None:
    core = _browser_core()
    source = np.zeros((1200, 1600, 3), dtype=np.uint8)
    image = core.BrowserImage(tmp_path / "large.png", source, {}, [])

    thumbnail = core.thumbnail_qimage(image, QSize(176, 132))

    assert thumbnail.width() <= 176
    assert thumbnail.height() <= 132
    assert thumbnail.width() > 0
    assert thumbnail.height() > 0


def test_load_browser_thumbnail_uses_bounded_reader_for_large_tiff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    core = _browser_core()
    path = tmp_path / "large.tiff"
    path.write_bytes(b"placeholder")
    calls: dict[str, object] = {}

    def fail_full_reader(_path):
        raise AssertionError("thumbnail loading must not use the full microscopy reader")

    class FakeReader:
        def __init__(self, source: str) -> None:
            calls["source"] = source

        def setAutoTransform(self, enabled: bool) -> None:
            calls["auto_transform"] = enabled

        def size(self) -> QSize:
            return QSize(4000, 3000)

        def setScaledSize(self, size: QSize) -> None:
            calls["scaled_size"] = size

        def read(self) -> QImage:
            calls["read"] = True
            return QImage(176, 132, QImage.Format.Format_RGB32)

        def errorString(self) -> str:
            return "fake reader error"

    monkeypatch.setattr(core, "read_microscopy_image", fail_full_reader)
    monkeypatch.setattr(core, "QImageReader", FakeReader)

    thumbnail = core.load_browser_thumbnail(path)

    assert thumbnail.size() == QSize(176, 132)
    assert calls["source"] == str(path)
    assert calls["auto_transform"] is True
    assert calls["scaled_size"] == QSize(176, 132)
    assert calls["read"] is True


def test_load_browser_thumbnail_allows_full_reader_only_for_czi_and_nd2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    core = _browser_core()
    expected = QImage(12, 8, QImage.Format.Format_RGB32)
    loaded: list[Path] = []
    thumbnailed: list[object] = []
    browser_image = object()

    def fake_load(path: str | Path):
        loaded.append(Path(path))
        return browser_image

    def fake_thumbnail(image, max_size: QSize = QSize(176, 132)):
        thumbnailed.append((image, max_size))
        return expected

    monkeypatch.setattr(core, "load_browser_image", fake_load)
    monkeypatch.setattr(core, "thumbnail_qimage", fake_thumbnail)

    czi = tmp_path / "cells.czi"
    nd2 = tmp_path / "cells.nd2"
    assert core.load_browser_thumbnail(czi) is expected
    assert core.load_browser_thumbnail(nd2) is expected

    assert loaded == [czi, nd2]
    assert thumbnailed == [(browser_image, QSize(176, 132)), (browser_image, QSize(176, 132))]


def test_load_browser_thumbnail_falls_back_to_tiff_memmap_without_full_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    core = _browser_core()
    path = tmp_path / "small.tiff"
    source = np.zeros((80, 100, 3), dtype=np.uint8)
    source[..., 0] = 12
    source[..., 1] = 48
    source[..., 2] = 96
    source[7, 9] = [200, 10, 30]
    tifffile.imwrite(path, source, photometric="rgb", compression=False)

    def fail_full_reader(_path):
        raise AssertionError("thumbnail loading must not use the full microscopy reader")

    class FailingReader:
        def __init__(self, source: str) -> None:
            self._source = source

        def setAutoTransform(self, enabled: bool) -> None:
            return None

        def size(self) -> QSize:
            return QSize(0, 0)

        def setScaledSize(self, size: QSize) -> None:
            return None

        def read(self) -> QImage:
            return QImage()

        def errorString(self) -> str:
            return "forced QImageReader failure"

    monkeypatch.setattr(core, "read_microscopy_image", fail_full_reader)
    monkeypatch.setattr(core, "QImageReader", FailingReader)

    thumbnail = core.load_browser_thumbnail(path, QSize(176, 132))

    assert not thumbnail.isNull()
    assert thumbnail.width() <= 176
    assert thumbnail.height() <= 132
    assert thumbnail.width() > 0
    assert thumbnail.height() > 0


def test_render_snapshot_preserves_native_dimensions_and_paints_calibrated_scale_bar(tmp_path: Path) -> None:
    app = _ensure_qapplication()
    core = _browser_core()
    source = np.zeros((1200, 1600, 3), dtype=np.uint8)
    source[:] = [18, 24, 32]
    before = source.copy()
    image = core.BrowserImage(tmp_path / "snapshot.png", source, {}, [])
    source_image = core.rgb_to_qimage(source)

    snapshot = core.render_snapshot(image, True, length_um=50.0, manual_um_per_px=0.5)
    assert app is not None

    assert snapshot.size() == QSize(1600, 1200)
    assert np.array_equal(source, before)
    assert snapshot != source_image

    snapshot_rgb = _qimage_rgb_array(snapshot)
    white = np.array([255, 255, 255], dtype=np.uint8)
    horizontal_runs: list[int] = []
    for y in range(1050, 1180):
        run = 0
        longest = 0
        for x in range(1300, 1560):
            if np.array_equal(snapshot_rgb[y, x], white):
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        horizontal_runs.append(longest)
    assert 98 <= max(horizontal_runs) <= 102
    assert sum(
        1
        for y in range(1000, 1150)
        for x in range(1300, 1560)
        if not np.array_equal(snapshot_rgb[y, x], [18, 24, 32])
    ) > 100


def test_render_snapshot_without_scale_bar_is_pixel_identical_and_missing_calibration_blocks_bar(
    tmp_path: Path,
) -> None:
    app = _ensure_qapplication()
    core = _browser_core()
    source = np.full((12, 16, 3), [18, 24, 32], dtype=np.uint8)
    image = core.BrowserImage(tmp_path / "uncalibrated.png", source, {}, [])

    snapshot = core.render_snapshot(image, False, length_um=50.0)
    assert app is not None

    assert _qimage_bytes(snapshot) == _qimage_bytes(core.rgb_to_qimage(source))
    with pytest.raises(ValueError, match="calibration"):
        core.render_snapshot(image, True, length_um=50.0)


def test_render_snapshot_with_scale_bar_returns_rgb888(tmp_path: Path) -> None:
    app = _ensure_qapplication()
    core = _browser_core()
    source = np.zeros((120, 160, 3), dtype=np.uint8)
    image = core.BrowserImage(tmp_path / "scaled.png", source, {}, [])

    snapshot = core.render_snapshot(image, True, length_um=5.0, manual_um_per_px=0.5)

    assert app is not None
    assert snapshot.format() == QImage.Format.Format_RGB888


def test_scale_bar_layout_clamps_complete_bounds_and_centers_text(tmp_path: Path) -> None:
    app = _ensure_qapplication()
    core = _browser_core()
    state = core.ScaleBarState(
        visible=True,
        length_um=50.0,
        um_per_px=0.5,
        center_x_px=1590.0,
        line_y_px=1190.0,
        line_color="#12AB34",
        text_color="#AB12CD",
        line_thickness_px=8.0,
        font_family="DejaVu Sans",
        font_size_px=24.0,
        bold=True,
        italic=True,
    )

    target = QRectF(0.0, 0.0, 1600.0, 1200.0)
    layout = core.scale_bar_layout(target, QSize(1600, 1200), state)

    assert state.visible is True
    assert state.length_um == pytest.approx(50.0)
    assert state.um_per_px == pytest.approx(0.5)
    assert state.center_x_px == pytest.approx(1590.0)
    assert state.line_y_px == pytest.approx(1190.0)
    assert state.line_color == "#12AB34"
    assert state.text_color == "#AB12CD"
    assert state.line_thickness_px == pytest.approx(8.0)
    assert state.font_family == "DejaVu Sans"
    assert state.font_size_px == pytest.approx(24.0)
    assert state.bold is True
    assert state.italic is True
    assert app is not None
    assert layout.line_rect.width() == pytest.approx(100.0, abs=1.0)
    assert layout.line_rect.center().x() == pytest.approx(layout.text_rect.center().x(), abs=0.5)
    assert target.contains(layout.bounds)
    assert layout.label == "50 µm"


def test_render_snapshot_uses_styled_source_position_and_shared_layout(tmp_path: Path) -> None:
    app = _ensure_qapplication()
    core = _browser_core()
    source = np.full((1200, 1600, 3), [18, 24, 32], dtype=np.uint8)
    before = source.copy()
    image = core.BrowserImage(tmp_path / "styled.png", source, {}, [])
    state = core.ScaleBarState(
        visible=True,
        length_um=50.0,
        um_per_px=0.5,
        center_x_px=600.0,
        line_y_px=700.0,
        line_color="#12AB34",
        text_color="#AB12CD",
        line_thickness_px=8.0,
        font_family="DejaVu Sans",
        font_size_px=24.0,
        bold=True,
        italic=True,
    )

    snapshot = core.render_snapshot(
        image,
        show_scale_bar=True,
        length_um=50.0,
        manual_um_per_px=0.5,
        scale_bar_state=state,
    )
    layout = core.scale_bar_layout(QRectF(0.0, 0.0, 1600.0, 1200.0), QSize(1600, 1200), state)
    rgb = _qimage_rgb_array(snapshot)

    assert app is not None
    assert snapshot.size() == QSize(1600, 1200)
    assert snapshot.format() == QImage.Format.Format_RGB888
    assert np.array_equal(source, before)
    line_rgb = np.array([0x12, 0xAB, 0x34], dtype=np.uint8)
    text_rgb = np.array([0xAB, 0x12, 0xCD], dtype=np.uint8)
    line_region = rgb[
        max(0, int(layout.line_rect.top()) - 2) : min(rgb.shape[0], int(layout.line_rect.bottom()) + 3),
        max(0, int(layout.line_rect.left()) - 2) : min(rgb.shape[1], int(layout.line_rect.right()) + 3),
    ]
    text_region = rgb[
        max(0, int(layout.text_rect.top())) : min(rgb.shape[0], int(layout.text_rect.bottom()) + 1),
        max(0, int(layout.text_rect.left())) : min(rgb.shape[1], int(layout.text_rect.right()) + 1),
    ]
    assert np.any(np.all(line_region == line_rgb, axis=2))
    assert np.any(np.all(text_region == text_rgb, axis=2))
    assert layout.line_rect.center().x() == pytest.approx(layout.text_rect.center().x(), abs=0.5)
