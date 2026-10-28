"""Coalesce one editing operation's notifications without copying project state."""
from contextlib import contextmanager


class EditNotifications:
    def __init__(self):
        self.enabled = False
        self.depth = 0
        self.pending = {}

    def begin(self):
        self.depth += 1

    def end(self):
        self.depth = max(0, self.depth - 1)
        if self.depth:
            return
        pending, self.pending = self.pending, {}
        for callback, args in pending.values():
            callback(*args)

    def emit(self, kind, callback, *args):
        if not self.enabled or not callable(callback):
            return
        if not self.depth:
            callback(*args)
            return
        if kind == "style" and kind in self.pending:
            previous = self.pending[kind][1][-1]
            args = (*args[:-1], {**previous, **args[-1]})
        self.pending[kind] = (callback, args)

    @contextmanager
    def transaction(self):
        self.begin()
        try:
            yield
        finally:
            self.end()
