from __future__ import annotations

import os

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QPainter
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QStyleOptionGraphicsItem

from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from ui.widgets.shared_layout_text import (
    SharedTextDocument,
    SharedTextItem,
    SharedTextLayout,
    normalize_text_payload,
    text_payload_from_layout,
)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def test_selected_text_frame_can_be_resized_without_changing_font():
    from PyQt6.QtCore import QPointF
    from types import SimpleNamespace
    obj=LayoutObject('resize','text',10,20,100,40,text='Label')
    item=SharedTextItem(obj)
    item.setSelected(True)
    handle=item.handles['se']
    assert handle.isVisible()
    size=item.layout_object.style.font_size_pt
    event=lambda x,y:SimpleNamespace(scenePos=lambda:QPointF(x,y),modifiers=lambda:Qt.KeyboardModifier.NoModifier,accept=lambda:None)
    handle.mousePressEvent(event(110,60));handle.mouseMoveEvent(event(150,80));handle.mouseReleaseEvent(event(150,80))
    assert item.to_layout_object().width==140
    assert item.boundingRect().height()>=60
    assert item.layout_object.style.font_size_pt==size


def test_rich_text_ranges_follow_insertions_before_the_formatted_character():
    from PyQt6.QtGui import QTextCursor
    item=SharedTextItem(LayoutObject('rich','text',0,0,200,40,text='H2O'))
    item.apply_text_range_style(1,1,{'vertical_align':'subscript'})
    item.begin_edit()
    cursor=item.textCursor();cursor.setPosition(0);cursor.insertText('Heavy ');item.setTextCursor(cursor)
    item.finish_edit()
    restored=SharedTextItem(item.to_layout_object())
    cursor=QTextCursor(restored.document());cursor.setPosition(7);cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,QTextCursor.MoveMode.KeepAnchor)
    from PyQt6.QtGui import QTextCharFormat
    assert cursor.charFormat().verticalAlignment()==QTextCharFormat.VerticalAlignment.AlignSubScript


def test_successive_range_formats_do_not_erase_prior_styles():
    item=SharedTextItem(LayoutObject('rich','text',0,0,200,40,text='H2O'))
    item.apply_text_range_style(1,1,{'bold':True})
    item.apply_text_range_style(1,1,{'vertical_align':'subscript'})
    run=item.to_layout_object().metadata['text']['runs'][0]
    assert run['bold'] and run['vertical_align']=='subscript'


def test_font_change_updates_document_after_plain_text_was_serialized():
    from PyQt6.QtGui import QTextCursor
    item=SharedTextItem(LayoutObject('plain','text',0,0,200,40,text='Label'))
    item.to_layout_object()
    item.apply_text_style({'font_size_pt':13.5})
    cursor=QTextCursor(item.document());cursor.setPosition(1)
    assert cursor.charFormat().fontPointSize()==13.5


@pytest.mark.parametrize('module',['composer','qpcr','fluoro','wb'])
def test_canvas_backspace_edits_text_instead_of_deleting_an_object(module):
    from ui.widgets.composer_canvas import ComposerCanvas
    from ui.widgets.qpcr_layout_canvas import QPCRLayoutCanvas
    from ui.widgets.fluoro_canvas import FluorescenceCanvas
    from ui.widgets.wb_canvas import WBCanvas
    from PyQt6.QtGui import QTextCursor
    from PyQt6.QtTest import QTest
    canvas={'composer':ComposerCanvas,'qpcr':QPCRLayoutCanvas,'fluoro':FluorescenceCanvas,'wb':WBCanvas}[module]()
    if module=='fluoro':canvas.current_step=4
    item=SharedTextItem(LayoutObject('keyboard','text',0,0,150,40,text='ABC'))
    canvas.scene.addItem(item)
    canvas.show();canvas.view.setFocus();_APP.processEvents()
    item.setSelected(True);item.begin_edit()
    cursor=item.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End);item.setTextCursor(cursor)
    QTest.keyClick(canvas.view,Qt.Key.Key_Backspace)
    assert item.toPlainText()=='AB'
    assert item.scene() is canvas.scene
    canvas.close();_APP.processEvents()


