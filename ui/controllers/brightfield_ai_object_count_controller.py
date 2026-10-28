from __future__ import annotations

from collections.abc import Callable
from typing import Any
from copy import deepcopy

from core.ai_pack.runtime import AIBackendUnavailableError, ModelNotCachedError
from core.utils.i18n import tr, trf
from core.measurement_freshness import analysis_job_fingerprint


class BrightfieldAIObjectCountController:
    def __init__(
        self,
        *,
        get_panel: Callable[[], Any],
        get_analysis_settings: Callable[[], dict[str, Any]],
        show_ai_warning: Callable[[str, str], None],
        refresh_after_ai_measurement: Callable[[dict[str, Any]], None],
        run_async: Callable[..., bool] | None = None,
        clone_panel: Callable[[Any], Any] | None = None,
        apply_panel_snapshot: Callable[[Any, Any], None] | None = None,
        set_busy: Callable[[bool], None] | None = None,
        can_start: Callable[[], bool] | None = None,
    ) -> None:
        self._get_panel = get_panel
        self._get_analysis_settings = get_analysis_settings
        self._show_ai_warning = show_ai_warning
        self._refresh_after_ai_measurement = refresh_after_ai_measurement
        self._run_async = run_async
        self._clone_panel = clone_panel
        self._apply_panel_snapshot = apply_panel_snapshot
        self._set_busy = set_busy or (lambda busy: None)
        self._can_start = can_start or (lambda: True)

    def run_object_count(self) -> None:
        if not self._can_start():
            return
        panel = self._get_panel()
        active_image = panel.active_image()
        if active_image is None:
            self._show_ai_warning(
                tr("v9_module_brightfield"),
                tr("import_he_before_fiber_area"),
            )
            return

        image_uid = str(active_image.get("uid", ""))
        roi = panel.active_roi(image_uid)
        if panel.he_fiber_area_calibration(image_uid) is None:
            self._show_ai_warning(
                tr("he_fiber_area"),
                tr("calibrate_he_before_measurement"),
            )
            return
        settings = self._get_analysis_settings()

        if self._run_async is not None:
            self._run_background(panel, image_uid, deepcopy(settings), deepcopy(roi))
            return

        try:
            measurement = panel.create_ai_object_count_measurement(
                image_uid=image_uid,
                settings=settings,
                roi=roi,
            )
        except AIBackendUnavailableError as exc:
            self._show_ai_warning(
                tr("he_fiber_unavailable_title"),
                str(exc),
            )
            return
        except ModelNotCachedError as exc:
            self._show_ai_warning(
                tr("ai_model_not_cached_title"),
                trf("ai_model_not_cached_message", error=exc),
            )
            return
        except FileNotFoundError as exc:
            self._show_ai_warning(tr("ai_pack_manifest_missing_title"), str(exc))
            return

        self._refresh_after_ai_measurement(measurement)

    def _run_background(self, panel, image_uid, settings, roi):
        try:
            snapshot = self._clone_panel(panel)
            fingerprint = analysis_job_fingerprint(panel)
        except Exception as exc:
            self._show_ai_warning(tr('he_fiber_area'), str(exc))
            return

        def work():
            return snapshot.create_ai_object_count_measurement(image_uid=image_uid, settings=settings, roi=roi)

        def current():
            return (self._get_panel() is panel
                and str((panel.active_image() or {}).get('uid', '')) == image_uid
                and analysis_job_fingerprint(panel) == fingerprint
                and self._get_analysis_settings() == settings)

        def success(measurement):
            try:
                self._apply_panel_snapshot(panel, snapshot)
                self._refresh_after_ai_measurement(measurement)
            finally:
                self._set_busy(False)

        def failure(exc):
            self._set_busy(False)
            self._show_ai_warning(tr('he_fiber_unavailable_title'), str(exc))

        self._set_busy(True)
        try:
            if not self._run_async(key='brightfield-fiber', work=work, on_success=success,
                on_error=failure, is_current=current, on_stale=lambda: self._set_busy(False)):
                self._set_busy(False)
        except Exception as exc:
            failure(exc)
