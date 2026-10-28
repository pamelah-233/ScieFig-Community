from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.utils.time import utc_now_iso


@dataclass(slots=True)
class AuditEntry:
    timestamp: str
    action: str
    detail: str

    @classmethod
    def create(cls, action: str, detail: str) -> "AuditEntry":
        return cls(timestamp=utc_now_iso(), action=action, detail=detail)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AuditEntry":
        return cls(
            timestamp=str(payload["timestamp"]),
            action=str(payload["action"]),
            detail=str(payload["detail"]),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "detail": self.detail,
        }


def audit_log_from_json(payload: list[dict[str, Any]]) -> list[AuditEntry]:
    return [AuditEntry.from_dict(item) for item in payload]


def audit_log_to_json(entries: list[AuditEntry]) -> list[dict[str, str]]:
    return [entry.to_dict() for entry in entries]