def test_svg_and_wb_callback_text_keeps_a_tight_hitbox():
    from ui.widgets.editable_text_item import EditableMovableTextItem
    from PyQt6.QtGui import QFont
    item=EditableMovableTextItem('Label',lambda *a:None,lambda *a:None)
    font=QFont('Arial');font.setPointSizeF(8.5)
    item.setFont(font)
    assert item.boundingRect().width()<80


def test_whole_text_font_change_updates_run_sizes_but_keeps_other_formats():
    item=SharedTextItem(_text_object(text='H2O'))
    item.apply_text_range_style(1,1,{'font_size_pt':7.25,'bold':True,'vertical_align':'subscript'})
    item.apply_text_style({'font_size_pt':12.5})
    runs=item.to_layout_object().metadata['text']['runs']
    assert all(run.get('font_size_pt',12.5)==12.5 for run in runs)
    assert any(run.get('bold') and run.get('vertical_align')=='subscript' for run in runs)


def test_rich_text_paints_live_input_before_commit():
    item = SharedTextItem(_text_object(text="Alpha", metadata={"text":{"html":"<p>Alpha</p>"}}))
    item.begin_edit()
    def image():
        result=QImage(180,90,QImage.Format.Format_ARGB32)
        result.fill(Qt.GlobalColor.transparent)
        painter=QPainter(result)
        item.paint(painter,QStyleOptionGraphicsItem())
        painter.end()
        return result
    before=image()
    cursor=item.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    cursor.insertText(" extra")
    item.setTextCursor(cursor)
    assert item.toPlainText()=="Alpha extra"
    assert image()!=before


def _text_object(**overrides: object) -> LayoutObject:
    payload: dict[str, object] = {
        "object_id": "text-1",
        "object_type": "text",
        "x": 12.5,
        "y": 21.25,
        "width": 160.0,
        "height": 80.0,
        "text": "Alpha\nBeta",
        "style": LayoutObjectStyle(font_family="Arial", font_size_pt=8.5, text_color="#112233"),
    }
    payload.update(overrides)
    return LayoutObject(**payload)


def test_plain_text_payload_round_trips_with_fractional_font_size() -> None:
    layout_object = _text_object()

    payload = text_payload_from_layout(layout_object)
    restored_payload = normalize_text_payload(payload)

    assert restored_payload["plain_text"] == "Alpha\nBeta"
    assert layout_object.style.font_size_pt == pytest.approx(8.5)
    assert restored_payload["runs"] == []


def test_shared_text_document_is_a_plain_json_facing_round_trip_value() -> None:
    document = SharedTextDocument.from_layout_object(_text_object())

    assert document.plain_text == "Alpha\nBeta"
    assert document.to_payload()["plain_text"] == "Alpha\nBeta"


def test_legacy_text_html_and_runs_metadata_are_read_without_schema_migration() -> None:
    layout_object = _text_object(
        text="H2O",
        metadata={
            "text_html": "<p>H2O</p>",
            "text_runs": [{"start": 1, "length": 1, "vertical_align": "subscript"}],
        },
    )

    payload = normalize_text_payload(layout_object)

    assert payload["html"] == "<p>H2O</p>"
    assert payload["runs"][0]["vertical_align"] == "subscript"


def test_rich_text_runs_round_trip_and_keep_alignment() -> None:
    layout_object = _text_object(
        text="H2O",
        metadata={
            "text": {
                "runs": [
                    {
                        "start": 1,
                        "length": 1,
                        "text": "2",
                        "font_size_pt": 7.25,
                        "vertical_align": "subscript",
                        "underline": True,
                        "color": "#C0392B",
                    }
                ],
                "alignment": "center",
                "line_spacing": 1.15,
            }
        },
    )

    item = SharedTextItem(layout_object)
    restored = item.to_layout_object()
    payload = normalize_text_payload(restored)

    assert payload["plain_text"] == "H2O"
    assert payload["alignment"] == "center"
    assert payload["line_spacing"] == pytest.approx(1.15)
    assert payload["runs"][0]["vertical_align"] == "subscript"
    assert payload["runs"][0]["font_size_pt"] == pytest.approx(7.25)
    assert payload["runs"][0]["underline"] is True


