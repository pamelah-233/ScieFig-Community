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


def _positive_um(value: object) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if resolved <= 0 or resolved != resolved or resolved in {float("inf"), float("-inf")}:
        return None
    return resolved


def test_ome_preserves_anisotropic_physical_sizes() -> None:
    xml = (
        "<OME><Image><Pixels "
        'PhysicalSizeX="0.5" PhysicalSizeXUnit="µm" '
        'PhysicalSizeY="0.25" PhysicalSizeYUnit="µm" /></Image></OME>'
    )

    metadata = io_engine._parse_ome_metadata(xml)

    assert metadata["pixel_size_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_y_um"] == pytest.approx(0.25)
    assert metadata["pixel_size_unit"] == "um"


def test_ome_x_only_does_not_invent_y() -> None:
    xml = '<OME><Image><Pixels PhysicalSizeX="0.5" PhysicalSizeXUnit="µm" /></Image></OME>'

    metadata = io_engine._parse_ome_metadata(xml)

    assert metadata["pixel_size_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert _positive_um(metadata.get("pixel_size_y_um")) is None


def test_ome_y_only_does_not_invent_x_or_legacy_scalar() -> None:
    xml = '<OME><Image><Pixels PhysicalSizeY="0.25" PhysicalSizeYUnit="µm" /></Image></OME>'

    metadata = io_engine._parse_ome_metadata(xml)

    assert _positive_um(metadata.get("pixel_size_um")) is None
    assert _positive_um(metadata.get("pixel_size_x_um")) is None
    assert metadata["pixel_size_y_um"] == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("unit", "x_value", "y_value", "expected_x", "expected_y"),
    [
        ("nm", 500.0, 250.0, 0.5, 0.25),
        ("mm", 0.002, 0.001, 2.0, 1.0),
        ("m", 0.0000015, 0.00000075, 1.5, 0.75),
        ("µm", 0.4, 0.8, 0.4, 0.8),
        ("um", 0.4, 0.8, 0.4, 0.8),
    ],
)
def test_ome_converts_xy_units_to_um(
    unit: str, x_value: float, y_value: float, expected_x: float, expected_y: float
) -> None:
    xml = (
        "<OME><Image><Pixels "
        f'PhysicalSizeX="{x_value}" PhysicalSizeXUnit="{unit}" '
        f'PhysicalSizeY="{y_value}" PhysicalSizeYUnit="{unit}" /></Image></OME>'
    )

    metadata = io_engine._parse_ome_metadata(xml)

    assert metadata["pixel_size_um"] == pytest.approx(expected_x)
    assert metadata["pixel_size_x_um"] == pytest.approx(expected_x)
    assert metadata["pixel_size_y_um"] == pytest.approx(expected_y)


def test_ome_unknown_unit_skips_calibration() -> None:
    xml = (
        "<OME><Image><Pixels "
        'PhysicalSizeX="3.5" PhysicalSizeXUnit="furlong" '
        'PhysicalSizeY="2.5" PhysicalSizeYUnit="furlong" /></Image></OME>'
    )

    metadata = io_engine._parse_ome_metadata(xml)

    assert _positive_um(metadata.get("pixel_size_um")) is None
    assert _positive_um(metadata.get("pixel_size_x_um")) is None
    assert _positive_um(metadata.get("pixel_size_y_um")) is None


def test_ome_non_positive_sizes_are_not_stored() -> None:
    xml = (
        "<OME><Image><Pixels "
        'PhysicalSizeX="0" PhysicalSizeY="-0.2" '
        'PhysicalSizeXUnit="µm" PhysicalSizeYUnit="µm" /></Image></OME>'
    )

    metadata = io_engine._parse_ome_metadata(xml)

    assert _positive_um(metadata.get("pixel_size_um")) is None
    assert _positive_um(metadata.get("pixel_size_x_um")) is None
    assert _positive_um(metadata.get("pixel_size_y_um")) is None


