"""Read-only spatial results and overlays on the existing fluorescence canvas."""
import json
import numpy as np
from PyQt6.QtCore import Qt, QRectF, QEvent
from PyQt6.QtGui import QColor, QPixmap, QPainter, QPen, QBrush, QPainterPath
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QWidget, QSplitter, QComboBox, QPushButton, QScrollArea
from core.utils.i18n import tr
from ui.widgets.fluoro_canvas import ImageCoordinateMapper


class SpatialOverview(QWidget):
    """Persistent, linked results beside the native image; narrow windows stack it below."""
    def __init__(self, controller):
        super().__init__(); self.controller=controller; self._key=None
        self.setMinimumWidth(0)
        layout=QVBoxLayout(self);layout.setContentsMargins(10,8,10,8);layout.setSpacing(7)
        self.title=QLabel();self.title.setWordWrap(True);layout.addWidget(self.title)
        self.mode=QComboBox();layout.addWidget(self.mode)
        self.mode.currentIndexChanged.connect(lambda i:controller.controls.overlay.setCurrentIndex(i))
        controller.controls.overlay.currentIndexChanged.connect(self.mode.setCurrentIndex)
        self.metrics=QLabel();self.metrics.setWordWrap(True);layout.addWidget(self.metrics)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1);self.tables={}
        for key in ('spatial_phenotypes','spatial_cells','spatial_neighbors'):
            table=QTableWidget();table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);table.setMinimumHeight(65)
            table.verticalHeader().hide();table.setWordWrap(True)
            table.cellClicked.connect(lambda row,col,t=table:controller.select_cell(t.item(row,col).data(Qt.ItemDataRole.UserRole))
                if t.item(row,col) and t.item(row,col).data(Qt.ItemDataRole.UserRole) is not None else None)
            self.tabs.addTab(table,tr(key));self.tables[key]=table
        self.graph=QLabel();layout.addWidget(self.graph)
        self.inspector=QLabel();self.inspector.setWordWrap(True);layout.addWidget(self.inspector)
        self.details=QPushButton();self.details.clicked.connect(controller.open_review);layout.addWidget(self.details)
        workspace=controller.window.fluoro_workspace;canvas=workspace.canvas
        workspace.layout().removeWidget(canvas)
        self.splitter=QSplitter(Qt.Orientation.Horizontal,workspace)
        self.splitter.addWidget(canvas);self.splitter.addWidget(self)
        self.splitter.setChildrenCollapsible(False);self.splitter.setStretchFactor(0,1);self.splitter.setStretchFactor(1,0)
        workspace.layout().addWidget(self.splitter,1);self.hide()
        workspace.installEventFilter(self);self.workspace=workspace

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Resize and self.isVisible():
            self.arrange();self.refresh(True)
        return False

    def arrange(self):
        wide=self.workspace.width()>=900
        orientation=Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical
        if self.splitter.orientation()!=orientation:self.splitter.setOrientation(orientation)
        self.setMaximumHeight(16777215 if wide else 300)
        self.setMaximumWidth(330 if wide else 16777215)
        self.graph.setVisible(wide);self.inspector.setVisible(wide)
        self.title.setVisible(wide)
        self.splitter.setSizes([max(250,self.workspace.width()-310),310] if wide else [max(200,self.workspace.height()-280),280])

    def refresh(self,visible):
        was_visible=self.isVisible();self.setVisible(visible)
        if not visible:return
        if not was_visible:self.arrange()
        c=self.controller;r=c.current_result();fresh=c.is_fresh(r) and c.parameters_match(r)
        self.title.setText(tr('task_spatial'))
        current=self.mode.currentIndex();self.mode.blockSignals(True);self.mode.clear()
        for i in range(c.controls.overlay.count()):self.mode.addItem(c.controls.overlay.itemText(i))
        self.mode.setCurrentIndex(c.controls.overlay.currentIndex());self.mode.blockSignals(False)
        self.details.setText(tr('spatial_results'));self.details.setEnabled(fresh)
        self.inspector.setText(c.controls.inspector.text())
        summary=c.summary_text(r)
        if fresh and self.workspace.width()<900:
            s=r['summary'];summary=f"{s['cells']} · {tr('spatial_cells')} | {len(r['phenotypes'])} · {tr('spatial_phenotypes')}\nA → B: {s['nearest_mean']:.2f}" if s['nearest_mean'] is not None else f"{s['cells']} · {tr('spatial_cells')} | {len(r['phenotypes'])} · {tr('spatial_phenotypes')}\n{tr('spatial_no_pairs')}"
            fm=self.metrics.fontMetrics();width=max(100,self.width()-20)
            pairs=[fm.elidedText(label+str(r['parameters'].get(key) or '—').replace(' / ',' '),Qt.TextElideMode.ElideRight,width)
                   for label,key in (('A: ','source_phenotype'),('B: ','target_phenotype'))]
            summary='\n'.join(pairs)+'\n'+summary
        self.metrics.setText(summary);self.metrics.setToolTip(c.summary_text(r))
        key=(id(r),fresh,tr('task_spatial'))
        if key==self._key:return
        self._key=key
        rows=r['object_table'] if fresh else []
        colors={row['phenotype']:row['overlay_color'] for row in rows}
        sets=[(r.get('phenotypes',[]) if fresh else [],('phenotype','cells','fraction')),
            (rows,('object_id','phenotype','niche')),
            ([row for row in rows if row.get('target_count_in_radius') is not None],('object_id','nearest_target','target_count_in_radius'))]
        for i,(key,(records,fields)) in enumerate(zip(self.tables,sets)):
            table=self.tables[key];self.tabs.setTabText(i,tr(key));table.setColumnCount(len(fields));table.setRowCount(len(records))
            unit='µm' if fresh and r['summary']['length_unit']=='um' else 'px'
            table.setHorizontalHeaderLabels([tr('spatial_col_'+f) if f!='nearest_target' else 'A → B / '+unit for f in fields])
            for j,f in enumerate(fields):table.horizontalHeader().setSectionResizeMode(j,QHeaderView.ResizeMode.Stretch if f=='phenotype' else QHeaderView.ResizeMode.ResizeToContents)
            for row,record in enumerate(records):
                for col,field in enumerate(fields):
                    value=record.get(field);text='—' if value is None else f'{value:.1%}' if field=='fraction' else f'{value:.2f}' if isinstance(value,float) else str(value)
                    if field=='phenotype':text=text.replace(' / ','\n')
                    item=QTableWidgetItem(text);item.setToolTip(text);item.setData(Qt.ItemDataRole.UserRole,record.get('object_id'))
                    shade=record.get('overlay_color',colors.get(record.get('phenotype'),'#aaaaaa'))
                    if record.get('include_in_statistics') is False:shade='#777777'
                    item.setForeground(QColor(shade))
                    table.setItem(row,col,item)
            table.resizeRowsToContents()
        self.graph.setPixmap(histogram(r.get('nearest_distances',[]) if fresh else [],'µm' if fresh and r['summary']['length_unit']=='um' else 'px',width=280))


