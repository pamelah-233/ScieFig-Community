from __future__ import annotations

from copy import deepcopy

import pytest

from core.io.migrations.ihc_hscore_v611 import migrate_ihc_hscore_v611


def _legacy_measurement(
    *,
    measurement_uuid: str = "m-1",
    h_score: float = 142.86,
    positive_percent: float = 47.62,
    method: str | None = "binary_simplified_3x_positive",
) -> dict:
    gmm = {} if method is None else {"h_score_method": method, "gmm_n_components": 2}
    return {
        "measurement_uuid": measurement_uuid,
        "image_uid": "img-1",
        "results": {
            "h_score": h_score,
            "positive_percent": positive_percent,
            "weak_percent": 0.0,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "negative_percent": 52.38,
            "positive_area_percent": positive_percent,
        },
        "analysis_metadata": {"gmm_classification": gmm},
    }


def _panel_payload(measurement: dict) -> dict:
    return {
        "panel_type": "ihc",
        "audit_log": [],
        "data": {
            "ihc_images": [
                {
                    "uid": "img-1",
                    "source_name": "legacy.png",
                    "measurements": [measurement.copy()],
                }
            ]
        },
        "analysis": {"ihc_measurements": [measurement]},
    }


def test_migrate_ihc_hscore_uses_positive_percent_not_old_divided_by_three() -> None:
    measurement = _legacy_measurement(h_score=150.0, positive_percent=47.62)
    panel_payload = _panel_payload(measurement)

    entries = migrate_ihc_hscore_v611(panel_payload)

    migrated = panel_payload["analysis"]["ihc_measurements"][0]
    assert migrated["results"]["h_score"] == pytest.approx(47.62)
    assert migrated["results"]["weak_percent"] == pytest.approx(47.62)
    assert migrated["results"]["moderate_percent"] == 0.0
    assert migrated["results"]["strong_percent"] == 0.0
    assert migrated["analysis_metadata"]["gmm_classification"]["_pre_migration_h_score"] == pytest.approx(150.0)
    assert (
        migrated["analysis_metadata"]["gmm_classification"]["h_score_method"]
        == "gmm_2_component_positive_conservative"
    )
    assert len(entries) == 1


def test_migrate_ihc_hscore_writes_existing_audit_schema() -> None:
    panel_payload = _panel_payload(_legacy_measurement())

    entries = migrate_ihc_hscore_v611(panel_payload)

    assert entries == panel_payload["audit_log"]
    assert entries[0]["action"] == "h_score_migration_v611"
    assert "timestamp" in entries[0]
    assert "measurement_uuid=m-1" in entries[0]["detail"]
    assert "old_h_score=142.8600" in entries[0]["detail"]
    assert "new_h_score=47.6200" in entries[0]["detail"]


def test_migrate_ihc_hscore_is_idempotent_after_v71_migration() -> None:
    panel_payload = _panel_payload(_legacy_measurement())

    first_entries = migrate_ihc_hscore_v611(panel_payload)
    assert len(first_entries) == 1
    migrated = panel_payload["analysis"]["ihc_measurements"][0]
    assert (
        migrated["analysis_metadata"]["gmm_classification"]["h_score_method"]
        == "gmm_2_component_positive_conservative"
    )

    second_entries = migrate_ihc_hscore_v611(panel_payload)
    assert second_entries == []
    assert len(panel_payload["audit_log"]) == 1
    assert migrated["results"]["h_score"] == pytest.approx(47.62)


def test_migrate_ihc_hscore_records_warning_when_old_value_is_not_three_x_positive() -> None:
    panel_payload = _panel_payload(_legacy_measurement(h_score=80.0, positive_percent=47.62))

    migrate_ihc_hscore_v611(panel_payload)

    gmm = panel_payload["analysis"]["ihc_measurements"][0]["analysis_metadata"]["gmm_classification"]
    assert "_migration_warning" in gmm
    assert "not consistent" in gmm["_migration_warning"]


def test_migrate_ihc_hscore_skips_conservative_measurement() -> None:
    panel_payload = _panel_payload(
        _legacy_measurement(method="gmm_2_component_positive_conservative", h_score=47.62)
    )

    entries = migrate_ihc_hscore_v611(panel_payload)

    assert entries == []
    assert panel_payload["analysis"]["ihc_measurements"][0]["results"]["h_score"] == pytest.approx(47.62)
    assert panel_payload["audit_log"] == []