def test_imagej_spacing_is_z_not_xy_calibration() -> None:
    metadata = io_engine._parse_imagej_metadata({"unit": "um", "spacing": 2})

    assert "pixel_size_um" not in metadata
    assert "pixel_size_x_um" not in metadata
    assert "pixel_size_y_um" not in metadata
    assert _positive_um(metadata.get("pixel_size_um")) is None


def test_imagej_non_um_unit_is_not_calibration() -> None:
    assert io_engine._parse_imagej_metadata({"unit": "pixel", "spacing": 0.4}) == {}


@pytest.mark.parametrize('unit', [r'\u00b5m', r'\\u00B5m', r'\u03bcm'])
def test_imagej_escaped_micrometre_unit_keeps_physical_calibration(tmp_path: Path, unit) -> None:
    import numpy as np
    import tifffile
    path=tmp_path/'escaped-unit.tif'
    tifffile.imwrite(path,np.zeros((12,14),dtype=np.uint8),
        description=f'ImageJ=1.54g\nunit={unit}\n',metadata=None,
        resolution=(2.27,2.27),resolutionunit='NONE')
    _,metadata=io_engine.read_rgb_image(path)
    assert metadata['pixel_size_x_um']==pytest.approx(1/2.27)
    assert metadata['pixel_size_y_um']==pytest.approx(1/2.27)
    assert metadata['pixel_size_unit']=='um'


def test_imagej_tiff_resolution_preserves_xy_and_ignores_z_spacing(tmp_path: Path) -> None:
    path = tmp_path / "ij-aniso.tif"
    data = np.arange(5 * 7, dtype=np.uint16).reshape(5, 7)
    tifffile.imwrite(
        path,
        data,
        imagej=True,
        resolution=(2.0, 4.0),
        metadata={"unit": "um", "spacing": 2.0},
    )

    result = io_engine.read_image(path)

    assert np.array_equal(result.analysis_data[0], data)
    assert result.metadata["pixel_size_um"] == pytest.approx(0.5)
    assert result.metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert result.metadata["pixel_size_y_um"] == pytest.approx(0.25)
    assert result.metadata["pixel_size_um"] != pytest.approx(2.0)


def test_imagej_tiff_x_resolution_only_does_not_invent_y(tmp_path: Path) -> None:
    path = tmp_path / "ij-x-only.tif"
    data = np.arange(5 * 7, dtype=np.uint16).reshape(5, 7)
    tifffile.imwrite(
        path,
        data,
        imagej=True,
        resolution=(2.0, 0.0),
        metadata={"unit": "um", "spacing": 2.0},
    )

    result = io_engine.read_image(path)

    assert result.metadata["pixel_size_um"] == pytest.approx(0.5)
    assert result.metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert _positive_um(result.metadata.get("pixel_size_y_um")) is None


def test_imagej_tiff_default_resolution_does_not_fabricate_one_um(tmp_path: Path) -> None:
    path = tmp_path / "ij-default.tif"
    data = np.arange(5 * 7, dtype=np.uint16).reshape(5, 7)
    tifffile.imwrite(path, data, imagej=True, metadata={"unit": "um", "spacing": 2.0})

    result = io_engine.read_image(path)

    assert _positive_um(result.metadata.get("pixel_size_um")) is None
    assert _positive_um(result.metadata.get("pixel_size_x_um")) is None
    assert _positive_um(result.metadata.get("pixel_size_y_um")) is None


def test_plain_tiff_identity_resolution_is_not_calibration(tmp_path: Path) -> None:
    path = tmp_path / "plain.tif"
    data = np.arange(5 * 7, dtype=np.uint16).reshape(5, 7)
    tifffile.imwrite(path, data)

    result = io_engine.read_image(path)

    assert result.metadata["pixel_size_um"] is None
    assert _positive_um(result.metadata.get("pixel_size_x_um")) is None
    assert _positive_um(result.metadata.get("pixel_size_y_um")) is None


