from __future__ import annotations

import gc
import json
from pathlib import Path
import tempfile
import weakref
from zipfile import ZipFile

import numpy as np
import pytest
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from core.constants import SFIG_REQUIRED_FILES
from core.image_analysis.brightfield_guided import (
    BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION,
    BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION,
)
from core.models import BrightfieldPanel, ExternalImagePanel, FluorescencePanel, IHCPanel, Project, QPCRPanel, WesternBlotPanel
from core.models.composer import ComposerLayout
from core.densitometry import measure_densitometry
from core.utils.hashing import sha256_bytes, sha256_json
import core.sfig_io as sfig_io


def _project_with_panels() -> Project:
    project = Project(title="Archive Test")
    project.append_audit("create_project", "Archive Test")
    qpcr = QPCRPanel(panel_id="Figure1A")
    qpcr.data.update(
        {
            "raw_source": "qpcr_raw.xlsx",
            "raw_hash": "sha256:" + "a" * 64,
            "raw_copy_path": "data/qpcr/qpcr_raw.xlsx",
        }
    )
    qpcr.append_audit("import_data", "qpcr_raw.xlsx")
    project.add_panel(qpcr)
    project.add_panel(WesternBlotPanel(panel_id="Figure1B"))
    project.add_panel(FluorescencePanel(panel_id="Figure1C"))
    layout = ComposerLayout.create("2x2")
    layout.place_panel(qpcr.panel_uuid)
    project.set_composer_layout(layout)
    return project


def _write_wb_strip(path: Path, lane_values: list[int], width: int = 120, height: int = 30) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(0, 0, 0))
    painter = QPainter(image)
    lane_width = width // len(lane_values)
    for index, value in enumerate(lane_values):
        painter.setBrush(QColor(value, value, value))
        painter.setPen(QColor(value, value, value))
        painter.drawRect(index * lane_width + 8, 8, lane_width - 16, height - 16)
    painter.end()
    assert image.save(str(path))
    return path


def _write_raster_image(path: Path, color: QColor, width: int = 32, height: int = 24) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    assert image.save(str(path))
    return path


def _write_synthetic_brightfield_he_png(path: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 246, 246))

    for y in range(1, 5):
        for x in range(2, 6):
            image.setPixelColor(x, y, QColor(235, 170, 190))

    assert image.save(str(path))
    return path


