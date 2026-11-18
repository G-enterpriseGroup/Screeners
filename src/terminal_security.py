"""Server-side identities, PIN plus TOTP MFA, and revocable access sessions.

This module deliberately has no Streamlit or financial-feature imports. OTP
acceptance and account-wide lockouts must use the durable CAS store; a browser
session or process cache is never a substitute for that store.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
import json
import math
import re
import secrets as random_secrets
import struct
import time
from types import MappingProxyType

from src.security_store import SecurityStoreError


PENDING_SESSION_KEY = "_terminal_security_pending_v1"
AUTH_SESSION_KEY = "_terminal_security_authenticated_v1"
FIRST_FACTOR_SECONDS = 300
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
_PUBLIC_FALLBACK_HASH = "d740238f374425d95f71dcd05dd1486800f0887790b992b74ef41fbb5c8e1167"


class SecurityConfigurationError(RuntimeError):
    """Invalid or incomplete server-only identity policy."""


def identity_key(user_id: str) -> str:
    return hashlib.sha256(("identity:v1:" + user_id).encode()).hexdigest()


def encode_reset_pin(pin: str) -> str:
    """Encode a new PIN without storing plaintext, for an authorized recovery CAS."""
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{8,32}", pin):
        raise ValueError("Use a new PIN containing 8 to 32 digits.")
    salt = random_secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 600000)
    b64 = lambda data: base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
    return "pbkdf2_sha256$600000$" + b64(salt) + "$" + b64(digest)


def _verify_reset_pin(pin: object, encoded: str) -> bool:
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{8,32}", pin):
        return False
    if not re.fullmatch(r"pbkdf2_sha256\$600000\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}", encoded):
        return False
    _, _, salt, expected = encoded.split("$")
    decoded = base64.urlsafe_b64decode(salt + "=" * (-len(salt) % 4))
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), decoded, 600000)
    supplied = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(supplied, expected)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _now(value=None) -> float:
    if value is None:
        value = time.time()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Authentication timestamps must include a timezone.")
        value = value.timestamp()
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ValueError("Authentication timestamp is invalid.")
    return float(value)


def _decode_totp_secret(value: object) -> bytes:
    if not isinstance(value, str):
        raise SecurityConfigurationError("Configure a valid server-side TOTP secret for each enabled user.")
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z2-7]{32,128}={0,6}", value):
        raise SecurityConfigurationError("Configure a valid server-side TOTP secret for each enabled user.")
    unpadded = value.rstrip("=")
    try:
        raw = base64.b32decode(unpadded + "=" * (-len(unpadded) % 8), casefold=False)
    except (ValueError, TypeError):
        raise SecurityConfigurationError("Configure a valid server-side TOTP secret for each enabled user.") from None
    if not 20 <= len(raw) <= 80:
        raise SecurityConfigurationError("Use at least 160 random bits for the TOTP secret.")
    return raw


def totp_code(secret: str, at=None) -> str:
    """RFC 6238 SHA-1 / 30-second / six-digit TOTP, for enrollment and tests."""
    counter = int(_now(at) // 30)
    digest = hmac.new(_decode_totp_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1000000:06d}"


def _matching_counter(secret: str, code: object, now: float) -> int | None:
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code.strip()):
        return None
    code = code.strip()
    counter = int(now // 30)
    matches = []
    for candidate in range(max(0, counter - 1), counter + 2):
        if hmac.compare_digest(totp_code(secret, candidate * 30), code):
            matches.append(candidate)
    return max(matches) if matches else None


def _expiry(value: object) -> float | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise SecurityConfigurationError("User expiry must be an ISO 8601 timestamp with timezone.")
    try:
        return _now(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        raise SecurityConfigurationError("User expiry must be an ISO 8601 timestamp with timezone.") from None


@dataclass(frozen=True)
class UserPolicy:
    user_id: str
    email: str
    role: str
    enabled: bool
    expires_at: float | None
    credential_version: str
    pin_sha256: str = field(repr=False)
    totp_secret: str = field(repr=False)

    def active(self, now: float) -> bool:
        return self.enabled and (self.expires_at is None or now < self.expires_at)


@dataclass(frozen=True)
class SecurityPolicy:
    users: Mapping[str, UserPolicy] = field(repr=False)
    fingerprint: str = field(repr=False)
    _signing_key: bytes = field(repr=False)
    idle_timeout_seconds: int = 900
    absolute_timeout_seconds: int = 28800
    owner_id: str = "owner"

    @property
    def owner(self) -> UserPolicy:
        return self.users[self.owner_id]


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    role: str
    authenticated_at: float
    last_active_at: float
    session_expires_at: float
    credential_version: str
    access_expires_at: float | None

    @property
    def can_access_finance(self) -> bool:
        return self.role == "owner"

    @property
    def mfa_verified_at(self) -> float:
        return self.authenticated_at


def _user(user_id: str, values: Mapping) -> UserPolicy:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", user_id):
        raise SecurityConfigurationError("Security user IDs must be short letters, numbers, underscores or hyphens.")
    email = values.get("email")
    if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()):
        raise SecurityConfigurationError("Configure an email address for each security identity.")
    role = values.get("role", "auditor")
    if not isinstance(role, str) or role not in {"owner", "auditor"}:
        raise SecurityConfigurationError("Security roles must be owner or auditor.")
    enabled = values.get("enabled", True)
    if type(enabled) is not bool:
        raise SecurityConfigurationError("Security enabled flags must be true or false.")
    version = values.get("credential_version", 1)
    if type(version) not in (str, int) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", str(version)):
        raise SecurityConfigurationError("Security credential versions must be nonempty identifiers.")
    pin = values.get("pin_sha256", "")
    secret = values.get("totp_secret", "")
    if enabled or pin:
        if (not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin.strip().lower())
                or pin.strip().lower() == _PUBLIC_FALLBACK_HASH):
            raise SecurityConfigurationError("Configure an explicit private PIN hash; the public fallback is not permitted.")
    if enabled or secret:
        _decode_totp_secret(secret)
    return UserPolicy(user_id, email.strip().lower(), role, enabled, _expiry(values.get("expires_at")),
                      str(version), str(pin).strip().lower(), str(secret).strip().upper().rstrip("="))


def load_policy(secrets: Mapping) -> SecurityPolicy:
    if not isinstance(secrets, Mapping) or not isinstance(secrets.get("security"), Mapping):
        raise SecurityConfigurationError("Configure the server-side security identity and MFA settings before login.")
    section = secrets["security"]
    if section.get("require_mfa", True) is not True:
        raise SecurityConfigurationError("MFA cannot be disabled for protected Terminal access.")
    if section.get("totp_enrollment_verified", True) is not True:
        raise SecurityConfigurationError("Finish authenticator enrollment verification before enabling login.")
    owner = _user("owner", {
        "email": section.get("owner_email"), "role": "owner",
        "enabled": section.get("owner_enabled", True),
        "expires_at": section.get("owner_expires_at"),
        "credential_version": section.get("credential_version", 1),
        "pin_sha256": section.get("login_pin_sha256", section.get("trade_access_code_sha256")),
        "totp_secret": section.get("totp_secret"),
    })
    users = {"owner": owner}
    additional = section.get("users", {})
    if not isinstance(additional, Mapping) or len(additional) > 100:
        raise SecurityConfigurationError("Security users must be a bounded server-side registry.")
    for identity, values in additional.items():
        if not isinstance(identity, str) or identity == "owner" or not isinstance(values, Mapping):
            raise SecurityConfigurationError("Security identity configuration is invalid.")
        users[identity] = _user(identity, values)
    idle = section.get("idle_timeout_seconds", 900)
    absolute = section.get("absolute_timeout_seconds", 28800)
    if (type(idle) is not int or not 60 <= idle <= 3600
            or type(absolute) is not int or not 300 <= absolute <= 86400 or absolute < idle):
        raise SecurityConfigurationError("Configure valid authentication idle and absolute timeouts.")
    canonical = _canonical({
        "version": 1, "idle": idle, "absolute": absolute,
        "users": {key: vars(value) for key, value in sorted(users.items())},
    })
    fingerprint = hashlib.sha256(canonical).hexdigest()
    # The fingerprint is public metadata. Never use it as the claim MAC key,
    # directly or through a public HMAC transform.
    signing_key = hashlib.sha256(b"raj-terminal:authentication-claims:v1\0" + canonical).digest()
    return SecurityPolicy(MappingProxyType(users), fingerprint, signing_key, idle, absolute)


class AuthService:
    def __init__(self, policy: SecurityPolicy, store):
        self.policy = policy
        if store is None or not callable(getattr(store, "read", None)) or not callable(getattr(store, "update", None)):
            raise SecurityStoreError("Durable authentication storage is required; access remains locked.")
        self.store = store

    @staticmethod
    def _identity_key(user_id: str) -> str:
        return identity_key(user_id)

    @staticmethod
    def _base_credential(user: UserPolicy) -> str:
        return hashlib.sha256(_canonical({"version": user.credential_version,
                                         "configured_pin_hash": user.pin_sha256})).hexdigest()

    def _override(self, state: dict, user: UserPolicy) -> dict | None:
        row = state["identities"].get(identity_key(user.user_id), {})
        override = row.get("pin_override")
        return override if isinstance(override, dict) and override.get("base_credential") == self._base_credential(user) else None

    def pin_revision_from_state(self, state: dict, user_id: str) -> str:
        user = self.policy.users[user_id]
        override = self._override(state, user)
        return "reset:" + override["revision"] if override else "config:" + self._base_credential(user)

    def set_pin_override_in_state(self, state: dict, user_id: str, encoded_hash: str,
                                  now=None, revision: str | None = None) -> str:
        """Call only inside an authorized, single-use recovery store.update callback."""
        if not re.fullmatch(r"pbkdf2_sha256\$600000\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}", encoded_hash):
            raise ValueError("Invalid encoded recovery PIN.")
        user = self.policy.users[user_id]
        now = _now(now)
        if not user.active(now):
            raise ValueError("The identity is not active.")
        revision = revision or random_secrets.token_hex(24)
        if not re.fullmatch(r"[0-9a-f]{32,64}", revision):
            raise ValueError("Invalid credential revision.")
        row = self._row(state, identity_key(user_id))
        row.update(failures=0, locked_until=0, pin_override={
            "base_credential": self._base_credential(user), "encoded_hash": encoded_hash,
            "revision": revision, "changed_at": now,
        })
        return "reset:" + revision

    @staticmethod
    def _counter_key(user: UserPolicy) -> str:
        return hashlib.sha256(("totp:v1:" + user.user_id + ":" + user.totp_secret).encode()).hexdigest()

    def _seal(self, payload: dict) -> dict:
        return {"payload": payload, "mac": hmac.new(self.policy._signing_key, _canonical(payload), hashlib.sha256).hexdigest()}

    def _unseal(self, value: object, kind: str) -> dict | None:
        try:
            if not isinstance(value, dict) or set(value) != {"payload", "mac"} or not isinstance(value["payload"], dict):
                return None
            payload = value["payload"]
            mac = hmac.new(self.policy._signing_key, _canonical(payload), hashlib.sha256).hexdigest()
            if not isinstance(value["mac"], str) or not hmac.compare_digest(mac, value["mac"]):
                return None
            if payload.get("kind") != kind or payload.get("policy") != self.policy.fingerprint:
                return None
            return payload
        except (TypeError, ValueError, OverflowError):
            return None

    def _pending(self, session: MutableMapping, now: float) -> tuple[dict, UserPolicy] | None:
        payload = self._unseal(session.get(PENDING_SESSION_KEY), "pending")
        if payload:
            user = self.policy.users.get(payload.get("user_id"))
            started = payload.get("started_at")
            if (user and user.active(now) and type(started) in (int, float)
                    and math.isfinite(started) and 0 <= now - started < FIRST_FACTOR_SECONDS):
                return payload, user
        session.pop(PENDING_SESSION_KEY, None)
        return None

    def pending_user_id(self, session: MutableMapping, now=None) -> str | None:
        pending = self._pending(session, _now(now))
        return pending[1].user_id if pending else None

    def clear_session(self, session: MutableMapping) -> None:
        session.pop(PENDING_SESSION_KEY, None)
        session.pop(AUTH_SESSION_KEY, None)

    @staticmethod
    def _row(state: dict, key: str) -> dict:
        return state["identities"].setdefault(key, {"failures": 0, "locked_until": 0})

    @staticmethod
    def _failure(row: dict, now: float) -> None:
        row["failures"] += 1
        if row["failures"] >= MAX_FAILURES:
            row["failures"] = 0
            row["locked_until"] = now + LOCKOUT_SECONDS

    def _begin(self, session: MutableMapping, user_id: object, pin: object, now: float,
               *, verified_passkey: bool) -> bool:
        self.clear_session(session)
        user = self.policy.users.get(user_id) if isinstance(user_id, str) else None
        rate_id = user.user_id if user else "__unknown_identity__"
        key = self._identity_key(rate_id)
        accepted = False
        revision = None

        def attempt(state):
            nonlocal accepted, revision
            accepted = False
            revision = None
            row = self._row(state, key)
            if row["locked_until"] > now:
                return
            if not user or not user.active(now):
                self._failure(row, now)
                return
            correct = verified_passkey
            if not verified_passkey:
                override = self._override(state, user)
                if override:
                    correct = _verify_reset_pin(pin, override["encoded_hash"])
                else:
                    candidate = hashlib.sha256(pin.encode("utf-8")).hexdigest() if isinstance(pin, str) else ""
                    correct = hmac.compare_digest(candidate, user.pin_sha256)
            if not correct:
                self._failure(row, now)
                return
            # Do not reset failures on a valid PIN: otherwise an attacker who
            # knows that PIN can reset the second-factor guessing budget.
            accepted = True
            revision = self.pin_revision_from_state(state, user.user_id)

        self.store.update(attempt)
        if accepted:
            session[PENDING_SESSION_KEY] = self._seal({
                "kind": "pending", "policy": self.policy.fingerprint,
                "user_id": user.user_id, "started_at": now,
                "credential_revision": revision,
                "nonce": random_secrets.token_urlsafe(24),
            })
        return accepted

    def begin_first_factor(self, session: MutableMapping, user_id: str, pin: str, now=None) -> bool:
        return self._begin(session, user_id, pin, _now(now), verified_passkey=False)

    def begin_verified_passkey(self, session: MutableMapping, user_id: str, now=None) -> bool:
        """Only call after cryptographic verification AND user-ID binding by the UI owner."""
        return self._begin(session, user_id, None, _now(now), verified_passkey=True)

    def verify_second_factor(self, session: MutableMapping, code: str, now=None) -> bool:
        now = _now(now)
        session.pop(AUTH_SESSION_KEY, None)
        pending = self._pending(session, now)
        if not pending:
            return False
        pending_payload, user = pending
        identity_key = self._identity_key(user.user_id)
        counter_key = self._counter_key(user)
        accepted_counter = None
        locked = False
        changed = False

        def attempt(state):
            nonlocal accepted_counter, locked, changed
            accepted_counter = None
            changed = False
            row = self._row(state, identity_key)
            locked = row["locked_until"] > now
            if locked:
                return
            if self.pin_revision_from_state(state, user.user_id) != pending_payload.get("credential_revision"):
                changed = True
                return
            matched = _matching_counter(user.totp_secret, code, now)
            previous = state["counters"].get(counter_key, -1)
            if matched is None or matched <= previous:
                self._failure(row, now)
                locked = row["locked_until"] > now
                return
            state["counters"][counter_key] = matched
            row.update(failures=0, locked_until=0)
            accepted_counter = matched

        try:
            self.store.update(attempt)
        except Exception:
            self.clear_session(session)
            raise
        if accepted_counter is None:
            if locked or changed:
                session.pop(PENDING_SESSION_KEY, None)
            return False
        session.pop(PENDING_SESSION_KEY, None)
        session[AUTH_SESSION_KEY] = self._seal({
            "kind": "authenticated", "policy": self.policy.fingerprint,
            "user_id": user.user_id, "authenticated_at": now, "last_active_at": now,
            "credential_revision": pending_payload["credential_revision"],
            "counter": accepted_counter, "nonce": random_secrets.token_urlsafe(24),
        })
        return True

    def authenticated(self, session: MutableMapping, now=None, *, touch: bool = False) -> Principal | None:
        now = _now(now)
        payload = self._unseal(session.get(AUTH_SESSION_KEY), "authenticated")
        if payload is None:
            session.pop(AUTH_SESSION_KEY, None)
            # The pre-render authentication gate is also evaluated between
            # the PIN and TOTP reruns. It must not erase a valid pending stage.
            self._pending(session, now)
            return None
        user = self.policy.users.get(payload.get("user_id")) if payload else None
        valid = bool(user and user.active(now))
        if valid:
            started, last = payload.get("authenticated_at"), payload.get("last_active_at")
            valid = (type(started) in (int, float) and type(last) in (int, float)
                     and math.isfinite(started) and math.isfinite(last)
                     and 0 <= now - started < self.policy.absolute_timeout_seconds
                     and started <= last <= now
                     and now - last < self.policy.idle_timeout_seconds
                     and type(payload.get("counter")) is int)
        if not valid:
            self.clear_session(session)
            return None
        try:
            state = self.store.read()
            row = state["identities"].get(self._identity_key(user.user_id))
            last_counter = state["counters"].get(self._counter_key(user), -1)
            if (not row or row["locked_until"] > now or last_counter < payload["counter"]
                    or self.pin_revision_from_state(state, user.user_id) != payload.get("credential_revision")):
                self.clear_session(session)
                return None
        except Exception:
            self.clear_session(session)
            raise
        if touch:
            payload = {**payload, "last_active_at": now}
            session[AUTH_SESSION_KEY] = self._seal(payload)
        expires = payload["authenticated_at"] + self.policy.absolute_timeout_seconds
        if user.expires_at is not None:
            expires = min(expires, user.expires_at)
        return Principal(user.user_id, user.email, user.role, payload["authenticated_at"],
                         payload["last_active_at"], expires, user.credential_version, user.expires_at)
