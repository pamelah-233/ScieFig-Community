"""Native image/contour review with staged changes and an ID-linked object table."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QGraphicsItem, QGraphicsPathItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGridLayout,
    QLabel, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.measurement_review import ObjectReviewSession, object_rows
from core.measurement_review_adapter import review_source_image
from core.measurement_scale import apply_length_scale
from core.utils.i18n import get_language


TEXT = {
    'title': ('Object review', '对象复核'),
    'select': ('Select', '选择'), 'add': ('Add polygon', '添加多边形'),
    'replace': ('Replace contour', '修正轮廓'), 'split': ('Split by line', '直线拆分'),
    'merge': ('Merge selected', '合并所选'), 'delete': ('Delete selected', '删除所选'),
    'undo': ('Undo', '撤销'), 'redo': ('Redo', '重做'), 'finish': ('Finish polygon', '完成多边形'),
    'fit': ('Fit image', '适合窗口'), 'apply': ('Apply changes', '应用修改'), 'cancel': ('Cancel', '取消'),
    'id': ('Object ID', '对象 ID'), 'source': ('Provenance', '来源'),
    'area_px': ('Area (px²)', '面积 (px²)'), 'area_um': ('Area (µm²)', '面积 (µm²)'),
    'diameter': ('Eq. diameter (µm)', '等效直径 (µm)'), 'feret': ('Max Feret (µm)', '最大卡尺径 (µm)'),
    'automatic': ('Automatic', '自动'), 'edited': ('Edited', '已编辑'),
    'summary': ('{count} objects · {edited} edited · {missing} contour unavailable', '{count} 个对象 · {edited} 个已编辑 · {missing} 个轮廓不可用'),
    'calibration': ('Saved calibration: X {x:g}, Y {y:g} µm/px', '历史校准：X {x:g}，Y {y:g} µm/px'),
    'uncalibrated': ('No complete saved calibration — physical dimensions are unavailable.', '无完整历史校准，不能计算物理尺寸。'),
    'help': ('Select an object in the image or table (Ctrl for multiple). Wheel: zoom. Changes are staged until Apply; source pixels are never changed.',
             '点击图像或表格选择对象（Ctrl 多选），滚轮缩放。点击“应用修改”前不保存；原始像素始终不变。'),
    'draw': ('Click vertices, then Finish polygon or Enter. Escape discards the draft.', '逐点绘制轮廓，点击“完成多边形”或按 Enter；Esc 放弃草稿。'),
    'line': ('Click two points for an extended line across the selected object. All source pixels are retained.', '点击两点，以延长直线拆分所选对象；不丢弃任何对象像素。'),
    'notice': ('Edited areas count mask pixels; automatic measurements stay unchanged. Missing contours are location markers, not segmentations.',
               '编辑后面积按掩膜像素计数；未编辑的自动结果保持不变。无轮廓对象仅显示位置标记，不代表分割边界。'),
    'error': ('Cannot complete: ', '无法完成：'),
    'draft': ('Finish or cancel the drawing before applying changes.', '请先完成或取消绘制，再应用修改。'),
    'image': ('Source image with object contours', '原始图像与对象轮廓'),
    'table': ('Object measurements; selection is linked to image contours', '对象测量表；所选行与图像轮廓关联'),
}


class ObjectReviewCanvas(QGraphicsView):
    objectSelectionChanged = pyqtSignal(object)
    drawingFinished = pyqtSignal(str, object)
    drawingChanged = pyqtSignal(str, int)

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self._image_item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.setSceneRect(QRectF(0, 0, image.width(), image.height()))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(220, 240)
        self.object_items = {}
        self._labels = []
        self.mode = 'select'
        self._points = []
        self._draft = self.scene().addPath(QPainterPath())
        self._draft.setZValue(10)
        pen = QPen(self.palette().color(QPalette.ColorRole.Highlight), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._draft.setPen(pen)
        self.scene().selectionChanged.connect(self._selection_changed)
        self.set_mode('select')

    def set_objects(self, rows):
        with QSignalBlocker(self.scene()):
            for item in [*self.object_items.values(), *self._labels]:
                self.scene().removeItem(item)
            self.object_items, self._labels = {}, []
            for row in rows:
                path = QPainterPath()
                for contour in row.get('review_contours_px', []):
                    if not contour:
                        continue
                    path.moveTo(*contour[0])
                    for x, y in contour[1:]:
                        path.lineTo(x, y)
                    path.closeSubpath()
                x = float(row.get('centroid_x', row.get('center_x_px', row.get('x', 0))) or 0)
                y = float(row.get('centroid_y', row.get('center_y_px', row.get('y', 0))) or 0)
                if path.isEmpty():
                    radius = max(1., self.sceneRect().width() / 180)
                    path.addEllipse(QPointF(x, y), radius, radius)
                    path.moveTo(x - radius, y)
                    path.lineTo(x + radius, y)
                    path.moveTo(x, y - radius)
                    path.lineTo(x, y + radius)
                item = QGraphicsPathItem(path)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
                item.setData(0, row['object_id'])
                item.setData(1, bool(row.get('review_geometry_available')))
                item.setToolTip(str(row['object_id']))
                item.setZValue(1)
                self.scene().addItem(item)
                self.object_items[str(row['object_id'])] = item
                label = QGraphicsSimpleTextItem(str(row['object_id']))
                label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                label.setBrush(self.palette().color(QPalette.ColorRole.HighlightedText))
                label.setPos(x, y)
                label.setZValue(2)
                self.scene().addItem(label)
                self._labels.append(label)
        self._style_selection()

    def _style_selection(self):
        for item in self.object_items.values():
            role = QPalette.ColorRole.Highlight if item.isSelected() else QPalette.ColorRole.HighlightedText
            color = QColor(self.palette().color(role))
            if color.lightnessF() < .6:
                color = color.lighter(180)
            pen = QPen(color, 3 if item.isSelected() else 1.5,
                       Qt.PenStyle.SolidLine if item.data(1) else Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            item.setPen(pen)
            color.setAlpha(70 if item.isSelected() else 20)
            item.setBrush(QBrush(color))

    def selected_object_ids(self):
        return [item.data(0) for item in self.object_items.values() if item.isSelected()]

    def select_objects(self, identifiers):
        selected = {str(value) for value in identifiers}
        with QSignalBlocker(self.scene()):
            for key, item in self.object_items.items():
                item.setSelected(key in selected)
        self._selection_changed()

    def _selection_changed(self):
        self._style_selection()
        self.objectSelectionChanged.emit(self.selected_object_ids())

    def set_mode(self, mode):
        self.mode, self._points = mode, []
        self._draft.setPath(QPainterPath())
        self.setDragMode(self.DragMode.RubberBandDrag if mode == 'select' else self.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor if mode == 'select' else Qt.CursorShape.CrossCursor)
        self.drawingChanged.emit(mode, 0)
        self.setFocus()

    def mousePressEvent(self, event):
        if self.mode != 'select' and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.sceneRect().contains(point):
                x, y = round(point.x()), round(point.y())
                if 0 <= x < self.sceneRect().width() and 0 <= y < self.sceneRect().height():
                    self._points.append([x, y])
                    path = QPainterPath(QPointF(*self._points[0]))
                    for vertex in self._points[1:]:
                        path.lineTo(*vertex)
                    self._draft.setPath(path)
                    self.drawingChanged.emit(self.mode, len(self._points))
                    if self.mode == 'split' and len(self._points) == 2:
                        self.finish_drawing()
            event.accept()
        else:
            super().mousePressEvent(event)

    def finish_drawing(self):
        mode, points = self.mode, list(self._points)
        if mode != 'select':
            self.set_mode('select')
            self.drawingFinished.emit(mode, points)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.mode != 'select':
            self.set_mode('select')
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.mode != 'select':
            self.finish_drawing()
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if .01 <= self.transform().m11() * factor <= 200:
            self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
            self.scale(factor, factor)
        event.accept()

    def fit_image(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()


class ObjectReviewDialog(QDialog):
    @classmethod
    def from_measurement(cls, panel, record, parent=None):
        object_rows(record)  # Explain aggregate-only results before attempting image IO.
        image, intensity = review_source_image(panel, record)
        return cls(record, image, parent=parent, intensity_image=intensity)

    def __init__(self, record, image, parent=None, *, intensity_image=None, language=None):
        super().__init__(parent)
        if image.isNull():
            raise ValueError('Source image data is unavailable for object review.')
        self._zh = str(language or get_language()).startswith('zh')
        self.session = ObjectReviewSession(record, (image.height(), image.width()), intensity_image=intensity_image)
        self.applied_record = None
        self._syncing = False
        self._draw_selection = []
        self.setWindowTitle(self.text('title'))
        self.resize(1120, 720)
        self.setMinimumSize(780, 560)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)
        self.calibration_label = QLabel()
        self.calibration_label.setWordWrap(True)
        sx, sy = (apply_length_scale(*axis, self.session.calibration_snapshot) for axis in ((1, 0), (0, 1)))
        self.calibration_label.setText(self.text('calibration').format(x=sx, y=sy) if sx > 0 and sy > 0 else self.text('uncalibrated'))
        layout.addWidget(self.calibration_label)
        self.buttons = {}
        toolbar = QGridLayout()
        for position, key in enumerate(('select', 'add', 'replace', 'split', 'merge', 'delete', 'undo', 'redo', 'finish', 'fit')):
            button = QPushButton(self.text(key))
            button.setAutoDefault(False)
            button.setAccessibleName(self.text(key))
            self.buttons[key] = button
            toolbar.addWidget(button, position // 5, position % 5)
        layout.addLayout(toolbar)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.canvas = ObjectReviewCanvas(image)
        self.canvas.setAccessibleName(self.text('image'))
        splitter.addWidget(self.canvas)
        self.table = QTableWidget(0, 8)
        self.table.setAccessibleName(self.text('table'))
        self.table.setHorizontalHeaderLabels([self.text(key) for key in ('id', 'source', 'area_px', 'area_um', 'diameter', 'feret')] + ['X (px)', 'Y (px)'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumWidth(220)
        splitter.addWidget(self.table)
        splitter.setSizes([560, 500])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        self.status_label = QLabel(self.text('help'))
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        notice = QLabel(self.text('notice'))
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.button_box.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText(self.text('apply'))
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(self.text('cancel'))
        self.apply_button.clicked.connect(self._apply)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        self._connect_actions()
        self._refresh()

    def text(self, key):
        return TEXT[key][int(self._zh)]

    def _error_message(self, error):
        message = str(error)
        if not self._zh:
            return message
        messages = {
            'A polygon needs at least three finite vertices.': '多边形至少需要三个有效顶点。',
            'Polygon vertices must be inside the source image.': '多边形顶点必须位于源图像范围内。',
            'Polygon must enclose a non-zero area without crossing itself.': '多边形须围成非零面积，且轮廓不能自交。',
            'Polygon edges must not cross.': '多边形边线不能相交。',
            'Polygon contains no image pixels.': '多边形内没有图像像素。',
            'Polygon must stay inside the saved measurement ROI and outside excluded regions.': '多边形须位于已保存的测量 ROI 内，且不能进入排除区域。',
            'Select at least one object to delete.': '请至少选择一个需要删除的对象。',
            'Select at least two objects to merge.': '请至少选择两个需要合并的对象。',
            'Split requires two different finite line points.': '拆分线需要两个不同的有效端点。',
            'The split line must cross the selected object.': '拆分线必须穿过所选对象。',
            'Saved object geometry is unavailable; draw a replacement polygon first.': '没有可用的历史对象轮廓，请先绘制替代多边形。',
            'Saved object geometry has no image pixels.': '已保存的对象轮廓内没有图像像素。',
        }
        return messages.get(message, message)

    def _connect_actions(self):
        for mode in ('select', 'add', 'replace', 'split'):
            self.buttons[mode].clicked.connect(lambda checked=False, name=mode: self._start_drawing(name))
        for operation in ('delete', 'merge', 'undo', 'redo'):
            self.buttons[operation].clicked.connect(lambda checked=False, name=operation: self._perform(name))
        self.buttons['fit'].clicked.connect(self.canvas.fit_image)
        self.buttons['finish'].clicked.connect(self.canvas.finish_drawing)
        self.table.itemSelectionChanged.connect(self._table_selection)
        self.canvas.objectSelectionChanged.connect(self._canvas_selection)
        self.canvas.drawingFinished.connect(self._finish_drawing)
        self.canvas.drawingChanged.connect(lambda *_: self._update_buttons())
        for sequence, key in (('Ctrl+Z', 'undo'), ('Ctrl+Shift+Z', 'redo'), ('Ctrl+Y', 'redo'), ('Delete', 'delete')):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(lambda name=key: self.buttons[name].click())

    def selected_object_ids(self):
        return [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()]

    def _table_selection(self):
        if self._syncing:
            return
        self._syncing = True
        self.canvas.select_objects(self.selected_object_ids())
        self._syncing = False
        self._update_buttons()

    def _canvas_selection(self, identifiers):
        if self._syncing:
            return
        self._syncing = True
        selected = {str(value) for value in identifiers}
        with QSignalBlocker(self.table):
            self.table.clearSelection()
            for row in range(self.table.rowCount()):
                if str(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) in selected:
                    for col in range(self.table.columnCount()):
                        self.table.item(row, col).setSelected(True)
        self._syncing = False
        self._update_buttons()

    def _update_buttons(self):
        identifiers = self.selected_object_ids()
        rows = [self.session.object(value) for value in identifiers]
        drawing = self.canvas.mode != 'select'
        geometry = bool(rows) and all(row.get('review_geometry_available') for row in rows)
        for key in ('select', 'add'):
            self.buttons[key].setEnabled(True)
        self.buttons['replace'].setEnabled(len(rows) == 1 and not drawing)
        self.buttons['split'].setEnabled(len(rows) == 1 and geometry and not drawing)
        self.buttons['merge'].setEnabled(len(rows) > 1 and geometry and not drawing)
        self.buttons['delete'].setEnabled(bool(rows) and not drawing)
        self.buttons['undo'].setEnabled(self.session.can_undo and not drawing)
        self.buttons['redo'].setEnabled(self.session.can_redo and not drawing)
        self.buttons['finish'].setEnabled(self.canvas.mode in ('add', 'replace'))
        self.apply_button.setEnabled(self.session.changed and not drawing)

    def _refresh(self, selected=()):
        rows = self.session.objects
        self._syncing = True
        with QSignalBlocker(self.table):
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                provenance = row['review_provenance']['status']
                values = [row['object_id'], self.text(provenance), row.get('area_px'), row.get('area_um2'),
                          row.get('equivalent_diameter_um'), row.get('max_feret_um'),
                          row.get('centroid_x', row.get('center_x_px')), row.get('centroid_y', row.get('center_y_px'))]
                for column, value in enumerate(values):
                    item = QTableWidgetItem()
                    item.setData(Qt.ItemDataRole.DisplayRole, '—' if value is None else (round(value, 3) if isinstance(value, float) else value))
                    item.setData(Qt.ItemDataRole.UserRole, row['object_id'])
                    self.table.setItem(index, column, item)
            self.table.setSortingEnabled(True)
        self.canvas.set_objects(rows)
        self._syncing = False
        self.canvas.select_objects(selected)
        self.summary_label.setText(self.text('summary').format(count=len(rows),
            edited=sum(row['review_provenance']['status'] == 'edited' for row in rows),
            missing=sum(not row.get('review_geometry_available') for row in rows)))
        self._update_buttons()

    def _start_drawing(self, mode):
        self._draw_selection = self.selected_object_ids()
        self.canvas.set_mode(mode)
        self.status_label.setText(self.text('line' if mode == 'split' else ('help' if mode == 'select' else 'draw')))

    def _finish_drawing(self, mode, points):
        try:
            if mode == 'add':
                selected = [self.session.add_polygon(points)]
            elif mode == 'replace':
                selected = [self.session.replace_polygon(self._draw_selection[0], points)]
            else:
                selected = self.session.split_object(self._draw_selection[0], points[0], points[1])
            self.status_label.setText(self.text('help'))
            self._refresh(selected)
        except (ValueError, IndexError) as error:
            self.status_label.setText(self.text('error') + self._error_message(error))
            self._update_buttons()

    def _perform(self, operation):
        selected = self.selected_object_ids()
        try:
            if operation == 'delete':
                self.session.delete_objects(selected)
                selected = []
            elif operation == 'merge':
                selected = [self.session.merge_objects(selected)]
            else:
                getattr(self.session, operation)()
                present = {str(row['object_id']) for row in self.session.objects}
                selected = [value for value in selected if str(value) in present]
            self._refresh(selected)
            self.status_label.setText(self.text('help'))
        except ValueError as error:
            self.status_label.setText(self.text('error') + self._error_message(error))

    def _apply(self):
        if self.canvas.mode != 'select':
            self.status_label.setText(self.text('draft'))
            return
        self.applied_record = self.session.updated_record()
        self.accept()

    def reject(self):
        self.canvas.set_mode('select')
        self.applied_record = None
        super().reject()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.canvas.mode != 'select':
            self.canvas.set_mode('select')
            self.status_label.setText(self.text('help'))
            event.accept()
            return
        super().keyPressEvent(event)
