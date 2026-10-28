"""Input-level checks for shared layout interactions, not scientific ROI tools."""
import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.models.layout_editor import LayoutCanvasSpec, LayoutObject
from ui.widgets.layout_editor_canvas import LayoutEditorCanvas

APP = QApplication.instance() or QApplication([])
LEFT = Qt.MouseButton.LeftButton
NONE = Qt.KeyboardModifier.NoModifier


@pytest.fixture
def canvas():
    widget = LayoutEditorCanvas()
    widget.resize(760, 570)
    image = QImage(120, 100, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkGreen)
    widget.set_layout_objects(
        LayoutCanvasSpec(canvas_id='interaction', width_px=640, height_px=440),
        [LayoutObject('text', 'text', 70, 60, 150, 40, text='Move me'),
         LayoutObject('image', 'image', 70, 130, 150, 120),
         LayoutObject('shape', 'shape', 300, 150, 80, 60)],
        lambda _: QPixmap.fromImage(image),
    )
    widget.show()
    APP.processEvents()
    yield widget
    widget.close()
    APP.processEvents()


def point(canvas, x, y):
    return canvas.view.mapFromScene(QPointF(x, y))


def drag(canvas, start, end, modifiers=NONE, cancel=False):
    view = canvas.view
    QTest.mousePress(view.viewport(), LEFT, modifiers, point(canvas, *start))
    for fraction in (.25, .5, 1):
        p = point(canvas, start[0]+fraction*(end[0]-start[0]), start[1]+fraction*(end[1]-start[1]))
        APP.sendEvent(view.viewport(), QMouseEvent(QEvent.Type.MouseMove, QPointF(p),
            QPointF(view.viewport().mapToGlobal(p)), Qt.MouseButton.NoButton, LEFT, modifiers))
    if cancel:
        QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.mouseRelease(view.viewport(), LEFT, modifiers, point(canvas, *end))
    APP.processEvents()


def select(canvas, *ids):
    canvas.scene.clearSelection()
    for uid in ids:
        canvas.item_for_object_id(uid).setSelected(True)


def test_shared_interaction_is_attached_to_editable_canvas(canvas):
    assert getattr(canvas.view, 'object_interaction', None) is not None


def test_grabbing_selected_text_keeps_image_in_group(canvas):
    select(canvas, 'text', 'image')
    drag(canvas, (110, 80), (145, 105))
    items = [canvas.item_for_object_id(uid) for uid in ('text', 'image')]
    assert all(i.isSelected() for i in items)
    assert items[0].pos()-QPointF(70, 60) == items[1].pos()-QPointF(70, 130)
    assert abs(items[0].pos().x()-105) < 2


def test_shift_click_toggles_without_clearing_other_objects(canvas):
    select(canvas, 'text', 'image')
    QTest.mouseClick(canvas.view.viewport(), LEFT, Qt.KeyboardModifier.ShiftModifier, point(canvas, 110, 80))
    assert not canvas.item_for_object_id('text').isSelected()
    assert canvas.item_for_object_id('image').isSelected()


def test_escape_reverts_only_the_current_drag(canvas):
    select(canvas, 'text', 'image')
    drag(canvas, (110, 80), (180, 100), cancel=True)
    assert canvas.item_for_object_id('text').pos() == QPointF(70, 60)
    assert canvas.item_for_object_id('image').pos() == QPointF(70, 130)
    assert len(canvas.scene.selectedItems()) == 2


def test_shift_drag_constrains_mixed_selection(canvas):
    select(canvas, 'text', 'image')
    drag(canvas, (110, 80), (160, 105), Qt.KeyboardModifier.ShiftModifier)
    assert canvas.item_for_object_id('text').pos().y() == 60
    assert canvas.item_for_object_id('image').pos().y() == 130


def test_nudge_is_in_scene_units_and_preserves_multiselection(canvas):
    select(canvas, 'text', 'image')
    QTest.keyClick(canvas.view, Qt.Key.Key_Right)
    QTest.keyClick(canvas.view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert canvas.item_for_object_id('text').pos() == QPointF(71, 70)
    assert canvas.item_for_object_id('image').pos() == QPointF(71, 140)


def test_force_marquee_can_start_on_image_and_use_contains_rule(canvas):
    interaction = getattr(canvas.view, 'object_interaction', None)
    assert interaction is not None
    interaction.force_marquee = True
    drag(canvas, (100, 160), (130, 190))
    assert canvas.item_for_object_id('image').isSelected()
    interaction.contains_only = True
    drag(canvas, (100, 160), (130, 190))
    assert not canvas.scene.selectedItems()


def test_text_edit_keyboard_does_not_move_canvas_object(canvas):
    item = canvas.item_for_object_id('text')
    item.setSelected(True)
    item.begin_edit()
    QTest.keyClick(canvas.view, Qt.Key.Key_Right)
    assert item.pos() == QPointF(70, 60)
    assert item.is_editing


def test_locked_object_does_not_move_with_selection(canvas):
    locked = canvas.item_for_object_id('image')
    locked.set_flags_for_locked(True)
    select(canvas, 'text', 'image')
    drag(canvas, (110, 80), (145, 105))
    assert locked.pos() == QPointF(70, 130)


def test_existing_zoom_bar_exposes_selection_controls(canvas):
    assert hasattr(canvas.zoom_controls, 'marquee_button')
    canvas.zoom_controls.marquee_button.click()
    assert canvas.view.object_interaction.force_marquee
    canvas.zoom_controls.contains_button.click()
    assert canvas.view.object_interaction.contains_only
    assert not canvas.view.object_interaction.snap_enabled


def test_drawing_tool_temporarily_owns_pointer_instead_of_selection(canvas):
    from ui.widgets.shared_layout_drawing import LayoutDrawTool
    tool = LayoutDrawTool(canvas.view)
    tool.arm('rectangle')
    assert not canvas.view.object_interaction.enabled()
    tool.cancel()
    assert canvas.view.object_interaction.enabled()


def test_explicit_hand_pan_mode_is_not_claimed_by_object_selection(canvas):
    from PyQt6.QtWidgets import QGraphicsView
    canvas.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
    assert not canvas.view.object_interaction.enabled()


def test_layout_root_is_not_an_editable_object(canvas):
    from ui.widgets.layout_editor_items import layout_editor_item_for
    root=layout_editor_item_for(LayoutObject('root','ihc_layout_root',0,0,600,400))
    canvas.scene.addItem(root)
    QTest.keyClick(canvas.view,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
    assert not root.isSelected()
    assert len(canvas.view.object_interaction.selected())==3


def test_space_during_object_drag_cancels_gesture_cleanly(canvas):
    select(canvas,'text')
    view=canvas.view
    QTest.mousePress(view.viewport(),LEFT,NONE,point(canvas,110,80))
    QTest.mouseMove(view.viewport(),point(canvas,150,100))
    QTest.keyPress(view,Qt.Key.Key_Space)
    QTest.mouseRelease(view.viewport(),LEFT,NONE,point(canvas,150,100))
    QTest.keyRelease(view,Qt.Key.Key_Space)
    assert view.object_interaction.mode is None
    assert canvas.item_for_object_id('text').pos()==QPointF(70,60)


def test_object_click_clears_old_text_range_selection(canvas):
    item=canvas.item_for_object_id('text')
    item.retained_text_selection=(0,4)
    QTest.mouseClick(canvas.view.viewport(),LEFT,NONE,point(canvas,110,80))
    assert item.retained_text_selection is None
