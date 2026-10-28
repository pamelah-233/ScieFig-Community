"""Keep existing actions readable as a workflow sidebar changes width."""
from PyQt6.QtCore import QEvent, QSize
from PyQt6.QtWidgets import QBoxLayout, QGridLayout, QHBoxLayout, QPushButton, QToolButton, QSizePolicy, QWidget


def adapt_action_rows(root):
    """Reuse the synchronous action grid for legacy rows of text buttons."""
    for row in root.findChildren(QHBoxLayout):
        parent=row.parent()
        if not isinstance(parent,QBoxLayout):continue
        buttons=[row.itemAt(i).widget() for i in range(row.count()) if not row.itemAt(i).spacerItem()]
        if len(buttons)<2 or not all(isinstance(button,(QPushButton,QToolButton)) for button in buttons):continue
        if not any(len(button.text())>3 for button in buttons):continue
        index=next((i for i in range(parent.count()) if parent.itemAt(i).layout() is row),-1)
        if index<0:continue
        while row.count():row.takeAt(0)
        parent.takeAt(index);row.setParent(None);row.deleteLater()
        parent.insertWidget(index,ResponsiveActionGrid(buttons))


def reflow_buttons(grid, buttons, width):
    if not buttons:
        return
    gap = max(0, grid.horizontalSpacing())
    needed = 2 * max(button.sizeHint().width() for button in buttons) + gap
    columns = 2 if len(buttons) > 1 and width >= needed else 1
    positions = [(index // columns, index % columns) for index in range(len(buttons))]
    if all(grid.getItemPosition(grid.indexOf(button))[:2] == position
           for button, position in zip(buttons, positions)):
        return
    for button in buttons:
        grid.removeWidget(button)
    for button, (row, column) in zip(buttons, positions):
        grid.addWidget(button, row, column)


class ResponsiveActionGrid(QWidget):
    def __init__(self, buttons, parent=None):
        super().__init__(parent)
        self.buttons = tuple(buttons)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        for index, button in enumerate(self.buttons):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            grid.addWidget(button, index // 2, index % 2)

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        return QSize(0, size.height())

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            reflow_buttons(self.layout(), self.buttons, self.contentsRect().width())
        return result
