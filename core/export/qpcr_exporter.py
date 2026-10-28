from __future__ import annotations

from pathlib import Path

from core.export.export_validator import validate_export_path
from core.models import QPCRPanel


def export_qpcr_panel(panel: QPCRPanel, path: str | Path, target: str | None = None) -> Path:
    target_path = Path(path)
    validation = validate_export_path(target_path)
    if not validation.ok:
        raise ValueError("; ".join(validation.messages))

    plot_payload = panel.data.get("processed_data", {}).get("fold_change", [])
    statistics = panel.analysis.get("statistics", {})
    if not plot_payload:
        raise ValueError("qPCR panel has no processed plot data to export.")

    from core.plotting import build_qpcr_figure
    from core.plotting.qpcr_plot import save_qpcr_figure
    import pandas as pd

    plot_data = pd.DataFrame(plot_payload)
    chart_kind = panel._layout_chart_kind_for_target(target)
    fig = build_qpcr_figure(
        plot_data,
        statistics,
        target=target,
        preset_name=str(panel.style.get("preset", "Nature")),
        chart_kind=chart_kind,
    )
    suffix = target_path.suffix.lower()
    dpi = int(panel.export.get("dpi", 1200))
    preset_name = str(panel.style.get("preset", "Nature"))
    if suffix == ".svg":
        save_qpcr_figure(fig, target_path, preset_name=preset_name, format="svg", bbox_inches="tight")
    elif suffix == ".pdf":
        save_qpcr_figure(fig, target_path, preset_name=preset_name, format="pdf", bbox_inches="tight")
    elif suffix in {".tiff", ".tif"}:
        save_qpcr_figure(fig, target_path, preset_name=preset_name, format="tiff", dpi=dpi, bbox_inches="tight")
    elif suffix == ".png":
        save_qpcr_figure(fig, target_path, preset_name=preset_name, format="png", dpi=dpi, bbox_inches="tight")
    else:
        raise ValueError(f"Unsupported qPCR export format: {suffix}")
    panel.export.update(
        {
            "format": suffix.lstrip(".").upper(),
            "dpi": dpi,
            "color_mode": panel.export.get("color_mode", "RGB"),
            "last_export_path": str(target_path),
        }
    )
    panel.append_audit("export", f"{target_path.name}, {dpi}dpi")
    return target_path
