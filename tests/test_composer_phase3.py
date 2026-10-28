from __future__ import annotations

import os
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import tifffile
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PyQt6.QtCore import QMimeData, QPointF, QRect, Qt, QUrl
from PyQt6.QtGui import QPainter
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtSvg import QSvgGenerator
from PyQt6.QtWidgets import QApplication, QGraphicsTextItem, QGraphicsView, QPushButton, QWidget
from PIL import Image

from core.models import ExternalImagePanel, Project, QPCRPanel
from core.models.composer import ComposerFigure, ComposerLayout, PanelReference
from core.utils.svg_text import svg_text_entries
from ui.main_window import MainWindow
from ui.panels.placeholder_panel import dropped_file_paths
from ui.widgets.composer_canvas import ComposerCanvas, MM_TO_PX, PANEL_UUID_MIME
from ui.widgets.layout_editing import EditableLayoutRecord, align_records, distribute_records, make_records_same_size


def test_composer_layout_places_panel_references() -> None:
    layout = ComposerLayout.create("2x2")
    first = layout.place_panel("panel-uuid-1")
    second = layout.place_panel("panel-uuid-2")

    assert first.panel_letter == "a"
    assert second.panel_letter == "b"
    assert layout.cells[0].panel_uuid == "panel-uuid-1"
    assert layout.to_dict()["cells"][1]["panel_uuid"] == "panel-uuid-2"


def test_composer_defaults_to_nature_global_style_and_lowercase_labels() -> None:
    figure = ComposerFigure(figure_id="Figure 1")
    figure.panel_refs.append(PanelReference(panel_uid="panel-1", source_module="qpcr", figure_label="a"))
    payload = figure.to_dict()

    assert figure.journal_preset == "Nature"
    assert figure.global_style["journal_preset"] == "Nature"
    assert figure.global_style["panel_label_case"] == "lowercase"
    assert figure.export_settings["primary_format"] == "svg"
    assert payload["global_style"]["panel_label_case"] == "lowercase"


def test_composer_legacy_payload_missing_preset_loads_nature_defaults() -> None:
    figure = ComposerFigure.from_dict({"figure_id": "Legacy Figure"})

    assert figure.journal_preset == "Nature"
    assert figure.global_style["journal_preset"] == "Nature"
    assert figure.global_style["panel_label_case"] == "lowercase"


def test_project_persists_composer_layout() -> None:
    project = Project(title="Composer Test")
    panel = QPCRPanel(panel_id="Figure1A")
    project.add_panel(panel)
    layout = ComposerLayout.create("2x3")
    layout.place_panel(panel.panel_uuid)
    project.set_composer_layout(layout)

    restored = Project.from_dict(project.to_dict())
    restored_layout = restored.get_composer_layout()

    assert restored_layout.preset == "2x3"
    assert restored_layout.cells[0].panel_uuid == panel.panel_uuid


def test_external_image_panel_imports_hash_and_path(tmp_path) -> None:
    image_path = tmp_path / "external.png"
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    assert image.save(str(image_path))

    panel = ExternalImagePanel(panel_id="Figure1B")
    panel.import_image(image_path)

    assert panel.data["raw_source"] == "external.png"
    assert panel.data["raw_hash"].startswith("sha256:")
    assert panel.data["raw_copy_path"] == "data/external/external.png"


def test_composer_svg_panel_persists_text_override(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    svg_path = tmp_path / "editable.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><text x="10" y="20">Control</text></svg>',
        encoding="utf-8",
    )
    project = Project(title="SVG text")
    panel = ExternalImagePanel(panel_id="Figure1A")
    panel.import_image(svg_path)
    project.add_panel(panel)
    canvas = ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(panel.panel_uuid)

    item = next(scene_item for scene_item in canvas.scene.items() if scene_item.data(0) == "panel_ref")
    assert item.apply_svg_text_override(0, "Treatment")
    assert item.ref.chart_text_overrides["svg"] == {"0": "Treatment"}
    assert [entry.text for entry in svg_text_entries(svg_path.read_bytes())] == ["Control"]
    assert svg_text_entries(item._rendered_svg_bytes())[0].text == "Treatment"
    canvas.close()
    app.processEvents()


