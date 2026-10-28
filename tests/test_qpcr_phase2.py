from __future__ import annotations

import copy
import os
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex
from PyQt6.QtCore import QByteArray, QPointF
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QComboBox, QGraphicsPixmapItem, QLabel, QScrollArea
from PIL import Image

from core.analysis import analyze_qpcr_dataframe
from core.export import export_qpcr_panel
from core.models import Project, QPCRPanel
from core.utils.i18n import tr
from core.parsers import parse_qpcr_file
from core.plotting import build_qpcr_figure
from core.plotting import qpcr_plot
from core.plotting.style_presets import get_journal_preset, plot_style_from_preset
from core.style_preset_registry import StylePresetRecord, StylePresetRegistry
from core.style_preset_resolver import (
    clear_runtime_active_style_preset,
    last_runtime_style_preset_status,
    set_runtime_active_style_preset,
)
from ui.main_window import MainWindow
from ui.widgets.editable_text_item import EditableMovableTextItem
from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas


SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "qpcr_phase2_sample.csv"
SAMPLE_XLSX = Path(__file__).resolve().parents[1] / "sample_data" / "qpcr_phase2_sample.xlsx"


def test_qpcr_parser_detects_ab_header_and_columns() -> None:
    parsed = parse_qpcr_file(SAMPLE)

    assert parsed.instrument == "applied_biosystems"
    assert parsed.header_row == 2
    assert parsed.dataframe.shape[0] == 36
    assert {"well", "sample", "target", "ct", "ct_raw"}.issubset(parsed.dataframe.columns)


def test_qpcr_parser_scans_excel_sheets_for_header() -> None:
    parsed = parse_qpcr_file(SAMPLE_XLSX)

    assert parsed.instrument == "applied_biosystems"
    assert parsed.sheet_name == "Results"
    assert parsed.header_row == 2
    assert parsed.dataframe.shape[0] == 36


def test_qpcr_parser_prefers_results_sheet_over_earlier_decoy_tables(tmp_path) -> None:
    path = tmp_path / "instrument_export.xlsx"
    decoy = pd.DataFrame(
        [
            ["Well", "Sample Name", "Target Name", "CT"],
            ["A01", "SetupControl", "GAPDH", 10.0],
        ]
    )
    metadata = pd.DataFrame([["Run metadata", "ignored"], ["Quantification Cycle Method", "Ct"], [None, None]])
    results = pd.concat(
        [
            metadata,
            pd.DataFrame(
                [
                    ["Well", "Well Position", "Sample Name", "Target Name", "CT"],
                    [1, "A01", "Ctrl_bio1_tech1", "GAPDH", 20.0],
                    [2, "A02", "Ctrl_bio1_tech1", "SPNS1", 24.0],
                ]
            ),
        ],
        ignore_index=True,
    )
    with pd.ExcelWriter(path) as writer:
        decoy.to_excel(writer, sheet_name="Sample Setup", index=False, header=False)
        results.to_excel(writer, sheet_name="Results", index=False, header=False)

    parsed = parse_qpcr_file(path)

    assert parsed.sheet_name == "Results"
    assert parsed.header_row == 3
    assert parsed.dataframe["sample"].tolist() == ["Ctrl_bio1_tech1", "Ctrl_bio1_tech1"]


def test_qpcr_parser_ignores_blank_sample_or_target_rows(tmp_path) -> None:
    path = tmp_path / "blank_rows.xlsx"
    rows = [
        ["Well", "Sample Name", "Target Name", "CT"],
        ["A01", "siCtrl_bio1_tech1", "GAPDH", 20.1],
        ["A02", None, "GAPDH", 20.2],
        ["A03", "siCtrl_bio1_tech1", None, 25.1],
        ["A04", "siCtrl_bio1_tech1", "SPNS1", ""],
    ]
    pd.DataFrame(rows).to_excel(path, index=False, header=False)

    parsed = parse_qpcr_file(path)

    assert parsed.dataframe.shape[0] == 2
    assert parsed.dataframe["sample"].tolist() == ["siCtrl_bio1_tech1", "siCtrl_bio1_tech1"]
    assert pd.isna(parsed.dataframe.loc[1, "ct"])


def test_qpcr_delta_delta_ct_analysis_and_statistics() -> None:
    parsed = parse_qpcr_file(SAMPLE)
    result = analyze_qpcr_dataframe(parsed.dataframe, reference_gene="GAPDH", control_group="siCtrl")

    assert result.analysis["reference_gene"] == "GAPDH"
    assert result.analysis["control_group"] == "siCtrl"
    assert result.analysis["stat_performed_on"] == "delta_ct"
    assert result.analysis["plotted_as"] == "fold_change_2neg_ddct"
    assert result.analysis["replicate_structure"]["biological_replicates"] == 3
    assert result.delta_ct.shape[0] == 6
    assert result.plot_data[result.plot_data["group"] == "siSPNS1"]["fold_change"].mean() < 0.2
    assert result.statistics["targets"]["SPNS1"]["comparisons"][0]["label"] == "***"
    assert "statistics:" in result.summary


