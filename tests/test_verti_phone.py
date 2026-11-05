"""Phone-workspace isolation and order-safety regressions with synthetic data."""

import ast
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import re
import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from src import vertical_options as math
from src import vertical_options_ui as desktop
from src import verti_phone_ui as phone


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = [
    {"accountIdKey": "other", "accountId": "10001234", "accountDesc": "Other"},
    {"accountIdKey": "preferred", "accountId": "10005474", "accountDesc": "Preferred"},
]


def quoted_spread():
    expiry = date.today() + timedelta(days=45)
    rows = [
        {"expiry": expiry, "call_put": "CALL", "strike": 100, "last": 3,
         "bid": 2.9, "ask": 3.1, "osi": "SYNTHETIC-BUY"},
        {"expiry": expiry, "call_put": "CALL", "strike": 105, "last": 2,
         "bid": 1.9, "ask": 2.1, "osi": "SYNTHETIC-SELL"},
    ]
    candidates, _ = math.scan_vertical_candidates(
        symbol="SPY", spot_last=100, option_rows=rows,
        target_breakeven=None, max_cost=200,
    )
    return candidates[0], rows


def order_ui(state, clicked=()):
    """Native-widget stand-in that enforces disabled controls and existing values."""
    ui = MagicMock()
    ui.session_state = state
    ui.button.side_effect = lambda label, *, key, disabled=False, **kwargs: key in clicked and not disabled
    ui.checkbox.side_effect = lambda label, *, key, **kwargs: state.get(key, False)
    ui.number_input.side_effect = lambda label, *, key, **kwargs: state.setdefault(key, 1)
    ui.selectbox.side_effect = lambda label, options, *, key, **kwargs: state.setdefault(key, options[0])
    ui.multiselect.side_effect = lambda label, options, *, key, **kwargs: list(state.get(key, []))
    ui.columns.side_effect = lambda sizes, **kwargs: [ui] * (sizes if isinstance(sizes, int) else len(sizes))
    return ui


