from PyQt6.QtWidgets import QApplication, QGraphicsTextItem, QMenu

from core.utils.text_formatting import display_unit, subscript_digits, superscript_digits
from ui.widgets.text_digit_transform import add_digit_transform_actions, apply_digit_transform_action


def test_display_unit_uses_unicode_area_exponents() -> None:
    assert display_unit("um^2") == "um²"
    assert display_unit("px^2") == "px²"
    assert display_unit("µm^3") == "µm³"
    assert superscript_digits("10-2") == "¹⁰⁻²"
    assert subscript_digits("H2O") == "H₂O"


def test_text_digit_transform_updates_selected_or_full_text() -> None:
    _app = QApplication.instance() or QApplication([])
    item = QGraphicsTextItem("Area um2")
    menu = QMenu()
    superscript_action, subscript_action = add_digit_transform_actions(menu, item)

    assert apply_digit_transform_action(superscript_action, (superscript_action, subscript_action))
    assert item.toPlainText() == "Area um²"

    item.setPlainText("H2O")
    cursor = item.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(2, cursor.MoveMode.KeepAnchor)
    item.setTextCursor(cursor)
    assert apply_digit_transform_action(subscript_action, (superscript_action, subscript_action))
    assert item.toPlainText() == "H₂O"
