"""Independent R.I.C. interaction, four-leg links and reviewed-order regressions."""
from copy import deepcopy
from contextlib import contextmanager
import csv
from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import sys
import time
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import unquote
import zipfile

from src import reverse_iron_condor_ui as ui
from src.reverse_iron_condor import RIC_PRICING_POLICY, scan_ric_candidates
from src.etrade_data_cache import session_namespace
from src.etrade_client import ETradeError


ACCOUNTS = [
    {"accountIdKey": "other", "accountId": "10001234", "accountDesc": "Other synthetic"},
    {"accountIdKey": "preferred", "accountId": "10005474", "accountDesc": "Preferred synthetic"},
]


def quoted_condor(width=5, fee=0):
    expiry = date.today() + timedelta(days=45)
    specs = [("PUT", 95-width, 1), ("PUT", 95, 1.5),
             ("CALL", 105, 1.5), ("CALL", 105+width, 1)]
    rows = [dict(expiry=expiry, call_put=side, strike=strike, last=last,
                 bid=last-.05, ask=last+.05, open_interest=1000, volume=100,
                 osi=f"SPY{expiry:%y%m%d}{side[0]}{strike*1000:08d}")
            for side, strike, last in specs]
    candidates, _ = scan_ric_candidates(symbol="SPY", spot_last=100, option_rows=rows,
                                        target_breakeven=None, max_cost=None,
                                        max_per_expiration=None, fee_per_spread=fee)
    return candidates[0], rows


def order_ui(state, clicked=()):
    view = MagicMock()
    view.session_state = state
    view.button.side_effect = lambda label, *, key, disabled=False, **kwargs: key in clicked and not disabled
    view.checkbox.side_effect = lambda label, *, key, **kwargs: state.get(key, False)
    view.number_input.side_effect = lambda label, *, key, **kwargs: state.setdefault(key, 1)
    view.selectbox.side_effect = lambda label, options, *, key, **kwargs: state.setdefault(key, options[0])
    view.multiselect.side_effect = lambda label, options, *, key, **kwargs: list(state.get(key, []))
    view.columns.side_effect = lambda widths, **kwargs: [view] * (widths if isinstance(widths, int) else len(widths))
    return view


def result(fee=0):
    return {"pricing_policy": RIC_PRICING_POLICY, "loaded_at": time.time(),
            "source": "E*TRADE", "max_cost": None, "min_reward_risk": 2,
            "pricing_mode": "NORMAL", "fee_per_spread": fee}


class LocalAccessCompatibilityTests(unittest.TestCase):
    def view(self, *, unlocked=False):
        view = MagicMock()
        view.session_state = {"etrade_access_unlocked": unlocked}
        view.fragment.side_effect = lambda render, **options: render
        view.dialog.side_effect = lambda title, **options: lambda render: render
        view.stop.side_effect = PermissionError("Terminal locked")
        return view

    def test_legacy_unlocked_contract_does_not_require_an_oauth_session(self):
        view = self.view(unlocked=True)
        with patch.object(ui, "st", view), patch.dict(sys.modules, {"src.security_runtime": None}):
            self.assertIsNone(ui._require_ric_access())
        view.rerun.assert_not_called()
        view.stop.assert_not_called()
        self.assertEqual(view.session_state, {"etrade_access_unlocked": True})

    def test_locked_entry_stops_before_feature_render_or_broker_calls(self):
        view, broker = self.view(), MagicMock()
        with (patch.object(ui, "st", view), patch.dict(sys.modules, {"src.security_runtime": None}),
              patch.object(ui, "_render_reverse_iron_condor_fragment") as render):
            with self.assertRaises(PermissionError):
                ui.render_reverse_iron_condor(broker, lambda: None)
        view.rerun.assert_called_once_with(scope="app")
        view.html.assert_not_called()
        render.assert_not_called()
        self.assertEqual(broker.mock_calls, [])

    def test_locked_callback_fragment_and_dialog_never_enter_their_bodies(self):
        for boundary in ("callback", "fragment", "dialog"):
            with self.subTest(boundary=boundary):
                view, called = self.view(), []
                def body():
                    called.append("financial work")
                with patch.object(ui, "st", view), patch.dict(sys.modules, {"src.security_runtime": None}):
                    decorate = (ui.finance_callback if boundary == "callback" else
                                ui.finance_fragment if boundary == "fragment" else
                                ui.finance_dialog("Synthetic portfolio"))
                    protected = decorate(body)
                    with self.assertRaises(PermissionError):
                        protected()
                self.assertEqual(called, [])
                view.rerun.assert_called_once_with(scope="app")

    def test_unlocked_local_wrappers_preserve_options_arguments_and_results(self):
        view = self.view(unlocked=True)
        def body(value):
            return value + 1
        with patch.object(ui, "st", view), patch.dict(sys.modules, {"src.security_runtime": None}):
            self.assertEqual(ui.finance_callback(body)(2), 3)
            self.assertEqual(ui.finance_fragment(run_every=1)(body)(3), 4)
            self.assertEqual(ui.finance_dialog("Synthetic portfolio", width="large")(body)(4), 5)
        self.assertEqual(view.fragment.call_args.kwargs, {"run_every": 1})
        view.dialog.assert_called_once_with("Synthetic portfolio", width="large")
        view.rerun.assert_not_called()

    def test_optional_finance_guard_is_authoritative_and_never_touches_idle_time(self):
        guard = ModuleType("src.security_runtime")
        principal = object()
        guard.require_finance_access = MagicMock(return_value=principal)
        view = self.view()
        with patch.object(ui, "st", view), patch.dict(sys.modules, {"src.security_runtime": guard}):
            self.assertIs(ui._require_ric_access(), principal)
        guard.require_finance_access.assert_called_once_with(touch=False)
        view.rerun.assert_not_called()

    def test_optional_guard_denial_cannot_fall_back_to_legacy_unlocked_boolean(self):
        guard = ModuleType("src.security_runtime")
        guard.require_finance_access = MagicMock(side_effect=PermissionError("MFA revoked"))
        view, called = self.view(unlocked=True), []
        def body():
            called.append("financial work")
        with patch.object(ui, "st", view), patch.dict(sys.modules, {"src.security_runtime": guard}):
            with self.assertRaisesRegex(PermissionError, "MFA revoked"):
                ui.finance_callback(body)()
        self.assertEqual(called, [])
        view.rerun.assert_not_called()

    def test_missing_dependency_of_present_security_module_fails_closed(self):
        view, original_import = self.view(unlocked=True), __import__
        def broken_import(name, *args, **kwargs):
            if name == "src.security_runtime":
                raise ModuleNotFoundError("Security storage dependency missing", name="src.security_store")
            return original_import(name, *args, **kwargs)
        with patch.object(ui, "st", view), patch("builtins.__import__", side_effect=broken_import):
            with self.assertRaises(ModuleNotFoundError) as raised:
                ui._require_ric_access()
        self.assertEqual(raised.exception.name, "src.security_store")
        view.rerun.assert_not_called()


class QuoteLinksAndExportTests(unittest.TestCase):
    def test_four_legs_two_breakevens_and_both_profits_reach_table(self):
        candidate, _ = quoted_condor()
        row = ui._spread_table_row(candidate, {candidate["id"]})
        self.assertEqual([leg["label"] for leg in row["ric_legs"]], ["P BUY", "P SELL", "C BUY", "C SELL"])
        self.assertEqual([leg["strike"] for leg in row["ric_legs"]], [95, 90, 105, 110])
        self.assertTrue(all(leg["open_interest"] == 1000 for leg in row["ric_legs"]))
        self.assertEqual(row["lower_breakeven"], candidate["lower_breakeven"])
        self.assertEqual(row["downside_profit"], candidate["downside_profit"])
        self.assertEqual(row["upside_profit"], candidate["upside_profit"])

    def test_optionstrat_and_tradingview_have_exact_four_actions(self):
        c, _ = quoted_condor()
        osi_date = c["expiry"].strftime("%y%m%d")
        self.assertIn("/build/inverse-iron-condor/SPY/", ui._optionstrat_url(c))
        self.assertTrue(ui._optionstrat_url(c).endswith(
            f"-.SPY{osi_date}P90,.SPY{osi_date}P95,.SPY{osi_date}C105,-.SPY{osi_date}C110"))
        formula = f"OPRA:SPY{osi_date}P95.0-OPRA:SPY{osi_date}P90.0+OPRA:SPY{osi_date}C105.0-OPRA:SPY{osi_date}C110.0"
        self.assertEqual(ui._tradingview_spread_formula(c), formula)
        self.assertIn(formula, unquote(ui._tradingview_spread_url(c)))
        broken = c | {"put_buy_osi": c["buy_osi"]}
        self.assertEqual(ui._tradingview_spread_formula(broken), "")
        self.assertEqual(ui._optionstrat_url(broken), "")

    def test_full_csv_excel_and_drafts_retain_put_legs_and_fees(self):
        c, _ = quoted_condor(fee=2.6)
        options = dict(source="SYNTHETIC", loaded_at=1, target=None, max_cost=None)
        data = list(csv.DictReader(io.StringIO(ui._qualified_spreads_csv([c], set(), **options))))
        self.assertEqual(len(data), 1)
        for key in ("Long Put Strike", "Short Put Strike", "Long Call Strike", "Short Call Strike",
                    "Lower Breakeven ($)", "Upper Breakeven ($)", "Downside Profit ($)", "Upside Profit ($)",
                    "OptionStrat URL", "TradingView URL"):
            self.assertTrue(data[0][key], key)
        self.assertEqual(float(data[0]["Estimated Fees Per RIC ($)"]), 2.6)
        book = ui._qualified_spreads_xlsx([c], set(), **options)
        with zipfile.ZipFile(io.BytesIO(book)) as archive:
            content = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Long Put Strike", content)
        self.assertIn("Short Call OSI", content)
        self.assertIn("Lower Breakeven", content)
        drafts = {}
        self.assertEqual(ui._save_checked_spreads(drafts, [c], "SYNTHETIC"), 1)
        self.assertEqual(ui._save_checked_spreads(drafts, [c], "SYNTHETIC"), 0)
        draft = list(csv.DictReader(io.StringIO(ui._saved_drafts_csv(drafts))))[0]
        self.assertEqual(draft["Long Put Strike"], "95.0")
        self.assertEqual(draft["Quote Snapshot"], "")
        self.assertEqual(draft["Status"], "TERMINAL SESSION DRAFT - NOT SENT")

    def test_minimum_is_strict_and_fee_estimate_nonnegative(self):
        self.assertEqual(ui._entered_reward_risk("1:2"), 2)
        self.assertEqual(ui._entered_reward_risk("3"), 3)
        for value in ("1.99", "0", "nan", "inf", "-1"):
            with self.assertRaises(ValueError, msg=value):
                ui._entered_reward_risk(value)
        self.assertEqual(ui._entered_fee("0"), 0)
        self.assertEqual(ui._entered_fee("$2.60"), 2.6)
        for value in ("-1", "nan", "inf", ""):
            with self.assertRaises(ValueError):
                ui._entered_fee(value)

    def test_account_defaults_never_borrow_risk_and_keep_empty_cash(self):
        state = {"etrade_accounts": ACCOUNTS, "risk_sizing_account": 0, "_ric_order_accounts_memory": []}
        broker = MagicMock()
        with patch.object(ui.st, "session_state", state):
            self.assertEqual(ui._default_account_keys(ACCOUNTS), ["preferred"])
            self.assertIn("UNAVAILABLE", ui._liquid_cash_markup(broker, lambda: None))
        broker.get_balance.assert_not_called()


