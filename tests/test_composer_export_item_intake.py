from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from core.layout_export_items import (
    EXPORT_ITEM_EXTERNAL_IMAGE,
    EXPORT_ITEM_FULL_LAYOUT,
    EXPORT_ITEM_INDIVIDUAL_PLOT,
    EXPORT_ITEM_OVERLAY,
    EXPORT_ITEM_STATISTICS_CHART,
    LayoutExportItem,
)
from core.models import BasePanel, ExternalImagePanel, Project
from core.models.composer import ComposerFigure, ComposerLayout
from ui.main_window import MainWindow
from ui.widgets.composer_canvas import ComposerCanvas


EXPORT_ITEM_SPECS = [
        {
            "case_id": "wb",
            "panel_id": "Figure1A_wb_layout",
            "source_module": "wb_export_item",
            "export_item_key": "wb_layout",
            "export_item_type": EXPORT_ITEM_FULL_LAYOUT,
            "source_record_id": "wb_layout",
            "source_record_namespace": "wb_layout",
            "source_dpi": 1200,
            "metadata": {"contains_wb_hierarchy": True},
        },
        {
            "case_id": "fluorescence",
            "panel_id": "Figure1B_fluorescence_plot",
            "source_module": "fluorescence_plot",
            "export_item_key": "fluorescence_plot:merge-a",
            "export_item_type": EXPORT_ITEM_INDIVIDUAL_PLOT,
            "source_record_id": "merge-a",
            "source_record_namespace": "fluorescence_layout_plot",
            "source_dpi": 600,
            "metadata": {"plot_uuid": "merge-a", "plot_type": "statistics"},
        },
        {
            "case_id": "qpcr",
            "panel_id": "Figure1C_qpcr_plot",
            "source_module": "qpcr_plot",
            "export_item_key": "qpcr_plot:spns1",
            "export_item_type": EXPORT_ITEM_INDIVIDUAL_PLOT,
            "source_record_id": "plot-spns1",
            "source_record_namespace": "qpcr_layout_plot",
            "source_dpi": 600,
            "metadata": {"plot_uuid": "plot-spns1", "qpcr_target": "SPNS1"},
        },
        {
            "case_id": "ihc",
            "panel_id": "Figure1D_ihc_overlay",
            "source_module": "ihc_overlay",
            "export_item_key": "measurement_overlay",
            "export_item_type": EXPORT_ITEM_OVERLAY,
            "source_record_id": "ihc-measurement-a",
            "source_record_namespace": "ihc_measurement_overlay",
            "source_dpi": 600,
            "metadata": {
                "measurement_uuid": "ihc-measurement-a",
                "plot_type": "measurement_overlay",
                "reliability_status": "WARN",
            },
        },
        {
            "case_id": "brightfield",
            "panel_id": "Figure1E_brightfield_overlay",
            "source_module": "brightfield_overlay",
            "export_item_key": "measurement_overlay",
            "export_item_type": EXPORT_ITEM_OVERLAY,
            "source_record_id": "brightfield-measurement-a",
            "source_record_namespace": "brightfield_measurement_overlay",
            "source_dpi": 600,
            "metadata": {
                "measurement_uuid": "brightfield-measurement-a",
                "plot_type": "measurement_overlay",
                "stain_type": "H&E",
                "segmentation_mode": "hsv",
            },
        },
]

EXPORT_ITEM_CASES = [pytest.param(spec, id=str(spec["case_id"])) for spec in EXPORT_ITEM_SPECS]

_QT_APP: QApplication | None = None


def _app() -> QApplication:
    global _QT_APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def _write_png(path: Path, color: int, width: int = 1800, height: int = 1200) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    assert image.save(str(path))
    return path