def histogram(values, unit, width=480):
    pixmap = QPixmap(width, 145); pixmap.fill(QColor('#242423'))
    painter = QPainter(pixmap); painter.setPen(QColor('#e2d6c9'))
    painter.drawText(10, 20, tr('spatial_histogram')+' / '+unit)
    if len(values):
        counts, edges = np.histogram(values, bins=10)
        stride = (width-30)/10
        for i, count in enumerate(counts):
            height = 75*count/max(1, counts.max())
            painter.fillRect(QRectF(15+i*stride, 110-height, stride-5, height), QColor('#c58d6c'))
        painter.drawText(15, 135, f'{edges[0]:.2f}'); painter.drawText(width-80, 135, f'{edges[-1]:.2f}')
    else: painter.drawText(15, 80, tr('spatial_no_pairs'))
    painter.end(); return pixmap


class SpatialResultsDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(tr('task_spatial')); self.resize(1120, 760)
        layout = QVBoxLayout(self)
        top = QHBoxLayout(); layout.addLayout(top)
        self.caption = QLabel(controller.summary_text(controller.current_result())); self.caption.setWordWrap(True); top.addWidget(self.caption, 1)
        result = controller.current_result(); unit = 'µm' if result['summary']['length_unit'] == 'um' else 'px'
        graph = QLabel(); graph.setPixmap(histogram(result.get('nearest_distances', []), unit)); top.addWidget(graph)
        self.inspector = QLabel(tr('spatial_inspect_hint')); self.inspector.setWordWrap(True); layout.addWidget(self.inspector)
        tabs = QTabWidget(); layout.addWidget(tabs, 1); self.tables = {}
        cells = controller.cell_rows(result)
        colors={row['phenotype']:row['overlay_color'] for row in result['object_table']}
        sets = [('spatial_phenotypes', result['phenotypes']), ('spatial_cells', cells),
                ('spatial_neighbors', [r for r in cells if r.get('target_count_in_radius') is not None]),
                ('spatial_niches', result.get('niches', [])), ('spatial_groups', controller.group_rows())]
        for key, rows in sets:
            fields = list(rows[0]) if rows else []
            table = QTableWidget(len(rows), len(fields)); table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); table.setSortingEnabled(False)
            table.setHorizontalHeaderLabels([tr('spatial_col_'+f) if tr('spatial_col_'+f) != 'spatial_col_'+f else f for f in fields])
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            for i, row in enumerate(rows):
                for j, field in enumerate(fields):
                    value = row.get(field, ''); value = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                    if isinstance(value, float): value = f'{value:.6g}'
                    item = QTableWidgetItem('—' if value is None else str(value)); item.setToolTip(item.text())
                    shade=colors.get(row.get('phenotype'))
                    if row.get('include_in_statistics') is False:shade='#777777'
                    if shade:item.setForeground(QColor(shade))
                    item.setData(Qt.ItemDataRole.UserRole, row.get('object_id')); table.setItem(i, j, item)
            table.resizeColumnsToContents(); table.setSortingEnabled(True)
            table.cellClicked.connect(lambda row, col, t=table: controller.select_cell(t.item(row, col).data(Qt.ItemDataRole.UserRole))
                                      if t.item(row, col).data(Qt.ItemDataRole.UserRole) is not None else None)
            tabs.addTab(table, tr(key)); self.tables[key] = table
        note = QLabel(tr('spatial_result_note')); note.setWordWrap(True); layout.addWidget(note)


