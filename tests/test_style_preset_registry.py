from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.models.style_profile_draft import STYLE_PROFILE_DRAFT_WARNINGS, StyleProfileDraft
from core.plotting.style_presets import get_journal_preset, plot_style_from_preset
from core.style_preset_resolver import (
    clear_runtime_active_style_preset,
    last_runtime_style_preset_status,
    resolve_image_plate_style,
    resolve_plot_style,
    set_runtime_active_style_preset,
)
from core.style_preset_registry import StylePresetRecord, StylePresetRegistry, StylePresetRegistryError


@pytest.fixture(autouse=True)
def _clear_runtime_style_preset_between_tests():
    clear_runtime_active_style_preset()
    yield
    clear_runtime_active_style_preset()


def _draft(*, display_name: str = "Reference draft") -> StyleProfileDraft:
    draft = StyleProfileDraft.seeded_from_builtin_preset(
        seed_preset_name="Nature",
        source_kind="raster",
        source_path="reference.png",
        source_page_index=0,
        source_page_count=1,
        source_size_px={"width": 120, "height": 80},
        source_crop_rect={"x": 10, "y": 12, "width": 30, "height": 24},
        extraction_profile={
            "crop_size_px": {"width": 30, "height": 24},
            "background_color": "#FFFFFF",
            "axis_color": "#202020",
            "series_candidates": ["#0F4D92", "#B64342"],
            "warnings": ["approximate crop"],
        },
        display_name=display_name,
    )
    draft.plot_style["font_family"] = "Arial"
    draft.plot_style["point_color"] = "#0F4D92"
    return draft


def test_style_preset_registry_save_update_list_and_get_detach_nested_payloads(tmp_path, monkeypatch) -> None:
    timestamps = iter(
        [
            "2026-06-12T08:00:00+00:00",
            "2026-06-12T08:05:00+00:00",
        ]
    )
    monkeypatch.setattr("core.style_preset_registry.utc_now_iso", lambda: next(timestamps))

    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    assert registry.list_presets() == []
    assert registry.get_preset("missing") is None

    saved = registry.save_draft(_draft(), source_type="extracted_draft")
    assert saved.display_name == "Reference draft"
    assert saved.source_type == "extracted_draft"
    assert saved.created_at == "2026-06-12T08:00:00+00:00"
    assert saved.updated_at == "2026-06-12T08:00:00+00:00"

    fetched = registry.get_preset(saved.preset_id)
    assert fetched is not None
    fetched.display_name = "Mutated outside registry"
    fetched.plot_style["point_color"] = "#FFFFFF"
    fetched.extraction_profile["series_candidates"].append("#ABCDEF")
    fetched.source_metadata["source_crop_rect"]["width"] = 999

    after_external_mutation = registry.get_preset(saved.preset_id)
    assert after_external_mutation is not None
    assert after_external_mutation.display_name == "Reference draft"
    assert after_external_mutation.plot_style["point_color"] == "#0F4D92"
    assert after_external_mutation.extraction_profile["series_candidates"] == ["#0F4D92", "#B64342"]
    assert after_external_mutation.source_metadata["source_crop_rect"]["width"] == 30

    to_update = registry.get_preset(saved.preset_id)
    assert to_update is not None
    to_update.display_name = "Renamed draft"
    to_update.plot_style["font_family"] = "Courier New"
    updated = registry.save_preset(to_update)
    assert updated.created_at == "2026-06-12T08:00:00+00:00"
    assert updated.updated_at == "2026-06-12T08:05:00+00:00"

    listed = registry.list_presets()
    assert [preset.display_name for preset in listed] == ["Renamed draft"]
    assert listed[0].plot_style["font_family"] == "Courier New"

    reloaded = StylePresetRegistry(tmp_path / "style_presets.json")
    reloaded_preset = reloaded.get_preset(saved.preset_id)
    assert reloaded_preset is not None
    assert reloaded_preset.display_name == "Renamed draft"
    assert reloaded_preset.plot_style["font_family"] == "Courier New"


