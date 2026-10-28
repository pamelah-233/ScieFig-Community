from copy import deepcopy

from core.models.style_profile_draft import StyleProfileDraft
from core.style_preset_registry import StylePresetRegistry


def save_extracted_style(draft: dict, registry: StylePresetRegistry):
    """Use the existing permanent library, retaining evidence and uncertainty."""
    source = draft.get("source", {})
    profile = StyleProfileDraft.seeded_from_builtin_preset(
        seed_preset_name="Nature", display_name=str(draft.get("name") or "提取样式"),
        source_kind=str(source.get("kind", "image")), source_path=str(source.get("path", "")),
        source_page_index=int(source.get("page_index", 0)), source_page_count=int(source.get("page_count", 1)),
        source_size_px={}, source_crop_rect={}, extraction_profile={"local_extraction": deepcopy(draft)},
        warnings=list(draft.get("warnings", [])),
    )
    for key, token in draft.get("tokens", {}).items():
        if token.get("status") not in {"detected", "estimated", "user_edited"} or token.get("value") is None:
            continue
        if key:
            profile.plot_style[key] = deepcopy(token["value"])
            if key == "point_size":
                # Extraction and preview use marker diameter (pt); Matplotlib
                # production scatter uses area (pt²). Existing presets keep their unit.
                profile.plot_style[key] = float(token["value"]) ** 2
            if key == "font_family":
                from .preview import _FontResolver
                actual = _FontResolver().actual(str(token["value"]))
                profile.plot_style[key] = actual
                if actual != token["value"]:
                    profile.warnings.append(f"字体替代：{token['value']} → {actual}；原始识别证据已保留。")
            if key == "series_colors" and token["value"]:
                profile.plot_style["use_series_colors"] = True
    unknown = [key for key, token in draft.get("tokens", {}).items()
               if token.get("status") not in {"detected", "estimated", "user_edited"}]
    if unknown:
        profile.warnings.append("未检出项沿用内置预设，不能视为论文恢复值：" + ", ".join(unknown))
    return registry.save_draft(profile, source_type="extracted_draft")

