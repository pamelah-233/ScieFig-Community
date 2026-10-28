from __future__ import annotations

from pathlib import Path

from core import plotting
from core.export import export_qpcr_panel
from core.models import QPCRPanel


def _multi_target_panel() -> QPCRPanel:
    panel = QPCRPanel(panel_id="Figure-qPCR-contract")
    panel.data["processed_data"] = {
        "fold_change": [
            {"target": "G1", "group": "Ctrl", "fold_change": 1.0, "mean_fold_change": 1.0, "error": 0.1},
            {"target": "G1", "group": "Ctrl", "fold_change": 1.2, "mean_fold_change": 1.0, "error": 0.1},
            {"target": "G1", "group": "Treat", "fold_change": 2.0, "mean_fold_change": 2.0, "error": 0.2},
            {"target": "G1", "group": "Treat", "fold_change": 2.2, "mean_fold_change": 2.0, "error": 0.2},
            {"target": "G2", "group": "Ctrl", "fold_change": 3.0, "mean_fold_change": 3.0, "error": 0.3},
            {"target": "G2", "group": "Ctrl", "fold_change": 3.2, "mean_fold_change": 3.0, "error": 0.3},
            {"target": "G2", "group": "Treat", "fold_change": 4.0, "mean_fold_change": 4.0, "error": 0.4},
            {"target": "G2", "group": "Treat", "fold_change": 4.2, "mean_fold_change": 4.0, "error": 0.4},
        ]
    }
    panel.analysis["statistics"] = {}
    panel.data["qpcr_layout"] = {
        "plots": [
            {"plot_uuid": "plot-g2", "target": "G2", "chart_kind": "violin", "enabled": False},
            {"plot_uuid": "plot-g1", "target": "G1", "chart_kind": "box", "enabled": True},
        ]
    }
    return panel


def test_qpcr_default_chart_kind_follows_effective_first_processed_target() -> None:
    panel = _multi_target_panel()

    assert panel._layout_chart_kind_for_target() == "box"
    assert panel._layout_chart_kind_for_target("G2") == "violin"


def test_qpcr_export_uses_effective_target_record_when_layout_order_differs(tmp_path: Path, monkeypatch) -> None:
    panel = _multi_target_panel()
    observed: list[str | None] = []
    real_build = plotting.build_qpcr_figure

    def capture_chart_kind(*args, **kwargs):
        observed.append(kwargs.get("chart_kind"))
        return real_build(*args, **kwargs)

    monkeypatch.setattr(plotting, "build_qpcr_figure", capture_chart_kind)
    export_qpcr_panel(panel, tmp_path / "qPCR.svg")

    assert observed == ["box"]
