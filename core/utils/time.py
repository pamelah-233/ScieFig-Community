from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Return a timezone-aware UTC timestamp suitable for persisted audit logs."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

