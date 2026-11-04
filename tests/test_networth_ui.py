"""Offline Networth UI, durable link recovery and saved-tab migration checks."""

from copy import deepcopy
from pathlib import Path
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from html.parser import HTMLParser
from io import StringIO
import re
import shutil
import subprocess
from types import SimpleNamespace
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
            self.assertFalse(app.dataframe)
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

    def test_legacy_string_preferences_render_and_edit_only_the_selected_account(self):
        self.store.state["items"]["i"] = {"label": "Synthetic Bank", "access_token": "PRIVATE"}
        self.store.state["accounts"] = {
            "a": {**statement_account("a", "Synthetic Checking", 1200, "Asset", mask="5474"),
                  "item_id": "i", "type": "depository", "subtype": "checking"},
            "b": {**statement_account("b", "Synthetic Card", 100, "Liability", mask="7777"),
                  "item_id": "i", "type": "credit", "subtype": "credit card"},
        }
        self.store.state["preferences"] = {"a": "Equity", "b": "Liability"}
        app = AppTest.from_string("from src.networth_ui import render_networth\nrender_networth()")
        with patch("src.networth_ui.PlaidConfig.from_runtime", return_value=self.plaid.config), patch(
            "src.networth_ui.StoreConfig.from_runtime", return_value=object()
        ), patch("src.networth_ui.GitHubNetworthStore", return_value=self.store):
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(app.text_input(key="networth_nickname_a").value, "")
            app.text_input(key="networth_nickname_a").input("Household Cash")
            next(button for button in app.button if button.label == "SAVE NICKNAME").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(self.store.state["preferences"]["a"], {"classification": "Equity", "nickname": "Household Cash"})
            self.assertEqual(self.store.state["preferences"]["b"], "Liability")
            app.multiselect[0].set_value(["b"]).run()
            self.assertFalse(app.error)
            app.selectbox(key="networth_apply_classification").select("Asset")
            next(button for button in app.button if button.label == "SAVE CLASSIFICATIONS").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(self.store.state["preferences"]["b"], {"classification": "Asset"})
            self.assertEqual(self.store.state["preferences"]["a"], {"classification": "Equity", "nickname": "Household Cash"})

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

    def test_legacy_string_preferences_keep_bank_labels_and_classification_math(self):
        account = statement_account("legacy", "Synthetic Legacy", 200, "Asset", mask="7777")
        for classification in ("Equity", "Liability"):
            with self.subTest(classification=classification):
                preferences = {"legacy": classification}
                state = {"accounts": {"legacy": account}, "preferences": preferences}
                rows = networth_ui._statement_rows(networth_ui.account_rows(state), preferences, "USD")
                detail = next(row for row in rows if row.get("account_id") == "legacy")
                self.assertEqual(detail["label"], "Synthetic Legacy • 7777")
                self.assertEqual(detail["classification"], classification)
                self.assertEqual(detail["amount"], -200 if classification == "Liability" else 200)
                self.assertEqual(self.row(rows, "Total Assets:")["amount"], 0)

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

    def test_legacy_string_nickname_save_preserves_class_and_normalizes_only_edited_account(self):
        self.store.state["preferences"] = {"cash": "Equity", "card": "Liability"}
        networth_ui._save_account_details(self.store, "cash", "Household Cash")
        self.assertEqual(self.store.state["preferences"]["cash"], {"classification": "Equity", "nickname": "Household Cash"})
        self.assertEqual(self.store.state["preferences"]["card"], "Liability")
        networth_ui._save_account_details(self.store, "cash", "", "Asset")
        self.assertEqual(self.store.state["preferences"]["cash"], {"classification": "Asset"})
        self.assertEqual(self.store.state["preferences"]["card"], "Liability")

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

    def test_verified_native_nickname_save_refreshes_readonly_cache(self):
        self.store.state["items"]["bank"] = {"access_token": "PRIVATE_ACCESS_TOKEN"}
        cache_key = ("Synthetic/Portfolio", "networth-data", self.store.scope, "production")
        session = {"_networth_verified_balances": {cache_key: networth_ui._readonly_snapshot(self.store.state)}}
        with patch("src.networth_ui.st.session_state", session):
            networth_ui._save_account_details(self.store, "cash", "New Household Name", cache_key=cache_key)
        cached = session["_networth_verified_balances"][cache_key]
        self.assertEqual(cached["preferences"]["cash"], {
            "nickname": "New Household Name", "classification": "Asset"})
        self.assertEqual(cached, networth_ui._readonly_snapshot(self.store.state))
        self.assertNotIn("PRIVATE_ACCESS_TOKEN", repr(cached))


class NetworthBalanceStatusTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [
            statement_account("cash", "Synthetic Cash", 100, "Asset"),
            statement_account("card", "Synthetic Card", 20, "Liability"),
            statement_account("capital", "Synthetic Capital", 80, "Equity"),
        ]

    def test_independent_equity_reconciles_but_generated_remainder_cannot_hide_gap(self):
        before = deepcopy(self.accounts)
        status = networth_ui._balance_status(self.accounts, "USD")
        self.assertEqual(status, {"state": "balanced", "gap": 0, "stale": False})
        self.accounts[2]["current"] = 75
        status = networth_ui._balance_status(self.accounts, "USD")
        self.assertEqual(status, {"state": "unbalanced", "gap": 5, "stale": False})
        statement = networth_ui._statement_rows(self.accounts, {}, "USD")
        self.assertEqual(next(row["amount"] for row in statement if row["label"] == "Unallocated equity"), 5)
        self.assertEqual(next(row["amount"] for row in statement if row["label"] == "Total Liabilities & Equity:"), 100)
        self.accounts[2]["current"] = 80
        self.assertEqual(self.accounts, before)

    def test_no_independent_equity_is_calculated_instead_of_falsely_verified(self):
        self.assertEqual(networth_ui._balance_status(self.accounts[:2], "USD"),
                         {"state": "calculated", "gap": None, "stale": False})
        self.assertNotEqual(networth_ui._balance_status([], "USD")["state"], "balanced")

    def test_native_currencies_never_offset_each_others_gap_or_staleness(self):
        self.accounts[2]["current"] = 75
        self.accounts.extend([
            {**statement_account("foreign", "Synthetic CAD Cash", 1000, "Asset", "CAD"), "stale": True},
            statement_account("foreign-equity", "Synthetic CAD Equity", 1000, "Equity", "CAD"),
        ])
        self.assertEqual(networth_ui._balance_status(self.accounts, "USD"),
                         {"state": "unbalanced", "gap": 5, "stale": False})
        self.assertEqual(networth_ui._balance_status(self.accounts, "CAD"),
                         {"state": "balanced", "gap": 0, "stale": True})

    def test_signed_credit_and_negative_equity_preserve_accounting_equation(self):
        self.accounts[1]["current"] = -25
        self.accounts[2]["current"] = 125
        self.assertEqual(networth_ui._balance_status(self.accounts, "USD")["state"], "balanced")
        self.accounts[0]["current"] = 0
        self.accounts[1]["current"] = 25
        self.accounts[2]["current"] = -25
        self.assertEqual(networth_ui._balance_status(self.accounts, "USD")["state"], "balanced")

    def test_cent_rounding_avoids_float_noise_without_hiding_a_real_cent(self):
        accounts = [statement_account("one", "Synthetic One", .1, "Asset"),
                    statement_account("two", "Synthetic Two", .2, "Asset"),
                    statement_account("equity", "Synthetic Equity", .3, "Equity")]
        self.assertEqual(networth_ui._balance_status(accounts, "USD")["state"], "balanced")
        self.accounts[2]["current"] = 79.996
        self.assertEqual(networth_ui._balance_status(self.accounts, "USD")["gap"], 0)
        self.accounts[2]["current"] = 79.994
        self.assertEqual(networth_ui._balance_status(self.accounts, "USD"),
                         {"state": "unbalanced", "gap": .01, "stale": False})

    def test_missing_invalid_and_unclassified_inputs_are_incomplete(self):
        for index in range(3):
            for value in (None, float("nan"), float("inf"), "invalid", True):
                with self.subTest(index=index, value=value):
                    accounts = deepcopy(self.accounts)
                    accounts[index]["current"] = value
                    status = networth_ui._balance_status(accounts, "USD")
                    self.assertEqual(status["state"], "incomplete")
                    self.assertIsNone(status["gap"])
        unclassified = self.accounts + [statement_account("unknown", "Synthetic Unknown", 5, "Unclassified")]
        self.assertEqual(networth_ui._balance_status(unclassified, "USD")["state"], "incomplete")
        unknown_currency = [statement_account("unknown", "Synthetic Unknown Currency", 5, "Asset", "UNKNOWN")]
        self.assertEqual(networth_ui._balance_status(unknown_currency, "UNKNOWN")["state"], "incomplete")

    def test_excluded_accounts_do_not_change_balance_or_freshness(self):
        accounts = self.accounts + [{**statement_account("excluded", "Synthetic Excluded", None, "Excluded"),
                                    "stale": True}]
        self.assertEqual(networth_ui._balance_status(accounts, "USD"),
                         {"state": "balanced", "gap": 0, "stale": False})
        accounts[0]["stale"] = True
        self.assertEqual(networth_ui._balance_status(accounts, "USD"),
                         {"state": "balanced", "gap": 0, "stale": True})


class NetworthBucketMoveTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.store.state["items"]["bank"] = {"label": "Synthetic Bank", "access_token": "PRIVATE_ACCESS_TOKEN"}
        self.store.state["accounts"] = {
            "cash": {**statement_account("cash", "Synthetic Cash", 100, "Asset"),
                     "item_id": "bank", "type": "depository"},
            "card": {**statement_account("card", "Synthetic Card", 20, "Liability"),
                     "item_id": "bank", "type": "credit"},
        }
        self.store.state["preferences"] = {
            "cash": {"classification": "Asset", "nickname": "Household Cash", "custom": "preserved"},
            "card": {"classification": "Liability", "nickname": "Card Name"},
        }
        self.store.state["selected_accounts"] = ["card"]
        self.store.days["2026-11-03"] = {"captures": {"original": {"accounts": deepcopy(
            networth_ui.account_rows(self.store.state))}}}
        self.view_id = "synthetic-current-view"

    def event(self, **changes):
        revision = networth_ui._bucket_revision(networth_ui.account_rows(self.store.state))
        return {"action": "classify", "account_id": "cash", "target_class": "Equity", "base_class": "Asset",
                "event_id": "11111111-1111-4111-8111-111111111111", "view_id": self.view_id,
                "revision": revision, **changes}

    def move(self, event, **kwargs):
        return networth_ui._save_bucket_move(self.store, event, view_id=self.view_id,
                                            revision=self.event()["revision"], **kwargs)

    def test_revision_tracks_only_account_identity_and_effective_classification(self):
        rows = networth_ui.account_rows(self.store.state)
        before = deepcopy(rows)
        revision = networth_ui._bucket_revision(rows)
        self.assertTrue(isinstance(revision, str) and revision)
        self.assertEqual(networth_ui._bucket_revision(list(reversed(rows))), revision)
        changed = deepcopy(rows)
        changed[0].update(name="Another Name", current=900, as_of="2099-01-01T00:00:00Z", stale=True)
        self.assertEqual(networth_ui._bucket_revision(changed), revision)
        changed[0]["classification"] = "Equity"
        self.assertNotEqual(networth_ui._bucket_revision(changed), revision)
        changed = deepcopy(rows)
        changed[0]["account_id"] = "another-account"
        self.assertNotEqual(networth_ui._bucket_revision(changed), revision)
        self.assertEqual(rows, before)

    def test_drop_changes_only_one_classification_and_replaying_it_is_idempotent(self):
        before_accounts = deepcopy(self.store.state["accounts"])
        before_history = deepcopy(self.store.days)
        event = self.event()
        with patch("src.networth_ui.sync_networth") as sync, patch.object(networth_ui.PlaidClient, "balances") as balances:
            result = self.move(event)
            self.assertEqual(result, self.store.state)
            self.assertEqual(result["preferences"]["cash"], {
                "classification": "Equity", "nickname": "Household Cash", "custom": "preserved"})
            first = deepcopy(self.store.state)
            replay = networth_ui._save_bucket_move(self.store, event, view_id=self.view_id, revision=event["revision"])
            self.assertEqual(replay, first)
            sync.assert_not_called()
            balances.assert_not_called()
        self.assertEqual(self.store.state["accounts"], before_accounts)
        self.assertEqual(self.store.state["preferences"]["card"]["nickname"], "Card Name")
        self.assertEqual(self.store.state["selected_accounts"], ["card"])
        self.assertEqual(self.store.days, before_history)

    def test_invalid_shapes_missing_or_extra_fields_never_change_preferences(self):
        event = self.event()
        invalid = [None, [], "classify", True, {**event, "balance": 0}, {**event, "nickname": "Injected"}]
        invalid.extend({key: value for key, value in event.items() if key != missing} for missing in event)
        before = self.store.read_state()
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(NetworthError):
                self.move(candidate)
            self.assertEqual(self.store.state, before)

    def test_invalid_action_account_class_view_revision_or_uuid_is_rejected(self):
        invalid = [
            {"action": "sync"}, {"action": ["classify"]}, {"account_id": "missing"},
            {"account_id": ""}, {"account_id": ["cash"]}, {"target_class": "Invalid"},
            {"target_class": None}, {"base_class": "Invalid"}, {"base_class": None},
            {"view_id": "historical-view"}, {"view_id": None},
            {"revision": "stale-revision"}, {"revision": None}, {"revision": False},
            {"event_id": "invalid"}, {"event_id": None}, {"event_id": "1" * 32},
            {"event_id": " 11111111-1111-4111-8111-111111111111 "},
            {"event_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        ]
        before = self.store.read_state()
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(NetworthError):
                self.move(self.event(**changes))
            self.assertEqual(self.store.state, before)

    def test_stale_account_classification_and_removed_accounts_are_not_overwritten(self):
        event = self.event()
        self.store.state["preferences"]["cash"]["classification"] = "Liability"
        before = self.store.read_state()
        with self.assertRaises(NetworthError):
            networth_ui._save_bucket_move(self.store, event, view_id=self.view_id, revision=event["revision"])
        self.assertEqual(self.store.state, before)
        self.store.state["accounts"].pop("cash")
        before = self.store.read_state()
        with self.assertRaises(NetworthError):
            networth_ui._save_bucket_move(self.store, event, view_id=self.view_id, revision=event["revision"])
        self.assertEqual(self.store.state, before)

    def test_cas_retry_keeps_new_balances_nicknames_and_unrelated_preferences(self):
        event = self.event()

        def retry(callback):
            callback(deepcopy(self.store.state))
            self.store.state["accounts"]["cash"]["current"] = 600
            self.store.state["preferences"]["cash"]["nickname"] = "Latest Household Name"
            self.store.state["preferences"]["cash"]["new_field"] = "concurrent"
            self.store.state["preferences"]["card"]["nickname"] = "Latest Card Name"
            latest = self.store.read_state()
            replacement = callback(latest)
            self.store.state = latest if replacement is None else replacement
            return self.store.read_state()

        with patch.object(self.store, "update_state", side_effect=retry):
            result = self.move(event)
        self.assertEqual(result["accounts"]["cash"]["current"], 600)
        self.assertEqual(result["preferences"]["cash"], {
            "classification": "Equity", "nickname": "Latest Household Name",
            "custom": "preserved", "new_field": "concurrent"})
        self.assertEqual(result["preferences"]["card"]["nickname"], "Latest Card Name")

    def test_legacy_preference_and_default_classification_can_be_moved(self):
        for preference in ("Asset", None):
            with self.subTest(preference=preference):
                self.setUp()
                if preference is None:
                    self.store.state["preferences"].pop("cash")
                else:
                    self.store.state["preferences"]["cash"] = preference
                self.store.state["preferences"]["card"] = "Liability"
                result = self.move(self.event())
                self.assertEqual(result["preferences"]["cash"], {"classification": "Equity"})
                self.assertEqual(result["preferences"]["card"], "Liability")

    def test_verified_save_refreshes_sanitized_session_cache_but_failure_does_not(self):
        cache_key = ("Synthetic/Portfolio", "networth-data", self.store.scope, "production")
        session = {"_networth_verified_balances": {cache_key: networth_ui._readonly_snapshot(self.store.state)}}
        before_cache = deepcopy(session)
        self.store.fail_next_save = True
        with patch("src.networth_ui.st.session_state", session):
            with self.assertRaises(NetworthError):
                self.move(self.event(), cache_key=cache_key)
            self.assertEqual(session, before_cache)
            result = self.move(self.event(), cache_key=cache_key)
        cached = session["_networth_verified_balances"][cache_key]
        self.assertEqual(cached["preferences"]["cash"]["classification"], "Equity")
        self.assertEqual(cached, networth_ui._readonly_snapshot(result))
        self.assertNotIn("PRIVATE_ACCESS_TOKEN", repr(cached))

    def test_component_repeated_event_is_saved_once_and_acknowledged_after_success(self):
        session = {}
        cache_key = ("Synthetic/Portfolio", "networth-data", self.store.scope, "production")
        events = []

        def component(**props):
            if not events:
                events.append(self.event(view_id=props["view_id"], revision=props["revision"]))
            return events[0]

        with patch("src.networth_ui.st.session_state", session), patch("src.networth_ui.st.rerun"), patch(
            "src.networth_ui._networth_buckets", side_effect=component
        ) as board, patch.object(self.store, "update_state", wraps=self.store.update_state) as write, patch(
            "src.networth_ui.sync_networth"
        ) as sync:
            networth_ui._bucket_editor(self.store, self.store.read_state(), cache_key=cache_key)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(self.store.state["preferences"]["cash"]["classification"], "Equity")
            networth_ui._bucket_editor(self.store, self.store.read_state(), cache_key=cache_key)
            self.assertEqual(write.call_count, 1)
            sync.assert_not_called()
        acknowledged = board.call_args.kwargs["ack"]
        self.assertEqual(acknowledged["event_id"], events[0]["event_id"])
        self.assertTrue(acknowledged["ok"])
        self.assertEqual(session["_networth_verified_balances"][cache_key]["preferences"]["cash"]["classification"], "Equity")
        self.assertNotIn("PRIVATE_ACCESS_TOKEN", repr(board.call_args.kwargs))

    def test_component_failed_save_keeps_cache_and_returns_failure_acknowledgment(self):
        cache_key = ("Synthetic/Portfolio", "networth-data", self.store.scope, "production")
        session = {"_networth_verified_balances": {cache_key: networth_ui._readonly_snapshot(self.store.state)}}
        before = deepcopy(session["_networth_verified_balances"])
        events = []

        def component(**props):
            if not events:
                events.append(self.event(view_id=props["view_id"], revision=props["revision"]))
            return events[0]

        self.store.fail_next_save = True
        with patch("src.networth_ui.st.session_state", session), patch("src.networth_ui.st.rerun"), patch(
            "src.networth_ui._networth_buckets", side_effect=component
        ) as board, patch.object(self.store, "update_state", wraps=self.store.update_state) as write:
            networth_ui._bucket_editor(self.store, self.store.read_state(), cache_key=cache_key)
            networth_ui._bucket_editor(self.store, self.store.read_state(), cache_key=cache_key)
            self.assertEqual(write.call_count, 1)
        self.assertFalse(board.call_args.kwargs["ack"]["ok"])
        self.assertEqual(self.store.state["preferences"]["cash"]["classification"], "Asset")
        self.assertEqual(session["_networth_verified_balances"], before)


class NetworthResilienceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.store.state["items"]["i"] = {
            "label": "Synthetic Bank", "access_token": "PRIVATE_ACCESS_TOKEN",
            "last_sync": "2026-11-03T16:00:00Z",
        }
        self.store.state["accounts"]["a"] = {
            **statement_account("a", "Synthetic Checking", 1200, "Asset", mask="5474"),
            "item_id": "i", "type": "depository", "subtype": "checking",
            "as_of": "2026-11-03T16:00:00Z",
        }
        self.store.state["preferences"]["a"] = {"nickname": "Household Cash", "classification": "Asset"}
        self.store.state["last_sync"] = "2026-11-03T17:00:00Z"
        self.storage_config = SimpleNamespace(repository="Synthetic/Portfolio", branch="networth-data")
        self.plaid_config = PlaidConfig("FAKE_ID", "FAKE_SECRET")

    @contextmanager
    def configured(self):
        app = AppTest.from_string("from src.networth_ui import render_networth\nrender_networth()")
        with patch("src.networth_ui.PlaidConfig.from_runtime", side_effect=lambda: self.plaid_config), patch(
            "src.networth_ui.StoreConfig.from_runtime", side_effect=lambda: self.storage_config
        ), patch("src.networth_ui.GitHubNetworthStore", return_value=self.store):
            yield app

    def test_manual_storage_or_transport_failure_keeps_loaded_statement(self):
        for failure in (NetworthError("ARCHIVE_UNAVAILABLE"), NetworthError("PLAID_CONNECTION_FAILED"),
                        NetworthError("NETWORTH_SYNC_LEASE_LOST"), RuntimeError("PRIVATE_FAILURE_DETAIL")):
            with self.subTest(failure=type(failure).__name__), self.configured() as app, patch(
                "src.networth_ui.sync_networth", side_effect=failure
            ), patch("src.networth_ui._render_statement", wraps=networth_ui._render_statement) as statement:
                app.run()
                statement.reset_mock()
                next(button for button in app.button if button.label == "SYNC NOW").click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                self.assertTrue(any("saved balances" in message.value for message in app.warning))
                self.assertEqual(statement.call_args.args[0][0]["current"], 1200)
                self.assertEqual(self.store.state["accounts"]["a"]["current"], 1200)
                self.assertNotIn("PRIVATE_FAILURE_DETAIL", " ".join(message.value for message in app.warning))

    def test_busy_sync_informs_user_and_preserves_statement(self):
        with self.configured() as app, patch("src.networth_ui.sync_networth",
                side_effect=NetworthError("NETWORTH_SYNC_IN_PROGRESS")), patch(
            "src.networth_ui._render_statement", wraps=networth_ui._render_statement
        ) as statement:
            app.run()
            statement.reset_mock()
            next(button for button in app.button if button.label == "SYNC NOW").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(any("already running" in message.value for message in app.info))
            self.assertEqual(statement.call_args.args[0][0]["account_id"], "a")

    def test_component_props_are_sanitized_and_history_has_no_second_editable_board(self):
        self.store.state["pending_links"]["link"] = {"link_token": "PRIVATE_LINK_TOKEN", "hosted_link_url": "PRIVATE_URL"}
        day = networth_ui.datetime.now(networth_ui.TIMEZONE).date().isoformat()
        self.store.days[day] = {"captures": {"saved": {
            "captured_at": day + "T10:00:00Z", "complete": True,
            "accounts": deepcopy(networth_ui.account_rows(self.store.state)),
        }}}
        with self.configured() as app, patch("src.networth_ui._pending_links"), patch(
            "src.networth_ui._networth_buckets", return_value=None
        ) as board, patch("src.networth_ui.sync_networth") as sync:
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertFalse(app.dataframe)
            self.assertEqual(board.call_count, 1)
            props = board.call_args.kwargs
            self.assertEqual(props["key"], "networth_account_buckets")
            self.assertIsNone(props["default"])
            self.assertTrue(props["view_id"])
            self.assertEqual(props["revision"], networth_ui._bucket_revision(networth_ui.account_rows(self.store.state)))
            self.assertEqual(set(props["accounts"][0]), {
                "account_id", "label", "balance", "currency", "classification", "stale"})
            self.assertEqual(props["accounts"][0]["label"], "Household Cash")
            self.assertTrue(any(expander.label == "Daily history & CSV" for expander in app.expander))
            for secret in ("PRIVATE_ACCESS_TOKEN", "PRIVATE_LINK_TOKEN", "PRIVATE_URL"):
                self.assertNotIn(secret, repr(props))
            sync.assert_not_called()

    def test_verified_session_snapshot_has_no_tokens_and_is_read_only_on_storage_outage(self):
        self.store.state["pending_links"]["link"] = {"link_token": "PRIVATE_LINK_TOKEN", "hosted_link_url": "PRIVATE_URL"}
        with self.configured() as app, patch("src.networth_ui._pending_links"), patch(
            "src.networth_ui.sync_networth"
        ) as sync:
            app.run()
            self.assertFalse(app.exception)
            cache = app.session_state["_networth_verified_balances"]
            self.assertNotIn("PRIVATE_ACCESS_TOKEN", repr(cache))
            self.assertNotIn("PRIVATE_LINK_TOKEN", repr(cache))
            self.assertNotIn("PRIVATE_URL", repr(cache))
            self.assertNotIn("pending_links", next(iter(cache.values())))
            before = deepcopy(self.store.state)
            with patch.object(self.store, "read_state", side_effect=NetworthError("STORAGE_UNAVAILABLE")), patch.object(
                self.store, "update_state", wraps=self.store.update_state
            ) as write, patch.object(self.store, "read_day", wraps=self.store.read_day) as history, patch(
                "src.networth_ui._render_statement", wraps=networth_ui._render_statement
            ) as statement, patch("src.networth_ui._networth_buckets") as board:
                app.run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                self.assertTrue(any("read-only" in message.value for message in app.warning))
                self.assertEqual(statement.call_args.args[0][0]["current"], 1200)
                self.assertEqual(statement.call_args.args[1]["a"]["nickname"], "Household Cash")
                self.assertFalse(app.button)
                write.assert_not_called()
                history.assert_not_called()
                sync.assert_not_called()
                board.assert_not_called()
            self.assertEqual(self.store.state, before)

    def test_storage_outage_cannot_reuse_cache_from_another_source_or_environment(self):
        for changed in ("repository", "branch", "scope", "environment"):
            with self.subTest(changed=changed):
                self.setUp()
                with self.configured() as app:
                    app.run()
                    self.assertFalse(app.exception)
                    if changed == "environment":
                        self.plaid_config = PlaidConfig("FAKE_ID", "FAKE_SECRET", "sandbox")
                    elif changed == "scope":
                        self.store.scope = "another-ledger"
                    else:
                        setattr(self.storage_config, changed, "another-source")
                    with patch.object(self.store, "read_state", side_effect=NetworthError("STORAGE_UNAVAILABLE")), patch(
                        "src.networth_ui._render_statement", wraps=networth_ui._render_statement
                    ) as statement, patch.object(self.store, "update_state", wraps=self.store.update_state) as write:
                        app.run()
                        self.assertFalse(app.exception)
                        self.assertTrue(app.error)
                        statement.assert_not_called()
                        write.assert_not_called()

    def test_no_verified_cache_means_storage_failure_cannot_invent_a_statement(self):
        with self.configured() as app, patch.object(self.store, "read_state", side_effect=NetworthError("STORAGE_UNAVAILABLE")), patch(
            "src.networth_ui._render_statement", wraps=networth_ui._render_statement
        ) as statement, patch.object(self.store, "update_state", wraps=self.store.update_state) as write:
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            statement.assert_not_called()
            write.assert_not_called()

    def test_readonly_cache_retains_legacy_string_classification_and_statement_math(self):
        for classification in ("Equity", "Liability"):
            with self.subTest(classification=classification), self.configured() as app:
                self.store.state["preferences"]["a"] = classification
                snapshot = networth_ui._readonly_snapshot(self.store.state)
                self.assertEqual(snapshot["preferences"]["a"], {"classification": classification})
                key = (self.storage_config.repository, self.storage_config.branch,
                       self.store.scope, self.plaid_config.environment)
                app.session_state["_networth_verified_balances"] = {key: snapshot}
                with patch.object(self.store, "read_state", side_effect=NetworthError("STORAGE_UNAVAILABLE")), patch(
                    "src.networth_ui._render_statement", wraps=networth_ui._render_statement
                ) as statement:
                    app.run()
                    self.assertFalse(app.exception)
                    self.assertFalse(app.error)
                    rows, preferences = statement.call_args.args[:2]
                    self.assertEqual(rows[0]["classification"], classification)
                    presentation = networth_ui._statement_rows(rows, preferences, "USD")
                    total_assets = next(row["amount"] for row in presentation if row["label"] == "Total Assets:")
                    self.assertEqual(total_assets, 0)
                    detail = next(row for row in presentation if row.get("account_id") == "a")
                    self.assertEqual(detail["amount"], -1200 if classification == "Liability" else 1200)
                    self.assertTrue(any("read-only" in message.value for message in app.warning))

    def test_failed_attempt_header_keeps_latest_account_update_distinct(self):
        self.store.state["last_errors"] = {"i": "PLAID_INTERNAL_SERVER_ERROR"}
        self.store.state["items"]["i"]["error"] = "PLAID_INTERNAL_SERVER_ERROR"
        with self.configured() as app:
            app.run()
            self.assertFalse(app.exception)
            captions = [message.value for message in app.caption]
            self.assertTrue(any("Last attempt:" in value for value in captions))
            self.assertFalse(any("Last pull:" in value for value in captions))
            self.assertTrue(any("Most recent account balance update:" in value and "some accounts may be older" in value for value in captions))
        self.assertEqual(networth_ui._latest_account_update(self.store.state), "2026-11-03T16:00:00Z")


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