def test_style_preset_registry_rename_duplicate_delete_and_builtin_copy_do_not_mutate_builtin(tmp_path) -> None:
    builtin_before = get_journal_preset("Nature")
    registry = StylePresetRegistry(tmp_path / "style_presets.json")

    builtin_copy = StylePresetRecord.from_builtin_preset_copy("Nature", display_name="Nature copy")
    saved = registry.save_preset(builtin_copy)
    renamed = registry.rename_preset(saved.preset_id, "Nature copy renamed")
    duplicate = registry.duplicate_preset(renamed.preset_id)

    assert renamed.preset_id == saved.preset_id
    assert renamed.display_name == "Nature copy renamed"
    assert duplicate.preset_id != renamed.preset_id
    assert duplicate.display_name == "Nature copy renamed copy"
    assert duplicate.source_type == "user_modified"
    assert duplicate.plot_style == renamed.plot_style
    assert duplicate.source_metadata["builtin_preset_name"] == "Nature"

    assert registry.delete_preset(renamed.preset_id) is True
    assert registry.delete_preset(renamed.preset_id) is False
    remaining = registry.list_presets()
    assert [preset.preset_id for preset in remaining] == [duplicate.preset_id]

    builtin_after = get_journal_preset("Nature")
    assert builtin_after == builtin_before


def test_style_preset_registry_rejects_corrupt_json_payload(tmp_path) -> None:
    path = tmp_path / "style_presets.json"
    path.write_text("{not valid json", encoding="utf-8")

    registry = StylePresetRegistry(path)
    with pytest.raises(StylePresetRegistryError, match="style preset registry"):
        registry.list_presets()


def test_style_preset_record_round_trips_through_style_profile_draft() -> None:
    record = StylePresetRecord.from_style_profile_draft(_draft(display_name="Draft to save"), source_type="extracted_draft")

    restored = StylePresetRecord.from_dict(record.to_dict())
    draft = restored.to_style_profile_draft()

    assert restored == record
    assert draft.display_name == "Draft to save"
    assert draft.seed_preset_name == "Nature"
    assert draft.source_crop_rect == {"x": 10, "y": 12, "width": 30, "height": 24}
    assert draft.plot_style["point_color"] == "#0F4D92"


def test_style_preset_registry_rejects_malformed_source_metadata_values(tmp_path) -> None:
    cases = [
        ("blank-seed", lambda metadata: metadata.update({"seed_preset_name": ""}), "source_metadata.seed_preset_name"),
        ("bad-kind", lambda metadata: metadata.update({"source_kind": 5}), "source_metadata.source_kind"),
        ("bad-page", lambda metadata: metadata.update({"source_page_index": "first"}), "source_metadata.source_page_index"),
        ("string-page", lambda metadata: metadata.update({"source_page_index": "3"}), "source_metadata.source_page_index"),
        ("unknown-seed", lambda metadata: metadata.update({"seed_preset_name": "NotARealPreset"}), "source_metadata.seed_preset_name"),
        ("negative-page", lambda metadata: metadata.update({"source_page_index": -5}), "source_metadata.source_page_index"),
        ("zero-page-count", lambda metadata: metadata.update({"source_page_count": 0}), "source_metadata.source_page_count"),
        ("bad-size", lambda metadata: metadata.update({"source_size_px": "120x80"}), "source_metadata.source_size_px"),
        (
            "negative-size-width",
            lambda metadata: metadata.update({"source_size_px": {"width": -100, "height": 80}}),
            "source_metadata.source_size_px.width",
        ),
        (
            "negative-size-height",
            lambda metadata: metadata.update({"source_size_px": {"width": 120, "height": -80}}),
            "source_metadata.source_size_px.height",
        ),
        (
            "negative-crop-x",
            lambda metadata: metadata.update({"source_crop_rect": {"x": -1, "y": 0, "width": 30, "height": 24}}),
            "source_metadata.source_crop_rect.x",
        ),
        (
            "negative-crop-y",
            lambda metadata: metadata.update({"source_crop_rect": {"x": 0, "y": -1, "width": 30, "height": 24}}),
            "source_metadata.source_crop_rect.y",
        ),
        (
            "negative-crop-width",
            lambda metadata: metadata.update({"source_crop_rect": {"x": 0, "y": 0, "width": -50, "height": 24}}),
            "source_metadata.source_crop_rect.width",
        ),
        (
            "negative-crop-height",
            lambda metadata: metadata.update({"source_crop_rect": {"x": 0, "y": 0, "width": 30, "height": -20}}),
            "source_metadata.source_crop_rect.height",
        ),
        (
            "missing-crop-width",
            lambda metadata: metadata.update({"source_crop_rect": {"x": 0, "y": 0, "height": 24}}),
            "source_metadata.source_crop_rect.width",
        ),
        (
            "bad-crop-width",
            lambda metadata: metadata.update({"source_crop_rect": {"x": 0, "y": 0, "width": "wide", "height": 24}}),
            "source_metadata.source_crop_rect.width",
        ),
        (
            "float-crop-width",
            lambda metadata: metadata.update({"source_crop_rect": {"x": 0, "y": 0, "width": 1.5, "height": 24}}),
            "source_metadata.source_crop_rect.width",
        ),
    ]
    for slug, mutate_metadata, error_match in cases:
        record = StylePresetRecord.from_style_profile_draft(
            _draft(display_name=f"Broken {slug}"),
            source_type="extracted_draft",
        ).to_dict()
        mutate_metadata(record["source_metadata"])
        path = tmp_path / f"{slug}.json"
        path.write_text(
            json.dumps({"schema_version": 1, "presets": [record]}, indent=2),
            encoding="utf-8",
        )
        original_text = path.read_text(encoding="utf-8")

        registry = StylePresetRegistry(path)

        with pytest.raises(StylePresetRegistryError, match=error_match):
            registry.list_presets()
        assert path.read_text(encoding="utf-8") == original_text


