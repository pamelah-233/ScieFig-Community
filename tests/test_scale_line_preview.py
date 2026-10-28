from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsScene

from core.utils.i18n import get_language, set_language, tr
from ui.widgets.fluoro_canvas import FluoroGraphicsView
from ui.widgets.preview_canvas import SelectableImageLabel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _mouse_event(
    event_type: QEvent.Type,
    point: QPoint,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )


def _make_label(app: QApplication) -> SelectableImageLabel:
    source = QPixmap(200, 100)
    source.fill(QColor("#6688AA"))
    label = SelectableImageLabel()
    label.resize(200, 100)
    label.set_source_pixmap(source, QPixmap(source))
    label.line_selection_enabled = True
    label.show()
    app.processEvents()
    return label


def test_selectable_label_scale_line_move_shows_preview_without_commit() -> None:
    app = _app()
    label = _make_label(app)
    selected: list[dict] = []
    dragged: list[dict] = []
    label.line_selected.connect(selected.append)
    label.line_dragged.connect(dragged.append)
    pixmap_before = label.pixmap().toImage().copy()
    grab_before = label.grab().toImage().copy()

    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPoint(20, 40)))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, QPoint(160, 80)))
    app.processEvents()

    assert selected == []
    assert dragged == []
    assert label._draft_source_line
    assert label.pixmap().toImage() == pixmap_before
    grab_after = label.grab().toImage()
    assert grab_after != grab_before
    mid = QPoint(90, 40)
    assert grab_after.pixelColor(mid) != QColor("#6688AA")


def test_selectable_label_scale_line_defaults_horizontal_shift_free_and_matches_release() -> None:
    app = _app()
    label = _make_label(app)
    selected: list[dict] = []
    label.line_selected.connect(selected.append)

    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPoint(20, 40)))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, QPoint(160, 80)))
    preview = dict(label._draft_source_line)
    assert preview["y1"] == preview["y2"]
    assert preview["y1"] == 40.0
    assert preview["x2"] != preview["x1"]

    label.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, QPoint(160, 80)))
    app.processEvents()
    assert selected == [preview]
    assert not label._draft_source_line

    selected.clear()
    label.mousePressEvent(
        _mouse_event(QEvent.Type.MouseButtonPress, QPoint(20, 40), Qt.KeyboardModifier.ShiftModifier)
    )
    label.mouseMoveEvent(
        _mouse_event(QEvent.Type.MouseMove, QPoint(160, 80), Qt.KeyboardModifier.ShiftModifier)
    )
    free_preview = dict(label._draft_source_line)
    assert free_preview["y2"] == 80.0
    assert free_preview["y2"] != free_preview["y1"]
    label.mouseReleaseEvent(
        _mouse_event(QEvent.Type.MouseButtonRelease, QPoint(160, 80), Qt.KeyboardModifier.ShiftModifier)
    )
    app.processEvents()
    assert selected == [free_preview]


def test_selectable_label_scale_line_escape_mode_switch_zero_length() -> None:
    app = _app()
    label = _make_label(app)
    selected: list[dict] = []
    label.line_selected.connect(selected.append)

    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPoint(20, 40)))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, QPoint(160, 80)))
    assert label._draft_source_line
    label.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    assert not label._draft_source_line
    assert label._line_press_pos is None
    assert selected == []
    assert label.grab().toImage().pixelColor(QPoint(90, 40)) == QColor("#6688AA")

    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPoint(20, 40)))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, QPoint(160, 80)))
    label.set_line_selection_enabled(False)
    assert not label._draft_source_line
    assert selected == []

    label.set_line_selection_enabled(True)
    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPoint(40, 40)))
    label.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, QPoint(40, 40)))
    app.processEvents()
    assert selected == []


def test_selectable_label_roi_and_committed_line_drag_unaffected() -> None:
    app = _app()
    label = _make_label(app)
    label.line_selection_enabled = False
    label.selection_enabled = True
    rois: list[QRect] = []
    dragged: list[dict] = []
    selected: list[dict] = []
    label.roi_selected.connect(rois.append)
    label.line_dragged.connect(dragged.append)
    label.line_selected.connect(selected.append)

    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPoint(10, 10)))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, QPoint(80, 70)))
    assert not label._draft_source_line
    label.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, QPoint(80, 70)))
    app.processEvents()
    assert rois
    assert rois[0].width() > 1 and rois[0].height() > 1

    label.selection_enabled = False
    label.set_source_line({"x1": 20.0, "y1": 40.0, "x2": 160.0, "y2": 40.0})
    label.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, QPoint(20, 40)))
    label.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, QPoint(20, 70)))
    app.processEvents()
    assert dragged
    assert dragged[-1]["y1"] == 70.0
    assert dragged[-1]["y2"] == 40.0
    label.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, QPoint(20, 70)))
    app.processEvents()
    assert selected
    assert selected[-1]["y1"] == 70.0