def _write_minimal_archive(target: Path, project_payload: dict[str, object]) -> None:
    manifest = {
        "schema_version": str(project_payload.get("schema_version", "2.3.0")),
        "software_version": str(project_payload.get("software_version", "0.1.0")),
        "project_uuid": str(project_payload.get("project_uuid", "")),
        "project_json_hash": sha256_json(project_payload),
    }
    with ZipFile(target, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("project.json", json.dumps(project_payload))


def _assert_unique_archive_members(target: Path) -> list[str]:
    with ZipFile(target) as archive:
        names = archive.namelist()
    assert len(names) == len(set(names))
    return names


def _assert_no_embedded_asset_fields(images: list[dict[str, object]]) -> None:
    for image in images:
        assert "embedded_image_path" not in image
        assert "embedded_data_path" not in image


def _rewrite_archive(source: Path, target: Path, *, omit: set[str] | None = None, replacements: dict[str, bytes] | None = None) -> None:
    omit = omit or set()
    replacements = replacements or {}
    with ZipFile(source, "r") as source_archive, ZipFile(target, "w") as target_archive:
        for info in source_archive.infolist():
            if info.filename in omit:
                continue
            payload = replacements.get(info.filename, source_archive.read(info.filename))
            target_archive.writestr(info, payload)


def test_sfig_save_writes_required_archive_structure(tmp_path) -> None:
    project = _project_with_panels()
    target = tmp_path / "archive_test.sfig"
    project.save_sfig(target)

    assert target.exists()
    with ZipFile(target) as archive:
        names = set(archive.namelist())
        for required_file in SFIG_REQUIRED_FILES:
            assert required_file in names
        assert "data/" in names
        assert "panels/" in names
        assert "thumbnails/" in names
        assert "style/" in names
        assert "exports/" in names
        assert "composer/" in names

        manifest = json.loads(archive.read("manifest.json"))
        project_json = json.loads(archive.read("project.json"))
        composer_path = project_json["composer_figures"][0]["file"]
        composer_json = json.loads(archive.read(composer_path))
        audit_log = archive.read("logs/project_audit.log").decode("utf-8")

    assert "qpcr_raw.xlsx" not in manifest["raw_hashes"]
    assert all(member in names for member in manifest["raw_hashes"])
    assert manifest["schema_version"] == "2.3.0"
    assert project_json["title"] == "Archive Test"
    assert len(project_json["panels"]) == 3
    assert project_json["composer_figures"][0]["figure_id"] == "Figure 1"
    assert composer_json["panel_refs"][0]["panel_uid"] == project.panels[0].panel_uuid
    assert "save_project" in audit_log


def test_sfig_save_fault_preserves_existing_target_and_removes_temp(tmp_path, monkeypatch) -> None:
    project = Project(title="Atomic Fault")
    panel = IHCPanel(panel_id="Figure1D")
    source_path = _write_raster_image(tmp_path / "atomic_source.png", QColor(50, 100, 150))
    panel.import_image(source_path)
    project.add_panel(panel)
    target = tmp_path / "atomic_fault.sfig"
    project.save_sfig(target)
    original_bytes = target.read_bytes()

    def fail_write(*args, **kwargs):
        raise OSError("injected archive write failure")

    monkeypatch.setattr(sfig_io.ZipFile, "write", fail_write)
    with pytest.raises(OSError, match="injected archive write failure"):
        project.save_sfig(target)

    assert target.read_bytes() == original_bytes
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


def test_sfig_save_success_replaces_atomically_and_loads(tmp_path, monkeypatch) -> None:
    project = Project(title="Atomic Before")
    target = tmp_path / "atomic_success.sfig"
    project.save_sfig(target)
    project.title = "Atomic After"
    real_replace = sfig_io.os.replace
    replace_calls: list[tuple[Path, Path]] = []

    def checked_replace(source, destination):
        temporary_path = Path(source)
        destination_path = Path(destination)
        assert temporary_path.parent == target.parent
        assert temporary_path.exists()
        with ZipFile(temporary_path) as archive:
            assert archive.testzip() is None
        replace_calls.append((temporary_path, destination_path))
        real_replace(source, destination)

    monkeypatch.setattr(sfig_io.os, "replace", checked_replace)
    project.save_sfig(target)

    assert len(replace_calls) == 1
    assert replace_calls[0][1] == target
    assert not replace_calls[0][0].exists()
    assert Project.load_sfig(target).title == "Atomic After"


def test_sfig_save_referenced_missing_assets_fails_closed_and_preserves_target(tmp_path) -> None:
    cases: list[tuple[str, Project, list[Path], object | None]] = []

    fluorescence = FluorescencePanel(panel_id="Figure1C")
    fluorescence_source = _write_raster_image(tmp_path / "missing_fluorescence.png", QColor(20, 30, 40))
    fluorescence.import_image(fluorescence_source)
    fluorescence_project = Project(title="Missing Fluorescence")
    fluorescence_project.add_panel(fluorescence)
    cases.append(("fluorescence", fluorescence_project, [fluorescence_source], None))

    ihc = IHCPanel(panel_id="Figure1D")
    ihc_source = _write_raster_image(tmp_path / "missing_ihc.png", QColor(40, 50, 60))
    ihc.import_image(ihc_source)
    ihc_project = Project(title="Missing IHC")
    ihc_project.add_panel(ihc)
    cases.append(("IHC", ihc_project, [ihc_source], None))

    brightfield = BrightfieldPanel(panel_id="Figure1F")
    brightfield_source = _write_raster_image(tmp_path / "missing_brightfield.png", QColor(60, 70, 80))
    brightfield.import_image(brightfield_source)
    brightfield_project = Project(title="Missing Brightfield")
    brightfield_project.add_panel(brightfield)
    cases.append(("Brightfield", brightfield_project, [brightfield_source], None))

    external = ExternalImagePanel(panel_id="Figure1E")
    external_source = _write_raster_image(tmp_path / "missing_external.png", QColor(80, 90, 100))
    external.import_image(external_source)
    external_project = Project(title="Missing External")
    external_project.add_panel(external)
    cases.append(("external", external_project, [external_source], None))

    wb_image = WesternBlotPanel(panel_id="Figure1B")
    wb_image_source = _write_wb_strip(tmp_path / "missing_wb.png", [40, 80, 120])
    wb_image.import_full_blot(wb_image_source)
    wb_image_project = Project(title="Missing WB Image")
    wb_image_project.add_panel(wb_image)
    cases.append(("WB image", wb_image_project, [wb_image_source], None))

    wb_strip = WesternBlotPanel(panel_id="Figure1G")
    wb_strip_full = _write_wb_strip(tmp_path / "wb_strip_full.png", [40, 80, 120])
    wb_strip_source = _write_wb_strip(tmp_path / "missing_wb_strip.png", [120, 80, 40])
    wb_strip.import_full_blot(wb_strip_full)
    wb_strip.data["band_strips"][0]["image_path"] = str(wb_strip_source)
    wb_strip_project = Project(title="Missing WB Strip")
    wb_strip_project.add_panel(wb_strip)
    cases.append(("WB strip", wb_strip_project, [wb_strip_source], None))

    for label, project, source_paths, _ in cases:
        target = tmp_path / f"missing_{label.replace(' ', '_')}.sfig"
        project.save_sfig(target)
        original_bytes = target.read_bytes()
        for source_path in source_paths:
            source_path.unlink()
        if label == "fluorescence":
            fluorescence._image_cache.clear()
        with pytest.raises(ValueError):
            project.save_sfig(target)
        assert target.read_bytes() == original_bytes
        assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


def test_sfig_empty_optional_image_panels_remain_valid(tmp_path) -> None:
    project = Project(title="Empty Optional Assets")
    project.add_panel(FluorescencePanel(panel_id="Figure1C"))
    project.add_panel(IHCPanel(panel_id="Figure1D"))
    project.add_panel(BrightfieldPanel(panel_id="Figure1F"))
    project.add_panel(ExternalImagePanel(panel_id="Figure1E"))
    project.add_panel(WesternBlotPanel(panel_id="Figure1B"))
    target = tmp_path / "empty_optional.sfig"

    project.save_sfig(target)

    assert Project.load_sfig(target).title == "Empty Optional Assets"


def test_sfig_load_preserves_panel_count_and_key_fields(tmp_path) -> None:
    project = _project_with_panels()
    target = tmp_path / "round_trip.sfig"
    project.save_sfig(target)

    restored = Project.load_sfig(target)

    assert restored.project_uuid == project.project_uuid
    assert restored.title == project.title
    assert len(restored.panels) == len(project.panels)
    assert restored.panels[0].panel_id == "Figure1A"
    assert restored.panels[0].panel_type == "qPCR_bar_scatter"
    assert restored.panels[0].source_module == "qpcr"
    assert restored.panels[0].data["raw_hash"] == "sha256:" + "a" * 64
    assert restored.ensure_active_composer_figure().panel_refs[0].panel_uid == restored.panels[0].panel_uuid
    assert any(entry.action == "load_project" for entry in restored.audit_log)
    restored.dispose()


def test_project_archive_extract_root_lifetime_cleanup_is_idempotent_and_nonserialized(tmp_path) -> None:
    project = Project(title="Transient Archive Root")
    extract_root = Path(tempfile.mkdtemp(prefix="sciefig_project_lifecycle_"))
    (extract_root / "asset.bin").write_bytes(b"asset")
    project.archive_extract_root = extract_root

    assert "archive_extract_root" not in project.to_dict()
    assert extract_root.exists()

    project.dispose()
    project.dispose()

    assert not extract_root.exists()


def test_project_dispose_does_not_delete_unowned_user_files(tmp_path) -> None:
    project = Project(title="Unowned Archive Root")
    user_root = tmp_path / "user_assets"
    user_root.mkdir()
    marker = user_root / "keep.bin"
    marker.write_bytes(b"keep")
    project.archive_extract_root = user_root

    project.dispose()

    assert marker.read_bytes() == b"keep"


def test_sfig_load_automatically_cleans_extract_root_when_project_is_unreachable(tmp_path) -> None:
    project = _project_with_panels()
    target = tmp_path / "automatic_cleanup.sfig"
    project.save_sfig(target)

    def load_and_drop_project() -> tuple[Path, weakref.ReferenceType[Project]]:
        restored = Project.load_sfig(target)
        extract_root = restored.archive_extract_root
        assert extract_root is not None
        assert extract_root.exists()
        return extract_root, weakref.ref(restored)

    extract_root, project_ref = load_and_drop_project()

    gc.collect()

    assert project_ref() is None
    assert not extract_root.exists()


def test_sfig_load_failure_after_extraction_cleans_new_root(monkeypatch, tmp_path) -> None:
    project = Project(title="Failed Extraction Cleanup")
    panel = ExternalImagePanel(panel_id="Figure1E")
    source_path = _write_raster_image(tmp_path / "source.png", QColor(30, 90, 180))
    panel.import_image(source_path)
    project.add_panel(panel)
    target = tmp_path / "failed_load.sfig"
    project.save_sfig(target)
    source_path.unlink()

    temp_dir = Path(tempfile.gettempdir())
    existing_roots = set(temp_dir.glob("sciefig_failed_load_*"))

    def fail_remap(*args, **kwargs):
        raise RuntimeError("remap failed")

    monkeypatch.setattr(sfig_io, "_remap_extracted_paths", fail_remap)
    with pytest.raises(RuntimeError, match="remap failed"):
        Project.load_sfig(target)

    assert set(temp_dir.glob("sciefig_failed_load_*")) == existing_roots


def test_sfig_load_migrates_legacy_2_1_1_project_payload(tmp_path) -> None:
    project = _project_with_panels()
    legacy_payload = project.to_dict()
    legacy_payload["schema_version"] = "2.1.1"
    legacy_payload["panels"][0]["schema_version"] = "2.1.1"
    legacy_payload["panels"][0].pop("source_module", None)
    legacy_payload.pop("panel_registry", None)
    legacy_payload.pop("composer_figures", None)
    legacy_payload.pop("active_figure_uid", None)
    target = tmp_path / "legacy.sfig"
    with ZipFile(target, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": "2.1.1",
                    "software_version": "0.1.0",
                    "project_uuid": project.project_uuid,
                    "project_json_hash": sha256_json(legacy_payload),
                }
            ),
        )
        archive.writestr("project.json", json.dumps(legacy_payload))

    restored = Project.load_sfig(target)

    assert restored.schema_version == "2.3.0"
    assert restored.panels[0].schema_version == "2.3.0"
    assert restored.panels[0].source_module == "qpcr"
    assert restored.ensure_active_composer_figure().panel_refs[0].panel_uid == restored.panels[0].panel_uuid
    assert any(entry.action == "migrate_schema" for entry in restored.audit_log)


