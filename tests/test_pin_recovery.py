"""Recovery changes one PIN via a durable, expiring, single-use email token."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import ssl
import unittest
from unittest.mock import MagicMock, patch

from src.pin_recovery import RecoveryConfig, RecoveryConfigurationError, RecoveryService
from src.terminal_security import AuthService, load_policy, totp_code
from tests.test_terminal_security import MemoryStore, NEW_PIN, NOW, PIN, SECRET, settings
from tests.test_security_store import FakeGitHub


def config(**overrides):
    return RecoveryConfig(**{
        "smtp_host": "smtp.example.test", "smtp_port": 465,
        "smtp_username": "terminal@example.test", "smtp_password": "synthetic-test-password",
        "sender": "terminal@example.test", "public_base_url": "https://terminal.example.test",
        **overrides,
    })


class RecoveryConfigTests(unittest.TestCase):
    def test_disabled_missing_insecure_or_untrusted_configuration_is_rejected(self):
        for values in ({}, {"security": {"pin_recovery": {"enabled": False}}}):
            with self.assertRaises(RecoveryConfigurationError):
                RecoveryConfig.from_runtime(values)
        for key, value in (("smtp_port", 25), ("smtp_port", True), ("smtp_username", "bad\r\nBcc:x"),
                           ("sender", "bad\r\n@example.test"), ("public_base_url", "http://terminal.example.test"),
                           ("public_base_url", "https://other@terminal.example.test"),
                           ("public_base_url", "https://terminal.example.test/?redirect=x"),
                           ("public_base_url", "https://terminal.example.test/#fragment"),
                           ("public_base_url", "https://terminal.example.test:8080")):
            with self.subTest(key=key, value=value), self.assertRaises(RecoveryConfigurationError):
                config(**{key: value})

    def test_runtime_configuration_and_repr_hide_credentials(self):
        values = {"security": {"pin_recovery": {"enabled": True, **vars(config(smtp_username="private-smtp-login"))}}}
        loaded = RecoveryConfig.from_runtime(values)
        self.assertEqual(loaded.smtp_port, 465)
        self.assertNotIn(loaded.smtp_password, repr(loaded))
        self.assertNotIn(loaded.smtp_username, repr(loaded))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.auth = AuthService(load_policy(settings()), self.store)
        self.recovery = RecoveryService(self.auth, config())
        self.mail = []
        self.send_patch = patch.object(self.recovery, "_send", side_effect=lambda recipient, token: self.mail.append((recipient, token)))
        self.send_patch.start()
        self.addCleanup(self.send_patch.stop)

    def issue(self, now=NOW):
        self.assertIsNone(self.recovery.request("owner", now))
        return self.mail[-1][1]

    def test_recipient_is_registered_identity_and_only_digest_is_persisted(self):
        token = self.issue()
        self.assertEqual(self.mail[0][0], "owner@example.test")
        self.assertEqual(len(token), 43)
        state = json.dumps(self.store.state)
        self.assertNotIn(token, state)
        self.assertNotIn(PIN, state)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.assertIn(digest, self.store.state["recovery"]["tokens"])
        self.assertIsNone(self.recovery.request("attacker@example.test", NOW + 1))
        self.assertEqual(len(self.mail), 1)

    def test_account_throttling_is_durable_and_unknown_names_share_bucket(self):
        self.issue()
        self.recovery.request("owner", NOW + 59)
        self.assertEqual(len(self.mail), 1)
        second = RecoveryService(self.auth, config())
        with patch.object(second, "_send", side_effect=lambda recipient, token: self.mail.append((recipient, token))):
            for n in range(1, 6):
                second.request("owner", NOW + n * 60)
            self.assertEqual(len(self.mail), 5)
            second.request("owner", NOW + 3600)
            self.assertEqual(len(self.mail), 6)
        for n in range(30):
            self.recovery.request(f"unknown-{n}", NOW + 3601)
        self.assertEqual(len(self.store.state["recovery"]["requests"]), 2)

    def test_success_is_single_use_resets_pin_revokes_sessions_and_still_requires_mfa(self):
        session, pending = {}, {}
        self.auth.begin_first_factor(session, "owner", PIN, NOW)
        self.assertTrue(self.auth.verify_second_factor(session, totp_code(SECRET, NOW), NOW))
        self.auth.begin_first_factor(pending, "owner", PIN, NOW + 1)
        token = self.issue(NOW + 2)
        self.assertTrue(self.recovery.redeem(token, NEW_PIN, NOW + 3))
        self.assertFalse(self.recovery.redeem(token, NEW_PIN, NOW + 4))
        self.assertIsNone(self.auth.authenticated(session, NOW + 4))
        self.assertFalse(self.auth.verify_second_factor(pending, totp_code(SECRET, NOW), NOW + 4))
        self.assertFalse(self.auth.begin_first_factor({}, "owner", PIN, NOW + 4))
        reset_session = {}
        self.assertTrue(self.auth.begin_first_factor(reset_session, "owner", NEW_PIN, NOW + 4))
        self.assertIsNone(self.auth.authenticated(reset_session, NOW + 4))
        self.assertTrue(self.auth.verify_second_factor(reset_session, totp_code(SECRET, NOW + 30), NOW + 30))

    def test_concurrent_redemption_commits_only_one_pin_change(self):
        token = self.issue()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: self.recovery.redeem(token, NEW_PIN, NOW + 1), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])

    def test_expiry_policy_and_pin_revision_bind_tokens(self):
        token = self.issue()
        self.assertFalse(self.recovery.redeem(token, NEW_PIN, NOW - 1))
        self.assertFalse(self.recovery.redeem(token, NEW_PIN, NOW + 900))
        token = self.issue(NOW + 901)
        changed = settings()
        changed["security"]["credential_version"] = 2
        other = RecoveryService(AuthService(load_policy(changed), self.store), config())
        self.assertFalse(other.redeem(token, NEW_PIN, NOW + 902))
        second_token = self.issue(NOW + 962)
        self.assertTrue(self.recovery.redeem(second_token, NEW_PIN, NOW + 963))
        self.assertFalse(self.recovery.redeem(token, PIN, NOW + 964))

    def test_disabled_user_and_bad_tokens_never_reset(self):
        token = self.issue()
        changed = settings()
        changed["security"]["owner_enabled"] = False
        other = RecoveryService(AuthService(load_policy(changed), self.store), config())
        self.assertFalse(other.redeem(token, NEW_PIN, NOW + 1))
        for bad_token, pin in (("not-a-token", NEW_PIN), (token, "1234"), (token, "１２３４５６７８"), (token, None)):
            self.assertFalse(self.recovery.redeem(bad_token, pin, NOW + 1))
        self.assertNotIn("pin_override", str(self.store.state))

    def test_storage_outage_or_uncertain_commit_does_not_send_or_grant(self):
        self.store.unavailable = True
        self.assertIsNone(self.recovery.request("owner", NOW))
        self.assertFalse(self.mail)
        self.store.unavailable = False
        self.store.fail_after_commit = True
        self.assertIsNone(self.recovery.request("owner", NOW))
        self.assertFalse(self.mail)
        self.store.fail_after_commit = False
        token = self.issue(NOW + 61)
        self.store.fail_after_commit = True
        self.assertFalse(self.recovery.redeem(token, NEW_PIN, NOW + 62))

    def test_smtp_failure_invalidates_token_and_returns_generic_result(self):
        with patch.object(self.recovery, "_send", side_effect=RuntimeError("synthetic-private-value")):
            self.assertIsNone(self.recovery.request("owner", NOW))
        self.assertTrue(all(row["used"] for row in self.store.state["recovery"]["tokens"].values()))

    def test_recovery_schema_round_trips_through_real_encryption_and_cas(self):
        store = FakeGitHub()
        auth = AuthService(self.auth.policy, store)
        recovery = RecoveryService(auth, config())
        issued = []
        with patch.object(recovery, "_send", side_effect=lambda _, token: issued.append(token)):
            recovery.request("owner", NOW)
        self.assertEqual(len(issued), 1)
        self.assertTrue(recovery.redeem(issued[0], NEW_PIN, NOW + 1))
        fresh = AuthService(self.auth.policy, store)
        self.assertTrue(fresh.begin_first_factor({}, "owner", NEW_PIN, NOW + 2))
        ciphertext = store.commits[store.head].decode()
        self.assertNotIn(issued[0], ciphertext)
        self.assertNotIn(NEW_PIN, ciphertext)


class SMTPTests(unittest.TestCase):
    def test_tls_transport_fixed_recipient_and_url(self):
        auth = AuthService(load_policy(settings()), MemoryStore())
        token = "a" * 43
        for port in (465, 587):
            with self.subTest(port=port), patch("src.pin_recovery.smtplib.SMTP_SSL") as implicit, patch("src.pin_recovery.smtplib.SMTP") as starttls:
                smtp = MagicMock()
                selected = implicit if port == 465 else starttls
                selected.return_value.__enter__.return_value = smtp
                RecoveryService(auth, config(smtp_port=port))._send("owner@example.test", token)
                smtp.login.assert_called_once_with("terminal@example.test", "synthetic-test-password")
                message = smtp.send_message.call_args.args[0]
                self.assertEqual(message["To"], "owner@example.test")
                self.assertIn("https://terminal.example.test/?pin_reset=" + token, message.get_content())
                self.assertIn("authenticator code is still required", message.get_content())
                self.assertEqual(smtp.send_message.call_args.kwargs["to_addrs"], ["owner@example.test"])
                if port == 465:
                    self.assertIsInstance(implicit.call_args.kwargs["context"], ssl.SSLContext)
                    starttls.assert_not_called()
                else:
                    smtp.starttls.assert_called_once()
                    self.assertIsInstance(smtp.starttls.call_args.kwargs["context"], ssl.SSLContext)
                    self.assertEqual([call[0] for call in smtp.method_calls][:4], ["ehlo", "starttls", "ehlo", "login"])
                    implicit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