def _make_fluoro_view(app: QApplication) -> FluoroGraphicsView:
    scene = QGraphicsScene()
    pixmap = QPixmap(200, 100)
    pixmap.fill(QColor("#102030"))
    scene.addPixmap(pixmap)
    view = FluoroGraphicsView(scene)
    view.resize(240, 140)
    view.show()
    app.processEvents()
    return view


def _scene_to_view(view: FluoroGraphicsView, x: float, y: float) -> QPoint:
    return view.mapFromScene(QPointF(x, y))


def test_fluoro_scale_line_horizontal_shift_free_matches_release() -> None:
    app = _app()
    view = _make_fluoro_view(app)
    emitted: list[tuple[str, dict]] = []
    view.scene_roi_drawn.connect(lambda mode, payload: emitted.append((mode, dict(payload))))
    view.set_roi_mode("scale_line")
    start = _scene_to_view(view, 20, 40)
    end = _scene_to_view(view, 160, 80)

    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end))
    app.processEvents()
    assert emitted == []
    assert view._temp_roi_item is not None
    preview = view._temp_roi_item.line()
    assert abs(preview.y1() - preview.y2()) < 0.01
    assert abs(preview.y1() - 40.0) < 1.0
    assert view._temp_roi_item.pen().isCosmetic()

    view.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, end))
    app.processEvents()
    assert len(emitted) == 1
    assert emitted[0][0] == "scale_line"
    payload = emitted[0][1]
    assert payload["y1"] == payload["y2"]
    assert abs(payload["x1"] - preview.x1()) < 0.01
    assert abs(payload["y1"] - preview.y1()) < 0.01
    assert abs(payload["x2"] - preview.x2()) < 0.01
    assert abs(payload["y2"] - preview.y2()) < 0.01
    assert view._temp_roi_item is None

    emitted.clear()
    view.set_roi_mode("scale_line")
    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start, Qt.KeyboardModifier.ShiftModifier))
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end, Qt.KeyboardModifier.ShiftModifier))
    free_preview = view._temp_roi_item.line()
    assert abs(free_preview.y2() - 80.0) < 1.0
    assert abs(free_preview.y2() - free_preview.y1()) > 1.0
    view.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, end, Qt.KeyboardModifier.ShiftModifier))
    app.processEvents()
    assert emitted[0][1]["y2"] != emitted[0][1]["y1"]
    assert abs(emitted[0][1]["y2"] - free_preview.y2()) < 0.01


def test_fluoro_profile_line_keeps_arbitrary_angle() -> None:
    app = _app()
    view = _make_fluoro_view(app)
    emitted: list[tuple[str, dict]] = []
    view.scene_roi_drawn.connect(lambda mode, payload: emitted.append((mode, dict(payload))))
    view.set_roi_mode("profile_line")
    start = _scene_to_view(view, 20, 40)
    end = _scene_to_view(view, 160, 80)
    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end))
    preview = view._temp_roi_item.line()
    assert abs(preview.y2() - preview.y1()) > 1.0
    view.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, end))
    app.processEvents()
    assert emitted[0][0] == "profile_line"
    assert emitted[0][1]["y2"] != emitted[0][1]["y1"]


def test_fluoro_scale_line_escape_mode_switch_zero_length_roi_unaffected() -> None:
    app = _app()
    view = _make_fluoro_view(app)
    emitted: list[tuple[str, object]] = []
    view.scene_roi_drawn.connect(lambda mode, payload: emitted.append((mode, payload)))
    view.set_roi_mode("scale_line")
    start = _scene_to_view(view, 20, 40)
    end = _scene_to_view(view, 160, 80)
    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end))
    assert view._temp_roi_item is not None
    view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    assert view._temp_roi_item is None
    assert emitted == []

    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end))
    view.set_roi_mode("")
    assert view._temp_roi_item is None
    assert emitted == []

    view.set_roi_mode("scale_line")
    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    view.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, start))
    app.processEvents()
    assert emitted == []
    assert view._temp_roi_item is None

    view.set_roi_mode("rectangle")
    corner = _scene_to_view(view, 120, 70)
    view.mousePressEvent(_mouse_event(QEvent.Type.MouseButtonPress, start))
    view.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, corner))
    view.mouseReleaseEvent(_mouse_event(QEvent.Type.MouseButtonRelease, corner))
    app.processEvents()
    assert emitted
    assert emitted[-1][0] == "rectangle"


def test_scale_line_hints_mention_horizontal_and_shift() -> None:
    previous = get_language()
    try:
        set_language("en")
        hint = tr("known_scale_line_hint").lower()
        tooltip = tr("draw_he_scale_tooltip").lower()
        assert "shift" in hint and "horizontal" in hint
        assert "shift" in tooltip and "horizontal" in tooltip
        set_language("zh")
        assert "Shift" in tr("known_scale_line_hint")
        assert "水平" in tr("known_scale_line_hint")
        assert "Esc" in tr("known_scale_line_hint")
        assert "Shift" in tr("draw_he_scale_tooltip")
        assert "水平" in tr("draw_he_scale_tooltip")
    finally:
        set_language(previous)
