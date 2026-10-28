from __future__ import annotations

import os

from PyQt6 import sip
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QLabel

from core.models import FluorescencePanel
from core.utils.i18n import set_language, tr
from ui.panels.fluorescence_workspace import FluorescenceWorkspace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _deliver_deferred_deletes() -> None:
    _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def _channel_owned_form_labels(workspace: FluorescenceWorkspace) -> list[QLabel]:
    labels = []
    for label in list(workspace._form_i18n_labels) + list(getattr(workspace, "_channel_form_i18n_labels", [])):
        if sip.isdeleted(label):
            continue
        key = label.property("i18nKey")
        if key in {"min", "max", "color"} and workspace.channel_controls.isAncestorOf(label):
            labels.append(label)
    return labels


def _owned_i18n_labels(workspace: FluorescenceWorkspace) -> list[QLabel]:
    return list(workspace._form_i18n_labels) + list(getattr(workspace, "_channel_form_i18n_labels", []))


def _panel_with_channels() -> FluorescencePanel:
    panel = FluorescencePanel(panel_id="retranslate-lifecycle")
    panel.data["fluorescence_images"] = [
        {
            "uid": "img-a",
            "source_name": "sample.tif",
            "metadata": {},
            "channels": [
                {"index": 0, "name": "DAPI", "visible": True, "display_min": 0, "display_max": 100, "pseudocolor": "#0066FF"},
                {"index": 1, "name": "FITC", "visible": True, "display_min": 0, "display_max": 200, "pseudocolor": "#00D26A"},
            ],
        }
    ]
    panel.data["active_image_uid"] = "img-a"
    return panel


def test_channel_rebuild_is_the_producer_of_stale_form_labels_and_retranslate_survives_deferred_delete():
    set_language("en")
    workspace = FluorescenceWorkspace()
    panel = _panel_with_channels()
    workspace.set_panel(panel, refresh=False)
    workspace._populate_channel_controls()
    produced = _channel_owned_form_labels(workspace)
    produced_keys = [label.property("i18nKey") for label in produced]
    assert produced
    assert {"min", "max", "color"} <= set(produced_keys)
    owned_before = _owned_i18n_labels(workspace)
    assert all(label in owned_before for label in produced)

    workspace._populate_channel_controls()
    _deliver_deferred_deletes()
    assert all(sip.isdeleted(label) for label in produced)

    for label in _owned_i18n_labels(workspace):
        assert not sip.isdeleted(label)

    set_language("zh")
    workspace.retranslate()
    live = _channel_owned_form_labels(workspace)
    assert live
    assert {label.text() for label in live if label.property("i18nKey") == "min"} == {tr("min")}
    assert {label.text() for label in live if label.property("i18nKey") == "max"} == {tr("max")}
    assert {label.text() for label in live if label.property("i18nKey") == "color"} == {tr("color")}
    static = [label for label in workspace._form_i18n_labels if label.property("i18nKey") == "format"]
    assert static and static[0].text() == tr("format")

    for language in ("en", "zh", "en", "zh"):
        set_language(language)
        workspace._populate_channel_controls()
        _deliver_deferred_deletes()
        workspace.retranslate()
        live = _channel_owned_form_labels(workspace)
        assert live
        assert all(not sip.isdeleted(label) for label in _owned_i18n_labels(workspace))
        assert {label.text() for label in live if label.property("i18nKey") == "min"} == {tr("min")}
        workflow = [label for label in workspace._workflow_i18n_labels if not sip.isdeleted(label)]
        assert workflow
        assert workflow[0].text() == tr(str(workflow[0].property("i18nKey")))

    set_language("en")
    workspace.close()
    workspace.deleteLater()
    _deliver_deferred_deletes()


def test_layout_font_change_after_canvas_delete_does_not_touch_deleted_scene():
    workspace = FluorescenceWorkspace()
    canvas = workspace.canvas
    canvas.setParent(None)
    canvas.deleteLater()
    _deliver_deferred_deletes()
    assert sip.isdeleted(canvas)
    workspace._apply_layout_text_style()
    workspace.close()
    workspace.deleteLater()
    _deliver_deferred_deletes()
