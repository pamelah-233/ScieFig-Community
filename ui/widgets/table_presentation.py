"""Shared macOS table presentation; source values and interaction stay intact."""
from html import escape
import re

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QHeaderView, QStyledItemDelegate,
                            QToolTip)


_NUMBER = re.compile(r'^[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+−-]?\d+)?%?$')


class _ReadableValues(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # Alignment belongs to painting, never to the model used for export.
        if index.data(Qt.ItemDataRole.TextAlignmentRole) is None:
            if _NUMBER.fullmatch(option.text.strip()):
                option.displayAlignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class _TablePresentation(QObject):
    def __init__(self, table):
        super().__init__(table)
        self.table = table
        self.sampled = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.size_columns)
        table.viewport().installEventFilter(self)
        table.horizontalHeader().installEventFilter(self)
        model = table.model()
        model.headerDataChanged.connect(self.schedule)
        model.columnsInserted.connect(self.schedule)
        model.rowsInserted.connect(self.first_rows)
        model.modelReset.connect(self.reset)
        self.schedule()

    def reset(self, *_args):
        self.sampled = False
        self.schedule()

    def first_rows(self, *_args):
        if not self.sampled:
            self.schedule()

    def schedule(self, *_args):
        # Translate/dynamic-column updates are coalesced. No row scans or
        # resizing run on scrollbar changes or individual cell updates.
        self.timer.start(0)

    def size_columns(self):
        table = self.table
        if table.property('workflowGroupTable') or table.objectName() == 'WorkflowExportTable':
            return
        header = table.horizontalHeader()
        header.setResizeContentsPrecision(40)
        header.setMinimumSectionSize(72)
        # Keep compact three-column forms proportional. Wider result tables
        # retain readable widths instead of compressing into a narrow sidebar.
        compact = table.columnCount() <= 3 and all(
            header.sectionResizeMode(col) == QHeaderView.ResizeMode.Stretch
            for col in range(table.columnCount()))
        if compact:
            return
        was_stretched = header.stretchLastSection()
        header.setStretchLastSection(False)
        for col in range(table.columnCount()):
            mode = header.sectionResizeMode(col)
            if mode == QHeaderView.ResizeMode.Fixed:
                continue
            label = str(table.model().headerData(col, Qt.Orientation.Horizontal) or '')
            readable = max(96, min(240, header.fontMetrics().horizontalAdvance(label) + 24))
            if not self.sampled:
                # Only the first small data batch informs initial widths. A
                # large import never triggers a full-table contents scan.
                for row in range(min(24, table.model().rowCount())):
                    value = str(table.model().index(row, col).data() or '')
                    readable = max(readable, min(220, table.fontMetrics().horizontalAdvance(value[:256]) + 20))
            width = table.columnWidth(col) if mode == QHeaderView.ResizeMode.Interactive else 0
            if was_stretched and col == table.columnCount() - 1:
                width = min(width, 240)
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(col, max(width, readable))
        # Qt retains a stretched last section's expanded width after a window
        # shrinks. Stable interactive widths also let users resize every column.
        self.sampled = table.model().rowCount() > 0

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ToolTip:
            table = self.table
            if watched is table.viewport():
                index = table.indexAt(event.pos())
                if not index.isValid():
                    return False
                text = index.data(Qt.ItemDataRole.ToolTipRole)
                if text is None:
                    text = index.data(Qt.ItemDataRole.DisplayRole)
            else:
                col = table.horizontalHeader().logicalIndexAt(event.pos())
                if col < 0:
                    return False
                text = table.model().headerData(col, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
                if text is None:
                    text = table.model().headerData(col, Qt.Orientation.Horizontal)
            if text is not None and str(text):
                QToolTip.showText(event.globalPos(), '<qt>' + escape(str(text)).replace('\n', '<br>') + '</qt>', watched)
                return True
        return False


def readable_table(table):
    if hasattr(table, '_readable_presentation'):
        table._readable_presentation.schedule()
        return
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.horizontalScrollBar().setTracking(True)
    table.setAlternatingRowColors(True)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    if table.objectName() != 'WorkflowExportTable':
        table.setWordWrap(False)
    rows = table.verticalHeader()
    rows.setMinimumSectionSize(28)
    rows.setDefaultSectionSize(max(30, rows.defaultSectionSize(), table.iconSize().height() + 8))
    # Respect custom delegates (including any future editors/check controls).
    if type(table.itemDelegate()) is QStyledItemDelegate:
        table.setItemDelegate(_ReadableValues(table))
    table._readable_presentation = _TablePresentation(table)
