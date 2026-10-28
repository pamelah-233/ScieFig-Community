from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QImage

from core.models.panel import BasePanel, register_panel_class
from core.utils.hashing import sha256_file
from core.utils.json import dataframe_records
from core.utils.svg_text import apply_svg_text_overrides


@register_panel_class
@dataclass
class QPCRPanel(BasePanel):
    PANEL_TYPE: ClassVar[str] = "qPCR_bar_scatter"
    SOURCE_MODULE: ClassVar[str] = "qpcr"
    panel_type: str = field(default=PANEL_TYPE)

    def _apply_analysis_result(self, result, parsed_rows=None, parsed_instrument=None, header_row=None, sheet_name=None) -> None:
        if parsed_rows is not None:
            self.data["raw_rows"] = parsed_rows
        self.data["processed_data"] = result.to_payload()
        if parsed_instrument is not None:
            self.data["instrument"] = parsed_instrument
        if header_row is not None:
            self.data["header_row"] = header_row
        if sheet_name is not None:
            self.data["sheet_name"] = sheet_name
        self.analysis.update(result.analysis)
        self.style.update(
            {
                "preset": self.style.get("preset", "Nature"),
                "font": self.style.get("font", "Arial"),
                "font_size_label": self.style.get("font_size_label", 7),
                "font_size_tick": self.style.get("font_size_tick", 6),
                "palette": self.style.get("palette", ["#999999", "#d97757", "#6b7fb8"]),
                "bar_fill": self.style.get("bar_fill", "outline"),
                "axis_width": self.style.get("axis_width", 0.5),
                "spines_top": False,
                "spines_right": False,
                "analysis_summary": result.summary,
            }
        )
        self.export.update({"format": "PDF", "dpi": 1200, "color_mode": "RGB", "width_mm": 89})

    def import_qpcr_file(
        self,
        path: str | Path,
        instrument: str = "auto",
        reference_gene: str | None = None,
        control_group: str | None = None,
        error_bar: str = "SEM",
        group_overrides: dict[str, str] | None = None,
        included_samples: set[str] | None = None,
    ) -> None:
        source = Path(path)
        from core.analysis import analyze_qpcr_dataframe
        from core.parsers import parse_qpcr_file

        parsed = parse_qpcr_file(source, instrument=instrument)  # type: ignore[arg-type]
        result = analyze_qpcr_dataframe(
            parsed.dataframe,
            reference_gene=reference_gene,
            control_group=control_group,
            error_bar=error_bar,
            group_overrides=group_overrides,
            included_samples=included_samples,
        )
        parsed_rows = dataframe_records(parsed.dataframe)
        self.data.update(
            {
                "raw_source": source.name,
                "raw_hash": sha256_file(source),
                "raw_copy_path": f"data/qpcr/{source.name}",
            }
        )
        self._apply_analysis_result(
            result,
            parsed_rows=parsed_rows,
            parsed_instrument=parsed.instrument,
            header_row=parsed.header_row,
            sheet_name=parsed.sheet_name,
        )
        self.raw_source_path = source
        self.append_audit("import_data", source.name)
        self.append_audit("set_reference", str(self.analysis["reference_gene"]))
        self.append_audit("set_control_group", str(self.analysis["control_group"]))
        self.append_audit("run_stat", f"{self.analysis['stat_test']} on ΔCt")

    def load_qpcr_file(self, path: str | Path, instrument: str = "auto") -> dict:
        source = Path(path)
        from core.analysis.qpcr_calculator import prepare_qpcr_import_options
        from core.parsers import parse_qpcr_file

        parsed = parse_qpcr_file(source, instrument=instrument)  # type: ignore[arg-type]
        parsed_rows = dataframe_records(parsed.dataframe)
        options = prepare_qpcr_import_options(parsed.dataframe)
        self.data.update(
            {
                "raw_source": source.name,
                "raw_hash": sha256_file(source),
                "raw_copy_path": f"data/qpcr/{source.name}",
                "raw_rows": parsed_rows,
                "cleaned_preview_rows": options["cleaned_data"],
                "instrument": parsed.instrument,
                "header_row": parsed.header_row,
                "sheet_name": parsed.sheet_name,
            }
        )
        self.raw_source_path = source
        self.analysis.clear()
        self.style["analysis_summary"] = "qPCR data imported. Select reference gene and control group, then run analysis."
        self.data["import_options"] = {
            "targets": options["targets"],
            "groups": options["groups"],
            "suggested_reference_gene": options["suggested_reference_gene"],
        }
        self.append_audit("import_data", source.name)
        return self.data["import_options"]

    def reanalyze(
        self,
        reference_gene: str | None = None,
        control_group: str | None = None,
        error_bar: str | None = None,
        group_overrides: dict[str, str] | None = None,
        included_samples: set[str] | None = None,
    ) -> None:
        raw_rows = self.data.get("raw_rows")
        if not raw_rows:
            raise ValueError("qPCR panel has no raw rows to reanalyze.")
        import pandas as pd
        from core.analysis import analyze_qpcr_dataframe

        dataframe = pd.DataFrame(raw_rows)
        result = analyze_qpcr_dataframe(
            dataframe,
            reference_gene=reference_gene or self.analysis.get("reference_gene"),
            control_group=control_group or self.analysis.get("control_group"),
            error_bar=error_bar or self.analysis.get("error_bar", "SEM"),
            group_overrides=group_overrides if group_overrides is not None else self.analysis.get("group_overrides"),
            included_samples=included_samples,
        )
        self._apply_analysis_result(result)
        self.append_audit("reanalyze_qpcr", f"reference={self.analysis['reference_gene']}; control={self.analysis['control_group']}")

    def layout_plot_records(self) -> list[dict]:
        plot_payload = self.data.get("processed_data", {}).get("fold_change", [])
        if not plot_payload:
            return []
        import pandas as pd

        plot_data = pd.DataFrame(plot_payload)
        targets = list(dict.fromkeys(plot_data["target"].astype(str).tolist()))
        layout = dict(self.data.get("qpcr_layout", {}))
        existing = [dict(record) for record in layout.get("plots", []) if isinstance(record, dict)]
        by_target = {str(record.get("target", "")): record for record in existing}
        # Reserve saved placements first, even when their target appears later.
        occupied = [QRectF(float(record.get('x', 24)), float(record.get('y', 24)),
                           float(record.get('width_px', 520)), float(record.get('height_px', 360)))
                    for target, record in by_target.items() if target in targets]
        next_slot = 0
        records: list[dict] = []
        for index, target in enumerate(targets):
            record = dict(by_target.get(target, {}))
            if target not in by_target:
                while True:
                    x = 24.0 + (next_slot % 2) * (520.0 + 24.0)
                    y = 24.0 + (next_slot // 2) * (360.0 + 24.0)
                    next_slot += 1
                    bounds = QRectF(x, y, 520, 360)
                    if not any(bounds.adjusted(-24, -24, 24, 24).intersects(other) for other in occupied):
                        record.update(x=x, y=y)
                        occupied.append(bounds)
                        break
            record.setdefault("plot_uuid", f"qpcr_plot_{uuid4().hex}")
            record.update(
                {
                    "plot_type": "qpcr_target",
                    "target": target,
                    "enabled": bool(record.get("enabled", True)),
                    "x": float(record.get("x", 24.0 + index * 36.0)),
                    "y": float(record.get("y", 24.0 + index * 24.0)),
                    "width_px": int(record.get("width_px", 520)),
                    "height_px": int(record.get("height_px", 360)),
                    "style": dict(record.get("style", {})),
                }
            )
            records.append(record)
        layout["plots"] = records
        self.data["qpcr_layout"] = layout
        return records

    def render_layout_plot(
        self,
        record: dict,
        dpi: int = 1200,
        format: str = "png",
        *,
        include_svg_text_overrides: bool = True,
    ) -> bytes:
        export_format = format.lower().lstrip(".")
        if export_format not in {"png", "svg", "pdf", "tif", "tiff"}:
            raise ValueError(f"Unsupported qPCR layout plot format: {format}")
        width_px = max(1, int(record.get("width_px", 520)))
        height_px = max(1, int(record.get("height_px", 360)))
        # Layout plot sizes are stored in scene logical units, which match the
        # 72 pt export coordinate system used elsewhere in the app.
        target_width_mm = width_px / 72.0 * 25.4
        target_height_mm = height_px / 72.0 * 25.4
        figure = self._build_render_figure(
            target_width_mm,
            target_height_mm,
            dpi,
            target=str(record.get("target", "")),
            style=dict(record.get("style", {})),
            chart_kind=record.get("chart_kind"),
        )
        buffer = BytesIO()
        save_format = "tiff" if export_format in {"tif", "tiff"} else export_format
        save_kwargs = {"format": save_format, "bbox_inches": None, "pad_inches": 0.0}
        if save_format in {"png", "tiff"}:
            save_kwargs["dpi"] = dpi
        from core.plotting.qpcr_plot import save_qpcr_figure

        save_qpcr_figure(figure, buffer, preset_name=str(self.style.get("preset", "Nature")), **save_kwargs)
        rendered = buffer.getvalue()
        if save_format == "svg" and include_svg_text_overrides:
            return apply_svg_text_overrides(rendered, dict(record.get("svg_text_overrides", {})))
        return rendered

    def render_preview(self, target_width_mm: float, target_height_mm: float, dpi: int = 150) -> QImage:
        figure = self._build_render_figure(target_width_mm, target_height_mm, dpi)
        buffer = BytesIO()
        from core.plotting.qpcr_plot import save_qpcr_figure

        save_qpcr_figure(
            figure,
            buffer,
            preset_name=str(self.style.get("preset", "Nature")),
            format="png",
            dpi=dpi,
            bbox_inches="tight",
        )
        image = QImage()
        image.loadFromData(buffer.getvalue(), "PNG")
        if image.isNull():
            raise ValueError("Could not render qPCR preview image.")
        return image

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> bytes:
        export_format = format.lower().lstrip(".")
        if export_format not in {"pdf", "svg", "png", "tif", "tiff"}:
            raise ValueError(f"Unsupported qPCR render export format: {format}")
        figure = self._build_render_figure(target_width_mm, target_height_mm, dpi)
        buffer = BytesIO()
        save_format = "tiff" if export_format in {"tif", "tiff"} else export_format
        save_kwargs = {"format": save_format, "bbox_inches": "tight"}
        if save_format in {"png", "tiff"}:
            save_kwargs["dpi"] = dpi
        from core.plotting.qpcr_plot import save_qpcr_figure

        save_qpcr_figure(figure, buffer, preset_name=str(self.style.get("preset", "Nature")), **save_kwargs)
        return buffer.getvalue()

    def _build_render_figure(self, target_width_mm: float, target_height_mm: float, dpi: int, target: str | None = None, style: dict | None = None, chart_kind: str | None = None):
        plot_payload = self.data.get("processed_data", {}).get("fold_change", [])
        if not plot_payload:
            raise ValueError("qPCR panel has no processed plot data to render.")
        import pandas as pd
        from core.plotting import build_qpcr_figure

        plot_data = pd.DataFrame(plot_payload)
        figure = build_qpcr_figure(
            plot_data,
            self.analysis.get("statistics", {}),
            target=target,
            preset_name=str(self.style.get("preset", "Nature")),
            style=style,
            chart_kind=chart_kind if chart_kind is not None else self._layout_chart_kind_for_target(target),
        )
        figure.set_size_inches(max(0.1, float(target_width_mm) / 25.4), max(0.1, float(target_height_mm) / 25.4), forward=True)
        figure.set_dpi(dpi)
        return figure

    def _layout_chart_kind_for_target(self, target: str | None = None):
        plots = [plot for plot in self.data.get("qpcr_layout", {}).get("plots", []) if isinstance(plot, dict)]
        if target:
            for plot in plots:
                if str(plot.get("target", "")) == str(target):
                    return plot.get("chart_kind")
            return None
        plot_payload = self.data.get("processed_data", {}).get("fold_change", [])
        effective_target = next(
            (
                str(row.get("target", ""))
                for row in plot_payload
                if isinstance(row, dict) and str(row.get("target", ""))
            ),
            "",
        )
        if effective_target:
            for plot in plots:
                if str(plot.get("target", "")) == effective_target:
                    return plot.get("chart_kind")
        return None
