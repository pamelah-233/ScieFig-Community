from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
import tifffile

from core.image_analysis import io_engine
from core.models.fluoro_panel import FluorescencePanel
from core.utils.hashing import sha256_file
from core.utils.fluoro_image_io import FluorescenceImportResult, import_fluorescence_image


LARGE_HEIGHT = 2049
LARGE_WIDTH = 2051


def _large_uint16_cyx() -> np.ndarray:
    data = np.zeros((2, LARGE_HEIGHT, LARGE_WIDTH), dtype=np.uint16)
    data[0].fill(1200)
    data[1].fill(42000)
    data[0, 1, 3] = 65535
    data[1, LARGE_HEIGHT - 2, LARGE_WIDTH - 2] = 17
    return data


def _large_rgb() -> np.ndarray:
    data = np.empty((LARGE_HEIGHT, LARGE_WIDTH, 3), dtype=np.uint8)
    data[..., 0] = 17
    data[..., 1] = 83
    data[..., 2] = 191
    data[1, 3] = [251, 7, 129]
    data[LARGE_HEIGHT - 2, LARGE_WIDTH - 2] = [3, 241, 61]
    return data


def test_io_engine_reads_common_raster_as_cyx(tmp_path: Path) -> None:
    path = tmp_path / "cells.png"
    raw = np.zeros((6, 8, 3), dtype=np.uint8)
    raw[..., 0] = 12
    raw[..., 1] = 34
    raw[..., 2] = 56
    Image.fromarray(raw, mode="RGB").save(path)

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == (3, 6, 8)
    assert result.metadata["source_format"] == "PNG"
    assert result.channel_names == ["Red", "Green", "Blue"]
    assert int(result.analysis_data[1, 0, 0]) == 34


def test_io_engine_reads_tiff_and_preserves_ome_channel_names(tmp_path: Path) -> None:
    path = tmp_path / "cells.ome.tif"
    data = np.zeros((2, 5, 7), dtype=np.uint16)
    data[0, 1:3, 2:4] = 100
    data[1, 2:5, 4:7] = 200
    tifffile.imwrite(
        path,
        data,
        ome=True,
        metadata={
            "axes": "CYX",
            "Channel": {"Name": ["DAPI", "GFP"]},
            "PhysicalSizeX": 0.5,
            "PhysicalSizeXUnit": "\u00b5m",
        },
    )

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == (2, 5, 7)
    assert result.metadata["source_format"] == "OME-TIFF"
    assert result.metadata["pixel_size_um"] == 0.5
    assert result.metadata["pixel_size_unit"] == "um"
    assert result.channel_names == ["DAPI", "GFP"]


@pytest.mark.parametrize(
    ("unit", "value", "expected", "stored_unit"),
    [
        ("nm", 250.0, 0.25, "nm"),
        ("mm", 0.002, 2.0, "mm"),
        ("m", 0.0000015, 1.5, "m"),
        ("\u00b5m", 0.75, 0.75, "um"),
        ("um", 0.75, 0.75, "um"),
        ("furlong", 3.5, None, "furlong"),
    ],
)
def test_io_engine_converts_ome_physical_size_to_um(
    unit: str, value: float, expected: float | None, stored_unit: str
) -> None:
    xml = (
        '<OME><Image><Pixels PhysicalSizeX="{value}" '
        'PhysicalSizeXUnit="{unit}" /></Image></OME>'
    ).format(value=value, unit=unit)

    metadata = io_engine._parse_ome_metadata(xml)

    if expected is None:
        assert "pixel_size_um" not in metadata
    else:
        assert metadata["pixel_size_um"] == pytest.approx(expected)
    assert metadata["pixel_size_unit"] == stored_unit


