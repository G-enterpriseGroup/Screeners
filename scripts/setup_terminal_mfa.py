"""User-run local TOTP enrollment. Never invoke this with real credentials in CI.

Secrets are written to an owner-readable file outside the repository and never
printed. This script makes no network calls and never changes a deployed app.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
from urllib.parse import quote, urlencode


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.terminal_security import totp_code


def _provisioning_uri(email: str, secret: str) -> str:
    return "otpauth://totp/" + quote("Raj's Terminal:" + email, safe="") + "?" + urlencode({
        "secret": secret, "issuer": "Raj's Terminal", "algorithm": "SHA1", "digits": 6, "period": 30,
    })


def _write_private_qr(path: Path, email: str, secret: str) -> bool:
    """Optional offline SVG; never render a credential to the console or network."""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathFillImage
    except ImportError:
        return False
    image = qrcode.make(_provisioning_uri(email, secret), image_factory=SvgPathFillImage, border=4)
    data = image.to_string(encoding="utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return True


def _body(email: str, pin: str, secret: str, verified: bool) -> str:
    uri = _provisioning_uri(email, secret)
    return (
        "# PRIVATE credentials. Keep outside Git, screenshots, chat and email.\n"
        "# Use totp_secret as the manual setup key in an RFC 6238 authenticator.\n"
        "# Provisioning URI for a LOCAL trusted QR tool only: " + uri + "\n"
        "[security]\n"
        "owner_email = " + json.dumps(email) + "\n"
        "require_mfa = true\n"
        "login_pin_sha256 = " + json.dumps(hashlib.sha256(pin.encode()).hexdigest()) + "\n"
        "totp_secret = " + json.dumps(secret) + "\n"
        "totp_enrollment_verified = " + ("true" if verified else "false") + "\n"
        "credential_version = 1\n"
        "idle_timeout_seconds = 900\n"
        "absolute_timeout_seconds = 28800\n"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Enroll an authenticator locally without uploading or printing secrets.")
    parser.add_argument("--email", required=True, help="Owner email for the server-side identity registry")
    parser.add_argument("--output", type=Path, default=Path.home() / ".raj_terminal" / "terminal-mfa-secrets.toml",
                        help="New private file outside the source repository; existing files are never overwritten")
    parser.add_argument("--qr", action="store_true",
                        help="Optionally create a private offline QR SVG next to the file (requires local qrcode package)")
    args = parser.parse_args(argv)
    email = args.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        parser.error("Provide a valid owner email.")
    path = args.output.expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        parser.error("Write the enrollment file outside the source repository.")
    if path.exists():
        parser.error("The output file already exists. Preserve it; choose another private filename if re-enrolling.")
    qr_path = path.with_suffix(".qr.svg")
    if args.qr and qr_path.exists():
        parser.error("The private QR filename already exists. Choose another private enrollment filename.")
    pin = getpass.getpass("Choose a new Terminal PIN (8-32 digits): ")
    if not re.fullmatch(r"[0-9]{8,32}", pin):
        parser.error("Use 8-32 ASCII digits.")
    if not hmac.compare_digest(pin, getpass.getpass("Confirm the new PIN: ")):
        parser.error("The PIN entries do not match.")
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(_body(email, pin, secret, False))
    print("Private enrollment file created:", path)
    if args.qr:
        try:
            qr_written = _write_private_qr(qr_path, email, secret)
        except (OSError, ValueError, TypeError):
            qr_written = False
        if qr_written:
            print("Private offline QR SVG created:", qr_path)
            print("Open it only on your own computer and scan it with your authenticator; never upload or share it.")
        else:
            print("Optional QR was not created. Use the private manual setup key, or install local qrcode and re-enroll to a new file.")
    print("Open that file locally and enter its totp_secret as the setup key in your authenticator.")
    print("Do not upload the setup key or provisioning URI to a QR website, chat, email or support ticket.")
    for _ in range(3):
        code = getpass.getpass("Enter the six-digit code from the authenticator: ").strip()
        now = time.time()
        counter = int(now // 30)
        valid = bool(re.fullmatch(r"[0-9]{6}", code)) and any(
            hmac.compare_digest(totp_code(secret, step * 30), code)
            for step in range(max(0, counter - 1), counter + 2)
        )
        if valid:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".terminal-mfa-", delete=False) as handle:
                handle.write(_body(email, pin, secret, True))
                temp = Path(handle.name)
            os.chmod(temp, 0o600)
            os.replace(temp, path)
            print("Authenticator verified. No production setting has been changed.")
            print("Copy the security values into Streamlit Secrets, preserving existing security entries and other sections.")
            print("Preserve the existing trade_access_code_sha256: it seals existing broker/cache data. The new login_pin_sha256 is separate.")
            print("Keep your app's encrypted authentication-store configuration and any existing Touch ID seal secret.")
            return 0
        print("Code was not verified. Check the device time and try the next code.")
    print("Enrollment is not verified. The private file remains marked unverified; do not activate it.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