class ReviewedOrderTests(unittest.TestCase):
    def setup_order(self, width=5, accounts=None):
        c, rows = quoted_condor(width)
        state = {"etrade_accounts": ACCOUNTS, "ric_accounts": accounts if accounts is not None else ["preferred"]}
        broker = MagicMock(is_offline=False)
        broker.preview_order.return_value = {"PreviewOrderResponse": {"PreviewIds": [{"previewId": 123}], "estimatedCommission": 0, "estimatedFees": 0}}
        broker.place_order.return_value = {"PlaceOrderResponse": {"OrderIds": [{"orderId": 456}]}}
        return c, rows, state, broker, result()

    def render(self, c, rows, state, broker, scan, clicked=()):
        view = order_ui(state, clicked)
        with patch.object(ui, "st", view), patch.object(ui, "_chain", return_value=rows):
            ui._render_selected_order(broker, lambda: None, c, scan, True)
        return view

    def test_idle_and_empty_accounts_never_preview_or_place(self):
        c, rows, state, broker, scan = self.setup_order(accounts=[])
        self.render(c, rows, state, broker, scan, clicked=("ric_preview", "ric_send"))
        self.assertEqual(state["ric_accounts"], [])
        broker.preview_order.assert_not_called()
        broker.place_order.assert_not_called()

    def test_preview_all_accounts_four_legs_then_confirmation_and_exact_send(self):
        c, rows, state, broker, scan = self.setup_order(accounts=["preferred", "other"])
        self.render(c, rows, state, broker, scan, clicked=("ric_preview",))
        self.assertEqual(broker.preview_order.call_count, 2)
        broker.place_order.assert_not_called()
        first_preview = broker.preview_order.call_args_list[0].args[1]["PreviewOrderRequest"]["Order"]
        instruments = first_preview[0]["Instrument"]
        self.assertEqual([(leg["Product"]["callPut"], leg["Product"]["strikePrice"], leg["orderAction"]) for leg in instruments],
                         [("PUT",90,"SELL_OPEN"),("PUT",95,"BUY_OPEN"),("CALL",105,"BUY_OPEN"),("CALL",110,"SELL_OPEN")])
        self.render(c, rows, state, broker, scan, clicked=("ric_send",))
        broker.place_order.assert_not_called()
        state["ric_live_confirm"] = True
        self.render(c, rows, state, broker, scan, clicked=("ric_send",))
        self.assertEqual(broker.place_order.call_count, 2)
        self.assertEqual(broker.place_order.call_args_list[0].args[1]["PlaceOrderRequest"]["Order"], first_preview)
        self.assertNotIn("ric_review_bundle", state)

    def test_actual_commission_blocks_prefee_two_to_one_boundary(self):
        c, rows, state, broker, scan = self.setup_order(width=3)
        self.assertAlmostEqual(c["reward_risk"], 2)
        broker.preview_order.return_value["PreviewOrderResponse"]["estimatedCommission"] = .01
        view = self.render(c, rows, state, broker, scan, clicked=("ric_preview",))
        self.assertNotIn("ric_review_bundle", state)
        self.assertTrue(any("commission" in str(call).lower() for call in view.warning.call_args_list))
        broker.place_order.assert_not_called()

    def test_broker_fees_alone_reject_two_to_one_preview_for_all_accounts(self):
        c, rows, state, broker, scan = self.setup_order(width=3, accounts=["preferred", "other"])
        broker.preview_order.return_value = {"PreviewOrderResponse": {
            "PreviewIds":[{"previewId":123}], "totalCommission":0,
            "Order":[{"estimatedCommission":0,"estimatedFees":.01,"estimatedTotalAmount":100.01}]}}
        view = self.render(c,rows,state,broker,scan,clicked=("ric_preview",))
        self.assertEqual(broker.preview_order.call_count,2)
        self.assertNotIn("ric_review_bundle",state)
        self.assertTrue(any("fees" in str(call).lower() for call in view.warning.call_args_list))
        broker.place_order.assert_not_called()

    def test_authoritative_broker_charges_do_not_double_count_repeated_leg_fields(self):
        payload = {"PreviewOrderResponse":{"PreviewIds":[{"previewId":123}],
                   "totalOrderValue":109.1,"totalCommission":2,
                   "Order":[{"estimatedCommission":2,"estimatedFees":.1,"estimatedTotalAmount":109.1,
                             "Instrument":[{"estimatedCommission":2,"estimatedFees":.1} for _ in range(4)]}]}}
        details = ui._ric_preview_details(payload,gross_debit=107)
        self.assertAlmostEqual(details["estimated_total_charges"],2.1)
        self.assertEqual(details["estimated_commission"],2)
        self.assertEqual(details["estimated_fees"],.1)
        # Official spread response's total contains a charge beyond commission.
        payload["PreviewOrderResponse"].update(totalOrderValue=508.4762,totalCommission=8.44)
        payload["PreviewOrderResponse"]["Order"] = [{"estimatedCommission":8.44,"estimatedTotalAmount":508.4762}]
        details = ui._ric_preview_details(payload,gross_debit=500)
        self.assertAlmostEqual(details["estimated_total_charges"],8.4762)

    def test_missing_or_partial_atomic_charges_block_every_account_even_with_scan_estimate(self):
        incomplete = ({}, {"estimatedCommission":0}, {"estimatedFees":0},
                      {"totalCommission":2.6}, {"totalFees":.1},
                      {"Order":[{"Instrument":[{"estimatedCommission":0,"estimatedFees":0} for _ in range(4)]}]})
        for scan_fee in (0, 10):
            for evidence in incomplete:
                with self.subTest(scan_fee=scan_fee,evidence=evidence):
                    c,rows,state,broker,scan=self.setup_order(accounts=["preferred","other"])
                    scan["fee_per_spread"]=scan_fee
                    broker.preview_order.return_value = {"PreviewOrderResponse": {
                        "PreviewIds":[{"previewId":123}], **evidence}}
                    view=self.render(c,rows,state,broker,scan,clicked=("ric_preview",))
                    self.assertEqual(broker.preview_order.call_count,2)
                    self.assertNotIn("ric_review_bundle",state)
                    self.assertNotIn("ric_live_confirm",state)
                    self.assertTrue(any("complete opening charges" in str(call) for call in view.warning.call_args_list))
                    broker.place_order.assert_not_called()

    def test_complete_zero_or_total_derived_charges_allow_preview_without_sending(self):
        for evidence,expected in (({"estimatedCommission":0,"estimatedFees":0},0),
                                  ({"totalOrderValue":107},0),
                                  ({"Order":[{"estimatedTotalAmount":109.7}]},2.7)):
            with self.subTest(evidence=evidence):
                c,rows,state,broker,scan=self.setup_order()
                self.assertAlmostEqual(c["debit"]*100,107)
                broker.preview_order.return_value = {"PreviewOrderResponse": {
                    "PreviewIds":[{"previewId":123}], **evidence}}
                self.render(c,rows,state,broker,scan,clicked=("ric_preview",))
                details=state["ric_review_bundle"]["previews"]["preferred"]["details"]
                self.assertAlmostEqual(details["estimated_total_charges"],expected)
                broker.place_order.assert_not_called()

    def test_total_evidence_preserves_larger_partial_charge_lower_bound(self):
        details=ui._ric_preview_details({"PreviewOrderResponse": {
            "totalCommission":3,"totalOrderValue":102.6}},gross_debit=100)
        self.assertEqual(details["estimated_total_charges"],3)

    def test_invalid_fee_or_total_estimates_cannot_enable_preview(self):
        for key in ("estimatedFees","estimatedTotalAmount"):
            for value in (None,True,"malformed",float("nan"),float("inf"),-1):
                c,rows,state,broker,scan=self.setup_order()
                broker.preview_order.return_value = {"PreviewOrderResponse":{"PreviewIds":[{"previewId":123}],
                    "Order":[{"estimatedCommission":0,key:value}]}}
                self.render(c,rows,state,broker,scan,clicked=("ric_preview",))
                self.assertNotIn("ric_review_bundle",state,(key,value))
                broker.place_order.assert_not_called()
        with self.assertRaises(ETradeError):
            ui._ric_preview_details({"PreviewOrderResponse":{"totalOrderValue":99}},gross_debit=100)

    def test_slow_first_submission_cannot_use_expired_second_account_preview(self):
        c,rows,state,broker,scan=self.setup_order(accounts=["preferred","other"])
        now=[time.time()]
        with patch.object(ui.time,"time",side_effect=lambda:now[0]):
            self.render(c,rows,state,broker,scan,clicked=("ric_preview",))
            state["ric_live_confirm"]=True
            def slow_place(*args,**kwargs):
                now[0]+=ui._PREVIEW_FRESH_SECONDS+1
                return {"PlaceOrderResponse":{"OrderIds":[{"orderId":456}]}}
            broker.place_order.side_effect=slow_place
            self.render(c,rows,state,broker,scan,clicked=("ric_send",))
        self.assertEqual(broker.place_order.call_count,1)
        self.assertEqual(state["ric_last_send_results"][0]["status"],"SUBMITTED")
        self.assertEqual(state["ric_last_send_results"][1]["status"],"NOT SUBMITTED")
        self.assertNotIn("ric_review_bundle",state)

    def test_put_quote_drift_duplicate_or_wrong_expiration_blocks_requote(self):
        c, rows, state, broker, scan = self.setup_order()
        variants = [rows + [deepcopy(rows[1])], [row | {"expiry": row["expiry"]+timedelta(days=1)} for row in rows],
                    [row | {"ask": row["ask"]+2} if row["call_put"] == "PUT" and row["strike"] == 95 else row for row in rows]]
        for changed in variants:
            with patch.object(ui.st, "session_state", state), patch.object(ui, "_chain", return_value=changed):
                with self.assertRaises(ValueError):
                    ui._validate_current_ric_limit(broker, lambda: None, c, scan, c["debit"])

    def test_preview_rejection_invalid_commission_and_stale_data_never_send(self):
        for commission in (float("nan"), -1):
            c, rows, state, broker, scan = self.setup_order()
            broker.preview_order.return_value["PreviewOrderResponse"]["estimatedCommission"] = commission
            self.render(c, rows, state, broker, scan, clicked=("ric_preview", "ric_send"))
            self.assertNotIn("ric_review_bundle", state)
            broker.place_order.assert_not_called()
        c, rows, state, broker, scan = self.setup_order()
        scan["loaded_at"] = time.time()-301
        self.render(c, rows, state, broker, scan, clicked=("ric_preview", "ric_send"))
        broker.preview_order.assert_not_called()

    def test_changed_put_leg_fingerprint_or_empty_selection_invalidates_preview(self):
        c, rows, state, broker, scan = self.setup_order()
        self.render(c, rows, state, broker, scan, clicked=("ric_preview",))
        self.assertIn("ric_review_bundle", state)
        changed = c | {"put_sell_strike": c["put_sell_strike"]-1}
        self.render(changed, rows, state, broker, scan)
        self.assertNotIn("ric_review_bundle", state)
        broker.place_order.assert_not_called()
        self.render(c, rows, state, broker, scan, clicked=("ric_preview",))
        state["ric_accounts"] = []
        self.render(c, rows, state, broker, scan)
        self.assertNotIn("ric_review_bundle", state)

    def test_send_requotes_then_stops_uncertain_multiaccount_batch(self):
        c, rows, state, broker, scan = self.setup_order(accounts=["preferred", "other"])
        self.render(c, rows, state, broker, scan, clicked=("ric_preview",))
        state["ric_live_confirm"] = True
        broker.place_order.side_effect = TimeoutError("synthetic uncertain reply")
        self.render(c, rows, state, broker, scan, clicked=("ric_send",))
        self.assertEqual(broker.place_order.call_count, 1)
        self.assertNotIn("ric_review_bundle", state)
        self.render(c, rows, state, broker, scan, clicked=("ric_send",))
        self.assertEqual(broker.place_order.call_count, 1)



