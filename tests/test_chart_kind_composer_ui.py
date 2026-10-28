from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from core.chart_payload import effective_composer_chart_kind, qpcr_live_plot_payload, raw_groups_from_payload, render_plot_payload_bytes
from core.models import ExternalImagePanel, Project, QPCRPanel
from core.models.composer import ComposerFigure, PanelReference
from core.utils.svg_text import svg_text_entries
from ui.widgets.composer_canvas import ComposerCanvas


def _q_app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _qpcr_panel() -> QPCRPanel:
    panel = QPCRPanel(panel_id="Figure1A")
    panel.data["processed_data"] = {
        "fold_change": [
            {"target": "GeneA", "group": "Control", "fold_change": 1.0},
            {"target": "GeneA", "group": "Drug", "fold_change": 2.0},
            {"target": "GeneB", "group": "Control", "fold_change": 10.0},
            {"target": "GeneB", "group": "Drug", "fold_change": 20.0},
        ]
    }
    panel.data["qpcr_layout"] = {
        "plots": [
            {"plot_uuid": "gene-a", "target": "GeneA", "enabled": True},
            {"plot_uuid": "gene-b", "target": "GeneB", "enabled": True},
        ]
    }
    panel.analysis["error_bar"] = "sem"
    for row in panel.data["processed_data"]["fold_change"]:
        row.update(mean_fold_change=row["fold_change"], error=0.0)
    return panel


def test_panel_reference_chart_kind_roundtrips_and_legacy_defaults_to_inherited() -> None:
    ref = PanelReference(panel_uid="panel-1", source_module="qpcr", chart_kind="box")
    payload = ref.to_dict()
    assert payload["chart_kind"] == "box"
    assert PanelReference.from_dict(payload).chart_kind == "box"
    assert PanelReference.from_dict({"panel_uid": "panel-1", "source_module": "qpcr"}).chart_kind == ""


def test_qpcr_live_payload_uses_enabled_target_record_and_filters_rows_by_target() -> None:
    panel = _qpcr_panel()
    panel.data["qpcr_layout"]["plots"] = [
        {"plot_uuid": "gene-a", "target": "GeneA", "enabled": True},
        {"plot_uuid": "gene-b", "target": "GeneB", "enabled": True},
    ]
    payload = qpcr_live_plot_payload(panel)
    assert payload is not None
    assert payload["target"] == "GeneA"
    assert {row["target"] for row in payload["fold_change_rows"]} == {"GeneA"}
    assert {row["group"] for row in raw_groups_from_payload(payload)} == {"Control", "Drug"}
    assert {row["value"] for row in raw_groups_from_payload(payload)} == {1.0, 2.0}
    mixed = dict(payload)
    mixed["fold_change_rows"] = list(mixed["fold_change_rows"]) + [
        {"target": "GeneB", "group": "Drug", "fold_change": 20.0}
    ]
    assert {row["value"] for row in raw_groups_from_payload(mixed)} == {1.0, 2.0}