class PhoneIsolationTests(unittest.TestCase):
    def test_production_route_has_its_own_owner_and_preserves_desktop_dispatch(self):
        app = ast.parse((ROOT / "streamlit_app.py").read_text())
        imported = {(node.module, alias.name) for node in ast.walk(app)
                    if isinstance(node, ast.ImportFrom) for alias in node.names}
        self.assertIn(("src.verti_phone_ui", "render_verti_phone"), imported)
        self.assertIn(("src.vertical_options_ui", "render_vertical_options"), imported)
        routes = {}
        for node in ast.walk(app):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name) and node.test.left.id == "active_tab"
                    and len(node.test.comparators) == 1
                    and isinstance(node.test.comparators[0], ast.Constant)):
                routes[node.test.comparators[0].value] = {
                    call.func.id for statement in node.body for call in ast.walk(statement)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                }
        self.assertIn("render_verti_phone", routes["VERTI PHONE"])
        self.assertIn("render_vertical_options", routes["VERTICAL OPTIONS"])
        from src.production_manifest import FEATURE_ROUTES
        self.assertEqual(FEATURE_ROUTES["verti_phone"]["owner"], "src/verti_phone_ui.py")

    def test_saved_navigation_adds_phone_without_reordering_existing_phone(self):
        from src.tab_bar_v4 import _clean_order
        old = ["GEX", "VERTICAL OPTIONS", "HOLDINGS"]
        migrated = _clean_order(old)
        self.assertEqual(migrated[migrated.index("VERTICAL OPTIONS") + 1], "VERTI PHONE")
        self.assertEqual([item for item in migrated if item in old], old)
        self.assertEqual(_clean_order(migrated), migrated)
        chosen = ["VERTI PHONE", "GEX", "VERTICAL OPTIONS", "HOLDINGS"]
        self.assertEqual(_clean_order(chosen)[:3], chosen[:3])

    def test_phone_reuses_canonical_math_and_has_no_desktop_state_or_css_keys(self):
        self.assertIs(phone.build_vertical_preview_payload, math.build_vertical_preview_payload)
        self.assertIs(phone.build_place_payload, math.build_place_payload)
        self.assertIs(phone.scan_vertical_candidates, math.scan_vertical_candidates)
        source = (ROOT / "src/verti_phone_ui.py").read_text()
        strings = [node.value for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        self.assertFalse([value for value in strings if value.startswith(("vo_", "_vo_"))])
        self.assertNotIn(".st-key-vertical_options_workspace", source)
        self.assertNotIn(".vo-", source)
        self.assertIn(".st-key-verti_phone_workspace", source)
        self.assertIn('aria-label="PHONE ACCOUNTS"', source)
        self.assertNotIn('aria-label="Accounts"', source)

    def test_scan_and_presentation_recovery_never_share_desktop_or_order_state(self):
        vault = "synthetic-phone-test-" + str(uuid4())
        desktop_memory = desktop._scan_memory(vault)
        phone_memory = phone._scan_memory(vault)
        self.assertIsNot(desktop_memory, phone_memory)
        desktop_memory["vo_scan_result"] = {"symbol": "DESKTOP"}
        state = {"vp_scan_result": {"symbol": "PHONE"},
                 "vp_review_bundle": {"previews": "never-cache"}, "vp_live_confirm": True,
                 "vp_saved_spread_drafts": {"draft": "never-cache"},
                 "vo_scan_result": {"symbol": "DESKTOP-SESSION"}, "vo_live_confirm": True}
        untouched = {key: deepcopy(value) for key, value in state.items() if key.startswith("vo_")}
        with patch.object(phone, "st", order_ui(state)):
            phone._restore_scan_memory(vault)
            phone._restore_presentation()
            state["vp_symbol_value"] = "PHONE"
            phone._remember_scan_inputs()
            phone._remember_scan_snapshot()
        self.assertEqual({key: state[key] for key in untouched}, untouched)
        self.assertEqual(desktop_memory, {"vo_scan_result": {"symbol": "DESKTOP"}})
        restored = {}
        with patch.object(phone, "st", order_ui(restored)):
            phone._restore_scan_memory(vault)
            phone._restore_presentation()
        self.assertEqual(restored["vp_scan_result"], {"symbol": "PHONE"})
        self.assertEqual(restored["vp_symbol_value"], "PHONE")
        for key in ("vp_review_bundle", "vp_live_confirm", "vp_saved_spread_drafts", "vo_scan_result"):
            self.assertNotIn(key, restored)
        for anonymous in ("", "default"):
            with patch.object(phone, "st", order_ui({})):
                phone._restore_scan_memory(anonymous)
                self.assertNotIn("_vp_memory", phone.st.session_state)

    def test_browser_pricing_storage_and_component_names_are_separate(self):
        browser = (ROOT / "src/components/vp_pricing_preference_v1/index.html").read_text()
        original = (ROOT / "src/components/vo_pricing_preference_v1/index.html").read_text()
        key = lambda source: re.search(r'const KEY\s*=\s*"([^"]+)"', source).group(1)
        self.assertNotEqual(key(browser), key(original))
        self.assertIn("localStorage.getItem(KEY)", browser)
        self.assertIn("localStorage.setItem(KEY, value)", browser)
        source = (ROOT / "src/verti_phone_ui.py").read_text()
        self.assertIn('"raj_vp_spread_scroller_v1"', source)
        self.assertIn('"raj_vp_pricing_preference_v1"', source)
        self.assertNotIn('"raj_vo_', source)

    def test_mobile_scroller_keeps_all_canonical_fields_and_interaction_protocol(self):
        browser = (ROOT / "src/components/vp_spread_scroller_v1/index.html").read_text()
        original = (ROOT / "src/components/vo_spread_scroller_v1/index.html").read_text()
        fields = lambda source: json.loads(re.search(r"const KEYS\s*=\s*(\[[^;]+\]);", source).group(1))
        self.assertEqual(fields(browser), fields(original))
        self.assertIn("@media(max-width:1023px)", browser)
        self.assertIn('id="vp-filter-toggle"', browser)
        self.assertIn('aria-controls="head"', browser)
        self.assertIn('aria-expanded="false"', browser)
        self.assertIn("data-label", browser)
        self.assertIn("data-field", browser)
        for signal in ("selected_ids", "filter_revision", "column_filters", "viewport_offset", "viewport_sort_key"):
            self.assertIn(signal, browser)
        self.assertNotIn("localStorage", browser)
        self.assertNotIn("sessionStorage", browser)

    def test_pricing_hydrates_before_writing_without_using_desktop_preference(self):
        state = {"vo_pricing_mode": "SMART FILL", "vo_smart_fill": True,
                 "_vo_pricing_mode_preference": "SMART FILL"}
        before = deepcopy(state)
        ui = order_ui(state)
        ui.toggle.return_value = False
        with (patch.object(phone, "st", ui),
              patch.object(phone, "_PRICING_PREFERENCE_COMPONENT") as component):
            component.side_effect = [None, {"ready": True, "mode": "NORMAL", "available": True}, None]
            self.assertIsNone(phone._pricing_mode_flip_switch())
            self.assertEqual(component.call_count, 1)
            self.assertEqual(component.call_args.kwargs["action"], "read")
            self.assertEqual(phone._pricing_mode_flip_switch(), "NORMAL")
            self.assertEqual(component.call_args.kwargs["action"], "write")
            self.assertEqual(component.call_args.kwargs["value"], "NORMAL")
        self.assertEqual({key: state[key] for key in before}, before)


class PhoneAccountTests(unittest.TestCase):
    def test_defaults_wait_for_inventory_then_choose_5474_independently_of_other_tabs(self):
        state = {"etrade_accounts": ACCOUNTS, "risk_sizing_account": 0,
                 "vo_accounts": ["other"], "_vo_portfolio_account_selection": []}
        with patch.object(phone, "st", order_ui(state)):
            self.assertEqual(phone._selected_account_keys([]), [])
            self.assertNotIn("_vp_order_account_selection", state)
            self.assertEqual(phone._selected_account_keys(ACCOUNTS), ["preferred"])
            self.assertEqual(phone._default_account_keys(ACCOUNTS[:1]), ["other"])
        self.assertEqual(state["vo_accounts"], ["other"])
        self.assertEqual(state["_vo_portfolio_account_selection"], [])

    def test_explicit_empty_and_removed_accounts_survive_widget_cleanup(self):
        state = {"vp_accounts": ["other", "preferred"]}
        with patch.object(phone, "st", order_ui(state)):
            phone._remember_order_accounts()
            state.pop("vp_accounts")
            self.assertEqual(phone._selected_account_keys(ACCOUNTS), ["other", "preferred"])
            self.assertEqual(phone._selected_account_keys(ACCOUNTS[1:]), ["preferred"])
            state["vp_accounts"] = []
            phone._remember_order_accounts()
            state.pop("vp_accounts")
            self.assertEqual(phone._selected_account_keys(ACCOUNTS), [])
            self.assertEqual(phone._selected_account_keys([]), [])
            self.assertEqual(phone._selected_account_keys(ACCOUNTS), [])

    def test_empty_cash_selection_does_not_read_default_or_desktop_accounts(self):
        state = {"etrade_accounts": ACCOUNTS, "_vp_order_account_selection": [], "vo_accounts": ["other"]}
        client = MagicMock(is_offline=False)
        with patch.object(phone, "st", order_ui(state)):
            markup = phone._liquid_cash_markup(client, MagicMock())
        self.assertIn("UNAVAILABLE", markup)
        self.assertNotIn("x5474", markup)
        self.assertEqual(client.method_calls, [])

    def test_real_widget_cleanup_retains_explicit_order_account_choice(self):
        from streamlit.testing.v1 import AppTest
        source = '''
import streamlit as st
import src.verti_phone_ui as phone
accounts = [{"accountIdKey":"other","accountId":"10001234"},
            {"accountIdKey":"preferred","accountId":"10005474"}]
st.session_state.setdefault("vo_accounts", ["other"])
if not st.session_state.get("hide_phone"):
    st.session_state["vp_accounts"] = phone._selected_account_keys(accounts)
    st.multiselect("PHONE ACCOUNTS", ["other", "preferred"], key="vp_accounts",
                   on_change=phone._remember_order_accounts)
'''
        app = AppTest.from_string(source).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.multiselect(key="vp_accounts").value, ["preferred"])
        for selected in (["other", "preferred"], []):
            app.multiselect(key="vp_accounts").set_value(selected).run()
            app.session_state["hide_phone"] = True
            app.run()
            self.assertNotIn("vp_accounts", app.session_state)
            app.session_state["hide_phone"] = False
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.multiselect(key="vp_accounts").value, selected)
            self.assertEqual(app.session_state["vo_accounts"], ["other"])

    def test_production_phone_portfolio_retains_empty_choice_without_touching_desktop(self):
        from streamlit.testing.v1 import AppTest
        source = '''
import streamlit as st
from unittest.mock import patch
import src.verti_phone_ui as phone
class Broker:
    is_offline = False
    def get_balance(self, key):
        return {"Computed":{"cashAvailableForInvestment": 100}}
    def get_portfolio(self, key):
        st.session_state.setdefault("reads", []).append(key)
        return []
    def cache_age(self, *args):
        return 5
    def __getattr__(self, name):
        raise AssertionError("Unexpected broker action: " + name)
st.session_state.setdefault("etrade_accounts", [
    {"accountIdKey":"other","accountId":"10001234"},
    {"accountIdKey":"preferred","accountId":"10005474"}])
st.session_state.setdefault("_vp_workspace_ready", "default")
st.session_state.setdefault("_vp_pricing_mode_hydrated", True)
st.session_state.setdefault("_vp_pricing_mode_preference", "NORMAL")
st.session_state.setdefault("vo_review_bundle", {"desktop":"untouched"})
st.session_state.setdefault("vo_live_confirm", True)
st.session_state.setdefault("_vo_portfolio_account_selection", ["other"])
with (patch.object(phone, "_PRICING_PREFERENCE_COMPONENT", return_value=None),
      patch.object(phone, "hydrate_gex_snapshots", return_value=False)):
    if not st.session_state.get("hide_phone"):
        phone.render_verti_phone(Broker(), lambda: None)
'''
        app = AppTest.from_string(source, default_timeout=10).run()
        self.assertFalse(app.exception)
        app.button(key="vp_portfolio_open").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.multiselect(key="vp_portfolio_accounts").value, ["preferred"])
        self.assertEqual(app.session_state["reads"], ["preferred"])
        app.multiselect(key="vp_portfolio_accounts").set_value([]).run()
        app.session_state["hide_phone"] = True
        app.run()
        self.assertNotIn("vp_portfolio_accounts", app.session_state)
        app.session_state["hide_phone"] = False
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.multiselect(key="vp_portfolio_accounts").value, [])
        self.assertEqual(app.session_state["reads"], ["preferred"])
        self.assertEqual(app.session_state["vo_review_bundle"], {"desktop": "untouched"})
        self.assertTrue(app.session_state["vo_live_confirm"])
        self.assertEqual(app.session_state["_vo_portfolio_account_selection"], ["other"])


