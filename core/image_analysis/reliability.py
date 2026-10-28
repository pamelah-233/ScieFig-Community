from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ReliabilityResult:
    status: str
    messages: list[str] = field(default_factory=list)
    include_in_statistics: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "messages": list(self.messages),
            "include_in_statistics": bool(self.include_in_statistics),
        }


def evaluate_analysis_reliability(
    results: dict[str, Any],
    qc_messages: list[str] | None = None,
    *,
    overlay_generated: bool = True,
    overlay_validation: dict[str, Any] | None = None,
    fail_messages: list[str] | None = None,
    warn_messages: list[str] | None = None,
) -> ReliabilityResult:
    del results
    messages = [str(message) for message in (qc_messages or []) if str(message).strip()]
    fail = False
    warn = bool(messages)

    overlay_payload = dict(overlay_validation or {})
    overlay_status = str(overlay_payload.get("status", "")).strip().upper()
    if not overlay_generated or overlay_status == "FAIL":
        fail = True
    if overlay_status == "WARN":
        warn = True
    for message in overlay_payload.get("messages", []):
        text = str(message).strip()
        if text:
            if text == "Overlay generation failed or has not been confirmed.":
                fail = True
            messages.append(text)

    if not overlay_generated and "Overlay generation failed or has not been confirmed." not in messages:
        fail = True
        messages.append("Overlay generation failed or has not been confirmed.")

    for message in fail_messages or []:
        text = str(message).strip()
        if text:
            fail = True
            messages.append(text)
    for message in warn_messages or []:
        text = str(message).strip()
        if text:
            warn = True
            messages.append(text)

    status = "FAIL" if fail else ("WARN" if warn else "PASS")
    return ReliabilityResult(status=status, messages=_dedupe(messages), include_in_statistics=status != "FAIL")


def evaluate_ihc_reliability(
    results: dict[str, Any],
    qc_messages: list[str] | None = None,
    *,
    overlay_generated: bool = True,
    threshold_source: str = "",
    example_guided_result: dict[str, Any] | None = None,
    overlay_validation: dict[str, Any] | None = None,
    quality_summary: dict[str, Any] | None = None,
    artifact_summary: dict[str, Any] | None = None,
) -> ReliabilityResult:
    tissue_area = int(results.get("tissue_area_px") or results.get("total_area_px") or 0)
    positive_percent = float(results.get("positive_area_percent", 0.0) or 0.0)
    example_payload = dict(example_guided_result or {})
    quality_payload = dict(quality_summary or {})
    artifact_payload = dict(artifact_summary or {})
    fail_messages: list[str] = []
    warn_messages: list[str] = []
    if tissue_area <= 0:
        fail_messages.append("Estimated tissue area is empty. This result should not be used for statistics.")
    if positive_percent >= 98.0:
        warn_messages.append("Positive mask covers almost the entire selected tissue area. Review staining and thresholds.")
    elif positive_percent <= 0.0:
        warn_messages.append("Positive mask is empty. Review the overlay and consider Assisted Mode.")
    if threshold_source == "image_override":
        warn_messages.append("A per-image threshold override was used; batch comparisons should be reviewed.")
    shift_fraction = example_payload.get("threshold_shift_fraction")
    if shift_fraction is not None and float(shift_fraction) > 0.40:
        warn_messages.append("Example-guided recalibration shifted the threshold substantially; confirm the overlay.")
    for warning in example_payload.get("warnings", []):
        warning_text = str(warning).strip()
        if warning_text:
            warn_messages.append(warning_text)
    for warning in quality_payload.get("warnings", []):
        warning_text = str(warning).strip()
        if warning_text:
            warn_messages.append(warning_text)
    artifact_fraction = float(artifact_payload.get("artifact_fraction", 0.0) or 0.0)
    if artifact_fraction > 0.20:
        warn_messages.append("A notable fraction of the selected tissue was excluded as artifact.")
    for warning in artifact_payload.get("warnings", []):
        warning_text = str(warning).strip()
        if warning_text:
            warn_messages.append(warning_text)

    return evaluate_analysis_reliability(
        results,
        qc_messages=qc_messages,
        overlay_generated=overlay_generated,
        overlay_validation=overlay_validation,
        fail_messages=fail_messages,
        warn_messages=warn_messages,
    )


def evaluate_brightfield_reliability(
    results: dict[str, Any],
    qc_messages: list[str] | None = None,
    *,
    overlay_generated: bool = True,
    threshold_source: str = "",
    overlay_validation: dict[str, Any] | None = None,
    quality_summary: dict[str, Any] | None = None,
    artifact_summary: dict[str, Any] | None = None,
) -> ReliabilityResult:
    roi_area = int(results.get("roi_area_px") or results.get("total_area_px") or 0)
    area_fraction_percent = float(results.get("area_fraction_percent", 0.0) or 0.0)
    object_count = int(results.get("object_count", 0) or 0)
    quality_payload = dict(quality_summary or {})
    artifact_payload = dict(artifact_summary or {})
    fail_messages: list[str] = []
    warn_messages: list[str] = []

    if roi_area <= 0:
        fail_messages.append("Estimated analysis area is empty. This result should not be used for statistics.")
    if object_count <= 0:
        warn_messages.append("No stained objects were detected. Review the overlay and segmentation settings.")
    if area_fraction_percent >= 98.0:
        warn_messages.append("Stained area covers almost the entire selected region. Review the segmentation settings.")
    elif area_fraction_percent <= 0.0:
        warn_messages.append("Stained area is empty. Review the overlay and segmentation settings.")
    if threshold_source == "image_override":
        warn_messages.append("A per-image threshold override was used; batch comparisons should be reviewed.")
    for warning in quality_payload.get("warnings", []):
        warning_text = str(warning).strip()
        if warning_text:
            warn_messages.append(warning_text)
    artifact_fraction = float(artifact_payload.get("artifact_fraction", 0.0) or 0.0)
    if artifact_fraction > 0.20:
        warn_messages.append("A notable fraction of the selected region was excluded as artifact.")
    for warning in artifact_payload.get("warnings", []):
        warning_text = str(warning).strip()
        if warning_text:
            warn_messages.append(warning_text)

    return evaluate_analysis_reliability(
        results,
        qc_messages=qc_messages,
        overlay_generated=overlay_generated,
        overlay_validation=overlay_validation,
        fail_messages=fail_messages,
        warn_messages=warn_messages,
    )


def _dedupe(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for message in messages:
        if message not in seen:
            seen.add(message)
            result.append(message)
    return result