def _export_asset(tmp_path: Path, index: int, spec: dict[str, object]) -> ExternalImagePanel:
    source_path = _write_png(tmp_path / f"{spec['source_module']}_{index}.png", 0xFF202020 + index * 0x00101010)
    panel = ExternalImagePanel(panel_id=str(spec["panel_id"]))
    panel.import_image(source_path)
    item = LayoutExportItem(
        export_item_id=f"{panel.panel_uuid}:{spec['export_item_key']}",
        export_item_type=str(spec["export_item_type"]),
        source_module=str(spec["source_module"]),
        source_panel_uuid=f"source-panel-{index}",
        export_item_key=str(spec["export_item_key"]),
        source_dpi=int(spec["source_dpi"]),
        preferred_width_mm=24.0,
        preferred_height_mm=16.0,
        source_record_id=str(spec["source_record_id"]),
        source_record_namespace=str(spec["source_record_namespace"]),
        source_context_hash="sha256:" + f"{index:064d}"[-64:],
        image_path=str(source_path),
        source_format="PNG",
        source_width_px=1800,
        source_height_px=1200,
        metadata=spec["metadata"],
    )
    panel.data.update(item.to_external_image_metadata())
    return panel


def _plain_external_asset(tmp_path: Path) -> ExternalImagePanel:
    source_path = _write_png(tmp_path / "plain_external.png", 0xFFB0B0B0)
    panel = ExternalImagePanel(panel_id="Figure1F_external_image")
    panel.import_image(source_path)
    panel.data.update(
        {
            "source_module": "external",
            "export_item_key": "imported_image",
            "export_item_type": EXPORT_ITEM_EXTERNAL_IMAGE,
            "source_panel_uuid": panel.panel_uuid,
            "source_record_id": "plain_external.png",
            "source_record_namespace": "external_image",
            "source_dpi": 300,
            "preferred_width_mm": 24.0,
            "preferred_height_mm": 16.0,
            "source_width_px": 1800,
            "source_height_px": 1200,
        }
    )
    return panel


def _project_with_assets(tmp_path: Path) -> tuple[Project, ComposerCanvas, list[ExternalImagePanel]]:
    _app()
    project = Project(title="Composer Export Item Intake")
    project.set_composer_layout(ComposerLayout.create("3x3"))
    assets = [_export_asset(tmp_path, index, spec) for index, spec in enumerate(EXPORT_ITEM_SPECS)]
    assets.append(_plain_external_asset(tmp_path))
    for panel in assets:
        project.add_panel(panel)

    canvas = ComposerCanvas()
    canvas.set_project(project)
    for index, panel in enumerate(assets):
        canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(16 + index * 84, 16))
    return project, canvas, assets


@pytest.mark.parametrize("spec", EXPORT_ITEM_CASES)
def test_export_item_asset_metadata_uses_normalized_composer_shape(tmp_path: Path, spec: dict[str, object]) -> None:
    panel = _export_asset(tmp_path, 1, spec)

    assert panel.panel_type == "external_image"
    assert panel.data["source_module"] == spec["source_module"]
    assert panel.data["source_panel_uuid"] == "source-panel-1"
    assert panel.data["export_item_key"] == spec["export_item_key"]
    assert panel.data["export_item_type"] == spec["export_item_type"]
    assert panel.data["source_record_namespace"] == spec["source_record_namespace"]
    assert panel.data["source_context_hash"].startswith("sha256:")
    assert panel.data["source_format"] == "PNG"
    assert panel.data["source_width_px"] == 1800
    assert panel.data["source_height_px"] == 1200
    assert panel.data["preferred_width_mm"] == pytest.approx(24.0)
    assert panel.data["preferred_height_mm"] == pytest.approx(16.0)


def test_composer_intake_promotes_export_item_source_metadata_to_panel_refs(tmp_path: Path) -> None:
    project, _canvas, assets = _project_with_assets(tmp_path)
    figure = project.ensure_active_composer_figure()

    refs_by_panel = {ref.panel_uid: ref for ref in figure.panel_refs}
    assert set(refs_by_panel) == {panel.panel_uuid for panel in assets}
    for panel in assets:
        ref = refs_by_panel[panel.panel_uuid]
        assert ref.source_module == panel.data["source_module"]
        assert ref.width_mm == pytest.approx(panel.data["preferred_width_mm"])
        assert ref.height_mm == pytest.approx(panel.data["preferred_height_mm"])


