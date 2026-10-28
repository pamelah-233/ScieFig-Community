from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PRODUCT_NAME: Final[str] = "ScieFig"
LICENSE_CHANNEL: Final[str] = "R0.95"
LICENSE_ENTITLEMENT: Final[str] = "all_features"
LICENSE_FILENAME: Final[str] = "license.json"
PUBLIC_KEY_FILENAME: Final[str] = "license_public_key.pem"
PUBLIC_KEY_ENV_VAR: Final[str] = "SCIEFIG_LICENSE_PUBLIC_KEY_PATH"
WINDOWS_MACHINE_GUID_ENV_VAR: Final[str] = "SCIEFIG_MACHINE_GUID"


class LicenseError(RuntimeError):
    """Raised when a license cannot be issued, loaded, or validated."""


@dataclass(frozen=True)
class LicensePayload:
    product: str
    channel: str
    license_id: str
    machine_hash: str
    entitlement: str
    issued_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "product": self.product,
            "channel": self.channel,
            "license_id": self.license_id,
            "machine_hash": self.machine_hash,
            "entitlement": self.entitlement,
            "issued_at": self.issued_at,
        }


@dataclass(frozen=True)
class LicenseStatus:
    is_valid: bool
    reason: str
    message: str
    payload: LicensePayload | None = None


def canonicalize_license_payload(payload: LicensePayload | dict[str, str]) -> bytes:
    payload_dict = payload.to_dict() if isinstance(payload, LicensePayload) else dict(payload)
    return json.dumps(payload_dict, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compute_machine_code_from_host_id(host_id: str) -> str:
    normalized = host_id.strip()
    if not normalized:
        raise LicenseError("Machine identifier is empty.")
    digest = hashlib.sha256(f"{PRODUCT_NAME}|{LICENSE_CHANNEL}|{normalized}".encode("utf-8")).hexdigest().upper()
    return "-".join(digest[index : index + 4] for index in range(0, 32, 4))


def _load_private_key(private_key_path: Path) -> Ed25519PrivateKey:
    key_bytes = private_key_path.read_bytes()
    key = serialization.load_pem_private_key(key_bytes, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError("Private key must be an Ed25519 PEM key.")
    return key


def _load_public_key(public_key_path: Path) -> Ed25519PublicKey:
    key_bytes = public_key_path.read_bytes()
    key = serialization.load_pem_public_key(key_bytes)
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("Public key must be an Ed25519 PEM key.")
    return key


def issue_license_file(
    *,
    private_key_path: Path,
    machine_code: str,
    output_path: Path,
    license_id: str,
    issued_at: str | None = None,
) -> Path:
    if not license_id.strip():
        raise LicenseError("License id must be non-empty.")
    payload = LicensePayload(
        product=PRODUCT_NAME,
        channel=LICENSE_CHANNEL,
        license_id=license_id.strip(),
        machine_hash=machine_code.strip(),
        entitlement=LICENSE_ENTITLEMENT,
        issued_at=(issued_at or utc_now_iso()).strip(),
    )
    signature = _load_private_key(private_key_path).sign(canonicalize_license_payload(payload)).hex()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"payload": payload.to_dict(), "signature": signature}, indent=2),
        encoding="utf-8",
    )
    return output_path


def default_license_storage_path() -> Path:
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        return Path(program_data) / PRODUCT_NAME / LICENSE_FILENAME
    return Path.home() / ".sciefig_programdata" / PRODUCT_NAME / LICENSE_FILENAME


def read_windows_machine_guid() -> str:
    env_value = os.environ.get(WINDOWS_MACHINE_GUID_ENV_VAR)
    if env_value:
        return env_value
    try:
        import winreg  # type: ignore[attr-defined]
    except ImportError as exc:  # pragma: no cover - Windows-only path
        raise LicenseError("Windows machine identifier is unavailable on this platform.") from exc
    try:  # pragma: no cover - exercised on Windows build machines rather than tests
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _value_type = winreg.QueryValueEx(key, "MachineGuid")
    except OSError as exc:  # pragma: no cover - Windows-only path
        raise LicenseError("Could not read the Windows MachineGuid.") from exc
    if not str(value).strip():
        raise LicenseError("Windows MachineGuid is blank.")
    return str(value)


