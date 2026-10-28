from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExportValidationResult:
    ok: bool
    messages: list[str]


SUPPORTED_FORMATS = {".pdf", ".svg", ".tiff", ".tif", ".png"}


def validate_export_path(path: str | Path) -> ExportValidationResult:
    target = Path(path)
    messages: list[str] = []
    if target.suffix.lower() not in SUPPORTED_FORMATS:
        messages.append(f"Unsupported export format: {target.suffix}")
    if not target.parent.exists():
        messages.append(f"Export directory does not exist: {target.parent}")
    return ExportValidationResult(ok=not messages, messages=messages)

