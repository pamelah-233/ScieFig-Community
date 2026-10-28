from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TaskProfile:
    task_id: str
    display_name: str
    image_type: str
    default_channel_or_stain: str
    default_measurements: list[str] = field(default_factory=list)
    auto_parameter_strategy: str = ""
    available_simple_controls: list[str] = field(default_factory=list)
    advanced_controls: list[str] = field(default_factory=list)
    qc_rules: list[str] = field(default_factory=list)
    default_settings: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "display_name": self.display_name,
            "image_type": self.image_type,
            "default_channel_or_stain": self.default_channel_or_stain,
            "default_measurements": list(self.default_measurements),
            "auto_parameter_strategy": self.auto_parameter_strategy,
            "available_simple_controls": list(self.available_simple_controls),
            "advanced_controls": list(self.advanced_controls),
            "qc_rules": list(self.qc_rules),
            "default_settings": dict(self.default_settings),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "TaskProfile":
        data = dict(payload or {})
        return cls(
            task_id=str(data.get("task_id", "")).strip(),
            display_name=str(data.get("display_name", "")).strip(),
            image_type=str(data.get("image_type", "")).strip(),
            default_channel_or_stain=str(data.get("default_channel_or_stain", "")).strip(),
            default_measurements=[str(item) for item in data.get("default_measurements", [])],
            auto_parameter_strategy=str(data.get("auto_parameter_strategy", "")).strip(),
            available_simple_controls=[str(item) for item in data.get("available_simple_controls", [])],
            advanced_controls=[str(item) for item in data.get("advanced_controls", [])],
            qc_rules=[str(item) for item in data.get("qc_rules", [])],
            default_settings=dict(data.get("default_settings", {})),
            description=str(data.get("description", "")).strip(),
        )


