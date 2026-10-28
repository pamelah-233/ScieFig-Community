from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtWidgets import QGraphicsTextItem, QMenu

from core.utils.text_formatting import subscript_digits, superscript_digits


def add_digit_transform_actions(
    menu: QMenu,
    item: QGraphicsTextItem,
    on_changed: Callable[[], None] | None = None,
) -> tuple[object, object]:
    """Add Unicode superscript/subscript controls for a graphics text item."""
    menu.addSeparator()
    superscript_action = menu.addAction("Superscript digits")
    subscript_action = menu.addAction("Subscript digits")

    def apply(action) -> bool:
        transform = superscript_digits if action is superscript_action else subscript_digits
        cursor = item.textCursor()
        if cursor.hasSelection():
            cursor.insertText(transform(cursor.selectedText()))
            item.setTextCursor(cursor)
        else:
            item.setPlainText(transform(item.toPlainText()))
        if on_changed is not None:
            on_changed()
        return True

    # The caller owns menu execution and calls the returned action matcher.
    superscript_action.setData(apply)
    subscript_action.setData(apply)
    return superscript_action, subscript_action


def apply_digit_transform_action(action: object, candidates: tuple[object, object]) -> bool:
    if action not in candidates:
        return False
    callback = action.data() if hasattr(action, "data") else None
    return bool(callback(action)) if callable(callback) else False
