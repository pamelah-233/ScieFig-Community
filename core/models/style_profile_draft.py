from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from core.plotting.style_presets import get_journal_preset, plot_style_from_preset


STYLE_PROFILE_DRAFT_WARNINGS = (
    "Visual-only draft profile. It does not carry calculation, measurement, or statistics semantics.",
    "No p-value meaning or statistical inference is extracted from the reference.",
    "PDF/image crop matching is approximate and limited to the selected rasterized region.",
)


def _normalize_px_rect(payload: dict[str, Any] | None, *, keys: tuple[str, ...]) -> dict[str, int]:
    payload = dict(payload or {})
    return {key: int(payload.get(key, 0) or 0) for key in keys}


def _deep_copy_payload(value: Any) -> Any:
    return deepcopy(value)


@dataclass(slots=True)
class StyleProfileDraft:
    draft_id: str
    display_name: str
    seed_preset_name: str
    source_kind: str
    source_path: str
    source_page_index: int
    source_page_count: int
    source_size_px: dict[str, int]
    source_crop_rect: dict[str, int]
    plot_style: dict[str, Any]
    extraction_profile: dict[str, Any]
    warnings: list[str] = field(default_factory=lambda: list(STYLE_PROFILE_DRAFT_WARNINGS))

    @classmethod
    def seeded_from_builtin_preset(
        cls,
        *,
        seed_preset_name: str,
        source_kind: str,
        source_path: str,
        source_page_index: int,
        source_page_count: int,
        source_size_px: dict[str, Any],
        source_crop_rect: dict[str, Any],
        extraction_profile: dict[str, Any],
        display_name: str | None = None,
        draft_id: str | None = None,
        warnings: list[str] | tuple[str, ...] | None = None,
    ) -> "StyleProfileDraft":
        plot_style = plot_style_from_preset(get_journal_preset(seed_preset_name))
        return cls(
            draft_id=draft_id or uuid4().hex,
            display_name=display_name or f"{seed_preset_name} visual draft",
            seed_preset_name=str(seed_preset_name),
            source_kind=str(source_kind),
            source_path=str(source_path),
            source_page_index=int(source_page_index),
            source_page_count=max(1, int(source_page_count)),
            source_size_px=_normalize_px_rect(source_size_px, keys=("width", "height")),
            source_crop_rect=_normalize_px_rect(source_crop_rect, keys=("x", "y", "width", "height")),
            plot_style=_deep_copy_payload(plot_style),
            extraction_profile=_deep_copy_payload(extraction_profile),
            warnings=_deep_copy_payload(list(warnings or STYLE_PROFILE_DRAFT_WARNINGS)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "display_name": self.display_name,
            "seed_preset_name": self.seed_preset_name,
            "source_kind": self.source_kind,
            "source_path": self.source_path,
            "source_page_index": int(self.source_page_index),
            "source_page_count": int(self.source_page_count),
            "source_size_px": _deep_copy_payload(_normalize_px_rect(self.source_size_px, keys=("width", "height"))),
            "source_crop_rect": _deep_copy_payload(_normalize_px_rect(self.source_crop_rect, keys=("x", "y", "width", "height"))),
            "plot_style": _deep_copy_payload(self.plot_style),
            "extraction_profile": _deep_copy_payload(self.extraction_profile),
            "warnings": _deep_copy_payload([str(item) for item in self.warnings]),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StyleProfileDraft":
        return cls(
            draft_id=str(payload.get("draft_id", "")),
            display_name=str(payload.get("display_name", "")),
            seed_preset_name=str(payload.get("seed_preset_name", "")),
            source_kind=str(payload.get("source_kind", "")),
            source_path=str(payload.get("source_path", "")),
            source_page_index=int(payload.get("source_page_index", 0) or 0),
            source_page_count=max(1, int(payload.get("source_page_count", 1) or 1)),
            source_size_px=_deep_copy_payload(
                _normalize_px_rect(dict(payload.get("source_size_px", {})), keys=("width", "height"))
            ),
            source_crop_rect=_deep_copy_payload(
                _normalize_px_rect(dict(payload.get("source_crop_rect", {})), keys=("x", "y", "width", "height"))
            ),
            plot_style=_deep_copy_payload(dict(payload.get("plot_style", {}))),
            extraction_profile=_deep_copy_payload(dict(payload.get("extraction_profile", {}))),
            warnings=_deep_copy_payload([str(item) for item in payload.get("warnings", list(STYLE_PROFILE_DRAFT_WARNINGS))]),
        )
