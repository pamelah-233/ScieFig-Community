from __future__ import annotations
import sys

DARK = {
    "bg": "#16130f",
    "surface": "#221d17",
    "hover": "#2c2620",
    "raised": "#342d25",
    "border": "#38302a",
    "border2": "#4a3f36",
    "text": "#f0eae2",
    "muted": "#a39689",
    "dim": "#6e6258",
    "accent": "#d97757",
    "accent2": "#e08a6e",
    "paper": "#ffffff",
    "canvas": "#2c2620",
    "error": "#f87171",
    "warning": "#fbbf24",
    "success": "#34d399",
    "info": "#7dd3fc",
    "input": "#2c2620",
    "handle": "#5a4b40",
    "badge_bg": "#2c2620",
}

LIGHT = {
    "bg": "#f3eee7",
    "surface": "#fffaf4",
    "hover": "#f2e8dc",
    "raised": "#ffffff",
    "border": "#e0d4c6",
    "border2": "#cbbdae",
    "text": "#221d17",
    "muted": "#6e6258",
    "dim": "#8a7d72",
    "accent": "#c45c3a",
    "accent2": "#d97757",
    "paper": "#ffffff",
    "canvas": "#e7dfd4",
    "error": "#b91c1c",
    "warning": "#b45309",
    "success": "#047857",
    "info": "#0369a1",
    "input": "#ffffff",
    "handle": "#c4b8ac",
    "badge_bg": "#f2e8dc",
}

_QSS = """
QWidget {
    background: $bg;
    color: $text;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 12px;
}
QMainWindow, QDialog {
    background: $bg;
}
QLabel { background: transparent; }
QFrame#TopBar, QFrame#BottomBar, QFrame#StageHeader {
    background: $surface;
    border: 0;
}
QFrame#TopBar {
    border-bottom: 1px solid $border;
}
QFrame#BottomBar {
    border-top: 1px solid $border;
}
QFrame#StageHeader {
    border-bottom: 1px solid $border;
}
QFrame#Pane, QFrame#TokenRow, QFrame#PreviewCard, QScrollArea, QWidget#PaneInner {
    background: $surface;
}
QFrame#Pane {
    border: 1px solid $border;
    border-radius: 6px;
}
QFrame#TokenRow {
    border: 1px solid $border;
    border-radius: 4px;
}
QFrame#TokenRow[selected="true"] {
    border: 1px solid $accent;
    background: $hover;
}
QLabel#BrandLabel {
    color: $accent;
    font-size: 14px;
    font-weight: 800;
}
QLabel#SectionLabel {
    color: $muted;
    font-size: 10px;
    font-weight: 600;
}
QLabel#DimLabel, QLabel#CropLabel {
    color: $dim;
    font-size: 11px;
}
QLabel#ErrorLabel {
    color: $error;
    font-size: 12px;
}
QLabel#WarnLabel {
    color: $warning;
    font-size: 11px;
}
QLabel#TitleLabel {
    color: $text;
    font-weight: 700;
}
QLabel#StatusDetected { color: $success; font-weight: 700; }
QLabel#StatusEstimated { color: $warning; font-weight: 700; }
QLabel#StatusUnknown { color: $dim; font-weight: 700; }
QLabel#StatusFallback { color: $info; font-weight: 700; }
QLabel#StatusEdited { color: $accent; font-weight: 700; }
QToolButton#LinkButton {
    background: transparent;
    border: 0;
    color: $info;
    padding: 0 2px;
    min-height: 18px;
    font-size: 11px;
}
QToolButton#LinkButton:checked {
    color: $accent2;
}
QTabWidget::pane {
    border: 1px solid $border;
    background: $surface;
    border-radius: 4px;
}
QTabBar::tab {
    background: $hover;
    color: $text;
    padding: 4px 10px;
    border: 1px solid $border;
    min-height: 22px;
}
QTabBar::tab:selected {
    background: $accent;
    color: #ffffff;
    font-weight: 700;
}
QPushButton {
    background: $hover;
    color: $text;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 4px 10px;
    min-height: 24px;
}
QPushButton:hover {
    background: $raised;
    border-color: $border2;
}
QFrame#ExtractionToolbarBody QPushButton {
    min-height: 20px;
    padding: 3px 8px;
}
QFrame#ExtractionToolbarBody QLineEdit {
    min-height: 20px;
    padding: 3px 8px;
}
QToolButton#ToolbarCollapseButton {
    color: $text;
    background: transparent;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 2px 8px;
}
QToolButton#ToolbarCollapseButton:hover {
    background: $raised;
}
QPushButton:pressed {
    background: $surface;
}
QPushButton:disabled {
    color: $dim;
    background: $bg;
    border-color: $border;
}
QPushButton#PrimaryButton {
    background: $accent;
    border: 1px solid $accent;
    color: #ffffff;
    font-weight: 700;
}
QPushButton#PrimaryButton:hover {
    background: $accent2;
}
QPushButton#PrimaryButton:disabled {
    background: $hover;
    border-color: $border;
    color: $dim;
}
QPushButton#LayoutButton:checked, QPushButton#ThemeButton:checked, QPushButton#StageButton:checked {
    background: $accent;
    border-color: $accent;
    color: #ffffff;
    font-weight: 700;
}
QLineEdit, QComboBox, QAbstractSpinBox, QFontComboBox {
    background: $input;
    color: $text;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 3px 6px;
    min-height: 22px;
}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {
    border-color: $accent;
}
QComboBox QAbstractItemView {
    background: $raised;
    color: $text;
    border: 1px solid $border2;
    selection-background-color: $hover;
}
QListWidget {
    background: $hover;
    color: $text;
    border: 1px solid $border;
    border-radius: 4px;
    outline: none;
}
QListWidget::item:selected {
    background: $raised;
    color: $accent;
}
QScrollArea {
    border: 0;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: $bg;
    border: 1px solid $border;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: $handle;
    border-radius: 3px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QCheckBox, QRadioButton {
    color: $text;
    spacing: 6px;
    background: transparent;
}
QProgressBar {
    border: 1px solid $border;
    border-radius: 4px;
    background: $hover;
    color: $text;
    text-align: center;
    min-height: 14px;
}
QProgressBar::chunk {
    background: $accent;
}
QSplitter::handle {
    background: $bg;
}
QStatusBar {
    background: $surface;
    color: $dim;
    border-top: 1px solid $border;
}
QMenu {
    background: $raised;
    color: $text;
    border: 1px solid $border2;
}
QMenu::item:selected {
    background: $hover;
}
QToolTip {
    background: $raised;
    color: $text;
    border: 1px solid $border2;
}
"""


