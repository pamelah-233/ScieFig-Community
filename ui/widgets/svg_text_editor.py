from __future__ import annotations

from PyQt6.QtWidgets import QInputDialog, QWidget

from core.utils.i18n import tr

from core.utils.svg_text import SvgTextEntry, svg_text_entries


def _select_svg_text_entry(parent: QWidget | None, svg_bytes: bytes, title: str) -> SvgTextEntry | None:
    entries = svg_text_entries(svg_bytes)
    if not entries:
        return None
    labels = [f"{entry.index + 1}: {entry.text}" for entry in entries]
    selected, accepted = QInputDialog.getItem(parent, title, tr("text_element"), labels, 0, False)
    if not accepted:
        return None
    index = labels.index(str(selected))
    return entries[index]


def prompt_svg_text_edit(parent: QWidget | None, svg_bytes: bytes, title: str) -> tuple[SvgTextEntry, str] | None:
    entry = _select_svg_text_entry(parent, svg_bytes, title)
    if entry is None:
        return None
    replacement, accepted = QInputDialog.getText(parent, title, tr("text"), text=entry.text)
    if not accepted or replacement == entry.text:
        return None
    return entry, str(replacement)


def prompt_svg_text_position_edit(parent: QWidget | None, svg_bytes: bytes, title: str) -> tuple[SvgTextEntry, float, float] | None:
    entry = _select_svg_text_entry(parent, svg_bytes, title)
    if entry is None:
        return None
    dx, accepted = QInputDialog.getDouble(parent, title, tr("horizontal_offset"), 0.0, -100000.0, 100000.0, 2)
    if not accepted:
        return None
    dy, accepted = QInputDialog.getDouble(parent, title, tr("vertical_offset"), 0.0, -100000.0, 100000.0, 2)
    if not accepted or (dx == 0.0 and dy == 0.0):
        return None
    return entry, float(dx), float(dy)