class LiveQuoteOverlayTests(unittest.TestCase):
    def fixture(self, count=1):
        now = time.time()
        candidate, _ = quoted_condor()
        rows = []
        for index in range(count):
            row = candidate | {"id": str(index)}
            for prefix, strike, side in (("put_buy", 995-index*10, "P"), ("put_sell", 990-index*10, "P"),
                                         ("buy", 1005+index*10, "C"), ("sell", 1010+index*10, "C")):
                row[prefix + "_strike"] = strike
                row[prefix + "_osi"] = f"SPY{candidate['expiry']:%y%m%d}{side}{strike*1000:08d}"
            rows.append(row)
        token = {"oauth_token":"RIC-SYNTHETIC-TOKEN", "oauth_token_secret":"RIC-SYNTHETIC-SECRET", "issued_at":now}
        state = {"etrade_access_unlocked":True, "etrade_access_token":token, "etrade_last_activity_at":now,
                 "terminal_tab_state":{"active":"R.I.C."},
                 "ric_scan_result":{"scope":"scope", "source":"E*TRADE", "unchanged":True},
                 "ric_review_bundle":{"untouched":True}, "ric_live_confirm":True}
        broker = MagicMock(is_offline=False)
        broker._namespace = session_namespace(token)
        broker.get_quote.side_effect = lambda symbols, **kwargs: self.payload(symbols, now)
        return state, broker, rows, now

    def payload(self, symbols, now, omit_put=False):
        data = []
        for symbol in symbols.split(","):
            root, year, month, day, side, strike = symbol.split(":")
            expiry = date(int(year),int(month),int(day))
            strike = float(strike)
            if omit_put and side == "PUT":
                continue
            long = (side == "CALL" and int(strike)%10 == 5) or (side == "PUT" and int(strike)%10 == 5)
            premium = 3 if long else 2
            data.append({"Product":{"symbol":root,"securityType":"OPTN","expiryYear":expiry.year,
                                     "expiryMonth":expiry.month,"expiryDay":expiry.day,"callPut":side,"strikePrice":strike},
                         "All":{"bid":premium-.05,"ask":premium+.05,
                                "osiKey":f"{root}{expiry:%y%m%d}{side[0]}{int(strike*1000):08d}"},
                         "dateTimeUTC":now,"quoteStatus":"REALTIME"})
        return {"QuoteResponse":{"QuoteData":data}}

    def tick(self, state, broker, rows, revision=1):
        ui._live_quote_context("scope", "view", rows, True, broker)
        state["bridge"] = {"kind":"quote_tick", "scope":"scope", "quote_sort_key":"view",
                           "quote_revision":revision,"quote_ids":[row["id"] for row in rows[:20]]}
        with patch.object(ui, "_require_ric_access"):
            ui._refresh_live_quotes(broker,"bridge","scope","view")
        return state["_ric_live_quotes"]

    def test_twenty_rows_deduplicate_and_batch_all_eighty_exact_contracts(self):
        state, broker, rows, now = self.fixture(20)
        before_rows, before_scan, before_review = deepcopy(rows), deepcopy(state["ric_scan_result"]), deepcopy(state["ric_review_bundle"])
        with patch.object(ui.st,"session_state",state), patch.object(ui.time,"time",return_value=now):
            overlay = self.tick(state,broker,rows)
        self.assertEqual(broker.get_quote.call_count,4)
        queried = [symbol for call in broker.get_quote.call_args_list for symbol in call.args[0].split(",")]
        self.assertEqual(len(queried),80)
        self.assertEqual(len(set(queried)),80)
        self.assertTrue(all(len(call.args[0].split(",")) <= 25 for call in broker.get_quote.call_args_list))
        for market in overlay["markets"].values():
            self.assertAlmostEqual(market["bid"],1.8)
            self.assertAlmostEqual(market["mid"],2)
            self.assertAlmostEqual(market["ask"],2.2)
            self.assertFalse(market["stale"])
        self.assertEqual(rows,before_rows)
        self.assertEqual(state["ric_scan_result"],before_scan)
        self.assertEqual(state["ric_review_bundle"],before_review)
        self.assertTrue(state["ric_live_confirm"])
        for method in (broker.preview_order,broker.place_order,broker.list_accounts,broker.get_balance,broker.get_option_chain):
            method.assert_not_called()

    def test_missing_put_marks_previous_overlay_stale_with_backoff(self):
        state,broker,rows,now = self.fixture()
        with patch.object(ui.st,"session_state",state), patch.object(ui.time,"time",return_value=now):
            self.tick(state,broker,rows)
        broker.get_quote.side_effect = lambda symbols, **kwargs:self.payload(symbols,now+11,omit_put=True)
        with patch.object(ui.st,"session_state",state), patch.object(ui.time,"time",return_value=now+11):
            overlay = self.tick(state,broker,rows,2)
        self.assertTrue(overlay["markets"]["0"]["stale"])
        self.assertEqual(overlay["markets"]["0"]["ask"],2.2)
        self.assertGreaterEqual(overlay["next_due"],now+41)

    def test_wrong_route_or_invalid_scope_never_queries_broker(self):
        state,broker,rows,now = self.fixture()
        state["terminal_tab_state"]["active"] = "VERTICAL OPTIONS"
        with patch.object(ui.st,"session_state",state), patch.object(ui.time,"time",return_value=now):
            self.tick(state,broker,rows)
        broker.get_quote.assert_not_called()
        state["terminal_tab_state"]["active"] = "R.I.C."
        state["ric_scan_result"]["scope"] = "other"
        with patch.object(ui.st,"session_state",state), patch.object(ui.time,"time",return_value=now):
            self.tick(state,broker,rows,2)
        broker.get_quote.assert_not_called()