def stylesheet(theme: str) -> str:
    tokens = LIGHT if theme == "light" else DARK
    text = _QSS
    if sys.platform in {'darwin', 'win32'}:
        text += """
QWidget { font-size: 12px; }
QFrame#Pane { border: none; border-radius: 0; }
QFrame#TokenRow { border: none; border-bottom: 1px solid $border; border-radius: 0; }
QFrame#TokenRow[selected="true"] { background: $hover; border-color: $border2; }
QLabel#TitleLabel, QLabel#BrandLabel { font-weight: 500; }
QLabel#DimLabel, QLabel#CropLabel { color: $muted; }
QLabel#WarnLabel, QLabel#StatusEstimated { color: #d5b18a; }
QLabel#StatusDetected { color: #91b5a0; }
QWidget#StyleActionRow QPushButton, QWidget#StylePaneTools QPushButton {
    min-height: 18px; padding: 3px 5px; font-size: 12px;
}
QWidget#StyleActionRow QLineEdit { min-height: 18px; padding: 3px 5px; }
QWidget#StyleActionRow QPushButton[styleIconOnly="true"], QWidget#StylePaneTools QPushButton[styleIconOnly="true"] {
    min-width: 0px; padding: 3px 0px;
}
QWidget#StylePaneTools { background: $surface; border-bottom: 1px solid $border; }
QWidget#StylePaneTools QComboBox, QWidget#StylePaneTools QSpinBox { min-height: 18px; padding: 2px 3px; }
QPushButton#StageButton, QPushButton#StyleParameterTab { background: transparent; border-color: transparent; padding: 3px 4px; }
QPushButton#StageButton:checked, QPushButton#StyleParameterTab:checked,
QPushButton#LayoutButton:checked, QPushButton#ThemeButton:checked {
    color: $accent; background: $hover; border: 1px solid $border2; font-weight: 500;
}
QFrame#TokenRow QPushButton { min-height: 0; padding: 0; }
QLabel#StylePaneCaption { color: $muted; font-size: 11px; padding: 3px 7px; border-top: 1px solid $border; }
QFrame#BottomBar QPushButton { min-height: 18px; padding: 2px 5px; font-size: 11px; }
QStatusBar QLabel { color: $muted; font-size: 11px; }
QScrollBar:horizontal { height: 10px; border: 0; }
QScrollBar:vertical { width: 8px; border: 0; }
"""
    for key in sorted(tokens, key=len, reverse=True):
        value = tokens[key]
        text = text.replace("$" + key, value)
    if sys.platform in {'darwin', 'win32'}:
        if theme != "light":
            from ui.platform_support import macos_theme_stylesheet
            text = macos_theme_stylesheet(text)
        text = text.replace('"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif',
                            '".AppleSystemUIFont", "Helvetica Neue", "PingFang SC", "Segoe UI", "Microsoft YaHei UI"')
        for weight in ('600', '700', '800'):
            text = text.replace('font-weight: ' + weight, 'font-weight: 500')
        if sys.platform == 'win32':
            text = text.replace('".AppleSystemUIFont", "Helvetica Neue", "PingFang SC", "Segoe UI", "Microsoft YaHei UI"',
                                '"Segoe UI", "Microsoft YaHei UI"')
    return text

