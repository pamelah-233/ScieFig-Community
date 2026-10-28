from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from core.image_analysis.io_engine import read_image
from core.utils.image_formats import FLUORESCENCE_IMAGE_SUFFIXES


SUPPORTED_FLUORESCENCE_SUFFIXES = FLUORESCENCE_IMAGE_SUFFIXES


@dataclass
class FluorescenceImportResult:
    analysis_data: np.ndarray
    metadata: dict[str, Any]
    source_hash: str
    channel_names: list[str]


def import_fluorescence_image(path: str | Path) -> FluorescenceImportResult:
    result = read_image(path)
    return FluorescenceImportResult(
        analysis_data=result.analysis_data,
        metadata=result.metadata,
        source_hash=result.source_hash,
        channel_names=result.channel_names,
    )
