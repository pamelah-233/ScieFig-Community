"""Interactive preview of multiplex phenotyping and spatial analysis on synthetic data."""
import csv
from collections import Counter
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QImage, QPixmap, QPen, QBrush, QPainter
from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QDoubleSpinBox, QLineEdit, QComboBox, QCheckBox, QScrollArea,
    QSplitter, QGraphicsView, QGraphicsScene, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QFileDialog, QMessageBox, QAbstractItemView)

from core.spatial_demo import synthetic_image, analyze, spatial_metrics, phenotype_name


COLORS = ('#92979c', '#f0ac55', '#ca88e0', '#48c5df', '#74cb87', '#d5ba68', '#f17d95', '#dcd5c9')


class TissueView(QGraphicsView):
    cell_clicked = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMinimumSize(360, 360)
        self.setBackgroundBrush(QColor('#111418'))
        self.fit_image = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_image:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event):
        factor = 1.18 if event.angleDelta().y() > 0 else 1/1.18
        current = self.transform().m11()
        if .2 <= current*factor <= 8:
            self.scale(factor, factor)
            self.fit_image = False

    def mousePressEvent(self, event):
        self._press = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.position()-self._press).manhattanLength() < 5:
            item = self.itemAt(event.position().toPoint())
            if item is not None and item.data(0) is not None:
                self.cell_clicked.emit(int(item.data(0)))
        super().mouseReleaseEvent(event)


class SpatialDemoDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('ScieFig · 多标记表型与空间邻域 Demo')
        self.resize(1380, 860)
        self.setMinimumSize(1060, 680)
        self.image = synthetic_image()
        self.pixel_um = .5
        self.unit = 'µm'
        self.data_type = 'SYNTHETIC_DEMO'
        self.source_label = '合成 4 通道图像'
        self.selected_id = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.recalculate)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        title = QLabel('多标记细胞表型  /  空间邻域')
        title.setStyleSheet('font-size:20px;font-weight:600;color:#ead8c4')
        outer.addWidget(title)
        badge = self.badge = QLabel('DEMO · 合成 4 通道图像 · 16-bit · 0.5 µm/px · 256 × 256 µm  |  不用于真实实验结论')
        badge.setWordWrap(True)
        badge.setStyleSheet('color:#d6b188;padding:7px;background:#322a22;border-radius:4px')
        outer.addWidget(badge)
        split = QSplitter()
        outer.addWidget(split, 1)
        controls = QWidget()
        column = QVBoxLayout(controls)
        column.setContentsMargins(4, 4, 10, 4)
        load = QPushButton('载入 4 通道 TIFF（核通道在首位）')
        load.clicked.connect(self.load_image)
        column.addWidget(load)
        self._heading(column, '01  核分割与细胞区域')
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        column.addLayout(form)
        self.nuclear = self._spin(.02, .95, .16, .02)
        self.expansion = self._spin(0, 10, 3, .5)
        self.expansion.setSuffix(' µm')
        form.addRow('DAPI 核阈值（0–1）', self.nuclear)
        form.addRow('核边界向外扩张', self.expansion)
        hint = QLabel('DAPI 阈值＋连通域分割；外扩区域按最近细胞核归属，不重叠。细胞边界为估计。')
        hint.setWordWrap(True)
        column.addWidget(hint)
        self._heading(column, '02  Marker 与阳性阈值')
        self.names, self.thresholds, self.channels = [], [], []
        self.dapi = QCheckBox('显示 DAPI')
        self.dapi.setChecked(True)
        self.dapi.toggled.connect(self.draw)
        column.addWidget(self.dapi)
        for name, color in zip(('CD3', 'CD8', 'CD68'), ('#36d578', '#f16779', '#edbd48')):
            row = QHBoxLayout()
            check = QCheckBox()
            check.setChecked(True)
            check.setStyleSheet('color:'+color)
            edit = QLineEdit(name)
            edit.setStyleSheet(f'border-left:3px solid {color};')
            edit.setMinimumWidth(0)
            value = self._spin(0, 1, .2, .05)
            value.setFixedWidth(105)
            row_height = max(36, edit.sizeHint().height(), value.sizeHint().height())
            edit.setFixedHeight(row_height)
            value.setFixedHeight(row_height)
            row.addWidget(check)
            row.addWidget(edit, 1)
            row.addWidget(value)
            column.addLayout(row)
            self.names.append(edit)
            self.thresholds.append(value)
            self.channels.append(check)
            edit.editingFinished.connect(self.schedule)
            check.toggled.connect(self.draw)
        threshold_hint = QLabel('判定：外扩细胞区域的平均原始强度 / 65535 ≥ 阈值')
        threshold_hint.setWordWrap(True)
        column.addWidget(threshold_hint)
        self._heading(column, '03  ROI 与空间关系')
        self.roi = QComboBox()
        self.roi.addItems(['全视野', '左半区域', '右半区域', '中央区域'])
        column.addWidget(self.roi)
        neighborhood = QFormLayout()
        neighborhood.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.source = QComboBox()
        self.target = QComboBox()
        self.radius = self._spin(1, 100, 20, 5)
        self.radius.setSuffix(' µm')
        for label, widget in (('来源表型 A', self.source), ('目标表型 B', self.target), ('邻域半径', self.radius)):
            neighborhood.addRow(label, widget)
        column.addLayout(neighborhood)
        for widget in (self.nuclear, self.expansion, *self.thresholds, self.radius):
            widget.valueChanged.connect(self.schedule)
        self.roi.currentIndexChanged.connect(self.schedule)
        self.source.currentIndexChanged.connect(self.schedule)
        self.target.currentIndexChanged.connect(self.schedule)
        column.addStretch()
        export = QPushButton('导出细胞明细 CSV')
        export.clicked.connect(self.export_csv)
        column.addWidget(export)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setMinimumWidth(270)
        scroll.setMaximumWidth(360)
        split.addWidget(scroll)
        middle = QWidget()
        center = QVBoxLayout(middle)
        center.setContentsMargins(8, 4, 8, 4)
        tools = QHBoxLayout()
        self.overlay = QComboBox()
        self.overlay.addItems(['原始通道合成＋表型', '仅显示原始通道', '核与估计细胞边界'])
        self.overlay.currentIndexChanged.connect(self.draw)
        self.fit = QPushButton('适应窗口')
        self.fit.clicked.connect(self.fit_view)
        tools.addWidget(self.overlay, 1)
        tools.addWidget(self.fit)
        center.addLayout(tools)
        self.view = TissueView()
        self.view.cell_clicked.connect(self.select_cell)
        center.addWidget(self.view, 1)
        self.inspector = QLabel('点击细胞轮廓，查看该细胞的 marker 强度及邻域。滚轮缩放，拖动画面。')
        self.inspector.setWordWrap(True)
        center.addWidget(self.inspector)
        split.addWidget(middle)
        right = QWidget()
        right.setMinimumWidth(300)
        results = QVBoxLayout(right)
        results.setContentsMargins(6, 4, 0, 4)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet('font-size:14px;color:#dfc5a9;padding:8px;background:#292724')
        results.addWidget(self.summary)
        tabs = QTabWidget()
        self.phenotypes = self._table(('表型', '数量', '比例', '个/mm²'))
        self.cells_table = self._table(('ID', '表型', 'X µm', 'Y µm'))
        self.neighbors = self._table(('A 细胞', '最近 B / µm', '半径内 B 数'))
        tabs.addTab(self.phenotypes, '表型')
        tabs.addTab(self.cells_table, '细胞')
        tabs.addTab(self.neighbors, '邻域')
        self.cells_table.cellClicked.connect(lambda row, col: self.select_cell(int(self.cells_table.item(row, 0).text())))
        results.addWidget(tabs, 1)
        legend = QLabel('表型表只列阳性 marker，未列者为阴性；悬停查看完整组合。')
        legend.setWordWrap(True)
        legend.setStyleSheet('color:#aaa297;font-size:11px')
        results.addWidget(legend)
        self.metrics = QLabel()
        self.metrics.setWordWrap(True)
        results.addWidget(self.metrics)
        self.histogram = QLabel()
        self.histogram.setMinimumHeight(125)
        results.addWidget(self.histogram)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 660, 360])
        note = QLabel('口径：细胞质心落入 ROI 才计入；距离以核质心计算，排除自身；邻居仅限当前 ROI。199 次标签置换，固定坐标与表型数量；未做空间边界校正，结果仅用于演示。')
        note.setWordWrap(True)
        note.setStyleSheet('color:#aaa297;font-size:11px')
        outer.addWidget(note)
        self.recalculate()
        QTimer.singleShot(0, self.fit_view)

    @staticmethod
    def _heading(layout, text):
        label = QLabel(text)
        label.setStyleSheet('color:#d6b188;font-weight:600;padding-top:10px;padding-bottom:3px')
        layout.addWidget(label)

    @staticmethod
    def _spin(low, high, value, step):
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(2)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        # Keep these compact summary tables proportional; the application's
        # wide result-table policy otherwise expands each column to 96 px.
        table.setProperty('workflowGroupTable', True)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setWordWrap(True)
        return table

    @staticmethod
    def _fill(table, rows):
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value)))
        table.resizeRowsToContents()

    def schedule(self, *_):
        self._timer.start(180)

    def marker_names(self):
        return tuple(edit.text().strip() or f'Marker {i+1}' for i, edit in enumerate(self.names))

    def load_image(self):
        path, _ = QFileDialog.getOpenFileName(self, '载入 4 通道原始 TIFF', '', 'TIFF (*.tif *.tiff)')
        if not path:
            return
        try:
            from core.image_analysis.io_engine import read_microscopy_image
            imported = read_microscopy_image(path)
            image = imported.analysis_data
            if image.ndim != 3 or image.shape[0] != 4 or image.dtype != np.uint16:
                raise ValueError('此 Demo 需要 4 个独立 uint16 通道：首通道为细胞核，后 3 个为 marker。RGB 合成图不能替代原始通道。')
            sx = imported.metadata.get('pixel_size_x_um') or imported.metadata.get('pixel_size_um')
            sy = imported.metadata.get('pixel_size_y_um') or sx
            scale = float(sx) if sx and sy and np.isclose(sx, sy) else None
            self.set_image(image, pixel_um=scale, names=imported.channel_names[1:4],
                           source_label=Path(path).name, data_type='RAW_TIFF_DEMO')
        except Exception as exc:
            QMessageBox.warning(self, '载入失败', str(exc))

    def set_image(self, image, *, pixel_um=None, names=(), source_label='原始图像', data_type='RAW_TIFF_DEMO'):
        """Keep the source dimensions; uncalibrated images report pixel units."""
        if image.ndim != 3 or image.shape[0] != 4 or image.dtype != np.uint16:
            raise ValueError('Expected four uint16 channels')
        if pixel_um is not None and (not np.isfinite(pixel_um) or pixel_um <= 0):
            raise ValueError('Invalid pixel scale')
        self._timer.stop()
        self.image = image
        self.pixel_um = float(pixel_um) if pixel_um is not None else 1.
        self.unit = 'µm' if pixel_um is not None else 'px'
        self.data_type, self.source_label = data_type, source_label
        self.selected_id = None
        for i, edit in enumerate(self.names):
            edit.setText(names[i] if i < len(names) else f'Marker {i+1}')
        for spin in (self.expansion, self.radius):
            spin.setSuffix(' '+self.unit)
        self.cells_table.setHorizontalHeaderLabels(('ID', '表型', 'X '+self.unit, 'Y '+self.unit))
        self.neighbors.setHorizontalHeaderLabels(('A 细胞', '最近 B / '+self.unit, '半径内 B 数'))
        self.phenotypes.setHorizontalHeaderLabels(('表型', '数量', '比例', '个/mm²' if pixel_um else '个/10⁶ px²'))
        h, w = image.shape[1:]
        for index in (1, 2):
            self.roi.model().item(index).setEnabled(w > 1)
        calibration = f'{self.pixel_um:g} µm/px' if pixel_um else '未标定；距离以像素计'
        self.badge.setText(f'DEMO · {source_label} · {w} × {h} px · 4 通道 · {calibration} | 阈值连通域演示，尚未验证生物学准确性')
        self.roi.setCurrentIndex(0)
        self.recalculate()
        self.fit_view()

    def short_name(self, code):
        return ' '.join(name+'+' for i,name in enumerate(self.marker_names()) if code & (1 << i)) or '全部阴性'

    def recalculate(self):
        h, w = self.image.shape[1:]
        roi = ((0, 0, w, h), (0, 0, max(1,w//2), h), (w//2, 0, w, h), (w//4, h//4, max(w//4+1,3*w//4), max(h//4+1,3*h//4)))[self.roi.currentIndex()]
        self.result = analyze(self.image, pixel_um=self.pixel_um, nuclear_threshold=self.nuclear.value(), expansion_um=self.expansion.value(),
                              thresholds=[s.value() for s in self.thresholds], roi=roi)
        r = self.result
        names = self.marker_names()
        for combo, default in ((self.source, 3), (self.target, 4)):
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for code in range(8):
                combo.addItem(phenotype_name(code, names), code)
            combo.setCurrentIndex(combo.findData(default if previous is None else previous))
            combo.blockSignals(False)
        self.spatial = spatial_metrics(r, self.source.currentData(), self.target.currentData(), self.radius.value())
        count = Counter(r.phenotype[r.selected].tolist())
        total = int(r.selected.sum())
        area_unit = 'mm²' if self.unit == 'µm' else '10⁶ px²'
        self.summary.setText(f'ROI 内 {total} 个细胞 / 全图 {len(r.ids)} 个\nROI 面积 {r.area_um2/1e6:.4f} {area_unit}\n细胞密度 {total/(r.area_um2/1e6):,.0f} 个/{area_unit}')
        present = [code for code in range(8) if count[code]]
        self._fill(self.phenotypes, [(self.short_name(code), count[code], f'{count[code]/max(1,total):.1%}', f'{count[code]/(r.area_um2/1e6):,.0f}') for code in present])
        for row,code in enumerate(present):
            self.phenotypes.item(row,0).setToolTip(phenotype_name(code,names))
            self.phenotypes.item(row,0).setForeground(QColor(COLORS[code]))
        self._fill(self.cells_table, [(r.ids[i], self.short_name(r.phenotype[i]), f'{r.xy[i,0]:.1f}', f'{r.xy[i,1]:.1f}') for i in np.flatnonzero(r.selected)])
        m = self.spatial
        self._fill(self.neighbors, [(cell, f'{distance:.1f}' if np.isfinite(distance) else '—', n) for cell, distance, n in zip(m['source_ids'], m['nearest'], m['neighbors'])])
        values = m['nearest'][np.isfinite(m['nearest'])]
        if len(values):
            p = '—' if m['p'] is None else f"{m['p']:.3f}"
            baseline = '—' if m['null_mean'] is None else f"{m['null_mean']:.1f} {self.unit}"
            self.metrics.setText(f"A → B  |  A={m['source_n']}，B={m['target_n']}\n最近邻均值 {values.mean():.1f} {self.unit} · 中位数 {np.median(values):.1f} {self.unit}\n{self.radius.value():g} {self.unit} 内有 B 邻居的 A：{np.mean(m['neighbors']>0):.1%}\n每个 A 的平均 B 邻居数：{m['neighbors'].mean():.2f}\n标签置换均值 {baseline} · 双侧 p={p}")
        else:
            self.metrics.setText(f"A={m['source_n']}，B={m['target_n']}\n当前 ROI 中缺少可配对细胞，无法计算距离。")
        self.draw_histogram(values)
        if self.selected_id not in r.ids[r.selected]:
            available = m['source_ids'] if len(m['source_ids']) else r.ids[r.selected]
            self.selected_id = int(available[0]) if len(available) else None
        self.draw()

    def draw_histogram(self, values):
        pix = QPixmap(330, 125)
        pix.fill(QColor('#242423'))
        painter = QPainter(pix)
        painter.setPen(QColor('#d8d1c7'))
        painter.drawText(10, 18, 'A → B  最近邻距离分布 / '+self.unit)
        if len(values):
            counts, edges = np.histogram(values, bins=10)
            for i, count in enumerate(counts):
                height = int(68*count/max(1,counts.max()))
                painter.fillRect(14+i*30, 94-height, 24, height, QColor('#c58d6c'))
            painter.drawText(12, 114, f'{edges[0]:.1f}')
            painter.drawText(275, 114, f'{edges[-1]:.1f}')
        painter.end()
        self.histogram.setPixmap(pix)

    def fit_view(self):
        self.view.fit_image = True
        self.view.fitInView(self.view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def select_cell(self, cell_id):
        if cell_id not in self.result.ids[self.result.selected]:
            return
        self.selected_id = cell_id
        self.draw()

    def draw(self, *_):
        if not hasattr(self, 'result'):
            return
        r = self.result
        h, w = self.image.shape[1:]
        rgb = np.zeros((h, w, 3), float)
        colors = ((.25, .40, 1), (.15, 1, .35), (1, .15, .22), (1, .75, .12))
        for ch, enabled in enumerate((self.dapi.isChecked(), *(w.isChecked() for w in self.channels))):
            if enabled:
                rgb += self.image[ch,:,:,None]/65535*np.array(colors[ch])
        if self.overlay.currentIndex() == 2:
            rgb *= .28
            from scipy import ndimage as ndi
            for labels, color in ((r.cells, (.65,.65,.65)), (r.nuclei, (.25,.6,1))):
                edge = (labels > 0) & (labels != ndi.minimum_filter(labels, size=3))
                rgb[edge] = color
        pixels = np.ascontiguousarray(np.clip(rgb*320, 0, 255).astype(np.uint8))
        image = QImage(pixels.data, w, h, pixels.strides[0], QImage.Format.Format_RGB888).copy()
        scene = self.view.scene()
        scene.clear()
        scene.addPixmap(QPixmap.fromImage(image))
        scene.setSceneRect(0, 0, w, h)
        x0,y0,x1,y1 = r.roi
        if r.roi != (0,0,w,h):
            shade = QBrush(QColor(0,0,0,155))
            for rect in (QRectF(0,0,w,y0),QRectF(0,y1,w,h-y1),QRectF(0,y0,x0,y1-y0),QRectF(x1,y0,w-x1,y1-y0)):
                scene.addRect(rect, QPen(Qt.PenStyle.NoPen), shade)
            scene.addRect(QRectF(x0,y0,x1-x0,y1-y0), QPen(QColor('#f0dac0'),1.5,Qt.PenStyle.DashLine))
        if self.selected_id is not None:
            i = int(self.selected_id)-1
            x,y = r.xy[i]/self.pixel_um
            radius = self.radius.value()/self.pixel_um
            circle = scene.addEllipse(x-radius,y-radius,2*radius,2*radius,QPen(QColor('#fff1b1'),1.5,Qt.PenStyle.DashLine),QBrush(QColor(255,225,130,18)))
            circle.setZValue(1)
            targets = np.flatnonzero(r.selected & (r.phenotype == self.target.currentData()) & (r.ids != self.selected_id))
            if len(targets):
                nearest = targets[np.argmin(np.linalg.norm(r.xy[targets]-r.xy[i],axis=1))]
                nx,ny = r.xy[nearest]/self.pixel_um
                scene.addLine(x,y,nx,ny,QPen(QColor('#fff1b1'),1.2,Qt.PenStyle.DotLine))
            expression = ' · '.join(f'{name} {value:.3f}' for name,value in zip(self.marker_names(),r.intensities[i]))
            self.inspector.setText(f'细胞 #{self.selected_id} · {phenotype_name(r.phenotype[i],self.marker_names())}\n{expression}\n虚线圆：{self.radius.value():g} {self.unit} 邻域；点线：该细胞最近的 B。')
        else:
            self.inspector.setText('当前参数下没有可选细胞；降低 DAPI 阈值可恢复检测。')
        if self.overlay.currentIndex() != 1:
            for i in np.flatnonzero(r.selected):
                x,y = r.xy[i]/self.pixel_um
                pen = QPen(QColor(COLORS[r.phenotype[i]]), 1.4)
                item = scene.addEllipse(x-7,y-7,14,14,pen,QBrush(Qt.BrushStyle.NoBrush))
                item.setData(0,int(r.ids[i]))
                item.setZValue(3)
        length_px = min(100., w*.2)
        margin = min(w,h)*.04
        scene.addLine(w-margin-length_px,h-margin,w-margin,h-margin,QPen(QColor('white'),3))
        text = scene.addText(f'{length_px*self.pixel_um:g} {self.unit}')
        text.setDefaultTextColor(QColor('white'))
        text.setPos(w-margin-length_px,h-margin-26)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出 Demo 数据', 'ScieFig_DEMO_cells.csv', 'CSV (*.csv)')
        if not path:
            return
        r = self.result
        m = self.spatial
        spatial = {int(cell):(distance,n) for cell,distance,n in zip(m['source_ids'],m['nearest'],m['neighbors'])}
        try:
            with Path(path).open('w',newline='',encoding='utf-8-sig') as stream:
                writer = csv.writer(stream)
                names = self.marker_names()
                writer.writerow(['data_type','cell_id','roi','x_'+('um' if self.unit == 'µm' else 'px'),'y_'+('um' if self.unit == 'µm' else 'px'),'phenotype',*(n+'_mean_normalized' for n in names),'DAPI_threshold','expansion_'+('um' if self.unit == 'µm' else 'px'),*(n+'_threshold' for n in names),'neighbor_source','neighbor_target','radius_'+('um' if self.unit == 'µm' else 'px'),'nearest_B_'+('um' if self.unit == 'µm' else 'px'),'B_count_in_radius'])
                for i in np.flatnonzero(r.selected):
                    distance,count = spatial.get(int(r.ids[i]),(None,None))
                    writer.writerow([self.data_type,r.ids[i],self.roi.currentText(),*r.xy[i],phenotype_name(r.phenotype[i],names),*r.intensities[i],self.nuclear.value(),self.expansion.value(),*(s.value() for s in self.thresholds),self.source.currentText(),self.target.currentText(),self.radius.value(),distance if distance is not None and np.isfinite(distance) else '',count])
        except OSError as exc:
            QMessageBox.warning(self,'导出失败',str(exc))


def run_demo(stylesheet):
    import sys
    from PyQt6.QtWidgets import QApplication
    from ui.platform_support import configure_application
    app = QApplication.instance() or QApplication(sys.argv)
    configure_application(app)
    app.setStyleSheet(stylesheet)
    dialog = SpatialDemoDialog()
    dialog.show()
    code = app.exec()
    # Retain Qt objects until process exit, as in the main Windows application.
    import os
    os._exit(code)
