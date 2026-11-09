from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from core.measurement_freshness import analysis_job_fingerprint, attach_analysis_inputs, build_analysis_inputs
from core.utils.i18n import tr


def _clone_panel(panel: Any) -> Any:
    from core.models.panel import panel_from_dict

    return panel_from_dict(deepcopy(panel.to_dict()))


def _panel_context_fingerprint(panel: Any) -> str:
    return analysis_job_fingerprint(panel)


class IHCAnalysisController:
    def __init__(
        self,
        *,
        get_panel: Callable[[], Any],
        get_overlay_opacity: Callable[[], float],
        get_examples_for_image: Callable[[str], list[dict[str, object]]],
        clear_examples_for_image: Callable[[str], None],
        clear_example_click_mode: Callable[[], None],
        refresh_measurement_views: Callable[..., None],
        notify_user: Callable[[str, str], None],
        report_failure: Callable[[str, str, Exception], None],
        run_async: Callable[..., bool] | None = None,
        apply_panel_snapshot: Callable[[Any, Any], None] | None = None,
        capture_analysis_inputs: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self._get_panel = get_panel
        self._get_overlay_opacity = get_overlay_opacity
        self._get_examples_for_image = get_examples_for_image
        self._clear_examples_for_image = clear_examples_for_image
        self._clear_example_click_mode = clear_example_click_mode
        self._refresh_measurement_views = refresh_measurement_views
        self._notify_user = notify_user
        self._report_failure = report_failure
        self._run_async = run_async
        self._apply_panel_snapshot = apply_panel_snapshot
        self._capture_analysis_inputs = capture_analysis_inputs

    def run_analysis(self) -> None:
        panel = self._get_panel()
        if panel.active_image() is None:
            self._notify_user(tr("histology_ihc"), tr("import_ihc_before_analysis"))
            return
        settings = panel.dab_auto_settings()
        self._run_measurement(
            panel,
            lambda snapshot: snapshot.auto_analyze_dab(settings=settings, guided_mode="foolproof"),
            status_key="ihc_analysis_complete_status",
            failure_key="ihc_analysis_failed_title",
            failure_detail="Histology / IHC analysis failed",
        )

    def _run_measurement(
        self,
        panel: Any,
        measure: Callable[[Any], dict[str, Any]],
        *,
        status_key: str,
        failure_key: str,
        failure_detail: str,
        samples_current: Callable[[], bool] | None = None,
    ) -> None:
        def fail(exc: Exception) -> None:
            self._report_failure(tr(failure_key), failure_detail, exc)

        def work(snapshot: Any) -> dict[str, Any]:
            guided = dict(snapshot.data.get("guided_state") or {})
            guided["overlay_opacity"] = overlay_opacity
            snapshot.data["guided_state"] = guided
            return measure(snapshot)

        def refresh(measurement: dict[str, Any]) -> None:
            if measurement.get("threshold_source") == "example_guided":
                # Calibration uses the existing measurement, which may precede a slider change.
                captured_inputs.setdefault("settings", {})["sensitivity"] = measurement["params"]["sensitivity"]
            attach_analysis_inputs(measurement, captured_inputs)
            self._refresh_measurement_views(panel, measurement, status_message=tr(status_key))

        try:
            overlay_opacity = float(self._get_overlay_opacity())
            captured_inputs = self._invocation_inputs(panel, panel.dab_auto_settings())
            if self._run_async is None or self._apply_panel_snapshot is None:
                refresh(work(panel))
                return
            fingerprint = _panel_context_fingerprint(panel)
            snapshot = _clone_panel(panel)

            def is_current() -> bool:
                return (
                    self._get_panel() is panel
                    and _panel_context_fingerprint(panel) == fingerprint
                    and (samples_current is None or samples_current())
                )

            def on_success(measurement: dict[str, Any]) -> None:
                live_opacity = float(self._get_overlay_opacity())
                self._apply_panel_snapshot(panel, snapshot)
                guided = dict(panel.data.get("guided_state") or {})
                guided["overlay_opacity"] = live_opacity
                panel.data["guided_state"] = guided
                refresh(measurement)

            self._run_async(
                key="ihc",
                work=lambda: work(snapshot),
                on_success=on_success,
                on_error=fail,
                is_current=is_current,
                on_stale=lambda: self._notify_user(tr("histology_ihc"), tr("ihc_analysis_stale")),
            )
        except Exception as exc:
            fail(exc)

    def _invocation_inputs(self, panel: Any, settings: dict[str, Any]) -> dict[str, Any]:
        if self._capture_analysis_inputs is not None:
            return deepcopy(self._capture_analysis_inputs() or {})
        image = panel.active_image() or {}
        image_uid = str(image.get("uid", "")).strip()
        roi = None
        calibration = None
        active_roi = getattr(panel, "active_roi", None)
        if callable(active_roi):
            roi = active_roi(image_uid) or image.get("roi")
        else:
            roi = image.get("roi")
        scale = getattr(panel, "measurement_scale_bar", None)
        if callable(scale) and image_uid:
            calibration = scale(image_uid)
        return build_analysis_inputs(
            method="ihc",
            settings={"mode": "foolproof", **settings},
            image_uid=image_uid,
            roi=roi if isinstance(roi, dict) else None,
            calibration=calibration if isinstance(calibration, dict) else None,
        )

    def recalibrate_from_examples(self) -> None:
        panel = self._get_panel()
        active_image = panel.active_image()
        if active_image is None:
            self._notify_user(
                tr("histology_ihc"),
                tr("ihc_import_analyze_before_recalibrate"),
            )
            return
        image_uid = str(active_image.get("uid", "")).strip()
        samples = deepcopy(self._get_examples_for_image(image_uid))
        if not samples:
            self._notify_user(tr("histology_ihc"), tr("ihc_add_example_before_recalibrate"))
            return
        self._run_measurement(
            panel,
            lambda snapshot: snapshot.recalibrate_dab_from_examples(image_uid=image_uid, examples=samples),
            status_key="ihc_recalibration_applied_status",
            failure_key="ihc_recalibration_failed_title",
            failure_detail="Histology / IHC threshold recalibration failed",
            samples_current=lambda: self._get_examples_for_image(image_uid) == samples,
        )

    def revert_to_auto(self) -> None:
        panel = self._get_panel()
        active_image = panel.active_image()
        if active_image is None:
            self._notify_user(
                tr("histology_ihc"),
                tr("ihc_import_analyze_before_revert"),
            )
            return
        try:
            image_uid = str(active_image.get("uid", "")).strip()
            measurement = panel.revert_dab_to_auto(image_uid=image_uid)
            self._clear_examples_for_image(image_uid)
            self._clear_example_click_mode()
            self._refresh_measurement_views(
                panel,
                measurement,
                status_message=tr("ihc_reverted_auto_status"),
            )
        except Exception as exc:
            self._report_failure(
                tr("ihc_revert_failed_title"),
                "Histology / IHC revert to auto failed",
                exc,
            )