def test_migrate_ihc_hscore_skips_missing_gmm_metadata() -> None:
    measurement = _legacy_measurement(method=None)
    panel_payload = _panel_payload(measurement)

    entries = migrate_ihc_hscore_v611(panel_payload)

    assert entries == []
    assert panel_payload["analysis"]["ihc_measurements"][0]["results"]["h_score"] == pytest.approx(142.86)


def test_migrate_ihc_hscore_preserves_other_result_fields() -> None:
    panel_payload = _panel_payload(_legacy_measurement())
    before = dict(panel_payload["analysis"]["ihc_measurements"][0]["results"])

    migrate_ihc_hscore_v611(panel_payload)

    after = panel_payload["analysis"]["ihc_measurements"][0]["results"]
    for key in ("positive_percent", "negative_percent", "positive_area_percent"):
        assert after[key] == before[key]
    assert after["moderate_percent"] == 0.0
    assert after["strong_percent"] == 0.0


def test_migrate_ihc_hscore_updates_image_measurement_copy_and_audits_once() -> None:
    panel_payload = _panel_payload(_legacy_measurement())

    migrate_ihc_hscore_v611(panel_payload)

    image_measurement = panel_payload["data"]["ihc_images"][0]["measurements"][0]
    assert image_measurement["results"]["h_score"] == pytest.approx(47.62)
    assert image_measurement["results"]["weak_percent"] == pytest.approx(47.62)
    assert len(panel_payload["audit_log"]) == 1


def test_migrate_ihc_hscore_accepts_direct_analysis_payload() -> None:
    panel_data = {"ihc_measurements": [_legacy_measurement()], "audit_log": []}

    migrate_ihc_hscore_v611(panel_data)

    assert panel_data["ihc_measurements"][0]["results"]["h_score"] == pytest.approx(47.62)
    assert panel_data["audit_log"][0]["action"] == "h_score_migration_v611"


def test_migrate_ihc_hscore_audits_once_when_copies_have_no_uuid() -> None:
    measurement = _legacy_measurement(measurement_uuid="")
    panel_payload = {
        "panel_type": "ihc",
        "audit_log": [],
        "data": {"ihc_images": [{"uid": "img-1", "measurements": [deepcopy(measurement)]}]},
        "analysis": {"ihc_measurements": [measurement]},
    }

    entries = migrate_ihc_hscore_v611(panel_payload)

    assert len(entries) == 1
    assert len(panel_payload["audit_log"]) == 1
    assert panel_payload["analysis"]["ihc_measurements"][0]["results"]["h_score"] == pytest.approx(47.62)
    assert panel_payload["data"]["ihc_images"][0]["measurements"][0]["results"]["h_score"] == pytest.approx(47.62)


def test_migrate_ihc_hscore_audits_each_no_uuid_logical_measurement_with_identical_values() -> None:
    first = _legacy_measurement(measurement_uuid="")
    second = _legacy_measurement(measurement_uuid="")
    panel_payload = {
        "panel_type": "ihc",
        "audit_log": [],
        "data": {
            "ihc_images": [
                {
                    "uid": "img-1",
                    "measurements": [deepcopy(first), deepcopy(second)],
                }
            ]
        },
        "analysis": {"ihc_measurements": [first, second]},
    }

    entries = migrate_ihc_hscore_v611(panel_payload)

    assert len(entries) == 2
    assert len(panel_payload["audit_log"]) == 2
    for measurement in panel_payload["analysis"]["ihc_measurements"]:
        assert measurement["results"]["h_score"] == pytest.approx(47.62)
        assert measurement["results"]["weak_percent"] == pytest.approx(47.62)
    for measurement in panel_payload["data"]["ihc_images"][0]["measurements"]:
        assert measurement["results"]["h_score"] == pytest.approx(47.62)
        assert measurement["results"]["weak_percent"] == pytest.approx(47.62)


def test_migrate_ihc_hscore_skips_missing_positive_percent_without_mutating_measurement() -> None:
    measurement = _legacy_measurement()
    measurement["results"].pop("positive_percent")
    measurement["results"]["weak_percent"] = 12.5
    panel_payload = _panel_payload(measurement)

    entries = migrate_ihc_hscore_v611(panel_payload)

    migrated = panel_payload["analysis"]["ihc_measurements"][0]
    assert entries == []
    assert panel_payload["audit_log"] == []
    assert migrated["results"]["h_score"] == pytest.approx(142.86)
    assert migrated["results"]["weak_percent"] == pytest.approx(12.5)
    assert migrated["analysis_metadata"]["gmm_classification"]["h_score_method"] == "binary_simplified_3x_positive"


