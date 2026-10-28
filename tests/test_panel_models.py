from __future__ import annotations

from core.constants import SCHEMA_VERSION, SOFTWARE_VERSION
from core.models import BasePanel, IHCPanel, QPCRPanel, WesternBlotPanel
from core.models.panel import panel_from_dict


def test_base_panel_serializes_and_deserializes_required_fields() -> None:
    panel = BasePanel(panel_id="Figure1A", panel_type="mock")
    panel.data["raw_hash"] = "sha256:abc"
    panel.append_audit("import_data", "mock.csv")

    payload = panel.to_dict()
    restored = BasePanel.from_dict(payload)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["software_version"] == SOFTWARE_VERSION
    assert payload["panel_uuid"] == restored.panel_uuid
    assert restored.panel_id == "Figure1A"
    assert restored.panel_type == "mock"
    assert restored.coordinate_system["layout_space"] == "millimeter"
    assert restored.unit_system["layout_length"] == "mm"
    assert restored.audit_log[0].action == "import_data"


def test_panel_factory_restores_subclass_by_panel_type() -> None:
    qpcr = QPCRPanel(panel_id="Figure1B")
    restored = panel_from_dict(qpcr.to_dict())
    assert isinstance(restored, QPCRPanel)
    assert restored.panel_type == "qPCR_bar_scatter"

    wb = WesternBlotPanel(panel_id="Figure1C")
    restored_wb = panel_from_dict(wb.to_dict())
    assert isinstance(restored_wb, WesternBlotPanel)

    ihc = IHCPanel(panel_id="Figure1D")
    restored_ihc = panel_from_dict(ihc.to_dict())
    assert isinstance(restored_ihc, IHCPanel)
    assert restored_ihc.panel_type == "ihc"


def test_ihc_panel_initializes_default_data_contract() -> None:
    panel = IHCPanel(panel_id="Figure1E")

    assert panel.panel_type == "ihc"
    assert panel.source_module == "ihc"
    assert panel.images() == []
    assert panel.active_image() is None
    assert panel.data["ihc_images"] == []
    assert panel.data["active_image_uid"] is None
    assert panel.data["active_step"] == 0
    assert panel.data["analysis_settings"] == {
            "positive_threshold": 0.20,
        "weak_threshold": 0.20,
        "moderate_threshold": 0.50,
        "strong_threshold": 0.80,
        "tissue_mask_enabled": False,
        "min_tissue_area_px": 100,
        "tissue_mask_saturation_threshold": 0.08,
        "tissue_mask_value_threshold": 0.90,
        "tissue_mask_min_area_px": 100,
    }
    assert panel.data["statistics"] == {}
    assert panel.data["composer_outputs"] == []


def test_audit_append_updates_modified_at() -> None:
    panel = BasePanel(panel_id="Figure1D", panel_type="mock")
    previous_modified = panel.modified_at
    panel.append_audit("set_style", "Nature preset")

    assert len(panel.audit_log) == 1
    assert panel.audit_log[0].detail == "Nature preset"
    assert panel.modified_at >= previous_modified
