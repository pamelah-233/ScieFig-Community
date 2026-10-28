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
        get_current_mode: Callable[[], str],
        get_current_stain_type: Callable[[], str],
        get_expert_settings: Callable[[], dict[str, Any]],
        get_assisted_settings: Callable[[], dict[str, Any]],
        get_foolproof_settings: Callable[[], dict[str, Any]],
        refresh_measurement_views: Callable[..., None],
        notify_user: Callable[[str, str], None],
        report_failure: Callable[[str, str, Exception], None],
        run_async: Callable[..., bool] | None = None,
        apply_panel_snapshot: Callable[[Any, Any], None] | None = None,
        capture_analysis_inputs: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self._get_panel = get_panel
        self._cancel_preview_debounce = cancel_preview_debounce
        self._get_current_mode = get_current_mode
        self._get_current_stain_type = get_current_stain_type
        self._get_expert_settings = get_expert_settings
        self._get_assisted_settings = get_assisted_settings
        self._get_foolproof_settings = get_foolproof_settings
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
            mode = str(self._get_current_mode() or "").strip().lower()
            stain_type = normalize_brightfield_stain_type(self._get_current_stain_type() or "H&E")
            if stain_type != "H&E" and mode == "assisted":
                self._notify_user(
                    tr("v9_module_brightfield"),
                    tr("brightfield_assisted_non_he_unavailable"),
                )
                return
            settings = self._settings_for_mode(mode) if mode == "expert" or stain_type == "H&E" else None
            captured_inputs = self._invocation_inputs(panel, mode, stain_type, settings)
            panel_fingerprint = _panel_context_fingerprint(panel)
            snapshot_panel = _clone_panel(panel)
        except Exception as exc:
            self._report_failure(tr("brightfield_analysis_failed_title"), "Brightfield analysis failed", exc)
            return

        def work() -> tuple[Any, dict[str, Any], Any]:
            if mode == "expert":
                measurement = snapshot_panel.create_measurement(settings=settings)
            elif stain_type == "H&E":
                measurement = snapshot_panel.auto_analyze_he(settings=settings, guided_mode=mode)
            else:
                measurement = snapshot_panel.create_profile_preset_measurement(
                    stain_type=stain_type,
                    guided_mode=mode,
                )
            overlay = snapshot_panel.render_measurement_overlay(
                image_uid=str(measurement.get("image_uid", "")),
                measurement_uuid=str(measurement.get("measurement_uuid", "")),
            )
            return snapshot_panel, measurement, overlay

        def is_current() -> bool:
            try:
                current_settings = self._settings_for_mode(mode) if settings is not None else None
                return (
                    self._get_panel() is panel
                    and _panel_context_fingerprint(panel) == panel_fingerprint
                    and str(self._get_current_mode() or "").strip().lower() == mode
                    and normalize_brightfield_stain_type(self._get_current_stain_type() or "H&E") == stain_type
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
            mode = str(self._get_current_mode() or "").strip().lower()
            stain_type = normalize_brightfield_stain_type(self._get_current_stain_type() or "H&E")
            settings = self._settings_for_mode(mode) if mode == "expert" or stain_type == "H&E" else None
            captured_inputs = self._invocation_inputs(panel, mode, stain_type, settings)
            if mode == "expert":
                measurement = panel.create_measurement(settings=settings)
            else:
                if stain_type != "H&E" and mode == "assisted":
                    self._notify_user(
                        tr("v9_module_brightfield"),
                        tr("brightfield_assisted_non_he_unavailable"),
                    )
                    return
                if stain_type == "H&E":
                    measurement = panel.auto_analyze_he(
                        settings=settings,
                        guided_mode=mode,
                    )
                else:
                    measurement = panel.create_profile_preset_measurement(
                        stain_type=stain_type,
                        guided_mode=mode,
                    )
            if isinstance(measurement, dict):
                attach_analysis_inputs(measurement, captured_inputs)
            self._refresh_measurement_views(
                panel,
                measurement,
                status_message=tr("brightfield_analysis_complete_status"),
            )
        except Exception as exc:
            self._report_failure(tr("brightfield_analysis_failed_title"), "Brightfield analysis failed", exc)

    def _invocation_inputs(self, panel: Any, mode: str, stain_type: str, settings: dict[str, Any] | None) -> dict[str, Any]:
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
        payload_settings = {"mode": mode, "stain_type": stain_type}
        if settings:
            payload_settings.update(deepcopy(settings))
        return build_analysis_inputs(
            method="brightfield",
            settings=payload_settings,
            image_uid=image_uid,
            roi=roi if isinstance(roi, dict) else None,
            calibration=calibration if isinstance(calibration, dict) else None,
        )

    def _settings_for_mode(self, mode: str) -> dict[str, Any]:
        if mode == "expert":
            return deepcopy(self._get_expert_settings())
        if mode == "assisted":
            return deepcopy(self._get_assisted_settings())
        return deepcopy(self._get_foolproof_settings())