class AcquisitionBeforeRankingTests(unittest.TestCase):
    @contextmanager
    def market(self, failed_indexes=()):
        clock, state, events = [1000.0], {}, []
        expiries = [ui._minimum_expiry_date()+timedelta(days=180*index) for index in range(6)]
        chains = {}
        for expiry in expiries:
            specs = (("PUT",85,.5),("PUT",90,1),("PUT",95,1.5),
                     ("CALL",105,1.5),("CALL",110,1),("CALL",115,.5))
            chains[expiry] = [dict(expiry=expiry,call_put=side,strike=strike,last=last,
                                  bid=last-.05,ask=last+.05,open_interest=1000,
                                  osi=f"SPY{expiry:%y%m%d}{side[0]}{strike*1000:08d}")
                              for side,strike,last in specs]
        broker = MagicMock(is_offline=False)
        broker.get_quote.return_value = {"lastTrade":100}
        def chain(client, symbol, expiry, touch_session, **kwargs):
            events.append(("chain",expiry))
            if expiries.index(expiry) in failed_indexes:
                raise RuntimeError("synthetic source failure")
            return chains[expiry]
        with (patch.object(ui.st,"session_state",state),
              patch.object(ui.st,"progress",return_value=MagicMock()),
              patch.object(ui,"options_market_client",return_value=broker),
              patch.object(ui,"_expirations",return_value=expiries),
              patch.object(ui,"_chain",side_effect=chain),
              patch.object(ui,"quote_summary",return_value={"description":"Synthetic SPY"}),
              patch.object(ui,"record_lookup"),
              patch.object(ui.time,"time",side_effect=lambda:clock[0]),
              patch.object(ui.time,"monotonic",side_effect=lambda:clock[0])):
            yield state,broker,clock,expiries,chains,events

    def scan_result(self, raw, scope=None):
        return result(10) | {"symbol":"SPY","raw_scan_id":raw["scan_id"],
                             "loaded_at":raw["loaded_at"],"target":None,
                             "max_strike_distance_pct":scope,"scope":"original"}

    def test_partial_source_never_prices_or_marks_an_expiration_ranked(self):
        with self.market() as (_,broker,_,_,_,events):
            raw = ui._scan_all_years(broker,"SPY",lambda:None)
            self.assertFalse(raw["scan_complete"])
            self.assertEqual(raw["next_expiry_index"],4)
            before = deepcopy(raw["rows"])
            with patch.object(ui,"scan_ric_candidates") as calculator:
                for scope,potential in ((None,36),(10,4)):
                    with self.subTest(scope=scope):
                        first,annual,cap,count = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,scope)
                        repeated,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,scope)
                        self.assertIs(first,repeated)
                        self.assertEqual((len(first),first.ranked_expirations),(0,0))
                        self.assertEqual(annual,set())
                        self.assertIsNone(cap)
                        self.assertEqual(count,potential)
                calculator.assert_not_called()
            self.assertEqual(raw["rows"],before)
            self.assertTrue(all(event[0]=="chain" for event in events))

    def test_all_chains_precede_slow_ranking_and_old_snapshot_finishes_exactly(self):
        add_expiration = ui.RICResultStore.add_expiration
        for scope in (None,10):
            with self.subTest(scope=scope),self.market() as (_,broker,clock,expiries,chains,events):
                def slow_first(store, expiry, rows, calculator, progress):
                    events.append(("rank",expiry))
                    added = add_expiration(store,expiry,rows,calculator,progress)
                    if added and store.ranked_expirations==1:
                        clock[0]+=301
                    return added
                with patch.object(ui.RICResultStore,"add_expiration",new=slow_first):
                    raw = ui._scan_all_years(broker,"SPY",lambda:None)
                    empty,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,scope)
                    self.assertEqual(empty.ranked_expirations,0)
                    advanced = ui._continue_ric_scan(self.scan_result(raw,scope),broker,lambda:None)
                    self.assertEqual(events[:6],[("chain",expiry) for expiry in expiries])
                    self.assertEqual(events[6],("rank",expiries[0]))
                    self.assertTrue(advanced["chains_complete"])
                    self.assertFalse(advanced["scan_complete"])
                    self.assertEqual(advanced["ranked_expirations"],1)
                    self.assertEqual(clock[0],1301)
                    with patch.object(ui,"_scan_all_years") as new_source:
                        finished = ui._continue_ric_scan(advanced,broker,lambda:None)
                    new_source.assert_not_called()
                self.assertTrue(finished["scan_complete"])
                self.assertEqual(finished["ranked_expirations"],len(expiries))
                self.assertEqual(finished["loaded_at"],1000)
                self.assertEqual(finished["raw_scan_id"],raw["scan_id"])
                self.assertEqual(finished["max_strike_distance_pct"],scope)
                self.assertEqual(finished["contract_count"],36)
                self.assertEqual([expiry for kind,expiry in events if kind=="chain"],expiries)
                broker.get_quote.assert_called_once_with("SPY")
                all_rows = [row for expiry in expiries for row in chains[expiry]
                            if scope is None or 90<=row["strike"]<=110]
                expected,annual = scan_ric_candidates(
                    symbol="SPY",spot_last=100,option_rows=all_rows,target_breakeven=None,
                    max_cost=None,max_per_expiration=None,desktop_fast_fill=True,fee_per_spread=10)
                self.assertEqual(list(finished["candidates"].iter_ranked()),expected)
                self.assertEqual(finished["annual_ids"],annual)
                self.assertGreater(len(expected),0)
                self.assertGreater(len(annual),1)

    def test_incomplete_sources_keep_exact_age_and_source_guards(self):
        for elapsed,offline in ((300,False),(299,True)):
            with self.subTest(elapsed=elapsed,offline=offline),self.market() as (_,broker,clock,_,_,events):
                raw = ui._scan_all_years(broker,"SPY",lambda:None)
                scan = self.scan_result(raw)
                clock[0]+=elapsed
                broker.is_offline = offline
                with (patch.object(ui,"_scan_all_years") as source,
                      patch.object(ui,"scan_ric_candidates") as calculator):
                    with self.assertRaisesRegex(ValueError,"QUOTE SNAPSHOT EXPIRED OR SOURCE CHANGED"):
                        ui._continue_ric_scan(scan,broker,lambda:None)
                source.assert_not_called()
                calculator.assert_not_called()
                self.assertEqual(len(events),4)
                self.assertEqual(raw["loaded_at"],1000)

    def test_failed_chains_remain_incomplete_evidence_and_cannot_bypass_age(self):
        with self.market((1,2)) as (_,broker,clock,expiries,_,_):
            raw = ui._scan_all_years(broker,"SPY",lambda:None)
            self.assertFalse(raw["scan_complete"])
            self.assertTrue(raw["stopped_on_errors"])
            with patch.object(ui,"scan_ric_candidates") as calculator:
                store,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10)
            calculator.assert_not_called()
            self.assertEqual(store.ranked_expirations,0)
            completed_source = ui._scan_all_years(broker,"SPY",lambda:None)
            self.assertTrue(completed_source["scan_complete"])
            self.assertEqual(completed_source["loaded_expirations"],4)
            self.assertEqual(completed_source["failed_expirations"],expiries[1:3])
            self.assertTrue(any("synthetic source failure" in error for error in completed_source["errors"]))
            failed = ui._continue_ric_scan(self.scan_result(completed_source),broker,lambda:None)
            self.assertEqual(failed["failed_expirations"],expiries[1:3])
            self.assertEqual(failed["errors"],completed_source["errors"])
            clock[0]=1300
            with patch.object(ui,"_scan_all_years") as source:
                with self.assertRaisesRegex(ValueError,"QUOTE SNAPSHOT EXPIRED OR SOURCE CHANGED"):
                    ui._continue_ric_scan(failed,broker,lambda:None)
            source.assert_not_called()

    def test_order_review_keeps_300_second_guard_before_and_during_requote(self):
        candidate,rows = quoted_condor()
        for during_requote in (False,True):
            with self.subTest(during_requote=during_requote):
                clock = [1299 if during_requote else 1300]
                scan = result() | {"loaded_at":1000}
                def requote(*args,**kwargs):
                    clock[0]=1300
                    return rows
                with (patch.object(ui.st,"session_state",{}),
                      patch.object(ui.time,"time",side_effect=lambda:clock[0]),
                      patch.object(ui,"_chain",side_effect=requote) as chain):
                    with self.assertRaisesRegex(ValueError,"Market snapshot expired"):
                        ui._validate_current_ric_limit(MagicMock(is_offline=False),lambda:None,
                                                       candidate,scan,candidate["debit"])
                self.assertEqual(chain.call_count,int(during_requote))


