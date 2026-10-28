from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest
import tifffile
from matplotlib.collections import PathCollection
from matplotlib import rcParams, rc_context
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from PyQt6.QtWidgets import QApplication, QComboBox, QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsTextItem, QLabel, QVBoxLayout, QWidget

from core.models import BrightfieldPanel, IHCPanel, Project
from core.plotting import qpcr_plot
from core import plotting_densitometry
from core.plotting.qpcr_plot import build_qpcr_figure
from core.plotting.statistics_plot import build_group_statistics_figure, resolve_statistics_plot_style
from core.plotting.style_presets import apply_matplotlib_preset, get_journal_preset, image_plate_style_from_preset, plot_style_from_preset, resolve_semantic_color
from core.plotting_densitometry import build_densitometry_figure
from core.models import FluorescencePanel
from core.style_preset_registry import StylePresetRecord, StylePresetRegistry
from core.style_preset_resolver import clear_runtime_active_style_preset, set_runtime_active_style_preset
from ui.main_window import MainWindow
from ui.panels.fluorescence_workspace import FluorescenceWorkspace
from ui.widgets.fluoro_canvas import FluorescenceCanvas


def test_nature_preset_loader_reads_structured_schema() -> None:
    preset = get_journal_preset("Nature")

    assert preset["id"] == "nature"
    assert preset["palette"]["blue_main"] == "#0F4D92"
    assert preset["export"]["primary_format"] == "svg"
    assert preset["typography"]["panel_label_case"] == "lowercase"
    assert preset["bar_scatter_sem"]["bar_width_fraction"] == 0.58
    assert preset["image_plate"]["channel_cyan"] == "#22D7E6"


def test_nature_preset_applies_editable_svg_and_pdf_fonts() -> None:
    preset = get_journal_preset("Nature")

    apply_matplotlib_preset(preset)

    assert rcParams["svg.fonttype"] == "none"
    assert rcParams["pdf.fonttype"] == 42
    assert rcParams["ps.fonttype"] == 42
    assert "Arial" in rcParams["font.sans-serif"]
    assert rcParams["axes.spines.top"] is False
    assert rcParams["axes.spines.right"] is False


def test_plot_style_from_preset_flattens_nature_roles() -> None:
    preset = get_journal_preset("Nature")
    style = plot_style_from_preset(preset)

    assert style["font_family"] == "Arial"
    assert style["bar_edge_color"] == "#272727"
    assert style["point_color"] == "#0F4D92"
    assert style["point_edge_color"] == "#272727"
    assert style["axis_line_width"] == 0.6
    assert style["bracket_line_width"] == 0.7
    assert resolve_semantic_color(preset, "treatment_primary", "#000000") == "#0F4D92"


def test_image_plate_style_from_preset_flattens_fluorescence_roles() -> None:
    preset = get_journal_preset("Nature")
    style = image_plate_style_from_preset(preset)

    assert style["background_color"] == "#000000"
    assert style["channel_colors"][:3] == ["#22D7E6", "#FF2AD4", "#FFFFFF"]
    assert style["scale_bar_color"] == "#FFFFFF"


def _rcparams_snapshot() -> dict[str, object]:
    return {
        "font.family": list(rcParams["font.family"]),
        "font.sans-serif": list(rcParams["font.sans-serif"]),
        "font.size": float(rcParams["font.size"]),
        "svg.fonttype": rcParams["svg.fonttype"],
        "pdf.fonttype": int(rcParams["pdf.fonttype"]),
        "ps.fonttype": int(rcParams["ps.fonttype"]),
        "axes.spines.top": bool(rcParams["axes.spines.top"]),
        "axes.spines.right": bool(rcParams["axes.spines.right"]),
        "axes.linewidth": float(rcParams["axes.linewidth"]),
        "xtick.major.width": float(rcParams["xtick.major.width"]),
        "ytick.major.width": float(rcParams["ytick.major.width"]),
        "xtick.major.size": float(rcParams["xtick.major.size"]),
        "ytick.major.size": float(rcParams["ytick.major.size"]),
    }


