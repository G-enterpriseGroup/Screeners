"""Offline Networth UI, durable link recovery and saved-tab migration checks."""

from copy import deepcopy
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import shutil
import subprocess
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.networth import PlaidConfig
from src.networth_store import NetworthError
from src.networth_ui import _complete_link, _start_link
from src.tab_bar_v4 import _clean_order
from scripts.sync_networth import main as worker_main


ROOT = Path(__file__).resolve().parents[1]


class Store:
    scope = "test-scope"

    def __init__(self):
        self.state = {"version": 1, "items": {}, "accounts": {}, "preferences": {}, "pending_links": {}}
        self.days = {}
        self.fail_next_save = False

    def read_state(self):
        return deepcopy(self.state)

    def update_state(self, callback):
        value = deepcopy(self.state)
        replacement = callback(value)
        if self.fail_next_save:
            self.fail_next_save = False
            raise NetworthError("Storage unavailable")
        self.state = replacement if replacement is not None else value
        return deepcopy(self.state)

    def read_day(self, day):
        return self.days.get(day)


class Plaid:
    config = PlaidConfig("FAKE_ID", "FAKE_SECRET")

    def __init__(self):
        self.calls = []
        self.fail_second = True

    def create_link(self, user_id, access_token=None, products=None):
        self.calls.append(("create", user_id, access_token, products))
        return {"link_token": "FAKE_LINK", "hosted_link_url": "https://secure.plaid.com/link/test",
                "expiration": "2099-01-01T00:00:00Z"}

    def link_results(self, token):
        return {"public_tokens": ["one", "two"], "completed": True, "items": []}

    def exchange(self, token):
        self.calls.append(("exchange", token))
        if token == "two" and self.fail_second:
            self.fail_second = False
            raise NetworthError("PLAID_CONNECTION_FAILED")
        return {"item_id": "item-" + token, "access_token": "PRIVATE-" + token}


class NetworthUITests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.plaid = Plaid()
        self.session = {}

    def test_no_configuration_has_clear_setup_without_exceptions(self):
        app = AppTest.from_string("from src.networth_ui import render_networth\nrender_networth()")
        with patch("src.networth_ui.PlaidConfig.from_runtime", return_value=None), patch(
            "src.networth_ui.StoreConfig.from_runtime", return_value=None
        ):
            app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("production credentials" in message.value for message in app.info))
        self.assertFalse(app.button)

    def test_link_persists_before_open_and_uses_selected_product(self):
        _start_link(self.store, self.plaid, products=("investments",))
        self.assertEqual(self.plaid.calls[0][-1], ("investments",))
        self.assertEqual(len(self.store.state["pending_links"]), 1)
        self.assertEqual(self.store.state["plaid_environment"], "production")

    def test_multi_item_partial_exchange_resumes_without_reusing_public_token(self):
        self.store.state["pending_links"]["link"] = {"link_token": "FAKE_LINK", "item_id": ""}
        pending = self.store.read_state()["pending_links"]["link"]
        with patch("src.networth_ui.st.session_state", self.session), patch("src.networth_ui.sync_networth"):
            with self.assertRaisesRegex(NetworthError, "PLAID_CONNECTION_FAILED"):
                _complete_link(self.store, self.plaid, "link", pending)
            self.assertIn("link", self.store.state["pending_links"])
            self.assertEqual(self.store.state["pending_links"]["link"]["processed_tokens"], ["one"])
            self.assertTrue(_complete_link(self.store, self.plaid, "link", pending))
        self.assertEqual(self.plaid.calls.count(("exchange", "one")), 1)
        self.assertEqual(set(self.store.state["items"]), {"item-one", "item-two"})
        self.assertFalse(self.store.state["pending_links"])

    def test_other_browser_cannot_exchange_claimed_link(self):
        self.store.state["pending_links"]["link"] = {
            "link_token": "FAKE_LINK", "processing_owner": "another-browser",
            "processing_until": "2099-01-01T00:00:00+00:00",
        }
        with patch("src.networth_ui.st.session_state", self.session):
            self.assertFalse(_complete_link(self.store, self.plaid, "link", self.store.state["pending_links"]["link"]))
        self.assertFalse(self.plaid.calls)

    def test_configured_ui_and_equity_edit_persist_across_reruns(self):
        self.store.state["items"]["i"] = {"label": "Test bank", "access_token": "PRIVATE"}
        self.store.state["accounts"]["a"] = {
            "account_id": "a", "item_id": "i", "name": "Checking", "mask": "5474",
            "type": "depository", "subtype": "checking", "current": 1500,
            "currency": "USD", "as_of": "2026-11-03T15:00:00Z",
        }
        app = AppTest.from_string("from src.networth_ui import render_networth\nrender_networth()")
        with patch("src.networth_ui.PlaidConfig.from_runtime", return_value=self.plaid.config), patch(
            "src.networth_ui.StoreConfig.from_runtime", return_value=object()
        ), patch("src.networth_ui.GitHubNetworthStore", return_value=self.store):
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(app.multiselect[0].value, ["a"])
            app.selectbox(key="networth_apply_classification").select("Equity")
            next(button for button in app.button if button.label == "SAVE CLASSIFICATIONS").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(self.store.state["preferences"]["a"]["classification"], "Equity")
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(self.store.state["preferences"]["a"]["classification"], "Equity")
            app.multiselect[0].set_value([]).run()
            self.assertEqual(self.store.state["selected_accounts"], [])
            restored = AppTest.from_string("from src.networth_ui import render_networth\nrender_networth()")
            restored.run()
            self.assertFalse(restored.exception)
            self.assertEqual(restored.multiselect[0].value, [])

    def test_saved_tab_order_migration_preserves_explicit_order(self):
        self.assertEqual(_clean_order(["HOLDINGS", "PERFORMANCE"])[:2], ["HOLDINGS", "NETWORTH"])
        self.assertEqual(_clean_order(["NETWORTH", "GEX", "HOLDINGS"])[:3], ["NETWORTH", "GEX", "HOLDINGS"])

    @unittest.skipUnless(shutil.which("node") or Path("/opt/homebrew/bin/node").exists(), "Node required")
    def test_browser_saved_tab_order_migration(self):
        source = (ROOT / "src/components/terminal_tabs_v3/index.html").read_text()
        function = source[source.index("function cleanOrder("):source.index("function persist()")]
        program = function + '''
const allowed = ["HOLDINGS","NETWORTH","PERFORMANCE","ACQUISITION","GEX"];
const migrated = cleanOrder(["HOLDINGS","GEX","PERFORMANCE"], allowed);
if (migrated.join() !== "HOLDINGS,NETWORTH,GEX,PERFORMANCE,ACQUISITION") throw Error(migrated);
const explicit = cleanOrder(["GEX","NETWORTH","HOLDINGS"], allowed);
if (explicit.slice(0,3).join() !== "GEX,NETWORTH,HOLDINGS") throw Error(explicit);
'''
        subprocess.run([shutil.which("node") or "/opt/homebrew/bin/node"], input=program, text=True, check=True, capture_output=True)