def test_composer_layout_does_not_promote_arbitrary_panel_data_source_module() -> None:
    panel = BasePanel(
        panel_id="ScientificPanel",
        panel_type="scientific_panel",
        source_module="real_scientific_module",
        data={"source_module": "fake_export_module"},
    )
    layout = ComposerLayout.create("2x2")
    layout.place_panel(panel.panel_uuid)

    figure = ComposerFigure.from_layout(layout, {panel.panel_uuid: panel})

    assert figure.panel_refs[0].source_module == "real_scientific_module"


def test_composer_source_package_preserves_normalized_export_item_sources(tmp_path: Path) -> None:
    _project, canvas, assets = _project_with_assets(tmp_path)

    messages = canvas.preflight_export(dpi=300)
    assert [message for message in messages if message["severity"] == "error"] == []

    source_dir = canvas.export_source_data_package(tmp_path / "figure_v9leg.png")
    composer_payload = json.loads((source_dir / "figure_v9leg_composer.json").read_text(encoding="utf-8"))
    panel_payloads = json.loads((source_dir / "figure_v9leg_panels.json").read_text(encoding="utf-8"))
    preflight_payload = json.loads((source_dir / "figure_v9leg_preflight.json").read_text(encoding="utf-8"))
    source_metadata_payloads = json.loads((source_dir / "figure_v9leg_source_metadata.json").read_text(encoding="utf-8"))

    refs_by_panel = {item["panel_uid"]: item for item in composer_payload["panel_refs"]}
    panel_payloads_by_uuid = {item["panel_uuid"]: item for item in panel_payloads}
    source_metadata_by_uuid = {item["panel_uid"]: item for item in source_metadata_payloads}
    for panel in assets:
        data = panel_payloads_by_uuid[panel.panel_uuid]["data"]
        assert refs_by_panel[panel.panel_uuid]["source_module"] == data["source_module"]
        assert source_metadata_by_uuid[panel.panel_uuid]["source_module"] == data["source_module"]
        assert source_metadata_by_uuid[panel.panel_uuid]["source_metadata"]["export_item_key"] == data["export_item_key"]
        assert (source_dir / source_metadata_by_uuid[panel.panel_uuid]["copied_source_path"]).exists()
        assert data["export_item_type"] in {
            EXPORT_ITEM_FULL_LAYOUT,
            EXPORT_ITEM_INDIVIDUAL_PLOT,
            EXPORT_ITEM_OVERLAY,
            EXPORT_ITEM_STATISTICS_CHART,
            EXPORT_ITEM_EXTERNAL_IMAGE,
        }
        assert data["source_width_px"] == 1800
        assert data["source_height_px"] == 1200

    copied_files = {path.name for path in (source_dir / "external_source_files").iterdir() if path.is_file()}
    assert copied_files == {Path(str(panel.data["image_path"])).name for panel in assets}
    assert preflight_payload["dpi"] == 300
    assert [message for message in preflight_payload["messages"] if message["severity"] == "error"] == []


