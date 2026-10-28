from __future__ import annotations
from PyQt6.QtCore import QEvent,QMimeData,QPoint,QSize,Qt,pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import QWidget,QListWidget,QListWidgetItem,QApplication
from core.utils.image_formats import COMPOSER_IMAGE_SUFFIXES,dropped_image_paths
from ui.widgets.composer_canvas import PANEL_UUID_MIME

class AssetShelfViewport(QWidget):
    def __init__(self, shelf: "ComposerAssetShelf") -> None:
        super().__init__(shelf)
        self.shelf = shelf
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if self.shelf._handle_external_file_drop_event(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self.shelf._handle_external_file_drop_event(event):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if self.shelf._handle_external_file_drop_event(event):
            return
        super().dropEvent(event)


class ComposerAssetShelf(QListWidget):
    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_start_pos: QPoint | None = None
        self.setViewport(AssetShelfViewport(self))
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.viewport().setAcceptDrops(True)
        self.viewport().installEventFilter(self)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setObjectName("ComposerAssetShelf")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setWrapping(False)
        self.setIconSize(QSize(104, 72))
        self.setGridSize(QSize(136, 106))
        self.setMaximumHeight(132)
        self._enable_external_file_drops()

    def _enable_external_file_drops(self) -> None:
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def showEvent(self, event) -> None:
        self._enable_external_file_drops()
        super().showEvent(event)

    def _dropped_file_paths(self, mime_data: QMimeData) -> list[str]:
        return dropped_image_paths(mime_data, COMPOSER_IMAGE_SUFFIXES)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.viewport() and event.type() in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        }:
            if self._handle_external_file_drop_event(event):
                return True
        return super().eventFilter(watched, event)

    def viewportEvent(self, event) -> bool:
        if event.type() in {QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop}:
            if self._handle_external_file_drop_event(event):
                return True
        return super().viewportEvent(event)

    def _handle_external_file_drop_event(self, event) -> bool:
        paths = self._dropped_file_paths(event.mimeData())
        if not paths:
            return False
        if event.type() == QEvent.Type.Drop:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()
        return True

    def dragEnterEvent(self, event) -> None:
        if self._dropped_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._dropped_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._dropped_file_paths(event.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        self._start_panel_drag(item)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            item = self.itemAt(self._drag_start_pos)
            if item is not None:
                self.setCurrentItem(item)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not event.buttons() & Qt.MouseButton.LeftButton or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
        if distance < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        item = self.itemAt(self._drag_start_pos) or self.currentItem()
        self._start_panel_drag(item)

    def _start_panel_drag(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        mime = self.panel_drag_mime_data(item)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = item.icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(self.iconSize()))
        drag.exec(Qt.DropAction.CopyAction)

    def panel_drag_mime_data(self, item: QListWidgetItem | None) -> QMimeData | None:
        if item is None:
            return None
        panel_uuid = item.data(Qt.ItemDataRole.UserRole)
        if not panel_uuid:
            return None
        mime = QMimeData()
        mime.setData(PANEL_UUID_MIME, str(panel_uuid).encode("utf-8"))
        return mime
