from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter

from core.models.panel import BasePanel, register_panel_class
from core.utils.rendering import qimage_to_bytes, target_pixel_size


@register_panel_class
@dataclass
class DerivedPlotPanel(BasePanel):
    PANEL_TYPE: ClassVar[str] = "derived_plot"
    SOURCE_MODULE: ClassVar[str] = "fluorescence"
    panel_type: str = field(default=PANEL_TYPE)

    @classmethod
    def from_measurement(cls, source_panel_uuid: str, measurement: dict[str, Any]) -> "DerivedPlotPanel":
        measurement_type = str(measurement.get("type", "measurement"))
        title = "ROI intensity" if measurement_type == "roi_intensity" else measurement_type.replace("_", " ").title()
        panel = cls(panel_id=f"{title} result")
        panel.data.update(
            {
                "plot_type": measurement_type,
                "title": title,
                "source_panel_uuid": source_panel_uuid,
                "source_measurement_uuid": measurement.get("measurement_uuid"),
                "source_channels": list(measurement.get("source_channels", [])),
                "results_table": list(measurement.get("results_table", [])),
                "results_summary": dict(measurement.get("results_summary", {})),
                "stale": False,
            }
        )
        panel.append_audit("create_derived_plot_panel", str(measurement.get("measurement_uuid", "")))
        return panel

    def render_preview(self, target_width_mm: float, target_height_mm: float, dpi: int = 150) -> QImage:
        size = target_pixel_size(target_width_mm, target_height_mm, dpi)
        image = QImage(size, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_summary(painter, image.width(), image.height())
        painter.end()
        return image

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> bytes:
        raster_format = format.lower().lstrip(".")
        if raster_format in {"pdf", "svg"}:
            raster_format = "png"
        return qimage_to_bytes(self.render_preview(target_width_mm, target_height_mm, dpi), raster_format)

    def _paint_summary(self, painter: QPainter, width: int, height: int) -> None:
        margin = max(12, int(width * 0.06))
        title_font = QFont("Arial", max(10, int(height * 0.075)), QFont.Weight.Bold)
        body_font = QFont("Arial", max(8, int(height * 0.048)))
        painter.setPen(QColor("#111111"))
        painter.setFont(title_font)
        painter.drawText(margin, margin, width - margin * 2, int(height * 0.12), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), str(self.data.get("title", "Derived result")))

        rows = list(self.data.get("results_table", []))
        painter.setFont(body_font)
        y = margin + int(height * 0.16)
        line_h = max(16, int(height * 0.075))
        if not rows:
            painter.drawText(margin, y, width - margin * 2, line_h, int(Qt.AlignmentFlag.AlignLeft), "No result rows")
            return
        for row in rows[:4]:
            text = (
                f"{row.get('channel', row.get('channel_name', 'Channel'))}: "
                f"mean {float(row.get('mean', 0.0)):.2f}, "
                f"IntDen {float(row.get('integrated_density', 0.0)):.2f}"
            )
            painter.drawText(margin, y, width - margin * 2, line_h, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
            y += line_h
