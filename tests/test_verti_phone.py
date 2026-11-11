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
        target_breakeven=None, max_cost=200, desktop_fast_fill=True,
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


class LiveQuoteOverlayTests(unittest.TestCase):
    def fixture(self, feature, prefix, count=1):
        now = time.time()
        expiry = date.today() + timedelta(days=45)
        rows = []
        for index in range(count):
            buy, sell = 100 + index * 10, 105 + index * 10
            rows.append({"id": str(index), "symbol": "SPY", "expiry": expiry, "call_put": "CALL",
                         "buy_strike": buy, "sell_strike": sell,
                         "buy_osi": f"SPY---{expiry:%y%m%d}C{buy * 1000:08d}",
                         "sell_osi": f"SPY---{expiry:%y%m%d}C{sell * 1000:08d}",
                         "debit": 1.07, "cost": 107, "width": 5, "reward_risk": 3.67,
                         "orderable": True, "spread_bid": .9, "spread_ask": 1.2})
        token = {"oauth_token": "DUMMY-QUOTE-TOKEN", "oauth_token_secret": "DUMMY-QUOTE-SECRET",
                 "issued_at": now}
        state = {"etrade_access_unlocked": True, "etrade_access_token": token,
                 "etrade_last_activity_at": now,
                 "terminal_tab_state": {"active": "VERTICAL OPTIONS" if prefix == "vo" else "VERTI PHONE"},
                 prefix + "_scan_result": {"scope": "pricing-a", "source": "E*TRADE"},
                 prefix + "_review_bundle": {"untouched": True}, prefix + "_live_confirm": True}
        client = MagicMock(is_offline=False)
        from src.etrade_data_cache import session_namespace
        client._namespace = session_namespace(token)
        client.get_quote.side_effect = lambda symbols, **kwargs: self.payload(symbols, now)
        return state, client, rows, now

    def payload(self, symbols, now):
        rows = []
        for symbol in symbols.split(","):
            root, year, month, day, side, strike = symbol.split(":")
            expiry = date(int(year), int(month), int(day))
            strike = float(strike)
            premium = 3 if int(strike) % 10 == 0 else 2
            rows.append({"Product": {"symbol": root, "securityType": "OPTN",
                                     "expiryYear": expiry.year, "expiryMonth": expiry.month,
                                     "expiryDay": expiry.day, "callPut": side, "strikePrice": strike},
                         "All": {"bid": premium - .2, "ask": premium + .2,
                                 "osiKey": f"{root}{expiry:%y%m%d}{side[0]}{int(strike * 1000):08d}"},
                         "dateTimeUTC": now, "quoteStatus": "REALTIME"})
        return {"QuoteResponse": {"QuoteData": rows}}

    def tick(self, feature, prefix, state, client, rows, revision=1):
        feature._live_quote_context("pricing-a", "view-a", rows, True, client)
        state["component"] = {"kind": "quote_tick", "scope": "pricing-a",
                              "quote_sort_key": "view-a", "quote_revision": revision,
                              "quote_ids": [row["id"] for row in rows[:20]]}
        feature._refresh_live_quotes(client, "component", "pricing-a", "view-a")
        return state["_" + prefix + "_live_quotes"]

    def test_both_owners_batch_only_visible_legs_without_changing_orders_or_scan(self):
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            state, client, rows, now = self.fixture(feature, prefix, 20)
            before, candidates = deepcopy(state), deepcopy(rows)
            with patch.object(feature.st, "session_state", state), patch.object(feature.time, "time", return_value=now):
                overlay = self.tick(feature, prefix, state, client, rows)
                self.assertEqual(client.get_quote.call_count, 2)
                self.assertEqual([len(call.args[0].split(",")) for call in client.get_quote.call_args_list], [25, 15])
                self.assertEqual(len(overlay["markets"]), 20)
                self.assertEqual(overlay["markets"]["0"]["status"], "REALTIME")
                self.assertEqual(overlay["markets"]["0"]["as_of"], now)
                self.assertAlmostEqual(overlay["markets"]["0"]["mid"], 1)
                self.assertEqual(rows, candidates)
                for key, value in before.items():
                    self.assertEqual(state[key], value)
                self.assertEqual([call[0] for call in client.mock_calls], ["get_quote", "get_quote"])
                overlay = self.tick(feature, prefix, state, client, rows, revision=2)
                self.assertEqual(client.get_quote.call_count, 2)
                self.assertEqual(overlay["revision"], 2)  # Throttled ticks are acknowledged.
                update = feature._live_quote_context("pricing-a", "view-a", rows[:1], True, client)
                self.assertEqual(set(update["markets"]), {"0"})

    def test_both_owners_reject_nonquote_stale_scope_invalid_ids_and_inactive_callbacks(self):
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            state, client, rows, _ = self.fixture(feature, prefix)
            with patch.object(feature.st, "session_state", state):
                feature._live_quote_context("pricing-a", "view-a", rows, True, client)
                valid = {"kind": "quote_tick", "scope": "pricing-a", "quote_sort_key": "view-a",
                         "quote_revision": 1, "quote_ids": ["0"]}
                for changes in ({"kind": "filters"}, {"scope": "old"}, {"quote_sort_key": "old"},
                                {"quote_ids": ["missing"]}, {"quote_ids": ["0", "0"]},
                                {"quote_ids": ["0"] * 21}, {"quote_revision": True},
                                {"quote_ids": []}):
                    state["component"] = {**valid, **changes}
                    feature._refresh_live_quotes(client, "component", "pricing-a", "view-a")
                state["component"] = valid
                state["terminal_tab_state"]["active"] = "HOLDINGS"
                feature._refresh_live_quotes(client, "component", "pricing-a", "view-a")
                client.get_quote.assert_not_called()

    def test_both_owners_validate_each_exact_contract_and_never_pair_by_position(self):
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            state, _, rows, now = self.fixture(feature, prefix)
            leg = feature._live_quote_leg(rows[0], "buy")
            symbols = ":".join(map(str, leg))
            payload = self.payload(symbols, now)
            self.assertEqual(set(feature._live_quote_batch(payload, {leg}, now)), {leg})
            for block, key, invalid in (("Product", "symbol", "QQQ"), ("Product", "securityType", "EQ"),
                                        ("Product", "expiryYear", leg[1] + .5), ("Product", "callPut", "PUT"),
                                        ("Product", "strikePrice", 999), ("All", "osiKey", "WRONG"),
                                        ("All", "bid", 100), ("All", "ask", float("nan"))):
                broken = deepcopy(payload)
                broken["QuoteResponse"]["QuoteData"][0][block][key] = invalid
                self.assertEqual(feature._live_quote_batch(broken, {leg}, now), {})
            duplicate = deepcopy(payload)
            duplicate["QuoteResponse"]["QuoteData"] *= 2
            self.assertEqual(feature._live_quote_batch(duplicate, {leg}, now), {})
            for key, invalid in (("quoteStatus", "INVALID"), ("dateTimeUTC", now + 61),
                                 ("dateTimeUTC", None), ("dateTimeUTC", float("inf"))):
                broken = deepcopy(payload)
                broken["QuoteResponse"]["QuoteData"][0][key] = invalid
                self.assertEqual(feature._live_quote_batch(broken, {leg}, now), {})
            spxw = {**rows[0], "symbol": "SPX", "buy_osi": rows[0]["buy_osi"].replace("SPY", "SPXW")}
            self.assertEqual(feature._live_quote_leg(spxw, "buy")[0], "SPXW")
            self.assertIsNone(feature._live_quote_leg({**rows[0], "buy_osi": "SYNTHETIC-WRONG"}, "buy"))

    def test_both_owners_preserve_status_older_leg_time_and_back_off_errors(self):
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            state, client, rows, now = self.fixture(feature, prefix)
            def delayed(symbols, **kwargs):
                payload = self.payload(symbols, now)
                payload["QuoteResponse"]["QuoteData"][-1].update(quoteStatus="DELAYED", dateTimeUTC=now - 900)
                return payload
            client.get_quote.side_effect = delayed
            with patch.object(feature.st, "session_state", state), patch.object(feature.time, "time", return_value=now):
                overlay = self.tick(feature, prefix, state, client, rows)
                market = deepcopy(overlay["markets"]["0"])
                self.assertEqual((market["status"], market["as_of"], market["stale"]), ("DELAYED", now - 900, True))
                client.get_quote.side_effect = RuntimeError("Unavailable")
                for revision, delay in ((2, 30), (3, 60), (4, 120)):
                    state["_" + prefix + "_live_quotes"]["next_due"] = 0
                    overlay = self.tick(feature, prefix, state, client, rows, revision)
                    self.assertEqual(overlay["next_due"] - now, delay)
                    self.assertEqual(overlay["markets"]["0"], market)

    def test_both_owners_reject_cached_fallback_and_do_not_extend_auth_activity(self):
        from src.etrade_data_cache import CachedETradeClient, session_namespace
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            state, _, rows, now = self.fixture(feature, prefix)
            client = MagicMock(spec=CachedETradeClient)
            client.is_offline = False
            client._namespace = session_namespace(state["etrade_access_token"])
            def fallback(symbols, **kwargs):
                state["_etrade_cache_last_event"] = {"stale": True}
                return self.payload(symbols, now)
            client.get_quote.side_effect = fallback
            with patch.object(feature.st, "session_state", state):
                overlay = self.tick(feature, prefix, state, client, rows)
                self.assertTrue(overlay["markets"]["0"]["stale"])
                self.assertIsNone(overlay["markets"]["0"]["as_of"])
                self.assertEqual(client.get_quote.call_args.kwargs, {"force_refresh": True})
                self.assertEqual(state["etrade_last_activity_at"], now)

    def test_both_owners_stop_on_lock_disconnect_expiry_or_changed_login(self):
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            for condition in ("locked", "missing-token", "expired", "other-login", "wrong-namespace",
                              "lock-during-read", "leave-during-read", "rescan-during-read"):
                state, client, rows, now = self.fixture(feature, prefix)
                with patch.object(feature.st, "session_state", state):
                    feature._live_quote_context("pricing-a", "view-a", rows, True, client)
                    state["component"] = {"kind": "quote_tick", "scope": "pricing-a", "quote_sort_key": "view-a",
                                          "quote_revision": 1, "quote_ids": ["0"]}
                    if condition == "locked":
                        state["etrade_access_unlocked"] = False
                    elif condition == "missing-token":
                        state.pop("etrade_access_token")
                    elif condition == "expired":
                        state["etrade_last_activity_at"] = now - 7201
                    elif condition == "other-login":
                        state["etrade_access_token"]["oauth_token"] = "REPLACED-DUMMY-TOKEN"
                    elif condition == "wrong-namespace":
                        client._namespace = "DIFFERENT-SESSION"
                    else:
                        def lock(symbols, **kwargs):
                            if condition == "lock-during-read":
                                state["etrade_access_unlocked"] = False
                            elif condition == "leave-during-read":
                                state["terminal_tab_state"]["active"] = "HOLDINGS"
                            else:
                                state[prefix + "_scan_result"]["scope"] = "new-scan"
                            return self.payload(symbols, now)
                        client.get_quote.side_effect = lock
                    feature._refresh_live_quotes(client, "component", "pricing-a", "view-a")
                    self.assertEqual(state["_" + prefix + "_live_quotes"]["markets"], {})
                    self.assertEqual(client.get_quote.call_count, int(condition.endswith("during-read")))

    def test_both_quote_bridges_dedupe_legs_and_do_not_render_parent_broker_helpers(self):
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            state, client, rows, now = self.fixture(feature, prefix)
            rows.append({**rows[0], "id": "same-legs"})
            with (patch.object(feature.st, "session_state", state),
                  patch.object(feature, "_QUOTE_BRIDGE_COMPONENT") as bridge,
                  patch.object(feature, "_liquid_cash_markup") as cash,
                  patch.object(feature, "_ticker_quote_preview") as ticker,
                  patch.object(feature, "_render_selected_order") as ticket):
                feature._live_quote_context("pricing-a", "view-a", rows, True, client)
                renderer = feature._render_live_quote_bridge.__wrapped__
                renderer(client, "private-channel", "pricing-a", "view-a")
                args = bridge.call_args.kwargs
                self.assertTrue(args["live_quotes_enabled"])
                state[args["key"]] = {"kind": "quote_tick", "scope": "pricing-a", "quote_sort_key": "view-a",
                                      "quote_revision": 1, "quote_ids": ["0", "same-legs"]}
                args["on_change"]()
                renderer(client, "private-channel", "pricing-a", "view-a")
                self.assertEqual(len(client.get_quote.call_args.args[0].split(",")), 2)
                self.assertEqual(set(bridge.call_args.kwargs["live_quote_update"]["markets"]), {"0", "same-legs"})
                self.assertEqual(state["etrade_last_activity_at"], now)
                cash.assert_not_called()
                ticker.assert_not_called()
                ticket.assert_not_called()


