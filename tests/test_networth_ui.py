"""Offline Networth UI, durable link recovery and saved-tab migration checks."""

from copy import deepcopy
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
from html.parser import HTMLParser
from io import StringIO
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src import networth_ui
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

    def test_nickname_save_reopen_and_clear_preserve_classification_and_saved_history(self):
        self.store.state["items"]["i"] = {"label": "Synthetic Bank", "access_token": "PRIVATE"}
        self.store.state["accounts"]["a"] = {
            **statement_account("a", "Synthetic Checking", 1200, "Asset", mask="5474"),
            "item_id": "i", "type": "depository", "subtype": "checking",
            "as_of": "2026-11-03T15:00:00Z",
        }
        self.store.state["preferences"]["a"] = {"classification": "Equity", "custom": "preserved"}
        self.store.days["2026-11-03"] = {"captures": {"first": {
            "captured_at": "2026-11-03T15:00:00Z", "complete": True,
            "accounts": [deepcopy(self.store.state["accounts"]["a"])],
        }}}
        before_history = deepcopy(self.store.days)
        app = AppTest.from_string("from src.networth_ui import render_networth\nrender_networth()")
        with patch("src.networth_ui.PlaidConfig.from_runtime", return_value=self.plaid.config), patch(
            "src.networth_ui.StoreConfig.from_runtime", return_value=object()
        ), patch("src.networth_ui.GitHubNetworthStore", return_value=self.store), patch(
            "src.networth_ui.sync_networth"
        ) as sync:
            app.run()
            self.assertFalse(app.exception)
            app.text_input(key="networth_nickname_a").input("Household Cash")
            next(button for button in app.button if button.label == "SAVE NICKNAME").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(self.store.state["preferences"]["a"]["nickname"], "Household Cash")
            self.assertEqual(self.store.state["preferences"]["a"]["classification"], "Equity")
            self.assertEqual(self.store.state["preferences"]["a"]["custom"], "preserved")
            restored = AppTest.from_string("from src.networth_ui import render_networth\nrender_networth()")
            restored.run()
            self.assertFalse(restored.exception)
            self.assertEqual(restored.text_input(key="networth_nickname_a").value, "Household Cash")
            self.assertTrue(any(expander.label == "Accounts & connections" for expander in restored.expander))
            restored.text_input(key="networth_nickname_a").input("")
            next(button for button in restored.button if button.label == "SAVE NICKNAME").click().run()
            self.assertFalse(restored.exception)
            self.assertNotIn("nickname", self.store.state["preferences"]["a"])
            self.assertEqual(self.store.state["preferences"]["a"]["classification"], "Equity")
            sync.assert_not_called()
        self.assertEqual(self.store.days, before_history)

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


def statement_account(account_id, name, amount, classification, currency="USD", mask=""):
    return {"account_id": account_id, "name": name, "current": amount,
            "classification": classification, "currency": currency, "mask": mask}


class NetworthStatementTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [
            statement_account("cash", "Synthetic Checking", 1200, "Asset", mask="5474"),
            statement_account("broker", "Synthetic Brokerage", 800, "Asset"),
            statement_account("card", "Synthetic Card", 500, "Liability"),
            statement_account("credit", "Synthetic Credit", -25, "Liability"),
        ]

    @staticmethod
    def row(rows, label):
        return next(row for row in rows if row["label"] == label)

    def test_nickname_is_trimmed_and_blank_uses_original_name_and_mask(self):
        account = self.accounts[0]
        preferences = {"cash": {"nickname": "  Household Cash  "}}
        self.assertEqual(networth_ui._account_label(account, preferences), "Household Cash")
        preferences["cash"]["nickname"] = "   "
        fallback = networth_ui._account_label(account, preferences)
        self.assertIn("Synthetic Checking", fallback)
        self.assertIn("5474", fallback)
        self.assertEqual(account["name"], "Synthetic Checking")

    def test_statement_balances_negative_liability_rows_and_asset_percentages(self):
        rows = networth_ui._statement_rows(self.accounts, {"cash": {"nickname": "Household Cash"}}, "USD")
        self.assertEqual(self.row(rows, "Assets:")["kind"], "section")
        self.assertEqual(self.row(rows, "Liabilities:")["kind"], "section")
        cash = next(row for row in rows if row.get("account_id") == "cash")
        card = next(row for row in rows if row.get("account_id") == "card")
        credit = next(row for row in rows if row.get("account_id") == "credit")
        self.assertEqual(cash["label"], "Household Cash")
        self.assertEqual((cash["amount"], cash["share"]), (1200, 60))
        self.assertEqual((card["amount"], card["share"]), (-500, -25))
        self.assertEqual((credit["amount"], credit["share"]), (25, 1.25))
        self.assertEqual(self.row(rows, "Total Assets:")["amount"], 2000)
        self.assertEqual(self.row(rows, "Total Liabilities:")["amount"], 475)
        self.assertEqual(self.row(rows, "Equity:")["amount"], 1525)
        grand_total = self.row(rows, "Total Liabilities & Equity:")
        self.assertEqual(grand_total["amount"], 2000)
        self.assertEqual(grand_total["share"], 100)
        self.assertEqual(grand_total["kind"], "grand_total")

    def test_explicit_equity_is_reconciled_without_double_counting_assets(self):
        accounts = self.accounts + [statement_account("owner", "Synthetic Capital", 1000, "Equity")]
        rows = networth_ui._statement_rows(accounts, {"owner": {"nickname": "Owner Capital"}}, "USD")
        explicit = next(row for row in rows if row.get("account_id") == "owner")
        self.assertEqual(explicit["label"], "Owner Capital")
        self.assertEqual(explicit["amount"], 1000)
        self.assertEqual(self.row(rows, "Equity:")["kind"], "section")
        self.assertEqual(self.row(rows, "Unallocated equity")["amount"], 525)
        self.assertEqual(self.row(rows, "Total Equity:")["amount"], 1525)
        self.assertEqual(self.row(rows, "Total Liabilities & Equity:")["amount"], 2000)
        self.assertEqual(self.row(rows, "Total Assets:")["amount"], 2000)

    def test_currency_isolation_and_excluded_unclassified_accounts_are_not_summed(self):
        accounts = self.accounts + [
            statement_account("foreign", "Synthetic Foreign", 9000, "Asset", "CAD"),
            statement_account("omitted", "Synthetic Excluded", 5000, "Excluded"),
            statement_account("unknown", "Synthetic Unclassified", 6000, "Unclassified"),
            statement_account("no-currency", "Synthetic Unknown Currency", 7000, "Asset", "UNKNOWN"),
        ]
        rows = networth_ui._statement_rows(accounts, {}, "USD")
        shown = {row.get("account_id") for row in rows if row.get("account_id")}
        self.assertEqual(shown, {"cash", "broker", "card", "credit"})
        self.assertEqual(self.row(rows, "Total Assets:")["amount"], 2000)
        foreign = networth_ui._statement_rows(accounts, {}, "CAD")
        self.assertEqual(self.row(foreign, "Total Assets:")["amount"], 9000)

    def test_zero_asset_denominator_leaves_percentages_unavailable(self):
        rows = networth_ui._statement_rows([
            statement_account("cash", "Synthetic Zero Cash", 0, "Asset"),
            statement_account("card", "Synthetic Card", 50, "Liability"),
        ], {}, "USD")
        self.assertEqual(self.row(rows, "Total Assets:")["amount"], 0)
        self.assertEqual(self.row(rows, "Equity:")["amount"], -50)
        self.assertTrue(all(row["share"] is None for row in rows))

    def test_missing_asset_amount_propagates_to_totals_equity_and_percentages(self):
        accounts = deepcopy(self.accounts)
        accounts[0]["current"] = None
        rows = networth_ui._statement_rows(accounts, {}, "USD")
        cash = next(row for row in rows if row.get("account_id") == "cash")
        self.assertIsNone(cash["amount"])
        self.assertIsNone(self.row(rows, "Total Assets:")["amount"])
        self.assertIsNone(self.row(rows, "Equity:")["amount"])
        self.assertIsNone(self.row(rows, "Total Liabilities & Equity:")["amount"])
        self.assertEqual(self.row(rows, "Total Liabilities:")["amount"], 475)
        self.assertTrue(all(row["share"] is None for row in rows))

    def test_missing_liability_preserves_known_assets_but_makes_equity_unavailable(self):
        accounts = deepcopy(self.accounts)
        accounts[2]["current"] = None
        rows = networth_ui._statement_rows(accounts, {}, "USD")
        self.assertEqual(self.row(rows, "Total Assets:")["amount"], 2000)
        self.assertIsNone(self.row(rows, "Total Liabilities:")["amount"])
        self.assertIsNone(self.row(rows, "Equity:")["amount"])
        self.assertIsNone(self.row(rows, "Total Liabilities & Equity:")["amount"])

    def test_missing_equity_allocation_does_not_erase_known_residual_equity(self):
        accounts = self.accounts + [statement_account("owner", "Synthetic Unknown Capital", None, "Equity")]
        rows = networth_ui._statement_rows(accounts, {}, "USD")
        self.assertIsNone(next(row for row in rows if row.get("account_id") == "owner")["amount"])
        self.assertIsNone(self.row(rows, "Unallocated equity")["amount"])
        self.assertEqual(self.row(rows, "Total Equity:")["amount"], 1525)
        self.assertEqual(self.row(rows, "Total Liabilities & Equity:")["amount"], 2000)

    def test_nonfinite_nonnumeric_and_unknown_currency_are_never_printed_as_money(self):
        for current in (float("nan"), float("inf"), "not-a-balance", True):
            with self.subTest(current=current):
                accounts = [statement_account("invalid", "Synthetic Invalid", current, "Asset")]
                rows = networth_ui._statement_rows(accounts, {}, "USD")
                self.assertIsNone(next(row for row in rows if row.get("account_id") == "invalid")["amount"])
                self.assertIsNone(self.row(rows, "Total Assets:")["amount"])
        unknown = networth_ui._statement_rows([
            statement_account("unknown", "Synthetic Unknown", 900, "Asset", "UNKNOWN")], {}, "UNKNOWN")
        self.assertIsNone(next(row for row in unknown if row.get("account_id") == "unknown")["amount"])
        self.assertIsNone(self.row(unknown, "Total Assets:")["amount"])

    def test_statement_never_mutates_source_history_or_preferences(self):
        preferences = {"cash": {"nickname": "Household Cash", "classification": "Equity"}}
        before_accounts, before_preferences = deepcopy(self.accounts), deepcopy(preferences)
        rows = networth_ui._statement_rows(self.accounts, preferences, "USD")
        self.assertEqual(self.accounts, before_accounts)
        self.assertEqual(preferences, before_preferences)
        self.assertEqual(self.row(rows, "Total Assets:")["amount"], 2000)
        cash = next(row for row in rows if row.get("account_id") == "cash")
        self.assertEqual(cash["classification"], "Asset")

    def test_statement_html_escapes_nicknames_and_has_three_columns(self):
        nickname = '<script>alert("synthetic")</script> & Household'
        rows = networth_ui._statement_rows(self.accounts, {"cash": {"nickname": nickname}}, "USD")
        markup = networth_ui._statement_html(rows, "USD")
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)
        self.assertIn("&amp; Household", markup)
        self.assertIn("Account", markup)
        self.assertIn("Balance", markup)
        self.assertIn("% of Assets", markup)
        self.assertNotIn(".stApp", markup)

        class HeaderParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_head = False
                self.columns = 0

            def handle_starttag(self, tag, attributes):
                if tag == "thead":
                    self.in_head = True
                if tag == "th" and self.in_head:
                    self.columns += 1

            def handle_endtag(self, tag):
                if tag == "thead":
                    self.in_head = False

        parsed = HeaderParser()
        parsed.feed(markup)
        self.assertEqual(parsed.columns, 3)
        style = markup.split("<style>", 1)[1].split("</style>", 1)[0]
        for selector in re.findall(r"([^{}]+)\{", style):
            if not selector.strip().startswith("@"):
                self.assertTrue(all(".networth-statement" in part for part in selector.split(",")), selector)


class NetworthAccountDetailsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.store.state["accounts"] = {"cash": statement_account("cash", "Synthetic Checking", 25, "Asset"),
                                         "card": statement_account("card", "Synthetic Card", 10, "Liability")}
        self.store.state["preferences"] = {
            "cash": {"classification": "Asset", "nickname": "Old Name", "custom": "preserved"},
            "card": {"classification": "Liability", "nickname": "Card Name"},
        }

    def test_save_account_details_preserves_other_preferences_and_current_balances(self):
        before_accounts = deepcopy(self.store.state["accounts"])
        networth_ui._save_account_details(self.store, "cash", "  Household Cash  ", "Equity")
        self.assertEqual(self.store.state["preferences"]["cash"], {
            "classification": "Equity", "nickname": "Household Cash", "custom": "preserved"})
        self.assertEqual(self.store.state["preferences"]["card"]["nickname"], "Card Name")
        self.assertEqual(self.store.state["accounts"], before_accounts)

    def test_nickname_only_save_preserves_latest_classification(self):
        self.store.state["preferences"]["cash"]["classification"] = "Equity"
        networth_ui._save_account_details(self.store, "cash", "Household Cash")
        self.assertEqual(self.store.state["preferences"]["cash"]["classification"], "Equity")
        self.assertEqual(self.store.state["preferences"]["cash"]["nickname"], "Household Cash")

    def test_blank_nickname_removes_override_and_restores_original_label(self):
        networth_ui._save_account_details(self.store, "cash", "  ", "Asset")
        self.assertNotIn("nickname", self.store.state["preferences"]["cash"])
        label = networth_ui._account_label(self.store.state["accounts"]["cash"], self.store.state["preferences"])
        self.assertIn("Synthetic Checking", label)
        self.assertEqual(self.store.state["preferences"]["cash"]["custom"], "preserved")

    def test_nickname_length_classification_and_account_are_validated_before_saving(self):
        before = self.store.read_state()
        for account_id, nickname, classification in (("cash", "x" * 61, "Asset"),
                ("cash", "Name", "Invalid"), ("absent", "Name", "Asset")):
            with self.subTest(account_id=account_id, classification=classification), self.assertRaises(NetworthError):
                networth_ui._save_account_details(self.store, account_id, nickname, classification)
            self.assertEqual(self.store.state, before)
        networth_ui._save_account_details(self.store, "cash", " " + "x" * 60 + " ", "Asset")
        self.assertEqual(self.store.state["preferences"]["cash"]["nickname"], "x" * 60)

    def test_cas_retry_preserves_concurrent_account_preferences(self):
        store = self.store

        def retry(callback):
            callback(deepcopy(store.state))
            store.state["preferences"]["cash"]["new_field"] = "concurrent"
            store.state["preferences"]["card"]["nickname"] = "Concurrent Card"
            latest = deepcopy(store.state)
            result = callback(latest)
            store.state = latest if result is None else result
            return deepcopy(store.state)

        with patch.object(store, "update_state", side_effect=retry):
            networth_ui._save_account_details(store, "cash", "Household Cash", "Asset")
        self.assertEqual(store.state["preferences"]["cash"]["new_field"], "concurrent")
        self.assertEqual(store.state["preferences"]["card"]["nickname"], "Concurrent Card")
        self.assertEqual(store.state["preferences"]["cash"]["nickname"], "Household Cash")


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
