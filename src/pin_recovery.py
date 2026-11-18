"""Single-use email PIN recovery that never removes the TOTP requirement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
import hashlib
import re
import secrets
import smtplib
import ssl
from urllib.parse import urlencode, urlsplit

from src.terminal_security import AuthService, _now, encode_reset_pin, identity_key


class RecoveryConfigurationError(RuntimeError):
    """Sanitized recovery configuration failure."""


@dataclass(frozen=True)
class RecoveryConfig:
    smtp_host: str
    smtp_port: int
    smtp_username: str = field(repr=False)
    smtp_password: str = field(repr=False)
    sender: str
    public_base_url: str

    def __post_init__(self):
        if (not isinstance(self.smtp_host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.smtp_host)
                or type(self.smtp_port) is not int or self.smtp_port not in (465, 587)):
            raise RecoveryConfigurationError("Configure authenticated SMTP with TLS on port 465 or 587.")
        if (not isinstance(self.smtp_username, str) or not self.smtp_username.strip()
                or any(c in self.smtp_username for c in "\r\n")
                or not isinstance(self.smtp_password, str) or not self.smtp_password):
            raise RecoveryConfigurationError("Configure the server-side SMTP credentials.")
        if not isinstance(self.sender, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.sender):
            raise RecoveryConfigurationError("Configure a valid fixed recovery sender address.")
        try:
            url = urlsplit(self.public_base_url)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.port not in (None, 443) or url.query or url.fragment
                    or any(c.isspace() for c in self.public_base_url)):
                raise ValueError
        except (TypeError, ValueError):
            raise RecoveryConfigurationError("Configure the fixed HTTPS public app URL for PIN recovery.") from None

    @classmethod
    def from_runtime(cls, values: Mapping) -> RecoveryConfig:
        security = values.get("security", {}) if isinstance(values, Mapping) else {}
        section = security.get("pin_recovery", {}) if isinstance(security, Mapping) else {}
        if not isinstance(section, Mapping) or section.get("enabled") is not True:
            raise RecoveryConfigurationError("Email PIN recovery is not configured.")
        return cls(section.get("smtp_host"), section.get("smtp_port"),
                   section.get("smtp_username"), section.get("smtp_password"),
                   section.get("sender"), section.get("public_base_url"))


class RecoveryService:
    def __init__(self, auth: AuthService, config: RecoveryConfig):
        self.auth = auth
        self.config = config

    @staticmethod
    def _container(state: dict, now: float) -> dict:
        recovery = state.setdefault("recovery", {})
        tokens = recovery.setdefault("tokens", {})
        requests = recovery.setdefault("requests", {})
        if not isinstance(tokens, dict) or not isinstance(requests, dict):
            raise ValueError("Recovery state is invalid.")
        # Keep bounded records; a missing/removed digest is never a valid token.
        for key, row in list(tokens.items()):
            if (not isinstance(row, dict) or type(row.get("expires_at")) not in (int, float)
                    or row["expires_at"] <= now):
                tokens.pop(key, None)
        for key, row in list(requests.items()):
            if (not isinstance(row, dict) or type(row.get("window_start")) not in (int, float)
                    or now - row["window_start"] >= 3600):
                requests.pop(key, None)
        if len(tokens) >= 1000 or len(requests) >= 1000:
            raise ValueError("Recovery state is full.")
        return recovery

    def _send(self, recipient: str, token: str) -> None:
        url = self.config.public_base_url.rstrip("/") + "/?" + urlencode({"pin_reset": token})
        message = EmailMessage()
        message["From"] = self.config.sender
        message["To"] = recipient
        message["Subject"] = "Raj's Terminal PIN reset request"
        message.set_content(
            "A PIN reset was requested for your registered Raj's Terminal identity.\n\n"
            "Open this single-use link within 15 minutes:\n" + url + "\n\n"
            "This resets only your PIN. Your authenticator code is still required to sign in.\n"
            "If you did not request this, ignore the message and contact the security owner.\n"
            "Do not forward this link or send your PIN or authenticator setup key to anyone.\n"
        )
        context = ssl.create_default_context()
        if self.config.smtp_port == 465:
            with smtplib.SMTP_SSL(self.config.smtp_host, 465, timeout=20, context=context) as smtp:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(message, from_addr=self.config.sender, to_addrs=[recipient])
        else:
            with smtplib.SMTP(self.config.smtp_host, 587, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(message, from_addr=self.config.sender, to_addrs=[recipient])

    def request(self, user_id: str, now=None) -> None:
        """Always return the same result. No recipient is accepted from a form."""
        now = _now(now)
        user = self.auth.policy.users.get(user_id) if isinstance(user_id, str) else None
        rate_key = identity_key(user.user_id if user else "__unknown_recovery_identity__")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        issued = False

        def attempt(state):
            nonlocal issued
            issued = False
            recovery = self._container(state, now)
            row = recovery["requests"].get(rate_key)
            if row and (now - row["sent_at"] < 60 or row["count"] >= 5):
                return
            if row is None:
                row = {"window_start": now, "sent_at": now, "count": 0}
                recovery["requests"][rate_key] = row
            row.update(sent_at=now, count=row["count"] + 1)
            if user is None or not user.active(now):
                return
            recovery["tokens"][digest] = {
                "user_id": user.user_id, "policy": self.auth.policy.fingerprint,
                "pin_revision": self.auth.pin_revision_from_state(state, user.user_id),
                "expires_at": now + 900, "used": False,
            }
            issued = True

        try:
            self.auth.store.update(attempt)
            if issued:
                try:
                    self._send(user.email, token)
                except Exception:
                    # The send outcome may be uncertain. Invalidate its token
                    # rather than leaving an undelivered credential active.
                    def invalidate(state):
                        entry = state.get("recovery", {}).get("tokens", {}).get(digest)
                        if isinstance(entry, dict):
                            entry["used"] = True
                    self.auth.store.update(invalidate)
        except Exception:
            # Identical UI outcome for unknown, throttled and unavailable users.
            # Never expose SMTP exception strings, recipients or reset tokens.
            return None
        return None

    def _valid(self, state: dict, digest: str, now: float):
        entry = state.get("recovery", {}).get("tokens", {}).get(digest)
        if (not isinstance(entry, dict) or entry.get("used") is not False
                or type(entry.get("expires_at")) not in (int, float)
                or not now < entry["expires_at"] <= now + 900
                or entry.get("policy") != self.auth.policy.fingerprint):
            return None
        identity = entry.get("user_id")
        user = self.auth.policy.users.get(identity) if isinstance(identity, str) else None
        if (not user or not user.active(now)
                or entry.get("pin_revision") != self.auth.pin_revision_from_state(state, user.user_id)):
            return None
        return user

    def redeem(self, token: str, new_pin: str, now=None) -> bool:
        now = _now(now)
        if (not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
                or not isinstance(new_pin, str) or not re.fullmatch(r"[0-9]{8,32}", new_pin)):
            return False
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        accepted = False
        try:
            if self._valid(self.auth.store.read(), digest, now) is None:
                return False
            encoded = encode_reset_pin(new_pin)
            revision = secrets.token_hex(24)

            def consume(state):
                nonlocal accepted
                accepted = False
                user = self._valid(state, digest, now)
                if user is None:
                    return
                self.auth.set_pin_override_in_state(state, user.user_id, encoded, now, revision)
                state["recovery"]["tokens"][digest]["used"] = True
                accepted = True

            self.auth.store.update(consume)
        except Exception:
            return False
        return accepted