@pytest.mark.parametrize("ome", [False, True], ids=["tiff", "ome-tiff"])
def test_io_engine_preserves_full_resolution_large_tiff(tmp_path: Path, ome: bool) -> None:
    path = tmp_path / ("large.ome.tif" if ome else "large.tif")
    source = _large_uint16_cyx()
    tifffile.imwrite(path, source, ome=ome, metadata={"axes": "CYX"})

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == source.shape
    assert result.analysis_data.dtype == source.dtype
    assert result.analysis_data.flags.c_contiguous
    assert result.metadata["width_px"] == LARGE_WIDTH
    assert result.metadata["height_px"] == LARGE_HEIGHT
    assert result.metadata["n_channels"] == 2
    assert result.metadata["original_width_px"] == LARGE_WIDTH
    assert result.metadata["original_height_px"] == LARGE_HEIGHT
    assert result.metadata["work_data_downsample"] == 1
    assert result.source_hash == sha256_file(path)
    for channel, y, x in ((0, 0, 0), (0, 1, 3), (1, LARGE_HEIGHT - 2, LARGE_WIDTH - 2)):
        assert result.analysis_data[channel, y, x] == source[channel, y, x]


def test_io_engine_preserves_full_resolution_large_float32_ome_tiff(tmp_path: Path) -> None:
    path = tmp_path / "large-float.ome.tif"
    source = np.zeros((1, LARGE_HEIGHT, LARGE_WIDTH), dtype=np.float32)
    source.fill(0.125)
    source[0, 1, 3] = 0.9875
    source[0, LARGE_HEIGHT - 2, LARGE_WIDTH - 2] = -0.25
    tifffile.imwrite(path, source, ome=True, metadata={"axes": "CYX"})

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == source.shape
    assert result.analysis_data.dtype == np.float32
    assert result.analysis_data.flags.c_contiguous
    assert result.metadata["bit_depth"] == 32
    assert result.metadata["original_width_px"] == LARGE_WIDTH
    assert result.metadata["original_height_px"] == LARGE_HEIGHT
    assert result.metadata["work_data_downsample"] == 1
    assert result.source_hash == sha256_file(path)
    for y, x in ((0, 0), (1, 3), (LARGE_HEIGHT - 2, LARGE_WIDTH - 2)):
        assert result.analysis_data[0, y, x] == source[0, y, x]


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".bmp", ".webp"])
def test_io_engine_preserves_full_resolution_large_common_raster(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"large{suffix}"
    source = _large_rgb()
    save_kwargs = {"quality": 95, "subsampling": 0} if suffix == ".jpg" else {"lossless": True} if suffix == ".webp" else {}
    Image.fromarray(source, mode="RGB").save(path, **save_kwargs)
    with Image.open(path) as encoded:
        expected = np.asarray(encoded.convert("RGB")).copy()

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == (3, LARGE_HEIGHT, LARGE_WIDTH)
    assert result.analysis_data.dtype == expected.dtype == np.uint8
    assert result.analysis_data.flags.c_contiguous
    assert result.metadata["width_px"] == LARGE_WIDTH
    assert result.metadata["height_px"] == LARGE_HEIGHT
    assert result.metadata["n_channels"] == 3
    assert result.metadata["original_width_px"] == LARGE_WIDTH
    assert result.metadata["original_height_px"] == LARGE_HEIGHT
    assert result.metadata["work_data_downsample"] == 1
    assert result.source_hash == sha256_file(path)
    for y, x in ((0, 0), (1, 3), (1025, 1027), (LARGE_HEIGHT - 2, LARGE_WIDTH - 2)):
        assert result.analysis_data[:, y, x].tolist() == expected[y, x].tolist()


def test_io_engine_rasterizes_declared_large_svg_at_exact_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "declared-large.svg"
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{LARGE_WIDTH}" height="{LARGE_HEIGHT}">'
        f'<rect width="{LARGE_WIDTH}" height="{LARGE_HEIGHT}" fill="#123456"/></svg>',
        encoding="utf-8",
    )

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == (4, LARGE_HEIGHT, LARGE_WIDTH)
    assert result.analysis_data.dtype == np.uint8
    assert result.analysis_data[:, LARGE_HEIGHT // 2, LARGE_WIDTH // 2].tolist() == [18, 52, 86, 255]
    assert result.metadata["width_px"] == LARGE_WIDTH
    assert result.metadata["height_px"] == LARGE_HEIGHT
    assert result.metadata["n_channels"] == 4
    assert result.metadata["original_width_px"] == LARGE_WIDTH
    assert result.metadata["original_height_px"] == LARGE_HEIGHT
    assert result.metadata["work_data_downsample"] == 1
    assert result.source_hash == sha256_file(path)


def test_fluorescence_panel_spills_large_full_resolution_import_losslessly(tmp_path: Path) -> None:
    path = tmp_path / "large-panel.ome.tif"
    source = _large_uint16_cyx()
    tifffile.imwrite(path, source, ome=True, metadata={"axes": "CYX"})
    panel = FluorescencePanel(panel_id="large-full-resolution")
    panel.IMAGE_CACHE_BYTE_BUDGET = 1

    record = panel.import_image(path)
    image_uid = str(record["uid"])
    spill_path = panel._spilled_image_paths[image_uid]
    actual = panel.image_data(image_uid)

    assert isinstance(actual, np.memmap)
    assert actual.shape == source.shape
    assert actual.dtype == source.dtype
    assert record["analysis_data_shape"] == list(source.shape)
    assert record["analysis_dtype"] == str(source.dtype)
    assert record["source_hash"] == sha256_file(path)
    assert record["metadata"]["width_px"] == LARGE_WIDTH
    assert record["metadata"]["height_px"] == LARGE_HEIGHT
    assert not panel._image_cache
    for channel, y, x in ((0, 1, 3), (1, 0, 0), (1, LARGE_HEIGHT - 2, LARGE_WIDTH - 2)):
        assert actual[channel, y, x] == source[channel, y, x]
    panel.dispose()
    assert not spill_path.exists()


def test_io_engine_read_rgb_image_returns_rgb_preview_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "cells_rgba.png"
    raw = np.zeros((4, 5, 4), dtype=np.uint8)
    raw[..., 0] = 12
    raw[..., 1] = 34
    raw[..., 2] = 56
    raw[..., 3] = 255
    Image.fromarray(raw, mode="RGBA").save(path)

    rgb, metadata = io_engine.read_rgb_image(path)

    assert rgb.shape == (4, 5, 3)
    assert rgb.dtype == np.uint8
    assert rgb[0, 0].tolist() == [12, 34, 56]
    assert metadata["source_format"] == "PNG"
    assert metadata["width_px"] == 5
    assert metadata["height_px"] == 4
    assert metadata["work_data_downsample"] == 1


def test_io_engine_cyx_to_rgb_preview_expands_grayscale_channel() -> None:
    data = np.array([[[0, 65535], [32768, 16384]]], dtype=np.uint16)

    rgb = io_engine.cyx_to_rgb_preview(data)

    assert rgb.shape == (2, 2, 3)
    assert np.array_equal(rgb[..., 0], rgb[..., 1])
    assert np.array_equal(rgb[..., 1], rgb[..., 2])
    assert rgb[0, 1, 0] == 255


def test_fluoro_wrapper_delegates_to_io_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "delegated.png"
    path.write_bytes(b"not-an-image")
    expected = io_engine.ImageImportResult(
        analysis_data=np.ones((1, 2, 3), dtype=np.uint16),
        metadata={"source_format": "PNG", "width_px": 3, "height_px": 2, "n_channels": 1},
        source_hash="sha256:" + "a" * 64,
        channel_names=["Gray"],
    )

    def _fake_read_image(source: str | Path) -> io_engine.ImageImportResult:
        assert Path(source) == path
        return expected

    monkeypatch.setattr("core.utils.fluoro_image_io.read_image", _fake_read_image)

    result = import_fluorescence_image(path)

    assert isinstance(result, FluorescenceImportResult)
    assert np.array_equal(result.analysis_data, expected.analysis_data)
    assert result.metadata == expected.metadata
    assert result.source_hash == expected.source_hash
    assert result.channel_names == expected.channel_names


def test_io_engine_nd2_import_failure_instructs_tiff_export(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "cells.nd2"
    path.write_bytes(b"nd2")
    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "nd2":
            raise ImportError("missing nd2 backend")
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    with pytest.raises(ValueError, match="Export the file to TIFF/OME-TIFF"):
        io_engine.read_image(path)


@pytest.mark.parametrize("reader_name", ["read_image", "read_microscopy_image"])
def test_io_engine_reads_nd2_via_nd2file_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reader_name: str) -> None:
    path = tmp_path / "cells.nd2"
    path.write_bytes(b"fake-nd2-payload")

    class _FakeND2File:
        def __init__(self, source: Path) -> None:
            self.source = Path(source)
            self.sizes = {"T": 1, "C": 2, "Y": 3, "X": 4}
            self.axes = "TCYX"
            self.metadata = SimpleNamespace(
                channels=[
                    SimpleNamespace(channel="DAPI"),
                    SimpleNamespace(channel="FITC"),
                ]
            )

        def __enter__(self) -> "_FakeND2File":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            data = np.arange(1 * 2 * 3 * 4, dtype=np.uint16).reshape(1, 2, 3, 4)
            return data

        def voxel_size(self) -> SimpleNamespace:
            return SimpleNamespace(x=0.325)

    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "nd2":
            return SimpleNamespace(ND2File=_FakeND2File)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    reader = getattr(io_engine, reader_name)
    result = reader(path)

    assert isinstance(result, io_engine.ImageImportResult)
    assert result.analysis_data.shape == (2, 3, 4)
    assert result.metadata["source_format"] == "ND2"
    assert result.metadata["width_px"] == 4
    assert result.metadata["height_px"] == 3
    assert result.metadata["n_channels"] == 2
    assert result.metadata["pixel_size_um"] == pytest.approx(0.325)
    assert result.metadata["acquisition_software"] == "Nikon ND2"
    assert result.metadata["axes"] == "CYX"
    assert result.metadata["work_data_downsample"] == 1
    assert result.source_hash == sha256_file(path)
    assert result.channel_names == ["DAPI", "FITC"]


@pytest.mark.parametrize(
    ("frame", "expected_axes"),
    [
        (np.arange(3 * 4, dtype=np.uint16).reshape(3, 4), "YX"),
        (np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4), "CYX"),
    ],
)
def test_io_engine_nd2_prefers_read_frame_for_first_plane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frame: np.ndarray, expected_axes: str
) -> None:
    path = tmp_path / "first-plane.nd2"
    path.write_bytes(b"fake-nd2-first-plane")
    read_frame_calls: list[int] = []
    normalize_axes: list[str] = []

    class _FakeND2File:
        def __init__(self, source: Path) -> None:
            self.sizes = {"T": 4, "C": 2, "Y": 3, "X": 4}
            self.axes = "TCYX"
            self.metadata = SimpleNamespace(channels=[SimpleNamespace(channel="DAPI")])

        def __enter__(self) -> "_FakeND2File":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read_frame(self, index: int) -> np.ndarray:
            read_frame_calls.append(index)
            return frame

        def asarray(self) -> np.ndarray:
            raise AssertionError("read_frame(0) should avoid loading the complete ND2 stack")

        def voxel_size(self) -> SimpleNamespace:
            return SimpleNamespace(x=0.325)

    original_import_module = importlib.import_module
    original_normalize_to_cyx = io_engine.normalize_to_cyx

    def _fake_import_module(name: str, package: str | None = None):
        if name == "nd2":
            return SimpleNamespace(ND2File=_FakeND2File)
        return original_import_module(name, package)

    def _record_normalize(raw: np.ndarray, axes: str = "") -> np.ndarray:
        normalize_axes.append(axes)
        return original_normalize_to_cyx(raw, axes=axes)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)
    monkeypatch.setattr(io_engine, "normalize_to_cyx", _record_normalize)

    result = io_engine.read_microscopy_image(path)

    expected = frame[np.newaxis, :, :] if frame.ndim == 2 else frame
    assert read_frame_calls == [0]
    assert normalize_axes == [expected_axes]
    assert np.array_equal(result.analysis_data, expected)