def test_qpcr_analysis_skips_empty_technical_replicates_and_reports_warning() -> None:
    dataframe = pd.DataFrame(
        [
            {"sample": "Ctrl_bio1_tech1", "target": "GAPDH", "ct": 20.0},
            {"sample": "Ctrl_bio1_tech2", "target": "GAPDH", "ct": 20.2},
            {"sample": "Ctrl_bio1_tech3", "target": "GAPDH", "ct": None},
            {"sample": "Ctrl_bio1_tech1", "target": "SPNS1", "ct": 24.0},
            {"sample": "Ctrl_bio1_tech2", "target": "SPNS1", "ct": 24.2},
            {"sample": "Ctrl_bio1_tech3", "target": "SPNS1", "ct": None},
            {"sample": "KD_bio1_tech1", "target": "GAPDH", "ct": 20.5},
            {"sample": "KD_bio1_tech2", "target": "GAPDH", "ct": None},
            {"sample": "KD_bio1_tech3", "target": "GAPDH", "ct": None},
            {"sample": "KD_bio1_tech1", "target": "SPNS1", "ct": 27.0},
            {"sample": "KD_bio1_tech2", "target": "SPNS1", "ct": None},
            {"sample": "KD_bio1_tech3", "target": "SPNS1", "ct": None},
        ]
    )

    result = analyze_qpcr_dataframe(dataframe, reference_gene="GAPDH", control_group="Ctrl")

    tech_counts = {
        (row["group"], row["target"]): int(row["technical_replicates"])
        for row in result.technical_summary.to_dict("records")
    }
    assert tech_counts[("Ctrl", "GAPDH")] == 2
    assert tech_counts[("Ctrl", "SPNS1")] == 2
    assert tech_counts[("KD", "GAPDH")] == 1
    assert tech_counts[("KD", "SPNS1")] == 1
    assert result.delta_ct.shape[0] == 2
    assert len(result.analysis["exclusions"]) == 6
    assert result.analysis["warnings"] == ["6 missing Ct technical replicate(s) were excluded before averaging."]
    assert "6 missing Ct technical replicate(s) excluded" in result.summary


def test_qpcr_analysis_keeps_valid_targets_when_control_has_no_delta_ct_for_another_target() -> None:
    dataframe = pd.DataFrame(
        [
            {"sample": "Ctrl_bio1_tech1", "target": "GAPDH", "ct": 20.0},
            {"sample": "Ctrl_bio1_tech1", "target": "TargetA", "ct": 24.0},
            {"sample": "Ctrl_bio1_tech1", "target": "TargetB", "ct": None},
            {"sample": "KD_bio1_tech1", "target": "GAPDH", "ct": 20.5},
            {"sample": "KD_bio1_tech1", "target": "TargetA", "ct": 27.0},
            {"sample": "KD_bio1_tech1", "target": "TargetB", "ct": 28.0},
        ]
    )

    result = analyze_qpcr_dataframe(dataframe, reference_gene="GAPDH", control_group="Ctrl")

    assert set(result.delta_ct["target"]) == {"TargetA"}
    control_target_a = result.technical_summary[
        (result.technical_summary["group"] == "Ctrl") & (result.technical_summary["target"] == "TargetA")
    ].iloc[0]
    assert int(control_target_a["technical_replicates"]) == 1
    assert result.delta_ct[result.delta_ct["group"] == "Ctrl"]["fold_change"].iloc[0] == 1.0
    assert result.analysis["warnings"] == [
        "1 missing Ct technical replicate(s) were excluded before averaging.",
        "Skipped 1 target(s) without a usable control-group ΔCt baseline: TargetB.",
    ]


def test_qpcr_panel_import_export_and_sfig_raw_copy(tmp_path) -> None:
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")

    assert panel.data["raw_source"] == SAMPLE.name
    assert panel.data["raw_copy_path"] == f"data/qpcr/{SAMPLE.name}"
    assert panel.data["processed_data"]["fold_change"]
    assert any(entry.action == "import_data" for entry in panel.audit_log)

    output = tmp_path / "Figure1A.svg"
    export_qpcr_panel(panel, output)
    assert output.exists()
    assert any(entry.action == "export" for entry in panel.audit_log)

    project = Project(title="qPCR Save Test")
    project.add_panel(panel)
    archive_path = tmp_path / "qpcr_save_test.sfig"
    project.save_sfig(archive_path)
    with ZipFile(archive_path) as archive:
        assert f"data/qpcr/{SAMPLE.name}" in archive.namelist()
        project_json = json.loads(archive.read("project.json"))
    assert project_json["panels"][0]["data"]["raw_hash"].startswith("sha256:")