def test_sfig_load_restores_wb_strip_images_from_archive(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(title="WB Strip Archive")
    panel = WesternBlotPanel(panel_id="Figure1B")
    full_blot = _write_wb_strip(tmp_path / "full_blot.png", [40, 70, 100], width=160, height=60)
    target_strip = _write_wb_strip(tmp_path / "target_strip.png", [120, 220, 180])
    loading_strip = _write_wb_strip(tmp_path / "loading_strip.png", [160, 160, 160])
    panel.import_full_blot(full_blot)
    panel.data["placed_strip_ids"] = ["strip_1", "strip_2"]
    panel.data["annotations"] = {
        "lane_labels": ["Control", "KO", "Rescue"],
        "lane_anchors": [20.0, 60.0, 100.0],
    }
    panel.data["band_strips"] = [
        {
            "strip_id": "strip_1",
            "protein_name": "SPNS1",
            "image_path": str(target_strip),
            "crop_roi": {"x": 0, "y": 0, "w": 120, "h": 30},
            "adjustments": {"brightness": 0, "contrast": 0, "rotation": 0.0, "flip_horizontal": False, "flip_vertical": False},
        },
        {
            "strip_id": "strip_2",
            "protein_name": "GAPDH",
            "image_path": str(loading_strip),
            "crop_roi": {"x": 0, "y": 0, "w": 120, "h": 30},
            "adjustments": {"brightness": 0, "contrast": 0, "rotation": 0.0, "flip_horizontal": False, "flip_vertical": False},
        },
    ]
    project.add_panel(panel)
    target = tmp_path / "wb_strip_roundtrip.sfig"
    project.save_sfig(target)

    full_blot.unlink()
    target_strip.unlink()
    loading_strip.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, WesternBlotPanel))
    restored_paths = [Path(str(strip.get("image_path", ""))) for strip in restored_panel.data["band_strips"]]

    assert all(path.exists() for path in restored_paths)
    assert restored_panel.data["band_strips"][0]["resolved_image_path"] == str(restored_paths[0])
    assert not restored_panel.render_preview(89, 60).isNull()

    result = measure_densitometry(
        restored_panel.data,
        loading_control_strip_id="strip_2",
        reference_lane_index=0,
        background_method="none",
        polarity_mode="dark_on_light",
    )
    assert result["measurements"][0]["strip_id"] == "strip_1"
    app.processEvents()


