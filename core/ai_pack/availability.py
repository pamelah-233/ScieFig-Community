from __future__ import annotations

from dataclasses import dataclass

from .cache import is_model_cached, model_cache_path
from .runtime import AI_PACK_INSTALL_HINT, is_ort_available, ort


@dataclass(slots=True)
class AIPackAvailability:
    backend_available: bool
    backend_reason: str
    model_available: bool = False
    model_reason: str = ""
    install_hint: str = ""
    runtime_version: str = ""

    @property
    def fully_available(self) -> bool:
        return self.backend_available and self.model_available


def check_backend_availability() -> AIPackAvailability:
    if not is_ort_available():
        hint = AI_PACK_INSTALL_HINT
        return AIPackAvailability(
            backend_available=False,
            backend_reason=f"onnxruntime is not installed. Install AI Pack extras with: {hint}",
            install_hint=hint,
        )

    return AIPackAvailability(
        backend_available=True,
        backend_reason="onnxruntime backend available",
        runtime_version=str(getattr(ort, "__version__", "") or ""),
    )


def check_model_availability(entry, cache_root=None) -> AIPackAvailability:
    availability = check_backend_availability()
    cached = is_model_cached(entry, cache_root=cache_root)
    path = model_cache_path(entry, cache_root=cache_root)
    availability.model_available = cached
    availability.model_reason = (
        f"Model cache is available at {path}"
        if cached
        else f"Model cache missing or hash verification failed at {path}"
    )
    return availability
