from __future__ import annotations

from copy import deepcopy

import pytest

from core.models import ComposerFigure, PanelReference, Project, QPCRPanel, WesternBlotPanel
from core.models.composer import AnnotationObject, ComposerLayout


def test_project_new_add_and_delete_panel() -> None:
    project = Project(title="Phase 1 Test")
    qpcr = QPCRPanel(panel_id="Figure1A")
    wb = WesternBlotPanel(panel_id="Figure1B")

    project.add_panel(qpcr)
    project.add_panel(wb)
    removed = project.remove_panel(qpcr.panel_uuid)

    assert removed.panel_id == "Figure1A"
    assert len(project.panels) == 1
    assert project.panels[0].panel_id == "Figure1B"
    assert [entry.action for entry in project.audit_log] == ["add_panel", "add_panel", "remove_panel"]


def test_project_delete_missing_panel_raises() -> None:
    project = Project()
    with pytest.raises(KeyError):
        project.remove_panel("missing")


def test_project_remove_panel_clears_all_composer_references_and_layout_cells() -> None:
    project = Project(title="Composer Reference Cleanup")
    removed_panel = QPCRPanel(panel_id="Figure1A")
    kept_panel = WesternBlotPanel(panel_id="Figure1B")
    project.add_panel(removed_panel)
    project.add_panel(kept_panel)

    first_figure = ComposerFigure(figure_id="Figure 1")
    first_figure.annotations.append(AnnotationObject(type="text", properties={"content": "keep"}))
    kept_ref = PanelReference(
        panel_uid=kept_panel.panel_uuid,
        source_module=kept_panel.source_module,
        figure_label="b",
        x_mm=17.0,
        y_mm=23.0,
        width_mm=41.0,
        height_mm=29.0,
    )
    first_figure.panel_refs = [
        PanelReference(panel_uid=removed_panel.panel_uuid, source_module=removed_panel.source_module),
        kept_ref,
    ]
    second_figure = ComposerFigure(figure_id="Figure 2")
    second_figure.panel_refs.append(
        PanelReference(panel_uid=removed_panel.panel_uuid, source_module=removed_panel.source_module)
    )
    project.set_composer_figures([first_figure, second_figure], second_figure.figure_uid)
    layout = ComposerLayout.create("2x2")
    layout.place_panel(removed_panel.panel_uuid)
    layout.place_panel(kept_panel.panel_uuid)
    project.composer_layout = layout.to_dict()

    removed = project.remove_panel(removed_panel.panel_uuid)

    assert removed is removed_panel
    assert project.panels == [kept_panel]
    assert [ref.panel_uid for ref in first_figure.panel_refs] == [kept_panel.panel_uuid]
    assert second_figure.panel_refs == []
    assert first_figure.annotations[0].properties["content"] == "keep"
    assert kept_ref.x_mm == 17.0
    assert kept_ref.y_mm == 23.0
    assert kept_ref.width_mm == 41.0
    assert kept_ref.height_mm == 29.0
    assert [figure.figure_uid for figure in project.composer_figures] == [first_figure.figure_uid, second_figure.figure_uid]
    assert project.active_figure_uid == second_figure.figure_uid
    assert all(cell["panel_uuid"] != removed_panel.panel_uuid for cell in project.composer_layout["cells"])
    assert project.composer_layout["cells"][0]["panel_uuid"] is None
    assert project.composer_layout["cells"][0]["panel_letter"] is None


def test_project_remove_missing_panel_does_not_mutate_composer_state() -> None:
    project = Project(title="Missing Composer Reference")
    panel = QPCRPanel(panel_id="Figure1A")
    project.add_panel(panel)
    figure = ComposerFigure(figure_id="Figure 1")
    figure.panel_refs.append(PanelReference(panel_uid=panel.panel_uuid, source_module=panel.source_module))
    project.set_composer_figures([figure], figure.figure_uid)
    layout = ComposerLayout.create("2x2")
    layout.place_panel(panel.panel_uuid)
    project.composer_layout = layout.to_dict()
    before_layout = deepcopy(project.composer_layout)
    before_refs = list(figure.panel_refs)
    before_figures = list(project.composer_figures)
    before_active_uid = project.active_figure_uid
    before_audit = list(project.audit_log)

    with pytest.raises(KeyError):
        project.remove_panel("missing")

    assert project.composer_layout == before_layout
    assert figure.panel_refs == before_refs
    assert project.composer_figures == before_figures
    assert project.active_figure_uid == before_active_uid
    assert project.audit_log == before_audit