def test_composer_source_package_keeps_same_basename_export_sources_distinct(tmp_path: Path) -> None:
    _app()
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_path = _write_png(first_dir / "layout.png", 0xFF0000FF)
    second_path = _write_png(second_dir / "layout.png", 0xFFFF0000)
    project = Project(title="Composer Source Collision")
    project.set_composer_layout(ComposerLayout.create("2x2"))
    panels = []
    for index, (path, module) in enumerate(((first_path, "wb_export_item"), (second_path, "qpcr_plot"))):
        panel = ExternalImagePanel(panel_id=f"Figure1{chr(65 + index)}_{module}")
        panel.import_image(path)
        item = LayoutExportItem(
            export_item_id=f"{module}:{index}",
            export_item_type=EXPORT_ITEM_FULL_LAYOUT if index == 0 else EXPORT_ITEM_INDIVIDUAL_PLOT,
            source_module=module,
            source_panel_uuid=f"source-panel-{index}",
            export_item_key=f"{module}:layout",
            source_dpi=600,
            preferred_width_mm=24.0,
            preferred_height_mm=16.0,
            source_record_id=f"record-{index}",
            source_record_namespace=f"{module}_layout",
            source_context_hash="sha256:" + f"{index + 1:064d}"[-64:],
            image_path=str(path),
            source_format="PNG",
            source_width_px=1800,
            source_height_px=1200,
        )
        panel.data.update(item.to_external_image_metadata())
        project.add_panel(panel)
        panels.append(panel)

    canvas = ComposerCanvas()
    canvas.set_project(project)
    for index, panel in enumerate(panels):
        canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(16 + index * 80, 16))

    source_dir = canvas.export_source_data_package(tmp_path / "figure_collision.png")
    source_metadata_payloads = json.loads((source_dir / "figure_collision_source_metadata.json").read_text(encoding="utf-8"))
    copied_paths = [item["copied_source_path"] for item in source_metadata_payloads]

    assert len(copied_paths) == 2
    assert len(set(copied_paths)) == 2
    assert all((source_dir / copied_path).exists() for copied_path in copied_paths)
    assert all(copied_path.endswith("/layout.png") for copied_path in copied_paths)
    assert (source_dir / "external_source_files" / "layout.png").exists()


def test_composer_preflight_reports_missing_and_low_resolution_export_item_sources(tmp_path: Path) -> None:
    _app()
    high_res_path = _write_png(tmp_path / "exact_600dpi.png", 0xFF208020, width=600, height=600)
    high_res_panel = ExternalImagePanel(panel_id="Figure1H_exact_600dpi")
    high_res_panel.import_image(high_res_path)
    high_res_panel.data.update(
        {
            "source_module": "ihc_overlay",
            "export_item_key": "measurement_overlay",
            "export_item_type": EXPORT_ITEM_OVERLAY,
            "source_record_id": "ihc-exact-600dpi",
            "source_record_namespace": "ihc_measurement_overlay",
            "source_dpi": 600,
            "preferred_width_mm": 25.4,
            "preferred_height_mm": 25.4,
            "source_width_px": 600,
            "source_height_px": 600,
        }
    )
    missing_panel = _export_asset(tmp_path, 0, EXPORT_ITEM_SPECS[0])
    missing_panel.data["image_path"] = str(tmp_path / "missing_wb_layout.png")
    low_res_path = _write_png(tmp_path / "low_res_qpcr.png", 0xFF4040AA, width=80, height=60)
    low_res_panel = ExternalImagePanel(panel_id="Figure1G_low_res_qpcr")
    low_res_panel.import_image(low_res_path)
    low_res_panel.data.update(
        {
            "source_module": "qpcr_plot",
            "export_item_key": "qpcr_plot:low",
            "export_item_type": EXPORT_ITEM_INDIVIDUAL_PLOT,
            "source_panel_uuid": "source-panel-low",
            "source_record_id": "plot-low",
            "source_record_namespace": "qpcr_layout_plot",
            "source_dpi": 600,
            "preferred_width_mm": 80.0,
            "preferred_height_mm": 60.0,
            "source_width_px": 80,
            "source_height_px": 60,
        }
    )
    project = Project(title="Composer Preflight")
    project.set_composer_layout(ComposerLayout.create("2x2"))
    project.add_panel(high_res_panel)
    project.add_panel(missing_panel)
    project.add_panel(low_res_panel)
    canvas = ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(high_res_panel.panel_uuid, scene_pos=QPointF(12, 12))
    canvas.place_panel(missing_panel.panel_uuid, scene_pos=QPointF(12, 12))
    canvas.place_panel(low_res_panel.panel_uuid, scene_pos=QPointF(120, 12))

    messages = canvas.preflight_export(dpi=600)

    assert any(message["severity"] == "error" and "Missing external source" in message["message"] for message in messages)
    resolution_warnings = [message["message"] for message in messages if message["severity"] == "warning" and "effective resolution" in message["message"]]
    assert any("Figure1G_low_res_qpcr" in message for message in resolution_warnings)
    assert all("Figure1H_exact_600dpi" not in message for message in resolution_warnings)
    assert any(message["severity"] == "warning" and "source panel is no longer in the project" in message["message"] for message in messages)
    assert project.ensure_active_composer_figure().export_settings["last_preflight"]["dpi"] == 600