@dataclass(slots=True)
class AutoParameterResult:
    task_id: str
    recommended_thresholds: dict[str, Any] = field(default_factory=dict)
    recommended_tissue_mask_settings: dict[str, Any] = field(default_factory=dict)
    recommended_min_area: int | None = None
    recommended_max_area: int | None = None
    recommended_watershed: bool = False
    confidence_label: str = "medium"
    confidence_score: float | None = None
    qc_warnings: list[str] = field(default_factory=list)
    explanation: str = ""
    threshold_method: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "recommended_thresholds": dict(self.recommended_thresholds),
            "recommended_tissue_mask_settings": dict(self.recommended_tissue_mask_settings),
            "recommended_min_area": self.recommended_min_area,
            "recommended_max_area": self.recommended_max_area,
            "recommended_watershed": bool(self.recommended_watershed),
            "confidence_label": self.confidence_label,
            "confidence_score": self.confidence_score,
            "qc_warnings": list(self.qc_warnings),
            "explanation": self.explanation,
            "threshold_method": self.threshold_method,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AutoParameterResult":
        data = dict(payload or {})
        score = data.get("confidence_score")
        return cls(
            task_id=str(data.get("task_id", "")).strip(),
            recommended_thresholds=dict(data.get("recommended_thresholds", {})),
            recommended_tissue_mask_settings=dict(data.get("recommended_tissue_mask_settings", {})),
            recommended_min_area=_coerce_optional_int(data.get("recommended_min_area")),
            recommended_max_area=_coerce_optional_int(data.get("recommended_max_area")),
            recommended_watershed=bool(data.get("recommended_watershed", False)),
            confidence_label=str(data.get("confidence_label", "medium")).strip() or "medium",
            confidence_score=float(score) if score is not None else None,
            qc_warnings=[str(item) for item in data.get("qc_warnings", [])],
            explanation=str(data.get("explanation", "")).strip(),
            threshold_method=str(data.get("threshold_method", "")).strip(),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(slots=True)
class BatchThresholdState:
    task_id: str
    batch_threshold: float | None = None
    batch_threshold_source_image: str | None = None
    batch_threshold_method: str | None = None
    locked: bool = True
    per_image_overrides: dict[str, float] = field(default_factory=dict)
    example_guided_overrides: dict[str, float] = field(default_factory=dict)
    override_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "batch_threshold": self.batch_threshold,
            "batch_threshold_source_image": self.batch_threshold_source_image,
            "batch_threshold_method": self.batch_threshold_method,
            "locked": bool(self.locked),
            "per_image_overrides": {str(key): float(value) for key, value in self.per_image_overrides.items()},
            "example_guided_overrides": {str(key): float(value) for key, value in self.example_guided_overrides.items()},
            "override_warnings": list(self.override_warnings),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "BatchThresholdState":
        data = dict(payload or {})
        threshold = data.get("batch_threshold")
        return cls(
            task_id=str(data.get("task_id", "")).strip(),
            batch_threshold=float(threshold) if threshold is not None else None,
            batch_threshold_source_image=_coerce_optional_str(data.get("batch_threshold_source_image")),
            batch_threshold_method=_coerce_optional_str(data.get("batch_threshold_method")),
            locked=bool(data.get("locked", True)),
            per_image_overrides={
                str(key): float(value) for key, value in dict(data.get("per_image_overrides", {})).items()
            },
            example_guided_overrides={
                str(key): float(value) for key, value in dict(data.get("example_guided_overrides", {})).items()
            },
            override_warnings=[str(item) for item in data.get("override_warnings", [])],
        )


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


DAB_IHC_PROFILE = TaskProfile(
    task_id="ihc_dab_positive_area",
    display_name="DAB IHC / Positive Area",
    image_type="ihc",
    default_channel_or_stain="dab",
    default_measurements=["positive_area_percent", "mean_dab_intensity", "h_score"],
    auto_parameter_strategy="dab_robust_local_stats",
    available_simple_controls=[
        "sensitivity",
        "cleanup_level",
        "overlay_opacity",
        "tissue_background_correction",
    ],
    advanced_controls=[
        "positive_threshold",
        "weak_threshold",
        "moderate_threshold",
        "strong_threshold",
        "tissue_mask_thresholds",
        "min_tissue_area_px",
    ],
    qc_rules=[
        "weak_dab_signal",
        "tissue_area_too_small",
        "threshold_unstable",
    ],
    default_settings={
        "sensitivity": "medium",
        "cleanup_level": "medium",
        "tissue_background_correction": True,
        "overlay_opacity": 0.35,
    },
    description="Guided DAB-positive area quantification with automatic threshold and tissue-background exclusion.",
)


BRIGHTFIELD_HE_PROFILE = TaskProfile(
    task_id="brightfield_he_eosin_area",
    display_name="Brightfield H&E / Eosin Area",
    image_type="brightfield",
    default_channel_or_stain="h&e",
    default_measurements=["area_fraction_percent", "object_count", "mean_object_area_px"],
    auto_parameter_strategy="he_lab_eosin_local_stats",
    available_simple_controls=[
        "sensitivity",
        "cleanup_level",
        "overlay_opacity",
    ],
    advanced_controls=[
        "segmentation_mode",
        "lab_thresholds",
        "cleanup_min_area_px",
        "object_area_limits",
    ],
    qc_rules=[
        "background_dominant",
        "weak_eosin_signal",
    ],
    default_settings={
        "sensitivity": "medium",
        "cleanup_level": "low",
        "overlay_opacity": 0.35,
    },
    description="Guided eosin/pink H&E area detection using automatically recommended brightfield settings.",
)


def _experimental_profile_metadata() -> dict[str, Any]:
    return {
        "profile_version": "0.1-experimental",
        "threshold_source": "profile_preset",
        "validation_status": "synthetic_test_only",
        "overlay_review_required": True,
    }


BRIGHTFIELD_SPECIAL_STAIN_PROFILE = TaskProfile(
    task_id="brightfield_special_stain_area",
    display_name="Brightfield Special Stain / Area",
    image_type="brightfield",
    default_channel_or_stain="special_stain",
    default_measurements=["area_fraction_percent", "object_count", "mean_object_area_px"],
    auto_parameter_strategy="profile_preset_experimental",
    available_simple_controls=[
        "overlay_opacity",
    ],
    advanced_controls=[
        "segmentation_mode",
        "gray_threshold",
        "gray_invert",
        "cleanup_min_area_px",
        "object_area_limits",
    ],
    qc_rules=[
        "preset_overlay_review_required",
        "background_dominant",
    ],
    default_settings={
        "segmentation_mode": "grayscale",
        "gray_threshold": 0.72,
        "gray_invert": True,
        "fill_holes": True,
        "cleanup_min_area_px": 0,
        "min_object_area_px": 10,
        "max_object_area_px": 0,
        "overlay_opacity": 0.35,
        "profile_metadata": _experimental_profile_metadata(),
    },
    description="Experimental preset for high-contrast brightfield special stains. Review the overlay before using results.",
)


BRIGHTFIELD_CRYSTAL_VIOLET_PROFILE = TaskProfile(
    task_id="brightfield_crystal_violet_area",
    display_name="Brightfield Crystal Violet / Area",
    image_type="brightfield",
    default_channel_or_stain="crystal_violet",
    default_measurements=["area_fraction_percent", "object_count", "mean_object_area_px"],
    auto_parameter_strategy="profile_preset_experimental",
    available_simple_controls=[
        "overlay_opacity",
    ],
    advanced_controls=[
        "segmentation_mode",
        "hsv_thresholds",
        "cleanup_min_area_px",
        "object_area_limits",
    ],
    qc_rules=[
        "preset_overlay_review_required",
        "weak_stain_signal",
    ],
    default_settings={
        "segmentation_mode": "hsv",
        "h_min": 0.72,
        "h_max": 0.92,
        "s_min": 0.18,
        "s_max": 1.00,
        "v_min": 0.00,
        "v_max": 0.90,
        "fill_holes": True,
        "cleanup_min_area_px": 0,
        "min_object_area_px": 10,
        "max_object_area_px": 0,
        "overlay_opacity": 0.35,
        "profile_metadata": _experimental_profile_metadata(),
    },
    description="Experimental preset for crystal violet brightfield area measurements. Review the overlay before using results.",
)


BRIGHTFIELD_OIL_RED_O_PROFILE = TaskProfile(
    task_id="brightfield_oil_red_o_area",
    display_name="Brightfield Oil Red O / Area",
    image_type="brightfield",
    default_channel_or_stain="oil_red_o",
    default_measurements=["area_fraction_percent", "object_count", "mean_object_area_px"],
    auto_parameter_strategy="profile_preset_experimental",
    available_simple_controls=[
        "overlay_opacity",
    ],
    advanced_controls=[
        "segmentation_mode",
        "hsv_thresholds",
        "cleanup_min_area_px",
        "object_area_limits",
    ],
    qc_rules=[
        "preset_overlay_review_required",
        "weak_stain_signal",
    ],
    default_settings={
        "segmentation_mode": "hsv",
        "h_min": 0.00,
        "h_max": 0.12,
        "s_min": 0.20,
        "s_max": 1.00,
        "v_min": 0.00,
        "v_max": 0.95,
        "fill_holes": True,
        "cleanup_min_area_px": 0,
        "min_object_area_px": 10,
        "max_object_area_px": 0,
        "overlay_opacity": 0.35,
        "profile_metadata": _experimental_profile_metadata(),
    },
    description="Experimental preset for Oil Red O brightfield area measurements. Review the overlay before using results.",
)


TASK_PROFILES = {
    DAB_IHC_PROFILE.task_id: DAB_IHC_PROFILE,
    BRIGHTFIELD_HE_PROFILE.task_id: BRIGHTFIELD_HE_PROFILE,
    BRIGHTFIELD_SPECIAL_STAIN_PROFILE.task_id: BRIGHTFIELD_SPECIAL_STAIN_PROFILE,
    BRIGHTFIELD_CRYSTAL_VIOLET_PROFILE.task_id: BRIGHTFIELD_CRYSTAL_VIOLET_PROFILE,
    BRIGHTFIELD_OIL_RED_O_PROFILE.task_id: BRIGHTFIELD_OIL_RED_O_PROFILE,
}


def get_task_profile(task_id: str) -> TaskProfile:
    profile = TASK_PROFILES[str(task_id).strip()]
    return TaskProfile.from_dict(profile.to_dict())
