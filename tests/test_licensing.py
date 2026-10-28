from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.licensing import (
    LICENSE_FILENAME,
    LicenseError,
    LicenseManager,
    compute_machine_code_from_host_id,
)


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "issuer_private_key.pem"
    public_path = tmp_path / "license_public_key.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def _runtime_manager(
    tmp_path: Path,
    public_key_path: Path,
    *,
    host_id: str = "windows-machine-guid-001",
) -> LicenseManager:
    return LicenseManager(
        public_key_path=public_key_path,
        license_storage_path=tmp_path / LICENSE_FILENAME,
        machine_id_provider=lambda: host_id,
    )


def test_machine_code_is_deterministic_and_non_reversible() -> None:
    machine_code = compute_machine_code_from_host_id("12345678-90ab-cdef-1234-567890abcdef")

    assert machine_code == compute_machine_code_from_host_id("12345678-90ab-cdef-1234-567890abcdef")
    assert machine_code != compute_machine_code_from_host_id("another-host-guid")
    assert machine_code == machine_code.upper()
    assert "12345678" not in machine_code
    assert "-" in machine_code


def test_license_status_is_missing_before_install(tmp_path: Path) -> None:
    _private_key_path, public_key_path = _write_keypair(tmp_path)
    manager = _runtime_manager(tmp_path, public_key_path)

    status = manager.license_status()

    assert status.is_valid is False
    assert status.reason == "missing"
    assert status.payload is None


def test_issuer_cli_round_trip_produces_valid_r095_all_features_license(tmp_path: Path) -> None:
    private_key_path, public_key_path = _write_keypair(tmp_path)
    manager = _runtime_manager(tmp_path, public_key_path)
    output_path = tmp_path / "issued_license.json"
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "licensing" / "issue_r095_license.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--private-key",
            str(private_key_path),
            "--machine-code",
            manager.current_machine_code(),
            "--output",
            str(output_path),
            "--license-id",
            "lic-r095-0001",
            "--issued-at",
            "2026-06-22T10:00:00Z",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stderr == ""
    assert completed.stdout.strip() == str(output_path)
    assert output_path.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "issued_license.json",
        "issuer_private_key.pem",
        "license_public_key.pem",
    ]

    installed_path = manager.install_license_file(output_path)
    status = manager.license_status()

    assert installed_path == tmp_path / LICENSE_FILENAME
    assert status.is_valid is True
    assert status.reason == "valid"
    assert status.payload is not None
    assert status.payload.product == "ScieFig"
    assert status.payload.channel == "R0.95"
    assert status.payload.entitlement == "all_features"
    assert status.payload.license_id == "lic-r095-0001"
    assert status.payload.issued_at == "2026-06-22T10:00:00Z"
    stored_payload = json.loads(installed_path.read_text(encoding="utf-8"))
    assert "machine_guid" not in json.dumps(stored_payload)


def test_license_validation_rejects_tampered_payload_wrong_product_channel_entitlement_and_machine(tmp_path: Path) -> None:
    private_key_path, public_key_path = _write_keypair(tmp_path)
    manager = _runtime_manager(tmp_path, public_key_path)
    output_path = tmp_path / "issued_license.json"
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "licensing" / "issue_r095_license.py"

    subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--private-key",
            str(private_key_path),
            "--machine-code",
            manager.current_machine_code(),
            "--output",
            str(output_path),
            "--license-id",
            "lic-r095-0002",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    envelope = json.loads(output_path.read_text(encoding="utf-8"))

    tampered_payload_path = tmp_path / "tampered.json"
    tampered_payload = json.loads(json.dumps(envelope))
    tampered_payload["payload"]["license_id"] = "lic-r095-tampered"
    tampered_payload_path.write_text(json.dumps(tampered_payload, indent=2), encoding="utf-8")
    with pytest.raises(LicenseError, match="signature"):
        manager.validate_license_file(tampered_payload_path)

    wrong_product_path = tmp_path / "wrong_product.json"
    wrong_product = json.loads(json.dumps(envelope))
    wrong_product["payload"]["product"] = "OtherApp"
    wrong_product_path.write_text(json.dumps(wrong_product, indent=2), encoding="utf-8")
    with pytest.raises(LicenseError, match="product"):
        manager.validate_license_file(wrong_product_path, skip_signature=True)

    wrong_channel_path = tmp_path / "wrong_channel.json"
    wrong_channel = json.loads(json.dumps(envelope))
    wrong_channel["payload"]["channel"] = "R0.9"
    wrong_channel_path.write_text(json.dumps(wrong_channel, indent=2), encoding="utf-8")
    with pytest.raises(LicenseError, match="channel"):
        manager.validate_license_file(wrong_channel_path, skip_signature=True)

    wrong_entitlement_path = tmp_path / "wrong_entitlement.json"
    wrong_entitlement = json.loads(json.dumps(envelope))
    wrong_entitlement["payload"]["entitlement"] = "analysis_only"
    wrong_entitlement_path.write_text(json.dumps(wrong_entitlement, indent=2), encoding="utf-8")
    with pytest.raises(LicenseError, match="entitlement"):
        manager.validate_license_file(wrong_entitlement_path, skip_signature=True)

    wrong_machine_manager = _runtime_manager(tmp_path, public_key_path, host_id="some-other-machine")
    with pytest.raises(LicenseError, match="machine"):
        wrong_machine_manager.validate_license_file(output_path)


def test_license_status_rejects_invalid_signature_in_installed_license(tmp_path: Path) -> None:
    _private_key_path, public_key_path = _write_keypair(tmp_path)
    manager = _runtime_manager(tmp_path, public_key_path)
    installed_path = tmp_path / LICENSE_FILENAME
    installed_path.write_text(
        json.dumps(
            {
                "payload": {
                    "product": "ScieFig",
                    "channel": "R0.95",
                    "license_id": "lic-r095-bad-signature",
                    "machine_hash": manager.current_machine_code(),
                    "entitlement": "all_features",
                    "issued_at": "2026-06-22T11:00:00Z",
                },
                "signature": "00",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    status = manager.license_status()

    assert status.is_valid is False
    assert status.reason == "invalid"
    assert "signature" in status.message.casefold()