def test_tiff_inch_dpi_is_not_physical_calibration(tmp_path: Path) -> None:
    path = tmp_path / "dpi.tif"
    data = np.arange(5 * 7, dtype=np.uint16).reshape(5, 7)
    tifffile.imwrite(path, data, resolution=(72.0, 72.0))

    result = io_engine.read_image(path)

    assert _positive_um(result.metadata.get("pixel_size_um")) is None
    assert _positive_um(result.metadata.get("pixel_size_x_um")) is None
    assert _positive_um(result.metadata.get("pixel_size_y_um")) is None


def test_tiff_centimeter_resolution_preserves_xy() -> None:
    page = SimpleNamespace(
        resolution=(20000.0, 10000.0),
        tags={"ResolutionUnit": SimpleNamespace(value=3)},
    )
    tif = SimpleNamespace(pages=[page], imagej_metadata=None)

    metadata = io_engine._parse_tiff_xy_resolution(tif)

    assert metadata["pixel_size_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_y_um"] == pytest.approx(1.0)


def test_czi_xml_preserves_anisotropic_distances() -> None:
    xml = (
        "<ImageDocument><Metadata><Information><Image><Dimensions>"
        '<Distance Id="X"><Value>0.0000005</Value></Distance>'
        '<Distance Id="Y"><Value>0.00000025</Value></Distance>'
        "</Dimensions></Image></Information></Metadata></ImageDocument>"
    )

    metadata = io_engine._parse_czi_metadata(xml)

    assert metadata["pixel_size_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_y_um"] == pytest.approx(0.25)


def test_czi_xml_x_only_does_not_invent_y() -> None:
    xml = (
        "<ImageDocument><Metadata><Information><Image><Dimensions>"
        '<Distance Id="X"><Value>0.0000005</Value></Distance>'
        "</Dimensions></Image></Information></Metadata></ImageDocument>"
    )

    metadata = io_engine._parse_czi_metadata(xml)

    assert metadata["pixel_size_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert _positive_um(metadata.get("pixel_size_y_um")) is None


def test_czi_scene_isotropic_attr_sets_both_axes() -> None:
    scene = SimpleNamespace(attrs={"pixel_size_um": 0.5})

    metadata = io_engine._parse_czi_scene_metadata(scene)

    assert metadata["pixel_size_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_y_um"] == pytest.approx(0.5)


def test_czi_scene_x_attr_does_not_invent_y() -> None:
    scene = SimpleNamespace(attrs={"PhysicalSizeX": 0.4})

    metadata = io_engine._parse_czi_scene_metadata(scene)

    assert metadata["pixel_size_um"] == pytest.approx(0.4)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.4)
    assert _positive_um(metadata.get("pixel_size_y_um")) is None