def test_io_engine_nd2_read_frame_yxc_normalizes_to_cyx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "rgb-yxc.nd2"
    path.write_bytes(b"fake-nd2-yxc")
    frame = np.arange(8 * 9 * 3, dtype=np.uint16).reshape(8, 9, 3)

    class _FakeND2File:
        def __init__(self, source: Path) -> None:
            self.sizes = {"T": 1, "C": 1, "Y": 8, "X": 9}
            self.axes = "TCYX"
            self.metadata = SimpleNamespace(channels=[SimpleNamespace(channel="RGB")])

        def __enter__(self) -> "_FakeND2File":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read_frame(self, index: int) -> np.ndarray:
            assert index == 0
            return frame

        def asarray(self) -> np.ndarray:
            raise AssertionError("read_frame(0) should avoid loading the complete ND2 stack")

        def voxel_size(self) -> SimpleNamespace:
            return SimpleNamespace(x=0.325)

    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "nd2":
            return SimpleNamespace(ND2File=_FakeND2File)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    result = io_engine.read_microscopy_image(path)

    assert result.analysis_data.shape == (3, 8, 9)
    assert np.array_equal(result.analysis_data, np.moveaxis(frame, -1, 0))


def test_io_engine_mocked_nd2_backend_preserves_large_full_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "large.nd2"
    path.write_bytes(b"large-fake-nd2")

    class _FakeLargeND2File:
        def __init__(self, source: Path) -> None:
            self.sizes = {"C": 1, "Y": LARGE_HEIGHT, "X": LARGE_WIDTH}
            self.axes = "CYX"
            self.metadata = SimpleNamespace(channels=[SimpleNamespace(channel="DAPI")])

        def __enter__(self) -> "_FakeLargeND2File":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            data = np.zeros((1, LARGE_HEIGHT, LARGE_WIDTH), dtype=np.uint16)
            data[0, 1, 3] = 54321
            return data

        def voxel_size(self) -> SimpleNamespace:
            return SimpleNamespace(x=0.25)

    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "nd2":
            return SimpleNamespace(ND2File=_FakeLargeND2File)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == (1, LARGE_HEIGHT, LARGE_WIDTH)
    assert result.analysis_data.dtype == np.uint16
    assert result.analysis_data.flags.c_contiguous
    assert result.analysis_data[0, 1, 3] == 54321
    assert result.metadata["original_width_px"] == LARGE_WIDTH
    assert result.metadata["original_height_px"] == LARGE_HEIGHT
    assert result.metadata["work_data_downsample"] == 1
    assert result.source_hash == sha256_file(path)