def test_qpcr_svg_export_keeps_editable_text(tmp_path) -> None:
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")

    output = tmp_path / "Figure1A.svg"
    export_qpcr_panel(panel, output)

    svg = output.read_text(encoding="utf-8")
    assert "<text" in svg
    assert "Relative mRNA expression" in svg


def test_qpcr_panel_can_load_before_manual_control_analysis() -> None:
    panel = QPCRPanel(panel_id="Figure1A")
    options = panel.load_qpcr_file(SAMPLE)

    assert panel.data["raw_rows"]
    assert not panel.data.get("processed_data")
    assert "siCtrl" in options["groups"]
    assert "siSPNS1" in options["groups"]
    assert options["suggested_reference_gene"] == "GAPDH"

    panel.reanalyze(reference_gene="GAPDH", control_group="siCtrl")

    assert panel.analysis["control_group"] == "siCtrl"
    assert panel.data["processed_data"]["fold_change"]


def test_qpcr_plot_builds_matplotlib_figure() -> None:
    parsed = parse_qpcr_file(SAMPLE)
    result = analyze_qpcr_dataframe(parsed.dataframe, reference_gene="GAPDH", control_group="siCtrl")
    figure = build_qpcr_figure(result.plot_data, result.statistics)

    assert figure.axes
    assert figure.axes[0].get_ylabel() == "Relative mRNA expression"


def test_qpcr_plot_accepts_nature_style_override() -> None:
    parsed = parse_qpcr_file(SAMPLE)
    result = analyze_qpcr_dataframe(parsed.dataframe, reference_gene="GAPDH", control_group="siCtrl")
    figure = build_qpcr_figure(
        result.plot_data,
        result.statistics,
        style={"point_color": "#B64342", "bar_edge_color": "#0F4D92", "axis_line_width": 1.1},
    )
    ax = figure.axes[0]
    scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

    assert to_hex(ax.patches[0].get_edgecolor()).upper() == "#0F4D92"
    assert to_hex(scatter.get_facecolors()[0]).upper() == "#B64342"
    assert ax.spines["left"].get_linewidth() == 1.1


def test_qpcr_runtime_saved_preset_changes_visual_tokens_without_changing_plot_or_statistics(tmp_path) -> None:
    parsed = parse_qpcr_file(SAMPLE)
    result = analyze_qpcr_dataframe(parsed.dataframe, reference_gene="GAPDH", control_group="siCtrl")
    plot_snapshot = copy.deepcopy(result.plot_data.to_dict("records"))
    stats_snapshot = copy.deepcopy(result.statistics)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime qPCR",
            source_type="user_modified",
            plot_style={"point_color": "#AA3377", "bar_edge_color": "#2277AA", "axis_line_width": 1.8},
            extraction_profile={},
            source_metadata={
                "seed_preset_name": "Nature",
                "source_kind": "manual",
                "source_path": "",
                "source_page_index": 0,
                "source_page_count": 1,
                "source_size_px": {"width": 0, "height": 0},
                "source_crop_rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            },
            warnings=[],
        )
    )

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        figure = build_qpcr_figure(result.plot_data, result.statistics, preset_name="Nature")
        ax = figure.axes[0]
        scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

        assert to_hex(ax.patches[0].get_edgecolor()).upper() == "#2277AA"
        assert to_hex(scatter.get_facecolors()[0]).upper() == "#AA3377"
        assert ax.spines["left"].get_linewidth() == 1.8
        assert result.plot_data.to_dict("records") == plot_snapshot
        assert result.statistics == stats_snapshot
    finally:
        clear_runtime_active_style_preset()


def test_qpcr_invalid_runtime_point_color_fails_closed_and_render_uses_builtin(tmp_path) -> None:
    parsed = parse_qpcr_file(SAMPLE)
    result = analyze_qpcr_dataframe(parsed.dataframe, reference_gene="GAPDH", control_group="siCtrl")
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Invalid qPCR runtime",
            source_type="user_modified",
            plot_style={"point_color": "red", "bar_edge_color": "#2277AA"},
            extraction_profile={},
            source_metadata={
                "seed_preset_name": "Nature",
                "source_kind": "manual",
                "source_path": "",
                "source_page_index": 0,
                "source_page_count": 1,
                "source_size_px": {"width": 0, "height": 0},
                "source_crop_rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            },
            warnings=[],
        )
    )
    original_text = registry.storage_path.read_text(encoding="utf-8")
    builtin_style = plot_style_from_preset(get_journal_preset("Nature"))

    try:
        status = set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        figure = build_qpcr_figure(result.plot_data, result.statistics, preset_name="Nature")
        ax = figure.axes[0]
        scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

        assert status.ok is False
        assert "point_color must be a #RRGGBB color" in status.message
        assert last_runtime_style_preset_status().source == "builtin"
        assert "point_color must be a #RRGGBB color" in last_runtime_style_preset_status().message
        assert to_hex(scatter.get_facecolors()[0]).upper() == str(builtin_style["point_color"]).upper()
        assert registry.storage_path.read_text(encoding="utf-8") == original_text
    finally:
        clear_runtime_active_style_preset()