def test_czi_scene_coord_scales_preserve_xy() -> None:
    scene = SimpleNamespace(
        coord_scales={"X": 0.0000005, "Y": 0.00000025},
        coord_units={"X": "meter", "Y": "meter"},
    )

    metadata = io_engine._parse_czi_scene_metadata(scene)

    assert metadata["pixel_size_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert metadata["pixel_size_y_um"] == pytest.approx(0.25)


def test_tiny_ome_tiff_read_preserves_xy_and_pixels(tmp_path: Path) -> None:
    path = tmp_path / "aniso.ome.tif"
    data = np.arange(2 * 5 * 7, dtype=np.uint16).reshape(2, 5, 7)
    tifffile.imwrite(
        path,
        data,
        ome=True,
        metadata={
            "axes": "CYX",
            "PhysicalSizeX": 0.5,
            "PhysicalSizeXUnit": "µm",
            "PhysicalSizeY": 0.25,
            "PhysicalSizeYUnit": "µm",
        },
    )

    result = io_engine.read_image(path)

    assert np.array_equal(result.analysis_data, data)
    assert result.metadata["pixel_size_um"] == pytest.approx(0.5)
    assert result.metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert result.metadata["pixel_size_y_um"] == pytest.approx(0.25)


def test_uncalibrated_raster_does_not_fabricate_pixel_size(tmp_path: Path) -> None:
    path = tmp_path / "plain.png"
    Image.fromarray(np.zeros((2, 3, 3), dtype=np.uint8), mode="RGB").save(path)

    result = io_engine.read_image(path)

    assert result.analysis_data.shape == (3, 2, 3)
    assert result.metadata["pixel_size_um"] is None
    assert _positive_um(result.metadata.get("pixel_size_x_um")) is None
    assert _positive_um(result.metadata.get("pixel_size_y_um")) is None
    assert result.metadata.get("pixel_size_um") != 1.0


def test_tiny_czi_read_preserves_xy_without_changing_pixels(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "aniso.czi"
    path.write_bytes(b"fake-czi")
    raw = np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4)
    xml = (
        "<ImageDocument><Metadata><Information><Image><Dimensions>"
        '<Channels><Channel Name="DAPI" /><Channel Name="GFP" /></Channels>'
        '<Distance Id="X"><Value>0.0000005</Value></Distance>'
        '<Distance Id="Y"><Value>0.00000025</Value></Distance>'
        "</Dimensions></Image></Information></Metadata></ImageDocument>"
    )

    class _FakeCziFile:
        def __init__(self, source: str) -> None:
            self.axes = "CYX"

        def __enter__(self) -> "_FakeCziFile":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            return raw

        def metadata(self) -> str:
            return xml

    monkeypatch.setitem(sys.modules, "czifile", SimpleNamespace(CziFile=_FakeCziFile))

    result = io_engine.read_microscopy_image(path)

    assert np.array_equal(result.analysis_data, raw)
    assert result.channel_names == ["DAPI", "GFP"]
    assert result.metadata["pixel_size_um"] == pytest.approx(0.5)
    assert result.metadata["pixel_size_x_um"] == pytest.approx(0.5)
    assert result.metadata["pixel_size_y_um"] == pytest.approx(0.25)


def test_tiny_nd2_read_preserves_xy_without_inventing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "aniso.nd2"
    path.write_bytes(b"fake-nd2")
    raw = np.arange(2 * 3 * 4, dtype=np.uint16).reshape(2, 3, 4)

    class _FakeND2File:
        def __init__(self, source: Path) -> None:
            self.sizes = {"C": 2, "Y": 3, "X": 4}
            self.axes = "CYX"
            self.metadata = SimpleNamespace(channels=[SimpleNamespace(channel="DAPI"), SimpleNamespace(channel="FITC")])

        def __enter__(self) -> "_FakeND2File":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def asarray(self) -> np.ndarray:
            return raw

        def voxel_size(self) -> SimpleNamespace:
            return SimpleNamespace(x=0.325, y=0.25)

    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "nd2":
            return SimpleNamespace(ND2File=_FakeND2File)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    result = io_engine.read_microscopy_image(path)

    assert np.array_equal(result.analysis_data, raw)
    assert result.channel_names == ["DAPI", "FITC"]
    assert result.metadata["pixel_size_um"] == pytest.approx(0.325)
    assert result.metadata["pixel_size_x_um"] == pytest.approx(0.325)
    assert result.metadata["pixel_size_y_um"] == pytest.approx(0.25)


def test_tiny_nd2_x_only_does_not_invent_y(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "x-only.nd2"
    path.write_bytes(b"fake-nd2-x")
    raw = np.arange(1 * 2 * 3, dtype=np.uint16).reshape(1, 2, 3)

    class _FakeND2File:
        def __init__(self, source: Path) -> None:
            self.sizes = {"C": 1, "Y": 2, "X": 3}
            self.axes = "CYX"
            self.metadata = SimpleNamespace(channels=[SimpleNamespace(channel="DAPI")])

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

    assert np.array_equal(result.analysis_data, raw)
    assert result.metadata["pixel_size_um"] == pytest.approx(0.325)
    assert result.metadata["pixel_size_x_um"] == pytest.approx(0.325)
    assert _positive_um(result.metadata.get("pixel_size_y_um")) is None
