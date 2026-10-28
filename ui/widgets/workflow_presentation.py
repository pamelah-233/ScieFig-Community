"""Small presentation widgets; scientific controls and actions retain their owners."""
import sys
from PyQt6.QtCore import Qt, QSize, QTimer, QSignalBlocker, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QBoxLayout, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QStyle, QStyleOptionToolButton, QStylePainter, QToolButton, QVBoxLayout, QWidget, QProgressBar

from core.utils.i18n import tr


class _DisclosureToggle(QToolButton):
    def __init__(self):
        super().__init__()
        self.reveal = 0.0
        spacer = QPixmap(14, 14)
        spacer.fill(Qt.GlobalColor.transparent)
        self._spacer_icon = QIcon(spacer)

    def paintEvent(self, event):
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.arrowType = Qt.ArrowType.NoArrow
        option.features &= ~QStyleOptionToolButton.ToolButtonFeature.Arrow
        option.icon = self._spacer_icon
        option.iconSize = QSize(14, 14)
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(7, self.height() / 2)
        painter.rotate(self.reveal * 90)
        painter.setPen(QPen(option.palette.color(QPalette.ColorRole.ButtonText), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath()
        path.moveTo(-2, -4)
        path.lineTo(2, 0)
        path.lineTo(-2, 4)
        painter.drawPath(path)


class WorkflowDisclosure(QWidget):
    def __init__(self, title_key, content, *, expanded=False, parent=None):
        super().__init__(parent)
        self.title_key = title_key
        self.setObjectName('WorkflowDisclosure')
        self.content = content
        self.toggle = _DisclosureToggle()
        self.toggle.setObjectName('WorkflowDisclosureToggle')
        self.toggle.setCheckable(True)
        from PyQt6.QtWidgets import QSizePolicy
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.toggle)
        layout.addWidget(content)
        self.toggle.toggled.connect(self._apply_expanded)
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._update_stack_geometry)
        self._motion = QVariantAnimation(self)
        self._motion.setDuration(180)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.valueChanged.connect(self._animate_opacity)
        self._motion.finished.connect(self._finish_fade)
        self._fade_effect = None
        self.retranslate()
        self.setExpanded(expanded)

    @property
    def expanded(self):
        return self.toggle.isChecked()

    def setExpanded(self, expanded):
        with QSignalBlocker(self.toggle):
            self.toggle.setChecked(bool(expanded))
        self._apply_expanded(bool(expanded), animate=False)

    def _apply_expanded(self, expanded, *, animate=True):
        from ui.widgets.studio_motion import motion_enabled
        self._motion.stop()
        self._finish_fade()
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)
        # Commit the final geometry once. Animating height makes QScrollArea
        # repeatedly clamp its scroll offset, moving the clicked disclosure.
        self._update_stack_geometry()
        if animate and motion_enabled() and self.isVisible():
            if expanded:
                self._fade_effect = QGraphicsOpacityEffect(self.content)
                self._fade_effect.setOpacity(0.2)
                self.content.setGraphicsEffect(self._fade_effect)
            self._motion.setStartValue(self.toggle.reveal)
            self._motion.setEndValue(1.0 if expanded else 0.0)
            self._motion.start()
        else:
            self.toggle.reveal = 1.0 if expanded else 0.0
            self.toggle.update()

    def _animate_opacity(self, value):
        self.toggle.reveal = value
        self.toggle.update()
        if self._fade_effect is not None:
            self._fade_effect.setOpacity(0.2 + 0.8 * value)

    def _finish_fade(self):
        self.content.setGraphicsEffect(None)
        self._fade_effect = None

    def _update_stack_geometry(self):
        self.layout().activate()
        child = self
        parent = self.parentWidget()
        while parent is not None:
            layout = parent.layout()
            if isinstance(layout, QBoxLayout):
                # Refresh stale QWidgetItem height caches before painting.
                index = layout.indexOf(child)
                if index >= 0:
                    stretch = layout.stretch(index)
                    item = layout.takeAt(index)
                    layout.insertWidget(index, child, stretch, item.alignment())
            if layout is not None:
                layout.invalidate()
                layout.activate()
            update = getattr(parent, '_sync_current_page_geometry', None)
            if callable(update):
                update()
                parent._parent_refresh.stop()
            if isinstance(parent, QScrollArea):
                body = parent.widget()
                width = parent.viewport().width()
                height = body.heightForWidth(width) if body.hasHeightForWidth() else body.sizeHint().height()
                body.resize(width, max(parent.viewport().height(), body.minimumSizeHint().height(), height))
                break
            parent.updateGeometry()
            child = parent
            parent = parent.parentWidget()

    def retranslate(self):
        self.toggle.setText(tr(self.title_key))
        self.toggle.setToolTip('')