def _patched_render_preset(name: str = "Nature", path=None) -> dict:
    preset = copy.deepcopy(get_journal_preset(name, path))
    normalized = str(name).casefold()
    if normalized == "cell":
        preset.setdefault("typography", {})
        preset["typography"].update(
            {
                "font_family": "Courier New",
                "fallbacks": [],
                "base_font_size_pt": 11,
                "axis_label_size_pt": 12,
                "tick_label_size_pt": 10,
            }
        )
        preset.setdefault("axes", {})
        preset["axes"].update(
            {
                "show_top_spine": True,
                "show_right_spine": True,
                "axis_line_width_pt": 2.4,
                "tick_width_pt": 1.7,
                "tick_length_pt": 8.0,
            }
        )
        return preset
    if normalized == "science":
        preset.setdefault("typography", {})
        preset["typography"].update(
            {
                "font_family": "Times New Roman",
                "fallbacks": [],
                "base_font_size_pt": 9,
                "axis_label_size_pt": 10,
                "tick_label_size_pt": 8,
            }
        )
        preset.setdefault("axes", {})
        preset["axes"].update(
            {
                "show_top_spine": True,
                "show_right_spine": False,
                "axis_line_width_pt": 1.4,
                "tick_width_pt": 1.2,
                "tick_length_pt": 5.5,
            }
        )
    return preset


def _qpcr_inputs() -> tuple[pd.DataFrame, dict]:
    plot_data = pd.DataFrame(
        [
            {"target": "SPNS1", "group": "Control", "fold_change": 1.0, "mean_fold_change": 1.0, "error": 0.1},
            {"target": "SPNS1", "group": "Control", "fold_change": 1.1, "mean_fold_change": 1.0, "error": 0.1},
            {"target": "SPNS1", "group": "Drug", "fold_change": 1.6, "mean_fold_change": 1.5, "error": 0.2},
            {"target": "SPNS1", "group": "Drug", "fold_change": 1.4, "mean_fold_change": 1.5, "error": 0.2},
        ]
    )
    statistics = {
        "targets": {
            "SPNS1": {
                "comparisons": [
                    {"group1": "Control", "group2": "Drug", "label": "*"},
                ]
            }
        }
    }
    return plot_data, statistics


def _densitometry_inputs() -> dict:
    return {
        "loading_control_strip_id": "",
        "measurements": [
            {
                "strip_id": "strip_1",
                "strip_label": "SPNS1",
                "lanes": [
                    {"lane_label": "Control", "fold": 1.0, "qc_flags": []},
                    {"lane_label": "Drug", "fold": 1.4, "qc_flags": []},
                ],
            }
        ],
    }


def _statistics_inputs() -> dict:
    return {
        "plot_data": [
            {"group": "Control", "value": 1.0},
            {"group": "Drug", "value": 1.4},
        ],
        "comparisons": [
            {"group1": "Control", "group2": "Drug", "label": "ns"},
        ],
    }


