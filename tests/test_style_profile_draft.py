from __future__ import annotations

from core.models.style_profile_draft import STYLE_PROFILE_DRAFT_WARNINGS, StyleProfileDraft


def _assert_json_like(value: object) -> None:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return
    if isinstance(value, dict):
        for item in value.values():
            _assert_json_like(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_json_like(item)
        return
    raise AssertionError(f"Unexpected non-JSON-like value: {value!r}")


def test_style_profile_draft_round_trips_without_qt_or_path_objects() -> None:
    draft = StyleProfileDraft.seeded_from_builtin_preset(
        seed_preset_name="Nature",
        source_kind="pdf",
        source_path="D:/refs/figure.pdf",
        source_page_index=1,
        source_page_count=3,
        source_size_px={"width": 1200, "height": 800},
        source_crop_rect={"x": 10, "y": 12, "width": 320, "height": 240},
        extraction_profile={
            "crop_size_px": {"width": 320, "height": 240},
            "aspect_ratio": 320 / 240,
            "is_grayscale": False,
            "has_transparency": False,
            "dominant_colors": ["#FFFFFF", "#0F4D92", "#B64342"],
            "series_candidates": ["#0F4D92", "#B64342"],
            "background_color": "#FFFFFF",
            "panel_background_color": "#FFFFFF",
            "text_color": "#202020",
            "axis_color": "#202020",
            "warnings": ["approximate PDF crop limits"],
        },
    )

    payload = draft.to_dict()
    restored = StyleProfileDraft.from_dict(payload)

    assert restored == draft
    _assert_json_like(payload)


def test_style_profile_draft_preserves_explicit_warnings() -> None:
    draft = StyleProfileDraft.seeded_from_builtin_preset(
        seed_preset_name="Nature",
        source_kind="raster",
        source_path="reference.png",
        source_page_index=0,
        source_page_count=1,
        source_size_px={"width": 100, "height": 80},
        source_crop_rect={"x": 0, "y": 0, "width": 100, "height": 80},
        extraction_profile={},
        warnings=[*STYLE_PROFILE_DRAFT_WARNINGS, "custom warning"],
    )

    restored = StyleProfileDraft.from_dict(draft.to_dict())

    assert restored.warnings[:-1] == list(STYLE_PROFILE_DRAFT_WARNINGS)
    assert restored.warnings[-1] == "custom warning"


def test_style_profile_draft_seeds_normalized_plot_style_from_builtin_preset() -> None:
    draft = StyleProfileDraft.seeded_from_builtin_preset(
        seed_preset_name="Nature",
        source_kind="raster",
        source_path="reference.png",
        source_page_index=0,
        source_page_count=1,
        source_size_px={"width": 100, "height": 80},
        source_crop_rect={"x": 0, "y": 0, "width": 100, "height": 80},
        extraction_profile={},
    )

    assert draft.plot_style["font_family"] == "Arial"
    assert draft.plot_style["font_size_pt"] == 7.0
    assert draft.plot_style["axis_label_size_pt"] == 8.0
    assert draft.plot_style["tick_label_size_pt"] == 7.0
    assert draft.plot_style["bar_fill_color"] == "#FFFFFF"
    assert draft.plot_style["bar_edge_color"] == "#272727"
    assert draft.plot_style["point_color"] == "#0F4D92"
    assert draft.plot_style["point_edge_color"] == "#272727"
    assert draft.plot_style["axis_line_width"] == 0.6
    assert draft.plot_style["tick_width"] == 0.6
    assert draft.plot_style["tick_length"] == 3.0
    assert draft.plot_style["sem_cap_size"] == 3.0
    assert draft.plot_style["bracket_line_width"] == 0.7
    assert draft.plot_style["show_top_spine"] is False
    assert draft.plot_style["show_right_spine"] is False


def test_style_profile_draft_to_dict_and_from_dict_detach_nested_payloads() -> None:
    draft = StyleProfileDraft.seeded_from_builtin_preset(
        seed_preset_name="Nature",
        source_kind="raster",
        source_path="reference.png",
        source_page_index=0,
        source_page_count=1,
        source_size_px={"width": 100, "height": 80},
        source_crop_rect={"x": 1, "y": 2, "width": 30, "height": 24},
        extraction_profile={
            "crop_size_px": {"width": 30, "height": 24},
            "series_candidates": ["#0F4D92", "#B64342"],
            "typography": {"supported": False, "notes": ["approximate"]},
        },
        warnings=[*STYLE_PROFILE_DRAFT_WARNINGS, "custom warning"],
    )

    payload = draft.to_dict()
    payload["source_size_px"]["width"] = 999
    payload["source_crop_rect"]["width"] = 999
    payload["plot_style"]["font_family"] = "Courier New"
    payload["extraction_profile"]["crop_size_px"]["width"] = 999
    payload["extraction_profile"]["series_candidates"].append("#FFFFFF")
    payload["extraction_profile"]["typography"]["notes"].append("mutated")
    payload["warnings"].append("mutated warning")

    assert draft.source_size_px["width"] == 100
    assert draft.source_crop_rect["width"] == 30
    assert draft.plot_style["font_family"] == "Arial"
    assert draft.extraction_profile["crop_size_px"]["width"] == 30
    assert draft.extraction_profile["series_candidates"] == ["#0F4D92", "#B64342"]
    assert draft.extraction_profile["typography"]["notes"] == ["approximate"]
    assert draft.warnings[-1] == "custom warning"

    restored = StyleProfileDraft.from_dict(draft.to_dict())
    restored.source_size_px["width"] = 777
    restored.source_crop_rect["width"] = 777
    restored.plot_style["font_family"] = "Times New Roman"
    restored.extraction_profile["crop_size_px"]["width"] = 777
    restored.extraction_profile["series_candidates"].append("#111111")
    restored.extraction_profile["typography"]["notes"].append("changed")
    restored.warnings.append("restored warning")

    assert draft.source_size_px["width"] == 100
    assert draft.source_crop_rect["width"] == 30
    assert draft.plot_style["font_family"] == "Arial"
    assert draft.extraction_profile["crop_size_px"]["width"] == 30
    assert draft.extraction_profile["series_candidates"] == ["#0F4D92", "#B64342"]
    assert draft.extraction_profile["typography"]["notes"] == ["approximate"]
    assert draft.warnings[-1] == "custom warning"