def test_io_engine_allows_singleton_czi_scene_axes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "basic_singleton.czi"
    path.write_bytes(b"fake-czi-payload")
    metadata_xml = (
        "<ImageDocument>"
        "<Metadata><Information><Image><Dimensions>"
        '<Channels><Channel Name="DAPI" /></Channels>'
        '<Distance Id="X"><Value>0.0000005</Value></Distance>'
        "</Dimensions></Image></Information></Metadata>"
        "</ImageDocument>"
    )

    class _FakeCziFile:
        def __init__(self, source: str) -> None:
            self.axes = "SCYX"

        def __enter__(self) -> "_FakeCziFile":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            return np.arange(1 * 2 * 3 * 4, dtype=np.uint16).reshape(1, 2, 3, 4)

        def metadata(self) -> str:
            return metadata_xml

    monkeypatch.setitem(sys.modules, "czifile", SimpleNamespace(CziFile=_FakeCziFile))

    result = io_engine.read_microscopy_image(path)

    assert result.analysis_data.shape == (2, 3, 4)
    assert result.metadata["source_format"] == "CZI"
    assert result.metadata["pixel_size_um"] == pytest.approx(0.5)
    assert result.metadata["acquisition_software"] == "Zeiss CZI"
    assert result.channel_names == ["DAPI"]


