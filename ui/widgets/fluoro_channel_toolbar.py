"""Compact preview selectors; rendering and measurements stay in the workspace."""
import sys
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton, QWidget

from core.plotting.fluoro_render import COMBINED_INTENSITY_NAME, RGB_COMPOSITE_MODE, rgb_interpretation_mode
from core.utils.i18n import tr


class FluoroChannelToolbar(QWidget):
    selected = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('FluoroChannelToolbar')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(32 if sys.platform in {'darwin', 'win32'} else 28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2) if sys.platform in {'darwin', 'win32'} else layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        self._signature = None
        self.caption = QLabel()
        self.caption.setObjectName('PreviewGroupCaption')
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setFixedHeight(26)
        layout.addWidget(self.caption)
        layout.addWidget(self._button('merge'))
        self.previous = QToolButton()
        self.previous.setText('‹')
        self.following = QToolButton()
        self.following.setText('›')
        for button in (self.previous, self.following):
            button.setFixedSize(22, 26)
        self.scroll = QScrollArea()
        self.scroll.setObjectName('FluoroChannelScroll')
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumWidth(0)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.scroll.setFixedHeight(26)
        self.body = QWidget()
        self.body.setObjectName('FluoroChannelButtons')
        self.row = QHBoxLayout(self.body)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(4)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.previous)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.following)
        layout.addWidget(self._button('split'))
        layout.addStretch(1)
        bar = self.scroll.horizontalScrollBar()
        self.previous.clicked.connect(lambda: bar.setValue(bar.value() - 100))
        self.following.clicked.connect(lambda: bar.setValue(bar.value() + 100))
        bar.rangeChanged.connect(self._scroll_range)
        self._scroll_range(0, 0)

    def _button(self, key):
        button = QPushButton()
        button.setObjectName('FluoroChannelButton')
        button.setCheckable(True)
        button.setFixedHeight(26)
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        button.clicked.connect(lambda checked=False, choice=key: self.selected.emit(choice))
        self.group.addButton(button)
        self.buttons[key] = button
        return button

    def _scroll_range(self, minimum, maximum):
        self.previous.setVisible(maximum > minimum)
        self.following.setVisible(maximum > minimum)

    def sync(self, image, channel_position, step):
        self.caption.setText(tr('preview_channel_group'))
        channels = list(image.get('channels', [])) if image else []
        names = tuple(tr('combined_intensity') if channel.get('name') == COMBINED_INTENSITY_NAME
                      else str(channel.get('name') or tr('fluoro_canvas_channel').format(index=i + 1))
                      for i, channel in enumerate(channels))
        if names != self._signature:
            for key in list(self.buttons):
                if isinstance(key, int):
                    button = self.buttons.pop(key)
                    self.group.removeButton(button)
                    self.row.removeWidget(button)
                    button.hide()  # A queued repaint may precede Qt's DeferredDelete event.
                    button.deleteLater()
            for position, name in enumerate(names):
                button = self._button(position)
                button.setText(name)
                self.row.addWidget(button)
                button.show()
            self._signature = names
        self.buttons['merge'].setText(tr('fluoro_preview_merge'))
        self.buttons['split'].setText(tr('fluoro_preview_split'))
        restricted = bool(image) and rgb_interpretation_mode(image) in {'auto_single_signal', RGB_COMPOSITE_MODE}
        for key, button in self.buttons.items():
            button.setEnabled(bool(channels) and (key == 'merge' or not restricted))
            button.setToolTip(tr('fluoro_preview_rgb_hint') if restricted and key != 'merge'
                              else tr('fluoro_preview_hint'))
        mode = str(image.get('view_mode', 'merge')) if image else 'merge'
        selected = channel_position if mode == 'single' else mode
        if selected in self.buttons:
            self.buttons[selected].setChecked(True)
        self.row.activate()
        self.body.setFixedSize(self.body.sizeHint())
        self.scroll.setMaximumWidth(max(1, self.body.width()))
        self.setVisible(bool(channels) and step in (0, 1, 2))

