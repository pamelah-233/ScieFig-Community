"""Record preview scale gestures with the application's existing command history."""
from copy import deepcopy
from PyQt6.QtCore import QObject, QEvent, QTimer, Qt
from PyQt6.QtWidgets import QApplication, QWidget
from core.utils.commands import SnapshotCommand, UndoManager


class ScalePositionHistory(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.histories = {}
        self.pending = None
        QApplication.instance().installEventFilter(self)

    def target(self):
        w = self.window
        module = w.active_module
        if module == 'viewer':
            ws = w.image_browser_workspace
            path = ws._cached_path
            return (module, ws, str(path), ws) if path else None
        if module not in ('fluoro', 'ihc', 'brightfield') or w.workflow_ui._step(module) != 0:
            return None
        ws = getattr(w, module + '_workspace')
        panel = ws.panel
        image = panel.active_image() if panel else None
        return (module, panel, str(image['uid']), ws) if image else None

    @staticmethod
    def key(target):
        module, owner, uid, _ = target
        return module, id(owner), uid

    def capture(self, target):
        module, owner, uid, _ = target
        if module == 'viewer':
            bar = owner.scale_bar_state_for(uid)
            return dict(center_x_px=bar.center_x_px, line_y_px=bar.line_y_px)
        image = next((i for i in owner.images() if str(i['uid']) == uid), None)
        if image is None:
            return None
        states = (image.get('layout_scale_bars', []) if module == 'fluoro' else
                  [s for s in owner.data.get(module+'_layout', {}).get('scale_bars', []) if str(s.get('target_id')) == uid])
        positions = {str(s.get('target_id')): {key:deepcopy(s.get('template', {}).get(key))
                     for key in ('offset_x_fraction','offset_y_fraction')} for s in states}
        measurement = image.get('measurement_scale_bar', {})
        line = measurement.get('display_line_px', measurement.get('line_px'))
        return deepcopy(dict(positions=positions, line=line))

    def restore(self, target, state):
        module, owner, uid, ws = target
        if module == 'viewer':
            bar = owner.scale_bar_state_for(uid)
            for key, value in state.items():
                setattr(bar, key, value)
            if str(owner._cached_path) == uid:
                owner.canvas.set_scale_bar_state(bar)
            owner._notify_browser_state_changed()
            return
        image = next((i for i in owner.images() if str(i['uid']) == uid), None)
        if image is None:
            return
        if module == 'fluoro':
            states = image.get('layout_scale_bars', [])
        else:
            states = owner.data.get(module+'_layout', {}).get('scale_bars', [])
        for bar in states:
            offsets = state['positions'].get(str(bar.get('target_id')))
            if offsets is None:
                continue
            template = bar.setdefault('template', {})
            for key, value in offsets.items():
                if value is None:
                    template.pop(key, None)
                else:
                    template[key] = value
        if state['line'] is not None:
            image.setdefault('measurement_scale_bar', {})['display_line_px'] = deepcopy(state['line'])
        if ws.panel is owner and str((owner.active_image() or {}).get('uid')) == uid:
            if module == 'fluoro':
                ws._sync_ai_measurement_scale_controls()
            elif module == 'ihc':
                ws._sync_calibration_controls()
            else:
                self.window._sync_brightfield_measurement_scale_controls(owner.measurement_scale_bar(uid))

    def finish(self):
        pending, self.pending = self.pending, None
        if pending is None:
            return
        target, before = pending
        after = self.capture(target)
        if before is not None and after is not None and before != after:
            history = self.histories.setdefault(self.key(target), UndoManager())
            history.record(SnapshotCommand('Move scale bar', before, after,
                                          lambda state: self.restore(target, state)))

    def undo(self, redo=False):
        self.finish()
        target = self.target()
        if target is None:
            return False
        history = self.histories.get(self.key(target))
        return bool(history and (history.redo() if redo else history.undo()))

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            return False
        if event.button() != Qt.MouseButton.LeftButton or not isinstance(watched, QWidget):
            return False
        target = self.target()
        if target is None or not target[3].canvas.isAncestorOf(watched):
            return False
        if event.type() == QEvent.Type.MouseButtonPress:
            self.finish()
            self.pending = target, self.capture(target)
        else:
            QTimer.singleShot(0, self.finish)
        return False
