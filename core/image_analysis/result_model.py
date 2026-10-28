from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AnalysisResult:
    mask: Any | None = None
    label_mask: Any | None = None
    measurement_table: Any | None = None
    results: dict[str, Any] = field(default_factory=dict)
    overlay_layers: dict[str, Any] = field(default_factory=dict)
    reliability_status: str = ""
    warnings: list[str] = field(default_factory=list)
    include_in_statistics: bool = True
    backend_name: str = ""
    backend_version: str = ""
    model_name: str = ""
    model_hash: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    user_overrides: dict[str, Any] = field(default_factory=dict)
    processing_time_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mask": self.mask,
            "label_mask": self.label_mask,
            "measurement_table": self.measurement_table,
            "results": dict(self.results),
            "overlay_layers": dict(self.overlay_layers),
            "reliability_status": self.reliability_status,
            "warnings": list(self.warnings),
            "include_in_statistics": bool(self.include_in_statistics),
            "backend_name": self.backend_name,
            "backend_version": self.backend_version,
            "model_name": self.model_name,
            "model_hash": self.model_hash,
            "parameters": dict(self.parameters),
            "user_overrides": dict(self.user_overrides),
            "processing_time_ms": int(self.processing_time_ms),
            "metadata": dict(self.metadata),
        }
