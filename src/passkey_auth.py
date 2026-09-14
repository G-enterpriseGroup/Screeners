"""Server-side WebAuthn/Touch ID support for Raj's Terminal.

Touch ID itself never exposes fingerprint data to the app. The browser invokes
Apple's platform authenticator through WebAuthn and the server verifies the
signed challenge with the credential public key enrolled for this terminal.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_STORE_DIR = Path.home() / ".raj_terminal"
_CREDENTIAL_FILE = _STORE_DIR / "touch_id_credential.json"
_LOCK = threading.Lock()

try:
    from webauthn import (
        generate_authentication_options,
        generate_registration_options,
        options_to_json,
        verify_authentication_response,
        verify_registration_response,
    )
    from webauthn.helpers.structs import (
        AttestationConveyancePreference,
        AuthenticatorAttachment,
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    _WEBAUTHN_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - deployment dependency guard
    _WEBAUTHN_IMPORT_ERROR = str(exc)


def webauthn_ready() -> bool:
    return not bool(_WEBAUTHN_IMPORT_ERROR)


def webauthn_error() -> str:
    return _WEBAUTHN_IMPORT_ERROR


def origin_and_rp_id(app_url: str) -> tuple[str, str]:
    parsed = urlsplit(str(app_url or "").strip())
    host = parsed.hostname or ""
    if not host:
        raise ValueError("Unable to determine the terminal hostname for Touch ID.")
    scheme = parsed.scheme or ("http" if host in {"localhost", "127.0.0.1"} else "https")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{host}{port}", host


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(value)).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    text = str(value or "")
    text += "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text.encode("ascii"))


def _read_record() -> dict[str, Any] | None:
    with _LOCK:
        try:
            payload = json.loads(_CREDENTIAL_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
    return payload if isinstance(payload, dict) else None


def _write_record(payload: dict[str, Any]) -> None:
    with _LOCK:
        _STORE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _CREDENTIAL_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(_CREDENTIAL_FILE)


def load_touch_id_record(app_url: str) -> dict[str, Any] | None:
    """Return the enrolled credential only for the current relying-party host."""
    try:
        _origin, rp_id = origin_and_rp_id(app_url)
    except ValueError:
        return None
    record = _read_record()
    if not record or str(record.get("rp_id") or "") != rp_id:
        return None
    if not record.get("credential_id") or not record.get("public_key"):
        return None
    return record


def build_registration_options(app_url: str, identity_seed: str) -> tuple[dict[str, Any], bytes]:
    if not webauthn_ready():
        raise RuntimeError(_WEBAUTHN_IMPORT_ERROR or "WebAuthn dependency is unavailable.")
    _origin, rp_id = origin_and_rp_id(app_url)
    user_id = hashlib.sha256(str(identity_seed or "raj-terminal").encode("utf-8")).digest()[:32]
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Raj's Terminal",
        user_id=user_id,
        user_name="raj-terminal",
        user_display_name="Raj's Terminal",
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return json.loads(options_to_json(options)), bytes(options.challenge)


def complete_registration(
    app_url: str,
    expected_challenge: bytes,
    credential: dict[str, Any],
) -> dict[str, Any]:
    if not webauthn_ready():
        raise RuntimeError(_WEBAUTHN_IMPORT_ERROR or "WebAuthn dependency is unavailable.")
    origin, rp_id = origin_and_rp_id(app_url)
    verification = verify_registration_response(
        credential=credential,
        expected_challenge=bytes(expected_challenge),
        expected_rp_id=rp_id,
        expected_origin=origin,
        require_user_verification=True,
    )
    transports = ((credential.get("response") or {}).get("transports") or [])
    record = {
        "rp_id": rp_id,
        "origin": origin,
        "credential_id": _b64e(verification.credential_id),
        "public_key": _b64e(verification.credential_public_key),
        "sign_count": int(verification.sign_count or 0),
        "device_type": str(verification.credential_device_type),
        "backed_up": bool(verification.credential_backed_up),
        "transports": [str(value) for value in transports],
    }
    _write_record(record)
    return record


def build_authentication_options(
    app_url: str,
    record: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    if not webauthn_ready():
        raise RuntimeError(_WEBAUTHN_IMPORT_ERROR or "WebAuthn dependency is unavailable.")
    _origin, rp_id = origin_and_rp_id(app_url)
    if str(record.get("rp_id") or "") != rp_id:
        raise ValueError("Touch ID credential belongs to a different terminal hostname.")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=_b64d(str(record["credential_id"])))
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return json.loads(options_to_json(options)), bytes(options.challenge)


def complete_authentication(
    app_url: str,
    expected_challenge: bytes,
    credential: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    if not webauthn_ready():
        raise RuntimeError(_WEBAUTHN_IMPORT_ERROR or "WebAuthn dependency is unavailable.")
    origin, rp_id = origin_and_rp_id(app_url)
    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=bytes(expected_challenge),
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=_b64d(str(record["public_key"])),
        credential_current_sign_count=int(record.get("sign_count") or 0),
        require_user_verification=True,
    )
    updated = dict(record)
    updated["sign_count"] = int(verification.new_sign_count or 0)
    updated["device_type"] = str(verification.credential_device_type)
    updated["backed_up"] = bool(verification.credential_backed_up)
    _write_record(updated)
    return updated
