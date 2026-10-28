from __future__ import annotations


def normalize_ihc_mode(mode: object) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in {"assisted", "expert"}:
        return normalized
    return "foolproof"