def test_sfig_load_restores_ihc_images_from_archive(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(title="IHC Archive")
    panel = IHCPanel(panel_id="Figure1D")
    source_path = _write_raster_image(tmp_path / "ihc_source.png", QColor(180, 110, 70))
    record = panel.import_image(source_path, group="Tumor")
    measurement = panel.auto_analyze_dab(image_uid=record["uid"])
    project.add_panel(panel)
    target = tmp_path / "ihc_roundtrip.sfig"

    project.save_sfig(target)

    with ZipFile(target) as archive:
        project_json = json.loads(archive.read("project.json"))
        payload_images = project_json["panels"][0]["data"]["ihc_images"]
        _assert_no_embedded_asset_fields(payload_images)
        archive_paths = [
            name
            for name in archive.namelist()
            if name.startswith(f"data/ihc/{panel.panel_uuid}/") and name.endswith(f"/{record['source_name']}")
        ]
        assert len(archive_paths) == 1
    _assert_unique_archive_members(target)

    source_path.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, IHCPanel))
    restored_image = restored_panel.data["ihc_images"][0]
    restored_source_path = Path(str(restored_image["source_path"]))
    restored_measurement = restored_panel.data["ihc_images"][0]["measurements"][0]

    assert restored_source_path.exists()
    assert restored_measurement["model_hash"] == measurement["model_hash"]
    assert restored_measurement["include_in_statistics"] == measurement["include_in_statistics"]
    assert restored_measurement["analysis_metadata"]["threshold_source"] == measurement["analysis_metadata"]["threshold_source"]
    assert restored_image["metadata"]["source_format"] == "PNG"
    assert restored_image["metadata"]["n_channels"] == 3
    assert restored_image["metadata"]["pixel_size_unit"] == "um"
    assert restored_image["metadata"]["bit_depth"] == 8
    assert restored_image["metadata"]["axes"] == "CYX"
    assert restored_image["metadata"]["work_data_downsample"] == 1
    assert restored_image["metadata"]["io_warnings"] == []
    assert not restored_panel.render_preview(40, 30, dpi=150).isNull()
    app.processEvents()


def test_sfig_load_migrates_legacy_ihc_binary_hscore_without_touching_panel_modified_at(tmp_path) -> None:
    project = Project(title="Legacy IHC H-score")
    panel = IHCPanel(panel_id="Figure1D")
    measurement = {
        "measurement_uuid": "legacy-m-1",
        "image_uid": "legacy-img-1",
        "results": {
            "h_score": 142.86,
            "positive_percent": 47.62,
            "weak_percent": 0.0,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "negative_percent": 52.38,
        },
        "analysis_metadata": {
            "gmm_classification": {
                "h_score_method": "binary_simplified_3x_positive",
                "gmm_n_components": 2,
            }
        },
    }
    panel.data["ihc_images"] = [
        {
            "uid": "legacy-img-1",
            "source_name": "legacy.png",
            "source_path": "legacy.png",
            "measurements": [json.loads(json.dumps(measurement))],
        }
    ]
    panel.analysis["ihc_measurements"] = [measurement]
    project.add_panel(panel)
    payload = project.to_dict()
    panel_payload = payload["panels"][0]
    original_panel_modified_at = panel_payload["modified_at"]
    target = tmp_path / "legacy_ihc_hscore.sfig"
    _write_minimal_archive(target, payload)

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, IHCPanel))
    restored_measurement = restored_panel.analysis["ihc_measurements"][0]
    restored_image_measurement = restored_panel.data["ihc_images"][0]["measurements"][0]
    gmm = restored_measurement["analysis_metadata"]["gmm_classification"]

    assert restored_panel.modified_at == original_panel_modified_at
    assert restored_measurement["results"]["h_score"] == pytest.approx(47.62)
    assert restored_measurement["results"]["weak_percent"] == pytest.approx(47.62)
    assert restored_image_measurement["results"]["h_score"] == pytest.approx(47.62)
    assert gmm["h_score_method"] == "gmm_2_component_positive_conservative"
    assert gmm["_pre_migration_h_score"] == pytest.approx(142.86)
    assert any(entry.action == "h_score_migration_v611" for entry in restored_panel.audit_log)


def test_sfig_load_restores_brightfield_images_from_archive(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(title="Brightfield Archive")
    panel = BrightfieldPanel(panel_id="Figure1F")
    source_path = _write_raster_image(tmp_path / "brightfield_source.png", QColor(210, 40, 40))
    record = panel.import_image(source_path, group="Control", stain_type="H&E")
    measurement = panel.auto_analyze_he(image_uid=record["uid"])
    panel.apply_batch_threshold_from_measurement(measurement["measurement_uuid"], lock=True)
    project.add_panel(panel)
    target = tmp_path / "brightfield_roundtrip.sfig"

    project.save_sfig(target)

    with ZipFile(target) as archive:
        project_json = json.loads(archive.read("project.json"))
        payload_images = project_json["panels"][0]["data"]["brightfield_images"]
        _assert_no_embedded_asset_fields(payload_images)
        archive_paths = [
            name
            for name in archive.namelist()
            if name.startswith(f"data/brightfield/{panel.panel_uuid}/") and name.endswith(f"/{record['source_name']}")
        ]
        assert len(archive_paths) == 1
    _assert_unique_archive_members(target)

    source_path.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, BrightfieldPanel))
    restored_image = restored_panel.data["brightfield_images"][0]
    restored_source_path = Path(str(restored_image["source_path"]))
    restored_measurement = restored_panel.data["brightfield_images"][0]["measurements"][0]

    assert restored_source_path.exists()
    assert restored_measurement["model_hash"] == measurement["model_hash"]
    assert restored_measurement["include_in_statistics"] is True
    assert restored_measurement["results"]["object_count"] == measurement["results"]["object_count"]
    assert restored_measurement["analysis_metadata"]["object_count_semantics"] == measurement["analysis_metadata"]["object_count_semantics"]
    assert restored_measurement["analysis_metadata"]["threshold_source"] == measurement["analysis_metadata"]["threshold_source"]
    assert restored_panel.data["guided_state"]["batch_threshold_state"]["locked"] is True
    assert restored_panel.data["guided_state"]["batch_threshold_state"]["batch_threshold"] == pytest.approx(measurement["params"]["a_min"])
    assert restored_image["metadata"]["source_format"] == "PNG"
    assert restored_image["metadata"]["n_channels"] == 3
    assert restored_image["metadata"]["pixel_size_unit"] == "um"
    assert restored_image["metadata"]["bit_depth"] == 8
    assert restored_image["metadata"]["axes"] == "CYX"
    assert restored_image["metadata"]["work_data_downsample"] == 1
    assert restored_image["metadata"]["io_warnings"] == []
    assert not restored_panel.render_preview(40, 30, dpi=150).isNull()
    app.processEvents()