def test_qpcr_runtime_saved_preset_reaches_panel_layout_plot_and_direct_export_without_serializing(tmp_path) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime qPCR export",
            source_type="user_modified",
            plot_style={"point_color": "#AA3377", "bar_edge_color": "#2277AA"},
            extraction_profile={},
            source_metadata={
                "seed_preset_name": "Nature",
                "source_kind": "manual",
                "source_path": "",
                "source_page_index": 0,
                "source_page_count": 1,
                "source_size_px": {"width": 0, "height": 0},
                "source_crop_rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            },
            warnings=[],
        )
    )
    panel = QPCRPanel(panel_id="Figure1RuntimeExport")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    plot_snapshot = copy.deepcopy(panel.data["processed_data"]["fold_change"])
    stats_snapshot = copy.deepcopy(panel.analysis["statistics"])
    project = Project(title="qPCR runtime style boundary")
    project.add_panel(panel)

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        record = panel.layout_plot_records()[0]
        layout_svg = panel.render_layout_plot(record, dpi=600, format="svg").decode("utf-8")
        export_path = tmp_path / "runtime_qpcr.svg"
        export_qpcr_panel(panel, export_path)
        export_svg = export_path.read_text(encoding="utf-8")

        project_payload_text = str(project.to_dict())
        assert "#2277aa" in layout_svg.lower()
        assert "#aa3377" in layout_svg.lower()
        assert "#2277aa" in export_svg.lower()
        assert "#aa3377" in export_svg.lower()
        assert panel.data["processed_data"]["fold_change"] == plot_snapshot
        assert panel.analysis["statistics"] == stats_snapshot
        assert saved.preset_id not in project_payload_text
        assert str(registry.storage_path) not in project_payload_text
        assert "Runtime qPCR export" not in project_payload_text
    finally:
        clear_runtime_active_style_preset()