def test_composer_references_same_asset_keep_local_kind_and_source_unchanged(tmp_path: Path) -> None:
    svg = tmp_path / "source.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
        '<text x="10" y="20">Control</text></svg>',
        encoding="utf-8",
    )
    source_before = svg.read_bytes()
    panel = ExternalImagePanel(panel_id="stats")
    panel.import_image(svg)
    panel.data["export_item_type"] = "statistics_chart"
    panel.data["plot_payload"] = {
        "schema_version": 1,
        "family": "grouped",
        "chart_kind": "bar_dot",
        "statistics_result": {"plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}]},
        "style": {},
        "y_label": "Value",
        "unit": "a.u.",
        "source_module": "test",
        "sample_unit": "biological_replicate",
    }
    ref_bar = PanelReference(panel_uid=panel.panel_uuid, source_module="statistics", chart_kind="bar")
    ref_box = PanelReference(panel_uid=panel.panel_uuid, source_module="statistics", chart_kind="box")
    payload = panel.data["plot_payload"]
    rendered = [
        render_plot_payload_bytes(payload, fmt="svg", chart_kind=effective_composer_chart_kind(panel, ref, payload), width_mm=30, height_mm=20, dpi=300)
        for ref in (ref_bar, ref_box)
    ]
    assert all(rendered)
    assert rendered[0] != rendered[1]
    assert svg.read_bytes() == source_before
    assert panel.data["plot_payload"]["chart_kind"] == "bar_dot"


def test_composer_plot_rerender_keeps_non_numeric_svg_override_but_drops_stale_tick_override() -> None:
    from core.chart_payload import filter_svg_text_overrides_for_rerender

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text>1</text><text>Panel label</text></svg>'
    kept = filter_svg_text_overrides_for_rerender(
        {"0": "999", "1": "Edited label"},
        svg,
    )
    assert "0" not in kept
    assert kept["1"] == "Edited label"


def test_live_qpcr_composer_does_not_switch_gene_when_first_layout_plot_disabled() -> None:
    panel = _qpcr_panel()
    panel.data["qpcr_layout"]["plots"][0].update(enabled=False, chart_kind="box")
    payload = qpcr_live_plot_payload(panel)
    assert payload["target"] == "GeneA"
    assert payload["chart_kind"] == "box"


def test_main_window_chart_selector_starts_hidden_and_composer_kind_changes_one_ref() -> None:
    from PyQt6.QtCore import QPointF
    from ui.main_window import MainWindow

    app = _q_app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window._confirm_destructive_action = lambda *args, **kwargs: True
    try:
        assert window.chart_kind_controls.isHidden()
        panel = _qpcr_panel()
        for row in panel.data["processed_data"]["fold_change"]:
            row.update(mean_fold_change=row["fold_change"], error=0.0)
        window.project.add_panel(panel)
        window.set_module("composer")
        window.composer_workspace.place_asset(panel.panel_uuid, QPointF(50, 50))
        figure = window.composer_canvas.current_figure()
        ref = next(ref for ref in figure.panel_refs if ref.panel_uid == panel.panel_uuid)
        item = next(item for item in window.composer_canvas.scene.items() if item.data(0) == "panel_ref" and item.data(1) == ref.ref_uid)
        item.setSelected(True)
        window._sync_chart_kind_controls()
        assert not window.chart_kind_controls.isHidden()
        window._on_chart_kind_chosen("box_dot")
        assert ref.chart_kind == "box_dot"
        assert window.chart_kind_controls.current_kind() == "box_dot"
        assert not panel.data["qpcr_layout"]["plots"][0].get("chart_kind")
        window.switch_language("zh")
        assert window.chart_kind_controls._label.text() == "图表类型"
        window.switch_language("en")
        assert window.chart_kind_controls._label.text() == "Chart type"
    finally:
        window.close()
        app.processEvents()


def test_qpcr_layout_chart_switch_preserves_selection_and_data() -> None:
    from ui.main_window import MainWindow
    from test_layout_chart_adapter import _qpcr_panel as layout_panel

    app = _q_app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window._confirm_destructive_action = lambda *args, **kwargs: True
    try:
        panel = layout_panel()
        before = copy.deepcopy(panel.data["processed_data"])
        window._active_qpcr_panel = lambda: panel
        window.active_module = "qpcr"
        window.canvas_stack.setCurrentWidget(window.qpcr_layout_workspace)
        window.qpcr_layout_canvas.set_panel(panel)
        item = next(item for item in window.qpcr_layout_canvas.scene.items() if item.data(0) == "qpcr_layout_plot")
        uid = item.data(1)
        item.setSelected(True)
        window._on_chart_kind_chosen("box_dot")
        record = next(record for record in panel.layout_plot_records() if record["plot_uuid"] == uid)
        assert record["chart_kind"] == "box_dot"
        assert window.chart_kind_controls.current_kind() == "box_dot"
        assert panel.data["processed_data"] == before
        assert any(item.data(1) == uid for item in window.qpcr_layout_canvas.scene.selectedItems())
    finally:
        window.close()
        app.processEvents()


def test_source_text_override_follows_label_when_chart_ticks_change() -> None:
    from core.chart_payload import filter_svg_text_overrides_for_rerender
    old = b'<svg xmlns="http://www.w3.org/2000/svg"><text>2</text><text>Value</text></svg>'
    new = b'<svg xmlns="http://www.w3.org/2000/svg"><text>Value</text></svg>'
    assert filter_svg_text_overrides_for_rerender({"0": "999", "1": "Edited axis"}, new, source_svg=old) == {"0": "Edited axis"}
    panel = _qpcr_panel()
    payload = qpcr_live_plot_payload(panel)
    source = render_plot_payload_bytes(payload, fmt="svg", chart_kind="bar")
    payload["chart_kind"] = "bar"
    entries = svg_text_entries(source)
    label = next(entry for entry in entries if entry.text == "Relative mRNA expression")
    updated = render_plot_payload_bytes(payload, fmt="svg", chart_kind="box", svg_text_overrides={str(label.index): "Edited axis"})
    assert "Edited axis" in [entry.text for entry in svg_text_entries(updated)]
    assert "Relative mRNA expression" not in [entry.text for entry in svg_text_entries(updated)]


def test_image_like_panel_never_rerenders_stale_chart_payload() -> None:
    app = _q_app()
    canvas = ComposerCanvas()
    panel = ExternalImagePanel(panel_id="overlay")
    panel.data.update(export_item_type="overlay", plot_type="measurement_overlay", plot_payload=qpcr_live_plot_payload(_qpcr_panel()))
    assert canvas._plot_payload_for_ref(panel, None) is None
    canvas.close()


def test_composer_chart_text_edit_is_reference_local_and_uses_current_svg(tmp_path: Path) -> None:
    from ui.widgets.composer_canvas import ComposerPanelItem
    app = _q_app()
    canvas = ComposerCanvas()
    panel = ExternalImagePanel(panel_id="chart")
    payload = qpcr_live_plot_payload(_qpcr_panel())
    source = tmp_path / "chart.svg"
    source.write_bytes(render_plot_payload_bytes(payload, fmt="svg"))
    panel.import_image(source)
    panel.data.update(export_item_type="statistics_chart", plot_payload=payload)
    ref = PanelReference(panel_uid=panel.panel_uuid, source_module="qpcr", chart_kind="box")
    before = source.read_bytes()
    item = ComposerPanelItem(canvas, ref, panel, None)
    svg = canvas._plot_payload_bytes_for_ref(panel, ref, "svg", 100, 70, 180)
    entry = next(entry for entry in svg_text_entries(svg) if entry.text == "Relative mRNA expression")
    assert item.apply_svg_text_override(entry.index, text="Local label")
    updated = canvas._plot_payload_bytes_for_ref(panel, ref, "svg", 100, 70, 180)
    assert "Local label" in [entry.text for entry in svg_text_entries(updated)]
    assert source.read_bytes() == before
    assert PanelReference.from_dict(ref.to_dict()).chart_text_overrides == ref.chart_text_overrides
    sibling = PanelReference(panel_uid=panel.panel_uuid, source_module="qpcr", chart_kind="box")
    assert "Local label" not in [entry.text for entry in svg_text_entries(canvas._plot_payload_bytes_for_ref(panel, sibling, "svg", 100, 70, 180))]
    canvas.close()


def test_composer_chart_svg_export_is_vector_and_all_formats_render(tmp_path: Path) -> None:
    app = _q_app()
    canvas = ComposerCanvas()
    project = Project(title="chart export")
    panel = _qpcr_panel()
    project.add_panel(panel)
    canvas.set_project(project)
    canvas.place_panel(panel.panel_uuid)
    ref = canvas.current_figure().panel_refs[0]
    ref.chart_kind = "box_dot"
    for extension in ("svg", "png", "pdf"):
        path = canvas.export_figure(tmp_path / f"chart.{extension}", dpi=300)
        assert path.stat().st_size > 1000
        if extension == "svg":
            assert "<image" not in path.read_text(encoding="utf-8")
    assert ref.chart_kind == "box_dot"
    canvas.close()


@pytest.mark.parametrize("module", ["wb", "fluoro", "ihc", "brightfield"])
def test_module_layout_chart_switch_repaints_and_keeps_selection(module, tmp_path):
    from PyQt6.QtGui import QImage
    from ui.main_window import MainWindow
    from ui.controllers.chart_kind_controller import layout_object_ref_from_item
    from test_layout_chart_adapter import _wb_panel, _fluoro_panel, _ihc_panel, _bf_panel

    app = _q_app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window._confirm_destructive_action = lambda *args: True
    panel = {"wb": _wb_panel, "fluoro": _fluoro_panel, "ihc": _ihc_panel, "brightfield": _bf_panel}[module]()
    window.active_module = module
    setattr(window, f"_active_{module}_panel", lambda: panel)
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(0xFF808080)
    try:
        if module == "wb":
            panel.data.pop("image_data", None)
            canvas = window.wb_canvas
            canvas.view.set_mode("move")
            canvas.set_panel(panel)
        elif module == "fluoro":
            path = tmp_path / "signal.png"
            image.save(str(path))
            panel.data["fluorescence_images"] = []
            panel.import_image(path)
            panel.analysis["fluoro_layout_plots"] = panel.analysis["fluoro_layout_plots"][:1]
            canvas = window.fluoro_workspace.canvas
            canvas.set_panel(panel)
            canvas.set_step(4)
        else:
            panel.source_qimage = lambda image_uid=None: image
            workspace = getattr(window, f"{module}_workspace")
            workspace.set_panel(panel)
            workspace.current_step = 3 if module == "ihc" else 4
            provider = window._ihc_layout_pixmap_provider(panel, None) if module == "ihc" else window._brightfield_layout_pixmap_provider(panel, None)
            workspace.prepare_layout(measurement=None, pixmap_provider=provider)
            canvas = workspace.layout_canvas
        from PyQt6.QtWidgets import QGraphicsItem
        item = next(item for item in canvas.scene.items() if item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable and window.chart_kind_controller.inspect_layout(panel, layout_object_ref_from_item(item)).visible)
        item.setSelected(True)
        window._on_chart_kind_chosen("box_dot")
        assert window.chart_kind_controls.current_kind() == "box_dot"
        assert any(window.chart_kind_controller.inspect_layout(panel, layout_object_ref_from_item(item)).visible for item in canvas.scene.selectedItems())
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("module", ["ihc", "brightfield"])
def test_histology_output_transfer_keeps_edited_chart_text_and_payload(module, tmp_path):
    from PyQt6.QtGui import QImage
    from ui.main_window import MainWindow
    from test_layout_chart_adapter import _ihc_panel, _bf_panel

    app = _q_app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window._confirm_destructive_action = lambda *args: True
    panel = _ihc_panel() if module == "ihc" else _bf_panel()
    window.project.add_panel(panel)
    setattr(window, f"_active_{module}_panel", lambda: panel)
    measurement = {"measurement_uuid": "m1", "image_uid": "image-a", "results": {}}
    if module == "brightfield":
        import numpy as np
        window._active_brightfield_measurement = lambda *args: measurement
        panel.render_measurement_overlay = lambda **kwargs: np.full((48, 64, 3), 128, dtype=np.uint8)
    setattr(window, f"_active_{module}_measurement_for_export", lambda *args, **kwargs: measurement)
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(0xFF808080)
    setattr(window, f"_render_{module}_overlay_image", lambda *args: image)
    window._generated_asset_dir = lambda: tmp_path
    output = panel.data[f"{module}_layout"]["outputs"][0]
    output["chart_kind"] = "box_dot"
    raw = window._statistics_svg(panel.analysis["ihc_statistics"], "Nature", chart_kind="box_dot") if module == "ihc" else window._brightfield_chart_svg(panel, panel.analysis["brightfield_chart_export"], chart_kind="box_dot")
    entry = next(entry for entry in svg_text_entries(raw) if entry.text == "Ctrl")
    output["svg_text_overrides"] = {str(entry.index): "Edited control"}
    try:
        assets = getattr(window, f"add_{module}_outputs_to_composer")()
        chart = next(asset for asset in assets if asset.data.get("export_item_key") == "statistics_chart")
        source = Path(chart.data["image_path"])
        assert "Edited control" in [entry.text for entry in svg_text_entries(source.read_bytes())]
        assert chart.data["plot_payload"]["chart_kind"] == "box_dot"
        assert chart.data["svg_text_overrides"] == output["svg_text_overrides"]
    finally:
        window.close()
        app.processEvents()
