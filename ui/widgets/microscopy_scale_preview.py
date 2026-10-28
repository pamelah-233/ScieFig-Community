"""Histology previews reuse the native/export scale renderer, without baking pixels."""
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import QObject, QEvent, QRectF, Qt
from core.layout_scale_bar import apply_native_image_scale_overlay, resolve_native_layout_scale_bar, normalize_scale_bar_template


class ScalePreviewDrag(QObject):
    """Hit-test the rendered native scale; translate its existing template only."""
    def __init__(self, workspace, layout_key, label):
        super().__init__(label)
        self.workspace, self.layout_key, self.label = workspace, layout_key, label
        self.start = None
        label.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            return False
        ws, label = self.workspace, self.label
        if not getattr(ws.canvas, 'scale_editing_enabled', True) or label.line_selection_enabled:
            self.start = None
            return False
        image = ws.panel.active_image() if ws.panel else None
        if image is None:
            return False
        state = next((s for s in ws.panel.data.get(self.layout_key, {}).get('scale_bars', [])
                      if s.get('target_id') == image['uid'] and s.get('enabled', True)), None)
        if state is None:
            return False
        point = label._map_label_point_to_source(event.position().toPoint())
        if point is None:
            return False
        width, height = label._source_size.width(), label._source_size.height()
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            spec = resolve_native_layout_scale_bar(target_id=image['uid'], native_width_px=width, native_height_px=height,
                source_metadata=image.get('metadata', {}), measurement_scale=ws.panel.measurement_scale_bar(image['uid']), states=[state])
            if not QRectF(spec['x'], spec['y'], spec['width'], spec['height']).adjusted(-5,-5,5,5).contains(point.toPointF()):
                return False
            self.start = (image['uid'], point, normalize_scale_bar_template(state.get('template')))
            label.setFocus(Qt.FocusReason.MouseFocusReason)
        elif self.start is not None:
            uid, origin, template = self.start
            if uid != image['uid']:
                self.start = None
                return False
            dx, dy = point.x()-origin.x(), point.y()-origin.y()
            anchor = template['anchor']
            state['template'] = {**template,
                'offset_x_fraction':max(0, template['offset_x_fraction'] + dx/width * (-1 if anchor.endswith('right') else 1)),
                'offset_y_fraction':max(0, template['offset_y_fraction'] + dy/height * (-1 if anchor.startswith('bottom') else 1))}
            ws.canvas._refresh_image_display()
            if event.type() == QEvent.Type.MouseButtonRelease:
                self.start = None
        else:
            return False
        event.accept()
        return True


def paint_scale_preview(workspace, layout_key, display, source):
    panel = workspace.panel
    image = panel.active_image() if panel else None
    label = workspace.canvas.image_label
    if label is not None and not hasattr(label, 'scale_preview_drag'):
        label.scale_preview_drag = ScalePreviewDrag(workspace, layout_key, label)
    if not image or (label is not None and label.line_selection_enabled):
        return None
    layout = panel.data.get(layout_key, {})
    uid = str(image['uid'])
    states = layout.get('scale_bars', [])
    if not any(str(state.get('target_id')) == uid for state in states):
        return None
    painted = apply_native_image_scale_overlay(
        display.toImage(), target_id=uid,
        native_width_px=display.width(), native_height_px=display.height(),
        source_metadata=image.get('metadata', {}),
        measurement_scale=panel.measurement_scale_bar(uid),
        template=layout.get('scale_bar_template'), states=states,
    )
    return QPixmap.fromImage(painted)
