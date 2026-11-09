from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from core.measurement_freshness import analysis_job_fingerprint, attach_analysis_inputs, build_analysis_inputs
from core.models.brightfield_panel import normalize_brightfield_stain_type
from core.models.panel import panel_from_dict
from core.utils.i18n import tr


def _clone_panel(panel: Any) -> Any:
    snapshot = panel_from_dict(deepcopy(panel.to_dict()))
    if hasattr(panel, "_transient_preview_state"):
        snapshot._transient_preview_state = deepcopy(panel._transient_preview_state)
    return snapshot


def _panel_context_fingerprint(panel: Any) -> str:
    return analysis_job_fingerprint(panel)


class BrightfieldTraditionalAnalysisController:
    def __init__(
        self,
        *,
        get_panel: Callable[[], Any],
        cancel_preview_debounce: Callable[[], None],
        get_current_stain_type: Callable[[], str],
        get_settings: Callable[[], dict[str, Any]],
        refresh_measurement_views: Callable[..., None],
        notify_user: Callable[[str, str], None],
        report_failure: Callable[[str, str, Exception], None],
        run_async: Callable[..., bool] | None = None,
        apply_panel_snapshot: Callable[[Any, Any], None] | None = None,
        capture_analysis_inputs: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self._get_panel = get_panel
        self._cancel_preview_debounce = cancel_preview_debounce
        self._get_current_stain_type = get_current_stain_type
        self._get_settings = get_settings
        self._refresh_measurement_views = refresh_measurement_views
        self._notify_user = notify_user
        self._report_failure = report_failure
        self._run_async = run_async
        self._apply_panel_snapshot = apply_panel_snapshot
        self._capture_analysis_inputs = capture_analysis_inputs

    def run_analysis(self) -> None:
        panel = self._get_panel()
        active_image = panel.active_image()
        if active_image is None:
            self._notify_user(
                tr("v9_module_brightfield"),
                tr("import_brightfield_before_analysis"),
            )
            return
        self._cancel_preview_debounce()

        if self._run_async is None or self._apply_panel_snapshot is None:
            self._run_analysis_sync(panel)
            return

        try:
            stain_type = normalize_brightfield_stain_type(self._get_current_stain_type())
            settings = deepcopy(self._get_settings()) if stain_type == "H&E" else {}
            captured_inputs = self._invocation_inputs(panel, stain_type, settings)
            panel_fingerprint = _panel_context_fingerprint(panel)
            snapshot_panel = _clone_panel(panel)
        except Exception as exc:
            self._report_failure(tr("brightfield_analysis_failed_title"), "Brightfield analysis failed", exc)
            return

        def work() -> tuple[Any, dict[str, Any], Any]:
            measurement = self._measure(snapshot_panel, stain_type, settings)
            overlay = snapshot_panel.render_measurement_overlay(
                image_uid=str(measurement.get("image_uid", "")),
                measurement_uuid=str(measurement.get("measurement_uuid", "")),
            )
            return snapshot_panel, measurement, overlay

        def is_current() -> bool:
            try:
                current_settings = self._get_settings() if stain_type == "H&E" else {}
                return (
                    self._get_panel() is panel
                    and _panel_context_fingerprint(panel) == panel_fingerprint
                    and normalize_brightfield_stain_type(self._get_current_stain_type()) == stain_type
                    and current_settings == settings
                )
            except Exception:
                return False

        def on_success(result: tuple[Any, dict[str, Any], Any]) -> None:
            result_panel, measurement, overlay = result
            self._apply_panel_snapshot(panel, result_panel)
            if isinstance(measurement, dict):
                attach_analysis_inputs(measurement, captured_inputs)
            self._refresh_measurement_views(
                panel,
                measurement,
                status_message=tr("brightfield_analysis_complete_status"),
                preview_payload={"overlay": overlay},
            )

        def on_stale() -> None:
            self._notify_user(
                tr("v9_module_brightfield"),
                tr("brightfield_analysis_stale"),
            )

        try:
            self._run_async(
                key="brightfield",
                work=work,
                on_success=on_success,
                on_error=lambda exc: self._report_failure(
                    tr("brightfield_analysis_failed_title"), "Brightfield analysis failed", exc
                ),
                is_current=is_current,
                on_stale=on_stale,
            )
        except Exception as exc:
            self._report_failure(tr("brightfield_analysis_failed_title"), "Brightfield analysis failed", exc)

    def _run_analysis_sync(self, panel: Any) -> None:
        try:
            stain_type = normalize_brightfield_stain_type(self._get_current_stain_type())
            settings = deepcopy(self._get_settings()) if stain_type == "H&E" else {}
            captured_inputs = self._invocation_inputs(panel, stain_type, settings)
            measurement = self._measure(panel, stain_type, settings)
            if isinstance(measurement, dict):
                attach_analysis_inputs(measurement, captured_inputs)
            self._refresh_measurement_views(
                panel,
                measurement,
                status_message=tr("brightfield_analysis_complete_status"),
            )
        except Exception as exc:
            self._report_failure(tr("brightfield_analysis_failed_title"), "Brightfield analysis failed", exc)

    @staticmethod
    def _measure(panel: Any, stain_type: str, settings: dict[str, Any]) -> dict[str, Any]:
        if stain_type == "H&E":
            return panel.auto_analyze_he(settings=settings)
        return panel.create_profile_preset_measurement(stain_type=stain_type)

    def _invocation_inputs(self, panel: Any, stain_type: str, settings: dict[str, Any]) -> dict[str, Any]:
        if self._capture_analysis_inputs is not None:
            return deepcopy(self._capture_analysis_inputs() or {})
        image = panel.active_image() or {}
        image_uid = str(image.get("uid", "")).strip()
        roi = None
        active_roi = getattr(panel, "active_roi", None)
        if callable(active_roi):
            roi = active_roi(image_uid) or image.get("roi")
        else:
            roi = image.get("roi")
        calibration = panel.measurement_scale_bar(image_uid) if image_uid else None
        payload_settings = {"mode": "foolproof", "stain_type": stain_type, **settings}
        return build_analysis_inputs(
            method="brightfield",
            settings=payload_settings,
            image_uid=image_uid,
            roi=roi if isinstance(roi, dict) else None,
            calibration=calibration if isinstance(calibration, dict) else None,
        )
