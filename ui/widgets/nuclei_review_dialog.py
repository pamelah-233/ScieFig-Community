"""Linked outline preview, per-fiber distribution, and explicit nuclear review."""
import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QSplitter,QTabWidget,QTableWidget,
    QTableWidgetItem,QAbstractItemView,QHeaderView,QLabel,QPushButton,QComboBox,QSpinBox,QWidget)
from core.image_browser import rgb_to_qimage
from core.utils.i18n import bind_i18n,tr
from ui.widgets.preview_canvas import PreviewCanvas


class NucleiReviewDialog(QDialog):
    def __init__(self,controller):
        super().__init__(controller.window)
        self.controller=controller;self.filling=False;self.overlay=None
        self.setWindowTitle(tr('nuclei_review'));self.resize(1280,850)
        layout=QVBoxLayout(self)
        self.hint=QLabel();self.hint.setWordWrap(True);layout.addWidget(self.hint)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.original_button=bind_i18n(QPushButton(),'nuclei_review_original')
        self.original_button.setCheckable(True);self.original_button.toggled.connect(self.full_preview)
        layout.addWidget(self.original_button)
        self.geometry_button=bind_i18n(QPushButton(),'nuclei_review_geometry')
        self.geometry_button.clicked.connect(controller.open_geometry_review);layout.addWidget(self.geometry_button)
        splitter=QSplitter(Qt.Orientation.Horizontal);self.preview=PreviewCanvas();splitter.addWidget(self.preview)
        right=QWidget();right_layout=QVBoxLayout(right);self.tabs=QTabWidget()
        self.distribution_table=self.table(['Central nuclei / fiber','Fibers','%'])
        self.fiber_table=self.table(['Fiber ID','Central nuclei','Peripheral nuclei','Review','Included','Edge'])
        self.nucleus_table=self.table(['Nucleus ID','Class','Fiber ID','X (px)','Y (px)','Area (px²)','Edge','Signal class','Corrected intensity'])
        self.tabs.addTab(self.distribution_table,tr('nuclei_distribution'))
        self.tabs.addTab(self.fiber_table,tr('nuclei_fibers'))
        self.tabs.addTab(self.nucleus_table,tr('nuclei_objects'))
        right_layout.addWidget(self.tabs)
        edits=QHBoxLayout();self.category=QComboBox();self.fiber_id=QSpinBox();self.fiber_id.setRange(0,1000000)
        edits.addWidget(self.category);edits.addWidget(self.fiber_id)
        self.apply_button=bind_i18n(QPushButton(),'nuclei_apply_review');edits.addWidget(self.apply_button);right_layout.addLayout(edits)
        self.status=QLabel();self.status.setWordWrap(True);right_layout.addWidget(self.status)
        full=bind_i18n(QPushButton(),'nuclei_full_image');full.clicked.connect(self.full_preview);right_layout.addWidget(full)
        self.legend=QLabel();self.legend.setWordWrap(True);right_layout.addWidget(self.legend)
        splitter.addWidget(right);splitter.setSizes([700,550]);layout.addWidget(splitter,1)
        self.apply_button.clicked.connect(self.apply_review)
        self.nucleus_table.itemSelectionChanged.connect(self.select_nucleus)
        self.fiber_table.itemSelectionChanged.connect(self.select_fiber)
        self.fiber_table.itemChanged.connect(self.fiber_checked)
        self.retranslate()

    @staticmethod
    def table(headers):
        table=QTableWidget(0,len(headers));table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        return table

    def refresh(self):
        result=self.controller.current_result()
        if result is None:self.close();return
        self.filling=True
        muscle=result['task']=='muscle'
        bind_i18n(self.hint,'nuclei_review_hint' if muscle else 'nuclei_count_review_hint')
        bind_i18n(self.legend,'nuclei_legend' if muscle else 'nuclei_count_legend')
        self.nucleus_table.setColumnHidden(2,not muscle)
        for column in (7,8):self.nucleus_table.setColumnHidden(column,not bool(result.get('signal_classification')))
        self.summary.setText(self.controller.summary_text(result))
        self.tabs.setTabVisible(0,muscle);self.tabs.setTabVisible(1,muscle)
        if not muscle:self.tabs.setCurrentIndex(2)
        self.fiber_id.setVisible(muscle)
        self.category.clear()
        for key in (['central','peripheral','outside','uncertain','excluded'] if muscle else ['included','excluded']):
            self.category.addItem(tr('nuclei_'+key),key)
        self.fill(self.distribution_table,[[r['central_nuclei'],r['fiber_count'],f"{r['percentage']:.2f}"] for r in result.get('summary',{}).get('distribution',[])])
        self.fill(self.fiber_table,[[r['fiber_id'],r['central_nuclei'],r['peripheral_nuclei'],r['uncertain_nuclei'],'',r['border_truncated']] for r in result.get('fibers',[])])
        for i,row in enumerate(result.get('fibers',[])):
            item=self.fiber_table.item(i,4);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row['include_in_statistics'] else Qt.CheckState.Unchecked)
        self.fill(self.nucleus_table,[[r['object_id'],tr('nuclei_'+r['classification']),r.get('fiber_id') or '',
            f"{r['center_x_px']:.1f}",f"{r['center_y_px']:.1f}",r['area_px'],tr('yes' if r['border_truncated'] else 'no'),
            tr('signal_'+r['signal_class']) if r.get('signal_class') else '',
            f"{r['background_corrected_mean']:.3f}" if r.get('background_corrected_mean') is not None else ''] for r in result['nuclei']])
        self.overlay=self.controller.overlay_rgb();self.full_preview();self.filling=False

    @staticmethod
    def fill(table,rows):
        table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):table.setItem(i,j,QTableWidgetItem(str(value)))

    def full_preview(self):
        if self.overlay is not None:
            image=self.controller.source_rgb() if self.original_button.isChecked() else self.overlay
            self.preview.set_pixmap(QPixmap.fromImage(rgb_to_qimage(image)))

    def focus(self,row):
        points=np.asarray(row['contour']);height,width=self.overlay.shape[:2]
        left,top=points.min(axis=0);right,bottom=points.max(axis=0)
        margin=max(40,int(max(right-left,bottom-top)*.5))
        image=self.controller.source_rgb() if self.original_button.isChecked() else self.overlay
        crop=image[max(0,top-margin):min(height,bottom+margin+1),max(0,left-margin):min(width,right+margin+1)]
        self.preview.set_pixmap(QPixmap.fromImage(rgb_to_qimage(crop)))

    def selected_nucleus(self):
        index=self.nucleus_table.currentRow()
        return self.controller.current_result()['nuclei'][index] if index>=0 else None

    def select_nucleus(self):
        if self.filling:return
        row=self.selected_nucleus()
        if row is None:return
        self.category.setCurrentIndex(self.category.findData(row['classification']))
        self.fiber_id.setValue(row.get('fiber_id') or 0);self.focus(row)

    def select_fiber(self):
        if self.filling:return
        index=self.fiber_table.currentRow()
        if index>=0:self.focus(self.controller.current_result()['fibers'][index])

    def apply_review(self):
        row=self.selected_nucleus()
        if row is None:return
        try:self.controller.reclassify(row['object_id'],self.category.currentData(),self.fiber_id.value() or None)
        except ValueError as error:self.status.setText(str(error))

    def fiber_checked(self,item):
        if self.filling or item.column()!=4:return
        identifier=int(self.fiber_table.item(item.row(),0).text())
        self.controller.set_fiber_included(identifier,item.checkState()==Qt.CheckState.Checked)

    def retranslate(self):
        self.setWindowTitle(tr('nuclei_review'))
        for table,key in [(self.distribution_table,'nuclei_distribution_headers'),(self.fiber_table,'nuclei_fiber_headers'),(self.nucleus_table,'nuclei_object_headers')]:
            table.setHorizontalHeaderLabels(tr(key).split('|'))
        for index,key in enumerate(['nuclei_distribution','nuclei_fibers','nuclei_objects']):self.tabs.setTabText(index,tr(key))
        self.refresh()