def test_migrate_ihc_hscore_skips_non_numeric_positive_percent_without_mutating_measurement() -> None:
    measurement = _legacy_measurement()
    measurement["results"]["positive_percent"] = "not-a-number"
    measurement["results"]["weak_percent"] = 12.5
    panel_payload = _panel_payload(measurement)

    entries = migrate_ihc_hscore_v611(panel_payload)

    migrated = panel_payload["analysis"]["ihc_measurements"][0]
    assert entries == []
    assert panel_payload["audit_log"] == []
    assert migrated["results"]["h_score"] == pytest.approx(142.86)
    assert migrated["results"]["weak_percent"] == pytest.approx(12.5)
    assert migrated["analysis_metadata"]["gmm_classification"]["h_score_method"] == "binary_simplified_3x_positive"


def test_migrate_ihc_hscore_does_not_create_audit_log_when_nothing_migrates() -> None:
    panel_data = {"ihc_measurements": [_legacy_measurement(method="gmm_2_component_positive_conservative")]}

    entries = migrate_ihc_hscore_v611(panel_data)

    assert entries == []
    assert "audit_log" not in panel_data


def test_migrate_ihc_hscore_clears_legacy_strong_and_moderate_percents() -> None:
    measurement = _legacy_measurement(h_score=90.0, positive_percent=30.0)
    measurement["results"]["strong_percent"] = 30.0
    measurement["results"]["moderate_percent"] = 0.0
    measurement["results"]["weak_percent"] = 0.0
    panel_payload = _panel_payload(measurement)

    migrate_ihc_hscore_v611(panel_payload)

    results = panel_payload["analysis"]["ihc_measurements"][0]["results"]
    assert results["h_score"] == pytest.approx(30.0)
    assert results["weak_percent"] == pytest.approx(30.0)
    assert results["moderate_percent"] == 0.0
    assert results["strong_percent"] == 0.0

    gmm = panel_payload["analysis"]["ihc_measurements"][0]["analysis_metadata"]["gmm_classification"]
    assert gmm["_pre_migration_strong_percent"] == pytest.approx(30.0)
    assert gmm["_pre_migration_weak_percent"] == 0.0


def test_migrate_ihc_hscore_synchronizes_nested_gmm_classification() -> None:
    measurement = _legacy_measurement(h_score=90.0, positive_percent=30.0)
    measurement["analysis_metadata"]["gmm_classification"].update(
        {
            "h_score": 90.0,
            "weak_percent": 0.0,
            "moderate_percent": 0.0,
            "strong_percent": 30.0,
            "positive_percent": 30.0,
            "negative_percent": 70.0,
            "percentages": {
                "negative": 70.0,
                "weak": 0.0,
                "moderate": 0.0,
                "strong": 30.0,
                "positive": 30.0,
            },
        }
    )
    panel_payload = _panel_payload(measurement)

    migrate_ihc_hscore_v611(panel_payload)

    migrated = panel_payload["analysis"]["ihc_measurements"][0]
    results = migrated["results"]
    gmm = migrated["analysis_metadata"]["gmm_classification"]

    assert results["h_score"] == pytest.approx(30.0)
    assert results["weak_percent"] == pytest.approx(30.0)
    assert results["moderate_percent"] == 0.0
    assert results["strong_percent"] == 0.0

    assert gmm["h_score"] == pytest.approx(30.0)
    assert gmm["weak_percent"] == pytest.approx(30.0)
    assert gmm["moderate_percent"] == 0.0
    assert gmm["strong_percent"] == 0.0
    assert gmm["positive_percent"] == pytest.approx(30.0)
    assert gmm["negative_percent"] == pytest.approx(70.0)

    assert gmm["percentages"]["weak"] == pytest.approx(30.0)
    assert gmm["percentages"]["moderate"] == 0.0
    assert gmm["percentages"]["strong"] == 0.0
    assert gmm["percentages"]["positive"] == pytest.approx(30.0)
    assert gmm["percentages"]["negative"] == pytest.approx(70.0)

    assert gmm["h_score_method"] == "gmm_2_component_positive_conservative"
    assert gmm["_pre_migration_h_score_method"] == "binary_simplified_3x_positive"