class StrikeDistanceScopeTests(unittest.TestCase):
    def raw(self, rows):
        return {"symbol":"SPY", "spot":100, "rows":rows, "scan_id":321,
                "loaded_at":time.time(), "scan_complete":True, "loaded_expirations":1,
                "expirations":[rows[0]["expiry"]], "errors":[], "next_expiry_index":1}

    def test_optional_scope_is_positive_finite_and_remembers_explicit_blank(self):
        self.assertIsNone(ui._entered_strike_distance(""))
        self.assertIsNone(ui._entered_strike_distance("  "))
        self.assertIsNone(ui._entered_strike_distance(None))
        self.assertEqual(ui._entered_strike_distance("1"),1)
        for invalid in ("0", "-1", "nan", "inf", "-inf", "bad", True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ui._entered_strike_distance(invalid)
        state = {}
        with patch.object(ui.st,"session_state",state), patch.object(ui,"_require_ric_access"):
            ui._restore_presentation()
            self.assertEqual(state["ric_max_strike_distance_pct"],"")
            state["ric_max_strike_distance_pct"] = "1"
            ui._remember_scan_inputs()
            ui._restore_presentation()
            self.assertEqual(state["ric_max_strike_distance_pct"],"1")
            state["ric_max_strike_distance_pct"] = ""
            ui._remember_scan_inputs()
            ui._restore_presentation()
            self.assertEqual(state["ric_max_strike_distance_pct"],"")

    def test_decimal_boundaries_are_inclusive_for_every_contract(self):
        rows = [{"strike":strike} for strike in
                ("98.999999999", "99", "99.7", "100.3", "101", "101.000000001", "NaN", "bad")]
        before = deepcopy(rows)
        scoped = ui._strike_scoped_rows(rows,100,1)
        self.assertEqual([row["strike"] for row in scoped],["99","99.7","100.3","101"])
        self.assertEqual(rows,before)
        self.assertIs(ui._strike_scoped_rows(rows,100,None),rows)
        self.assertEqual(ui._strike_scoped_rows([{"strike":"99.00495"},{"strike":"101.00505"}],100.005,1),
                         [{"strike":"99.00495"},{"strike":"101.00505"}])

    def test_dense_chain_is_scoped_before_engine_enumeration_without_mutating_raw(self):
        expiry = date.today()+timedelta(days=30)
        rows = [{"expiry":expiry,"call_put":side,"strike":strike,"osi":f"{side}{strike}"}
                for side in ("PUT","CALL") for strike in range(1,201)]
        raw, received = self.raw(rows), []
        before = deepcopy(rows)
        def calculator(**kwargs):
            received.append(kwargs["option_rows"])
            return [],set()
        with (patch.object(ui.st,"progress",return_value=MagicMock()),
              patch.object(ui,"scan_ric_candidates",side_effect=calculator)):
            store, _, _, potential = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,1)
        self.assertEqual(len(received),1)
        self.assertEqual(len(received[0]),6)
        self.assertTrue(all(99<=row["strike"]<=101 for row in received[0]))
        self.assertEqual(raw["rows"],before)
        self.assertEqual(potential,9)
        self.assertEqual(store.ranked_expirations,1)
        self.assertEqual(len(store),0)

    def test_unlimited_results_match_engine_and_scopes_have_separate_cached_stores(self):
        candidate, rows = quoted_condor(fee=10)
        raw = self.raw(rows)
        with patch.object(ui.st,"progress",return_value=MagicMock()):
            unrestricted, _, _, _ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10)
            same, _, _, _ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,None)
            scoped, _, _, _ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,10)
            repeated, _, _, _ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,10)
            narrower, _, _, _ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,9)
        self.assertIs(unrestricted,same)
        self.assertIsNot(unrestricted,scoped)
        self.assertIs(scoped,repeated)
        self.assertEqual(unrestricted.page(),[candidate])
        self.assertEqual(scoped.page(),[candidate])
        self.assertEqual(len(narrower),0)
        self.assertEqual(narrower.ranked_expirations,1)

    def test_continuation_and_mode_repricing_keep_fixed_spot_scope_and_fee(self):
        _, rows = quoted_condor(fee=10)
        raw = self.raw(rows)
        state = {"_ric_chain_cache":{"E*TRADE:SPY":raw}}
        scan = result(10) | {"symbol":"SPY","raw_scan_id":321,"target":None,
                            "scope":"old","max_strike_distance_pct":10}
        with (patch.object(ui.st,"session_state",state),
              patch.object(ui.st,"progress",return_value=MagicMock()),
              patch.object(ui,"_scan_all_years") as market_calls):
            continued = ui._continue_ric_scan(scan,MagicMock(is_offline=False),lambda:None)
            repriced = ui._reprice_cached_ric_scan(continued,"SMART FILL")
        market_calls.assert_not_called()
        for snapshot in (continued,repriced):
            self.assertEqual(snapshot["max_strike_distance_pct"],10)
            self.assertEqual(snapshot["fee_per_spread"],10)
            self.assertTrue(snapshot["scan_complete"])
            self.assertEqual(len(snapshot["candidates"]),1)
            row = snapshot["candidates"].page()[0]
            self.assertTrue(all(90<=row[key]<=110 for key in
                                ("put_sell_strike","put_buy_strike","buy_strike","sell_strike")))
        self.assertIsNot(continued["candidates"],repriced["candidates"])
        self.assertNotEqual(repriced["scope"],"old")

    def test_scoped_exports_label_scope_and_unlimited_csv_remains_identical(self):
        candidate, rows = quoted_condor(fee=10)
        raw = self.raw(rows)
        options = dict(source="SYNTHETIC",loaded_at=1,target=None,max_cost=None)
        legacy = ui._qualified_spreads_csv([candidate],set(),**options)
        unlimited = ui._qualified_spreads_csv([candidate],set(),**options,max_strike_distance_pct=None)
        self.assertEqual(unlimited,legacy)
        self.assertNotIn("Max Strike Distance From Scan Spot (%)",unlimited)
        with patch.object(ui.st,"progress",return_value=MagicMock()):
            store, _, _, _ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,10,10)
        scoped_csv = ui._qualified_store_csv(store,**options,max_strike_distance_pct=10)
        record = list(csv.DictReader(io.StringIO(scoped_csv)))[0]
        self.assertEqual(record["Max Strike Distance From Scan Spot (%)"],"10")
        self.assertEqual(float(record["Estimated Fees Per RIC ($)"]),10)
        book = ui._qualified_store_xlsx(store,**options,max_strike_distance_pct=10)
        with zipfile.ZipFile(io.BytesIO(book)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Max Strike Distance From Scan Spot (%)",sheet)
        page = ui._qualified_store_html(store,source="SYNTHETIC",loaded_at=1,
                                        pricing_mode="NORMAL",max_strike_distance_pct=10)
        meta = json.loads(page.split('<script id="vertical-meta" type="application/json">',1)[1].split("</script>",1)[0])
        self.assertIn("ALL FOUR STRIKES WITHIN ±10% OF SCAN SPOT",meta["source"])


class BreakevenMoveScopeTests(unittest.TestCase):
    def fixture(self):
        _,rows = quoted_condor()
        mapping = {90:93,95:98,105:102,110:110}
        rows = [row | {"strike":mapping[row["strike"]],
                        "osi":f"SPY{row['expiry']:%y%m%d}{row['call_put'][0]}{mapping[row['strike']]*1000:08d}",
                        **{key:row[key]-(.05 if row["strike"] in (95,105) else 0)
                           for key in ("bid","ask","last")}}
                for row in rows]
        candidates,_ = scan_ric_candidates(symbol="SPY",spot_last=100,option_rows=rows,
                                           target_breakeven=None,max_cost=None,fee_per_spread=2.6)
        raw = {"symbol":"SPY","spot":100,"rows":rows,"scan_id":987,
               "loaded_at":time.time(),"scan_complete":True,"loaded_expirations":1,
               "expirations":[rows[0]["expiry"]],"errors":[],"failed_expirations":[],"next_expiry_index":1}
        return candidates[0],rows,raw

    def test_optional_input_and_local_memory_preserve_an_explicit_blank(self):
        for value in (None,"","  "):
            self.assertIsNone(ui._entered_breakeven_move(value))
        self.assertEqual(ui._entered_breakeven_move("3"),3)
        for value in (0,-1,True,"nan","inf","-inf","bad"):
            with self.subTest(value=value),self.assertRaises(ValueError):
                ui._entered_breakeven_move(value)
        state = {}
        with patch.object(ui.st,"session_state",state),patch.object(ui,"_require_ric_access"):
            ui._restore_presentation()
            self.assertEqual(state["ric_max_breakeven_move_pct"],"")
            for value in ("3",""):
                state["ric_max_breakeven_move_pct"] = value
                ui._remember_scan_inputs()
                del state["ric_max_breakeven_move_pct"]
                ui._restore_presentation()
                self.assertEqual(state["ric_max_breakeven_move_pct"],value)

    def test_search_scope_has_independent_stores_and_continues_through_mode_flip(self):
        candidate,rows,raw = self.fixture()
        before = deepcopy(rows)
        state = {"_ric_chain_cache":{"E*TRADE:SPY":raw},"ric_max_breakeven_move_pct":"99"}
        with (patch.object(ui.st,"session_state",state),
              patch.object(ui.st,"progress",return_value=MagicMock()),
              patch.object(ui,"scan_ric_candidates",wraps=scan_ric_candidates) as calculator):
            unrestricted,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,2.6)
            same,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,2.6,None,None)
            bounded,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,2.6,None,3)
            repeated,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,2.6,None,3)
            self.assertEqual(calculator.call_args.kwargs["max_breakeven_move_pct"],3)
            tighter,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,2.6,None,2.98)
            self.assertIs(unrestricted,same)
            self.assertIs(bounded,repeated)
            self.assertIsNot(unrestricted,bounded)
            self.assertIsNot(bounded,tighter)
            self.assertEqual(unrestricted.page(),[candidate])
            self.assertEqual(bounded.page(),[candidate])
            self.assertLess(tighter.page()[0]["debit"],candidate["debit"])
            self.assertLess(candidate["put_sell_strike"],97)
            self.assertGreater(candidate["sell_strike"],103)
            scan = result(2.6) | {"symbol":"SPY","spot":100,"raw_scan_id":987,"target":None,
                                  "loaded_at":raw["loaded_at"],"scope":"original","max_breakeven_move_pct":3.2}
            with patch.object(ui,"_scan_all_years") as source:
                continued = ui._continue_ric_scan(scan,MagicMock(is_offline=False),lambda:None)
                repriced = ui._reprice_cached_ric_scan(continued,"SMART FILL")
            source.assert_not_called()
        self.assertEqual(rows,before)
        for snapshot in (continued,repriced):
            self.assertEqual(snapshot["max_breakeven_move_pct"],3.2)
            self.assertEqual(snapshot["fee_per_spread"],2.6)
            self.assertEqual(snapshot["loaded_at"],raw["loaded_at"])
            self.assertTrue(snapshot["scan_complete"])
            self.assertEqual(len(snapshot["candidates"]),1)
            row = snapshot["candidates"].page()[0]
            self.assertGreaterEqual(row["lower_breakeven"],96.8)
            self.assertLessEqual(row["upper_breakeven"],103.2)
        self.assertIsNot(continued["candidates"],repriced["candidates"])

    def test_exports_label_both_breakeven_scope_and_leave_unlimited_bytes_unchanged(self):
        candidate,rows,raw = self.fixture()
        before = deepcopy(candidate)
        options = dict(source="SYNTHETIC",loaded_at=1,target=None,max_cost=None)
        original = ui._qualified_spreads_csv([candidate],set(),**options)
        self.assertEqual(ui._qualified_spreads_csv([candidate],set(),**options,
                                                  max_breakeven_move_pct=None),original)
        self.assertNotIn("Max Breakeven Move From Scan Spot (%)",original)
        with patch.object(ui.st,"progress",return_value=MagicMock()):
            store,_,_,_ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,2.6,None,3)
        record = list(csv.DictReader(io.StringIO(ui._qualified_store_csv(
            store,**options,max_breakeven_move_pct=3))))[0]
        self.assertEqual(record["Max Breakeven Move From Scan Spot (%)"],"3")
        self.assertEqual(float(record["Underlying Last"]),100)
        book = ui._qualified_store_xlsx(store,**options,max_breakeven_move_pct=3)
        with zipfile.ZipFile(io.BytesIO(book)) as archive:
            self.assertIn("Max Breakeven Move From Scan Spot (%)",archive.read("xl/worksheets/sheet1.xml").decode())
        page = ui._qualified_store_html(store,source="SYNTHETIC",loaded_at=1,
                                        pricing_mode="NORMAL",max_breakeven_move_pct=3)
        meta = json.loads(page.split('<script id="vertical-meta" type="application/json">',1)[1].split("</script>",1)[0])
        self.assertIn(ui._breakeven_scope_label(3),meta["source"])
        self.assertEqual(candidate,before)

    def test_review_fixed_spot_cap_fingerprint_and_fee_boundary_despite_passing_ratio(self):
        candidate,rows,_ = self.fixture()
        scan = result(2.6) | {"spot":100,"max_breakeven_move_pct":3}
        self.assertEqual(candidate["debit"],.97)
        self.assertEqual(ui._ric_review_max_cost(candidate,scan,None),100)
        self.assertEqual(ui._ric_review_max_cost(candidate,scan,99),99)
        self.assertEqual(ui._ric_review_max_cost(candidate,scan,200),100)
        self.assertEqual(ui._ric_review_max_cost(candidate,scan | {"max_breakeven_move_pct":None},200),200)
        state = {"etrade_accounts":ACCOUNTS,"ric_accounts":["preferred"]}
        broker = MagicMock(is_offline=False)
        broker.preview_order.return_value = {"PreviewOrderResponse":{
            "PreviewIds":[{"previewId":123}],"estimatedCommission":3,"estimatedFees":0}}
        def render(clicked=()):
            view = order_ui(state,clicked)
            effective = scan | {"max_cost":ui._ric_review_max_cost(candidate,scan,scan["max_cost"])}
            with patch.object(ui,"st",view),patch.object(ui,"_chain",return_value=rows):
                ui._render_selected_order(broker,lambda:None,candidate,effective,True)
            return view
        render(("ric_preview",))
        self.assertIn("ric_review_bundle",state)
        expected = ui._review_fingerprint(candidate=candidate,quantity=1,limit_price=candidate["debit"],
                                          min_reward_risk=2,order_term="GOOD_FOR_DAY",
                                          account_keys=["preferred"],max_cost=100,fee_per_spread=2.6)
        self.assertEqual(state["ric_review_bundle"]["fingerprint"],expected)
        scan["max_breakeven_move_pct"] = 3.2
        render()
        self.assertNotIn("ric_review_bundle",state)
        scan["max_breakeven_move_pct"] = 3
        broker.preview_order.return_value["PreviewOrderResponse"]["estimatedFees"] = .01
        risk = candidate["debit"]*100+3.01
        self.assertGreater((candidate["width"]*100-risk)/risk,2)
        self.assertGreater(candidate["buy_strike"]+risk/100,103)
        view = render(("ric_preview",))
        self.assertNotIn("ric_review_bundle",state)
        self.assertTrue(any("reviewed total-cost cap" in str(call).lower() for call in view.warning.call_args_list))
        self.assertIsNone(scan["max_cost"])
        broker.place_order.assert_not_called()


