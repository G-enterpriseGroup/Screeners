"""Server-side WebAuthn/Touch ID support for Raj's Terminal.

Touch ID itself never exposes fingerprint data to the app. The browser invokes
Apple's platform authenticator through WebAuthn and the server verifies the
signed challenge with the credential public key enrolled for this terminal.

Version 2 credentials are deliberately discoverable platform passkeys. That
lets Safari/Chrome/macOS route authentication to the Mac's built-in platform
authenticator instead of falling back to a removable security-key ceremony.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_STORE_DIR = Path.home() / ".raj_terminal"
_CREDENTIAL_FILE = _STORE_DIR / "touch_id_credential.json"
_LOCK = threading.Lock()
_CREDENTIAL_VERSION = 2
_BROWSER_MEMORY_VERSION = 1

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


def _validated_record(app_url: str, raw: Any) -> dict[str, Any] | None:
    """Validate and normalize the non-secret public passkey verification record."""
    if not isinstance(raw, dict):
        return None
    try:
        origin, rp_id = origin_and_rp_id(app_url)
        credential_version = int(raw.get("credential_version") or 0)
        sign_count = max(0, int(raw.get("sign_count") or 0))
    except (TypeError, ValueError):
        return None
    if credential_version < _CREDENTIAL_VERSION:
        return None
    if str(raw.get("rp_id") or "") != rp_id:
        return None
    if str(raw.get("origin") or "") not in {"", origin}:
        return None
    credential_id = str(raw.get("credential_id") or "").strip()
    public_key = str(raw.get("public_key") or "").strip()
    if not credential_id or not public_key:
        return None
    if str(raw.get("authenticator_attachment") or "") != "platform":
        return None
    transports = [
        str(value).strip().lower()
        for value in (raw.get("transports") or [])
        if str(value).strip()
    ]
    if transports and "internal" not in set(transports):
        return None
    if not transports:
        transports = ["internal"]
    return {
        "credential_version": credential_version,
        "rp_id": rp_id,
        "origin": origin,
        "credential_id": credential_id,
        "public_key": public_key,
        "sign_count": sign_count,
        "device_type": str(raw.get("device_type") or ""),
        "backed_up": bool(raw.get("backed_up")),
        "authenticator_attachment": "platform",
        "discoverable": bool(raw.get("discoverable", True)),
        "transports": transports,
    }


def _browser_memory_key(identity_seed: str) -> bytes:
    """Derive a stable server-only MAC key from the terminal access-code hash."""
    material = ("raj-terminal-touch-id-memory-v1:" + str(identity_seed or "")).encode("utf-8")
    return hashlib.sha256(material).digest()


def _browser_memory_payload(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal_touch_id_record(
    app_url: str,
    record: dict[str, Any],
    identity_seed: str,
) -> dict[str, Any]:
    """Create a tamper-evident browser-memory envelope for the public passkey record.

    The fingerprint/biometric template and private key never enter this payload.
    The HMAC prevents browser-local storage from becoming an unauthenticated
    replacement trust anchor after a Streamlit process/container reboot.
    """
    cleaned = _validated_record(app_url, record)
    if not cleaned:
        raise ValueError("Touch ID record cannot be persisted for this terminal origin.")
    mac = hmac.new(
        _browser_memory_key(identity_seed),
        _browser_memory_payload(cleaned),
        hashlib.sha256,
    ).hexdigest()
    return {
        "memory_version": _BROWSER_MEMORY_VERSION,
        "record": cleaned,
        "mac": mac,
    }


def restore_touch_id_record(
    app_url: str,
    envelope: Any,
    identity_seed: str,
) -> dict[str, Any] | None:
    """Restore a sealed browser-backed passkey record after server reboot."""
    if not isinstance(envelope, dict):
        return None
    try:
        version = int(envelope.get("memory_version") or 0)
    except (TypeError, ValueError):
        return None
    if version != _BROWSER_MEMORY_VERSION:
        return None
    record = _validated_record(app_url, envelope.get("record"))
    supplied_mac = str(envelope.get("mac") or "")
    if not record or not supplied_mac:
        return None
    expected_mac = hmac.new(
        _browser_memory_key(identity_seed),
        _browser_memory_payload(record),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_mac, expected_mac):
        return None
    _write_record(record)
    return record


def clear_touch_id_record() -> None:
    """Forget only the terminal's server-side passkey record."""
    with _LOCK:
        try:
            _CREDENTIAL_FILE.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def load_touch_id_record(app_url: str) -> dict[str, Any] | None:
    """Return only a current platform/discoverable credential for this host.

    The server file is a runtime cache. Durable reboot survival is provided by
    the HMAC-sealed browser-memory envelope restored through the lock component.
    """
    return _validated_record(app_url, _read_record())


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
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    payload = json.loads(options_to_json(options))
    # WebAuthn L3 hint. Unsupported browsers simply ignore it.
    payload["hints"] = ["client-device"]
    return payload, bytes(options.challenge)


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

    attachment = str(credential.get("authenticatorAttachment") or "").strip().lower()
    if attachment and attachment != "platform":
        raise ValueError("A Mac platform passkey is required; external security keys are not accepted.")

    transports = [
        str(value).strip().lower()
        for value in ((credential.get("response") or {}).get("transports") or [])
        if str(value).strip()
    ]
    if transports and "internal" not in transports:
        raise ValueError("Touch ID setup did not return an internal Mac authenticator.")
    if not transports:
        # Platform attachment is already required above. Some browser versions
        # omit getTransports(); persist the expected internal transport.
        transports = ["internal"]

    record = {
        "credential_version": _CREDENTIAL_VERSION,
        "rp_id": rp_id,
        "origin": origin,
        "credential_id": _b64e(verification.credential_id),
        "public_key": _b64e(verification.credential_public_key),
        "sign_count": int(verification.sign_count or 0),
        "device_type": str(verification.credential_device_type),
        "backed_up": bool(verification.credential_backed_up),
        "authenticator_attachment": "platform",
        "discoverable": True,
        "transports": transports,
    }
    _write_record(record)
    return record