class LicenseManager:
    def __init__(
        self,
        *,
        public_key_path: Path | None = None,
        license_storage_path: Path | None = None,
        machine_id_provider: Callable[[], str] | None = None,
        resources_dir: Path | None = None,
    ) -> None:
        self._resources_dir = Path(resources_dir) if resources_dir is not None else None
        self.public_key_path = Path(public_key_path) if public_key_path is not None else self._resolve_public_key_path()
        self.license_storage_path = Path(license_storage_path) if license_storage_path is not None else default_license_storage_path()
        self.machine_id_provider = machine_id_provider or read_windows_machine_guid

    def _resolve_public_key_path(self) -> Path:
        configured = os.environ.get(PUBLIC_KEY_ENV_VAR)
        if configured:
            return Path(configured)
        if self._resources_dir is not None:
            return self._resources_dir / PUBLIC_KEY_FILENAME
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")) / "resources" / PUBLIC_KEY_FILENAME
        return Path(__file__).resolve().parents[1] / "resources" / PUBLIC_KEY_FILENAME

    def current_machine_code(self) -> str:
        return compute_machine_code_from_host_id(self.machine_id_provider())

    def validate_license_file(self, path: str | Path, *, skip_signature: bool = False) -> LicensePayload:
        try:
            envelope = json.loads(Path(path).read_text(encoding="utf-8"))
            payload = LicensePayload(**dict(envelope["payload"]))
            signature = str(envelope["signature"])
        except (FileNotFoundError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LicenseError(f"License file is invalid: {exc}") from exc
        self._validate_payload_contract(payload)
        if not skip_signature:
            self._verify_signature(payload, signature)
        return payload

    def _validate_payload_contract(self, payload: LicensePayload) -> None:
        if payload.product != PRODUCT_NAME:
            raise LicenseError("License product does not match ScieFig.")
        if payload.channel != LICENSE_CHANNEL:
            raise LicenseError("License channel does not match R0.95.")
        if payload.entitlement != LICENSE_ENTITLEMENT:
            raise LicenseError("License entitlement does not grant all features.")
        if payload.machine_hash != self.current_machine_code():
            raise LicenseError("License machine code does not match this computer.")
        if not payload.license_id.strip():
            raise LicenseError("License id is missing.")
        if not payload.issued_at.strip():
            raise LicenseError("License issued time is missing.")

    def _verify_signature(self, payload: LicensePayload, signature_hex: str) -> None:
        try:
            signature = bytes.fromhex(signature_hex)
        except ValueError as exc:
            raise LicenseError("License signature is invalid.") from exc
        try:
            _load_public_key(self.public_key_path).verify(signature, canonicalize_license_payload(payload))
        except FileNotFoundError as exc:
            raise LicenseError(f"License public key is missing: {self.public_key_path}") from exc
        except InvalidSignature as exc:
            raise LicenseError("License signature verification failed.") from exc

    def install_license_file(self, path: str | Path) -> Path:
        source_path = Path(path)
        self.validate_license_file(source_path)
        destination = self.license_storage_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_suffix(".tmp")
        shutil.copyfile(source_path, temp_path)
        temp_path.replace(destination)
        return destination

    def license_status(self) -> LicenseStatus:
        if not self.license_storage_path.exists():
            return LicenseStatus(is_valid=False, reason="missing", message="No installed license was found.")
        try:
            payload = self.validate_license_file(self.license_storage_path)
        except LicenseError as exc:
            return LicenseStatus(is_valid=False, reason="invalid", message=str(exc))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            return LicenseStatus(is_valid=False, reason="invalid", message=f"Installed license is unreadable: {exc}")
        return LicenseStatus(is_valid=True, reason="valid", message="License is valid.", payload=payload)