def test_rich_range_edit_then_text_input_serializes_and_reopens() -> None:
    item = SharedTextItem(_text_object(text="H2O"))
    item.apply_text_range_style(1, 1, {"bold": True, "vertical_align": "superscript"})

    assert item.begin_edit() is True
    from PyQt6.QtGui import QTextCursor
    cursor=item.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End);cursor.insertText('!')
    item.setTextCursor(cursor)
    item.finish_edit()
    restored = SharedTextItem(item.to_layout_object())
    payload = normalize_text_payload(restored.to_layout_object())

    assert payload["plain_text"] == "H2O!"
    assert any(
        run["start"] == 1
        and run["length"] == 1
        and run["bold"] is True
        and run["vertical_align"] == "superscript"
        for run in payload["runs"]
    )


def test_multiline_preview_and_item_paint_use_the_same_layout() -> None:
    layout_object = _text_object()
    item = SharedTextItem(layout_object)
    scene = QGraphicsScene()
    scene.addItem(item)
    option = QStyleOptionGraphicsItem()

    item_image = QImage(180, 90, QImage.Format.Format_ARGB32)
    item_image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(item_image)
    item.paint(painter, option, None)
    painter.end()

    preview_image = SharedTextLayout.render_to_image(layout_object, 180, 90)
    item_pixels = [item_image.pixelColor(x, y) for y in range(90) for x in range(180)]
    preview_pixels = [preview_image.pixelColor(x, y) for y in range(90) for x in range(180)]

    assert any(color.alpha() > 0 for color in item_pixels)
    assert any(color.alpha() > 0 for color in preview_pixels)
    assert item_pixels == preview_pixels
    assert SharedTextLayout.document_for(layout_object).toPlainText() == "Alpha\nBeta"
    assert SharedTextLayout.document_for(layout_object).size().height() > 8.5


def test_plain_text_color_is_shared_by_preview_and_export() -> None:
    layout_object = _text_object(
        text="Red",
        style=LayoutObjectStyle(font_family="Arial", font_size_pt=12.0, text_color="#C0392B"),
    )

    image = SharedTextLayout.render_to_image(layout_object, 180, 90)
    colored = [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    ]

    assert any(color.red() > 100 and color.green() < 120 and color.blue() < 120 for color in colored)


def test_text_style_mutation_does_not_change_position() -> None:
    layout_object = _text_object()
    item = SharedTextItem(layout_object)
    item.setPos(44.75, 55.5)
    old_position = item.pos()

    item.apply_text_style({"font_size_pt": 13.25, "bold": True, "underline": True, "text_color": "#AA0000"})

    assert item.pos() == old_position
    assert item.layout_object.x == pytest.approx(12.5)
    assert item.layout_object.y == pytest.approx(21.25)
    assert item.layout_object.style.font_size_pt == pytest.approx(13.25)
    assert item.layout_object.metadata["text"]["underline"] is True


def test_style_commit_callback_contains_only_style_delta() -> None:
    changes: list[dict[str, object]] = []
    item = SharedTextItem(_text_object(), style_changed_callback=lambda _item, delta: changes.append(dict(delta)))

    item.apply_text_style({"font_size_pt": 12.5, "bold": True})

    assert changes[-1] == {"font_size_pt": 12.5, "bold": True}
    assert not {"x", "y", "width", "height"}.intersection(changes[-1])


def test_editing_shortcuts_are_focus_sensitive_and_locked_text_stays_readonly() -> None:
    editable = SharedTextItem(_text_object(text="A"))
    scene = QGraphicsScene()
    scene.addItem(editable)

    assert editable.begin_edit() is True
    editable.setPlainText("A")
    cursor = editable.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editable.setTextCursor(cursor)
    editable.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert editable.toPlainText() == "A\n"
    editable.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier))
    assert editable.is_editing is False

    locked = SharedTextItem(_text_object(locked=True))
    scene.addItem(locked)
    assert locked.begin_edit() is False
    assert locked.is_editing is False
