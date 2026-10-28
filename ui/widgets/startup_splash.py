"""Lightweight startup feedback, available before importing analysis modules."""
from pathlib import Path
from PyQt6 import sip

from PyQt6.QtCore import QEventLoop, QPropertyAnimation, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from core.constants import SOFTWARE_VERSION
from core.utils.i18n import get_language


COPY = {
    'zh': (
        '科学图像分析与绘图',
        ('启动运行环境', '载入分析模块', '初始化画布工具', '准备工作区', '准备就绪'),
        ('准备应用组件', 'qPCR、WB 与图像分析', '绘图、标注与排版', '整理界面与窗口布局', '正在进入软件主界面'),
        ('运行环境', '分析模块', '画布工具', '工作区'),
    ),
    'en': (
        'Scientific image analysis & figures',
        ('Starting the application', 'Loading analysis modules', 'Preparing canvas tools', 'Preparing your workspace', 'Ready'),
        ('Initializing application components', 'qPCR, WB and image analysis', 'Drawing, annotations and layout', 'Finalizing the interface and window layout', 'Opening ScieFig'),
        ('Environment', 'Analysis', 'Canvas', 'Workspace'),
    ),
}


class StartupSplash(QWidget):
    def __init__(self, resources_dir: Path):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(400, 210)
        self.setWindowTitle('ScieFig')
        self.icon=QPixmap(str(resources_dir/'icons'/'sciefig_app_icon.png'))
        self.stage_index=0
        self.progress_percent=0
        self._angle=0
        self._timer=QTimer(self);self._timer.setInterval(40);self._timer.timeout.connect(self._spin)
        self._fade=QPropertyAnimation(self,b'windowOpacity',self)
        self._fade.setDuration(180);self._fade.setStartValue(1.);self._fade.setEndValue(0.)
        self._fade.finished.connect(self.close)
        screen=QApplication.primaryScreen()
        if screen:self.move(screen.availableGeometry().center()-self.rect().center())

    def set_stage(self, index):
        if index not in range(5):raise ValueError('Invalid startup stage')
        self.stage_index=index;self.progress_percent=index*25
        copy=COPY[get_language()]
        self.setAccessibleName(f'ScieFig · {copy[1][index]} · {self.progress_percent}%')
        self.setAccessibleDescription(copy[2][index])
        # Paint only this window during initialization. Pumping the whole
        # event loop here can run main-window timers before startup is ready.
        if index==0:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        self.repaint()

    def finish(self):
        self.set_stage(4);self._timer.stop();self._fade.start()

    def dispose(self):
        """Release native window/pixmap resources before QApplication exits."""
        self._timer.stop();self._fade.stop();self.close()
        self.icon=QPixmap()
        sip.delete(self)

    def showEvent(self,event):
        super().showEvent(event);self._timer.start()

    def hideEvent(self,event):
        self._timer.stop();super().hideEvent(event)

    def _spin(self):
        self._angle=(self._angle+14)%360;self.update()

    @staticmethod
    def _text(painter,rect,text,size=12,color='#e9e5df',weight=QFont.Weight.Normal,align=Qt.AlignmentFlag.AlignLeft):
        font=QFont('Segoe UI');font.setPixelSize(size);font.setWeight(weight)
        painter.setFont(font);painter.setPen(QColor(color))
        painter.drawText(QRectF(*rect),align|Qt.AlignmentFlag.AlignVCenter,text)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        copy=COPY[get_language()];index=self.stage_index
        p.setPen(QPen(QColor('#45403b'),1));p.setBrush(QColor('#262525'))
        p.drawRoundedRect(QRectF(.5,.5,399,209),9,9)
        p.drawPixmap(22,20,36,36,self.icon)
        self._text(p,(70,15,232,30),'ScieFig',23,weight=QFont.Weight.DemiBold)
        self._text(p,(71,44,307,18),copy[0],11,'#aaa198')
        self._text(p,(322,18,56,20),SOFTWARE_VERSION,11,'#94897f',align=Qt.AlignmentFlag.AlignRight)
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor('#655245'),1.5))
        p.drawEllipse(QRectF(22,94,12,12));p.setPen(QPen(QColor('#e3ad8d'),1.5))
        if index<4:p.drawArc(QRectF(22,94,12,12),-self._angle*16,190*16)
        else:p.drawLine(25,100,28,103);p.drawLine(28,103,32,97)
        self._text(p,(44,88,286,24),copy[1][index],14,weight=QFont.Weight.Medium)
        self._text(p,(334,88,44,24),f'{self.progress_percent}%',12,'#d8b49c',align=Qt.AlignmentFlag.AlignRight)
        self._text(p,(44,115,334,18),copy[2][index],12,'#aaa198')
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#3c3733'));p.drawRoundedRect(QRectF(22,150,356,4),2,2)
        p.setBrush(QColor('#c58d6c'))
        if self.progress_percent:p.drawRoundedRect(QRectF(22,150,356*self.progress_percent/100,4),2,2)
        for i,(x,label) in enumerate(zip((22,120,216,312),copy[3])):
            color='#e4c4ae' if i==index else '#b6ada4' if i<index else '#827a72'
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(color));p.drawEllipse(QRectF(x,181,4,4))
            self._text(p,(x+10,173,76,20),label,11,color)