class WorkflowTaskHeading(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('workflowPlain', True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 6) if sys.platform in {'darwin', 'win32'} else layout.setContentsMargins(14, 4, 14, 12)
        layout.setSpacing(4 if sys.platform in {'darwin', 'win32'} else 6)
        self.step = QLabel()
        self.step.setObjectName('WorkflowStepMeta')
        self.settings_button = QToolButton()
        self.settings_button.setObjectName('WorkflowSettingsButton')
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.settings_button.setMenu(QMenu(self.settings_button))
        self.settings_button.hide()
        row = QHBoxLayout()
        row.addWidget(self.step, 1)
        row.addWidget(self.settings_button)
        layout.addLayout(row)
        self.title = QLabel()
        self.title.setObjectName('WorkflowHeadingTitle')
        self.hint = QLabel()
        self.hint.setObjectName('WorkflowHeadingHint')
        self.hint.setWordWrap(True)
        for label in (self.title, self.hint):
            layout.addWidget(label)

    def set_step(self, index, count, name, task):
        self.settings_button.setText(tr('workflow_settings_entry') + '…')
        self.step.setText(tr('workflow_step').format(current=index+1,count=count,name=name))
        self.title.setText(tr(f'workflow_heading_{task}'))
        self.hint.setText(tr(f'workflow_hint_{task}'))


class WorkflowActionFooter(QFrame):
    def __init__(self, previous, following, parent=None):
        super().__init__(parent)
        self.setObjectName('WorkflowActionFooter')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.hint = QLabel()
        self.hint.setObjectName('DimLabel')
        self.hint.setWordWrap(True)
        self.primary = QPushButton()
        self.primary.setObjectName('PrimaryButton')
        self.primary.setMinimumHeight(34)
        self.job_status = QLabel()
        self.job_status.setObjectName('DimLabel')
        self.job_status.setWordWrap(True)
        self.job_status.hide()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        self._busy = False
        self._action_label = ''
        self.previous = QPushButton()
        self.following = QPushButton()
        row = QHBoxLayout()
        row.addWidget(self.previous)
        row.addWidget(self.following)
        layout.addWidget(self.hint)
        layout.addWidget(self.job_status)
        layout.addWidget(self.progress)
        layout.addWidget(self.primary)
        layout.addLayout(row)
        self.previous.clicked.connect(lambda checked=False: previous())
        self.following.clicked.connect(lambda checked=False: following())
        self.source = None
        self.primary.clicked.connect(self._activate)
        self.retranslate()

    def _activate(self):
        if not self._busy and self.source is not None and self.source.isEnabled():
            self.source.click()

    def set_action(self, source, *, label=None, hint=''):
        self.source = source
        self.primary.setVisible(source is not None)
        self.primary.setEnabled(not self._busy and source is not None and source.isEnabled())
        if source is not None:
            self._action_label = label or source.text()
            self.primary.setText(tr('analysis_feedback_running_button') if self._busy else self._action_label)
            self.primary.setToolTip(source.toolTip())
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))

    def set_job_feedback(self, state='', *, elapsed=0.0, error=None):
        self._busy = state == 'running'
        self.progress.setVisible(self._busy)
        text = tr('analysis_feedback_' + state).format(seconds=elapsed) if state else ''
        self.job_status.setText(text)
        self.job_status.setToolTip(str(error) if error else '')
        self.job_status.setVisible(bool(text))
        self.primary.setText(tr('analysis_feedback_running_button') if self._busy else self._action_label)
        self.primary.setEnabled(not self._busy and self.source is not None and self.source.isEnabled())

    def retranslate(self):
        self.previous.setText('← ' + tr('workflow_previous'))
        self.following.setText(tr('workflow_next') + ' →')