def test_sfig_load_restores_same_basename_fluorescence_assets_distinct(tmp_path) -> None:
    project = Project(title="Fluorescence Collision Archive")
    panel = FluorescencePanel(panel_id="Figure1C")
    source_dir_a = tmp_path / "fluoro_a"
    source_dir_b = tmp_path / "fluoro_b"
    source_dir_a.mkdir()
    source_dir_b.mkdir()
    source_a = _write_raster_image(source_dir_a / "shared.png", QColor(220, 30, 30))
    source_b = _write_raster_image(source_dir_b / "shared.png", QColor(30, 30, 220))
    record_a = panel.import_image(source_a)
    record_b = panel.import_image(source_b)
    expected = {
        record_a["uid"]: panel.image_data(record_a["uid"]).copy(),
        record_b["uid"]: panel.image_data(record_b["uid"]).copy(),
    }
    project.add_panel(panel)
    target = tmp_path / "fluorescence_collision_roundtrip.sfig"

    project.save_sfig(target)

    with ZipFile(target) as archive:
        project_json = json.loads(archive.read("project.json"))
        payload_images = project_json["panels"][0]["data"]["fluorescence_images"]
        _assert_no_embedded_asset_fields(payload_images)
        archive_paths = [
            name
            for name in archive.namelist()
            if name.startswith(f"data/fluorescence/{panel.panel_uuid}/") and name.endswith("/shared.png")
        ]
        assert len(archive_paths) == 2
        assert len(set(archive_paths)) == 2
    _assert_unique_archive_members(target)

    source_a.unlink()
    source_b.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, FluorescencePanel))
    restored_images = restored_panel.data["fluorescence_images"]
    assert [image["uid"] for image in restored_images] == [record_a["uid"], record_b["uid"]]
    assert np.array_equal(restored_panel.image_data(record_a["uid"]), expected[record_a["uid"]])
    assert np.array_equal(restored_panel.image_data(record_b["uid"]), expected[record_b["uid"]])
    assert restored_images[0]["source_path"] != restored_images[1]["source_path"]


def test_sfig_load_restores_same_basename_ihc_and_brightfield_assets_distinct(tmp_path) -> None:
    project = Project(title="Histology Collision Archive")
    ihc_panel = IHCPanel(panel_id="Figure1D")
    brightfield_panel = BrightfieldPanel(panel_id="Figure1F")
    ihc_dir_a = tmp_path / "ihc_a"
    ihc_dir_b = tmp_path / "ihc_b"
    brightfield_dir_a = tmp_path / "brightfield_a"
    brightfield_dir_b = tmp_path / "brightfield_b"
    for directory in (ihc_dir_a, ihc_dir_b, brightfield_dir_a, brightfield_dir_b):
        directory.mkdir()
    ihc_a = _write_raster_image(ihc_dir_a / "shared.png", QColor(170, 80, 40))
    ihc_b = _write_raster_image(ihc_dir_b / "shared.png", QColor(60, 160, 220))
    brightfield_a = _write_raster_image(brightfield_dir_a / "shared.png", QColor(220, 40, 40))
    brightfield_b = _write_raster_image(brightfield_dir_b / "shared.png", QColor(40, 220, 40))
    ihc_panel.import_image(ihc_a, group="Tumor")
    ihc_panel.import_image(ihc_b, group="Control")
    brightfield_panel.import_image(brightfield_a, group="Tumor", stain_type="H&E")
    brightfield_panel.import_image(brightfield_b, group="Control", stain_type="H&E")
    project.add_panel(ihc_panel)
    project.add_panel(brightfield_panel)
    target = tmp_path / "histology_collision_roundtrip.sfig"

    project.save_sfig(target)

    with ZipFile(target) as archive:
        project_json = json.loads(archive.read("project.json"))
        for panel_payload, key, kind in (
            (project_json["panels"][0], "ihc_images", "ihc"),
            (project_json["panels"][1], "brightfield_images", "brightfield"),
        ):
            payload_images = panel_payload["data"][key]
            _assert_no_embedded_asset_fields(payload_images)
            archive_paths = [
                name
                for name in archive.namelist()
                if name.startswith(f"data/{kind}/{panel_payload['panel_uuid']}/") and name.endswith("/shared.png")
            ]
            assert len(archive_paths) == 2
            assert len(set(archive_paths)) == 2
    _assert_unique_archive_members(target)

    for source_path in (ihc_a, ihc_b, brightfield_a, brightfield_b):
        source_path.unlink()

    restored = Project.load_sfig(target)
    restored_ihc = next(item for item in restored.panels if isinstance(item, IHCPanel))
    restored_brightfield = next(item for item in restored.panels if isinstance(item, BrightfieldPanel))
    ihc_paths = [Path(str(image["source_path"])) for image in restored_ihc.data["ihc_images"]]
    brightfield_paths = [Path(str(image["source_path"])) for image in restored_brightfield.data["brightfield_images"]]
    assert all(path.exists() for path in ihc_paths + brightfield_paths)
    assert QImage(str(ihc_paths[0])).pixelColor(0, 0) != QImage(str(ihc_paths[1])).pixelColor(0, 0)
    assert QImage(str(brightfield_paths[0])).pixelColor(0, 0) != QImage(str(brightfield_paths[1])).pixelColor(0, 0)