def test_composer_preflight_does_not_report_stale_when_source_context_hash_matches(tmp_path: Path) -> None:
    _app()
    from ui.layout_adapters.wb_layout_adapter import layout_context_hash

    source_panel = BasePanel(
        panel_id="Current WB",
        panel_type="western_blot",
        source_module="wb",
        data={"layout": {"lanes": ["A", "B"]}, "wb_layout": {"manual": {"x": 1}}, "annotations": {"labels": ["A"]}},
    )
    context_hash = layout_context_hash(source_panel)
    generated_panel = _export_asset(tmp_path, 0, EXPORT_ITEM_SPECS[0])
    generated_panel.data["source_panel_uuid"] = source_panel.panel_uuid
    generated_panel.data["source_context_hash"] = context_hash
    project = Project(title="Composer Fresh Asset")
    project.set_composer_layout(ComposerLayout.create("2x2"))
    project.add_panel(source_panel)
    project.add_panel(generated_panel)
    source_panel.modified_at = "2999-01-01T00:00:00+00:00"
    canvas = ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(generated_panel.panel_uuid, scene_pos=QPointF(12, 12))

    messages = canvas.preflight_export(dpi=300)

    assert not any(message["severity"] == "warning" and "generated asset may be stale" in message["message"] for message in messages)


def test_composer_preflight_reports_stale_generated_export_item(tmp_path: Path) -> None:
    _app()
    source_path = _write_png(tmp_path / "source_panel.png", 0xFF808080)
    source_panel = ExternalImagePanel(panel_id="Source WB")
    source_panel.import_image(source_path)

    generated_panel = _export_asset(tmp_path, 0, EXPORT_ITEM_SPECS[0])
    generated_panel.data["source_panel_uuid"] = source_panel.panel_uuid
    project = Project(title="Composer Stale Asset")
    project.set_composer_layout(ComposerLayout.create("2x2"))
    project.add_panel(source_panel)
    project.add_panel(generated_panel)
    source_panel.modified_at = "2999-01-01T00:00:00+00:00"
    canvas = ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(generated_panel.panel_uuid, scene_pos=QPointF(12, 12))

    messages = canvas.preflight_export(dpi=300)

    assert any(message["severity"] == "warning" and "generated asset may be stale" in message["message"] for message in messages)


def test_composer_refresh_keeps_generated_external_asset_without_thumbnail(tmp_path: Path) -> None:
    app = _app()
    project = Project(title="Composer Refresh Missing Asset")
    project.set_composer_layout(ComposerLayout.create("2x2"))
    panel = ExternalImagePanel(panel_id="MissingGeneratedLayout")
    panel.data.update(
        {
            "source_module": "wb_export_item",
            "export_item_key": "wb_layout",
            "export_item_type": EXPORT_ITEM_FULL_LAYOUT,
            "source_panel_uuid": "source-panel-missing",
            "source_dpi": 600,
            "preferred_width_mm": 24.0,
            "preferred_height_mm": 16.0,
            "image_path": str(tmp_path / "missing_generated_layout.png"),
        }
    )
    project.add_panel(panel)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")
    labels = [window.composer_workspace.assets.item(index).text() for index in range(window.composer_workspace.assets.count())]

    assert "missing_generated_layout.png" in labels
    window.close()
    app.processEvents()