def build_authentication_options(
    app_url: str,
    record: dict[str, Any],
) -> tuple[dict[str, Any], bytes]:
    """Build a discoverable-passkey request biased to the current Mac.

    We intentionally omit allowCredentials for v2 credentials. A discoverable
    platform passkey lets macOS choose the built-in authenticator directly,
    instead of presenting a removable-security-key path for a stale credential
    ID. The response credential ID is still matched server-side before verify.
    """
    if not webauthn_ready():
        raise RuntimeError(_WEBAUTHN_IMPORT_ERROR or "WebAuthn dependency is unavailable.")
    _origin, rp_id = origin_and_rp_id(app_url)
    if str(record.get("rp_id") or "") != rp_id:
        raise ValueError("Touch ID credential belongs to a different terminal hostname.")
    if int(record.get("credential_version") or 0) < _CREDENTIAL_VERSION:
        raise ValueError("Touch ID must be enrolled again with the current platform-passkey flow.")

    options = generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    payload = json.loads(options_to_json(options))
    payload.pop("allowCredentials", None)
    payload["hints"] = ["client-device"]
    return payload, bytes(options.challenge)


def complete_authentication(
    app_url: str,
    expected_challenge: bytes,
    credential: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    if not webauthn_ready():
        raise RuntimeError(_WEBAUTHN_IMPORT_ERROR or "WebAuthn dependency is unavailable.")
    origin, rp_id = origin_and_rp_id(app_url)

    returned_id = str(credential.get("id") or credential.get("rawId") or "").rstrip("=")
    expected_id = str(record.get("credential_id") or "").rstrip("=")
    if not returned_id or returned_id != expected_id:
        raise ValueError("The selected passkey is not the one enrolled for Raj's Terminal.")

    attachment = str(credential.get("authenticatorAttachment") or "").strip().lower()
    if attachment and attachment != "platform":
        raise ValueError("External security keys are not accepted for this Touch ID shortcut.")

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
