"""Display-only region picking; deliberately separate from analytical black balance."""
from copy import deepcopy

from PyQt6.QtCore import Qt

from core.plotting.fluoro_display import display_settings, sample_display_point
from core.utils.commands import SnapshotCommand
from core.utils.i18n import tr


class FluoroDisplayBalanceController:
    MODES = {'display_black_point': 'black', 'display_white_point': 'white'}

    def __init__(self, workspace):
        self.workspace = workspace
        self.pending = None
        workspace.canvas.view.display_point_cancelled.connect(self.cancel)

    def _controls(self, index):
        controls = getattr(self.workspace, '_display_processing_controls', [])
        return controls[index] if 0 <= index < len(controls) else None

    def cancel(self):
        self.pending = None
        if self.workspace.canvas.view.roi_mode in self.MODES:
            self.workspace.canvas.set_roi_mode('')
        for control in getattr(self.workspace, '_display_processing_controls', []):
            control.black_point_button.setChecked(False)
            control.white_point_button.setChecked(False)
            control.reset_hint()

    def begin(self, channel_index, point):
        self.cancel()
        ws = self.workspace
        image = ws._active_image()
        if point not in self.MODES.values() or not image or ws.canvas.current_step != 1:
            return
        if ws._busy_analysis_operations or ws.lock_display_checkbox.isChecked():
            return
        if not 0 <= channel_index < len(image.get('channels', [])):
            return
        self.pending = (ws.panel, str(image['uid']), channel_index, point)
        control = self._controls(channel_index)
        if control is not None:
            getattr(control, point + '_point_button').setChecked(True)
            control.hint.setText(tr('fluoro_display_pick_' + point))
        ws.canvas.set_roi_mode('display_' + point + '_point')
        ws.canvas.view.setFocus(Qt.FocusReason.OtherFocusReason)

    def consume(self, mode, geometry):
        if mode not in self.MODES:
            return False
        pending = self.pending
        self.cancel()
        ws = self.workspace
        image = ws._active_image()
        if pending is None or not image:
            return True
        panel, uid, index, point = pending
        if (ws.panel is not panel or str(image['uid']) != uid or ws.canvas.current_step != 1
                or self.MODES[mode] != point or ws.lock_display_checkbox.isChecked() or ws._busy_analysis_operations):
            return True
        control = self._controls(index)
        try:
            if geometry.get('source_channel_index', index) != index:
                raise ValueError('Pick the target channel, not another split image')
            data = panel.image_data(uid)
            settings = sample_display_point(data[index], geometry, image['channels'][index], point)
        except (ValueError, TypeError, IndexError):
            if control is not None:
                control.hint.setText(tr('fluoro_display_pick_invalid'))
            return True
        self.update_processing(index, settings)
        if control is not None:
            control.hint.setText(tr('fluoro_display_point_applied'))
        return True

    def update_processing(self, index, settings):
        ws = self.workspace
        image = ws._active_image()
        if not image or ws.lock_display_checkbox.isChecked():
            return
        before = {'uid': str(image['uid']), 'index': index,
                  'settings': deepcopy(image['channels'][index].get('display_processing'))}
        after = {**before, 'settings': display_settings(settings)}
        if before == after:
            return
        self._restore(after)
        ws._roi_clear_history.record(SnapshotCommand(tr('fluoro_display_optimization'), before, after, self._restore))

    def _restore(self, state):
        self.cancel()
        ws = self.workspace
        if ws.panel is None:
            return
        image = next((i for i in ws.panel.images() if str(i['uid']) == state['uid']), None)
        if image is None:
            return
        ws.panel.update_channel(state['uid'], state['index'], display_processing=state['settings'])
        if state['settings'] is None:
            image['channels'][state['index']].pop('display_processing', None)
        if ws._active_image() is image:
            control = self._controls(state['index'])
            if control is not None:
                control.set_settings(state['settings'])
            ws._schedule_channel_refresh()