class TableStrikeDistanceControlTests(unittest.TestCase):
    def message(self, *, scope="pre-scan", revision=1, filters=None):
        return {"scope":scope,"filter_scope":scope,"filter_revision":revision,
                "selected_ids":[],"revision":0,
                "column_filters":{} if filters is None else filters}

    def test_table_payload_and_export_use_fixed_scan_spot_without_mutating_candidate(self):
        candidate, _ = quoted_condor()
        before = deepcopy(candidate)
        with patch.object(ui.st,"session_state",{"_ric_ticker_quotes":{"SPY":{"last":999}}}):
            row = ui._spread_table_row(candidate,set())
        self.assertEqual(row["scan_spot"],100)
        self.assertEqual(row["lower_breakeven"],candidate["lower_breakeven"])
        self.assertEqual(row["upper_breakeven"],candidate["breakeven"])
        self.assertEqual(row["strike_distance_pct"],10)
        exported = ui._export_record(candidate,set(),source="SYNTHETIC",loaded_at=1,
                                     target=None,max_cost=None,max_strike_distance_pct=20)
        self.assertEqual(exported["Max Actual Strike Distance (%)"],10)
        self.assertEqual(exported["Max Strike Distance From Scan Spot (%)"],20)
        self.assertEqual(exported["Underlying Last"],100)
        self.assertEqual(candidate,before)
        unknown = candidate | {"spot_last":None}
        self.assertIsNone(ui._spread_table_row(unknown,set())["scan_spot"])
        self.assertIsNone(ui._spread_table_row(unknown,set())["strike_distance_pct"])

    def test_actual_distance_sort_matches_numeric_order_with_unknown_last(self):
        first, _ = quoted_condor()
        smaller = first | {"id":"smaller","put_sell_strike":98,"put_buy_strike":99,
                            "buy_strike":101,"sell_strike":102}
        unknown = first | {"id":"unknown","spot_last":None}
        self.assertEqual([row["id"] for row in ui._sort_qualified_spreads(
            [unknown,first,smaller],"MAX STRIKE DISTANCE %","ASC",None)],
            ["smaller",first["id"],"unknown"])
        self.assertEqual([row["id"] for row in ui._sort_qualified_spreads(
            [unknown,first,smaller],"MAX STRIKE DISTANCE %","DESC",None)],
            [first["id"],"smaller","unknown"])

    def test_moved_ceiling_migrates_and_remembers_minimum_and_explicit_clear(self):
        state = {"_ric_presentation":{"ric_max_strike_distance_pct":"8"}}
        with patch.object(ui.st,"session_state",state), patch.object(ui,"_require_ric_access"):
            ui._restore_presentation()
            self.assertEqual(state["ric_strike_distance_filter"],{"max":8})
            ui._remember_strike_distance_filter({"strike_distance_pct":{"min":2,"max":5}})
            ui._restore_presentation()
            self.assertEqual(state["ric_max_strike_distance_pct"],"5")
            self.assertEqual(ui._with_saved_strike_distance_filter({"expiry":["2027-01-15"]}),
                             {"expiry":["2027-01-15"],"strike_distance_pct":{"min":2,"max":5}})
            ui._remember_strike_distance_filter({})
            ui._restore_presentation()
            self.assertEqual(state["ric_max_strike_distance_pct"],"")
            self.assertEqual(state["ric_strike_distance_filter"],{})
            self.assertEqual(ui._with_saved_strike_distance_filter(
                {"strike_distance_pct":{"max":8},"expiry":[]}),{"expiry":[]})

    def test_pre_scan_protocol_accepts_only_new_matching_filter_without_market_calls(self):
        state = {"etrade_access_unlocked":True,"ric_max_strike_distance_pct":"8",
                 "ric_strike_distance_filter":{"max":8},"ric_review_bundle":{"preview":"old"},
                 "ric_live_confirm":True,"_ric_order_candidate":"old","ric_last_send_results":["old"]}
        protocol = {"scope":"pre-scan","revision":0}
        with (patch.object(ui.st,"session_state",state), patch.object(ui,"_require_ric_access"),
              patch.object(ui,"_scan_all_years") as download,
              patch.object(ui,"_rank_cached_ric_chains") as rank,
              patch.object(ui,"options_market_client") as market,
              patch.object(ui,"_render_selected_order") as ticket):
            update = self.message(filters={"strike_distance_pct":{"min":2,"max":9}})
            self.assertTrue(ui._apply_pre_scan_strike_filter(update,protocol))
            self.assertEqual(state["ric_max_strike_distance_pct"],"9")
            self.assertEqual(state["ric_strike_distance_filter"],{"min":2,"max":9})
            for key in ("ric_review_bundle","ric_live_confirm","_ric_order_candidate","ric_last_send_results"):
                self.assertNotIn(key,state)
            self.assertEqual(protocol["revision"],1)
            self.assertFalse(ui._apply_pre_scan_strike_filter(update,protocol))
            self.assertTrue(ui._apply_pre_scan_strike_filter(self.message(revision=2),protocol))
            self.assertEqual(state["ric_max_strike_distance_pct"],"")
            for action in (download,rank,market,ticket):
                action.assert_not_called()

    def test_stale_context_selection_and_financial_injection_cannot_change_header(self):
        for altered in (
            {"scope":"old"}, {"filter_scope":"old"}, {"filter_revision":0},
            {"filter_revision":True}, {"filter_revision":10**100}, {"selected_ids":["invented"]},
            {"rows":[{"debit":0,"reward_risk":999}]}, {"scan_spot":1},
            {"fee_per_spread":0}, {"max_strike_distance_pct":100},
        ):
            with self.subTest(altered=altered):
                state = {"ric_max_strike_distance_pct":"8","ric_strike_distance_filter":{"max":8}}
                protocol = {"scope":"pre-scan","revision":0}
                before = deepcopy(state)
                update = self.message(filters={"strike_distance_pct":{"max":9}}) | altered
                with patch.object(ui.st,"session_state",state),patch.object(ui,"_require_ric_access"):
                    self.assertFalse(ui._apply_pre_scan_strike_filter(update,protocol))
                self.assertEqual(state,before)
                self.assertEqual(protocol,{"scope":"pre-scan","revision":0})

    def test_invalid_header_filters_are_acknowledged_without_adopting_values(self):
        invalid = ({"cost":{"max":1}}, {"strike_distance_pct":{"max":0}},
                   {"strike_distance_pct":{"max":-1}}, {"strike_distance_pct":{"max":True}},
                   {"strike_distance_pct":{"max":"8"}}, {"strike_distance_pct":{"max":float("inf")}},
                   {"strike_distance_pct":{"max":10**1000}},
                   {"strike_distance_pct":{"min":float("nan")}},
                   {"strike_distance_pct":{"target":8}})
        for filters in invalid:
            with self.subTest(filters=filters):
                state = {"ric_max_strike_distance_pct":"8","ric_strike_distance_filter":{"max":8}}
                protocol = {"scope":"pre-scan","revision":0}
                before = deepcopy(state)
                with patch.object(ui.st,"session_state",state),patch.object(ui,"_require_ric_access"):
                    self.assertTrue(ui._apply_pre_scan_strike_filter(self.message(filters=filters),protocol))
                self.assertEqual(state,before)
                self.assertEqual(protocol["revision"],1)
                self.assertIn("error",protocol)

    def test_header_only_component_exists_before_scan_and_rejects_old_symbol_context(self):
        view = MagicMock(session_state={"ric_strike_distance_filter":{"max":8}})
        component = MagicMock(return_value=None)
        with (patch.object(ui,"st",view),patch.object(ui,"_SPREAD_SCROLL_COMPONENT",component),
              patch.object(ui,"_scan_all_years") as download,
              patch.object(ui,"_rank_cached_ric_chains") as rank):
            ui._render_pre_scan_strike_header("SPY")
            first = component.call_args.kwargs
            self.assertTrue(first["pre_scan"])
            self.assertEqual(first["rows"],[])
            self.assertEqual(first["total_rows"],0)
            self.assertEqual(first["column_filters"],{"strike_distance_pct":{"max":8}})
            self.assertFalse(first["sortable"])
            self.assertFalse(first["live_quotes_enabled"])
            ui._render_pre_scan_strike_header("SPY")
            self.assertEqual(component.call_args.kwargs["scope"],first["scope"])
            ui._render_pre_scan_strike_header("QQQ")
            current = component.call_args.kwargs
            self.assertNotEqual(current["scope"],first["scope"])
            self.assertFalse(ui._apply_pre_scan_strike_filter(
                self.message(scope=first["scope"],revision=3,filters={"strike_distance_pct":{"max":99}}),
                view.session_state["_ric_pre_scan_filter_state"]))
            download.assert_not_called()
            rank.assert_not_called()

    def test_widening_or_clearing_requires_explicit_scan_but_narrowing_is_complete(self):
        for covered, requested in ((None,None),(None,8),(8,5),(8,8)):
            self.assertIsNone(ui._strike_scope_change_notice(covered,requested))
        self.assertIn("±8%",ui._strike_scope_change_notice(8,9))
        self.assertIn("±9%",ui._strike_scope_change_notice(8,9))
        self.assertIn("SCAN ALL YEARS",ui._strike_scope_change_notice(8,None))
        self.assertIn("UNLIMITED",ui._strike_scope_change_notice(8,None))