def test_composer_svg_panel_persists_text_position_override(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    svg_path = tmp_path / "editable.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><text x="10" y="20">Control</text></svg>',
        encoding="utf-8",
    )
    project = Project(title="SVG text move")
    panel = ExternalImagePanel(panel_id="Figure1A")
    panel.import_image(svg_path)
    project.add_panel(panel)
    canvas = ComposerCanvas()
    canvas.set_project(project)
    canvas.place_panel(panel.panel_uuid)

    item = next(scene_item for scene_item in canvas.scene.items() if scene_item.data(0) == "panel_ref")
    assert item.apply_svg_text_override(0, text="Treatment", dx=18.0, dy=16.0)
    assert item.ref.chart_text_overrides["svg"] == {"0": {"text": "Treatment", "dx": 18.0, "dy": 16.0}}
    assert svg_text_entries(svg_path.read_bytes())[0].text == "Control"
    entries = svg_text_entries(item._rendered_svg_bytes())
    assert [entry.text for entry in entries] == ["Treatment"]
    assert entries[0].x == 28.0
    assert entries[0].y == 36.0
    canvas.close()
    app.processEvents()


def test_composer_renders_svg_and_pdf_external_panels(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    svg_path = tmp_path / "external.svg"
    generator = QSvgGenerator()
    generator.setFileName(str(svg_path))
    generator.setSize(QRect(0, 0, 120, 80).size())
    generator.setViewBox(QRect(0, 0, 120, 80))
    painter = QPainter(generator)
    painter.fillRect(QRect(0, 0, 120, 80), 0xFF00AA00)
    painter.end()

    windows = []
    try:
        blank_window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
        windows.append(blank_window)
        canvas = blank_window.composer_canvas
        canvas.set_project(Project(title="Blank PDF"))
        pdf_path = tmp_path / "external.pdf"
        canvas.export_figure(pdf_path)

        for path in (svg_path, pdf_path):
            panel = ExternalImagePanel(panel_id="Figure1X")
            panel.import_image(path)
            project = Project(title="Format Test")
            project.add_panel(panel)
            window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
            windows.append(window)
            canvas = window.composer_canvas
            canvas.set_project(project)
            canvas.place_panel(panel.panel_uuid)
            pixmap = canvas._panel_pixmap(panel, 160, 120)
            assert pixmap is not None
            assert not pixmap.isNull()
    finally:
        for window in windows:
            window.close()
        app.processEvents()


def test_composer_ui_places_panel_and_exports_png(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")
    panel_uuid = window.project.panels[0].panel_uuid
    window.composer_canvas.place_panel(panel_uuid)
    output = tmp_path / "composer.png"
    window.composer_canvas.export_figure(output)

    assert output.exists()
    assert window.project.get_composer_layout().cells[0].panel_uuid == panel_uuid
    window.close()
    app.processEvents()


def test_composer_export_omits_layout_guides(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")

    output = tmp_path / "composer.svg"
    window.composer_canvas.export_figure(output)
    svg_text = output.read_text(encoding="utf-8", errors="ignore")

    assert "Drop Panel Object" not in svg_text
    assert "drop_panel_object" not in svg_text
    assert "dash" not in svg_text.lower()
    window.close()
    app.processEvents()


def test_composer_inspector_emits_preflight_and_global_export_signal() -> None:
    from ui.panels.composer_workspace import ComposerWorkspace
    app = QApplication.instance() or QApplication([])
    workspace=ComposerWorkspace()
    preflight_requests=[]; export_requests=[]
    workspace.canvas.preflight_requested.connect(lambda:preflight_requests.append(True))
    workspace.canvas.export_requested.connect(lambda:export_requests.append(True))
    workspace.preflight_button.click()
    workspace.canvas.export_requested.emit()
    assert preflight_requests==[True] and export_requests==[True]
    workspace.close();app.processEvents()


def test_composer_canvas_has_canvas_first_workspace_without_legacy_toolbars() -> None:
    from ui.panels.composer_workspace import ComposerWorkspace
    app=QApplication.instance() or QApplication([])
    workspace=ComposerWorkspace()
    assert workspace.objectName()=='ComposerWorkspace'
    assert workspace.canvas.view.objectName()=='ComposerView'
    assert not workspace.asset_dialog.isVisible()
    assert not workspace.inspector_dialog.isVisible()
    assert workspace.findChild(QWidget,'ComposerToolPalette') is None
    assert workspace.findChild(QWidget,'ComposerPagesStrip') is None
    workspace.close();app.processEvents()


def test_composer_pages_are_real_add_switch_delete_controls() -> None:
    from ui.panels.composer_workspace import ComposerWorkspace
    app=QApplication.instance() or QApplication([])
    project=Project(title='Composer Pages')
    workspace=ComposerWorkspace();workspace.set_project(project)
    first=project.active_figure_uid
    assert not workspace.delete_page_button.isEnabled()
    workspace.add_page_button.click()
    second=project.active_figure_uid
    assert second!=first and workspace.pages_combo.count()==2
    workspace.pages_combo.setCurrentIndex(workspace.pages_combo.findData(first))
    workspace.pages_combo.activated.emit(workspace.pages_combo.currentIndex())
    assert project.active_figure_uid==first
    workspace.delete_page_button.click()
    assert project.active_figure_uid==second
    assert workspace.pages_combo.count()==1
    assert not workspace.delete_page_button.isEnabled()
    workspace.close();app.processEvents()


def test_composer_view_remains_large_in_main_window_composer_mode(desktop_screen) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.resize(1366, 768)
    window.set_module("composer")
    window.show()
    app.processEvents()

    view = window.composer_canvas.view
    assert view.isVisible()
    assert view.width() >= 620
    assert view.height() >= 430

    window.close()
    app.processEvents()


def test_composer_canvas_guide_toggle_is_visual_only_and_not_persisted() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    project = Project(title="Guide Toggle")
    canvas = ComposerCanvas()
    canvas.set_project(project)

    def guide_hint_count() -> int:
        return sum(
            1
            for item in canvas.scene.items()
            if isinstance(item, QGraphicsTextItem) and "Drop Panel" in item.toPlainText()
        )

    assert canvas.guides_visible is True
    assert guide_hint_count() > 0

    canvas.set_guides_visible(False)
    assert canvas.guides_visible is False
    assert guide_hint_count() == 0
    assert "guides_visible" not in json.dumps(project.to_dict())

    canvas.set_guides_visible(True)
    assert guide_hint_count() > 0
    canvas.close()
    app.processEvents()


def test_composer_asset_shelf_lists_panels_and_drop_places_panel() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")
    panel_uuid = window.project.panels[0].panel_uuid

    assert window.composer_workspace.assets.count() >= 1
    window.composer_workspace.place_asset(panel_uuid, QPointF(10, 10))

    assert window.project.get_composer_layout().cells[0].panel_uuid == panel_uuid
    window.switch_language("zh")
    assert window.composer_workspace.import_button.text() == "导入图片"
    window.switch_language("en")
    assert window.composer_workspace.import_button.text() == "Import images"
    window.close()
    app.processEvents()


def test_composer_remove_asset_uses_shelf_selection(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "remove_me.png"
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(0xFFEEEEEE)
    assert image.save(str(image_path))

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")
    panel = ExternalImagePanel(panel_id="FigureRemove")
    panel.import_image(image_path)
    window.project.add_panel(panel)
    window.refresh_composer_panel_list()

    labels = [window.composer_workspace.assets.item(index).text() for index in range(window.composer_workspace.assets.count())]
    assert "remove_me.png" in labels
    assert all("FigureRemove" not in label for label in labels)

    for index in range(window.composer_workspace.assets.count()):
        item = window.composer_workspace.assets.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == panel.panel_uuid:
            window.composer_workspace.assets.setCurrentItem(item)
            break
    window.remove_selected_composer_asset()

    assert window.project.get_panel(panel.panel_uuid) is None
    window.close()
    app.processEvents()


def test_composer_remove_asset_clears_all_pages_and_serialized_payload(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    remove_path = tmp_path / "remove_all_pages.png"
    keep_path = tmp_path / "keep_all_pages.png"
    for image_path, color in ((remove_path, 0xFFEEEEEE), (keep_path, 0xFFCCDDFF)):
        image = QImage(100, 80, QImage.Format.Format_RGB32)
        image.fill(color)
        assert image.save(str(image_path))

    project = Project(title="Composer Multi-page Removal")
    removed_panel = ExternalImagePanel(panel_id="FigureRemove")
    kept_panel = ExternalImagePanel(panel_id="FigureKeep")
    removed_panel.import_image(remove_path)
    kept_panel.import_image(keep_path)
    project.add_panel(removed_panel)
    project.add_panel(kept_panel)

    first_figure = ComposerFigure(figure_id="Figure 1")
    first_figure.panel_refs = [
        PanelReference(panel_uid=removed_panel.panel_uuid, source_module="external_image", x_mm=8.0, y_mm=9.0),
        PanelReference(panel_uid=kept_panel.panel_uuid, source_module="external_image", x_mm=42.0, y_mm=12.0),
    ]
    second_figure = ComposerFigure(figure_id="Figure 2")
    second_figure.panel_refs = [
        PanelReference(panel_uid=removed_panel.panel_uuid, source_module="external_image", x_mm=13.0, y_mm=21.0),
        PanelReference(panel_uid=kept_panel.panel_uuid, source_module="external_image", x_mm=55.0, y_mm=25.0),
    ]
    project.set_composer_figures([first_figure, second_figure], first_figure.figure_uid)
    project.composer_layout = first_figure.to_layout().to_dict()
    page_uids = [figure.figure_uid for figure in project.composer_figures]
    active_figure_uid = project.active_figure_uid

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")
    for index in range(window.composer_workspace.assets.count()):
        item = window.composer_workspace.assets.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == removed_panel.panel_uuid:
            window.composer_workspace.assets.setCurrentItem(item)
            break

    window.remove_selected_composer_asset()

    assert [figure.figure_uid for figure in project.composer_figures] == page_uids
    assert project.active_figure_uid == active_figure_uid
    assert all(
        all(ref.panel_uid != removed_panel.panel_uuid for ref in figure.panel_refs)
        for figure in project.composer_figures
    )
    assert all(
        [ref.panel_uid for ref in figure.panel_refs] == [kept_panel.panel_uuid]
        for figure in project.composer_figures
    )
    assert [ref.x_mm for ref in first_figure.panel_refs] == [42.0]
    payload = project.to_dict()
    assert removed_panel.panel_uuid not in json.dumps(payload)
    assert kept_panel.panel_uuid in json.dumps(payload)
    archive_path = tmp_path / "composer_multi_page_removed.sfig"
    project.save_sfig(archive_path)
    with ZipFile(archive_path) as archive:
        project_payload = json.loads(archive.read("project.json"))
        composer_payloads = [
            json.loads(archive.read(record["file"])) for record in project_payload["composer_figures"]
        ]
    assert removed_panel.panel_uuid not in json.dumps(composer_payloads)
    assert kept_panel.panel_uuid in json.dumps(composer_payloads)
    assert len(project_payload["panels"]) == 1
    assert project_payload["panels"][0]["panel_uuid"] == kept_panel.panel_uuid
    window.close()
    app.processEvents()


def test_composer_imports_external_files_to_asset_shelf(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "dragged_to_shelf.png"
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(0xFFDDDDDD)
    assert image.save(str(image_path))

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")
    existing_count = window.composer_workspace.assets.count()
    window.import_external_images_to_shelf([str(image_path)])

    labels = [window.composer_workspace.assets.item(index).text() for index in range(window.composer_workspace.assets.count())]
    assert window.composer_workspace.assets.count() == existing_count + 1
    assert "dragged_to_shelf.png" in labels
    assert any(panel.data.get("raw_source") == "dragged_to_shelf.png" for panel in window.project.panels)
    window.close()
    app.processEvents()


def test_composer_imports_external_tiff_to_asset_shelf(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    tiff_path = tmp_path / "external_rgba.tiff"
    rgba = np.zeros((96, 128, 4), dtype=np.uint8)
    rgba[..., 0] = 180
    rgba[..., 3] = 255
    tifffile.imwrite(tiff_path, rgba, photometric="rgb")

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")
    existing_count = window.composer_workspace.assets.count()
    window.import_external_images_to_shelf([str(tiff_path)])

    labels = [window.composer_workspace.assets.item(index).text() for index in range(window.composer_workspace.assets.count())]
    assert window.composer_workspace.assets.count() == existing_count + 1
    assert "external_rgba.tiff" in labels
    assert any(panel.data.get("raw_source") == "external_rgba.tiff" for panel in window.project.panels)
    window.close()
    app.processEvents()


def test_composer_asset_shelf_drag_mime_matches_canvas() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")
    item = window.composer_workspace.assets.item(0)

    mime = window.composer_workspace.assets.panel_drag_mime_data(item)

    assert mime is not None
    assert mime.hasFormat(PANEL_UUID_MIME)
    assert bytes(mime.data(PANEL_UUID_MIME)).decode("utf-8") == item.data(Qt.ItemDataRole.UserRole)
    window.close()
    app.processEvents()


def test_composer_external_file_drop_mime_is_accepted(tmp_path) -> None:
    image_path = tmp_path / "external_drop.png"
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(0xFFCCCCCC)
    assert image.save(str(image_path))

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(image_path))])

    assert dropped_file_paths(mime) == [str(image_path)]


def test_composer_asset_shelf_accepts_external_file_drop_event(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "event_drop.png"
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(0xFFBBBBBB)
    assert image.save(str(image_path))

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")
    existing_count = window.composer_workspace.assets.count()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(image_path))])
    drag_enter = QDragEnterEvent(
        QPointF(5, 5).toPoint(),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    drop = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    QApplication.sendEvent(window.composer_workspace.assets.viewport(), drag_enter)
    QApplication.sendEvent(window.composer_workspace.assets.viewport(), drop)
    app.processEvents()

    labels = [window.composer_workspace.assets.item(index).text() for index in range(window.composer_workspace.assets.count())]
    assert drag_enter.isAccepted()
    assert window.composer_workspace.assets.count() == existing_count + 1
    assert "event_drop.png" in labels
    window.close()
    app.processEvents()


def test_composer_canvas_persists_panel_reference_move_and_resize(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "panel_ref.png"
    image = QImage(120, 80, QImage.Format.Format_RGB32)
    image.fill(0xFFDDDDDD)
    assert image.save(str(image_path))
    project = Project(title="PanelReference Test")
    panel = ExternalImagePanel(panel_id="Figure1A")
    panel.import_image(image_path)
    project.add_panel(panel)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")
    window.composer_canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(40, 60))
    figure = project.ensure_active_composer_figure()
    ref = figure.panel_refs[0]
    assert ref.panel_uid == panel.panel_uuid
    assert ref.x_mm == 10
    assert ref.y_mm == 15

    item = next(item for item in window.composer_canvas.scene.items() if item.data(0) == "panel_ref")
    item.setSelected(True)
    item.setPos(QPointF(80, 100))
    window.composer_canvas.resize_selected(1.2)
    app.processEvents()

    assert round(ref.x_mm, 2) == 20
    assert round(ref.y_mm, 2) == 25
    assert ref.width_mm > 40
    archive_path = tmp_path / "panel_ref.sfig"
    project.save_sfig(archive_path)
    restored = Project.load_sfig(archive_path)
    restored_ref = restored.ensure_active_composer_figure().panel_refs[0]
    assert round(restored_ref.x_mm, 2) == 20
    assert round(restored_ref.y_mm, 2) == 25
    assert restored_ref.panel_uid == panel.panel_uuid
    window.close()
    app.processEvents()


def test_composer_text_annotation_is_saved_as_annotation_object(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    project = Project(title="Annotation Test")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")

    annotation = window.composer_canvas.add_text_annotation("SPNS1 KO", QPointF(24, 32))
    assert annotation.type == "text"
    assert annotation.properties["content"] == "SPNS1 KO"
    text_item = next(item for item in window.composer_canvas.scene.items() if item.data(0) == "annotation")
    text_item.setPlainText("Edited label")
    text_item.setPos(QPointF(40, 48))
    app.processEvents()

    assert annotation.properties["content"] == "Edited label"
    assert round(annotation.x_mm, 2) == 10
    archive_path = tmp_path / "annotation.sfig"
    project.save_sfig(archive_path)
    with ZipFile(archive_path) as archive:
        project_json = json.loads(archive.read("project.json"))
        composer_json = json.loads(archive.read(project_json["composer_figures"][0]["file"]))
    assert composer_json["annotations"][0]["type"] == "text"
    assert composer_json["annotations"][0]["properties"]["content"] == "Edited label"
    restored = Project.load_sfig(archive_path)
    assert restored.ensure_active_composer_figure().annotations[0].properties["content"] == "Edited label"
    window.close()
    app.processEvents()


def test_composer_text_formatting_controls_update_selected_text(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.new_project()
    window.set_module("composer")

    annotation = window.composer_canvas.add_text_annotation("Area um2", QPointF(24, 32))
    app.processEvents()
    item = next(
        scene_item
        for scene_item in window.composer_canvas.scene.items()
        if isinstance(scene_item, QGraphicsTextItem) and scene_item.toPlainText() == "Area um2"
    )
    item.setSelected(True)
    window.composer_workspace.sync_selection()
    window.composer_workspace.rotation_spin.setValue(-90.0)
    window.composer_workspace.geometry_fields['x_mm'].setValue(10.0)
    window.composer_workspace.geometry_fields['y_mm'].setValue(12.5)

    assert annotation.properties["rotation_deg"] == -90.0
    assert annotation.x_mm == 40.0 / MM_TO_PX
    assert annotation.y_mm == 50.0 / MM_TO_PX
    window.close()
    app.processEvents()


def test_composer_selection_handles_resize_and_lock_hide(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    project = Project(title="Composer Handles")
    image_path = tmp_path / "resize.png"
    image = QImage(120, 80, QImage.Format.Format_RGB32)
    image.fill(0xFFD0D0D0)
    assert image.save(str(image_path))
    panel = ExternalImagePanel(panel_id="Figure1A")
    panel.import_image(image_path)
    project.add_panel(panel)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")
    assert window.composer_canvas.view.dragMode() == QGraphicsView.DragMode.RubberBandDrag
    window.composer_canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(20, 20))
    item = next(item for item in window.composer_canvas.scene.items() if item.data(0) == "panel_ref")
    ref = project.ensure_active_composer_figure().panel_refs[0]

    item.setSelected(True)
    app.processEvents()
    assert item.handles["se"].isVisible()
    item.apply_geometry(ref.x_mm, ref.y_mm, ref.width_mm + 12, ref.height_mm + 8)
    assert round(ref.width_mm, 2) > 40
    assert round(ref.height_mm, 2) > 30

    item = next(item for item in window.composer_canvas.scene.items() if item.data(0) == "panel_ref")
    item.setSelected(True)
    window.composer_canvas.toggle_lock_selected()
    assert ref.locked is True
    window.composer_canvas.hide_selected()
    assert ref.visible is True
    item = next(item for item in window.composer_canvas.scene.items() if item.data(0) == "panel_ref")
    item.setSelected(True)
    window.composer_canvas.toggle_lock_selected()
    assert ref.locked is False
    window.composer_canvas.hide_selected()
    assert ref.visible is False
    window.composer_canvas.show_all()
    assert ref.visible is True
    window.close()
    app.processEvents()


def test_composer_align_distribute_and_layer_commands(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    project = Project(title="Composer Arrange")
    panels = []
    for index in range(3):
        image_path = tmp_path / f"arrange_{index}.png"
        image = QImage(120, 80, QImage.Format.Format_RGB32)
        image.fill(0xFFE0E0E0 + index)
        assert image.save(str(image_path))
        panel = ExternalImagePanel(panel_id=f"Figure1{chr(65 + index)}")
        panel.import_image(image_path)
        project.add_panel(panel)
        panels.append(panel)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")
    for index, panel in enumerate(panels):
        window.composer_canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(20 + index * 120, 20 + index * 20))

    refs = project.ensure_active_composer_figure().panel_refs
    for ref, x_mm in zip(refs, [10.0, 40.0, 90.0]):
        ref.x_mm = x_mm
        ref.y_mm = 10.0 + x_mm / 10.0
        ref.width_mm = 10.0
        ref.height_mm = 8.0
    window.composer_canvas.reference_changed()
    window.composer_canvas.render()

    for item in window.composer_canvas.scene.items():
        if item.data(0) == "panel_ref":
            item.setSelected(True)
    window.composer_canvas.align_selected("top")
    assert len({round(ref.y_mm, 2) for ref in refs}) == 1

    for ref, x_mm in zip(refs, [0.0, 30.0, 90.0]):
        ref.x_mm = x_mm
        ref.y_mm = 10.0
        ref.width_mm = 10.0
    window.composer_canvas.reference_changed()
    window.composer_canvas.render()
    for item in window.composer_canvas.scene.items():
        if item.data(0) == "panel_ref":
            item.setSelected(True)
    window.composer_canvas.distribute_selected("horizontal")
    sorted_refs = sorted(refs, key=lambda ref: ref.x_mm)
    gap_a = sorted_refs[1].x_mm - (sorted_refs[0].x_mm + sorted_refs[0].width_mm)
    gap_b = sorted_refs[2].x_mm - (sorted_refs[1].x_mm + sorted_refs[1].width_mm)
    assert round(gap_a, 2) == round(gap_b, 2)

    target = refs[0]
    window.composer_canvas.render()
    window.composer_canvas.scene.clearSelection()
    for item in window.composer_canvas.scene.items():
        if item.data(1) == target.ref_uid:
            item.setSelected(True)
    window.composer_canvas.layer_selected("front")
    assert target.layer_index == max(ref.layer_index for ref in refs)
    window.close()
    app.processEvents()


def test_composer_toolbar_can_make_selected_panels_same_size_and_delete_annotations(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    project = Project(title="Composer Same Size Delete")
    panels = []
    for index in range(2):
        image_path = tmp_path / f"same_size_{index}.png"
        image = QImage(120 + index * 40, 80, QImage.Format.Format_RGB32)
        image.fill(0xFFE0E0E0 + index)
        assert image.save(str(image_path))
        panel = ExternalImagePanel(panel_id=f"Figure1{chr(65 + index)}")
        panel.import_image(image_path)
        project.add_panel(panel)
        panels.append(panel)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")
    for index, panel in enumerate(panels):
        window.composer_canvas.place_panel(panel.panel_uuid, scene_pos=QPointF(20 + index * 120, 20))

    refs = project.ensure_active_composer_figure().panel_refs
    refs[0].width_mm = 42.0
    refs[0].height_mm = 30.0
    refs[1].width_mm = 24.0
    refs[1].height_mm = 18.0
    window.composer_canvas.reference_changed()
    window.composer_canvas.render()
    for item in window.composer_canvas.scene.items():
        if item.data(0) == "panel_ref":
            item.setSelected(True)

    window.composer_workspace.same_size_button.click()
    assert {round(ref.width_mm, 2) for ref in refs} == {42.0}
    assert {round(ref.height_mm, 2) for ref in refs} == {30.0}

    annotation = window.composer_canvas.add_text_annotation("remove", QPointF(24, 32))
    window.composer_canvas.render()
    for item in window.composer_canvas.scene.items():
        if item.data(1) == annotation.annotation_uid:
            item.setSelected(True)
    window.composer_workspace.delete_button.click()
    assert not any(a.annotation_uid==annotation.annotation_uid for a in project.ensure_active_composer_figure().annotations)
    window.close()
    app.processEvents()


def test_layout_editing_geometry_records_align_size_and_distribute() -> None:
    records = [
        EditableLayoutRecord(kind="shape", uid="a", model={}, x=0.0, y=4.0, width=10.0, height=8.0),
        EditableLayoutRecord(kind="shape", uid="b", model={}, x=30.0, y=12.0, width=12.0, height=6.0),
        EditableLayoutRecord(kind="shape", uid="c", model={}, x=70.0, y=18.0, width=14.0, height=10.0),
    ]

    make_records_same_size(records)
    assert {(record.width, record.height) for record in records} == {(10.0, 8.0)}

    align_records(records, "top")
    assert {record.y for record in records} == {4.0}

    records[1].x = 40.0
    records[2].x = 100.0
    distribute_records(records, "horizontal")
    gaps = [
        records[1].x - (records[0].x + records[0].width),
        records[2].x - (records[1].x + records[1].width),
    ]
    assert round(gaps[0], 4) == round(gaps[1], 4)


def test_composer_shape_annotations_are_editable_arrangeable_and_exported(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("composer")

    window.composer_workspace.controller.add_annotation('ellipse')
    window.composer_workspace.controller.apply_style({'stroke_width':2.5,'stroke_color':'#C0392B','fill_color':'#22D7E6','fill_alpha':1.0})
    window.composer_workspace.controller.add_annotation('rectangle')

    figure = window.project.ensure_active_composer_figure()
    shapes = [annotation for annotation in figure.annotations if annotation.type == "shape"]
    assert len(shapes) == 2
    assert shapes[0].properties["shape_type"] == "ellipse"
    assert shapes[0].properties["stroke_width_pt"] == 2.5
    assert shapes[0].properties["stroke_color"] == "#C0392B"
    assert shapes[0].properties["fill_color"] == "#22D7E6"

    window.composer_canvas.render()
    shape_items = [item for item in window.composer_canvas.scene.items() if item.data(0) == "annotation" and item.data(2) == "shape"]
    assert len(shape_items) == 2
    for item in shape_items:
        handles = list(item.handles.values())
        assert len(handles) == 8
        item.setSelected(True)

    first_shape_item = next(item for item in shape_items if item.data(1) == shapes[0].annotation_uid)
    first_shape_item.apply_geometry(8.0, 9.0, 26.0, 14.0)
    assert shapes[0].x_mm == 8.0
    assert shapes[0].y_mm == 9.0
    assert shapes[0].properties["width_mm"] == 26.0
    assert shapes[0].properties["height_mm"] == 14.0

    shapes[1].properties["width_mm"] = 8.0
    shapes[1].properties["height_mm"] = 6.0
    window.composer_canvas.make_selected_same_size()
    assert {round(shape.properties["width_mm"], 2) for shape in shapes} == {26.0}
    assert {round(shape.properties["height_mm"], 2) for shape in shapes} == {14.0}

    output = tmp_path / "composer_shapes.png"
    window.composer_canvas.export_figure(output, dpi=300)
    with Image.open(output) as image:
        pixels = np.asarray(image.convert("RGBA"))
        assert np.any((pixels[..., 0] < 80) & (pixels[..., 1] > 150) & (pixels[..., 2] > 150))

    for item in window.composer_canvas.scene.items():
        if item.data(1) == shapes[0].annotation_uid:
            item.setSelected(True)
        elif item.data(0) == "annotation":
            item.setSelected(False)
    window.composer_workspace.delete_button.click()
    assert [annotation.annotation_uid for annotation in figure.annotations] == [shapes[1].annotation_uid]

    window.close()
    app.processEvents()


def test_composer_export_uses_physical_size_dpi_and_source_package(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    green_path = tmp_path / "green.png"
    red_path = tmp_path / "red.png"
    green = QImage(320, 240, QImage.Format.Format_RGB32)
    green.fill(0xFF00FF00)
    red = QImage(320, 240, QImage.Format.Format_RGB32)
    red.fill(0xFFFF0000)
    assert green.save(str(green_path))
    assert red.save(str(red_path))
    project = Project(title="Export Quality")
    green_panel = ExternalImagePanel(panel_id="Figure1A")
    red_panel = ExternalImagePanel(panel_id="Figure1B")
    green_panel.import_image(green_path)
    red_panel.import_image(red_path)
    project.add_panel(green_panel)
    project.add_panel(red_panel)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.project = project
    window.set_module("composer")
    figure = project.ensure_active_composer_figure()
    figure.canvas.update({"width_mm": 50.0, "height_mm": 30.0})
    window.composer_canvas.layout_model.width_mm = 50.0
    window.composer_canvas.layout_model.height_mm = 30.0
    window.composer_canvas.layout_model.rebuild_empty_cells()
    window.composer_canvas.place_panel(green_panel.panel_uuid, scene_pos=QPointF(0, 0))
    window.composer_canvas.place_panel(red_panel.panel_uuid, scene_pos=QPointF(96, 0))
    refs = figure.panel_refs
    refs[0].x_mm = 0.0
    refs[0].y_mm = 0.0
    refs[0].width_mm = 20.0
    refs[0].height_mm = 15.0
    refs[1].x_mm = 24.0
    refs[1].y_mm = 0.0
    refs[1].width_mm = 20.0
    refs[1].height_mm = 15.0
    refs[1].visible = False
    window.composer_canvas.reference_changed()

    png_path = tmp_path / "figure.png"
    window.composer_canvas.export_figure(png_path, dpi=300, include_source_data=True)
    with Image.open(png_path) as image:
        assert image.size == (591, 354)
        rgba = image.convert("RGBA")
        assert rgba.getbbox() is not None
        pixels = np.asarray(rgba)
        assert not np.any((pixels[..., 0] > 220) & (pixels[..., 1] < 60) & (pixels[..., 2] < 60))
    source_dir = tmp_path / "figure_source"
    assert (source_dir / "figure_composer.json").exists()
    assert (source_dir / "figure_panels.json").exists()
    assert (source_dir / "figure_audit.log").exists()
    assert (source_dir / "external_source_files" / "green.png").exists()
    assert (source_dir / "external_source_files" / "red.png").exists()
    assert any("hidden object" in item["message"] for item in figure.export_settings["last_preflight"]["messages"])

    tiff_path = tmp_path / "figure.tiff"
    window.composer_canvas.export_figure(tiff_path, dpi=300)
    with Image.open(tiff_path) as image:
        assert image.size == (591, 354)
        dpi = image.info.get("dpi")
        if dpi:
            assert abs(dpi[0] - 300) < 2

    pdf_path = tmp_path / "figure.pdf"
    window.composer_canvas.export_figure(pdf_path, dpi=300)
    document = QPdfDocument(None)
    assert document.load(str(pdf_path)) == QPdfDocument.Error.None_
    page_size = document.pagePointSize(0)
    assert abs(page_size.width() - 50.0 * 72 / 25.4) < 1.0
    assert abs(page_size.height() - 30.0 * 72 / 25.4) < 1.0
    window.close()
    app.processEvents()
