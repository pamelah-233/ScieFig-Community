from __future__ import annotations

from collections.abc import Sequence

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from core.utils.i18n import bind_i18n, tr


class ReliabilityBanner(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ReliabilityBanner")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self.status_label = bind_i18n(QLabel(), "reliability_awaiting")
        self.status_label.setObjectName("SectionLabel")
        self.status_label.setWordWrap(True)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("DimLabel")
        self.detail_label.setWordWrap(True)

        layout.addWidget(self.status_label)
        layout.addWidget(self.detail_label)
        self.set_status("INFO", [], metrics=None)

    def clear(self, message: str | None = None) -> None:
        self.set_status("INFO", [], metrics=None)
        self.status_label.setText(message if message is not None else tr("reliability_awaiting"))

    def set_status(
        self,
        status: str,
        warnings: Sequence[str] | None = None,
        metrics: list[tuple[str, str]] | None = None,
    ) -> None:
        normalized = str(status or "INFO").strip().upper() or "INFO"
        palette = {
            "PASS": ("#EAF7EE", "#1E6B3A", "#C7E9D2"),
            "WARN": ("#FFF8E5", "#8A5A00", "#F1D49B"),
            "FAIL": ("#FDECEC", "#8F1D1D", "#E7B5B5"),
            "INFO": ("#EEF4FF", "#1D4F8F", "#C8D6F0"),
        }
        background, foreground, border = palette.get(normalized, palette["INFO"])
        self.setStyleSheet(
            f"#ReliabilityBanner {{ background: {background}; border: 1px solid {border}; border-radius: 6px; }}"
            f"#ReliabilityBanner QLabel {{ color: {foreground}; }}"
        )
        status_text = f"{tr('reliability')}: {normalized}"
        metric_parts = [
            f"{str(label).strip()}: {str(value).strip()}"
            for label, value in list(metrics or [])
            if str(label).strip() and str(value).strip()
        ]
        if metric_parts:
            status_text += " | " + " | ".join(metric_parts)
        self.status_label.setText(status_text)
        detail_lines = [str(message).strip() for message in list(warnings or []) if str(message).strip()]
        self.detail_label.setVisible(bool(detail_lines))
        self.detail_label.setText("\n".join(detail_lines[:3]))
