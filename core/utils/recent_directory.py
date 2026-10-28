from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings


_SETTINGS_KEY = "dialogs/last_directory"


def recent_directory() -> str:
    return str(QSettings("ScieFig", "ScieFig").value(_SETTINGS_KEY, "") or "")


def remember_file_directory(path: str | Path) -> None:
    _remember_directory(Path(path).parent)


def remember_directory(path: str | Path) -> None:
    _remember_directory(Path(path))


def _remember_directory(path: Path) -> None:
    settings = QSettings("ScieFig", "ScieFig")
    settings.setValue(_SETTINGS_KEY, str(path))
    settings.sync()