def test_project_serialization_round_trip() -> None:
    project = Project(title="Serializable")
    project.append_audit("create_project", "Serializable")
    project.add_panel(QPCRPanel(panel_id="Figure1A"))

    restored = Project.from_dict(project.to_dict())

    assert restored.project_uuid == project.project_uuid
    assert restored.title == "Serializable"
    assert len(restored.panels) == 1
    assert restored.panels[0].panel_id == "Figure1A"
    assert restored.audit_log[0].action == "create_project"


def test_project_registry_and_composer_figure_use_panel_uuid() -> None:
    project = Project(title="v2.3 Data Model")
    qpcr = QPCRPanel(panel_id="Figure1A")
    project.add_panel(qpcr)
    figure = ComposerFigure(figure_id="Figure 1")
    figure.panel_refs.append(
        PanelReference(
            panel_uid=qpcr.panel_uuid,
            source_module=qpcr.source_module,
            figure_label="A",
            x_mm=5,
            y_mm=6,
            width_mm=40,
            height_mm=30,
        )
    )
    project.set_composer_figures([figure], figure.figure_uid)

    assert project.panel_registry().get(qpcr.panel_uuid) is qpcr
    assert project.to_dict()["composer_figures"][0]["file"] == "composer/Figure1.json"
    assert project.ensure_active_composer_figure().panel_refs[0].panel_uid == qpcr.panel_uuid


def test_project_from_dict_preserves_index_only_composer_figures() -> None:
    project = Project(title="Indexed Figures")
    qpcr = QPCRPanel(panel_id="Figure1A")
    project.add_panel(qpcr)
    figure = ComposerFigure(figure_id="Figure 2")
    project.set_composer_figures([figure], figure.figure_uid)

    restored = Project.from_dict(project.to_dict())

    assert len(restored.composer_figures) == 1
    assert restored.composer_figures[0].figure_uid == figure.figure_uid
    assert restored.composer_figures[0].figure_id == "Figure 2"
    assert restored.composer_figures[0].file_name == "composer/Figure2.json"


def test_composer_figure_from_index_record_preserves_file_reference() -> None:
    figure = ComposerFigure.from_dict(
        {
            "figure_uid": "fig-123",
            "figure_id": "Figure 9",
            "file": "composer/Figure9.json",
        }
    )

    assert figure.figure_uid == "fig-123"
    assert figure.figure_id == "Figure 9"
    assert figure.file_name == "composer/Figure9.json"


def test_project_add_panel_rejects_duplicate_panel_uuid() -> None:
    project = Project(title="Duplicate Guard")
    qpcr = QPCRPanel(panel_id="Figure1A", panel_uuid="duplicate-panel")
    wb = WesternBlotPanel(panel_id="Figure1B", panel_uuid="duplicate-panel")

    project.add_panel(qpcr)

    with pytest.raises(ValueError, match="Duplicate panel_uuid"):
        project.add_panel(wb)


def test_project_save_rejects_duplicate_panel_uuid_even_if_panels_list_was_mutated(tmp_path) -> None:
    project = Project(title="Duplicate Save Guard")
    project.panels = [
        QPCRPanel(panel_id="Figure1A", panel_uuid="duplicate-panel"),
        WesternBlotPanel(panel_id="Figure1B", panel_uuid="duplicate-panel"),
    ]

    with pytest.raises(ValueError, match="Duplicate panel_uuid"):
        project.save_sfig(tmp_path / "duplicate.sfig")
