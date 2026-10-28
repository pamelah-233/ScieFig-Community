"""Temporary white-patch picking without changing scientific measurement ROIs."""
from PyQt6 import sip
from PyQt6.QtCore import QObject,QEvent,QRect,Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QGroupBox,QVBoxLayout,QLabel,QPushButton,QRubberBand
from pathlib import Path
from PyQt6.QtWidgets import QFileDialog
from core.utils.recent_directory import recent_directory,remember_file_directory
from core.utils.i18n import bind_i18n,tr
from core.plotting.histology_display import sample_white_balance,white_balanced_qimage


class HistologyWhiteBalanceController(QObject):
    def __init__(self,workspace):
        super().__init__(workspace)
        self.workspace=workspace;self.pending=None;self.label=None;self.start=None;self.rubber=None
        self.widget=bind_i18n(QGroupBox(),'histology_white_balance','title')
        layout=QVBoxLayout(self.widget)
        self.hint=bind_i18n(QLabel(),'histology_white_balance_hint');self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.pick_button=bind_i18n(QPushButton(),'histology_white_pick');self.pick_button.setCheckable(True)
        self.auto_button=bind_i18n(QPushButton(),'histology_white_auto')
        self.reset_button=bind_i18n(QPushButton(),'reset')
        self.export_button=bind_i18n(QPushButton(),'histology_white_export')
        self.export_button.setEnabled(False)
        for button in (self.pick_button,self.auto_button,self.reset_button,self.export_button):layout.addWidget(button)
        self.pick_button.clicked.connect(self.begin)
        self.auto_button.clicked.connect(lambda:self.apply_region(None))
        self.reset_button.clicked.connect(self.reset)
        self.export_button.clicked.connect(self.choose_export)
        workspace.canvas.display_transform=self.transform

    def image(self):
        panel=self.workspace.panel
        return panel.active_image() if panel else None

    def identity(self):
        return (id(self.workspace.panel),(self.image() or {}).get('uid'))

    def sync(self):
        if self.pending and self.pending!=self.identity():self.cancel()
        self.widget.setEnabled(self.image() is not None)
        self.export_button.setEnabled(bool((self.image() or {}).get('display_white_balance')))

    def transform(self,pixmap):
        settings=(self.image() or {}).get('display_white_balance')
        return QPixmap.fromImage(white_balanced_qimage(pixmap.toImage(),settings)) if settings else pixmap

    def cancel(self):
        if self.label is not None and not sip.isdeleted(self.label):
            self.label.removeEventFilter(self);self.label.unsetCursor()
        if self.rubber is not None and not sip.isdeleted(self.rubber):self.rubber.hide();self.rubber.deleteLater()
        self.pending=None;self.start=None;self.label=None;self.rubber=None
        self.pick_button.setChecked(False)

    def begin(self,checked=True):
        self.cancel()
        if not checked or self.image() is None:return
        self.workspace.show_original_preview()
        self.label=self.workspace.canvas.image_label
        if self.label is None:return
        self.pending=self.identity();self.pick_button.setChecked(True)
        self.label.installEventFilter(self);self.label.setCursor(Qt.CursorShape.CrossCursor)
        self.label.setFocus();self.rubber=QRubberBand(QRubberBand.Shape.Rectangle,self.label)
        self.hint.setText(tr('histology_white_pick_hint'))

    def eventFilter(self,watched,event):
        if watched is not self.label or self.pending is None:return False
        if self.pending!=self.identity():self.cancel();return False
        if event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape:
            self.cancel();return True
        if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
            self.start=event.position().toPoint();return True
        if event.type()==QEvent.Type.MouseMove and self.start is not None:
            self.rubber.setGeometry(QRect(self.start,event.position().toPoint()).normalized());self.rubber.show();return True
        if event.type()==QEvent.Type.MouseButtonRelease and self.start is not None:
            rectangle=watched._map_label_rect_to_source(QRect(self.start,event.position().toPoint()).normalized())
            self.cancel()
            self.apply_region(dict(x=rectangle.x(),y=rectangle.y(),w=rectangle.width(),h=rectangle.height()))
            return True
        return False

    def apply_region(self,geometry):
        self.cancel()
        image=self.image()
        if image is None:return
        try:
            _,rgb=self.workspace.panel._load_image_rgb(image['uid'])
            settings=sample_white_balance(rgb,geometry)
        except (ValueError,KeyError,OSError) as error:
            self.hint.setText(str(error));return
        image['display_white_balance']=settings
        self.workspace.panel.append_audit('display_white_balance',str(image['uid']))
        self.workspace.canvas.show_original_preview()
        self.hint.setText(tr('histology_white_balance_hint'))
        self.sync()

    def reset(self):
        self.cancel()
        image=self.image()
        if image is None:return
        image.pop('display_white_balance',None)
        self.workspace.panel.append_audit('reset_display_white_balance',str(image['uid']))
        self.workspace.canvas.show_original_preview()
        self.hint.setText(tr('histology_white_balance_hint'))
        self.sync()

    def choose_export(self):
        name=Path(str((self.image() or {}).get('source_name','image'))).stem+'_white_balance.png'
        filename,_=QFileDialog.getSaveFileName(self.workspace,tr('histology_white_export'),
                                              str(Path(recent_directory())/name),'PNG (*.png)')
        if not filename:return
        try:
            target=Path(filename).with_suffix('.png')
            self.export_to_path(target);remember_file_directory(target)
        except (ValueError,OSError) as error:self.hint.setText(str(error))

    def export_to_path(self,path):
        image=self.image()
        if image is None:raise ValueError(tr('histology_white_no_image'))
        # Explicit display export; the existing Original export still preserves raw colors.
        rendered=self.workspace.panel.display_qimage(image['uid'])
        if not rendered.save(str(path),'PNG'):raise OSError(tr('histology_white_export_failed'))
