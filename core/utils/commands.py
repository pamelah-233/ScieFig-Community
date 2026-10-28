from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class Command(Protocol):
    description: str

    def execute(self) -> None: ...

    def undo(self) -> None: ...


@dataclass
class SnapshotCommand:
    description: str
    before: dict
    after: dict
    restore: Callable[[dict], None]

    def execute(self) -> None:
        self.restore(self.after)

    def undo(self) -> None:
        self.restore(self.before)


class UndoManager:
    def __init__(self, max_steps: int = 50) -> None:
        self.max_steps = max_steps
        self.undo_stack: list[Command] = []
        self.redo_stack: list[Command] = []
        self._transaction_depth = 0

    def begin_transaction(self) -> None:
        """The caller captures one before/after snapshot for a continuous gesture."""
        self._transaction_depth += 1

    def end_transaction(self) -> None:
        self._transaction_depth = max(0, self._transaction_depth - 1)

    def execute(self, command: Command) -> None:
        command.execute()
        self.record(command)

    def record(self, command: Command) -> None:
        if self._transaction_depth:
            return
        self.undo_stack.append(command)
        if len(self.undo_stack) > self.max_steps:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        command = self.redo_stack.pop()
        command.execute()
        self.undo_stack.append(command)
        return True

    def clear(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