class NetworthWorkerTests(unittest.TestCase):
    def test_manual_worker_does_not_claim_a_scheduled_slot(self):
        store = Store()
        store.state["items"]["i"] = {"access_token": "PRIVATE_TOKEN"}
        with patch.dict("os.environ", {"NETWORTH_TRIGGER": "workflow_dispatch"}), patch(
            "scripts.sync_networth.PlaidConfig.from_runtime", return_value=Plaid.config
        ), patch("scripts.sync_networth.StoreConfig.from_runtime", return_value=object()), patch(
            "scripts.sync_networth.GitHubNetworthStore", return_value=store
        ), patch("scripts.sync_networth.sync_networth", return_value=store.state) as sync, redirect_stdout(StringIO()):
            self.assertEqual(worker_main(), 0)
        self.assertIsNone(sync.call_args.kwargs["scheduled_slot"])

    def test_missing_configuration_fails_with_safe_actionable_message(self):
        output = StringIO()
        with patch("scripts.sync_networth.PlaidConfig.from_runtime", return_value=None), patch(
            "scripts.sync_networth.StoreConfig.from_runtime", return_value=None
        ), redirect_stderr(output):
            self.assertEqual(worker_main(), 1)
        self.assertIn("Actions secrets", output.getvalue())

    def test_unlinked_worker_does_not_write_empty_snapshots(self):
        output = StringIO()
        with patch("scripts.sync_networth.PlaidConfig.from_runtime", return_value=Plaid.config), patch(
            "scripts.sync_networth.StoreConfig.from_runtime", return_value=object()
        ), patch("scripts.sync_networth.scheduled_slot", return_value="DUE"), patch(
            "scripts.sync_networth.GitHubNetworthStore", return_value=Store()
        ), patch("scripts.sync_networth.sync_networth") as sync, redirect_stdout(output):
            self.assertEqual(worker_main(), 0)
        sync.assert_not_called()

    def test_incomplete_worker_fails_without_logging_financial_payload(self):
        store = Store()
        store.state["items"]["i"] = {"access_token": "PRIVATE_TOKEN"}
        output = StringIO()
        with patch("scripts.sync_networth.PlaidConfig.from_runtime", return_value=Plaid.config), patch(
            "scripts.sync_networth.StoreConfig.from_runtime", return_value=object()
        ), patch("scripts.sync_networth.scheduled_slot", return_value="DUE"), patch(
            "scripts.sync_networth.GitHubNetworthStore", return_value=store
        ), patch("scripts.sync_networth.sync_networth", return_value={
            "items": store.state["items"], "last_errors": {"i": "PRIVATE_ERROR"}
        }), redirect_stdout(output):
            self.assertEqual(worker_main(), 1)
        self.assertNotIn("PRIVATE", output.getvalue())

    def test_manual_worker_missing_balance_is_incomplete(self):
        store = Store()
        store.state["items"]["i"] = {"access_token": "PRIVATE_TOKEN"}
        with patch.dict("os.environ", {"NETWORTH_TRIGGER": "workflow_dispatch"}), patch(
            "scripts.sync_networth.PlaidConfig.from_runtime", return_value=Plaid.config
        ), patch("scripts.sync_networth.StoreConfig.from_runtime", return_value=object()), patch(
            "scripts.sync_networth.GitHubNetworthStore", return_value=store
        ), patch("scripts.sync_networth.sync_networth", return_value={"last_capture_complete": False}), redirect_stdout(StringIO()):
            self.assertEqual(worker_main(), 1)


if __name__ == "__main__":
    unittest.main()
