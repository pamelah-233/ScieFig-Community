from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


RESIZE_HANDLE_ROLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
RESIZE_HANDLE_SIZE_PX = 8.0


def aspect_resize_size(start_width, start_height, width, height, role):
    """Keep the original image ratio; side handles use their own axis."""
    ratio = start_width / max(.001, start_height)
    vertical = role in {'n','s'} or (len(role)==2 and abs(height-start_height)/max(.001,start_height) > abs(width-start_width)/max(.001,start_width))
    return (height*ratio,height) if vertical else (width,width/ratio)


@dataclass
class EditableLayoutRecord:
    kind: str
    uid: str
    model: Any
    x: float
    y: float
    width: float
    height: float
    locked: bool = False
    can_resize: bool = True
    hidden: bool = False
    z_order: int = 0

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


def editable_records(records: Iterable[EditableLayoutRecord]) -> list[EditableLayoutRecord]:
    return [record for record in records if not record.locked and not record.hidden]


def visible_records(records: Iterable[EditableLayoutRecord]) -> list[EditableLayoutRecord]:
    return [record for record in records if not record.hidden]


def move_records(records: Iterable[EditableLayoutRecord], dx: float, dy: float) -> bool:
    selected = editable_records(records)
    if not selected:
        return False
    for record in selected:
        record.x += float(dx)
        record.y += float(dy)
    return True


def set_records_locked(records: Iterable[EditableLayoutRecord], locked: bool) -> bool:
    selected = list(records)
    if not selected:
        return False
    for record in selected:
        record.locked = bool(locked)
    return True


def set_records_hidden(records: Iterable[EditableLayoutRecord], hidden: bool) -> bool:
    selected = list(records)
    if not selected:
        return False
    for record in selected:
        record.hidden = bool(hidden)
    return True


def normalize_z_order(records: Iterable[EditableLayoutRecord], start: int = 0) -> bool:
    selected = sorted(list(records), key=lambda record: (record.z_order, record.y, record.x, record.uid))
    if not selected:
        return False
    for index, record in enumerate(selected, start=int(start)):
        record.z_order = index
    return True


def bring_records_to_front(records: Iterable[EditableLayoutRecord], selected_uids: set[str]) -> bool:
    all_records = list(records)
    selected = [record for record in all_records if record.uid in selected_uids and not record.locked and not record.hidden]
    if not selected:
        return False
    max_z = max((record.z_order for record in all_records), default=0)
    for offset, record in enumerate(sorted(selected, key=lambda item: item.z_order), start=1):
        record.z_order = max_z + offset
    normalize_z_order(all_records)
    return True


def send_records_to_back(records: Iterable[EditableLayoutRecord], selected_uids: set[str]) -> bool:
    all_records = list(records)
    selected = [record for record in all_records if record.uid in selected_uids and not record.locked and not record.hidden]
    if not selected:
        return False
    min_z = min((record.z_order for record in all_records), default=0)
    for offset, record in enumerate(sorted(selected, key=lambda item: item.z_order), start=1):
        record.z_order = min_z - len(selected) + offset - 1
    normalize_z_order(all_records)
    return True


def align_records(records: Iterable[EditableLayoutRecord], mode: str, reference_mode: str = "extent") -> bool:
    selected = editable_records(records)
    if len(selected) < 2:
        return False
    if mode == "left":
        target = selected[0].x if reference_mode == "first" else min(record.x for record in selected)
        for record in selected:
            record.x = target
    elif mode == "right":
        target = selected[0].right if reference_mode == "first" else max(record.right for record in selected)
        for record in selected:
            record.x = target - record.width
    elif mode == "top":
        target = selected[0].y if reference_mode == "first" else min(record.y for record in selected)
        for record in selected:
            record.y = target
    elif mode == "bottom":
        target = selected[0].bottom if reference_mode == "first" else max(record.bottom for record in selected)
        for record in selected:
            record.y = target - record.height
    elif mode == "hcenter":
        target = selected[0].center_x if reference_mode == "first" else sum(record.center_x for record in selected) / len(selected)
        for record in selected:
            record.x = target - record.width / 2.0
    elif mode == "vcenter":
        target = selected[0].center_y if reference_mode == "first" else sum(record.center_y for record in selected) / len(selected)
        for record in selected:
            record.y = target - record.height / 2.0
    else:
        raise ValueError(f"Unsupported align mode: {mode}")
    return True


def distribute_records(records: Iterable[EditableLayoutRecord], orientation: str) -> bool:
    selected = editable_records(records)
    if len(selected) < 3:
        return False
    if orientation == "horizontal":
        selected.sort(key=lambda record: record.x)
        start = selected[0].x
        end = selected[-1].right
        total = sum(record.width for record in selected)
        gap = (end - start - total) / (len(selected) - 1)
        cursor = start
        for record in selected:
            record.x = cursor
            cursor += record.width + gap
    elif orientation == "vertical":
        selected.sort(key=lambda record: record.y)
        start = selected[0].y
        end = selected[-1].bottom
        total = sum(record.height for record in selected)
        gap = (end - start - total) / (len(selected) - 1)
        cursor = start
        for record in selected:
            record.y = cursor
            cursor += record.height + gap
    else:
        raise ValueError(f"Unsupported distribute orientation: {orientation}")
    return True


def make_records_same_size(records: Iterable[EditableLayoutRecord], min_size: float = 5.0, reference_mode: str = "top_left") -> bool:
    selected = [record for record in editable_records(records) if record.can_resize]
    if len(selected) < 2:
        return False
    if reference_mode == "largest":
        reference = max(selected, key=lambda record: record.width * record.height)
    elif reference_mode == "top_left":
        reference = sorted(selected, key=lambda record: (record.y, record.x))[0]
    else:
        raise ValueError(f"Unsupported same-size reference mode: {reference_mode}")
    target_width = max(float(min_size), float(reference.width))
    target_height = max(float(min_size), float(reference.height))
    for record in selected:
        record.width = target_width
        record.height = target_height
    return True
