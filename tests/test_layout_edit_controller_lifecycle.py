from __future__ import annotations

import os

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView, QMainWindow, QVBoxLayout, QWidget

from ui.controllers.layout_edit_controller import LayoutEditController
from ui.widgets.canvas_object_interaction import CanvasObjectInteraction
from ui.widgets.canvas_zoom import CanvasZoomControls


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


class _Panel:
    panel_uuid = "lifecycle-panel"
    data: dict = {}


class _StubCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.zoom_controls = CanvasZoomControls(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.object_interaction=CanvasObjectInteraction(self.view,True)

    def _capture_layout_snapshot(self):
        return None

    def persist_layout_edits(self):
        pass


class _ProbeController(LayoutEditController):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.begun_targets: list[object] = []

    def _begin_interaction(self):
        self.begun_targets.append(self.canvas)
        super()._begin_interaction()


def _deliver_deferred_deletes() -> None:
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def _close(*widgets) -> None:
    for widget in widgets:
        widget.close()
        widget.deleteLater()
    _deliver_deferred_deletes()


def test_clear_target_then_deferred_delete_then_retarget_keeps_draw_tool_alive():
    window = QMainWindow()
    first = _StubCanvas()
    second = _StubCanvas()
    controller = LayoutEditController(window)
    controller.set_target("fluoro", _Panel(), first)
    first_viewport = first.view.viewport()
    first_tool = controller.draw
    assert first_tool is not None
    assert not sip.isdeleted(first_tool)

    controller.set_target()
    _deliver_deferred_deletes()
    assert sip.isdeleted(first_tool)
    assert controller.draw is None

    controller.set_target("fluoro", _Panel(), second)
    assert controller.draw is not None
    assert not sip.isdeleted(controller.draw)
    assert controller.draw is not first_tool
    controller.draw.cancel()

    QTest.mousePress(first_viewport, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    _APP.processEvents()
    controller.set_target()
    _close(first, second, window)


def test_shared_interaction_binding_is_detached_when_leaving_target():
    window = QMainWindow()
    first = _StubCanvas()
    second = _StubCanvas()
    controller = _ProbeController(window)
    controller.set_target("fluoro", _Panel(), first)
    old_interaction = first.view.object_interaction
    new_interaction = second.view.object_interaction

    controller.set_target()
    _deliver_deferred_deletes()
    controller.begun_targets.clear()
    old_interaction.gesture_started.emit()
    _APP.processEvents()
    assert first not in controller.begun_targets
    assert old_interaction.command_handler is None

    controller.set_target("fluoro", _Panel(), second)
    controller.begun_targets.clear()
    old_interaction.gesture_started.emit()
    new_interaction.gesture_started.emit()
    _APP.processEvents()
    assert first not in controller.begun_targets
    assert second in controller.begun_targets

    controller.set_target()
    _close(first, second, window)


class _UndoPanel:
    def __init__(self, uuid: str, texts: list[dict] | None = None) -> None:
        self.panel_uuid = uuid
        self.data = {
            "qpcr_layout": {"texts": list(texts or []), "shapes": [], "plots": [], "outputs": []},
            "active_image_uid": None,
        }


def test_queued_record_after_retarget_does_not_pollute_new_undo_history():
    window = QMainWindow()
    first = _StubCanvas()
    second = _StubCanvas()
    panel_a = _UndoPanel("panel-a", [{"text": "a"}])
    panel_b = _UndoPanel("panel-b")
    controller = LayoutEditController(window)
    controller.set_target("qpcr", panel_a, first)
    first.view.object_interaction.gesture_started.emit()
    before = controller._snapshot()
    panel_a.data["qpcr_layout"]["texts"] = [{"text": "changed"}]
    first.view.object_interaction.finish_timer.start(0)
    controller.set_target("qpcr", panel_b, second)
    _APP.processEvents()
    assert controller.undo_states == []
    assert controller.panel is panel_b

    same_before = controller._snapshot()
    second.view.object_interaction.gesture_started.emit()
    panel_b.data["qpcr_layout"]["texts"] = [{"text": "b"}]
    second.view.object_interaction.finish_timer.start(0)
    _APP.processEvents()
    assert controller.undo_states == [same_before]
    controller.set_target()
    _close(first, second, window)