def test_sfig_load_restores_merged_cached_fluorescence_pixel_exact(tmp_path) -> None:
    project = Project(title="Merged Fluorescence Archive")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.IMAGE_CACHE_BYTE_BUDGET = 1
    source_a = _write_raster_image(tmp_path / "channel_a.png", QColor(220, 30, 30))
    source_b = _write_raster_image(tmp_path / "channel_b.png", QColor(30, 220, 30))
    first = panel.import_image(source_a)
    second = panel.import_image(source_b)
    merged = panel.merge_images_as_channels([first["uid"], second["uid"]])
    expected = panel.image_data(merged["uid"]).copy()
    panel_payload = panel.to_dict()
    assert "_spilled_image_paths" not in panel_payload
    assert "_spill_directory" not in panel_payload
    assert "sciefig-fluorescence-" not in str(panel_payload)
    project.add_panel(panel)
    project_payload = project.to_dict()
    assert "_spilled_image_paths" not in project_payload["panels"][0]
    assert "sciefig-fluorescence-" not in str(project_payload)
    target = tmp_path / "merged_fluorescence_roundtrip.sfig"

    project.save_sfig(target)

    assert "embedded_data_path" not in merged
    with ZipFile(target) as archive:
        project_json = json.loads(archive.read("project.json"))
        manifest = json.loads(archive.read("manifest.json"))
        payload_images = project_json["panels"][0]["data"]["fluorescence_images"]
        _assert_no_embedded_asset_fields(payload_images)
        merged_payload = project_json["panels"][0]["data"]["fluorescence_images"][-1]
        analysis_paths = [
            name
            for name in archive.namelist()
            if name.startswith(f"data/fluorescence/{panel.panel_uuid}/") and name.endswith("/analysis.npy")
        ]
        assert len(analysis_paths) == 1
        analysis_path = analysis_paths[0]
        nonexistent_source_path = f"{Path(analysis_path).parent.as_posix()}/{merged_payload['source_name']}"
        assert analysis_path in manifest["raw_hashes"]
        assert nonexistent_source_path not in manifest["raw_hashes"]
        assert nonexistent_source_path not in archive.namelist()
        with archive.open(analysis_path) as handle:
            archived_data = np.load(handle, allow_pickle=False)
        assert np.array_equal(archived_data, expected)
    _assert_unique_archive_members(target)

    source_a.unlink()
    source_b.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, FluorescencePanel))
    restored_record = next(image for image in restored_panel.data["fluorescence_images"] if image["uid"] == merged["uid"])
    restored_data = restored_panel.image_data(merged["uid"])
    assert restored_data is not None
    assert restored_data.shape == expected.shape
    assert restored_data.dtype == expected.dtype
    assert np.array_equal(restored_data, expected)
    assert restored_record["analysis_data_shape"] == list(expected.shape)
    assert restored_record["analysis_dtype"] == str(expected.dtype)
    # RGB backing planes preserve appearance but are not measurement channels.
    assert restored_record["metadata"]["n_channels"] == merged["merged_signal_channels"]
    assert len(restored_record["channels"]) == merged["merged_signal_channels"]
    assert restored_record["metadata"]["axes"] == "CYX"


def test_sfig_load_restores_ordinary_fluorescence_source_backed_image(tmp_path) -> None:
    project = Project(title="Ordinary Fluorescence Archive")
    panel = FluorescencePanel(panel_id="Figure1C")
    source_path = _write_raster_image(tmp_path / "ordinary.png", QColor(100, 40, 210))
    record = panel.import_image(source_path)
    expected = panel.image_data(record["uid"]).copy()
    project.add_panel(panel)
    target = tmp_path / "ordinary_fluorescence_roundtrip.sfig"

    project.save_sfig(target)

    with ZipFile(target) as archive:
        project_json = json.loads(archive.read("project.json"))
        payload_images = project_json["panels"][0]["data"]["fluorescence_images"]
        _assert_no_embedded_asset_fields(payload_images)
        archive_paths = [
            name
            for name in archive.namelist()
            if name.startswith(f"data/fluorescence/{panel.panel_uuid}/") and name.endswith(f"/{record['source_name']}")
        ]
        assert len(archive_paths) == 1
    _assert_unique_archive_members(target)
    source_path.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, FluorescencePanel))
    restored_record = restored_panel.data["fluorescence_images"][0]
    assert Path(str(restored_record["source_path"])).exists()
    assert np.array_equal(restored_panel.image_data(record["uid"]), expected)


def test_sfig_load_restores_assisted_brightfield_threshold_bias_metadata_and_hash(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(title="Brightfield Assisted Archive")
    panel = BrightfieldPanel(panel_id="Figure1FAssisted")
    source_path = _write_synthetic_brightfield_he_png(tmp_path / "brightfield_assisted_source.png")
    record = panel.import_image(source_path, group="Control", stain_type="H&E")
    measurement = panel.auto_analyze_he(
        image_uid=record["uid"],
        guided_mode="assisted",
        settings={
            "sensitivity": "low",
            "cleanup_level": "high",
            "threshold_bias": 1,
            "small_region_filter_bias": 2,
        },
    )
    project.add_panel(panel)
    target = tmp_path / "brightfield_assisted_roundtrip.sfig"

    project.save_sfig(target)
    source_path.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, BrightfieldPanel))
    restored_measurement = restored_panel.analysis["brightfield_measurements"][0]

    assert restored_measurement["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert restored_measurement["guided_effective_settings"]["mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert restored_measurement["guided_effective_settings_hash"] == measurement["guided_effective_settings_hash"]
    assert restored_measurement["guided_slider_values"] == measurement["guided_slider_values"]
    assert restored_measurement["analysis_metadata"]["assisted_threshold_bias"] == 1
    assert restored_measurement["analysis_metadata"]["assisted_threshold_bias_active"] is True
    assert restored_measurement["analysis_metadata"]["assisted_threshold_bias_inactive_reason"] is None
    assert restored_measurement["analysis_metadata"]["assisted_small_region_filter_bias"] == 2
    assert restored_measurement["analysis_metadata"]["assisted_small_region_filter_mapping_version"] == (
        BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION
    )
    assert restored_measurement["analysis_metadata"]["assisted_effective_min_area_px"] == measurement["analysis_metadata"]["assisted_effective_min_area_px"]
    assert restored_panel.data["guided_state"]["assisted_threshold_bias"] == 1
    assert restored_panel.data["guided_state"]["assisted_small_region_filter_bias"] == 2
    assert not restored_panel.render_preview(40, 30, dpi=150).isNull()
    app.processEvents()


def test_sfig_load_legacy_brightfield_payload_preserves_results_object_count_key(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(title="Legacy Brightfield Object Count")
    panel = BrightfieldPanel(panel_id="Figure1FLegacy")
    source_path = _write_raster_image(tmp_path / "legacy_brightfield_source.png", QColor(210, 40, 40))
    record = panel.import_image(source_path, group="Control", stain_type="H&E")
    measurement = panel.auto_analyze_he(image_uid=record["uid"])
    project.add_panel(panel)

    legacy_payload = project.to_dict()
    legacy_payload["schema_version"] = "2.1.1"
    legacy_payload["panels"][0]["schema_version"] = "2.1.1"
    legacy_payload["panels"][0].pop("source_module", None)
    legacy_measurement = legacy_payload["panels"][0]["analysis"]["brightfield_measurements"][0]
    legacy_measurement["results"] = dict(legacy_measurement["results"])
    legacy_measurement["results"]["object_count"] = measurement["results"]["object_count"]
    target = tmp_path / "legacy_brightfield_object_count.sfig"

    _write_minimal_archive(target, legacy_payload)
    source_path.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, BrightfieldPanel))
    restored_measurement = restored_panel.analysis["brightfield_measurements"][0]

    assert restored_measurement["results"]["object_count"] == measurement["results"]["object_count"]
    assert restored_measurement["analysis_metadata"]["object_count_semantics"] == measurement["analysis_metadata"]["object_count_semantics"]
    app.processEvents()