class FillEstimateFilterTests(unittest.TestCase):
    def store(self, groups):
        from src.vertical_options_store import VerticalResultStore
        store = VerticalResultStore("SPY", 100, 100, None, "NORMAL")
        for expiry, side, scores in groups:
            rows = []
            for index, score in enumerate(scores):
                debit = .5 if score is None else (score - 5) / 90
                cost = 100 * debit
                rows.append({"id": f"{expiry}-{side}-{index}", "expiry": expiry, "year": expiry.year,
                             "call_put": side, "symbol": "SPY", "spot_last": 100,
                             "buy_strike": 100 if side == "CALL" else 110,
                             "sell_strike": 110 if side == "CALL" else 100,
                             "buy_osi": "", "sell_osi": "", "buy_last": 3, "sell_last": 2,
                             "debit": debit, "cost": cost, "max_loss": cost, "width": 10,
                             "max_profit": 1000-cost, "reward_risk": (1000-cost)/cost,
                             "breakeven": 100 if side == "CALL" else 110, "basis": "LIMIT", "grade": 0,
                             "orderable": score is not None, "spread_bid": 0 if score is not None else None,
                             "spread_mid": .5 if score is not None else None,
                             "spread_ask": 1 if score is not None else None})
            def calculator(*, candidate_sink, rows=rows, **kwargs):
                for row in rows:
                    candidate_sink(row)
            store.add_expiration(expiry, rows, calculator)
        return store

    def test_strict_gt_uses_display_rounding_and_excludes_equal_or_unknown(self):
        expiry = date.today() + timedelta(days=45)
        store = self.store([(expiry, "CALL", [72.49, 72.5, 90, None])])
        self.assertEqual(store.fill_est_values(), [72, 73, 90])
        self.assertEqual(store.count(column_filters={"fill_scale": {"gt": 72}}), 2)
        self.assertEqual(store.count(column_filters={"fill_scale": {"gt": 73}}), 1)
        self.assertEqual(store.count(column_filters={"fill_scale": {"gt": 90}}), 0)
        self.assertEqual(store.count(column_filters={"fill_scale": {"gt": 100}}), 0)
        self.assertEqual(store.count(column_filters={"fill_scale": {"gt": 72.5}}), 2)
        self.assertEqual(len(list(store.iter_rows(column_filters={"fill_scale": {"gt": 73}}))), 1)
        self.assertEqual(store.fill_est_values(column_filters={"fill_scale": {"gt": 100}}), [72, 73, 90])
        for invalid in ({}, {"min": 72}, {"max": 90}, {"gt": 72, "max": 90}, {"gt": True},
                        {"gt": None}, {"gt": "72"}, {"gt": float("nan")}, {"gt": -1}, {"gt": 101}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                store.count(column_filters={"fill_scale": invalid})
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                store.fill_est_values(column_filters={"fill_scale": invalid})

    def test_choices_cover_the_full_store_under_other_filters_year_and_distance(self):
        first, second = date(2027, 1, 15), date(2028, 1, 21)
        store = self.store([(first, "CALL", [10] * 250 + [90]), (second, "PUT", [80, None])])
        self.assertEqual(len(store.page()), 200)
        self.assertEqual(store.fill_est_values(), [10, 80, 90])
        self.assertEqual(store.fill_est_values(column_filters={"side": ["CALL"], "fill_scale": {"gt": 95}}), [10, 90])
        self.assertEqual(store.fill_est_values(year=2028), [80])
        self.assertEqual(store.fill_est_values(column_filters={"side": []}), [])
        self.assertEqual(store.fill_est_values(column_filters={"ticker": ["QQQ"]}), [])
        self.assertEqual(store.fill_est_values(max_distance=0), [10, 90])
        self.assertEqual(store.fill_est_values(column_filters={"cost": {"max": 10}}), [10])
        with self.assertRaises(ValueError):
            store.fill_est_values(column_filters={"unknown": {"gt": 60}})

    def test_choice_cache_is_bounded_reused_and_invalidated_only_by_store_mutation(self):
        first, second = date(2027, 1, 15), date(2028, 1, 21)
        store = self.store([(first, "CALL", [10, 90])])
        with patch.object(store, "_connect", wraps=store._connect) as connect:
            self.assertEqual(store.fill_est_values(), [10, 90])
            self.assertEqual(store.fill_est_values(column_filters={"fill_scale": {"gt": 95}}), [10, 90])
            self.assertEqual(connect.call_count, 1)
            for minimum in range(10):
                store.fill_est_values(column_filters={"cost": {"min": minimum}})
            self.assertLessEqual(len(store._fill_values_cache), 8)
        self.assertTrue(store._fill_values_cache)
        rows = []
        store.add_expiration(second, rows, lambda **kwargs: None)
        self.assertEqual(store._fill_values_cache, {})
        store.fill_est_values()
        store.prune_before(second)
        self.assertEqual(store._fill_values_cache, {})
        self.assertEqual(store.fill_est_values(), [])

    def test_offline_worker_matches_strict_gt_and_preserves_recovery_choices(self):
        import shutil
        import subprocess
        from src.vertical_options_store import filterable_verticals_html
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is unavailable for actual offline worker verification")
        store = self.store([(date(2027, 1, 15), "CALL", [10] * 250 + [72.49, 72.5, 90]),
                            (date(2028, 1, 21), "PUT", [80, None])])
        output = filterable_verticals_html(store, source="SYNTHETIC", loaded_at=1234,
            pricing_mode="NORMAL", column_filters={"fill_scale": {"gt": 100}})
        script = output.split("<script>", 1)[1].split("</script>", 1)[0]
        metadata = json.loads(re.search(r'id="vertical-meta"[^>]*>(.*?)</script>', output).group(1))
        packed = re.search(r'id="vertical-data"[^>]*>(.*?)</script>', output).group(1)
        cases = []
        for filters, year in (({"fill_scale": {"gt": 72}}, None),
                              ({"fill_scale": {"gt": 90}}, None),
                              ({"fill_scale": {"gt": 100}}, None),
                              ({"fill_scale": {"gt": 72}, "side": ["CALL"]}, None),
                              ({"fill_scale": {"gt": 72}}, 2028),
                              ({"fill_scale": {"gt": 72}, "side": []}, None)):
            cases.append({"state": {"filters": filters, "year": year or "", "sort_by": "COST",
                                    "direction": "ASC", "search": "", "page": 0},
                          "total": store.count(column_filters=filters, year=year),
                          "choices": store.fill_est_values(column_filters=filters, year=year)})
        harness = r'''
const vm=require("vm"),assert=require("assert"),fixture=JSON.parse(require("fs").readFileSync(0,"utf8"));
const context={DecompressionStream,Response,Blob,Uint8Array,atob,setTimeout,clearTimeout,onmessage:null,postMessage:()=>{}};
vm.createContext(context);vm.runInContext(fixture.script.split("\n(()=>{")[0]+"\nverticalsWorker();",context);
(async()=>{
 await context.onmessage({data:{type:"init",meta:fixture.meta,compressed:fixture.packed}});
 let revision=0;
 async function query(state){return await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error("No worker result")),3000);
  context.postMessage=message=>{if(["page","error"].includes(message.type)){clearTimeout(timer);resolve(message);}};
  context.onmessage({data:{type:"query",revision:++revision,state}});
 });}
 for(const item of fixture.cases){
  const message=await query(item.state);assert.equal(message.type,"page");assert.equal(message.total,item.total);
  assert.deepEqual(Array.from(message.fill_values),item.choices);
 }
 for(const invalid of [{min:72},{gt:true},{gt:NaN},{gt:72,max:90}]){
  const message=await query({...fixture.cases[0].state,filters:{fill_scale:invalid}});
  assert.equal(message.type,"error");
 }
 new vm.Script(fixture.script);
 console.log("PASS actual offline strict greater-than worker and full-store recovery choices");
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([node, "-e", harness], input=json.dumps(
            {"script": script, "meta": metadata, "packed": packed, "cases": cases}),
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with self.assertRaises(ValueError):
            filterable_verticals_html(store, source="SYNTHETIC", loaded_at=1234,
                pricing_mode="NORMAL", column_filters={"fill_scale": {"min": 72}})
        self.assertIn('id="fill_gt"', output)
        self.assertIn('(saved)', output)


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
            self.assertEqual(phone._pricing_mode_flip_switch(), "NORMAL")
            self.assertEqual(component.call_count, 1)
            self.assertEqual(component.call_args.kwargs["action"], "read")
            self.assertEqual(phone._pricing_mode_flip_switch(), "NORMAL")
            self.assertEqual(component.call_args.kwargs["action"], "write")
            self.assertEqual(component.call_args.kwargs["value"], "NORMAL")
        self.assertEqual({key: state[key] for key in before}, before)


class PhoneParityTests(unittest.TestCase):
    def test_html_download_keeps_a_read_snapshot_without_blocking_new_sort_indexes(self):
        import threading
        from src.vertical_options_store import VerticalResultStore
        expiry = date.today() + timedelta(days=45)
        contracts = [dict(expiry=expiry, call_put="CALL", strike=100 + k,
                          last=4 - .05 * k, bid=3.95 - .05 * k, ask=4.05 - .05 * k,
                          osi=f"SYNTHETIC-{k}") for k in range(25)]
        for owner in (desktop, phone):
            with self.subTest(owner=owner.__name__):
                store = VerticalResultStore("SPY", 100, None, None, "NORMAL")
                store.add_expiration(expiry, contracts, math.scan_vertical_candidates)
                original = [row["id"] for row in store.iter_ranked()]
                self.assertGreater(len(original), 256)  # Keep the SQLite read cursor active.
                reading, release, indexed = (threading.Event() for _ in range(3))
                errors, exported, output = [], [], []
                def builder(retained, **options):
                    rows = retained.iter_ranked()
                    exported.append(next(rows)["id"])
                    reading.set()
                    if not release.wait(5):
                        raise RuntimeError("Export release timed out")
                    exported.extend(row["id"] for row in rows)
                    return "complete offline snapshot"
                def download():
                    try:
                        output.append(owner._qualified_store_html(
                            store, source="E*TRADE", loaded_at=1000, pricing_mode="NORMAL"))
                    except BaseException as error:
                        errors.append(error)
                def interact():
                    try:
                        store.page(sort_by="MAX PROFIT", direction="DESC")
                        later = expiry + timedelta(days=30)
                        store.add_expiration(later, [{**row, "expiry": later} for row in contracts[:2]],
                                             math.scan_vertical_candidates)
                        indexed.set()
                    except BaseException as error:
                        errors.append(error)
                with patch.object(owner, "filterable_verticals_html", side_effect=builder):
                    download_thread = threading.Thread(target=download, daemon=True)
                    interaction_thread = threading.Thread(target=interact, daemon=True)
                    try:
                        download_thread.start()
                        self.assertTrue(reading.wait(3))
                        interaction_thread.start()
                        self.assertTrue(indexed.wait(3), "HTML compression blocked interactive sorting")
                    finally:
                        release.set()
                        download_thread.join(3)
                        if interaction_thread.ident is not None:
                            interaction_thread.join(3)
                self.assertFalse(download_thread.is_alive())
                self.assertFalse(interaction_thread.is_alive())
                self.assertFalse(errors, errors)
                self.assertEqual(output, ["complete offline snapshot"])
                self.assertEqual(exported, original)
                self.assertGreater(len(store), len(original))

    def test_offline_html_delegates_complete_snapshot_without_deferred_ui_calls(self):
        from src.vertical_options_store import VerticalResultStore
        _, rows = quoted_spread()
        today = date(2026, 1, 1)
        expiries = [today + timedelta(days=6), today + timedelta(days=30), today + timedelta(days=60)]
        for owner in (desktop, phone):
            with self.subTest(owner=owner.__name__):
                store = VerticalResultStore("SPY", 100, None, None, "NORMAL")
                for expiry in expiries:
                    store.add_expiration(expiry, [{**row, "expiry": expiry} for row in rows],
                                         math.scan_vertical_candidates)
                candidate = next(row for row in store.iter_ranked() if row["expiry"] == expiries[1])
                lookup = {owner._leg_quote_key(candidate, "buy_"): 0,
                          owner._leg_quote_key(candidate, "sell_"): 1250}
                filters = {"cost": {"max": 0}, "expiry": [expiries[1].isoformat()]}
                captured = {}
                def builder(retained, **options):
                    captured.update(options)
                    captured["rows"] = list(retained.iter_ranked())
                    captured["oi"] = options["contract_open_interest"](candidate)
                    return "<!doctype html>synthetic complete snapshot"
                ui = MagicMock()
                with (patch.object(owner, "_minimum_expiry_date", return_value=today + timedelta(days=7)),
                      patch.object(owner, "st", ui),
                      patch.object(owner, "_table_open_interest", side_effect=AssertionError("UI access")),
                      patch.object(owner, "_chain") as chain,
                      patch.object(owner, "_rank_cached_vertical_chains") as rank,
                      patch.object(owner, "filterable_verticals_html", side_effect=builder)):
                    downloaded = owner._qualified_store_html(
                        store, source="E*TRADE", loaded_at=1000, pricing_mode="NORMAL",
                        column_filters=filters, year=expiries[1].year, sort_by="COST", direction="DESC",
                        contract_open_interest=lookup)
                self.assertTrue(downloaded.startswith("<!doctype html>"))
                self.assertEqual(len(captured["rows"]), 2)
                self.assertEqual({row["expiry"] for row in captured["rows"]}, set(expiries[1:]))
                self.assertEqual(captured["column_filters"], filters)
                self.assertEqual((captured["year"], captured["sort_by"], captured["direction"]),
                                 (expiries[1].year, "COST", "DESC"))
                self.assertEqual(captured["oi"], {"buy": 0, "sell": 1250})
                self.assertEqual(store.count(column_filters=filters), 0)
                ui.assert_not_called()
                self.assertFalse(ui.method_calls)
                chain.assert_not_called()
                rank.assert_not_called()

    def test_phone_quote_policy_matches_desktop_and_reuses_ranked_expirations(self):
        _, rows = quoted_spread()
        raw = {"rows": rows, "symbol": "SPY", "spot": 100}
        ui = order_ui({})
        with (patch.object(phone, "st", ui),
              patch.object(phone, "scan_vertical_candidates", wraps=math.scan_vertical_candidates) as scanner):
            normal, _, _, _ = phone._rank_cached_vertical_chains(raw, None, None, "NORMAL")
            again, _, _, _ = phone._rank_cached_vertical_chains(raw, None, None, "NORMAL")
            self.assertIs(again, normal)
            self.assertEqual(scanner.call_count, 1)
            self.assertTrue(scanner.call_args.kwargs["desktop_fast_fill"])
            smart, _, _, _ = phone._rank_cached_vertical_chains(raw, None, None, "SMART FILL")
            self.assertEqual(scanner.call_count, 2)
            normal_again, _, _, _ = phone._rank_cached_vertical_chains(raw, None, None, "NORMAL")
            self.assertIs(normal_again, normal)
            self.assertEqual(scanner.call_count, 2)
        for mode, store in (("NORMAL", normal), ("SMART FILL", smart)):
            expected, _ = math.scan_vertical_candidates(
                symbol="SPY", spot_last=100, option_rows=rows, target_breakeven=None,
                max_cost=None, pricing_mode=mode, desktop_fast_fill=True)
            self.assertEqual([(r["id"], r["basis"], r["debit"]) for r in store.iter_ranked()],
                             [(r["id"], r["basis"], r["debit"]) for r in expected])
        ui.get_option_chain.assert_not_called()
        ui.preview_order.assert_not_called()
        ui.place_order.assert_not_called()

    def test_both_modes_can_compare_stale_chains_but_cannot_review_a_stale_order(self):
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            with self.subTest(feature=prefix):
                _, rows = quoted_spread()
                loaded_at = time.time() - feature._SCAN_CACHE_SECONDS - 1
                raw = {"rows": rows, "symbol": "SPY", "spot": 100, "scan_id": 44,
                       "loaded_at": loaded_at, "scan_complete": True, "loaded_expirations": 1}
                filters = {"filters": {"cost": {"max": 200}}, "revision": 7}
                state = {"_" + prefix + "_chain_cache": {"E*TRADE:SPY": raw},
                         "_" + prefix + "_column_filters": deepcopy(filters)}
                ui = order_ui(state)
                client, touch = MagicMock(), MagicMock()
                with (patch.object(feature, "st", ui), patch.object(feature, "_chain") as chain):
                    result = {"source": "E*TRADE", "symbol": "SPY", "raw_scan_id": 44,
                              "loaded_at": loaded_at, "target": None, "max_cost": None,
                              "min_reward_risk": 2, "pricing_mode": "NORMAL", "scope": "old",
                              "pricing_policy": feature.VERTICAL_PRICING_POLICY}
                    updated = feature._reprice_cached_vertical_scan(result, "SMART FILL")
                    self.assertIsNotNone(updated)
                    self.assertEqual(updated["pricing_mode"], "SMART FILL")
                    self.assertEqual(updated["loaded_at"], loaded_at)
                    self.assertEqual(state["_" + prefix + "_column_filters"], filters)
                    candidate = updated["candidates"].page(limit=1)[0]
                    self.assertTrue(candidate["orderable"])
                    feature._render_selected_order(client, touch, candidate, updated, True)
                    self.assertIn("MARKET SNAPSHOT EXPIRED", str(ui.warning.call_args))
                    self.assertEqual(client.mock_calls, [])
                    touch.assert_not_called()
                    chain.assert_not_called()
                    self.assertIsNone(feature._reprice_cached_vertical_scan(
                        {**result, "raw_scan_id": 45}, "SMART FILL"))
                    self.assertIsNone(feature._reprice_cached_vertical_scan(
                        {**result, "source": "YFINANCE"}, "SMART FILL"))

    def test_historical_cached_ranking_finishes_in_bounded_passes_without_market_reads(self):
        from datetime import timedelta
        for feature, prefix in ((desktop, "vo"), (phone, "vp")):
            with self.subTest(feature=prefix):
                _, quoted_rows = quoted_spread()
                expiries = [quoted_rows[0]["expiry"] + timedelta(days=7 * index) for index in range(3)]
                rows = [{**row, "expiry": expiry} for expiry in expiries for row in quoted_rows]
                loaded_at = time.time() - feature._SCAN_CACHE_SECONDS - 1
                raw = {"rows": rows, "symbol": "SPY", "spot": 100, "scan_id": 44,
                       "loaded_at": loaded_at, "scan_complete": True, "loaded_expirations": 3,
                       "expirations": expiries, "next_expiry_index": 3, "errors": [],
                       "failed_expirations": []}
                state = {"_" + prefix + "_chain_cache": {"E*TRADE:SPY": raw}}
                ui = order_ui(state)
                client, touch = MagicMock(is_offline=False), MagicMock()
                with (patch.object(feature, "st", ui), patch.object(feature, "_SCAN_RANK_SECONDS", 0),
                      patch.object(feature, "_scan_all_years", side_effect=AssertionError("Network acquisition")) as scan,
                      patch.object(feature, "_chain", side_effect=AssertionError("Broker chain")) as chain):
                    result = {"source": "E*TRADE", "symbol": "SPY", "raw_scan_id": 44,
                              "loaded_at": loaded_at, "target": None, "max_cost": None,
                              "min_reward_risk": 2, "pricing_mode": "NORMAL", "scope": "old",
                              "pricing_policy": feature.VERTICAL_PRICING_POLICY}
                    updated = feature._reprice_cached_vertical_scan(result, "SMART FILL")
                    self.assertEqual(updated["ranked_expirations"], 1)
                    self.assertFalse(updated["scan_complete"])
                    scope = updated["scope"]
                    for expected_expirations in (2, 3):
                        updated = feature._continue_vertical_scan(updated, client, touch)
                        self.assertEqual(updated["ranked_expirations"], expected_expirations)
                        self.assertEqual(updated["loaded_at"], loaded_at)
                        self.assertEqual(updated["scope"], scope)
                    self.assertTrue(updated["scan_complete"])
                    self.assertEqual({row["expiry"] for row in updated["candidates"].iter_rows()}, set(expiries))
                    candidate = updated["candidates"].page(limit=1)[0]
                    feature._render_selected_order(client, touch, candidate, updated, True)
                    self.assertIn("MARKET SNAPSHOT EXPIRED", str(ui.warning.call_args))
                    # Only already acquired complete chains can bypass the acquisition gate.
                    raw["scan_complete"] = False
                    with self.assertRaisesRegex(ValueError, "EXPIRED OR SOURCE CHANGED"):
                        feature._continue_vertical_scan(updated, client, touch)
                    raw["loaded_at"] = time.time()
                    with self.assertRaisesRegex(ValueError, "EXPIRED OR SOURCE CHANGED"):
                        feature._continue_vertical_scan(updated, MagicMock(is_offline=True), touch)
                    raw["scan_complete"] = True
                    raw["failed_expirations"] = [expiries[0]]
                    raw["loaded_at"] = loaded_at
                    with self.assertRaisesRegex(ValueError, "EXPIRED OR SOURCE CHANGED"):
                        feature._continue_vertical_scan(updated, client, touch)
                    self.assertEqual(client.mock_calls, [])
                    scan.assert_not_called()
                    chain.assert_not_called()
                    touch.assert_not_called()

    def test_explicit_mode_wins_over_late_preferences_and_sort_memories_are_separate(self):
        state = {"vp_smart_fill": True, "_vp_pricing_mode_preference": "NORMAL",
                 "_vp_presentation": {"vp_best_pair_sort": "COST", "vp_best_pair_direction": "DESC",
                                      "vp_all_pair_sort": "FILL EST", "vp_all_pair_direction": "ASC"}}
        ui = order_ui(state)
        ui.toggle.return_value = True
        with (patch.object(phone, "st", ui),
              patch.object(phone, "_PRICING_PREFERENCE_COMPONENT") as component):
            phone._remember_pricing_mode()
            self.assertEqual(phone._pricing_mode_flip_switch(), "SMART FILL")
            self.assertEqual([call.kwargs["action"] for call in component.call_args_list], ["write"])
            phone._restore_presentation()
        self.assertEqual(state["vp_best_pair_sort"], "COST")
        self.assertEqual(state["vp_best_pair_direction"], "DESC")
        self.assertEqual(state["vp_all_pair_sort"], "FILL EST")
        self.assertEqual(state["vp_all_pair_direction"], "ASC")
        self.assertFalse(any(key.startswith(("vo_", "_vo_")) for key in state))

    def test_stacked_leg_open_interest_uses_exact_cached_quote_and_keeps_zero(self):
        from copy import deepcopy
        from unittest.mock import patch
        import src.verti_phone_ui as vo
        expiry = date(2029, 1, 19)
        rows = [dict(expiry=expiry, call_put="CALL", strike=100, osi="QQQ-C100",
                     last=6.0, bid=5.9, ask=6.1, open_interest=12345),
                dict(expiry=expiry, call_put="CALL", strike=110, osi="QQQ-C110",
                     last=3.0, bid=2.9, ask=3.1, open_interest=0)]
        raw = {"scan_id": 77, "rows": rows}
        result = {"source": "E*TRADE", "symbol": "QQQ", "raw_scan_id": 77}
        state = {"_vp_chain_cache": {"E*TRADE:QQQ": raw}}
        candidates, annual = math.scan_vertical_candidates(
            symbol="QQQ", spot_last=100, option_rows=rows, target_breakeven=None,
            max_cost=None, max_per_expiration=None, pricing_mode="SMART FILL",
            desktop_fast_fill=True)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        before = deepcopy(candidate)
        with patch.object(vo.st, "session_state", state):
            index = vo._table_open_interest(result)
            self.assertIs(index, vo._table_open_interest(result))
            payload = vo._spread_table_row(candidate, annual, contract_open_interest=index)
            self.assertEqual(payload["open_interest"], {"buy": 12345, "sell": 0})
            self.assertEqual(payload["values"], vo._spread_table_row(candidate, annual)["values"])
            self.assertEqual(candidate, before)  # Read-only presentation; no repricing.
            self.assertEqual(vo._table_open_interest({**result, "raw_scan_id": 78}), {})
            self.assertEqual(vo._table_open_interest({**result, "source": "YFINANCE"}), {})
            # A second quote of the same OSI must not steal OI from the chosen quote.
            rows.append({**rows[0], "last": 6.2, "open_interest": 999})
            updated = vo._table_open_interest(result)
            self.assertIsNot(index, updated)
            self.assertEqual(vo._spread_table_row(candidate, annual,
                             contract_open_interest=updated)["open_interest"]["buy"], 12345)
            # Without matching quotes show unknown, not OI from another contract.
            missing = {**candidate, "sell_osi": "OTHER"}
            self.assertIsNone(vo._spread_table_row(missing, annual,
                              contract_open_interest=updated)["open_interest"]["sell"])


    def test_stacked_open_interest_missing_invalid_and_put_leg_identity(self):
        from unittest.mock import patch
        import src.verti_phone_ui as vo
        expiry = date(2029, 1, 19)
        for invalid in (None, "", -1, float("nan"), float("inf"), 1.5, True):
            with self.subTest(oi=invalid):
                row = dict(expiry=expiry, call_put="PUT", strike=110, osi="QQQ-P110",
                           last=3.0, bid=2.9, ask=3.1, open_interest=invalid)
                raw = {"scan_id": 1, "rows": [row]}
                state = {"_vp_chain_cache": {"E*TRADE:QQQ": raw}}
                result = {"source": "E*TRADE", "symbol": "QQQ", "raw_scan_id": 1}
                with patch.object(vo.st, "session_state", state):
                    index = vo._table_open_interest(result)
                self.assertIsNone(index[vo._leg_quote_key(row)])
        buy = dict(expiry=expiry, call_put="PUT", strike=110, osi="QQQ-P110",
                   last=3.0, bid=2.9, ask=3.1, open_interest=2500)
        sell = {**buy, "strike": 100, "osi": "QQQ-P100", "last": 1.0,
                "bid": .9, "ask": 1.1, "open_interest": 777}
        index = {vo._leg_quote_key(buy): 2500, vo._leg_quote_key(sell): 777}
        candidates, annual = math.scan_vertical_candidates(
            symbol="QQQ", spot_last=100, option_rows=[buy, sell], target_breakeven=None,
            max_cost=None, max_per_expiration=None)
        self.assertEqual(vo._spread_table_row(candidates[0], annual,
                         contract_open_interest=index)["open_interest"], {"buy": 2500, "sell": 777})


    def test_live_review_bypasses_chain_cache_without_affecting_scans(self):
        from unittest.mock import MagicMock, patch
        import src.verti_phone_ui as vo

        class CachedBrokerFixture:
            def __init__(self):
                self.get_option_chain = MagicMock(return_value={"OptionChainResponse": {"OptionPair": []}})

        cached = CachedBrokerFixture()
        touch = MagicMock()
        with patch.object(vo, "CachedETradeClient", CachedBrokerFixture):
            vo._chain(cached, "QQQ", date(2029, 1, 19), touch)
            self.assertNotIn("force_refresh", cached.get_option_chain.call_args.kwargs)
            vo._chain(cached, "QQQ", date(2029, 1, 19), touch, fresh_broker_quote=True)
            self.assertIs(cached.get_option_chain.call_args.kwargs["force_refresh"], True)
            self.assertEqual(touch.call_count, 2)
        direct = MagicMock()
        direct.get_option_chain.return_value = {"OptionChainResponse": {"OptionPair": []}}
        vo._chain(direct, "QQQ", date(2029, 1, 19), touch, fresh_broker_quote=True)
        self.assertNotIn("force_refresh", direct.get_option_chain.call_args.kwargs)


    def test_preference_storage_failure_does_not_block_scanner(self):
        from unittest.mock import patch
        import src.verti_phone_ui as vo
        state = {"_vp_bootstrap_failed": True}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo.st, "html"),
              patch.object(vo, "install_native_switch_theme"),
              patch.object(vo, "_restore_scan_memory"),
              patch.object(vo, "_restore_presentation"),
              patch.object(vo, "_render_verti_phone_fragment") as renderer):
            vo.render_verti_phone(None, lambda: None, vault_key="fixture")
            self.assertEqual(state["_vp_workspace_ready"], "fixture")
            self.assertNotIn("_vp_bootstrap_failed", state)
            renderer.assert_called_once()
            self.assertNotIn("_vp_pricing_mode_hydrated", state)


    def test_delayed_preferences_retry_silently_without_blocking_ui(self):
        from unittest.mock import patch
        import src.verti_phone_ui as vo
        state = {}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo, "_hydrate_vertical_options_pricing", return_value=True) as read,
              patch.object(vo.st, "rerun") as rerun):
            vo._recover_vertical_pricing_without_blocking.__wrapped__()
            read.assert_called_once_with("vp_pricing_background_reader")
            rerun.assert_called_once()
            self.assertNotIn("_vp_pref_recovery_unavailable", state)
        state = {"_vp_pref_recovery_started": 1}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo, "_hydrate_vertical_options_pricing", return_value=False),
              patch.object(vo.time, "monotonic", return_value=30),
              patch.object(vo.st, "rerun") as rerun,
              patch.object(vo.st, "error") as error):
            vo._recover_vertical_pricing_without_blocking.__wrapped__()
            self.assertTrue(state["_vp_pref_recovery_unavailable"])
            rerun.assert_called_once()
            error.assert_not_called()



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
                       "pricing_mode": "NORMAL", "scope": "synthetic-scope",
                       "pricing_policy": phone.VERTICAL_PRICING_POLICY}
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

    def test_review_requotes_exact_legs_then_only_explicit_place_can_send(self):
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
        self.render_order()
        self.client.place_order.assert_not_called()
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

    def test_review_requests_uncached_quote_only_on_explicit_review(self):
        ui = order_ui(self.state, {"vp_preview"})
        touch = MagicMock()
        with (patch.object(phone, "st", ui),
              patch.object(phone, "_chain", return_value=self.rows) as chain):
            phone._render_selected_order(self.client, touch, self.candidate, self.result, True)
        chain.assert_called_once_with(self.client, "SPY", self.candidate["expiry"], touch,
                                      fresh_broker_quote=True)
        self.client.preview_order.assert_called_once()
        self.client.place_order.assert_not_called()

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

    def test_smart_discounted_limit_reviews_and_sends_exact_unchanged_debit(self):
        candidates, _ = math.scan_vertical_candidates(
            symbol="SPY", spot_last=100, option_rows=self.rows,
            target_breakeven=None, max_cost=200, desktop_fast_fill=True, pricing_mode="SMART FILL")
        self.candidate = candidates[0]
        self.result["pricing_mode"] = "SMART FILL"
        self.assertEqual((self.candidate["basis"], self.candidate["debit"]), ("SMART", 1.17))
        before = deepcopy(self.candidate)
        self.render_order({"vp_preview"})
        self.client.preview_order.assert_called_once()
        self.render_order({"vp_send"})
        self.client.place_order.assert_called_once()
        self.assertEqual(self.candidate, before)
        self.assertEqual(self.client.place_order.call_args.args[1]["PlaceOrderRequest"]["Order"][0]["limitPrice"], 1.17)

    def test_send_requotes_and_blocks_out_of_band_without_changing_reviewed_limit(self):
        candidates, _ = math.scan_vertical_candidates(
            symbol="SPY", spot_last=100, option_rows=self.rows,
            target_breakeven=None, max_cost=200, desktop_fast_fill=True, pricing_mode="SMART FILL")
        self.candidate, self.result["pricing_mode"] = candidates[0], "SMART FILL"
        self.render_order({"vp_preview"})
        payload = deepcopy(self.state["vp_review_bundle"]["previews"]["preferred"]["place_payload"])
        ui = self.render_order({"vp_send"}, rows=[{**self.rows[0], "ask": 3.11}, self.rows[1]])
        self.client.place_order.assert_not_called()
        self.assertNotIn("vp_review_bundle", self.state)
        self.assertNotIn("vp_live_confirm", self.state)
        self.assertTrue(any("LIVE SPREAD REQUOTE BLOCKED" in str(call) for call in ui.error.call_args_list))
        self.assertEqual(payload["PlaceOrderRequest"]["Order"][0]["limitPrice"], 1.17)
        self.assertEqual(self.candidate["debit"], 1.17)

    def test_normal_fixed_limit_may_review_when_preferred_price_changes_inside_band(self):
        self.render_order({"vp_preview"}, rows=[{**self.rows[0], "bid": 2.8}, self.rows[1]])
        self.client.preview_order.assert_called_once()
        self.assertEqual(self.client.preview_order.call_args.args[1]["PreviewOrderRequest"]["Order"][0]["limitPrice"], 1.07)

    def test_legacy_policy_cannot_reuse_review_or_reach_broker(self):
        self.result["pricing_policy"] = "desktop-quote-policy-v2"
        self.state["vp_review_bundle"], self.state["vp_live_confirm"] = {"old": True}, True
        self.render_order({"vp_preview", "vp_send"})
        self.client.preview_order.assert_not_called()
        self.client.place_order.assert_not_called()
        self.assertNotIn("vp_review_bundle", self.state)
        self.assertNotIn("vp_live_confirm", self.state)

    def test_cached_fallback_cannot_approve_review_or_live_send(self):
        from src.etrade_data_cache import CachedETradeClient
        original_client = self.client
        self.render_order({"vp_preview"})
        self.client = MagicMock(spec=CachedETradeClient)
        self.client.is_offline = False
        self.client.preview_order, self.client.place_order = MagicMock(), MagicMock()
        self.state["_etrade_cache_last_event"] = {"resource": "option_chain", "stale": True}
        self.render_order({"vp_send"})
        self.client.place_order.assert_not_called()
        self.assertNotIn("vp_review_bundle", self.state)
        self.render_order({"vp_preview"})
        self.client.preview_order.assert_not_called()
        original_client.place_order.assert_not_called()

    def test_preview_expiring_during_send_requote_cannot_place(self):
        self.render_order({"vp_preview"})
        ui = order_ui(self.state, {"vp_send"})
        def slow_quote(*args, **kwargs):
            self.state["vp_review_bundle"]["previewed_at"] -= phone._PREVIEW_FRESH_SECONDS + 1
            return self.rows
        with patch.object(phone, "st", ui), patch.object(phone, "_chain", side_effect=slow_quote):
            phone._render_selected_order(self.client, MagicMock(), self.candidate, self.result, True)
        self.client.place_order.assert_not_called()
        self.assertNotIn("vp_review_bundle", self.state)
        self.assertTrue(any("PREVIEW EXPIRED DURING REQUOTE" in str(call) for call in ui.error.call_args_list))

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
                         {**expected, "breakeven": {"target": 103, "percent": 5.0}})

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
    st.session_state["fixture_component_path"] = tuple(st._main._active_dg._cursor.delta_path)
    if st.session_state.get("fixture_persist_update"):
        return st.session_state.get("fixture_update")
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
    if function is feature._qualified_store_html:
        st.session_state["fixture_html_callback"] = deferred(function, *args, **kwargs)
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
        "pricing_policy":feature.VERTICAL_PRICING_POLICY,
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
      patch.object(feature, "filterable_verticals_html", side_effect=AssertionError("Eager HTML export")),
      patch.object(feature, "partial", side_effect=exporter)):
    feature.FEATURE_RENDER(Broker(), lambda: None, vault_key=vault)
'''.replace("FEATURE_MODULE", module).replace("FEATURE_PREFIX", prefix).replace("FEATURE_RENDER", renderer).replace("FIXTURE_VAULT", vault)
                app = AppTest.from_string(source, default_timeout=15).run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                initial_filter_scope, initial_component_key = args["filter_scope"], args["key"]
                result_key = prefix + "_scan_result"
                filter_key = "_" + prefix + "_column_filters"
                self.assertEqual([field.label for field in app.text_input],
                                 ["Ticker", "MIN RISK:REWARD 1:"])
                self.assertNotIn(prefix + "_max_cost", app.session_state)
                self.assertNotIn("cost", args["column_filters"])
                self.assertIsNone(app.session_state[result_key]["max_cost"])
                self.assertNotIn("scan_max_cost", app.session_state[filter_key])
                html_callback = app.session_state["fixture_html_callback"]
                self.assertTrue(callable(html_callback))
                self.assertEqual(html_callback.keywords["column_filters"],
                                 {**args["column_filters"], "breakeven": {"target": None}})
                self.assertEqual(html_callback.keywords["sort_by"], args["sort_by"])
                self.assertIsInstance(html_callback.keywords["contract_open_interest"], dict)
                cached_oi = app.session_state["_" + prefix + "_chain_cache"]["E*TRADE:QQQ"][
                    "_" + prefix + "_table_open_interest"]["values"]
                self.assertIsNot(html_callback.keywords["contract_open_interest"], cached_oi)
                self.assertNotIn("fixture_scan_calls", app.session_state)
                self.assertTrue(args["sortable"])
                self.assertEqual(args["sort_by"], "YEAR + FILL")
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
                expected_edited = {**edited, "breakeven": {"target": 103, "percent": 5.0}}
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
                        html_callback = app.session_state["fixture_html_callback"]
                        self.assertEqual(html_callback.keywords["column_filters"]["cost"]["max"], cap)
                        controls = {button.proto.label: button.proto.disabled
                                    for button in app.get("download_button")}
                        self.assertEqual(len(controls), 3)
                        self.assertFalse(controls["EXPORT HTML // ALL" if prefix == "vo" else "HTML // ALL"])
                app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["column_filters"], expected_edited)
                self.assertEqual(app.session_state["fixture_args"]["filter_ack_revision"], 4)
                self.assertEqual(app.session_state["fixture_args"]["filter_scope"], initial_filter_scope)
                self.assertEqual(app.session_state["fixture_args"]["key"], initial_component_key)
                self.assertNotIn("fixture_scan_calls", app.session_state)
                self.assertTrue(all(cap is None for cap in app.session_state["fixture_rank_caps"]))

                # Clearing MAX reveals the retained expensive pairs without downloading chains.
                args = app.session_state["fixture_args"]
                without_max = {**edited, "cost": {"min": 75}}
                expected_without_max = {**expected_edited, "cost": {"min": 75}}
                app.session_state["fixture_update"] = {
                    "scope": args["scope"], "column_filters": without_max,
                    "filter_revision": args["filter_ack_revision"] + 1,
                    "selected_ids": [selected_id], "revision": 1}
                app.run()
                self.assertIsNone(app.session_state["fixture_order_cap"])
                app.toggle(key=prefix + "_smart_fill").set_value(False).run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual(args["column_filters"], expected_without_max)
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
                self.assertEqual(restored.session_state["fixture_args"]["column_filters"], expected_without_max)

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

                ranks_before_sort = list(app.session_state["fixture_rank_caps"])
                scans_before_sort = list(app.session_state["fixture_scan_calls"])
                app.session_state["fixture_update"] = {
                    "scope": args["scope"], "column_filters": args["column_filters"],
                    "filter_revision": args["filter_ack_revision"] + 1,
                    "selected_ids": [], "revision": 0,
                    "sort_by": "COST", "sort_direction": "DESC"}
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["sort_by"], "COST")
                app.radio[0].set_value("ALL QUALIFIED").run()
                self.assertEqual(app.session_state["fixture_args"]["sort_by"], "FILL + TARGET")
                app.radio[0].set_value("BEST PER YEAR").run()
                args = app.session_state["fixture_args"]
                self.assertEqual((args["sort_by"], args["sort_direction"]), ("COST", "DESC"))
                self.assertEqual(app.session_state["fixture_rank_caps"], ranks_before_sort)
                self.assertEqual(app.session_state["fixture_scan_calls"], scans_before_sort)

                # A delayed BEST sort updates only that view after switching to ALL.
                component_path = app.session_state["fixture_component_path"]
                app.radio[0].set_value("ALL QUALIFIED").run()
                self.assertEqual(app.session_state["fixture_component_path"], component_path)
                args = app.session_state["fixture_args"]
                app.session_state["fixture_update"] = {
                    "scope": args["scope"], "filter_scope": args["filter_scope"],
                    "column_filters": args["column_filters"],
                    "filter_revision": args["filter_ack_revision"] + 1,
                    "sort_view": "BEST PER YEAR", "sort_by": "MAX PROFIT", "sort_direction": "DESC"}
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["sort_by"], "FILL + TARGET")
                app.radio[0].set_value("BEST PER YEAR").run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual((args["sort_by"], args["sort_direction"]), ("MAX PROFIT", "DESC"))

                # Streamlit keeps a component's last value until the browser emits
                # another one. Returning to NORMAL must not revive an old selection.
                app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                app.toggle(key=prefix + "_smart_fill").set_value(False).run()
                args = app.session_state["fixture_args"]
                original_normal_scope = args["scope"]
                selected_id = app.session_state[result_key]["candidates"].page(limit=1)[0]["id"]
                app.session_state["fixture_persist_update"] = True
                app.session_state["fixture_update"] = {
                    "scope": original_normal_scope, "filter_scope": args["filter_scope"],
                    "column_filters": args["column_filters"], "filter_revision": args["filter_ack_revision"],
                    "selected_ids": [selected_id], "revision": 1}
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["selected_ids"], [selected_id])
                old_pricing_revision = app.session_state[result_key]["pricing_revision"]
                app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                self.assertFalse(app.exception)
                smart_scope = app.session_state["fixture_args"]["scope"]
                self.assertEqual(app.session_state["fixture_args"]["selected_ids"], [])
                app.toggle(key=prefix + "_smart_fill").set_value(False).run()
                app.run()  # The same old payload is still returned on this rerun.
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual(args["selected_ids"], [])
                self.assertEqual(args["ack_revision"], 0)
                self.assertNotIn(args["scope"], (original_normal_scope, smart_scope))
                self.assertEqual(app.session_state[result_key]["pricing_revision"], old_pricing_revision + 2)
                app.session_state["fixture_persist_update"] = False
                del app.session_state["fixture_update"]

                # A mode change must accept a pending filter from the same snapshot,
                # while dropping that old mode's selection and viewport request.
                self.assertNotEqual(args["filter_scope"], initial_filter_scope)
                year_button = next(button for button in app.button
                                   if button.label[:4].isdigit() and " // " in button.label)
                selected_year = int(year_button.label[:4])
                year_button.click().run()
                args = app.session_state["fixture_args"]
                old_scope = args["scope"]
                component_path = app.session_state["fixture_component_path"]
                current_filter_scope, component_key = args["filter_scope"], args["key"]
                current_revision = args["filter_ack_revision"]
                app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertNotEqual(args["scope"], old_scope)
                self.assertEqual(args["filter_scope"], current_filter_scope)
                self.assertEqual(args["key"], component_key)
                self.assertEqual(args["filter_ack_revision"], current_revision)
                self.assertTrue(any(button.label.startswith(str(selected_year) + " // ")
                                    and button.proto.type == "primary" for button in app.button))
                app.session_state["fixture_update"] = {
                    "scope": old_scope, "filter_scope": current_filter_scope,
                    "column_filters": {"side": []}, "filter_revision": current_revision + 1,
                    "selected_ids": [selected_id], "revision": 99,
                    "viewport_offset": 1, "viewport_sort_key": args["sort_key"]}
                app.run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual(args["column_filters"], {"side": []})
                self.assertEqual(args["total_rows"], 0)
                self.assertEqual(app.session_state["fixture_component_path"], component_path)
                self.assertEqual(args["selected_ids"], [])
                self.assertEqual(args["ack_revision"], 0)
                self.assertEqual(args["row_offset"], 0)
                self.assertTrue(any(button.label == f"{selected_year} // 0" and
                                    button.proto.type == "primary" for button in app.button))
                self.assertTrue(any(button.label.startswith("ALL YEARS // ") for button in app.button))

                # Even a genuinely empty repriced universe keeps the filters usable.
                from src.vertical_options_store import VerticalResultStore
                empty_result = dict(app.session_state[result_key])
                empty_result["candidates"] = VerticalResultStore("QQQ", 100, None, None, "SMART FILL")
                empty_result["annual_ids"] = set()
                app.session_state[result_key] = empty_result
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["key"], component_key)
                self.assertEqual(app.session_state["fixture_component_path"], component_path)
                self.assertEqual(app.session_state["fixture_args"]["column_filters"], {"side": []})
                self.assertEqual(app.session_state["fixture_args"]["total_rows"], 0)
                app.toggle(key=prefix + "_smart_fill").set_value(False).run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual(args["column_filters"], {"side": []})
                self.assertTrue(any(button.label == f"{selected_year} // 0" and
                                    button.proto.type == "primary" for button in app.button))
                next(button for button in app.button if button.label.startswith("ALL YEARS // ")).click().run()
                args = app.session_state["fixture_args"]
                app.session_state["fixture_update"] = {
                    "scope": args["scope"], "filter_scope": current_filter_scope,
                    "column_filters": {}, "filter_revision": args["filter_ack_revision"] + 1}
                app.run()
                self.assertFalse(app.exception)
                args = app.session_state["fixture_args"]
                self.assertEqual(args["column_filters"], {})
                self.assertGreater(args["total_rows"], 0)
                acknowledged_revision = args["filter_ack_revision"]
                for rejected_scope, rejected_revision in ((current_filter_scope, acknowledged_revision - 1),
                                                          (initial_filter_scope, acknowledged_revision + 100)):
                    app.session_state["fixture_update"] = {
                        "scope": args["scope"], "filter_scope": rejected_scope,
                        "column_filters": {"side": []}, "filter_revision": rejected_revision}
                    app.run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.session_state["fixture_args"]["column_filters"], {})
                    self.assertEqual(app.session_state["fixture_args"]["filter_ack_revision"], acknowledged_revision)
                app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                app.toggle(key=prefix + "_smart_fill").set_value(False).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["column_filters"], {})
                self.assertEqual(app.session_state["fixture_args"]["filter_ack_revision"], acknowledged_revision)
                self.assertEqual(app.session_state["fixture_scan_calls"], scans_before_sort)

                # Historical mode comparison preserves filters without refreshing quote age.
                component_path = app.session_state["fixture_component_path"]
                import time
                expired_at = time.time() - desktop._SCAN_CACHE_SECONDS - 1
                expired_result = dict(app.session_state[result_key])
                expired_result["loaded_at"] = expired_at
                app.session_state[result_key] = expired_result
                raw_cache = app.session_state["_" + prefix + "_chain_cache"]
                raw_cache["E*TRADE:QQQ"]["loaded_at"] = expired_at
                app.session_state[prefix + "_review_bundle"] = {"old": True}
                app.session_state[prefix + "_live_confirm"] = True
                app.session_state["_" + prefix + "_auto_scan_error"] = "Old ranking failure"
                app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["fixture_args"]["column_filters"], {})
                self.assertEqual(app.session_state["fixture_args"]["filter_ack_revision"], acknowledged_revision)
                self.assertEqual(app.session_state[result_key]["loaded_at"], expired_at)
                self.assertEqual(app.session_state["fixture_component_path"], component_path)
                self.assertTrue(any("STALE SNAPSHOT" in warning.value for warning in app.warning))
                self.assertNotIn(prefix + "_review_bundle", app.session_state)
                self.assertNotIn(prefix + "_live_confirm", app.session_state)
                self.assertNotIn("_" + prefix + "_auto_scan_error", app.session_state)
                self.assertEqual(app.session_state["fixture_scan_calls"], scans_before_sort)

                # A mode with no qualifying dates postpones automatic defaults until
                # another mode has choices; an explicit CLEAR during the empty view wins.
                for explicit_clear in (False, True):
                    raw = dict(app.session_state["_" + prefix + "_chain_cache"]["E*TRADE:QQQ"])
                    raw.update(scan_id=raw["scan_id"] + 1, loaded_at=time.time(), result_stores={})
                    app.session_state["_" + prefix + "_chain_cache"] = {"E*TRADE:QQQ": raw}
                    result = dict(app.session_state[result_key])
                    result.update(raw_scan_id=raw["scan_id"], loaded_at=raw["loaded_at"],
                                  scope="empty-snapshot-" + str(raw["scan_id"]),
                                  candidates=VerticalResultStore("QQQ", 100, None, None, "SMART FILL"),
                                  annual_ids=set())
                    app.session_state[result_key] = result
                    app.run()
                    self.assertFalse(app.exception)
                    self.assertTrue(app.session_state[filter_key]["defaults_pending"])
                    empty_component_path = app.session_state["fixture_component_path"]
                    args = app.session_state["fixture_args"]
                    self.assertEqual(args["total_rows"], 0)
                    if explicit_clear:
                        app.session_state["fixture_update"] = {
                            "scope": args["scope"], "filter_scope": args["filter_scope"],
                            "column_filters": {}, "filter_revision": 1}
                        app.run()
                        self.assertFalse(app.exception)
                        self.assertFalse(app.session_state[filter_key].get("defaults_pending"))
                    app.toggle(key=prefix + "_smart_fill").set_value(False).run()
                    self.assertFalse(app.exception)
                    args = app.session_state["fixture_args"]
                    available = app.session_state[result_key]["candidates"].column_filter_choices()["expiry"]
                    default = (desktop if prefix == "vo" else phone)._default_column_filters(available)
                    self.assertEqual(args["column_filters"], {} if explicit_clear else default)
                    self.assertEqual(app.session_state["fixture_component_path"], empty_component_path)
                    self.assertEqual(args["filter_ack_revision"], 1 if explicit_clear else 0)
                    self.assertFalse(app.session_state[filter_key].get("defaults_pending"))
                    self.assertGreater(args["total_rows"], 0)
                    app.toggle(key=prefix + "_smart_fill").set_value(True).run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.session_state["fixture_args"]["column_filters"],
                                     {} if explicit_clear else default)

                # A same-mode policy upgrade retains filters while invalidating old order state.
                current = dict(app.session_state[result_key])
                previous_scope = current["scope"]
                filters_before_upgrade = deepcopy(app.session_state[filter_key]["filters"])
                current.pop("pricing_policy")
                app.session_state[result_key] = current
                app.session_state[prefix + "_review_bundle"] = {"old-policy": True}
                app.session_state[prefix + "_live_confirm"] = True
                scans_before_upgrade = list(app.session_state["fixture_scan_calls"])
                app.run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state[result_key]["pricing_policy"], math.VERTICAL_PRICING_POLICY)
                self.assertNotEqual(app.session_state[result_key]["scope"], previous_scope)
                self.assertEqual(app.session_state[filter_key]["filters"], filters_before_upgrade)
                self.assertNotIn(prefix + "_review_bundle", app.session_state)
                self.assertNotIn(prefix + "_live_confirm", app.session_state)
                self.assertEqual(app.session_state["fixture_args"]["selected_ids"], [])
                self.assertEqual(app.session_state["fixture_scan_calls"], scans_before_upgrade)

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