@pytest.mark.parametrize(
    ("payload", "error_match"),
    (
        (
            {
                "schema_version": 1,
                "presets": [
                    StylePresetRecord.from_style_profile_draft(_draft(display_name="Good"), source_type="extracted_draft").to_dict(),
                    "garbage entry",
                ],
            },
            "non-dict preset entry",
        ),
        (
            {
                "schema_version": 1,
                "presets": [
                    {
                        **StylePresetRecord.from_style_profile_draft(
                            _draft(display_name="Blank id"),
                            source_type="extracted_draft",
                        ).to_dict(),
                        "preset_id": "",
                    }
                ],
            },
            "field 'preset_id' must be a non-empty string",
        ),
        (
            {
                "schema_version": 1,
                "presets": [
                    {
                        "preset_id": "missing-required-fields",
                        "display_name": "Broken entry",
                    }
                ],
            },
            "missing required field",
        ),
        (
            {
                "schema_version": 1,
                "presets": [
                    StylePresetRecord(
                        preset_id="duplicate-id",
                        display_name="First",
                        source_type="user_modified",
                        plot_style={"font_family": "Arial"},
                        extraction_profile={},
                        source_metadata={
                            "seed_preset_name": "Nature",
                            "source_kind": "raster",
                            "source_path": "one.png",
                            "source_page_index": 0,
                            "source_page_count": 1,
                            "source_size_px": {"width": 1, "height": 1},
                            "source_crop_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                        },
                        warnings=list(STYLE_PROFILE_DRAFT_WARNINGS),
                        created_at="2026-06-12T09:00:00+00:00",
                        updated_at="2026-06-12T09:00:00+00:00",
                    ).to_dict(),
                    StylePresetRecord(
                        preset_id="duplicate-id",
                        display_name="Second",
                        source_type="user_modified",
                        plot_style={"font_family": "Arial"},
                        extraction_profile={},
                        source_metadata={
                            "seed_preset_name": "Nature",
                            "source_kind": "raster",
                            "source_path": "two.png",
                            "source_page_index": 0,
                            "source_page_count": 1,
                            "source_size_px": {"width": 1, "height": 1},
                            "source_crop_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                        },
                        warnings=list(STYLE_PROFILE_DRAFT_WARNINGS),
                        created_at="2026-06-12T09:01:00+00:00",
                        updated_at="2026-06-12T09:01:00+00:00",
                    ).to_dict(),
                ],
            },
            "duplicate preset_id",
        ),
        (
            [
                StylePresetRecord.from_style_profile_draft(_draft(display_name="Wrong root"), source_type="extracted_draft").to_dict(),
            ],
            "root object must be a JSON object",
        ),
    ),
)
def test_style_preset_registry_rejects_corrupt_payload_shapes_and_entries(
    tmp_path,
    payload: object,
    error_match: str,
) -> None:
    path = tmp_path / "style_presets.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    registry = StylePresetRegistry(path)

    with pytest.raises(StylePresetRegistryError, match=error_match):
        registry.list_presets()


def test_style_preset_registry_refuses_save_while_corrupt_payload_remains(tmp_path) -> None:
    path = tmp_path / "style_presets.json"
    corrupt_payload = {
        "schema_version": 1,
        "presets": [
            StylePresetRecord.from_style_profile_draft(_draft(display_name="Good"), source_type="extracted_draft").to_dict(),
            {"preset_id": "broken-entry", "display_name": "Broken only"},
        ],
    }
    original_text = json.dumps(corrupt_payload, indent=2)
    path.write_text(original_text, encoding="utf-8")

    registry = StylePresetRegistry(path)

    with pytest.raises(StylePresetRegistryError, match="missing required field"):
        registry.save_draft(_draft(display_name="Should not save"), source_type="extracted_draft")

    assert path.read_text(encoding="utf-8") == original_text


def test_style_preset_registry_refuses_save_while_empty_preset_id_payload_remains(tmp_path) -> None:
    path = tmp_path / "style_presets.json"
    corrupt_payload = {
        "schema_version": 1,
        "presets": [
            {
                **StylePresetRecord.from_style_profile_draft(
                    _draft(display_name="Broken id"),
                    source_type="extracted_draft",
                ).to_dict(),
                "preset_id": "",
            }
        ],
    }
    original_text = json.dumps(corrupt_payload, indent=2)
    path.write_text(original_text, encoding="utf-8")

    registry = StylePresetRegistry(path)

    with pytest.raises(StylePresetRegistryError, match="field 'preset_id' must be a non-empty string"):
        registry.save_draft(_draft(display_name="Should not save"), source_type="extracted_draft")

    assert path.read_text(encoding="utf-8") == original_text


def test_style_preset_registry_refuses_update_while_corrupt_payload_remains(tmp_path) -> None:
    path = tmp_path / "style_presets.json"
    valid = StylePresetRecord.from_style_profile_draft(_draft(display_name="Original"), source_type="extracted_draft").to_dict()
    corrupt_payload = {
        "schema_version": 1,
        "presets": [
            valid,
            17,
        ],
    }
    original_text = json.dumps(corrupt_payload, indent=2)
    path.write_text(original_text, encoding="utf-8")
    registry = StylePresetRegistry(path)

    candidate = StylePresetRecord.from_dict(valid)
    candidate.display_name = "Attempted update"

    with pytest.raises(StylePresetRegistryError, match="non-dict preset entry"):
        registry.save_preset(candidate)

    assert path.read_text(encoding="utf-8") == original_text


def test_style_preset_registry_wraps_read_filesystem_failure_without_raw_path(tmp_path, monkeypatch) -> None:
    path = tmp_path / "style_presets.json"
    registry = StylePresetRegistry(path)
    original_exists = Path.exists

    def _raise_exists(self):
        if self == path:
            raise PermissionError(f"denied: {path}")
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", _raise_exists)

    with pytest.raises(StylePresetRegistryError) as exc_info:
        registry.list_presets()

    message = str(exc_info.value)
    assert "Could not inspect style preset registry storage." in message
    assert str(path) not in message


def test_style_preset_registry_wraps_write_directory_failure_without_raw_path(tmp_path, monkeypatch) -> None:
    path = tmp_path / "blocked" / "style_presets.json"
    registry = StylePresetRegistry(path)
    original_mkdir = Path.mkdir

    def _raise_mkdir(self, *args, **kwargs):
        if self == path.parent:
            raise PermissionError(f"denied: {path.parent}")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _raise_mkdir)

    with pytest.raises(StylePresetRegistryError) as exc_info:
        registry.save_draft(_draft(display_name="Blocked write"), source_type="extracted_draft")

    message = str(exc_info.value)
    assert "Could not prepare style preset storage." in message
    assert str(path.parent) not in message
    assert not path.exists()


def test_style_preset_registry_replace_failure_keeps_existing_registry_and_cleans_temp(tmp_path, monkeypatch) -> None:
    path = tmp_path / "style_presets.json"
    registry = StylePresetRegistry(path)
    saved = registry.save_draft(_draft(display_name="Original"), source_type="extracted_draft")
    original_text = path.read_text(encoding="utf-8")
    original_replace = Path.replace

    def _raise_replace(self, target):
        if target == path:
            raise OSError(f"replace denied: {path}")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", _raise_replace)
    candidate = registry.get_preset(saved.preset_id)
    assert candidate is not None
    candidate.display_name = "Should not persist"

    with pytest.raises(StylePresetRegistryError) as exc_info:
        registry.save_preset(candidate)

    message = str(exc_info.value)
    assert "Could not update style preset registry." in message
    assert str(path) not in message
    assert path.read_text(encoding="utf-8") == original_text
    assert list(tmp_path.glob("*.tmp")) == []


def test_style_preset_registry_write_failure_keeps_existing_registry_and_cleans_temp(tmp_path, monkeypatch) -> None:
    path = tmp_path / "style_presets.json"
    registry = StylePresetRegistry(path)
    saved = registry.save_draft(_draft(display_name="Original"), source_type="extracted_draft")
    original_text = path.read_text(encoding="utf-8")

    def _raise_dump(*args, **kwargs):
        raise OSError(f"write denied: {path}")

    monkeypatch.setattr("core.style_preset_registry.json.dump", _raise_dump)
    candidate = registry.get_preset(saved.preset_id)
    assert candidate is not None
    candidate.display_name = "Should not persist"

    with pytest.raises(StylePresetRegistryError) as exc_info:
        registry.save_preset(candidate)

    message = str(exc_info.value)
    assert "Could not write style preset registry." in message
    assert str(path) not in message
    assert path.read_text(encoding="utf-8") == original_text
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("module_name", ["core.style_preset_resolver", "core.style_preset_registry"])
def test_style_preset_runtime_modules_import_cleanly_in_fresh_process(module_name: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module_name}; print('ok')"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "ok"


def test_style_preset_resolver_uses_builtin_only_when_no_runtime_saved_preset_is_active() -> None:
    clear_runtime_active_style_preset()

    style = resolve_plot_style("Cell")

    assert style["point_color"] == plot_style_from_preset(get_journal_preset("Cell"))["point_color"]
    assert style["bar_edge_color"] == plot_style_from_preset(get_journal_preset("Cell"))["bar_edge_color"]
    assert last_runtime_style_preset_status().source == "builtin"


def test_style_preset_resolver_applies_saved_runtime_preset_over_builtin_fallback(tmp_path) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    draft = _draft(display_name="Runtime style")
    draft.plot_style["point_color"] = "#123456"
    draft.plot_style["bar_edge_color"] = "#654321"
    saved = registry.save_draft(draft, source_type="extracted_draft")

    try:
        status = set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        style = resolve_plot_style("Cell")

        assert status.ok is True
        assert style["point_color"] == "#123456"
        assert style["bar_edge_color"] == "#654321"
        assert last_runtime_style_preset_status().source == "runtime_registry"
    finally:
        clear_runtime_active_style_preset()


def test_style_preset_resolver_precedence_is_local_saved_runtime_builtin_then_nature(tmp_path) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    draft = _draft(display_name="Precedence style")
    draft.plot_style["point_color"] = "#111111"
    draft.plot_style["bar_edge_color"] = "#222222"
    draft.plot_style["axis_line_width"] = 1.2
    saved = registry.save_draft(draft, source_type="extracted_draft")

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        style = resolve_plot_style(
            "Cell",
            saved_style={"point_color": "#333333", "axis_line_width": 2.0},
            local_override={"axis_line_width": 3.5},
        )

        assert style["bar_edge_color"] == "#222222"
        assert style["point_color"] == "#333333"
        assert style["axis_line_width"] == 3.5

        fallback = resolve_plot_style("Not a builtin preset")
        assert fallback["bar_edge_color"] == "#222222"
        assert fallback["font_family"] == plot_style_from_preset(get_journal_preset("Nature"))["font_family"]
    finally:
        clear_runtime_active_style_preset()


def test_style_preset_resolver_missing_or_corrupt_registry_fails_closed_without_rewrite(tmp_path) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_draft(_draft(display_name="Will disappear"), source_type="extracted_draft")
    original_builtin_style = plot_style_from_preset(get_journal_preset("Cell"))

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        registry.storage_path.unlink()
        missing_style = resolve_plot_style("Cell")
        missing_status = last_runtime_style_preset_status()

        assert missing_style["point_color"] == original_builtin_style["point_color"]
        assert missing_status.ok is False
        assert "not found" in missing_status.message.casefold()

        registry.storage_path.write_text("{not valid json", encoding="utf-8")
        corrupt_text = registry.storage_path.read_text(encoding="utf-8")
        corrupt_style = resolve_plot_style("Cell")
        corrupt_status = last_runtime_style_preset_status()

        assert corrupt_style["point_color"] == original_builtin_style["point_color"]
        assert corrupt_status.ok is False
        assert "invalid style preset registry" in corrupt_status.message.casefold()
        assert registry.storage_path.read_text(encoding="utf-8") == corrupt_text
    finally:
        clear_runtime_active_style_preset()


def test_style_preset_resolver_invalid_runtime_style_tokens_fail_closed_without_rewrite(tmp_path) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    record = StylePresetRecord.from_builtin_preset_copy("Nature", display_name="Invalid runtime tokens")
    record.plot_style["axis_line_width"] = "not-a-number"
    record.plot_style["point_color"] = "#123456"
    saved = registry.save_preset(record)
    original_text = registry.storage_path.read_text(encoding="utf-8")

    try:
        status = set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        style = resolve_plot_style("Cell")

        assert status.ok is False
        assert "invalid style token" in status.message.casefold()
        assert style["point_color"] == plot_style_from_preset(get_journal_preset("Cell"))["point_color"]
        assert style["axis_line_width"] == plot_style_from_preset(get_journal_preset("Cell"))["axis_line_width"]
        assert registry.storage_path.read_text(encoding="utf-8") == original_text
    finally:
        clear_runtime_active_style_preset()


@pytest.mark.parametrize(
    ("plot_style", "error_match"),
    (
        ({"point_color": "red"}, "point_color must be a #RRGGBB color"),
        ({"background_color": "#12345G"}, "background_color must be a #RRGGBB color"),
        ({"panel_background_color": "123456"}, "panel_background_color must be a #RRGGBB color"),
        ({"gridline_color": "#1234567"}, "gridline_color must be a #RRGGBB color"),
        ({"series_colors": ["#123456", "123456"]}, "series_colors[1] must be a #RRGGBB color"),
    ),
)
def test_style_preset_resolver_invalid_runtime_colors_fail_closed_without_rewrite(
    tmp_path,
    plot_style: dict[str, object],
    error_match: str,
) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    record = StylePresetRecord.from_builtin_preset_copy("Nature", display_name="Invalid runtime color")
    record.plot_style.update(plot_style)
    saved = registry.save_preset(record)
    original_text = registry.storage_path.read_text(encoding="utf-8")
    builtin_style = plot_style_from_preset(get_journal_preset("Cell"))

    try:
        status = set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        style = resolve_plot_style("Cell")

        assert status.ok is False
        assert error_match in status.message
        assert style["point_color"] == builtin_style["point_color"]
        assert style["series_colors"] == builtin_style["series_colors"]
        assert last_runtime_style_preset_status().source == "builtin"
        assert error_match in last_runtime_style_preset_status().message
        assert registry.storage_path.read_text(encoding="utf-8") == original_text
    finally:
        clear_runtime_active_style_preset()


@pytest.mark.parametrize(
    ("plot_style", "error_match"),
    (
        ({"scale_bar_color": "white"}, "scale_bar_color must be a #RRGGBB color"),
        ({"label_color": "#12345"}, "label_color must be a #RRGGBB color"),
        ({"frame_color": "中文颜色"}, "frame_color must be a #RRGGBB color"),
        ({"channel_colors": ["#00FF00", "bad"]}, "channel_colors[1] must be a #RRGGBB color"),
    ),
)
def test_style_preset_resolver_invalid_image_plate_colors_fail_closed_without_rewrite(
    tmp_path,
    plot_style: dict[str, object],
    error_match: str,
) -> None:
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    record = StylePresetRecord.from_builtin_preset_copy("Nature", display_name="Invalid image plate color")
    record.plot_style.update({"point_color": "#123456", "bar_edge_color": "#654321", **plot_style})
    saved = registry.save_preset(record)
    original_text = registry.storage_path.read_text(encoding="utf-8")
    builtin_plate = resolve_image_plate_style("Nature")

    try:
        status = set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        plate_style = resolve_image_plate_style("Nature")

        assert status.ok is False
        assert error_match in status.message
        assert plate_style == builtin_plate
        assert last_runtime_style_preset_status().source == "builtin"
        assert error_match in last_runtime_style_preset_status().message
        assert registry.storage_path.read_text(encoding="utf-8") == original_text
    finally:
        clear_runtime_active_style_preset()