class PortfolioAndIsolationTests(unittest.TestCase):
    def positions(self):
        c, rows = quoted_condor()
        return [{"Product": {"symbol":"SPY", "securityType":"OPTN", "callPut":row["call_put"],
                              "expiryYear":row["expiry"].year,"expiryMonth":row["expiry"].month,
                              "expiryDay":row["expiry"].day,"strikePrice":row["strike"]},
                 "positionType":"LONG" if row["strike"] in (95,105) else "SHORT", "quantity":2,
                 "osiKey":row["osi"],"totalCost":row["last"]*200,"totalGain":10,
                 "Complete":{"optionMultiplier":100,"optionsAdjustedFlag":False}}
                for row in rows]

    def test_portfolio_pairs_only_unambiguous_four_leg_standard_condors(self):
        positions = self.positions()
        condors, unmatched = ui._portfolio_rics(positions)
        self.assertEqual(len(condors), 1)
        self.assertFalse(unmatched)
        self.assertEqual(condors[0]["quantity"], 2)
        self.assertEqual(condors[0]["lower_breakeven"], 94)
        self.assertEqual(condors[0]["breakeven"], 106)
        for bad in (positions[:3], positions+[deepcopy(positions[0])],
                    [row | {"Complete":{"optionMultiplier":10}} if i == 0 else row for i,row in enumerate(positions)]):
            paired, review = ui._portfolio_rics(bad)
            self.assertFalse(paired)
            self.assertTrue(review)

    def test_repricing_retains_fee_snapshot_filters_and_independent_store(self):
        c, rows = quoted_condor(fee=2.6)
        now = time.time()
        raw = {"symbol":"SPY", "spot":100, "rows":rows, "scan_id":123,
               "loaded_at":now-40, "scan_complete":True,"loaded_expirations":1}
        state = {"_ric_chain_cache":{"E*TRADE:SPY":raw},
                 "_ric_column_filters":{"scope":"market-data", "filters":{"put_buy":{"min":94}}}}
        before = deepcopy(state["_ric_column_filters"])
        scan = result(2.6) | {"symbol":"SPY","raw_scan_id":123,"target":None,"scope":"old",
                              "loaded_at":raw["loaded_at"],"capacity_limited":False}
        with patch.object(ui.st,"session_state",state), patch.object(ui.st,"progress",return_value=MagicMock()):
            normal, _, _, _ = ui._rank_cached_ric_chains(raw,None,None,"NORMAL",2,2.6)
            repriced = ui._reprice_cached_ric_scan(scan,"SMART FILL")
        self.assertEqual(len(normal),1)
        self.assertEqual(len(repriced["candidates"]),1)
        self.assertIsNot(normal,repriced["candidates"])
        self.assertEqual(repriced["fee_per_spread"],2.6)
        self.assertEqual(repriced["loaded_at"],now-40)
        self.assertEqual(repriced["candidates"].page()[0]["fee_per_spread"],2.6)
        self.assertEqual(state["_ric_column_filters"],before)
        self.assertNotEqual(repriced["scope"],"old")

    def test_new_owner_preserves_original_features_and_private_keys(self):
        from src import vertical_options_ui as desktop
        self.assertIsNot(ui._scan_memory("ric-test"), desktop._scan_memory("ric-test"))
        source = Path(ui.__file__).read_text()
        for private in ('key="ric_accounts"', 'key="ric_live_confirm"', '"_ric_chain_cache"', '"_ric_live_quotes"'):
            self.assertIn(private, source)
        self.assertNotIn('key="vo_', source)
        self.assertIn('@finance_callback\ndef _refresh_live_quotes', source)
        self.assertIn('@finance_fragment\ndef _render_reverse_iron_condor_fragment', source)