def test_io_engine_reads_current_czi_scene_api_and_uses_first_scene(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "current-scenes.czi"
    path.write_bytes(b"fake-current-czi")
    first = np.arange(3 * 12 * 16, dtype=np.uint16).reshape(3, 12, 16)

    class _FakeScene:
        axes = "CYX"
        shape = (3, 12, 16)
        attrs = {"pixel_size_um": 0.5}
        channels = {"DAPI": {}, "GFP": {}, "RFP": {}}
        coord_scales = {"X": 0.0000005, "Y": 0.0000005}
        coord_units = {"X": "meter", "Y": "meter"}

        def __init__(self, data: np.ndarray) -> None:
            self._data = data

        def asarray(self) -> np.ndarray:
            return self._data

    class _FakeCziFile:
        def __init__(self, source: str) -> None:
            self.scenes = [_FakeScene(first), _FakeScene(first + 1)]

        def __enter__(self) -> "_FakeCziFile":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            raise AssertionError("current czifile scene API should be preferred")

        def metadata(self) -> str:
            return "<ImageDocument />"

    monkeypatch.setitem(sys.modules, "czifile", SimpleNamespace(CziFile=_FakeCziFile))

    result = io_engine.read_microscopy_image(path)

    assert np.array_equal(result.analysis_data, first)
    assert result.metadata["source_format"] == "CZI"
    assert result.metadata["pixel_size_um"] == pytest.approx(0.5)
    assert result.channel_names == ["DAPI", "GFP", "RFP"]
    assert result.metadata["io_warnings"]
    assert "first scene" in result.metadata["io_warnings"][0].lower()


def test_io_engine_nd2_selects_first_higher_dimensional_plane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "time-series.nd2"
    path.write_bytes(b"fake-nd2-time-series")
    raw = np.arange(2 * 2 * 3 * 4, dtype=np.uint16).reshape(2, 2, 3, 4)

    class _FakeND2File:
        def __init__(self, source: Path) -> None:
            self.sizes = {"T": 2, "C": 2, "Y": 3, "X": 4}
            self.axes = "TCYX"
            self.metadata = SimpleNamespace(
                channels=[SimpleNamespace(channel="DAPI"), SimpleNamespace(channel="FITC")]
            )

        def __enter__(self) -> "_FakeND2File":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            return raw

        def voxel_size(self) -> SimpleNamespace:
            return SimpleNamespace(x=0.325)

    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "nd2":
            return SimpleNamespace(ND2File=_FakeND2File)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    result = io_engine.read_microscopy_image(path)

    assert np.array_equal(result.analysis_data, raw[0])
    assert result.metadata["pixel_size_um"] == pytest.approx(0.325)
    assert result.channel_names == ["DAPI", "FITC"]


def test_io_engine_mocked_czi_backend_preserves_large_full_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "large.czi"
    path.write_bytes(b"large-fake-czi")

    class _FakeLargeCziFile:
        def __init__(self, source: str) -> None:
            self.axes = "CYX"

        def __enter__(self) -> "_FakeLargeCziFile":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            data = np.zeros((1, LARGE_HEIGHT, LARGE_WIDTH), dtype=np.uint16)
            data[0, LARGE_HEIGHT - 2, LARGE_WIDTH - 2] = 45678
            return data

        def metadata(self) -> str:
            return "<ImageDocument />"

    monkeypatch.setitem(sys.modules, "czifile", SimpleNamespace(CziFile=_FakeLargeCziFile))

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == (1, LARGE_HEIGHT, LARGE_WIDTH)
    assert result.analysis_data.dtype == np.uint16
    assert result.analysis_data.flags.c_contiguous
    assert result.analysis_data[0, LARGE_HEIGHT - 2, LARGE_WIDTH - 2] == 45678
    assert result.metadata["original_width_px"] == LARGE_WIDTH
    assert result.metadata["original_height_px"] == LARGE_HEIGHT
    assert result.metadata["work_data_downsample"] == 1
    assert result.source_hash == sha256_file(path)


def test_io_engine_rejects_multi_scene_czi_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "complex_multi_scene.czi"
    path.write_bytes(b"fake-czi-payload")

    class _FakeCziFile:
        def __init__(self, source: str) -> None:
            self.axes = "SCYX"

        def __enter__(self) -> "_FakeCziFile":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            return np.arange(2 * 2 * 3 * 4, dtype=np.uint16).reshape(2, 2, 3, 4)

        def metadata(self) -> str:
            return "<ImageDocument />"

    monkeypatch.setitem(sys.modules, "czifile", SimpleNamespace(CziFile=_FakeCziFile))

    with pytest.raises(ValueError, match="Export this file to OME-TIFF first"):
        io_engine.read_microscopy_image(path)