def test_sfig_load_restores_external_image_from_archive_after_source_deleted(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(title="External Archive")
    panel = ExternalImagePanel(panel_id="Figure1E")
    source_path = _write_raster_image(tmp_path / "external_source.png", QColor(70, 110, 220))
    panel.import_image(source_path)
    project.add_panel(panel)
    target = tmp_path / "external_roundtrip.sfig"

    project.save_sfig(target)

    expected_archive_path = f"data/external/{panel.panel_uuid}/{source_path.name}"
    with ZipFile(target) as archive:
        project_json = json.loads(archive.read("project.json"))
        panel_payload = project_json["panels"][0]
        assert panel_payload["data"]["raw_copy_path"] == expected_archive_path
        assert panel_payload["data"]["embedded_copy_path"] == expected_archive_path
        assert expected_archive_path in set(archive.namelist())

    source_path.unlink()

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, ExternalImagePanel))
    restored_source_path = Path(str(restored_panel.data["image_path"]))
    extract_root = restored.archive_extract_root

    assert restored_panel.data["raw_copy_path"] == expected_archive_path
    assert extract_root is not None
    assert extract_root.exists()
    assert restored_source_path.exists()
    assert not restored_panel.render_preview(40, 30, dpi=150).isNull()
    restored.dispose()
    restored.dispose()
    assert not extract_root.exists()
    assert not restored_source_path.exists()
    app.processEvents()