class PhoneOrderTests(unittest.TestCase):
    def setUp(self):
        self.candidate, self.rows = quoted_spread()
        self.result = {"source": "E*TRADE", "loaded_at": time.time(), "max_cost": 200,
                       "pricing_mode": "NORMAL", "scope": "synthetic-scope"}
        self.state = {"etrade_accounts": deepcopy(ACCOUNTS),
                      "_vp_order_account_selection": ["preferred"],
                      "vo_review_bundle": {"desktop": "approved"}, "vo_live_confirm": True,
                      "vo_saved_spread_drafts": {"desktop": "retained"}}
        self.desktop_state = {key: deepcopy(value) for key, value in self.state.items() if key.startswith("vo_")}
        self.client = MagicMock(is_offline=False)
        self.client.preview_order.return_value = {"PreviewOrderResponse": {"PreviewIds": [{"previewId": 123}]}}
        self.client.place_order.return_value = {"PlaceOrderResponse": {"OrderIds": [{"orderId": 456}]}}

    def render_order(self, clicked=(), *, rows=None, broker_live=True):
        ui = order_ui(self.state, clicked)
        with (patch.object(phone, "st", ui),
              patch.object(phone, "_chain", return_value=self.rows if rows is None else rows)):
            phone._render_selected_order(self.client, MagicMock(), self.candidate, self.result, broker_live)
        self.assertEqual({key: self.state[key] for key in self.desktop_state}, self.desktop_state)
        return ui

    def test_review_requotes_exact_legs_then_only_explicit_phone_confirmation_can_send(self):
        self.render_order()
        self.client.preview_order.assert_not_called()
        self.client.place_order.assert_not_called()
        self.render_order({"vp_preview"})
        self.client.preview_order.assert_called_once()
        account, payload = self.client.preview_order.call_args.args
        self.assertEqual(account, "preferred")
        order = payload["PreviewOrderRequest"]["Order"][0]
        self.assertEqual(order["priceType"], "NET_DEBIT")
        self.assertEqual(order["limitPrice"], self.candidate["debit"])
        self.assertEqual([leg["orderAction"] for leg in order["Instrument"]], ["BUY_OPEN", "SELL_OPEN"])
        self.assertNotIn("vp_live_confirm", self.state)
        self.render_order({"vp_send"})
        self.client.place_order.assert_not_called()
        self.state["vp_live_confirm"] = True
        self.render_order({"vp_send"})
        self.client.place_order.assert_called_once()
        placed_account, placed = self.client.place_order.call_args.args
        self.assertEqual(placed_account, account)
        self.assertEqual(placed["PlaceOrderRequest"]["Order"], [order])
        self.assertEqual(placed["PlaceOrderRequest"]["PreviewIds"], [{"previewId": 123}])
        self.assertNotIn("vp_review_bundle", self.state)
        self.assertNotIn("vp_live_confirm", self.state)
        self.assertEqual(self.state["vp_last_send_results"][0]["status"], "SUBMITTED")
        self.render_order({"vp_send"})
        self.assertEqual(self.client.place_order.call_count, 1)

    def test_two_account_review_sends_each_exact_preview_once(self):
        self.state["_vp_order_account_selection"] = ["other", "preferred"]
        self.client.preview_order.side_effect = [
            {"PreviewOrderResponse": {"PreviewIds": [{"previewId": 101}]}},
            {"PreviewOrderResponse": {"PreviewIds": [{"previewId": 202}]}},
        ]
        self.render_order({"vp_preview"})
        previews = {call.args[0]: call.args[1]["PreviewOrderRequest"]
                    for call in self.client.preview_order.call_args_list}
        self.client.place_order.assert_not_called()
        self.state["vp_live_confirm"] = True
        self.render_order({"vp_send"})
        self.assertEqual(self.client.place_order.call_count, 2)
        for call, preview_id in zip(self.client.place_order.call_args_list, (101, 202)):
            account, payload = call.args
            placed = payload["PlaceOrderRequest"]
            self.assertEqual(placed["Order"], previews[account]["Order"])
            self.assertEqual(placed["clientOrderId"], previews[account]["clientOrderId"])
            self.assertEqual(placed["PreviewIds"], [{"previewId": preview_id}])
        self.render_order({"vp_send"})
        self.assertEqual(self.client.place_order.call_count, 2)

    def test_uncertain_submission_is_reported_and_never_automatically_retried(self):
        self.render_order({"vp_preview"})
        self.state["vp_live_confirm"] = True
        self.client.place_order.side_effect = RuntimeError("synthetic transport interruption")
        self.render_order({"vp_send"})
        self.assertEqual(self.state["vp_last_send_results"][0]["status"], "UNCERTAIN / FAILED")
        self.assertNotIn("vp_review_bundle", self.state)
        self.assertNotIn("vp_live_confirm", self.state)
        self.render_order({"vp_send"})
        self.client.place_order.assert_called_once()

    def test_every_selected_account_must_preview_before_any_send(self):
        self.state["_vp_order_account_selection"] = ["other", "preferred"]
        self.client.preview_order.side_effect = [self.client.preview_order.return_value, RuntimeError("synthetic rejection")]
        ui = self.render_order({"vp_preview"})
        self.assertEqual(self.client.preview_order.call_count, 2)
        self.assertNotIn("vp_review_bundle", self.state)
        self.assertNotIn("vp_live_confirm", self.state)
        self.assertTrue(any("PREVIEW BLOCKED" in str(call) for call in ui.error.call_args_list))
        self.state["vp_live_confirm"] = True
        self.render_order({"vp_send"})
        self.client.place_order.assert_not_called()

    def test_explicit_empty_order_selection_cannot_preview_or_send(self):
        self.state["_vp_order_account_selection"] = []
        self.render_order({"vp_preview", "vp_send"})
        self.assertEqual(self.state["vp_accounts"], [])
        self.client.preview_order.assert_not_called()
        self.client.place_order.assert_not_called()
        self.assertNotIn("vp_review_bundle", self.state)

    def test_quantity_account_or_expired_preview_requires_new_review(self):
        for changed in ("quantity", "accounts", "expired"):
            with self.subTest(changed=changed):
                self.state.pop("vp_review_bundle", None)
                self.state["vp_qty"] = 1
                self.state["_vp_order_account_selection"] = ["preferred"]
                self.render_order({"vp_preview"})
                self.state["vp_live_confirm"] = True
                if changed == "quantity":
                    self.state["vp_qty"] = 2
                elif changed == "accounts":
                    self.state["_vp_order_account_selection"] = ["other"]
                else:
                    self.state["vp_review_bundle"]["previewed_at"] -= phone._PREVIEW_FRESH_SECONDS + 1
                self.render_order({"vp_send"})
                self.client.place_order.assert_not_called()
                self.assertNotIn("vp_review_bundle", self.state)
                self.assertNotIn("vp_live_confirm", self.state)

    def test_changed_market_or_contract_identity_blocks_preview(self):
        for rows in ([], [self.rows[0] | {"osi": "DIFFERENT-CONTRACT"}, self.rows[1]],
                     [self.rows[0] | {"ask": 3.3}, self.rows[1]]):
            with self.subTest(rows=rows):
                ui = self.render_order({"vp_preview"}, rows=rows)
                self.client.preview_order.assert_not_called()
                self.client.place_order.assert_not_called()
                self.assertTrue(any("LIVE SPREAD REQUOTE BLOCKED" in str(call) for call in ui.error.call_args_list))

    def test_public_stale_and_informational_rows_cannot_review_or_send(self):
        for blocked in ("public", "stale", "informational", "disconnected"):
            with self.subTest(blocked=blocked):
                original_result, original_candidate = deepcopy(self.result), deepcopy(self.candidate)
                if blocked == "public":
                    self.result["source"] = "YFINANCE"
                elif blocked == "stale":
                    self.result["loaded_at"] -= phone._SCAN_CACHE_SECONDS + 1
                elif blocked == "informational":
                    self.candidate["orderable"] = False
                self.render_order({"vp_preview", "vp_send"}, broker_live=blocked != "disconnected")
                self.client.preview_order.assert_not_called()
                self.client.place_order.assert_not_called()
                self.result, self.candidate = original_result, original_candidate

    def test_scans_drafts_and_portfolio_do_not_preview_place_or_mutate_desktop(self):
        ui = order_ui(self.state)
        self.client.get_quote.return_value = {"QuoteResponse": {"QuoteData": [{"All": {"lastTrade": 100}, "Product": {"symbol": "SPY"}}]}}
        self.client.get_portfolio.return_value = []
        self.client.cache_age.return_value = 5
        with (patch.object(phone, "st", ui),
              patch.object(phone, "_expirations", return_value=[self.candidate["expiry"]]),
              patch.object(phone, "_chain", return_value=self.rows),
              patch.object(phone, "record_lookup"), patch.object(phone, "company_name", return_value="Synthetic")):
            raw = phone._scan_all_years(self.client, "SPY", MagicMock())
            self.assertTrue(raw["scan_complete"])
            self.assertEqual(raw["rows"], self.rows)
            drafts = {}
            self.assertEqual(phone._save_checked_spreads(drafts, [self.candidate], "E*TRADE"), 1)
            self.assertEqual(phone._save_checked_spreads(drafts, [self.candidate], "E*TRADE"), 0)
            self.assertIn("TERMINAL SESSION DRAFT - NOT SENT", phone._saved_drafts_csv(drafts))
            snapshots = phone._load_portfolio_verticals(self.client, MagicMock(), ACCOUNTS)
            self.assertEqual(len(snapshots), 2)
            self.assertTrue(all(not snapshot["error"] for snapshot in snapshots))
        self.client.preview_order.assert_not_called()
        self.client.place_order.assert_not_called()
        self.assertNotIn("_vo_chain_cache", self.state)
        self.assertEqual({key: self.state[key] for key in self.desktop_state}, self.desktop_state)


