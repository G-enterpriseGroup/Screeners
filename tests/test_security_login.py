"""Offline integration coverage for the production login and financial boundary."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
import builtins
import unittest
from unittest.mock import Mock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from src import security_runtime
from src.terminal_security import (
    AUTH_SESSION_KEY, PENDING_SESSION_KEY, AuthService, SecurityConfigurationError,
    load_policy, totp_code,
)
from tests.test_terminal_security import MemoryStore, NOW, PIN, SECRET, settings


ROOT = Path(__file__).resolve().parents[1]


class SecurityLoginIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.config = settings()
        self.config["security"]["touch_id_memory_secret"] = "synthetic-browser-memory-secret-" * 2
        self.store = MemoryStore()
        self.auth = AuthService(load_policy(self.config), self.store)
        self.events = []
        self.keypad_props = []

    def keypad(self, **props):
        self.keypad_props.append(props)
        st.text("Keypad stage: " + str(props.get("authentication_stage")))
        return self.events.pop(0) if self.events else None

    def event(self, code="", action="code", nonce=1, credential=None):
        self.events.append({"action": action, "code": code, "nonce": nonce, "credential": credential})

    @contextmanager
    def production_app(self, *, service=None, memory_event=None, stop_at_broker=True):
        """Run the actual entrypoint with synthetic secrets and no broker/network I/O."""
        def broker_boundary(**kwargs):
            st.text("Verified owner reached broker boundary")
            if stop_at_broker:
                st.stop()

        with ExitStack() as stack:
            stack.enter_context(patch("streamlit.secrets", deepcopy(self.config)))
            stack.enter_context(patch("src.terminal_security.time.time", return_value=NOW))
            service_mock = stack.enter_context(patch("src.security_runtime.get_auth_service", return_value=service or self.auth))
            stack.enter_context(patch("src.reboot_guard.ensure_fresh_process"))
            stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("Network is forbidden in login tests")))
            keypad = stack.enter_context(patch("src.lock_screen_v2._lock_keypad", side_effect=self.keypad))
            stack.enter_context(patch("src.lock_screen_v2._touch_memory_bridge_event", return_value=memory_event or (None, True)))
            stack.enter_context(patch("src.lock_screen_v2._current_app_url", return_value="https://terminal.example.test"))
            stack.enter_context(patch("src.lock_screen_v2.webauthn_ready", return_value=True))
            restore = stack.enter_context(patch("src.session_persistence.restore_etrade_session", return_value=None))
            broker = stack.enter_context(patch("src.etrade_connection_ui_v2.render_compact_etrade_connection", side_effect=broker_boundary))
            networth = stack.enter_context(patch("src.networth_ui.render_networth"))
            client = stack.enter_context(patch("src.etrade_client.ETradeClient"))
            app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=15)
            yield app, service_mock, keypad, restore, broker, networth, client

    def assert_locked(self, app, restore, broker, networth):
        self.assertFalse(app.exception)
        self.assertFalse("etrade_access_unlocked" in app.session_state and app.session_state["etrade_access_unlocked"])
        self.assertNotIn(AUTH_SESSION_KEY, app.session_state)
        restore.assert_not_called()
        broker.assert_not_called()
        networth.assert_not_called()

    def test_legacy_boolean_cannot_bypass_server_mfa_or_restore_broker(self):
        with self.production_app() as (app, _, _, restore, broker, networth, client):
            app.session_state["etrade_access_unlocked"] = True
            app.session_state["etrade_access_token"] = {"oauth_token": "SYNTHETIC_LEGACY_TOKEN"}
            app.run()
            self.assert_locked(app, restore, broker, networth)
            self.assertNotIn("etrade_access_token", app.session_state)
            self.assertEqual(self.keypad_props[-1]["authentication_stage"], "pin")
            client.assert_not_called()

    def test_pin_transitions_to_totp_without_broker_restore_and_correct_totp_unlocks(self):
        with self.production_app() as (app, _, _, restore, broker, networth, _):
            self.event(PIN)
            app.run()
            self.assert_locked(app, restore, broker, networth)
            self.assertIn(PENDING_SESSION_KEY, app.session_state)
            self.assertEqual(self.keypad_props[-1]["authentication_stage"], "otp")
            self.event(totp_code(SECRET, NOW), nonce=2)
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state["etrade_access_unlocked"])
            self.assertIn(AUTH_SESSION_KEY, app.session_state)
            self.assertNotIn(PENDING_SESSION_KEY, app.session_state)
            restore.assert_called_once()
            broker.assert_called_once()
            networth.assert_not_called()
            self.assertTrue(any("Verified owner" in item.value for item in app.text))

    def test_incorrect_totp_keeps_pin_stage_pending_and_financial_data_locked(self):
        with self.production_app() as (app, _, _, restore, broker, networth, _):
            self.event(PIN)
            app.run()
            invalid = "000000" if totp_code(SECRET, NOW) != "000000" else "111111"
            self.event(invalid, nonce=2)
            app.run()
            self.assert_locked(app, restore, broker, networth)
            self.assertIn(PENDING_SESSION_KEY, app.session_state)
            self.assertEqual(self.keypad_props[-1]["authentication_stage"], "otp")
            self.assertIn("AUTHENTICATOR CODE NOT ACCEPTED", self.keypad_props[-1]["feedback"])

    def test_missing_mfa_configuration_is_fail_closed(self):
        with self.production_app() as (app, service, _, restore, broker, networth, _):
            service.side_effect = SecurityConfigurationError("Synthetic MFA setup incomplete")
            app.run()
            self.assert_locked(app, restore, broker, networth)
            self.assertFalse(self.keypad_props[-1]["access_enabled"])
            self.assertTrue(any("finish server-side MFA setup" in item.value for item in app.caption))

    def test_touch_id_authentication_creates_only_pending_second_factor(self):
        record = {"credential_id": "synthetic-public-id"}
        with self.production_app() as (app, _, _, restore, broker, networth, _), patch(
            "src.lock_screen_v2.build_authentication_options", return_value=({"challenge": "public"}, b"synthetic-challenge")
        ), patch("src.lock_screen_v2.complete_authentication", return_value=record) as verify, patch(
            "src.lock_screen_v2.seal_touch_id_record", return_value={"synthetic": "sealed-public-record"}
        ):
            app.session_state["_touchid_browser_record_v3"] = record
            self.event(action="authenticate", credential={"id": "synthetic-proof"})
            app.run()
            self.assert_locked(app, restore, broker, networth)
            verify.assert_called_once()
            self.assertIn(PENDING_SESSION_KEY, app.session_state)
            self.assertEqual(self.keypad_props[-1]["authentication_stage"], "otp")

    def test_touch_id_enrollment_finish_requires_fresh_pin_and_totp(self):
        with self.production_app() as (app, _, _, restore, broker, networth, _), patch(
            "src.lock_screen_v2.complete_registration"
        ) as finish:
            app.session_state["_terminal_enroll_after_mfa"] = True
            app.session_state["_touchid_registration_challenge"] = b"synthetic-challenge"
            self.event(action="finish_registration", credential={"id": "synthetic-proof"})
            app.run()
            self.assert_locked(app, restore, broker, networth)
            finish.assert_not_called()
            self.assertNotIn("_touchid_registration_challenge", app.session_state)
            self.assertIn("REQUIRES FRESH PIN + AUTHENTICATOR", self.keypad_props[-1]["feedback"])

    def test_browser_memory_saved_callback_cannot_unlock_without_mfa(self):
        event = ({"action": "saved", "command_id": "synthetic-save"}, True)
        with self.production_app(memory_event=event) as (app, _, _, restore, broker, networth, _), patch(
            "src.lock_screen_v2.seal_touch_id_record", return_value={"synthetic": "public"}
        ):
            app.session_state["_touchid_persist_then_unlock"] = True
            app.session_state["_touchid_browser_record_v3"] = {"credential_id": "synthetic"}
            app.run()
            self.assert_locked(app, restore, broker, networth)

    def test_auditor_with_valid_mfa_cannot_reach_broker_or_networth(self):
        self.config["security"]["users"] = {"reviewer": {
            "email": "reviewer@example.test", "role": "auditor",
            "pin_sha256": self.config["security"]["trade_access_code_sha256"], "totp_secret": SECRET}}
        auth = AuthService(load_policy(self.config), self.store)
        session = {}
        self.assertTrue(auth.begin_first_factor(session, "reviewer", PIN, NOW))
        self.assertTrue(auth.verify_second_factor(session, totp_code(SECRET, NOW), NOW))
        with self.production_app(service=auth) as (app, _, keypad, restore, broker, networth, client):
            for key, value in session.items():
                app.session_state[key] = value
            app.session_state["etrade_access_unlocked"] = True
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(item.value == "Terminal security review" for item in app.title))
            restore.assert_not_called()
            broker.assert_not_called()
            networth.assert_not_called()
            client.assert_not_called()
            keypad.assert_not_called()

    def test_public_policy_and_pin_reset_routes_stop_before_financial_core_loads(self):
        original_read = Path.read_text
        original_import = builtins.__import__
        def read(path, *args, **kwargs):
            if path.resolve() == (ROOT / "src" / "terminal_core.py").resolve():
                raise AssertionError("Public route must not load financial core")
            return original_read(path, *args, **kwargs)
        def guarded_import(name, *args, **kwargs):
            if name in {"src.terminal_core", "src.etrade_client", "src.networth_ui", "src.lock_screen_v2"}:
                raise AssertionError("Public route must not import financial owners")
            return original_import(name, *args, **kwargs)
        for query, expected in (({"policy": "privacy"}, "Privacy policy"),
                                ({"policy": "../../secrets"}, "Policy not found"),
                                ({"pin_reset": "synthetic-token"}, "Reset Terminal PIN")):
            with self.subTest(query=query), self.production_app() as (app, service, keypad, restore, broker, networth, client), patch.object(
                Path, "read_text", read
            ), patch("builtins.__import__", side_effect=guarded_import):
                app.query_params.update(query)
                app.run()
                self.assertFalse(app.exception)
                self.assertTrue(any(item.value == expected for item in app.title))
                service.assert_not_called()
                keypad.assert_not_called()
                restore.assert_not_called()
                broker.assert_not_called()
                networth.assert_not_called()
                client.assert_not_called()


class FragmentAccessBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.config = settings()
        self.auth = AuthService(load_policy(self.config), self.store)
        self.session = {"etrade_access_unlocked": True, "_networth_verified_balances": {"old": "synthetic"},
                        "etrade_access_token": "SYNTHETIC_PRIVATE_TOKEN"}
        self.assertTrue(self.auth.begin_first_factor(self.session, "owner", PIN, NOW))
        self.assertTrue(self.auth.verify_second_factor(self.session, totp_code(SECRET, NOW), NOW))

    def check_guard(self, service, now=NOW):
        with patch("src.security_runtime.get_auth_service", return_value=service), patch(
            "src.security_runtime.st.session_state", self.session
        ), patch("src.terminal_security.time.time", return_value=now):
            return security_runtime.require_finance_access(touch=False)

    def test_valid_polling_does_not_extend_idle_deadline(self):
        before = deepcopy(self.session[AUTH_SESSION_KEY])
        principal = self.check_guard(self.auth, NOW + 30)
        self.assertTrue(principal.can_access_finance)
        self.assertEqual(self.session[AUTH_SESSION_KEY], before)

    def test_expiry_revocation_and_unavailable_store_clear_all_financial_caches(self):
        for cause in ("expired", "disabled", "unavailable"):
            with self.subTest(cause=cause):
                self.setUp()
                service = self.auth
                now = NOW
                if cause == "expired":
                    now = NOW + self.auth.policy.idle_timeout_seconds
                elif cause == "disabled":
                    changed = deepcopy(self.config)
                    changed["security"]["owner_enabled"] = False
                    service = AuthService(load_policy(changed), self.store)
                else:
                    self.store.unavailable = True
                with patch("src.security_runtime.st.rerun", side_effect=RuntimeError("full app rerun")) as rerun:
                    with self.assertRaisesRegex(RuntimeError, "full app rerun"):
                        self.check_guard(service, now)
                self.assertEqual(self.session, {})
                rerun.assert_called_once_with(scope="app")

    def test_finance_fragment_wrapper_checks_guard_before_its_body(self):
        calls = []
        with patch("src.security_runtime.st.fragment", side_effect=lambda render, **kwargs: render):
            @security_runtime.finance_fragment(run_every="5s")
            def financial_body():
                calls.append("private data")
        with patch("src.security_runtime.require_finance_access", side_effect=RuntimeError("revoked")) as guard:
            with self.assertRaisesRegex(RuntimeError, "revoked"):
                financial_body()
        guard.assert_called_once_with()
        self.assertEqual(calls, [])

    def test_missing_context_cannot_return_from_a_denied_financial_guard(self):
        self.session.pop(AUTH_SESSION_KEY)
        with patch("src.security_runtime.st.rerun") as rerun, patch("src.security_runtime.st.stop") as stop:
            with self.assertRaises(PermissionError):
                self.check_guard(self.auth)
        rerun.assert_called_once_with(scope="app")
        stop.assert_called_once_with()
        self.assertEqual(self.session, {})

    def test_dialog_only_rerun_uses_real_guard_before_any_private_data_read(self):
        reads = []
        with patch("src.security_runtime.st.dialog", side_effect=lambda title, **options: lambda render: render):
            @security_runtime.finance_dialog("Synthetic private account dialog", width="large")
            def private_dialog():
                reads.append("private account data")
        self.session.pop(AUTH_SESSION_KEY)
        with patch("src.security_runtime.get_auth_service", return_value=self.auth), patch(
            "src.security_runtime.st.session_state", self.session
        ), patch("src.terminal_security.time.time", return_value=NOW), patch(
            "src.security_runtime.st.rerun"
        ) as rerun, patch("src.security_runtime.st.stop"):
            with self.assertRaises(PermissionError):
                private_dialog()
        self.assertEqual(reads, [])
        self.assertEqual(self.session, {})
        rerun.assert_called_once_with(scope="app")

    def test_direct_desktop_and_phone_broker_callbacks_reject_invalid_or_expired_mfa(self):
        from src import vertical_options_ui, verti_phone_ui
        for feature in (vertical_options_ui, verti_phone_ui):
            for cause in ("invalid", "expired"):
                with self.subTest(feature=feature.__name__, cause=cause):
                    self.setUp()
                    now = NOW + self.auth.policy.idle_timeout_seconds if cause == "expired" else NOW
                    if cause == "invalid":
                        self.session.pop(AUTH_SESSION_KEY)
                    broker = Mock(is_offline=False)
                    with patch("src.security_runtime.get_auth_service", return_value=self.auth), patch(
                        "src.security_runtime.st.session_state", self.session
                    ), patch("src.terminal_security.time.time", return_value=now), patch(
                        "src.security_runtime.st.rerun"
                    ) as rerun, patch("src.security_runtime.st.stop"):
                        with self.assertRaises(PermissionError):
                            feature._refresh_live_quotes(broker, "synthetic-component", "scope", "sort")
                    broker.get_quote.assert_not_called()
                    self.assertEqual(self.session, {})
                    rerun.assert_called_once_with(scope="app")

    def test_denied_presentation_and_risk_callbacks_cannot_mutate_shared_cached_state(self):
        from src import vertical_options_ui, verti_phone_ui, risk_sizing_ui_v2
        for feature, prefix in ((vertical_options_ui, "vo"), (verti_phone_ui, "vp"), (risk_sizing_ui_v2, "risk")):
            with self.subTest(feature=feature.__name__):
                self.setUp()
                self.session.pop(AUTH_SESSION_KEY)
                cached = {}
                widget = prefix + "_synthetic_preference"
                self.session.update({widget: True, "_" + prefix + "_memory": cached})
                with ExitStack() as stack:
                    stack.enter_context(patch("src.security_runtime.get_auth_service", return_value=self.auth))
                    stack.enter_context(patch("src.security_runtime.st.session_state", self.session))
                    stack.enter_context(patch("src.terminal_security.time.time", return_value=NOW))
                    rerun = stack.enter_context(patch("src.security_runtime.st.rerun"))
                    stack.enter_context(patch("src.security_runtime.st.stop"))
                    if prefix == "risk":
                        save = stack.enter_context(patch.object(feature, "_save_risk_intent_overrides"))
                        vault = stack.enter_context(patch.object(feature, "_risk_intent_vault", return_value=cached))
                        invoke = lambda: feature._set_long_term_override("synthetic-account", "SYNTHETIC", widget)
                    else:
                        invoke = lambda: feature._remember_presentation(widget)
                    with self.assertRaises(PermissionError):
                        invoke()
                    if prefix == "risk":
                        save.assert_not_called()
                        vault.assert_not_called()
                self.assertEqual(cached, {})
                self.assertEqual(self.session, {})
                rerun.assert_called_once_with(scope="app")


if __name__ == "__main__":
    unittest.main()
