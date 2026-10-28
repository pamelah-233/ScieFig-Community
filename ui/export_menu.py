from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import QMenu, QWidget

from core.utils.i18n import tr
from core.utils.recent_directory import recent_directory


FIGURE_DIALOG_FILTER = "PDF (*.pdf);;SVG (*.svg);;TIFF (*.tiff);;PNG (*.png)"
FIGURE_SUFFIX_BY_FILTER = {
    "PDF (*.pdf)": ".pdf",
    "SVG (*.svg)": ".svg",
    "TIFF (*.tiff)": ".tiff",
    "PNG (*.png)": ".png",
}

ACTION_IMAGE = "image"
ACTION_FIGURE = "figure"
ACTION_TABLE = "table"
ACTION_TABLE_EXCEL = "table_excel"
ACTION_TABLE_RESULTS_CSV = "table_results_csv"
ACTION_TABLE_STATS_CSV = "table_stats_csv"
ACTION_TABLE_AI_STATS_CSV = "table_ai_stats_csv"
ACTION_MORE = "more"
ACTION_SUMMARY_JSON = "summary_json"


@dataclass(frozen=True)
class ExportMenuEntry:
    action_id: str
    label_key: str
    children: tuple["ExportMenuEntry", ...] = ()


MODULE_EXPORT_MENU: dict[str, tuple[ExportMenuEntry, ...]] = {
    "qpcr": (ExportMenuEntry(ACTION_FIGURE, "export_figure"),),
    "wb": (ExportMenuEntry(ACTION_FIGURE, "export_figure"),),
    "fluoro": (
        ExportMenuEntry(ACTION_IMAGE, "export_image"),
        ExportMenuEntry(ACTION_FIGURE, "export_figure"),
        ExportMenuEntry(ACTION_TABLE_EXCEL, "export_table"),
    ),
    "ihc": (
        ExportMenuEntry(ACTION_IMAGE, "export_image"),
        ExportMenuEntry(ACTION_FIGURE, "export_figure"),
        ExportMenuEntry(
            ACTION_TABLE,
            "export_table",
            (
                ExportMenuEntry(ACTION_TABLE_EXCEL, "export_excel"),
                ExportMenuEntry(ACTION_TABLE_RESULTS_CSV, "export_results_csv"),
                ExportMenuEntry(ACTION_TABLE_STATS_CSV, "export_statistics_csv"),
            ),
        ),
        ExportMenuEntry(
            ACTION_MORE,
            "export_more",
            (ExportMenuEntry(ACTION_SUMMARY_JSON, "export_summary_json"),),
        ),
    ),
    "brightfield": (
        ExportMenuEntry(ACTION_IMAGE, "export_image"),
        ExportMenuEntry(ACTION_FIGURE, "export_figure"),
        ExportMenuEntry(
            ACTION_TABLE,
            "export_table",
            (
                ExportMenuEntry(ACTION_TABLE_EXCEL, "export_excel"),
                ExportMenuEntry(ACTION_TABLE_STATS_CSV, "export_traditional_stats_csv"),
                ExportMenuEntry(ACTION_TABLE_AI_STATS_CSV, "export_he_fiber_stats_csv"),
            ),
        ),
    ),
    "viewer": (ExportMenuEntry(ACTION_IMAGE, "export_image"),),
    "composer": (ExportMenuEntry(ACTION_FIGURE, "export_figure"),),
    "stylelab": (),
}


def menu_entries_for_module(module_id: str) -> tuple[ExportMenuEntry, ...]:
    return MODULE_EXPORT_MENU.get(module_id, ())


def top_level_label_keys(module_id: str) -> tuple[str, ...]:
    return tuple(entry.label_key for entry in menu_entries_for_module(module_id))


def figure_suffix_for_filter(selected_filter: str, fallback: str = ".pdf") -> str:
    if not fallback.startswith("."):
        fallback = f".{fallback}"
    text = (selected_filter or "").strip()
    mapped = FIGURE_SUFFIX_BY_FILTER.get(text)
    if mapped:
        return mapped
    upper = text.upper()
    if "SVG" in upper:
        return ".svg"
    if "TIFF" in upper or "TIF" in upper:
        return ".tiff"
    if "PNG" in upper:
        return ".png"
    if "PDF" in upper:
        return ".pdf"
    return fallback


def apply_selected_figure_suffix(path: str, selected_filter: str, fallback: str = ".pdf") -> str:
    if not fallback.startswith("."):
        fallback = f".{fallback}"
    target = Path(path)
    if not target.suffix:
        return str(target) + figure_suffix_for_filter(selected_filter, fallback)
    if target.suffix.lower() not in {".pdf", ".svg", ".tif", ".tiff", ".png"}:
        return str(target.with_suffix(figure_suffix_for_filter(selected_filter, fallback)))
    return path


def apply_native_png_suffix(path: str) -> str:
    target = Path(path)
    if target.suffix.lower() != ".png":
        return str(target.with_suffix(".png"))
    return str(target)


def suggested_export_path(filename: str) -> str:
    recent = recent_directory()
    if recent:
        return str(Path(recent) / filename)
    return filename


def build_export_menu(parent: QWidget, module_id: str, callbacks: dict[str, object]) -> QMenu:
    menu = QMenu(parent)
    menu.setObjectName("V9ExportMenu")
    _add_entries(menu, menu_entries_for_module(module_id), callbacks)
    return menu


def _add_entries(menu: QMenu, entries: tuple[ExportMenuEntry, ...], callbacks: dict[str, object]) -> None:
    for entry in entries:
        if entry.children:
            submenu = menu.addMenu(tr(entry.label_key))
            submenu.setObjectName(f"V9ExportMenu_{entry.action_id}")
            submenu.menuAction().setData(entry.action_id)
            _add_entries(submenu, entry.children, callbacks)
            continue
        action = menu.addAction(tr(entry.label_key))
        action.setData(entry.action_id)
        callback = callbacks.get(entry.action_id)
        if callable(callback):
            action.triggered.connect(lambda _checked=False, cb=callback: cb())