def _write_runtime_fluoro_tiff(path: Path) -> Path:
    data = np.zeros((2, 12, 14), dtype=np.uint16)
    data[0, 2:6, 3:8] = 100
    data[1, 2:6, 3:8] = 250
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _runtime_style_record(display_name: str, plot_style: dict) -> StylePresetRecord:
    return StylePresetRecord(
        display_name=display_name,
        source_type="user_modified",
        plot_style=plot_style,
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


def test_interleaved_panel_presets_do_not_leak_rcparams(monkeypatch) -> None:
    apply_matplotlib_preset(get_journal_preset("Nature"))
    baseline = _rcparams_snapshot()
    qpcr_data, qpcr_stats = _qpcr_inputs()

    monkeypatch.setattr(qpcr_plot, "get_journal_preset", _patched_render_preset)
    monkeypatch.setattr(plotting_densitometry, "get_journal_preset", _patched_render_preset)

    qpcr_cell = build_qpcr_figure(qpcr_data, qpcr_stats, preset_name="Cell")
    qpcr_cell_ax = qpcr_cell.axes[0]
    assert qpcr_cell_ax.spines["top"].get_visible() is True
    assert qpcr_cell_ax.spines["left"].get_linewidth() == pytest.approx(2.4)
    assert _rcparams_snapshot() == baseline

    qpcr_nature = build_qpcr_figure(qpcr_data, qpcr_stats, preset_name="Nature")
    qpcr_nature_ax = qpcr_nature.axes[0]
    assert qpcr_nature_ax.spines["top"].get_visible() is False
    assert qpcr_nature_ax.spines["left"].get_linewidth() == pytest.approx(0.6)
    assert _rcparams_snapshot() == baseline

    wb_science = build_densitometry_figure(_densitometry_inputs(), preset_name="Science")
    wb_science_ax = wb_science.axes[0]
    assert wb_science_ax.spines["top"].get_visible() is True
    assert wb_science_ax.spines["left"].get_linewidth() == pytest.approx(1.4)
    assert _rcparams_snapshot() == baseline

    wb_nature = build_densitometry_figure(_densitometry_inputs(), preset_name="Nature")
    wb_nature_ax = wb_nature.axes[0]
    assert wb_nature_ax.spines["top"].get_visible() is False
    assert wb_nature_ax.spines["left"].get_linewidth() == pytest.approx(0.6)
    assert _rcparams_snapshot() == baseline


@pytest.mark.parametrize("module_name", ["core.plotting.qpcr_plot", "core.plotting.statistics_plot", "core.plotting_densitometry"])
def test_plotting_modules_do_not_mutate_global_rcparams_at_import(module_name: str) -> None:
    probe = f"""
import json
from matplotlib import rcParams
rcParams['font.size'] = 13.0
rcParams['axes.linewidth'] = 3.25
before = {{'font.size': float(rcParams['font.size']), 'axes.linewidth': float(rcParams['axes.linewidth'])}}
import {module_name}
after = {{'font.size': float(rcParams['font.size']), 'axes.linewidth': float(rcParams['axes.linewidth'])}}
print(json.dumps({{'before': before, 'after': after}}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["after"] == payload["before"]


def test_direct_statistics_svg_save_preserves_editable_text_without_global_rcparams_leak() -> None:
    with rc_context({"svg.fonttype": "path", "pdf.fonttype": 3, "ps.fonttype": 3}):
        baseline = _rcparams_snapshot()
        figure = build_group_statistics_figure(
            _statistics_inputs(),
            style=resolve_statistics_plot_style("Nature"),
            y_label="Relative expression (fold change)",
        )
        buffer = io.StringIO()

        figure.savefig(buffer, format="svg")

        svg = buffer.getvalue()
        assert "<text" in svg
        assert _rcparams_snapshot() == baseline


def test_qpcr_cell_render_does_not_leak_into_nature_statistics(monkeypatch) -> None:
    apply_matplotlib_preset(get_journal_preset("Nature"))
    baseline = _rcparams_snapshot()
    qpcr_data, qpcr_stats = _qpcr_inputs()

    monkeypatch.setattr(qpcr_plot, "get_journal_preset", _patched_render_preset)

    qpcr_cell = build_qpcr_figure(qpcr_data, qpcr_stats, preset_name="Cell")
    qpcr_cell_ax = qpcr_cell.axes[0]
    assert qpcr_cell_ax.spines["left"].get_linewidth() == pytest.approx(2.4)
    assert _rcparams_snapshot() == baseline

    nature_stats = build_group_statistics_figure(
        _statistics_inputs(),
        style=resolve_statistics_plot_style("Nature"),
        y_label="Relative expression (fold change)",
    )
    nature_stats_ax = nature_stats.axes[0]

    assert nature_stats_ax.spines["top"].get_visible() is False
    assert nature_stats_ax.spines["left"].get_linewidth() == pytest.approx(0.6)
    assert list(rcParams["font.sans-serif"]) == baseline["font.sans-serif"]
    assert _rcparams_snapshot() == baseline


def test_statistics_plot_style_priority_inserts_runtime_saved_preset_under_saved_and_local(tmp_path) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime stats",
            source_type="user_modified",
            plot_style={"point_color": "#102030", "bar_edge_color": "#405060", "axis_line_width": 1.4},
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
        style = resolve_statistics_plot_style(
            "Nature",
            saved_style={"point_color": "#111111", "axis_line_width": 1.8},
            local_override={"axis_line_width": 2.6},
        )

        assert style["bar_edge_color"] == "#405060"
        assert style["point_color"] == "#111111"
        assert style["axis_line_width"] == 2.6
    finally:
        clear_runtime_active_style_preset()


def test_fluorescence_stats_style_uses_runtime_saved_preset_without_changing_stats_payload(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    panel = FluorescencePanel(panel_id="Figure1FluoroRuntimeStyle")
    statistics = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.5}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
        "source": "roi_intensity",
        "metric": "mean",
    }
    stats_snapshot = copy.deepcopy(statistics)
    panel.analysis["fluoro_statistics"] = statistics
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime Fluoro",
            source_type="user_modified",
            plot_style={"point_color": "#663399", "bar_edge_color": "#339966", "axis_line_width": 1.5},
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
        figure = build_group_statistics_figure(
            statistics,
            style=workspace._fluoro_stats_plot_style(),
            y_label=workspace._fluoro_stats_axis_label(statistics),
        )
        ax = figure.axes[0]
        scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

        assert to_hex(ax.patches[0].get_edgecolor()).upper() == "#339966"
        assert to_hex(scatter.get_facecolors()[0]).upper() == "#663399"
        assert ax.spines["left"].get_linewidth() == 1.5
        assert statistics == stats_snapshot
    finally:
        clear_runtime_active_style_preset()
        workspace.close()
        app.processEvents()


def test_fluorescence_import_defaults_ignore_runtime_saved_preset_and_do_not_serialize_it(tmp_path) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime Fluoro import boundary",
            source_type="user_modified",
            plot_style={
                "point_color": "#123456",
                "bar_edge_color": "#654321",
                "series_colors": ["#123456", "#654321"],
            },
            extraction_profile={"should_not_serialize": True},
            source_metadata={
                "seed_preset_name": "Nature",
                "source_kind": "manual",
                "source_path": "reference.png",
                "source_page_index": 0,
                "source_page_count": 1,
                "source_size_px": {"width": 0, "height": 0},
                "source_crop_rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            },
            warnings=[],
        )
    )
    panel = FluorescencePanel(panel_id="Figure1FluoroImportBoundary")
    image_path = _write_runtime_fluoro_tiff(tmp_path / "runtime_import_boundary.tif")
    archive_path = tmp_path / "runtime_import_boundary.sfig"
    project = Project(title="Fluoro runtime boundary")
    project.add_panel(panel)

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        image_record = panel.import_image(image_path)
        project.save_sfig(archive_path)

        channel_colors = [str(channel.get("pseudocolor", "")) for channel in image_record["channels"]]
        scale_bar_color = str(image_record["scale_bar"]["color"])
        project_payload_text = str(project.to_dict())
        assert "#123456" not in channel_colors
        assert "#654321" not in channel_colors
        assert scale_bar_color not in {"#123456", "#654321"}
        assert saved.preset_id not in project_payload_text
        assert str(registry.storage_path) not in project_payload_text
        assert "should_not_serialize" not in project_payload_text

        with ZipFile(archive_path, "r") as archive:
            project_text = archive.read("project.json").decode("utf-8")
            assert saved.preset_id not in project_text
            assert str(registry.storage_path) not in project_text
            assert "should_not_serialize" not in project_text
    finally:
        clear_runtime_active_style_preset()


def test_fluorescence_canvas_layout_plot_renderer_uses_runtime_saved_preset_under_saved_plot_style(
    tmp_path,
    monkeypatch,
) -> None:
    captured_styles: list[dict] = []

    def fake_build_group_statistics_figure(
        statistics_result: dict,
        style: dict | None = None,
        y_label: str = "Relative expression (fold change)",
        **_kwargs,
    ) -> Figure:
        captured_styles.append(dict(style or {}))
        return Figure(figsize=(2, 1.5), dpi=100)

    monkeypatch.setattr("ui.widgets.fluoro_canvas.build_group_statistics_figure", fake_build_group_statistics_figure)
    app = QApplication.instance() or QApplication([])
    panel = FluorescencePanel(panel_id="Figure1FluoroCanvasRuntimeStyle")
    canvas = FluorescenceCanvas()
    canvas.set_panel(panel)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime Fluoro canvas",
            source_type="user_modified",
            plot_style={"point_color": "#663399", "bar_edge_color": "#339966", "axis_line_width": 1.5},
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
    record = {
        "plot_type": "statistics",
        "plot_uuid": "runtime-fluoro-stat",
        "style": {"point_color": "#111111"},
        "statistics_result": {
            "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.5}],
            "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
            "source": "roi_intensity",
            "metric": "mean",
        },
    }

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        image = canvas._render_layout_plot_image(record, 320, 240)

        assert not image.isNull()
        assert captured_styles
        assert captured_styles[-1]["bar_edge_color"] == "#339966"
        assert captured_styles[-1]["point_color"] == "#111111"
        assert captured_styles[-1]["axis_line_width"] == 1.5
        assert "runtime_style_preset" not in str(panel.analysis)
    finally:
        clear_runtime_active_style_preset()
        canvas.close()
        app.processEvents()


def test_fluorescence_canvas_runtime_cache_key_changes_when_active_saved_preset_is_overwritten(
    tmp_path,
    monkeypatch,
) -> None:
    captured_styles: list[dict] = []

    def fake_build_group_statistics_figure(
        statistics_result: dict,
        style: dict | None = None,
        y_label: str = "Relative expression (fold change)",
        **_kwargs,
    ) -> Figure:
        captured_styles.append(dict(style or {}))
        return Figure(figsize=(2, 1.5), dpi=100)

    monkeypatch.setattr("ui.widgets.fluoro_canvas.build_group_statistics_figure", fake_build_group_statistics_figure)
    app = QApplication.instance() or QApplication([])
    panel = FluorescencePanel(panel_id="Figure1FluoroCanvasRuntimeCache")
    canvas = FluorescenceCanvas()
    canvas.set_panel(panel)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    record = StylePresetRecord(
        display_name="Runtime Fluoro cache",
        source_type="user_modified",
        plot_style={"bar_edge_color": "#339966"},
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
    saved = registry.save_preset(record)
    render_record = {
        "plot_type": "statistics",
        "plot_uuid": "runtime-fluoro-cache",
        "style": {},
        "statistics_result": {
            "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.5}],
            "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
        },
    }

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        first = canvas._render_layout_plot_image(render_record, 320, 240)
        updated = registry.get_preset(saved.preset_id)
        assert updated is not None
        updated.plot_style["bar_edge_color"] = "#AA4400"
        registry.save_preset(updated)
        second = canvas._render_layout_plot_image(render_record, 320, 240)

        assert not first.isNull()
        assert not second.isNull()
        assert len(captured_styles) == 2
        assert captured_styles[0]["bar_edge_color"] == "#339966"
        assert captured_styles[1]["bar_edge_color"] == "#AA4400"
    finally:
        clear_runtime_active_style_preset()
        canvas.close()
        app.processEvents()


def test_style_lab_runtime_saved_preset_refreshes_all_science_render_dependents_when_style_lab_active(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        _runtime_style_record(
            "Runtime refresh proof",
            {"point_color": "#123456", "bar_edge_color": "#654321"},
        )
    )
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    calls: list[str] = []

    qpcr_panel = window._active_qpcr_panel()
    wb_panel = window._active_wb_panel()
    fluoro_panel = window._active_fluoro_panel()
    ihc_panel = window._active_ihc_panel()
    brightfield_panel = window._active_brightfield_panel()

    wb_panel.data["densitometry"] = _densitometry_inputs()
    wb_panel.data["wb_statistics"] = _statistics_inputs()
    fluoro_panel.analysis["fluoro_statistics"] = _statistics_inputs()
    ihc_panel.analysis["ihc_statistics"] = _statistics_inputs()
    brightfield_panel.analysis["brightfield_statistics"] = _statistics_inputs()
    window.fluoro_workspace.canvas.current_step = 4
    window.active_module = "stylelab"

    snapshots = {
        "qpcr_data": copy.deepcopy(qpcr_panel.data),
        "wb_data": copy.deepcopy(wb_panel.data),
        "fluoro_analysis": copy.deepcopy(fluoro_panel.analysis),
        "ihc_analysis": copy.deepcopy(ihc_panel.analysis),
        "brightfield_analysis": copy.deepcopy(brightfield_panel.analysis),
    }

    monkeypatch.setattr(window, "_refresh_qpcr_ui", lambda panel: calls.append(f"qpcr:{panel.panel_id}"))
    monkeypatch.setattr(window, "_refresh_wb_density_chart", lambda result: calls.append("wb_density"))
    monkeypatch.setattr(window, "_refresh_wb_stats_chart", lambda result: calls.append("wb_stats"))
    monkeypatch.setattr(window.wb_canvas, "render", lambda: calls.append("wb_canvas"))
    monkeypatch.setattr(window.fluoro_workspace, "_refresh_fluoro_stats_chart", lambda result: calls.append("fluoro_stats"))
    monkeypatch.setattr(window.fluoro_workspace.canvas, "refresh", lambda: calls.append("fluoro_canvas"))
    monkeypatch.setattr(window, "_refresh_ihc_stats_chart", lambda result: calls.append("ihc_stats"))
    monkeypatch.setattr(window, "_refresh_brightfield_stats_chart", lambda result: calls.append("brightfield_stats"))
    monkeypatch.setattr(window.data_panel, "brightfield_stats_plot_source", lambda: "traditional")

    expected = [
        f"qpcr:{qpcr_panel.panel_id}",
        "wb_density",
        "wb_stats",
        "fluoro_stats",
        "fluoro_canvas",
        "ihc_stats",
        "brightfield_stats",
    ]

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        window.apply_style_lab_runtime_saved_preset()
        clear_runtime_active_style_preset()
        window.apply_style_lab_runtime_saved_preset()
    finally:
        clear_runtime_active_style_preset()

    assert calls == [*expected, *expected]
    assert qpcr_panel.data == snapshots["qpcr_data"]
    assert wb_panel.data == snapshots["wb_data"]
    assert fluoro_panel.analysis == snapshots["fluoro_analysis"]
    assert ihc_panel.analysis == snapshots["ihc_analysis"]
    assert brightfield_panel.analysis == snapshots["brightfield_analysis"]

    window.close()
    app.processEvents()


def test_fluorescence_canvas_applies_runtime_image_plate_style_without_mutating_panel_records(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        _runtime_style_record(
            "Runtime Fluoro image plate",
            {
                "point_color": "#123456",
                "bar_edge_color": "#654321",
                "font_family": "Courier New",
                "font_size_pt": 12,
                "bar_line_width": 3.5,
                "series_colors": ["#123456", "#AA5500"],
            },
        )
    )
    panel = FluorescencePanel(panel_id="Figure1FluoroImagePlateRuntimeStyle")
    image_path = _write_runtime_fluoro_tiff(tmp_path / "runtime_image_plate.tif")
    image_record = panel.import_image(image_path)
    image_record["layout_mode"] = "merge_only"
    image_record["channels"][0]["display_min"] = 0.0
    image_record["channels"][0]["display_max"] = 100.0
    image_record["channels"][1]["visible"] = False
    image_record["scale_bar"].update(
        {
            "length_px": 4.0,
            "length_um": 5.0,
            "height_px": 1.0,
            "font_size": 7,
            "color": "#FFFFFF",
            "show_text": True,
        }
    )
    original_channels = copy.deepcopy(image_record["channels"])
    original_scale_bar = copy.deepcopy(image_record["scale_bar"])
    canvas = FluorescenceCanvas()

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        canvas.set_panel(panel)
        canvas.set_step(4)

        pixmap_item = next(item for item in canvas.scene.items() if isinstance(item, QGraphicsPixmapItem))
        # Layout fitting is transform-based; the backing pixmap remains at
        # the source channel dimensions rather than a 720 px preview.
        assert pixmap_item.pixmap().width() == 14
        assert pixmap_item.pixmap().height() == 12
        rendered_pixel = pixmap_item.pixmap().toImage().pixelColor(4, 3).name().upper()
        labels = [
            item
            for item in canvas.scene.items()
            if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"Merge", "5 um"}
        ]
        scale_lines = [item for item in canvas.scene.items() if isinstance(item, QGraphicsLineItem)]

        assert rendered_pixel == "#123456"
        assert labels
        assert {item.defaultTextColor().name().upper() for item in labels} == {"#654321"}
        assert scale_lines
        assert scale_lines[0].pen().color().name().upper() == "#654321"
        assert scale_lines[0].pen().widthF() > 1.0
        assert image_record["channels"] == original_channels
        assert image_record["scale_bar"] == original_scale_bar
        assert saved.preset_id not in str(panel.to_dict())
        assert str(registry.storage_path) not in str(panel.to_dict())
    finally:
        clear_runtime_active_style_preset()
        canvas.close()
        app.processEvents()


def test_fluorescence_core_render_applies_runtime_image_plate_style_without_mutating_records(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        _runtime_style_record(
            "Runtime Fluoro core render",
            {
                "point_color": "#123456",
                "bar_edge_color": "#654321",
                "font_family": "Courier New",
                "font_size_pt": 12,
                "bar_line_width": 3.0,
                "series_colors": ["#123456", "#AA5500"],
            },
        )
    )
    data = np.zeros((2, 48, 64), dtype=np.uint16)
    data[0, 10:30, 10:30] = 100
    data[1, 10:30, 10:30] = 100
    image_path = tmp_path / "runtime_core_render.tif"
    tifffile.imwrite(image_path, data, metadata={"axes": "CYX"})
    panel = FluorescencePanel(panel_id="Figure1FluoroCoreRenderRuntimeStyle")
    image_record = panel.import_image(image_path)
    image_record["channels"][0]["display_min"] = 0.0
    image_record["channels"][0]["display_max"] = 100.0
    image_record["channels"][1]["visible"] = False
    image_record["scale_bar"].update(
        {
            "length_px": 12.0,
            "length_um": 5.0,
            "height_px": 1.0,
            "font_size": 7,
            "color": "#FFFFFF",
            "show_text": True,
        }
    )
    original_channels = copy.deepcopy(image_record["channels"])
    original_scale_bar = copy.deepcopy(image_record["scale_bar"])
    project = Project(title="Fluoro core render runtime boundary")
    project.add_panel(panel)

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        rendered = panel._render_active_image()
        preview = panel.render_preview(30, 20, dpi=120)
        png = panel.render_export(30, 20, dpi=300, format="png")

        assert not rendered.isNull()
        assert not preview.isNull()
        assert png.startswith(b"\x89PNG")
        assert rendered.pixelColor(15, 15).name().upper() == "#123456"
        assert rendered.pixelColor(45, 38).name().upper() == "#654321"
        assert image_record["channels"] == original_channels
        assert image_record["scale_bar"] == original_scale_bar
        assert saved.preset_id not in str(project.to_dict())
        assert str(registry.storage_path) not in str(project.to_dict())
        assert "Runtime Fluoro core render" not in str(project.to_dict())
    finally:
        clear_runtime_active_style_preset()
        app.processEvents()


def test_fluorescence_core_render_preserves_saved_channel_and_scale_bar_style_without_runtime(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    clear_runtime_active_style_preset()
    data = np.zeros((2, 48, 64), dtype=np.uint16)
    data[0, 10:30, 10:30] = 100
    data[1, 10:30, 10:30] = 100
    image_path = tmp_path / "custom_core_render.tif"
    tifffile.imwrite(image_path, data, metadata={"axes": "CYX"})
    panel = FluorescencePanel(panel_id="Figure1FluoroCoreRenderSavedStyle")
    image_record = panel.import_image(image_path)
    image_record["channels"][0]["pseudocolor"] = "#445566"
    image_record["channels"][0]["display_min"] = 0.0
    image_record["channels"][0]["display_max"] = 100.0
    image_record["channels"][1]["visible"] = False
    image_record["scale_bar"].update(
        {
            "length_px": 12.0,
            "length_um": 5.0,
            "height_px": 2.0,
            "font_size": 7,
            "color": "#778899",
            "show_text": False,
        }
    )

    try:
        rendered = panel._render_active_image()

        assert rendered.pixelColor(15, 15).name().upper() == "#445566"
        assert rendered.pixelColor(45, 38).name().upper() == "#778899"
    finally:
        clear_runtime_active_style_preset()
        app.processEvents()


@pytest.mark.parametrize("stale_mode", ["deleted", "corrupt", "invalid_image_plate_color", "invalid_channel_colors"])
def test_fluorescence_core_render_preserves_saved_style_when_runtime_selection_becomes_invalid(
    tmp_path,
    stale_mode: str,
) -> None:
    app = QApplication.instance() or QApplication([])
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        _runtime_style_record(
            "Runtime Fluoro stale render",
            {
                "point_color": "#123456",
                "bar_edge_color": "#654321",
                "series_colors": ["#123456", "#AA5500"],
            },
        )
    )
    data = np.zeros((2, 48, 64), dtype=np.uint16)
    data[0, 10:30, 10:30] = 100
    data[1, 10:30, 10:30] = 100
    image_path = tmp_path / f"stale_core_render_{stale_mode}.tif"
    tifffile.imwrite(image_path, data, metadata={"axes": "CYX"})
    panel = FluorescencePanel(panel_id=f"Figure1FluoroStaleRuntime{stale_mode}")
    image_record = panel.import_image(image_path)
    image_record["channels"][0]["pseudocolor"] = "#445566"
    image_record["channels"][0]["display_min"] = 0.0
    image_record["channels"][0]["display_max"] = 100.0
    image_record["channels"][1]["visible"] = False
    image_record["scale_bar"].update(
        {
            "length_px": 12.0,
            "length_um": 5.0,
            "height_px": 2.0,
            "font_size": 7,
            "color": "#778899",
            "show_text": False,
        }
    )
    original_channels = copy.deepcopy(image_record["channels"])
    original_scale_bar = copy.deepcopy(image_record["scale_bar"])
    project = Project(title="Fluoro stale runtime render boundary")
    project.add_panel(panel)

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        if stale_mode == "deleted":
            assert registry.delete_preset(saved.preset_id)
        elif stale_mode == "corrupt":
            registry.storage_path.write_text("{not valid json", encoding="utf-8")
        elif stale_mode == "invalid_channel_colors":
            saved.plot_style["channel_colors"] = ["#00FF00", "bad"]
            registry.save_preset(saved)
        else:
            saved.plot_style["scale_bar_color"] = "white"
            registry.save_preset(saved)
        registry_text = registry.storage_path.read_text(encoding="utf-8") if registry.storage_path.exists() else ""

        rendered = panel._render_active_image()
        png = panel.render_export(30, 20, dpi=300, format="png")

        assert not rendered.isNull()
        assert png.startswith(b"\x89PNG")
        assert rendered.pixelColor(15, 15).name().upper() == "#445566"
        assert rendered.pixelColor(45, 38).name().upper() == "#778899"
        assert image_record["channels"] == original_channels
        assert image_record["scale_bar"] == original_scale_bar
        assert registry.storage_path.read_text(encoding="utf-8") == registry_text
        project_payload = str(project.to_dict())
        assert saved.preset_id not in project_payload
        assert str(registry.storage_path) not in project_payload
        assert "Runtime Fluoro stale render" not in project_payload
    finally:
        clear_runtime_active_style_preset()
        app.processEvents()


def test_fluorescence_core_render_runtime_channel_colors_map_by_channel_index_when_first_channel_hidden(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        _runtime_style_record(
            "Runtime Fluoro hidden first",
            {
                "point_color": "#111111",
                "series_colors": ["#111111", "#222222"],
            },
        )
    )
    data = np.zeros((2, 48, 64), dtype=np.uint16)
    data[0, 10:30, 10:30] = 100
    data[1, 10:30, 10:30] = 100
    image_path = tmp_path / "hidden_first_core_render.tif"
    tifffile.imwrite(image_path, data, metadata={"axes": "CYX"})
    panel = FluorescencePanel(panel_id="Figure1FluoroHiddenFirstRuntimeStyle")
    image_record = panel.import_image(image_path)
    image_record["view_mode"] = "single"
    image_record["channels"][0]["visible"] = False
    image_record["channels"][1]["visible"] = True
    image_record["channels"][1]["display_min"] = 0.0
    image_record["channels"][1]["display_max"] = 100.0

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        rendered = panel._render_active_image()

        assert rendered.pixelColor(15, 15).name().upper() == "#222222"
    finally:
        clear_runtime_active_style_preset()
        app.processEvents()


def test_global_toolbar_preset_selector_updates_active_science_module() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("qpcr")
    preset_combo = window.findChild(QComboBox, "V9StylePresetCombo")
    assert preset_combo is not None
    assert [preset_combo.itemText(index) for index in range(preset_combo.count())] == ["Nature", "Cell", "Science"]
    assert preset_combo.currentText() == "Nature"
    assert window.data_panel.journal_preset_container.isHidden()

    preset_combo.setCurrentText("Cell")
    assert window._active_qpcr_panel().style["preset"] == "Cell"

    window.set_module("wb")
    assert preset_combo.currentText() == "Nature"
    assert window.data_panel.journal_preset_container.isHidden()

    preset_combo.setCurrentText("Science")
    assert window._active_wb_panel().style["preset"] == "Science"

    window.set_module("fluoro")
    assert preset_combo.currentText() == "Nature"
    preset_combo.setCurrentText("Cell")
    assert window._active_fluoro_panel().style["preset"] == "Cell"

    window.set_module("ihc")
    assert preset_combo.currentText() == "Nature"
    assert window._active_ihc_panel().style["preset"] == "Nature"
    preset_combo.setCurrentText("Science")
    assert window._active_ihc_panel().style["preset"] == "Science"

    window.set_module("brightfield")
    assert preset_combo.currentText() == "Nature"
    assert window._active_brightfield_panel().style["preset"] == "Nature"
    preset_combo.setCurrentText("Cell")
    assert window._active_brightfield_panel().style["preset"] == "Cell"

    window.close()
    app.processEvents()


def test_ihc_and_brightfield_panels_seed_nature_preset() -> None:
    assert IHCPanel(panel_id="Figure1E").style["preset"] == "Nature"
    assert BrightfieldPanel(panel_id="Figure1F").style["preset"] == "Nature"


def test_module_workflow_panels_do_not_embed_journal_preset_selector() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("wb")

    assert window.data_panel.journal_preset_container.isHidden()
    assert window.data_panel.wb_bottom_bar.layout().indexOf(window.data_panel.journal_preset_container) == -1
    assert window.data_panel.wb_step_nav.layout().indexOf(window.data_panel.journal_preset_container) == -1

    window.set_module("qpcr")
    assert window.data_panel.journal_preset_container.isHidden()
    assert window.data_panel.qpcr_import_layout.indexOf(window.data_panel.journal_preset_container) == -1
    window.close()
    app.processEvents()


def test_fluor_module_uses_global_preset_selector_not_embedded_workflow_selector() -> None:
    app = QApplication.instance() or QApplication([])
    panel = FluorescencePanel(panel_id="Figure1C")
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workflow_host = QWidget()
    host_layout = QVBoxLayout(workflow_host)
    host_layout.addWidget(workspace.workflow_panel)
    workflow_host.show()
    app.processEvents()

    assert not hasattr(workspace, "journal_preset_combo")
    assert not any(label.text() == "Journal preset" for label in workspace.workflow_panel.findChildren(QLabel))

    workflow_host.close()
    app.processEvents()