def draw_spatial_overlay(canvas, result, mode, selected_id):
    canvas.clear_measurement_overlay()
    if mode == 'original': return
    item = canvas._item_for_channel(result.get('channel_index'))
    if item is None: return
    mapper = ImageCoordinateMapper(item, canvas._analysis_image_shape())
    overlays = []
    for row in result['object_table']:
        shade = row.get('overlay_color', '#a0a0a0') if row.get('include_in_statistics') else '#777777'
        if mode == 'niche':
            colors = ('#777777', '#3ED995', '#F5A65B', '#BE91EA', '#40C7DF', '#EE7B96', '#DCCF61', '#89A7F7')
            shade = colors[row.get('niche', 0) % len(colors)]
        def outline(points, color, role):
            if not points: return
            points = [p if isinstance(p, dict) else dict(x=p[0], y=p[1]) for p in points]
            graphic = canvas._ai_object_overlay_item(mapper, dict(contour_points_px=points), QColor(color))
            graphic.setData(0, role); graphic.setData(1, result['measurement_uuid']); graphic.setData(2, row['object_id'])
            overlays.append(graphic)
        if mode == 'boundaries':
            outline(row.get('cell_contour_points_px'), '#9e9e9e', 'spatial_cell_boundary')
            outline(row.get('contour_points_px'), '#62b9ff', 'spatial_nuclear_boundary')
        else: outline(row.get('contour_points_px'), shade, 'spatial_phenotype')
    chosen = next((r for r in result['object_table'] if r['object_id'] == selected_id), None)
    if chosen is not None:
        scale = result['parameters'].get('pixel_size_xy') or (1., 1.)
        radius = result['parameters']['radius']; x, y = chosen['center_x_px'], chosen['center_y_px']
        a = mapper.image_px_to_local_pos((x-radius/scale[0], y-radius/scale[1]))
        b = mapper.image_px_to_local_pos((x+radius/scale[0], y+radius/scale[1]))
        from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsLineItem
        circle = QGraphicsEllipseItem(QRectF(a, b), item); pen = QPen(QColor('#fff1aa'), 1.6, Qt.PenStyle.DashLine); pen.setCosmetic(True)
        circle.setPen(pen); circle.setBrush(QBrush(QColor(255, 225, 120, 15))); circle.setZValue(513)
        circle.setAcceptedMouseButtons(Qt.MouseButton.NoButton); overlays.append(circle)
        paired = chosen.get('include_in_statistics') and chosen['phenotype'] == result['parameters'].get('source_phenotype')
        target_id = chosen.get('nearest_target_id') if paired and chosen.get('nearest_target') is not None else None
        target = next((r for r in result['object_table'] if r['object_id'] == target_id), None)
        if target:
            a = mapper.image_px_to_local_pos((x, y)); b = mapper.image_px_to_local_pos((target['center_x_px'], target['center_y_px']))
            line = QGraphicsLineItem(a.x(), a.y(), b.x(), b.y(), item)
            pen = QPen(QColor('#fff1aa'), 1.6, Qt.PenStyle.DotLine); pen.setCosmetic(True); line.setPen(pen)
            line.setZValue(514); line.setAcceptedMouseButtons(Qt.MouseButton.NoButton); overlays.append(line)
    scope = result.get('roi_snapshot')
    if scope:
        from PyQt6.QtWidgets import QGraphicsPathItem
        geometry = scope['geometry']; path = QPainterPath()
        if scope['type'] == 'rectangle':
            x,y,w,h = [geometry.get(k, 0) for k in ('x','y','w','h')]
            points = [(x,y), (x+w,y), (x+w,y+h), (x,y+h)]
        else:
            points = geometry.get('points', [])
        if points:
            def pos(p): return mapper.image_px_to_local_pos((p['x'],p['y']) if isinstance(p,dict) else p)
            path.moveTo(pos(points[0]))
            for point in points[1:]: path.lineTo(pos(point))
            path.closeSubpath()
        elif scope['type'] == 'ellipse':
            x,y,rx,ry = [geometry.get(k, 0) for k in ('cx','cy','rx','ry')]
            path.addEllipse(QRectF(mapper.image_px_to_local_pos((x-rx,y-ry)), mapper.image_px_to_local_pos((x+rx,y+ry))))
        graphic = QGraphicsPathItem(path, item); pen = QPen(QColor('#ffffff'), 1.5, Qt.PenStyle.DashLine); pen.setCosmetic(True)
        graphic.setPen(pen); graphic.setZValue(515); graphic.setAcceptedMouseButtons(Qt.MouseButton.NoButton); overlays.append(graphic)
    canvas._measurement_overlay_items = overlays; canvas._measurement_overlay_item = overlays[0] if overlays else None
    canvas.active_measurement_overlay_uuid = 'spatial:'+result['measurement_uuid']
    canvas.set_measurement_overlay_visible(True); canvas.measurement_overlay_changed.emit(bool(overlays))
