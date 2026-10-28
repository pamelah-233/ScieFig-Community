from __future__ import annotations

from pathlib import Path


COMMON_RASTER_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
COMMON_VECTOR_SUFFIXES = {".svg"}
COMMON_IMAGE_SUFFIXES = COMMON_RASTER_SUFFIXES | COMMON_VECTOR_SUFFIXES
WB_IMAGE_SUFFIXES = COMMON_RASTER_SUFFIXES
IHC_IMAGE_SUFFIXES = COMMON_RASTER_SUFFIXES | {".ome.tif", ".ome.tiff"}
BRIGHTFIELD_IMAGE_SUFFIXES = IHC_IMAGE_SUFFIXES
FLUORESCENCE_MICROSCOPY_SUFFIXES = {".ome.tif", ".ome.tiff", ".czi", ".nd2"}
FLUORESCENCE_IMAGE_SUFFIXES = COMMON_IMAGE_SUFFIXES | FLUORESCENCE_MICROSCOPY_SUFFIXES
COMPOSER_IMAGE_SUFFIXES = FLUORESCENCE_IMAGE_SUFFIXES | {".pdf"}


def compound_suffix(path: str | Path) -> str:
    name = Path(path).name.lower()
    for suffix in (".ome.tiff", ".ome.tif"):
        if name.endswith(suffix):
            return suffix
    return Path(path).suffix.lower()


def image_filter(label: str, suffixes: set[str]) -> str:
    ordered = sorted(suffixes, key=lambda item: (item.count("."), item))
    return f"{label} ({' '.join('*' + suffix for suffix in ordered)})"


def dropped_image_paths(mime_data, suffixes: set[str]) -> list[str]:
    if not mime_data.hasUrls():
        return []
    paths = []
    for url in mime_data.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if compound_suffix(path) in suffixes:
            paths.append(str(path))
    return paths