def test_sfig_load_restores_same_basename_external_assets_from_distinct_archive_paths(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    project = Project(title="External Collision Archive")
    source_dir_a = tmp_path / "source_a"
    source_dir_b = tmp_path / "source_b"
    source_dir_a.mkdir()
    source_dir_b.mkdir()
    source_a = _write_raster_image(source_dir_a / "shared.png", QColor(10, 120, 240))
    source_b = _write_raster_image(source_dir_b / "shared.png", QColor(220, 80, 20))
    panel_a = ExternalImagePanel(panel_id="Figure1G")
    panel_b = ExternalImagePanel(panel_id="Figure1H")
    panel_a.import_image(source_a)
    panel_b.import_image(source_b)
    project.add_panel(panel_a)
    project.add_panel(panel_b)
    target = tmp_path / "external_collision_roundtrip.sfig"

    project.save_sfig(target)

    archive_path_a = f"data/external/{panel_a.panel_uuid}/{source_a.name}"
    archive_path_b = f"data/external/{panel_b.panel_uuid}/{source_b.name}"
    with ZipFile(target) as archive:
        names = set(archive.namelist())
        assert archive_path_a in names
        assert archive_path_b in names

    source_a.unlink()
    source_b.unlink()

    restored = Project.load_sfig(target)
    restored_panels = {panel.panel_id: panel for panel in restored.panels if isinstance(panel, ExternalImagePanel)}
    restored_a = restored_panels["Figure1G"]
    restored_b = restored_panels["Figure1H"]
    preview_a = restored_a.render_preview(40, 30, dpi=150)
    preview_b = restored_b.render_preview(40, 30, dpi=150)

    assert restored_a.data["image_path"] != restored_b.data["image_path"]
    assert Path(str(restored_a.data["image_path"])).exists()
    assert Path(str(restored_b.data["image_path"])).exists()
    assert preview_a.pixelColor(preview_a.width() // 2, preview_a.height() // 2) != preview_b.pixelColor(
        preview_b.width() // 2, preview_b.height() // 2
    )
    app.processEvents()


def test_sfig_load_skips_duplicate_panel_uuid_and_keeps_first_panel_payload(tmp_path) -> None:
    duplicate_uuid = "duplicate-panel"
    first_panel = QPCRPanel(panel_id="Figure1A", panel_uuid=duplicate_uuid)
    second_panel = WesternBlotPanel(panel_id="Figure1B", panel_uuid=duplicate_uuid)
    project_payload = {
        "schema_version": "2.3.0",
        "software_version": "0.1.0",
        "project_uuid": "duplicate-load-project",
        "title": "Duplicate Load",
        "created_at": "2026-05-15T00:00:00+00:00",
        "modified_at": "2026-05-15T00:00:00+00:00",
        "panels": [first_panel.to_dict(), second_panel.to_dict()],
        "panel_registry": [
            {
                "panel_uuid": duplicate_uuid,
                "panel_id": first_panel.panel_id,
                "panel_type": first_panel.panel_type,
                "source_module": first_panel.source_module,
            },
            {
                "panel_uuid": duplicate_uuid,
                "panel_id": second_panel.panel_id,
                "panel_type": second_panel.panel_type,
                "source_module": second_panel.source_module,
            },
        ],
        "composer_figures": [],
        "active_figure_uid": None,
        "composer_layout": {},
        "audit_log": [],
    }
    target = tmp_path / "duplicate_load.sfig"
    _write_minimal_archive(target, project_payload)

    restored = Project.load_sfig(target)

    assert len(restored.panels) == 1
    assert restored.panels[0].panel_id == first_panel.panel_id
    assert restored.panel_registry().get(duplicate_uuid) is restored.panels[0]
    assert [entry.detail for entry in restored.audit_log].count(f"duplicate panel_uuid skipped: {duplicate_uuid}") == 1


def test_sfig_load_preserves_inline_full_composer_figure_payload(tmp_path) -> None:
    project = Project(title="Inline Composer Figure")
    panel = QPCRPanel(panel_id="Figure1A")
    project.add_panel(panel)
    layout = ComposerLayout.create("2x2")
    layout.place_panel(panel.panel_uuid)
    project.set_composer_layout(layout)
    active_figure = project.ensure_active_composer_figure()
    project_payload = project.to_dict()
    project_payload["composer_figures"] = [active_figure.to_dict()]
    project_payload["active_figure_uid"] = active_figure.figure_uid
    target = tmp_path / "inline_full_figure.sfig"
    _write_minimal_archive(target, project_payload)

    restored = Project.load_sfig(target)

    assert len(restored.composer_figures) == 1
    assert restored.ensure_active_composer_figure().figure_uid == active_figure.figure_uid
    assert restored.ensure_active_composer_figure().panel_refs[0].panel_uid == panel.panel_uuid


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_sfig_load_rejects_missing_or_corrupt_composer_member(tmp_path, mode) -> None:
    project = Project(title="Composer Integrity")
    project.add_panel(QPCRPanel(panel_id="Figure1A"))
    source = tmp_path / "composer_integrity.sfig"
    project.save_sfig(source)
    with ZipFile(source) as archive:
        project_payload = json.loads(archive.read("project.json"))
    composer_path = project_payload["composer_figures"][0]["file"]
    tampered = tmp_path / f"composer_integrity_{mode}.sfig"
    if mode == "missing":
        _rewrite_archive(source, tampered, omit={composer_path})
    else:
        _rewrite_archive(source, tampered, replacements={composer_path: b"{not valid json"})

    with pytest.raises(ValueError, match="composer"):
        Project.load_sfig(tampered)


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_sfig_load_rejects_missing_or_corrupt_raw_member(tmp_path, mode) -> None:
    project = Project(title="Raw Integrity")
    panel = IHCPanel(panel_id="Figure1D")
    source_image = _write_raster_image(tmp_path / "raw_integrity.png", QColor(90, 100, 110))
    panel.import_image(source_image)
    project.add_panel(panel)
    source = tmp_path / "raw_integrity.sfig"
    project.save_sfig(source)
    with ZipFile(source) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    raw_member = next(name for name in manifest["raw_hashes"] if name.startswith("data/ihc/"))
    tampered = tmp_path / f"raw_integrity_{mode}.sfig"
    if mode == "missing":
        _rewrite_archive(source, tampered, omit={raw_member})
    else:
        _rewrite_archive(source, tampered, replacements={raw_member: b"corrupt raw bytes"})

    with pytest.raises(ValueError, match="raw"):
        Project.load_sfig(tampered)


def test_sfig_load_maps_legacy_raw_key_without_data_prefix(tmp_path) -> None:
    raw_bytes = b"legacy raw member"
    panel = QPCRPanel(panel_id="Figure1A")
    panel.data.update(
        {
            "raw_source": "legacy.xlsx",
            "raw_hash": sha256_bytes(raw_bytes),
            "raw_copy_path": "data/qpcr/legacy.xlsx",
        }
    )
    project = Project(title="Legacy Raw Key")
    project.add_panel(panel)
    project_payload = project.to_dict()
    target = tmp_path / "legacy_raw_key.sfig"
    manifest = {
        "schema_version": project_payload["schema_version"],
        "software_version": project_payload["software_version"],
        "project_uuid": project_payload["project_uuid"],
        "project_json_hash": sha256_json(project_payload),
        "raw_hashes": {"legacy.xlsx": sha256_bytes(raw_bytes)},
    }
    with ZipFile(target, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("project.json", json.dumps(project_payload))
        archive.writestr("data/qpcr/legacy.xlsx", raw_bytes)

    restored = Project.load_sfig(target)

    restored_path = Path(str(restored.panels[0].raw_source_path))
    assert restored_path.exists()
    assert restored_path.read_bytes() == raw_bytes


def test_sfig_cached_uint16_fluorescence_hashes_exact_npy_and_round_trips(tmp_path) -> None:
    project = Project(title="Cached Uint16 Fluorescence")
    panel = FluorescencePanel(panel_id="Figure1C")
    image_uid = "cached-uint16"
    expected = np.arange(24, dtype=np.uint16).reshape(2, 3, 4)
    panel.data["fluorescence_images"] = [
        {
            "uid": image_uid,
            "source_path": "",
            "source_name": "cached.tif",
            "source_hash": "sha256:" + "0" * 64,
            "analysis_data_shape": list(expected.shape),
            "analysis_dtype": str(expected.dtype),
        }
    ]
    panel.data["active_image_uid"] = image_uid
    panel.cache_image_data(image_uid, expected)
    project.add_panel(panel)
    target = tmp_path / "cached_uint16.sfig"

    project.save_sfig(target)

    with ZipFile(target) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        data_path = next(name for name in archive.namelist() if name.endswith("/analysis.npy"))
        npy_bytes = archive.read(data_path)
    assert manifest["raw_hashes"][data_path] == sha256_bytes(npy_bytes)

    restored = Project.load_sfig(target)
    restored_panel = next(item for item in restored.panels if isinstance(item, FluorescencePanel))
    restored_data = restored_panel.image_data(image_uid)
    assert restored_data is not None
    assert restored_data.dtype == np.uint16
    assert np.array_equal(restored_data, expected)


def test_sfig_project_json_schema_and_asset_fields_remain_unchanged(tmp_path) -> None:
    project = _project_with_panels()
    target = tmp_path / "project_shape.sfig"
    project.save_sfig(target)

    with ZipFile(target) as archive:
        project_payload = json.loads(archive.read("project.json"))

    assert project_payload["schema_version"] == "2.3.0"
    assert set(project_payload) == {
        "schema_version",
        "software_version",
        "project_uuid",
        "title",
        "created_at",
        "modified_at",
        "panels",
        "panel_registry",
        "composer_figures",
        "active_figure_uid",
        "composer_layout",
        "audit_log",
        "browser_workspace_state",
        "session_ui_state",
    }
    assert project_payload["browser_workspace_state"] == project.browser_workspace_state == {}
    for panel_payload in project_payload["panels"]:
        for key in ("fluorescence_images", "ihc_images", "brightfield_images"):
            _assert_no_embedded_asset_fields(panel_payload.get("data", {}).get(key, []))