def test_qpcr_active_panel_preset_drives_preview_layout_and_export_without_touching_data(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def patched_preset(name: str = "Nature", path=None) -> dict:
        preset = copy.deepcopy(get_journal_preset(name, path))
        if str(name).casefold() != "cell":
            return preset
        preset.setdefault("palette", {})
        preset["palette"].update(
            {
                "blue_main": "#2E7D32",
                "neutral_black": "#5D4037",
                "green_3": "#66BB6A",
            }
        )
        preset.setdefault("semantic_roles", {})
        preset["semantic_roles"].update(
            {
                "treatment_primary": "blue_main",
                "bar_edge": "neutral_black",
                "point_edge": "neutral_black",
            }
        )
        return preset

    monkeypatch.setattr(qpcr_plot, "get_journal_preset", patched_preset)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    panel = window._active_qpcr_panel()
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    plot_snapshot = copy.deepcopy(panel.data["processed_data"]["fold_change"])
    stats_snapshot = copy.deepcopy(panel.analysis["statistics"])

    panel.style["preset"] = "Nature"
    window._refresh_qpcr_ui(panel)
    nature_figure = window.preview_canvas.figure_canvas.figure
    nature_ax = nature_figure.axes[0]
    nature_scatter = next(collection for collection in nature_ax.collections if isinstance(collection, PathCollection))

    panel.style["preset"] = "Cell"
    window._refresh_qpcr_ui(panel)
    cell_figure = window.preview_canvas.figure_canvas.figure
    cell_ax = cell_figure.axes[0]
    cell_scatter = next(collection for collection in cell_ax.collections if isinstance(collection, PathCollection))

    record = panel.layout_plot_records()[0]
    layout_svg = panel.render_layout_plot(record, dpi=600, format="svg").decode("utf-8")
    export_path = tmp_path / "qpcr_cell.svg"
    export_qpcr_panel(panel, export_path)
    export_svg = export_path.read_text(encoding="utf-8")

    assert to_hex(nature_ax.patches[0].get_edgecolor()).upper() == "#272727"
    assert to_hex(nature_scatter.get_facecolors()[0]).upper() == "#0F4D92"
    assert to_hex(cell_ax.patches[0].get_edgecolor()).upper() == "#5D4037"
    assert to_hex(cell_scatter.get_facecolors()[0]).upper() == "#2E7D32"
    assert "#5d4037" in layout_svg.lower()
    assert "#2e7d32" in layout_svg.lower()
    assert "#5d4037" in export_svg.lower()
    assert "#2e7d32" in export_svg.lower()
    assert panel.data["processed_data"]["fold_change"] == plot_snapshot
    assert panel.analysis["statistics"] == stats_snapshot

    window.close()
    app.processEvents()


def test_qpcr_toolbar_preset_change_repaints_live_preview_without_reanalysis(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def patched_preset(name: str = "Nature", path=None) -> dict:
        preset = copy.deepcopy(get_journal_preset(name, path))
        if str(name).casefold() != "cell":
            return preset
        preset.setdefault("palette", {})
        preset["palette"].update(
            {
                "blue_main": "#2E7D32",
                "neutral_black": "#5D4037",
                "green_3": "#66BB6A",
            }
        )
        preset.setdefault("semantic_roles", {})
        preset["semantic_roles"].update(
            {
                "treatment_primary": "blue_main",
                "bar_edge": "neutral_black",
                "point_edge": "neutral_black",
            }
        )
        return preset

    monkeypatch.setattr(qpcr_plot, "get_journal_preset", patched_preset)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    panel = window._active_qpcr_panel()
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    window._refresh_qpcr_ui(panel)
    plot_snapshot = copy.deepcopy(panel.data["processed_data"]["fold_change"])
    stats_snapshot = copy.deepcopy(panel.analysis["statistics"])

    preset_combo = window.findChild(QComboBox, "V9StylePresetCombo")
    assert preset_combo is not None

    nature_figure = window.preview_canvas.figure_canvas.figure
    nature_ax = nature_figure.axes[0]
    nature_scatter = next(collection for collection in nature_ax.collections if isinstance(collection, PathCollection))

    preset_combo.setCurrentText("Cell")
    app.processEvents()

    cell_figure = window.preview_canvas.figure_canvas.figure
    cell_ax = cell_figure.axes[0]
    cell_scatter = next(collection for collection in cell_ax.collections if isinstance(collection, PathCollection))

    assert panel.style["preset"] == "Cell"
    assert to_hex(nature_ax.patches[0].get_edgecolor()).upper() == "#272727"
    assert to_hex(nature_scatter.get_facecolors()[0]).upper() == "#0F4D92"
    assert to_hex(cell_ax.patches[0].get_edgecolor()).upper() == "#5D4037"
    assert to_hex(cell_scatter.get_facecolors()[0]).upper() == "#2E7D32"
    assert panel.data["processed_data"]["fold_change"] == plot_snapshot
    assert panel.analysis["statistics"] == stats_snapshot

    window.close()
    app.processEvents()


def test_qpcr_panel_creates_layout_plot_records_per_target() -> None:
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")

    records = panel.layout_plot_records()

    assert records
    assert {record["target"] for record in records} == {"SPNS1"}
    assert records[0]["plot_type"] == "qpcr_target"
    assert records[0]["enabled"] is True
    assert records[0]["width_px"] == 520
    assert records[0]["height_px"] == 360
    assert records[0]["plot_uuid"]
    assert panel.data["qpcr_layout"]["plots"][0]["plot_uuid"] == records[0]["plot_uuid"]


def test_qpcr_layout_svg_uses_requested_logical_plot_size() -> None:
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")

    record = panel.layout_plot_records()[0]
    svg = panel.render_layout_plot(record, dpi=600, format="svg")
    renderer = QSvgRenderer(QByteArray(svg))
    size = renderer.defaultSize()

    assert renderer.isValid()
    assert size.width() >= 480
    assert size.height() >= 320
    assert abs((size.width() / size.height()) - (record["width_px"] / record["height_px"])) < 0.05


def test_qpcr_target_plots_send_to_composer_as_separate_assets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    panel = window._active_qpcr_panel()
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    window._refresh_qpcr_ui(panel)

    window.show_qpcr_layout()
    window.qpcr_layout_workspace.add_text_button.click()
    window.qpcr_layout_workspace.to_composer_button.click()

    layout_assets = [
        item
        for item in window.project.panels
        if item.panel_type == "external_image" and item.data.get("source_module") == "qpcr_layout"
    ]
    assets = [
        item
        for item in window.project.panels
        if item.panel_type == "external_image" and item.data.get("source_module") == "qpcr_plot"
    ]
    assert len(layout_assets) == 1
    assert len(assets) == len(panel.layout_plot_records())
    assert layout_assets[0].data["image_path"] != assets[0].data["image_path"]
    assert assets[0].data["qpcr_target"] == "SPNS1"
    assert Path(str(assets[0].data["image_path"])).exists()
    assert assets[0].data["preferred_width_mm"] > 0
    shelf=window.composer_workspace.assets
    from PyQt6.QtCore import Qt
    assert any(shelf.item(i).data(Qt.ItemDataRole.UserRole)==assets[0].panel_uuid for i in range(shelf.count()))
    window._capture_clean_project_baseline()
    window.close()
    window.deleteLater()
    app.processEvents()
    from PyQt6.QtCore import QCoreApplication,QEvent
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def test_qpcr_module_hides_redundant_style_sidebar_and_uses_step_nav() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")

    assert window.style_sidebar.isHidden()
    assert window.data_panel.qpcr_step_buttons[0].isVisibleTo(window.v9_shell.workflow_row)
    labels = [button.text().replace("✓ ", "").replace("• ", "").replace("○ ", "") for button in window.data_panel.qpcr_step_buttons]
    assert labels == [tr(key) for key in ('import','analyze','layout','export')]

    window.close()
    app.processEvents()


def test_qpcr_module_switch_restores_processed_preview_figure() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    panel = window._active_qpcr_panel()
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    window._refresh_qpcr_ui(panel)

    assert window.preview_canvas.figure_canvas is not None

    window.set_module("wb")
    window.set_module("qpcr")
    app.processEvents()

    assert window.canvas_stack.currentWidget() is window.qpcr_preview_workspace
    assert window.preview_canvas.figure_canvas is not None
    assert window.data_panel.qpcr_context_stack.currentIndex() == 1

    window.close()
    app.processEvents()


def test_qpcr_empty_preview_uses_white_placeholder_not_dark_blocks() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")

    assert window.preview_canvas.placeholder.objectName() == "PaperPlaceholder"
    assert "#ffffff" in window.preview_canvas.placeholder.styleSheet().lower()

    window.close()
    app.processEvents()


def test_qpcr_layout_canvas_edits_plots_text_shapes_and_delete(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    canvas = QPCRLayoutCanvas()
    canvas.set_panel(panel)

    plot_item = next(item for item in canvas.scene.items() if item.data(0) == "qpcr_layout_plot")
    assert any(isinstance(child, QGraphicsPixmapItem) for child in plot_item.childItems())
    plot_item.setSelected(True)
    handles = list(plot_item.handles.values())
    assert len(handles) == 8
    plot_item.apply_geometry(40.0, 52.0, 420.0, 260.0)
    canvas.persist_layout_edits()

    plot = panel.data["qpcr_layout"]["plots"][0]
    assert plot["x"] == 40.0
    assert plot["y"] == 52.0
    assert plot["width_px"] == 420
    assert plot["height_px"] == 260

    text = canvas.add_text("qPCR note")
    shape = canvas.add_shape("ellipse", stroke_color="#C0392B", fill_color="#22D7E6")
    canvas.persist_layout_edits()
    assert panel.data["qpcr_layout"]["texts"][0]["text"] == "qPCR note"
    assert panel.data["qpcr_layout"]["shapes"][0]["shape_type"] == "ellipse"

    canvas.scene.clearSelection()
    for item in canvas.scene.items():
        if item.data(1) in {text.data(1), shape.data(1)}:
            item.setSelected(True)
    canvas.delete_selected()
    assert panel.data["qpcr_layout"].get("texts", []) == []
    assert panel.data["qpcr_layout"].get("shapes", []) == []
    canvas.close()
    canvas.deleteLater()
    app.processEvents()
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_qpcr_layout_plot_text_is_directly_movable_without_dialog(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    canvas = QPCRLayoutCanvas()
    canvas.set_panel(panel)

    plot_item = next(item for item in canvas.scene.items() if item.data(0) == "qpcr_layout_plot")
    text_item = plot_item._svg_text_items[0]
    plot_visual_item = next(child for child in plot_item.childItems() if isinstance(child, QGraphicsPixmapItem))
    start_scene = text_item.scenePos()
    plot_start = plot_item.pos()

    assert text_item.parentItem() is plot_visual_item
    assert b"<text" not in plot_item._display_svg_bytes

    plot_item.setPos(plot_start + QPointF(22.0, 14.0))

    assert text_item.scenePos() == start_scene + QPointF(22.0, 14.0)

    moved_index = int(text_item.data(1))
    text_item.setPos(text_item.pos().x() + 14.0, text_item.pos().y() + 8.0)
    canvas.persist_layout_edits()

    override = panel.data["qpcr_layout"]["plots"][0]["svg_text_overrides"][str(moved_index)]
    assert isinstance(override, dict)
    assert override["x"] > 0
    assert override["y"] > 0

    assert plot_item.apply_svg_text_override(moved_index, "Edited target")
    canvas.persist_layout_edits()

    override = panel.data["qpcr_layout"]["plots"][0]["svg_text_overrides"][str(moved_index)]
    assert isinstance(override, dict)
    assert override["text"] == "Edited target"
    assert override["x"] > 0
    assert override["y"] > 0

    exported = canvas.export_figure(tmp_path / "qpcr_layout_text.svg", dpi=300)
    svg_text = exported.read_text(encoding="utf-8")

    assert svg_text.count("Edited target") == 1
    canvas.close()
    canvas.deleteLater()
    app.processEvents()
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_qpcr_analyze_step_exposes_group_table_and_hides_layout_shortcuts() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    panel = window._active_qpcr_panel()
    options = panel.load_qpcr_file(SAMPLE)
    window._refresh_qpcr_import_ui(panel, options)

    assert window.data_panel.qpcr_group_table.rowCount() > 0
    assert window.data_panel.qpcr_layout_button.isHidden()
    assert window.data_panel.qpcr_to_composer_button.isHidden()

    row = 0
    sample = window.data_panel.qpcr_group_table.item(row, 0).text()
    window.data_panel.qpcr_group_table.item(row, 1).setText("ManualControl")
    overrides, included_samples = window.data_panel.qpcr_group_assignments()

    assert overrides[sample] == "ManualControl"
    assert sample in included_samples
    window.close()
    app.processEvents()


def test_qpcr_preview_analysis_controls_live_in_right_context_panel() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    window.show()
    app.processEvents()

    assert window.canvas_stack.currentWidget() is window.qpcr_preview_workspace
    assert window.qpcr_workflow_panel.parentWidget() is window.v9_shell.right_panel
    assert window.data_panel.isHidden()
    assert window.data_panel.qpcr_step_buttons[0].isVisibleTo(window.v9_shell.workflow_row)
    assert window.qpcr_workflow_panel.height() >= 500
    assert window.data_panel.qpcr_context_panel.isVisibleTo(window.qpcr_workflow_panel)
    assert window.data_panel.preview_table.isVisibleTo(window.data_panel.qpcr_context_panel)
    assert window.data_panel.preview_table.height() >= 260
    assert window.data_panel.preview_table.parentWidget() is not window.data_panel
    assert window.data_panel.qpcr_group_table.parentWidget() is not window.data_panel
    footer = window.workflow_ui.footers['qpcr']
    assert footer.primary.isVisibleTo(window.qpcr_workflow_panel)
    assert footer.primary.text() == window.data_panel.import_button.text()
    stray_texts = {
        widget.text()
        for widget in app.topLevelWidgets()
        if isinstance(widget, QLabel) and widget.isVisible() and widget.window() is widget
    }
    assert "Reference" not in stray_texts
    assert "Control" not in stray_texts

    window.data_panel.qpcr_step_buttons[1].click()
    assert window.data_panel.reference_combo.isVisibleTo(window.qpcr_workflow_panel)
    assert window.data_panel.control_combo.isVisibleTo(window.qpcr_workflow_panel)
    assert window.data_panel.qpcr_group_table.isVisibleTo(window.data_panel.qpcr_context_panel)
    stray_texts = {
        widget.text()
        for widget in app.topLevelWidgets()
        if isinstance(widget, QLabel) and widget.isVisible() and widget.window() is widget
    }
    assert "Reference" not in stray_texts
    assert "Control" not in stray_texts

    window.close()
    app.processEvents()


def test_qpcr_analyze_and_export_steps_keep_preview_canvas_visible() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    window.show()
    app.processEvents()

    window.data_panel.qpcr_step_buttons[1].click()
    app.processEvents()

    assert window.canvas_stack.currentWidget() is window.qpcr_preview_workspace
    assert window.data_panel.qpcr_context_panel.isVisibleTo(window.qpcr_workflow_panel)
    assert window.data_panel.reference_combo.isVisibleTo(window.qpcr_workflow_panel)
    assert window.qpcr_layout_workspace.tools_panel.isHidden()

    window.data_panel.qpcr_step_buttons[3].click()
    app.processEvents()

    assert window.canvas_stack.currentWidget() is window.qpcr_preview_workspace
    assert window.data_panel.qpcr_context_panel.isVisibleTo(window.qpcr_workflow_panel)
    assert window.data_panel.qpcr_to_composer_button.isVisibleTo(window.data_panel.qpcr_context_panel)
    assert window.qpcr_layout_workspace.tools_panel.isHidden()

    window.data_panel.qpcr_step_buttons[2].click()
    app.processEvents()

    assert window.canvas_stack.currentWidget() is window.qpcr_layout_workspace
    assert window.qpcr_canvas_tools_group.isVisibleTo(window.qpcr_workflow_panel)
    assert window.qpcr_layout_workspace.tools_panel.isVisibleTo(window.qpcr_workflow_panel)
    assert window.qpcr_workflow_group.isHidden() is True

    window.close()
    app.processEvents()


def test_qpcr_manual_group_assignments_drive_reanalysis() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    panel = window._active_qpcr_panel()
    options = panel.load_qpcr_file(SAMPLE)
    window._refresh_qpcr_import_ui(panel, options)

    for row in range(window.data_panel.qpcr_group_table.rowCount()):
        sample = window.data_panel.qpcr_group_table.item(row, 0).text()
        group_item = window.data_panel.qpcr_group_table.item(row, 1)
        if sample.startswith("siSPNS1_bio1"):
            group_item.setText("siSPNS1_low")
        elif sample.startswith("siSPNS1"):
            group_item.setText("siSPNS1_high")

    window.run_qpcr_analysis()

    groups = {row["group"] for row in panel.data["processed_data"]["fold_change"]}
    assert {"siCtrl", "siSPNS1_low", "siSPNS1_high"}.issubset(groups)
    assert panel.analysis["stat_test"] == "one_way_anova_tukey"
    window.close()
    app.processEvents()


def test_qpcr_layout_workspace_exposes_wb_style_editing_controls_and_persists_styles(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    panel = QPCRPanel(panel_id="Figure1A")
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    window.qpcr_layout_workspace.set_panel(panel)
    canvas = window.qpcr_layout_workspace.canvas

    plot_item = next(item for item in canvas.scene.items() if item.data(0) == "qpcr_layout_plot")
    plot_item.setSelected(True)
    window.qpcr_layout_workspace.plot_height_spin.setValue(340)
    window.qpcr_layout_workspace.bar_edge_combo.setCurrentText("#1F4E79")
    window.qpcr_layout_workspace.point_color_combo.setCurrentText("#C0392B")
    window.qpcr_layout_workspace.apply_plot_style()

    plot = panel.data["qpcr_layout"]["plots"][0]
    assert plot["height_px"] == 340
    assert plot["style"]["bar_edge_color"] == "#1F4E79"
    assert plot["style"]["point_color"] == "#C0392B"

    text = canvas.add_text("styled label")
    canvas.scene.clearSelection()
    text.setSelected(True)
    window.qpcr_layout_workspace.text_size_spin.setValue(12.0)
    window.qpcr_layout_workspace.text_bold_button.setChecked(True)
    window.qpcr_layout_workspace.text_rotation_spin.setValue(15.0)
    window.qpcr_layout_workspace.apply_text_style()

    saved_text = panel.data["qpcr_layout"]["texts"][0]
    assert saved_text["font_size"] == 12.0
    assert saved_text["bold"] is True
    assert saved_text["rotation"] == 15.0

    assert window.qpcr_layout_workspace.to_composer_button.text() == tr('add_qpcr_plots')
    window.close()
    app.processEvents()


def test_main_window_qpcr_layout_mode_exports_scene(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")
    panel = window._active_qpcr_panel()
    panel.import_qpcr_file(SAMPLE, reference_gene="GAPDH", control_group="siCtrl")
    window._refresh_qpcr_ui(panel)

    assert window.data_panel.qpcr_layout_button.text() == tr('layout')
    window.data_panel.qpcr_layout_button.click()
    assert window.canvas_stack.currentWidget() is window.qpcr_layout_workspace
    assert window.data_panel.isHidden() is True
    assert window.qpcr_workflow_panel.isHidden() is False
    assert window.data_panel.qpcr_step_buttons[0].isVisibleTo(window.v9_shell.workflow_row)
    assert window.qpcr_layout_workspace.layout().count() == 1
    assert window.qpcr_layout_workspace.layout().itemAt(0).widget() is window.qpcr_layout_canvas
    assert window.qpcr_canvas_tools_group.isHidden() is False
    assert window.qpcr_layout_workspace.tools_content.isVisibleTo(window.qpcr_workflow_panel)
    assert not window.qpcr_layout_workspace.tools_content.findChildren(QScrollArea)
    assert window.qpcr_workflow_group.isHidden() is True
    assert any(item.data(0) == "qpcr_layout_plot" for item in window.qpcr_layout_canvas.scene.items())

    assert window.qpcr_layout_workspace.add_text_button.text() == tr('add_text')
    window.qpcr_layout_workspace.add_text_button.click()
    window.qpcr_layout_workspace.add_shape_button.click()
    output = tmp_path / "qpcr_layout.png"
    window.qpcr_layout_canvas.export_figure(output, dpi=300)
    with Image.open(output) as image:
        assert image.convert("RGBA").getbbox() is not None

    window.close()
    app.processEvents()


def test_qpcr_layout_workspace_returns_to_preview_without_crowding_main_panel() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("qpcr")

    assert window.data_panel.isHidden() is True
    assert window.qpcr_workflow_panel.isHidden() is False
    assert window.data_panel.qpcr_layout_button.isHidden() is True
    assert window.data_panel.qpcr_step_buttons[2].isHidden() is False
    assert window.data_panel.qpcr_add_text_button.isHidden() is True
    assert window.data_panel.qpcr_same_size_button.isHidden() is True

    window.data_panel.qpcr_step_buttons[2].click()
    assert window.canvas_stack.currentWidget() is window.qpcr_layout_workspace
    assert window.data_panel.isHidden() is True
    assert window.data_panel.qpcr_step_buttons[0].isVisibleTo(window.v9_shell.workflow_row)
    assert window.data_panel.preview_table.isHidden() is True
    assert window.qpcr_canvas_tools_group.isHidden() is False
    assert window.qpcr_layout_workspace.tools_panel.isHidden() is False
    assert window.qpcr_workflow_group.isHidden() is True

    window.qpcr_layout_workspace.preview_button.click()
    assert window.canvas_stack.currentWidget() is window.qpcr_preview_workspace
    assert window.data_panel.isHidden() is True
    assert window.qpcr_workflow_panel.isHidden() is False
    assert window.qpcr_workflow_group.isHidden() is False
    assert window.data_panel.preview_table.isHidden() is False
    assert window.qpcr_layout_workspace.tools_panel.isHidden() is True
    window.close()
    app.processEvents()
