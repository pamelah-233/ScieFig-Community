from __future__ import annotations

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover - depends on optional dependency
    ort = None

from .cache import is_model_cached, model_cache_path

AI_PACK_INSTALL_HINT = "pip install -r requirements.txt -r requirements-ai.txt"


class AIBackendUnavailableError(RuntimeError):
    """Raised when the optional ONNX Runtime backend is unavailable."""


class ModelNotCachedError(FileNotFoundError):
    """Raised when a model file is absent from cache or fails hash verification."""


def is_ort_available() -> bool:
    return ort is not None


def load_session(entry, cache_root=None, providers=None):
    if ort is None:
        raise AIBackendUnavailableError(
            f"onnxruntime is not installed. Install AI Pack extras with: {AI_PACK_INSTALL_HINT}"
        )

    if not is_model_cached(entry, cache_root=cache_root):
        path = model_cache_path(entry, cache_root=cache_root)
        raise ModelNotCachedError(f"Model cache entry is missing or hash verification failed: {path}")

    path = model_cache_path(entry, cache_root=cache_root)
    session_providers = list(providers or ["CPUExecutionProvider"])
    return ort.InferenceSession(str(path), providers=session_providers)


def run_inference(session, input_array, input_name=None):
    if input_name is None:
        input_name = session.get_inputs()[0].name
    return session.run(None, {input_name: input_array})
