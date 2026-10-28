from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from core.measurement_freshness import analysis_job_fingerprint, attach_analysis_inputs, build_analysis_inputs
from core.utils.i18n import tr, trf
from ui.controllers.ihc_mode import normalize_ihc_mode


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
        get_expert_settings: Callable[[], dict[str, Any]],
        get_assisted_settings: Callable[[], dict[str, Any]],
        get_foolproof_settings: Callable[[], dict[str, Any]],
        get_current_mode: Callable[[], str],
        get_overlay_opacity: Callable[[], float],
        get_current_task_type: Callable[[], str],
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
        self._get_expert_settings = get_expert_settings
        self._get_assisted_settings = get_assisted_settings
        self._get_foolproof_settings = get_foolproof_settings
        self._get_current_mode = get_current_mode
        self._get_overlay_opacity = get_overlay_opacity
        self._get_current_task_type = get_current_task_type
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
        task_type = str(self._get_current_task_type() or "").strip()
        if task_type != "ihc_dab":
            self._notify_user(
                tr("histology_ihc"),
                trf("ihc_task_not_implemented", task=task_type),
            )
            return
        if self._run_async is None or self._apply_panel_snapshot is None:
            self._run_analysis_sync(panel)
            return

        try:
            mode = normalize_ihc_mode(self._get_current_mode())
            settings = self._settings_for_mode(mode)
            overlay_opacity = float(self._get_overlay_opacity())
            captured_inputs = self._invocation_inputs(panel, mode, settings)
            panel_fingerprint = _panel_context_fingerprint(panel)
            snapshot_panel = _clone_panel(panel)
        except Exception as exc:
            self._report_failure(tr("ihc_analysis_failed_title"), "Histology / IHC analysis failed", exc)
            return

        def work() -> tuple[Any, dict[str, Any]]:
            guided_state = dict(snapshot_panel.data.get("guided_state", {}))
            guided_state["overlay_opacity"] = overlay_opacity
            snapshot_panel.data["guided_state"] = guided_state
            if mode == "expert":
                measurement = snapshot_panel.measure_dab_positive_area(settings=settings)
            elif mode == "assisted":
                measurement = snapshot_panel.auto_analyze_dab(settings=settings, guided_mode=mode)
            else:
                measurement = snapshot_panel.auto_analyze_dab(settings=settings, guided_mode="foolproof")
                snapshot_panel.append_audit("foolproof_auto_analyze", str(snapshot_panel.active_image().get("uid", "")))
            return snapshot_panel, measurement

        def is_current() -> bool:
            try:
                return (
                    self._get_panel() is panel
                    and _panel_context_fingerprint(panel) == panel_fingerprint
                    and normalize_ihc_mode(self._get_current_mode()) == mode
                    and str(self._get_current_task_type() or "").strip() == "ihc_dab"
                    and self._settings_for_mode(mode) == settings
                )
            except Exception:
                return False

        def on_success(result: tuple[Any, dict[str, Any]]) -> None:
            result_panel, measurement = result
            live_opacity = float(self._get_overlay_opacity())
            self._apply_panel_snapshot(panel, result_panel)
            guided = dict(panel.data.get("guided_state") or {})
            guided["overlay_opacity"] = live_opacity
            panel.data["guided_state"] = guided
            if isinstance(measurement, dict):
                attach_analysis_inputs(measurement, captured_inputs)
            self._refresh_measurement_views(
                panel,
                measurement,
                status_message=tr("ihc_analysis_complete_status"),
            )

        def on_stale() -> None:
            self._notify_user(
                tr("histology_ihc"),
                tr("ihc_analysis_stale"),
            )

        try:
            self._run_async(
                key="ihc",
                work=work,
                on_success=on_success,
                on_error=lambda exc: self._report_failure(
                    tr("ihc_analysis_failed_title"), "Histology / IHC analysis failed", exc
                ),
                is_current=is_current,
                on_stale=on_stale,
            )
        except Exception as exc:
            self._report_failure(tr("ihc_analysis_failed_title"), "Histology / IHC analysis failed", exc)

    def _run_analysis_sync(self, panel: Any) -> None:
        try:
            guided_state = dict(panel.data.get("guided_state", {}))
            guided_state["overlay_opacity"] = self._get_overlay_opacity()
            panel.data["guided_state"] = guided_state
            mode = normalize_ihc_mode(self._get_current_mode())
            settings = self._settings_for_mode(mode)
            captured_inputs = self._invocation_inputs(panel, mode, settings)
            if mode == "expert":
                measurement = panel.measure_dab_positive_area(settings=settings)
            elif mode == "assisted":
                measurement = panel.auto_analyze_dab(
                    settings=settings,
                    guided_mode=mode,
                )
            else:
                measurement = panel.auto_analyze_dab(
                    settings=settings,
                    guided_mode="foolproof",
                )
                panel.append_audit("foolproof_auto_analyze", str(panel.active_image().get("uid", "")))
            if isinstance(measurement, dict):
                attach_analysis_inputs(measurement, captured_inputs)
            self._refresh_measurement_views(
                panel,
                measurement,
                status_message=tr("ihc_analysis_complete_status"),
            )
        except Exception as exc:
            self._report_failure(tr("ihc_analysis_failed_title"), "Histology / IHC analysis failed", exc)

    def _invocation_inputs(self, panel: Any, mode: str, settings: dict[str, Any]) -> dict[str, Any]:
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
            settings={"mode": mode, **deepcopy(settings)},
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
        samples = self._get_examples_for_image(image_uid)
        if not samples:
            self._notify_user(
                tr("histology_ihc"),
                tr("ihc_add_example_before_recalibrate"),
            )
            return
        try:
            measurement = panel.recalibrate_dab_from_examples(image_uid=image_uid, examples=list(samples))
            self._refresh_measurement_views(
                panel,
                measurement,
                status_message=tr("ihc_recalibration_applied_status"),
            )
        except Exception as exc:
            self._report_failure(
                tr("ihc_recalibration_failed_title"),
                "Histology / IHC assisted recalibration failed",
                exc,
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
