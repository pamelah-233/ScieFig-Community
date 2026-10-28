"""Named analysis presets and per-image/method widget restore."""
from __future__ import annotations

from contextlib import contextmanager

from core.utils.analysis_presets import AnalysisPresets, sanitize_settings
from ui.widgets.analysis_preset_bar import AnalysisPresetBar

UI_STATE_KEY = "analysis_ui_state_by_image"
WINDOW_TO_PRESET_MODULE = {"ihc": "ihc", "brightfield": "brightfield", "fluoro": "fluorescence"}
PRESET_TO_WINDOW = {value: key for key, value in WINDOW_TO_PRESET_MODULE.items()}


def window_module_for(preset_module: str) -> str:
    return PRESET_TO_WINDOW.get(preset_module, preset_module)


class AnalysisStateController:
    def __init__(self, window, library=None):
        self.window = window
        self.library = library if library is not None else AnalysisPresets()
        self.bars = {}
        self._restoring = False
        self._install_bars()

    def _install_bars(self):
        window = self.window
        ihc_bar = AnalysisPresetBar(
            "ihc",
            library=self.library,
            on_apply=self.apply_preset,
            on_save=lambda name: self.save_current(name, "ihc"),
        )
        window.data_panel.ihc_measure_actions_group.layout().insertWidget(0, ihc_bar)
        bf_bar = AnalysisPresetBar(
            "brightfield",
            library=self.library,
            on_apply=self.apply_preset,
            on_save=lambda name: self.save_current(name, "brightfield"),
        )
        window.data_panel.brightfield_measure_group.layout().insertWidget(1, bf_bar)
        fluoro_bar = AnalysisPresetBar(
            "fluorescence",
            library=self.library,
            on_apply=self.apply_preset,
            on_save=lambda name: self.save_current(name, "fluorescence"),
        )
        page = window.fluoro_workspace.measure_params_stack.parentWidget()
        page.layout().insertWidget(1, fluoro_bar)
        window.fluoro_workspace._analysis_state = self
        self.bars = {"ihc": ihc_bar, "brightfield": bf_bar, "fluorescence": fluoro_bar}

    def retranslate(self):
        for bar in self.bars.values():
            bar.retranslate()

    @contextmanager
    def suspend_capture(self):
        previous = self._restoring
        self._restoring = True
        try:
            yield
        finally:
            self._restoring = previous

    def current_preset_module(self) -> str:
        return WINDOW_TO_PRESET_MODULE.get(self.window.active_module, "")

    def panel_for(self, preset_module: str):
        window = self.window
        if preset_module == "ihc":
            return window._active_ihc_panel()
        if preset_module == "brightfield":
            return window._active_brightfield_panel()
        if preset_module == "fluorescence":
            return window.fluoro_workspace.panel or window._active_fluoro_panel()
        return None

    def current_method(self, preset_module: str | None = None) -> str:
        module = preset_module or self.current_preset_module()
        controls = self.window.data_panel
        fluoro = self.window.fluoro_workspace
        if module == "ihc":
            return "measure_dab_positive_area" if controls.current_ihc_guided_mode() == "expert" else "auto_analyze_dab"
        if module == "fluorescence":
            return {
                "spot_count": "count_channel_objects",
                "ai_assisted": "run_ai_assisted_analysis",
                "roi_intensity": "roi_intensity",
            }.get(getattr(fluoro, "measure_mode", ""), "")
        if module != "brightfield":
            return ""
        kind = controls.brightfield_measure_type_combo.currentData()
        if kind == "cells":
            return "measure_bright_round_cells"
        if kind == "fiber":
            return "create_ai_object_count_measurement"
        mode = controls.current_brightfield_guided_mode()
        stain = str(controls.brightfield_stain_type_combo.currentText() or "H&E")
        if mode == "expert":
            return "create_measurement"
        if stain != "H&E":
            return "create_profile_preset_measurement"
        return "auto_analyze_he"

    def current_settings(self, preset_module: str | None = None) -> dict:
        module = preset_module or self.current_preset_module()
        controls = self.window.data_panel
        fluoro = self.window.fluoro_workspace
        if module == "ihc":
            mode = controls.current_ihc_guided_mode()
            if mode == "expert":
                payload = dict(controls.ihc_analysis_settings())
            elif mode == "assisted":
                payload = dict(controls.ihc_guided_analysis_settings())
            else:
                payload = dict(controls.ihc_foolproof_analysis_settings())
            payload["guided_mode"] = mode
            return sanitize_settings(payload)
        if module == "fluorescence":
            mode = getattr(fluoro, "measure_mode", "")
            if mode == "spot_count":
                return sanitize_settings(fluoro.count_analysis_settings())
            if mode == "ai_assisted":
                return sanitize_settings(fluoro.ai_analysis_settings())
            if mode == "roi_intensity":
                return sanitize_settings(fluoro.roi_analysis_settings())
            return {}
        if module != "brightfield":
            return {}
        kind = controls.brightfield_measure_type_combo.currentData()
        if kind == "cells":
            return sanitize_settings(controls.bright_round_cell_analysis_settings())
        if kind == "fiber":
            return sanitize_settings(controls.brightfield_analysis_settings())
        mode = controls.current_brightfield_guided_mode()
        if mode == "expert":
            payload = dict(controls.brightfield_analysis_settings())
        elif mode == "assisted":
            payload = dict(controls.brightfield_guided_analysis_settings())
        else:
            payload = dict(controls.brightfield_foolproof_analysis_settings())
        payload["guided_mode"] = mode
        payload["measure_type"] = "area"
        return sanitize_settings(payload)

    def capture_widget_state(self, preset_module: str) -> dict:
        controls = self.window.data_panel
        fluoro = self.window.fluoro_workspace
        if preset_module == "ihc":
            return {
                "active_method": self.current_method("ihc"),
                "guided_mode": controls.current_ihc_guided_mode(),
                "expert": sanitize_settings(controls.ihc_analysis_settings()),
                "assisted": sanitize_settings(controls.ihc_guided_analysis_settings()),
                "foolproof": sanitize_settings(controls.ihc_foolproof_analysis_settings()),
            }
        if preset_module == "brightfield":
            return {
                "active_method": self.current_method("brightfield"),
                "measure_type": str(controls.brightfield_measure_type_combo.currentData() or "cells"),
                "guided_mode": controls.current_brightfield_guided_mode(),
                "cells": sanitize_settings(controls.bright_round_cell_analysis_settings()),
                "fiber": sanitize_settings(controls.brightfield_analysis_settings()),
                "expert": sanitize_settings(controls.brightfield_analysis_settings()),
                "assisted": sanitize_settings(controls.brightfield_guided_analysis_settings()),
                "foolproof": sanitize_settings(controls.brightfield_foolproof_analysis_settings()),
                "independent": self.independent_settings('brightfield'),
            }
        if preset_module == "fluorescence":
            return {
                "active_method": self.current_method("fluorescence"),
                "measure_mode": str(getattr(fluoro, "measure_mode", "") or "roi_intensity"),
                "count": sanitize_settings(fluoro.count_analysis_settings()),
                "ai": sanitize_settings(fluoro.ai_analysis_settings()),
                "roi": sanitize_settings(fluoro.roi_analysis_settings()),
                "independent": self.independent_settings('fluoro'),
            }
        return {}

    def independent_settings(self,module):
        deck=getattr(self.window,'measurement_tasks',{}).get(module)
        result={}
        if deck:
            for task,controller in deck.controllers.items():
                controls=controller.controls
                state={'parameters':sanitize_settings(controls.parameters())}
                if hasattr(controls,'roi'):state['roi_scope']=controls.roi.currentData()
                if task=='central':state['peripheral_band']=controls.band.value()
                result[task]=state
        return result

    def apply_widget_state(self, preset_module: str, state: dict) -> None:
        if not isinstance(state, dict):
            return
        controls = self.window.data_panel
        fluoro = self.window.fluoro_workspace
        previous = self._restoring
        self._restoring = True
        window = self.window
        window._syncing_ihc_mode = True
        window._syncing_brightfield_mode = True
        try:
            if preset_module == "ihc":
                if state.get("expert"):
                    controls.set_ihc_analysis_settings(state["expert"])
                if state.get("assisted"):
                    controls.set_ihc_guided_analysis_settings(state["assisted"])
                mode = str(state.get("guided_mode") or ("expert" if state.get("active_method") == "measure_dab_positive_area" else "foolproof"))
                controls.set_ihc_guided_mode(mode)
            elif preset_module == "brightfield":
                if state.get("cells"):
                    controls.set_cell_analysis_settings(state["cells"])
                fiber = state.get("fiber") or state.get("expert")
                if fiber:
                    controls.set_brightfield_analysis_settings(fiber)
                if state.get("assisted"):
                    controls.set_brightfield_guided_analysis_settings(state["assisted"])
                if state.get("foolproof"):
                    controls.set_brightfield_foolproof_analysis_settings(state["foolproof"])
                measure_type = str(state.get("measure_type") or "")
                if measure_type:
                    index = controls.brightfield_measure_type_combo.findData(measure_type)
                    if index >= 0:
                        controls.brightfield_measure_type_combo.setCurrentIndex(index)
                if state.get("guided_mode"):
                    controls.set_brightfield_guided_mode(str(state["guided_mode"]))
            elif preset_module == "fluorescence":
                if state.get("count"):
                    fluoro.set_count_analysis_settings(state["count"])
                if state.get("ai"):
                    fluoro.set_ai_analysis_settings(state["ai"])
                if state.get("roi"):
                    fluoro.set_roi_analysis_settings(state["roi"])
                mode = str(state.get("measure_mode") or "")
                if mode:
                    fluoro._set_measure_mode(mode)
            deck=getattr(window,'measurement_tasks',{}).get(window_module_for(preset_module))
            if deck:
                for task,task_state in state.get('independent',{}).items():
                    controller=deck.controllers.get(task)
                    if controller is None:continue
                    controls=controller.controls
                    params={key:value for key,value in task_state.get('parameters',{}).items() if value is not None}
                    setter=getattr(controls,'set_parameters',None) or controls.restore_parameters
                    setter(params)
                    if 'roi_scope' in task_state:
                        controls.roi.setCurrentIndex(controls.roi.findData(task_state['roi_scope']))
                    if task=='central' and 'peripheral_band' in task_state:
                        controls.band.setValue(task_state['peripheral_band'])
        finally:
            window._syncing_ihc_mode = False
            window._syncing_brightfield_mode = False
            self._restoring = previous

    def capture_outgoing(self, preset_module: str) -> None:
        if self._restoring:
            return
        panel = self.panel_for(preset_module)
        if panel is None:
            return
        uid = str(panel.data.get("active_image_uid") or "").strip()
        if not uid:
            return
        store = dict(panel.data.get(UI_STATE_KEY) or {})
        store[uid] = self.capture_widget_state(preset_module)
        panel.data[UI_STATE_KEY] = store

    def restore_incoming(self, preset_module: str) -> None:
        panel = self.panel_for(preset_module)
        if panel is None:
            self.reload_bar(preset_module)
            return
        uid = str(panel.data.get("active_image_uid") or "").strip()
        state = dict(panel.data.get(UI_STATE_KEY) or {}).get(uid)
        if state:
            self.apply_widget_state(preset_module, state)
        self.reload_bar(preset_module)

    def reload_bar(self, preset_module: str | None = None) -> None:
        module = preset_module or self.current_preset_module()
        bar = self.bars.get(module)
        if bar is None:
            return
        bar.set_method(self.current_method(module))

    def apply_preset(self, record: dict) -> None:
        module = str(record.get("module") or "")
        method = str(record.get("method") or "")
        if module != self.current_preset_module() or method != self.current_method(module):
            raise ValueError("analysis_preset_incompatible")
        if not self.library.compatible(record, module, method):
            raise ValueError("analysis_preset_incompatible")
        settings = sanitize_settings(dict(record.get("settings") or {}))
        self._apply_settings(module, method, settings)
        if hasattr(self.window, "_refresh_result_freshness_status"):
            self.window._refresh_result_freshness_status()

    def _apply_settings(self, module: str, method: str, settings: dict) -> None:
        controls = self.window.data_panel
        fluoro = self.window.fluoro_workspace
        previous = self._restoring
        self._restoring = True
        self.window._syncing_ihc_mode = True
        self.window._syncing_brightfield_mode = True
        try:
            if module == "ihc":
                mode = str(settings.get("guided_mode") or ("expert" if method == "measure_dab_positive_area" else "foolproof"))
                controls.set_ihc_guided_mode(mode)
                if mode == "expert":
                    controls.set_ihc_analysis_settings(settings)
                elif mode == "assisted":
                    controls.set_ihc_guided_analysis_settings(settings)
            elif module == "brightfield":
                if method == "measure_bright_round_cells":
                    index = controls.brightfield_measure_type_combo.findData("cells")
                    if index >= 0:
                        controls.brightfield_measure_type_combo.setCurrentIndex(index)
                    controls.set_cell_analysis_settings(settings)
                elif method == "create_ai_object_count_measurement":
                    index = controls.brightfield_measure_type_combo.findData("fiber")
                    if index >= 0:
                        controls.brightfield_measure_type_combo.setCurrentIndex(index)
                    controls.set_brightfield_analysis_settings(settings)
                else:
                    index = controls.brightfield_measure_type_combo.findData("area")
                    if index >= 0:
                        controls.brightfield_measure_type_combo.setCurrentIndex(index)
                    mode = str(settings.get("guided_mode") or ("expert" if method == "create_measurement" else "foolproof"))
                    controls.set_brightfield_guided_mode(mode)
                    if mode == "expert":
                        controls.set_brightfield_analysis_settings(settings)
                    elif mode == "assisted":
                        controls.set_brightfield_guided_analysis_settings(settings)
                    else:
                        controls.set_brightfield_foolproof_analysis_settings(settings)
            elif module == "fluorescence":
                if method == "count_channel_objects":
                    fluoro._set_measure_mode("spot_count")
                    fluoro.set_count_analysis_settings(settings)
                elif method == "run_ai_assisted_analysis":
                    fluoro._set_measure_mode("ai_assisted")
                    fluoro.set_ai_analysis_settings(settings)
                elif method == "roi_intensity":
                    fluoro._set_measure_mode("roi_intensity")
                    fluoro.set_roi_analysis_settings(settings)
        finally:
            self.window._syncing_ihc_mode = False
            self.window._syncing_brightfield_mode = False
            self._restoring = previous

    def save_current(self, name: str, preset_module: str):
        method = self.current_method(preset_module)
        if not method:
            raise ValueError("analysis_preset_incompatible")
        return self.library.save(name, preset_module, method, self.current_settings(preset_module))

    def capture_session(self) -> None:
        window = self.window
        module = self.current_preset_module()
        if module:
            self.capture_outgoing(module)
        selected = {
            "ihc": window.data_panel.selected_ihc_measurement_uuid(),
            "brightfield": window.data_panel.selected_brightfield_measurement_uuid()
            or window.data_panel.selected_brightfield_ai_measurement_uuid(),
            "fluoro": {},
        }
        identity = window.fluoro_workspace.selected_result_identity()
        if identity:
            selected["fluoro"] = {"kind": identity[0], "uuid": identity[1]}
        window.project.session_ui_state = {
            "active_module": window.active_module,
            "preview_mode_by_module": {
                "ihc": window.ihc_workspace.current_preview_mode(),
                "brightfield": window.brightfield_workspace.current_preview_mode(),
                "fluoro": "overlay"
                if getattr(window.fluoro_workspace, "ai_overlay_preview_button", None)
                and window.fluoro_workspace.ai_overlay_preview_button.isChecked()
                else "original",
            },
            "selected_record_by_module": selected,
            "fluoro_step": int(window.fluoro_workspace.context_stack.currentIndex())
            if hasattr(window.fluoro_workspace, "context_stack")
            else 0,
        }

    def _restore_fluoro_selection(self, selected) -> None:
        kind = ""
        uuid = ""
        if isinstance(selected, dict):
            kind = str(selected.get("kind") or "")
            uuid = str(selected.get("uuid") or "")
        else:
            uuid = str(selected or "")
        if not uuid:
            return
        select = getattr(self.window.fluoro_workspace, "select_result_identity", None)
        if callable(select):
            select(kind, uuid)

    def restore_session(self) -> None:
        window = self.window
        state = dict(getattr(window.project, "session_ui_state", None) or {})
        module = str(state.get("active_module") or window.active_module)
        with self.suspend_capture():
            window.set_module(module)
            preset_module = WINDOW_TO_PRESET_MODULE.get(module, "")
            if module == "fluoro":
                step = int(state.get("fluoro_step", window._active_fluoro_panel().data.get("active_step", 0)) or 0)
                window.fluoro_workspace.set_step(step)
            selected = dict(state.get("selected_record_by_module") or {})
            if module == "ihc" and selected.get("ihc"):
                window.data_panel.select_ihc_measurement(str(selected["ihc"]))
            if module == "brightfield" and selected.get("brightfield"):
                uuid = str(selected["brightfield"])
                if window.data_panel.select_brightfield_measurement(uuid) is not True:
                    select_ai = getattr(window.data_panel, "select_brightfield_ai_measurement", None)
                    if callable(select_ai):
                        select_ai(uuid)
            if module == "fluoro":
                self._restore_fluoro_selection(selected.get("fluoro"))
            if preset_module:
                self.restore_incoming(preset_module)
            preview = dict(state.get("preview_mode_by_module") or {})
            if preview.get("ihc") == "overlay":
                window.ihc_workspace.show_overlay_preview()
            elif preview.get("ihc") == "original":
                window.ihc_workspace.show_original_preview()
            if preview.get("brightfield") == "overlay":
                window.brightfield_workspace.show_overlay_preview()
            elif preview.get("brightfield") == "original":
                window.brightfield_workspace.show_original_preview()
            fluoro = window.fluoro_workspace
            if module == "fluoro":
                if preview.get("fluoro") == "overlay" and hasattr(fluoro, "_set_ai_overlay_visible"):
                    fluoro._set_ai_overlay_visible(True)
                elif preview.get("fluoro") == "original" and hasattr(fluoro, "_set_ai_overlay_visible"):
                    fluoro._set_ai_overlay_visible(False)
            measurement_preview = getattr(window, 'measurement_preview', None)
            if measurement_preview is not None:
                measurement_preview.restore_preview_mode(module, preview.get(module))
            self.reload_bar(preset_module)
