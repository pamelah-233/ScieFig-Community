"""Shared studio transitions, respecting native reduced-motion preferences."""
import sys

from PyQt6.QtWidgets import QApplication


def motion_enabled():
    app = QApplication.instance()
    if app is None or app.platformName() == 'offscreen':
        return False
    if sys.platform == 'darwin':
        from AppKit import NSWorkspace
        return not NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
    if sys.platform == 'win32':
        import ctypes
        enabled = ctypes.c_int()
        # SPI_GETCLIENTAREAANIMATION reads, never changes, the user's preference.
        success = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return bool(success and enabled.value)
    return False