class VerticalCostFilterTests(unittest.TestCase):
    def test_phone_defaults_have_no_cost_ceiling_and_preserve_expiry_and_target_defaults(self):
        expiries = ["2027-01-15", "2028-01-21", "2029-01-19", "2028-06-16"]
        expected = {"expiry": ["2028-01-21", "2028-06-16", "2029-01-19"]}
        self.assertEqual(phone._default_column_filters(expiries), expected)
        self.assertEqual(phone._default_column_filters(expiries, "103"),
                         {**expected, "breakeven": {"target": 103}})

    def test_both_renderers_use_only_column_max_over_the_complete_store(self):
        import csv
        import io
        from streamlit.testing.v1 import AppTest
        for module, prefix, renderer in (("vertical_options_ui", "vo", "render_vertical_options"),
                                          ("verti_phone_ui", "vp", "render_verti_phone")):
            with self.subTest(module=module):
                vault = "cost-filter-test-" + uuid4().hex
                source = '''
import time
from datetime import date
from functools import partial as deferred
from unittest.mock import patch
import streamlit as st
import src.FEATURE_MODULE as feature
vault = "FIXTURE_VAULT"
class Broker:
    is_offline = False
    def get_balance(self, key):
        return {"Computed":{"cashAvailableForInvestment":1000}}
    def __getattr__(self, name):
        raise AssertionError("Unexpected broker action: " + name)
def snapshot(scan_id):
    expiries = [date(date.today().year + offset + scan_id, 1, 15) for offset in range(3)]
    rows = []
    for expiry in expiries:
        for strike, premium in ((100,3), (105,2), (110,1)):
            rows.append({"expiry":expiry,"call_put":"CALL","strike":strike,
                         "last":premium,"bid":premium-.05,"ask":premium+.05,
                         "osi":f"QQQ-{expiry}-{strike}"})
    return {"scan_id":scan_id,"loaded_at":time.time(),"symbol":"QQQ",
            "source":"E*TRADE","spot":100,"name":"Synthetic QQQ",
            "expirations":expiries,"loaded_expirations":len(expiries),"rows":rows,
            "errors":[],"failed_expirations":[],"next_expiry_index":len(expiries),
            "scan_complete":True,"result_stores":{}}
def component(**args):
    st.session_state["fixture_args"] = args
    return st.session_state.pop("fixture_update", None)
def scan(client, symbol, touch, *, refresh=False):
    raw = st.session_state["_FEATURE_PREFIX_chain_cache"]["E*TRADE:QQQ"]
    if st.session_state.pop("fixture_new_raw", False):
        raw = snapshot(raw["scan_id"] + 1)
        st.session_state["_FEATURE_PREFIX_chain_cache"] = {"E*TRADE:QQQ":raw}
    st.session_state.setdefault("fixture_scan_calls", []).append((symbol, refresh, raw["scan_id"]))
    return raw
original_rank = feature._rank_cached_vertical_chains
def rank(raw, target, max_cost, mode, minimum=2):
    st.session_state.setdefault("fixture_rank_caps", []).append(max_cost)
    return original_rank(raw, target, max_cost, mode, minimum)
def exporter(function, *args, **kwargs):
    if function is feature._qualified_store_csv:
        st.session_state["fixture_csv"] = function(*args, **kwargs)
        st.session_state["fixture_export_cap"] = kwargs["max_cost"]
    return deferred(function, *args, **kwargs)
def order(client, touch, candidate, result, broker_live):
    st.session_state["fixture_order_cap"] = result["max_cost"]
    st.session_state["fixture_order_is_scan"] = result is st.session_state["FEATURE_PREFIX_scan_result"]
st.session_state.setdefault("etrade_accounts", [{"accountIdKey":"synthetic","accountId":"10005474"}])
st.session_state.setdefault("_FEATURE_PREFIX_workspace_ready", vault)
st.session_state.setdefault("_FEATURE_PREFIX_pricing_mode_hydrated", True)
st.session_state.setdefault("_FEATURE_PREFIX_pricing_mode_preference", "NORMAL")
st.session_state.setdefault("fixture_rank_caps", [])
if (not st.session_state.get("fixture_restore_only")
        and "FEATURE_PREFIX_scan_result" not in st.session_state
        and not st.session_state.get("_FEATURE_PREFIX_scan_pending")):
    raw = snapshot(1)
    store, annual, _, _ = feature._rank_cached_vertical_chains(raw, None, None, "NORMAL")
    st.session_state["_FEATURE_PREFIX_chain_cache"] = {"E*TRADE:QQQ":raw}
    st.session_state["FEATURE_PREFIX_symbol_value"] = "QQQ"
    # A retired top-field preference must never act as a hidden ranking cap.
    st.session_state["_FEATURE_PREFIX_presentation"] = {"FEATURE_PREFIX_max_cost":"75"}
    st.session_state["FEATURE_PREFIX_scan_result"] = {
        "symbol":"QQQ","source":"E*TRADE","spot":100,"target":None,
        "max_cost":None,"pricing_mode":"NORMAL","raw_scan_id":1,"scope":"initial",
        "loaded_at":raw["loaded_at"],"candidates":store,"annual_ids":annual,
        "listed_years":3,"loaded_expirations":3,"listed_expirations":3,
        "contract_count":len(raw["rows"]),"ranked_expirations":3,"scan_complete":True,"errors":[]}
with (patch.object(feature, "_SPREAD_SCROLL_COMPONENT", side_effect=component),
      patch.object(feature, "_PRICING_PREFERENCE_COMPONENT", return_value=None),
      patch.object(feature, "hydrate_gex_snapshots", return_value=False),
      patch.object(feature, "_cboe_wall_markup", return_value="<span></span>"),
      patch.object(feature, "_ticker_quote_preview", return_value={"last":100,"name":"Synthetic QQQ"}),
      patch.object(feature, "_scan_all_years", side_effect=scan),
      patch.object(feature, "_rank_cached_vertical_chains", side_effect=rank),
      patch.object(feature, "_render_selected_order", side_effect=order),
      patch.object(feature, "partial", side_effect=exporter)):
    feature.FEATURE_RENDER(Broker(), lambda: None, vault_key=vault)
'''.replace("FEATURE_MODULE", module).replace("FEATURE_PREFIX", prefix).replace("FEATURE_RENDER", renderer).replace("FIXTURE_VAULT", vault)
                app = AppTest.from_string(source, default_timeout=15).run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                result_key = prefix + "_scan_result"
                filter_key = "_" + prefix + "_column_filters"
                self.assertEqual([field.label for field in app.text_input],
                                 ["Ticker", "MIN RISK:REWARD 1:"])
                self.assertNotIn(prefix + "_max_cost", app.session_state)
                self.assertNotIn("cost", args["column_filters"])
                self.assertIsNone(app.session_state[result_key]["max_cost"])
                self.assertNotIn("scan_max_cost", app.session_state[filter_key])
                store = app.session_state[result_key]["candidates"]
                self.assertEqual(len(store), 9)
                self.assertTrue(any(row["cost"] > 150 for row in store.iter_rows()))
                selected_id = next(row["id"] for row in store.iter_rows() if row["cost"] < 150)
                app.session_state["fixture_update"] = {
                    "scope": args["scope"], "column_filters": args["column_filters"], "filter_revision": 0,
                    "selected_ids": [selected_id], "revision": 1}
                app.run()
                self.assertFalse(app.exception)
                self.assertIsNone(app.session_state["fixture_order_cap"])
                self.assertFalse(app.session_state["fixture_order_is_scan"])
                app.session_state[prefix + "_review_bundle"] = {"current": True}
                app.session_state[prefix + "_live_confirm"] = True
                edited = {**args["column_filters"], "side": ["CALL"],
                          "breakeven": {"target": 103}, "cost": {"min": 75, "max": 150}}
                # Desktop migrates target-only updates to its requested ±5% band;
                # Verti Phone preserves its original target-only ranking contract.
                expected_edited = ({**edited, "breakeven": {"target": 103, "percent": 5.0}}
                                   if prefix == "vo" else edited)
                rank_caps = list(app.session_state["fixture_rank_caps"])
                app.session_state["fixture_update"] = {
                    "scope": args["scope"], "column_filters": edited, "filter_revision": 1,
                    "selected_ids": [selected_id], "revision": 1}
                app.run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual(args["column_filters"], expected_edited)
                self.assertLess(args["total_rows"], 6)
                self.assertEqual(len(store), 9)
                self.assertEqual(app.session_state["fixture_rank_caps"], rank_caps)
                self.assertNotIn("fixture_scan_calls", app.session_state)
                self.assertEqual(app.session_state["fixture_order_cap"], 150)
                self.assertFalse(app.session_state["fixture_order_is_scan"])
                self.assertNotIn(prefix + "_review_bundle", app.session_state)
                self.assertNotIn(prefix + "_live_confirm", app.session_state)
                self.assertIsNone(app.session_state[result_key]["max_cost"])
                exported = list(csv.DictReader(io.StringIO(app.session_state["fixture_csv"])))
                self.assertEqual(len(exported), args["total_rows"])
                self.assertTrue(all(float(row["Cost Per Contract ($)"]) <= 150 for row in exported))
                self.assertTrue(all(float(row["Max Cost Filter ($)"]) == 150 for row in exported))
                self.assertEqual(app.session_state["fixture_export_cap"], 150)
                for revision, cap in enumerate((0, -1, 150), 2):
                    args = app.session_state["fixture_args"]
                    app.session_state["fixture_update"] = {
                        "scope": args["scope"], "column_filters": {**edited, "cost": {"min": 75, "max": cap}},
                        "filter_revision": revision, "selected_ids": [selected_id], "revision": 1}
                    app.run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.session_state["fixture_order_cap"], cap)
                    self.assertEqual(app.session_state["fixture_export_cap"], cap)
                    self.assertIsNone(app.session_state[result_key]["max_cost"])
                    if cap <= 0:
                        self.assertEqual(app.session_state["fixture_args"]["total_rows"], 0)
                app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["column_filters"], expected_edited)
                self.assertNotIn("fixture_scan_calls", app.session_state)
                self.assertTrue(all(cap is None for cap in app.session_state["fixture_rank_caps"]))

                # Clearing MAX reveals the retained expensive pairs without downloading chains.
                args = app.session_state["fixture_args"]
                without_max = {**edited, "cost": {"min": 75}}
                app.session_state["fixture_update"] = {
                    "scope": args["scope"], "column_filters": without_max, "filter_revision": 1,
                    "selected_ids": [selected_id], "revision": 1}
                app.run()
                self.assertIsNone(app.session_state["fixture_order_cap"])
                app.toggle(key=prefix + "_smart_fill").set_value(False).run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual(args["column_filters"], without_max)
                self.assertEqual(args["total_rows"], 6)
                self.assertIsNone(app.session_state["fixture_export_cap"])
                exported = list(csv.DictReader(io.StringIO(app.session_state["fixture_csv"])))
                self.assertTrue(any(float(row["Cost Per Contract ($)"]) > 150 for row in exported))
                self.assertTrue(all(row["Max Cost Filter ($)"] == "" for row in exported))
                self.assertNotIn("fixture_scan_calls", app.session_state)
                restored = AppTest.from_string(source, default_timeout=15)
                restored.session_state["fixture_restore_only"] = True
                restored.run()
                self.assertFalse(restored.exception)
                self.assertEqual(restored.session_state["fixture_args"]["column_filters"], without_max)

                # A user-triggered scan remains uncapped, with fresh display defaults.
                app.session_state["fixture_new_raw"] = True
                next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertNotIn("cost", args["column_filters"])
                self.assertNotIn("side", args["column_filters"])
                self.assertEqual(app.session_state[result_key]["raw_scan_id"], 2)
                self.assertIsNone(app.session_state[result_key]["max_cost"])
                self.assertTrue(all(cap is None for cap in app.session_state["fixture_rank_caps"]))

                # Capped legacy snapshots require an explicit rescan and cannot resume or trade.
                legacy = dict(app.session_state[result_key])
                legacy.update(max_cost=150, scan_complete=False)
                app.session_state[result_key] = legacy
                app.session_state[prefix + "_review_bundle"] = {"legacy": True}
                app.session_state[prefix + "_live_confirm"] = True
                app.session_state["_" + prefix + "_order_candidate"] = "legacy"
                del app.session_state["fixture_args"]
                scans_before = list(app.session_state["fixture_scan_calls"])
                ranks_before = list(app.session_state["fixture_rank_caps"])
                app.run()
                self.assertFalse(app.exception)
                self.assertTrue(any("SCAN ALL YEARS" in message.value for message in app.info))
                self.assertNotIn("fixture_args", app.session_state)
                self.assertNotIn(prefix + "_review_bundle", app.session_state)
                self.assertNotIn(prefix + "_live_confirm", app.session_state)
                self.assertNotIn("_" + prefix + "_order_candidate", app.session_state)
                self.assertEqual(app.session_state["fixture_scan_calls"], scans_before)
                self.assertEqual(app.session_state["fixture_rank_caps"], ranks_before)
                self.assertFalse(any(button.label.startswith("CONTINUE ALL YEARS") for button in app.button))
                next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
                self.assertFalse(app.exception)
                self.assertIsNone(app.session_state[result_key]["max_cost"])
                self.assertIn("fixture_args", app.session_state)
                self.assertEqual(len(app.session_state["fixture_scan_calls"]), len(scans_before) + 1)

    def test_effective_column_max_changes_invalidate_review_identity_in_both_owners(self):
        candidate, _ = quoted_spread()
        kwargs = dict(symbol=candidate["symbol"], call_put=candidate["call_put"],
                      expiry=candidate["expiry"], buy_strike=candidate["buy_strike"],
                      sell_strike=candidate["sell_strike"], quantity=1, limit_price=candidate["debit"],
                      min_reward_risk=2, order_term="GOOD_FOR_DAY", account_keys=["synthetic"])
        for owner in (desktop, phone):
            with self.subTest(owner=owner.__name__):
                reviewed = owner._review_fingerprint(**kwargs, max_cost=150)
                bundle = {"fingerprint": reviewed, "previewed_at": time.time()}
                self.assertTrue(owner._reviews_are_fresh(bundle, reviewed))
                for changed in (None, 0, 200):
                    self.assertFalse(owner._reviews_are_fresh(
                        bundle, owner._review_fingerprint(**kwargs, max_cost=changed)))


if __name__ == "__main__":
    unittest.main()