class SubmittedScanRecoveryTests(unittest.TestCase):
    @contextmanager
    def app(self, stage, *, initial="5"):
        from streamlit.testing.v1 import AppTest
        from scripts.test_reverse_iron_condor_browser import FIXTURE
        injected = '''
from unittest.mock import patch
original_source, original_rank = ric._scan_all_years, ric._rank_cached_ric_chains
def interrupt(stage):
    if st.session_state.get('fixture_interrupt') == stage and not st.session_state.get('fixture_interrupted'):
        st.session_state['fixture_interrupted'] = True
        if st.session_state.get('fixture_change_draft'):
            st.session_state['_ric_presentation'].update(ric_symbol_value='SPY',ric_min_reward_risk='3',ric_fee_per_spread='1',ric_max_breakeven_move_pct='10')
            st.session_state['_ric_pricing_mode_preference'] = 'SMART FILL'
        st.rerun()
def source(*args,**kwargs):
    interrupt('source')
    value = original_source(*args,**kwargs)
    interrupt('source_commit')
    return value
def rank(*args,**kwargs):
    record('rank_policy',symbol=args[0]['symbol'],mode=args[3],ratio=args[4],fees=args[5],scope=args[7])
    if st.session_state.get('fixture_age_raw') and not st.session_state.get('fixture_raw_aged'):
        st.session_state['fixture_raw_aged'] = True
        args[0]['loaded_at'] -= 301
        if st.session_state.get('fixture_incomplete_raw'):
            args[0]['scan_complete'] = False
        st.rerun()
    if st.session_state.get('fixture_supersede') and not st.session_state.get('fixture_superseded'):
        st.session_state['fixture_superseded'] = True
        st.session_state['_ric_scan_pending'] = dict(st.session_state['_ric_scan_pending'],breakeven='10')
    interrupt('rank')
    if st.session_state.get('fixture_interrupt') == 'error':
        raise ValueError('SYNTHETIC SCAN FAILURE')
    return original_rank(*args,**kwargs)
'''
        with tempfile.TemporaryDirectory(prefix="ric_submit_test_") as directory:
            directory = Path(directory)
            fixture, events = directory / "app.py", directory / "events.jsonl"
            call = "    ric.render_reverse_iron_condor(SyntheticBroker(),lambda:None,vault_key='ric-synthetic-browser')"
            code = FIXTURE.replace("order,active=render_terminal_tab_bar", injected + "\norder,active=render_terminal_tab_bar")
            code = code.replace(call, "    with patch.object(ric,'_scan_all_years',source), patch.object(ric,'_rank_cached_ric_chains',rank):\n    " + call)
            fixture.write_text(code.replace("ric-synthetic-browser", directory.name))
            with (patch.dict(os.environ,{"RIC_BROWSER_ROOT":str(Path(__file__).resolve().parents[1]),"RIC_BROWSER_TEST_LOG":str(events)}),
                  patch("requests.sessions.Session.request",side_effect=AssertionError("Network disabled")),
                  patch("curl_cffi.requests.Session.request",side_effect=AssertionError("Network disabled")),
                  patch("socket.create_connection",side_effect=AssertionError("Network disabled"))):
                app = AppTest.from_file(str(fixture),default_timeout=30).run()
                app.text_input(key="ric_symbol_value").set_value("QQQ")
                app.text_input(key="ric_max_breakeven_move_pct").set_value(initial)
                next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
                self.assertFalse(app.exception)
                app.session_state["fixture_interrupt"] = stage
                yield app, events

    def test_submitted_scope_survives_source_and_ranking_interruptions(self):
        for stage in ("source", "source_commit", "rank"):
            for initial, entered, expected, rows in (("5","",None,20),("","10",10,8)):
                with self.subTest(stage=stage,entered=entered),self.app(stage,initial=initial) as (app,events):
                    app.text_input(key="ric_max_breakeven_move_pct").set_value(entered)
                    next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
                    self.assertFalse(app.exception)
                    result = app.session_state["ric_scan_result"]
                    self.assertEqual(result["max_breakeven_move_pct"],expected)
                    self.assertEqual(len(result["candidates"]),rows)
                    self.assertTrue(result["scan_complete"])
                    self.assertFalse(any("MAX BREAKEVEN MOVE CHANGED" in value.value for value in app.info))

    def test_pending_scan_keeps_submitted_values_when_draft_changes(self):
        with self.app("rank") as (app,events):
            app.session_state["fixture_change_draft"] = True
            app.text_input(key="ric_max_breakeven_move_pct").set_value("")
            next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
            self.assertFalse(app.exception)
            result = app.session_state["ric_scan_result"]
            self.assertEqual((result["symbol"],result["pricing_mode"],result["min_reward_risk"],result["fee_per_spread"],result["max_breakeven_move_pct"]),("QQQ","NORMAL",2,0,None))
            self.assertEqual(app.text_input(key="ric_symbol_value").value,"SPY")

    def test_manual_refresh_reuses_only_committed_new_source_after_interruption(self):
        for stage in ("source", "source_commit", "rank"):
            with self.subTest(stage=stage),self.app(stage) as (app,events):
                old_id = app.session_state["ric_scan_result"]["raw_scan_id"]
                next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
                self.assertFalse(app.exception)
                self.assertNotEqual(app.session_state["ric_scan_result"]["raw_scan_id"],old_id)
                logged = [json.loads(line) for line in events.read_text().splitlines()]
                self.assertEqual(sum(event["event"] == "chain" for event in logged),8)
                self.assertFalse(any(event["event"] in {"preview","place"} for event in logged))

    def test_failed_new_request_stays_visible_without_automatic_retry(self):
        with self.app("error") as (app,events):
            app.text_input(key="ric_max_breakeven_move_pct").set_value("")
            next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
            self.assertTrue(any("SYNTHETIC SCAN FAILURE" in value.value for value in app.error))
            before = [line for line in events.read_text().splitlines()
                      if json.loads(line)["event"] in {"rank_policy","chain","expirations"}]
            app.run()
            self.assertTrue(any("SYNTHETIC SCAN FAILURE" in value.value for value in app.error))
            self.assertEqual(app.session_state["ric_scan_result"]["max_breakeven_move_pct"],5)
            self.assertEqual([line for line in events.read_text().splitlines()
                              if json.loads(line)["event"] in {"rank_policy","chain","expirations"}],before)
            app.session_state["fixture_interrupt"] = None
            next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertIsNone(app.session_state["ric_scan_result"]["max_breakeven_move_pct"])
            self.assertEqual(app.session_state["ric_scan_result"]["min_reward_risk"],2)

    def test_failed_replacement_does_not_automatically_resume_previous_partial_scan(self):
        with self.app("error") as (app,events):
            app.session_state["ric_scan_result"]["scan_complete"] = False
            app.text_input(key="ric_max_breakeven_move_pct").set_value("")
            next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
            self.assertTrue(any("QQQ // SYNTHETIC SCAN FAILURE" in value.value for value in app.error))
            self.assertFalse(app.session_state["ric_scan_result"]["scan_complete"])
            count = sum(json.loads(line)["event"] == "rank_policy" for line in events.read_text().splitlines())
            app.run()
            self.assertEqual(sum(json.loads(line)["event"] == "rank_policy" for line in events.read_text().splitlines()),count)

    def test_latched_raw_keeps_incomplete_freshness_guard_and_complete_research_timestamp(self):
        for incomplete in (True,False):
            with self.subTest(incomplete=incomplete),self.app(None) as (app,events):
                old_time = app.session_state["ric_scan_result"]["loaded_at"]
                app.session_state["fixture_age_raw"] = True
                app.session_state["fixture_incomplete_raw"] = incomplete
                app.text_input(key="ric_max_breakeven_move_pct").set_value("")
                next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
                self.assertFalse(app.exception)
                if incomplete:
                    self.assertTrue(any("QUOTE SNAPSHOT EXPIRED" in value.value for value in app.error))
                    self.assertEqual(app.session_state["ric_scan_result"]["max_breakeven_move_pct"],5)
                else:
                    self.assertFalse(app.error)
                    self.assertIsNone(app.session_state["ric_scan_result"]["max_breakeven_move_pct"])
                    self.assertLessEqual(app.session_state["ric_scan_result"]["loaded_at"],old_time-300)

    def test_superseded_request_cannot_publish_over_new_submission(self):
        with self.app(None) as (app,events):
            app.session_state["fixture_supersede"] = True
            app.text_input(key="ric_max_breakeven_move_pct").set_value("")
            next(button for button in app.button if button.label == "SCAN ALL YEARS").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["ric_scan_result"]["max_breakeven_move_pct"],10)
            self.assertEqual(len(app.session_state["ric_scan_result"]["candidates"]),8)


if __name__ == "__main__":
    unittest.main()
