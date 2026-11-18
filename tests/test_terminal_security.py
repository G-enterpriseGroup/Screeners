"""Focused MFA boundary, durable replay, revocation and enrollment regressions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.security_store import SecurityStoreError, initial_state
from src.terminal_security import (
    AUTH_SESSION_KEY, PENDING_SESSION_KEY, AuthService, SecurityConfigurationError,
    encode_reset_pin, identity_key, load_policy, totp_code,
)


PIN = "86420864"
NEW_PIN = "97531975"
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # Public RFC test vector, not a credential.
NOW = 1800000000.0


def settings():
    return {"security": {"owner_email": "owner@example.test", "totp_secret": SECRET,
                         "trade_access_code_sha256": hashlib.sha256(PIN.encode()).hexdigest()}}


class MemoryStore:
    """Atomic durable-store contract shared by separate service instances."""
    def __init__(self):
        self.state = initial_state()
        self.lock = threading.Lock()
        self.unavailable = False
        self.fail_after_commit = False

    def read(self):
        if self.unavailable:
            raise SecurityStoreError("Synthetic storage unavailable")
        with self.lock:
            return deepcopy(self.state)

    def update(self, callback):
        if self.unavailable:
            raise SecurityStoreError("Synthetic storage unavailable")
        with self.lock:
            state = deepcopy(self.state)
            replacement = callback(state)
            if replacement is not None:
                state = replacement
            self.state = state
            if self.fail_after_commit:
                raise SecurityStoreError("Synthetic commit verification failed")
            return deepcopy(state)


class PolicyTests(unittest.TestCase):
    def test_required_configuration_and_no_public_fallback(self):
        invalid = [{}, {"security": {}}, {"security": {**settings()["security"], "require_mfa": False}},
                   {"security": {**settings()["security"], "totp_enrollment_verified": False}}]
        invalid.append({"security": {**settings()["security"], "trade_access_code_sha256":
                                     "d740238f374425d95f71dcd05dd1486800f0887790b992b74ef41fbb5c8e1167"}})
        for value in invalid:
            with self.subTest(value=list(value)):
                with self.assertRaises(SecurityConfigurationError):
                    load_policy(value)

    def test_bad_secret_role_expiry_and_configuration_types_fail_closed(self):
        for field, value in [("totp_secret", "too-short"), ("owner_expires_at", "2027-01-01"),
                             ("owner_enabled", "yes"), ("idle_timeout_seconds", True),
                             ("credential_version", {}), ("owner_email", "not-an-email")]:
            config = settings()
            config["security"][field] = value
            with self.subTest(field=field), self.assertRaises(SecurityConfigurationError):
                load_policy(config)
        config = settings()
        config["security"]["users"] = {"review": {"email": "review@example.test", "role": [],
                                                   "pin_sha256": "a" * 64, "totp_secret": SECRET}}
        with self.assertRaises(SecurityConfigurationError):
            load_policy(config)

    def test_no_secret_repr_and_immutable_registry(self):
        policy = load_policy(settings())
        self.assertNotIn(SECRET, repr(policy))
        self.assertNotIn(SECRET, repr(policy.owner))
        with self.assertRaises(TypeError):
            policy.users["other"] = policy.owner
        changed = settings()
        changed["security"]["credential_version"] = 2
        self.assertNotEqual(policy.fingerprint, load_policy(changed).fingerprint)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.policy = load_policy(settings())
        self.auth = AuthService(self.policy, self.store)

    def login(self, *, session=None, at=NOW, pin=PIN, user="owner", auth=None):
        session = {} if session is None else session
        auth = auth or self.auth
        self.assertTrue(auth.begin_first_factor(session, user, pin, at))
        self.assertTrue(auth.verify_second_factor(session, totp_code(SECRET, at), at))
        return session

    def test_rfc6238_reference_vectors(self):
        for at, expected in [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
                             (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")]:
            with self.subTest(at=at):
                self.assertEqual(totp_code(SECRET, at), expected)

    def test_pin_only_never_authenticates_and_pre_render_preserves_stage(self):
        session = {"etrade_access_unlocked": True}
        self.assertIsNone(self.auth.authenticated(session, NOW))
        self.assertTrue(self.auth.begin_first_factor(session, "owner", PIN, NOW))
        self.assertNotIn(AUTH_SESSION_KEY, session)
        self.assertIsNone(self.auth.authenticated(session, NOW + 1))
        self.assertEqual(self.auth.pending_user_id(session, NOW + 1), "owner")
        self.assertFalse(self.auth.verify_second_factor(session, "000000", NOW))
        self.assertNotIn(AUTH_SESSION_KEY, session)
        self.assertTrue(self.auth.verify_second_factor(session, totp_code(SECRET, NOW), NOW))
        principal = self.auth.authenticated(session, NOW)
        self.assertEqual((principal.user_id, principal.role), ("owner", "owner"))
        self.assertTrue(principal.can_access_finance)
        self.assertEqual(principal.mfa_verified_at, NOW)
        self.assertNotIn(SECRET, str(session))
        self.assertNotIn(PIN, str(session))

    def test_passkey_first_factor_still_requires_totp(self):
        session = {}
        self.assertTrue(self.auth.begin_verified_passkey(session, "owner", NOW))
        self.assertIsNone(self.auth.authenticated(session, NOW))
        self.assertTrue(self.auth.verify_second_factor(session, totp_code(SECRET, NOW), NOW))

    def test_separate_login_pin_preserves_legacy_vault_hash_and_overrides_only_login(self):
        config = settings()
        legacy_hash = config["security"]["trade_access_code_sha256"]
        config["security"]["login_pin_sha256"] = hashlib.sha256(NEW_PIN.encode()).hexdigest()
        service = AuthService(load_policy(config), self.store)
        self.assertFalse(service.begin_first_factor({}, "owner", PIN, NOW))
        session = {}
        self.assertTrue(service.begin_first_factor(session, "owner", NEW_PIN, NOW))
        self.assertIsNone(service.authenticated(session, NOW))
        self.assertTrue(service.verify_second_factor(session, totp_code(SECRET, NOW), NOW))
        self.assertEqual(config["security"]["trade_access_code_sha256"], legacy_hash)
        config["security"]["login_pin_sha256"] = ""
        with self.assertRaises(SecurityConfigurationError):
            load_policy(config)

    def test_stage_expiry_bad_otp_and_clock_validation(self):
        session = {}
        self.auth.begin_first_factor(session, "owner", PIN, NOW)
        self.assertFalse(self.auth.verify_second_factor(session, totp_code(SECRET, NOW + 300), NOW + 300))
        self.assertNotIn(PENDING_SESSION_KEY, session)
        for value in ("１２３４５６", "12345", "1234567", 123456, None):
            self.auth.begin_first_factor(session, "owner", PIN, NOW + 330)
            self.assertFalse(self.auth.verify_second_factor(session, value, NOW + 330))
        with self.assertRaises(ValueError):
            self.auth.begin_first_factor({}, "owner", PIN, datetime(2026, 1, 1))

    def test_bounded_clock_window_and_same_counter_after_restart(self):
        session = {}
        self.auth.begin_first_factor(session, "owner", PIN, NOW)
        self.assertFalse(self.auth.verify_second_factor(session, totp_code(SECRET, NOW + 60), NOW))
        self.assertTrue(self.auth.verify_second_factor(session, totp_code(SECRET, NOW + 30), NOW))
        restarted = AuthService(load_policy(settings()), self.store)
        other = {}
        self.assertTrue(restarted.begin_first_factor(other, "owner", PIN, NOW + 1))
        self.assertFalse(restarted.verify_second_factor(other, totp_code(SECRET, NOW + 30), NOW + 1))
        self.assertIsNone(restarted.authenticated(other, NOW + 1))

    def test_concurrent_duplicate_consumption_grants_only_one_session(self):
        sessions = [{}, {}]
        for session in sessions:
            self.auth.begin_first_factor(session, "owner", PIN, NOW)
        def attempt(session):
            return AuthService(self.policy, self.store).verify_second_factor(session, totp_code(SECRET, NOW), NOW)
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, sessions))
        self.assertEqual(sorted(result), [False, True])

    def test_account_wide_failures_survive_sessions_and_pin_success(self):
        for _ in range(5):
            self.assertFalse(AuthService(self.policy, self.store).begin_first_factor({}, "owner", "bad", NOW))
        self.assertFalse(self.auth.begin_first_factor({}, "owner", PIN, NOW + 1))
        session = {}
        self.assertTrue(self.auth.begin_first_factor(session, "owner", PIN, NOW + 301))
        for n in range(5):
            self.assertFalse(self.auth.verify_second_factor(session, "bad", NOW + 301))
            if n < 4:
                self.assertTrue(self.auth.begin_first_factor(session, "owner", PIN, NOW + 301))
        self.assertFalse(self.auth.begin_verified_passkey({}, "owner", NOW + 302))

    def test_unknown_user_attempts_use_one_bounded_bucket(self):
        for n in range(50):
            self.assertFalse(self.auth.begin_first_factor({}, f"unknown{n}", PIN, NOW))
        self.assertEqual(len(self.store.state["identities"]), 1)

    def test_unavailable_storage_and_uncertain_commits_never_grant_access(self):
        session = self.login()
        self.store.unavailable = True
        with self.assertRaises(SecurityStoreError):
            self.auth.authenticated(session, NOW + 1)
        self.assertNotIn(AUTH_SESSION_KEY, session)
        with self.assertRaises(SecurityStoreError):
            self.auth.begin_first_factor(session, "owner", PIN, NOW + 1)
        self.store.unavailable = False
        self.auth.begin_first_factor(session, "owner", PIN, NOW + 30)
        self.store.fail_after_commit = True
        with self.assertRaises(SecurityStoreError):
            self.auth.verify_second_factor(session, totp_code(SECRET, NOW + 30), NOW + 30)
        self.assertNotIn(AUTH_SESSION_KEY, session)
        self.assertNotIn(PENDING_SESSION_KEY, session)

    def test_idle_absolute_expiry_and_polls_do_not_extend_idle(self):
        session = self.login()
        self.assertIsNotNone(self.auth.authenticated(session, NOW + 899))
        self.assertIsNone(self.auth.authenticated(session, NOW + 900))
        session = self.login(at=NOW + 930)
        self.assertIsNotNone(self.auth.authenticated(session, NOW + 1200, touch=True))
        self.assertIsNotNone(self.auth.authenticated(session, NOW + 2000))
        config = settings()
        config["security"].update(idle_timeout_seconds=60, absolute_timeout_seconds=300)
        auth = AuthService(load_policy(config), self.store)
        session = self.login(at=NOW + 2100, auth=auth)
        for offset in (50, 100, 150, 200, 250, 299):
            self.assertIsNotNone(auth.authenticated(session, NOW + 2100 + offset, touch=True))
        self.assertIsNone(auth.authenticated(session, NOW + 2400, touch=True))

    def test_identity_revocation_configuration_rotation_and_claim_tampering(self):
        session = self.login()
        changed = settings()
        changed["security"]["owner_enabled"] = False
        self.assertIsNone(AuthService(load_policy(changed), self.store).authenticated(deepcopy(session), NOW + 1))
        changed["security"].pop("owner_enabled")
        changed["security"]["credential_version"] = 2
        self.assertIsNone(AuthService(load_policy(changed), self.store).authenticated(deepcopy(session), NOW + 1))
        bad = deepcopy(session)
        bad[AUTH_SESSION_KEY]["payload"]["authenticated_at"] = NOW + 100
        self.assertIsNone(self.auth.authenticated(bad, NOW))

    def test_public_policy_fingerprint_cannot_sign_or_extend_access_claims(self):
        session = self.login()
        claim = session[AUTH_SESSION_KEY]
        claim["payload"].update(authenticated_at=NOW + 1000, last_active_at=NOW + 1000)
        # The known policy identifier and public domain must not suffice to
        # forge the server's independently derived authentication signature.
        public_key = hmac.new(bytes.fromhex(self.policy.fingerprint),
                              b"raj-terminal:authentication-claims:v1", hashlib.sha256).digest()
        payload = json.dumps(claim["payload"], sort_keys=True, separators=(",", ":")).encode()
        claim["mac"] = hmac.new(public_key, payload, hashlib.sha256).hexdigest()
        self.assertIsNone(self.auth.authenticated(session, NOW + 1001))

    def test_auditor_role_and_expiration(self):
        config = settings()
        config["security"]["users"] = {"review": {"email": "review@example.test", "role": "auditor",
                                                   "pin_sha256": hashlib.sha256(PIN.encode()).hexdigest(),
                                                   "totp_secret": SECRET,
                                                   "expires_at": datetime.fromtimestamp(NOW + 60, timezone.utc).isoformat()}}
        auth = AuthService(load_policy(config), self.store)
        session = self.login(user="review", auth=auth)
        self.assertFalse(auth.authenticated(session, NOW).can_access_finance)
        self.assertIsNone(auth.authenticated(session, NOW + 60))

    def test_pin_reset_revokes_old_sessions_stages_and_preserves_mfa(self):
        session = self.login()
        pending = {}
        self.auth.begin_first_factor(pending, "owner", PIN, NOW + 1)
        encoded = encode_reset_pin(NEW_PIN)
        self.assertNotIn(NEW_PIN, encoded)
        def reset(state):
            self.auth.set_pin_override_in_state(state, "owner", encoded, NOW + 2)
        self.store.update(reset)
        self.assertIsNone(self.auth.authenticated(session, NOW + 3))
        self.assertFalse(self.auth.verify_second_factor(pending, totp_code(SECRET, NOW), NOW + 3))
        self.assertFalse(self.auth.begin_first_factor({}, "owner", PIN, NOW + 4))
        reset_session = {}
        self.assertTrue(self.auth.begin_first_factor(reset_session, "owner", NEW_PIN, NOW + 4))
        self.assertFalse(self.auth.verify_second_factor(reset_session, totp_code(SECRET, NOW), NOW + 4))
        self.assertTrue(self.auth.verify_second_factor(reset_session, totp_code(SECRET, NOW + 30), NOW + 30))
        changed = settings()
        changed["security"]["credential_version"] = 2
        rotated = AuthService(load_policy(changed), self.store)
        self.assertFalse(rotated.begin_first_factor({}, "owner", NEW_PIN, NOW + 31))
        self.assertTrue(rotated.begin_first_factor({}, "owner", PIN, NOW + 31))

    def test_reset_pins_use_distinct_salts_and_enforce_minimum(self):
        self.assertNotEqual(encode_reset_pin(NEW_PIN), encode_reset_pin(NEW_PIN))
        for value in ("123", "1234567", "a" * 8, "１" * 8):
            with self.assertRaises(ValueError):
                encode_reset_pin(value)


class EnrollmentTests(unittest.TestCase):
    def test_user_run_enrollment_is_private_local_and_verified(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "setup_terminal_mfa.py"
        spec = importlib.util.spec_from_file_location("test_mfa_enrollment_helper", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "private.toml"
            stream = io.StringIO()
            with patch.object(module.getpass, "getpass", side_effect=[PIN, PIN, totp_code(SECRET, NOW)]), \
                    patch.object(module.secrets, "token_bytes", return_value=b"12345678901234567890"), \
                    patch.object(module.time, "time", return_value=NOW), patch("sys.stdout", stream):
                self.assertEqual(module.main(["--email", "owner@example.test", "--output", str(output)]), 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertIn("totp_enrollment_verified = true", output.read_text())
            self.assertIn("login_pin_sha256 = ", output.read_text())
            self.assertNotIn("trade_access_code_sha256", output.read_text())
            self.assertNotIn(SECRET, stream.getvalue())
            self.assertNotIn(PIN, stream.getvalue())

    def test_optional_offline_qr_is_private_and_contains_expected_totp_uri(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "setup_terminal_mfa.py"
        spec = importlib.util.spec_from_file_location("test_mfa_qr_helper", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        factory = object()
        def make(uri, **kwargs):
            calls.append((uri, kwargs))
            return SimpleNamespace(to_string=lambda **_: b"<svg>synthetic QR test</svg>")
        with tempfile.TemporaryDirectory() as directory, patch.dict("sys.modules", {
            "qrcode": SimpleNamespace(make=make), "qrcode.image": SimpleNamespace(),
            "qrcode.image.svg": SimpleNamespace(SvgPathFillImage=factory),
        }):
            output = Path(directory) / "private.qr.svg"
            self.assertTrue(module._write_private_qr(output, "owner@example.test", SECRET))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertIn("otpauth://totp/", calls[0][0])
            self.assertIn("secret=" + SECRET, calls[0][0])
            self.assertEqual(calls[0][1], {"image_factory": factory, "border": 4})
            with self.assertRaises(FileExistsError):
                module._write_private_qr(output, "owner@example.test", SECRET)


if __name__ == "__main__":
    unittest.main()
