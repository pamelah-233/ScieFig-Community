"""Keep wheel input on sidebar scrolling, not on parameter editing."""
from PyQt6.QtCore import QEvent, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QAbstractSpinBox, QComboBox, QScrollArea, QWidget


def filter_panel_wheel(watched, event, panel: QWidget | None) -> bool:
    if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
        return False

    ancestors = []
    widget = watched
    while widget is not None:
        ancestors.append(widget)
        widget = widget.parentWidget()

    combo = next((widget for widget in ancestors if isinstance(widget, QComboBox)), None)
    if combo is not None and combo.view().isVisible():
        return False

    surface=next((widget for widget in ancestors if widget is panel or widget.property('parameterScrollSurface')),None)
    if surface is not None:
        # Use the containing form, not a table/list's own scroll area. Browser
        # thumbnails and settings remain independent sibling scrolling regions.
        containers = [widget for widget in ancestors[:ancestors.index(surface)]
                      if isinstance(widget, QScrollArea)]
        if containers:
            scroll = containers[-1]
            if watched in (scroll, scroll.viewport(), scroll.verticalScrollBar(), scroll.horizontalScrollBar()):
                return False
            target = scroll.viewport()
            forwarded = QWheelEvent(
                QPointF(target.mapFromGlobal(event.globalPosition().toPoint())),
                event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                device=event.pointingDevice(),
            )
            QApplication.sendEvent(target, forwarded)
            event.accept()
            return True

    # Retain the existing wheel-edit protection outside the sidebar too.
    if combo is not None or any(isinstance(widget, QAbstractSpinBox) for widget in ancestors):
        event.accept()
        return True
    return False
