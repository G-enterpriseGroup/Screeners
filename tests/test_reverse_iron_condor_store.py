"""Complete R.I.C. retention, four-leg filters and offline snapshot regressions."""
from __future__ import annotations

import base64
from datetime import date, timedelta
from decimal import Decimal
import gzip
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from src.reverse_iron_condor import scan_ric_candidates
from src import reverse_iron_condor_store as store_module
from src.reverse_iron_condor_store import RICResultStore, filterable_ric_html, indicative_fill_score, ric_strike_distance_pct


ROOT = Path(__file__).resolve().parents[1]


def chain(expiry, count=9):
    rows = []
    for side in ("CALL", "PUT"):
        for index in range(1, count + 1):
            strike = 100 + index if side == "CALL" else 100 - index
            premium = round(3 - .03 * index, 3)
            rows.append({
                "expiry": expiry, "call_put": side, "strike": strike,
                "bid": premium - .005, "ask": premium + .005, "last": premium,
                "osi": f"TEST{expiry:%y%m%d}{side[0]}{strike * 1000:08d}",
                "open_interest": 100 + index, "volume": 20 + index,
                "delta": .3 if side == "CALL" else -.3,
                "gamma": .01, "theta": -.02, "vega": .04,
            })
    return rows


class RICStoreTests(unittest.TestCase):
    def setUp(self):
        self.expiry = date.today() + timedelta(days=45)

    def store(self, *, target=None, fee=0, count=9):
        rows = chain(self.expiry, count)
        result = RICResultStore("TEST", 100, target, None, "NORMAL", fee_per_spread=fee)
        self.addCleanup(result._directory.cleanup)
        result.add_expiration(self.expiry, rows, scan_ric_candidates)
        return result, rows

    def test_complete_mult_page_retention_and_exact_four_leg_roundtrip(self):
        store, rows = self.store()
        expected, winners = scan_ric_candidates(
            symbol="TEST", spot_last=100, option_rows=rows,
            target_breakeven=None, max_cost=None, max_per_expiration=None,
        )
        self.assertGreater(len(expected), 200)
        self.assertEqual(len(store), len(expected))
        self.assertEqual(store.annual_ids, winners)
        restored = store.get_many([row["id"] for row in expected])
        self.assertEqual(set(restored), {row["id"] for row in expected})
        for candidate in expected:
            self.assertEqual(restored[candidate["id"]], candidate)
        final = store.page(offset=len(store) - 1, limit=1)
        self.assertEqual(len(final), 1)
        self.assertEqual(list(store.iter_rows())[-1], final[0])
        self.assertEqual(len(list(store.iter_ranked())), len(store))

    def test_compact_details_omit_reconstructed_copies_and_keep_financial_extras(self):
        store, _ = self.store(fee=2.6, count=5)
        candidate = store.page(limit=1)[0]
        with store._connect() as db:
            packed = db.execute("SELECT details FROM spreads WHERE id=?", [candidate["id"]]).fetchone()[0]
        values = json.loads(packed)
        legacy = json.dumps([candidate.get(key) for key in store_module._DETAILS]
                            + [{key: value for key, value in candidate.items() if key != "expiry"}],
                            separators=(",", ":"), allow_nan=False)
        self.assertEqual(len(values), 13)
        self.assertEqual(values[:12], [candidate.get(key) for key in store_module._DETAILS])
        self.assertLess(len(packed), len(legacy) * .75)
        for key in ("id", "symbol", "spot_last", "grade", "cost", "max_loss", "debit",
                    "breakeven", "buy_strike", "sell_osi"):
            self.assertNotIn(key, values[12])
        for key in ("upper_breakeven", "lower_breakeven", "put_buy_strike", "put_sell_osi",
                    "call_width", "put_width", "option_root", "fee_per_spread", "net_delta"):
            self.assertEqual(values[12][key], candidate[key])

    def test_unknown_future_extras_and_json_containers_survive_full_roundtrip(self):
        rows = chain(self.expiry, count=4)
        expected, _ = scan_ric_candidates(symbol="TEST", spot_last=100, option_rows=rows,
                                          target_breakeven=None, max_cost=None, max_per_expiration=None)
        extras = {"future_metric": {"version": 2, "values": [1, None, True, "retained"]},
                  "future_flag": False, "future_total": 1 << 100}
        store = RICResultStore("TEST", 100, None, None, "NORMAL")
        self.addCleanup(store._directory.cleanup)
        def enriched(**kwargs):
            for candidate in expected:
                kwargs["candidate_sink"](candidate | extras)
        store.add_expiration(self.expiry, rows, enriched)
        restored = store.get_many([candidate["id"] for candidate in expected])
        self.assertEqual(restored, {candidate["id"]: candidate | extras for candidate in expected})

    def test_legacy_and_compact_json_rows_share_unchanged_decoder_and_queries(self):
        store, _ = self.store(count=5)
        expected = list(store.iter_rows())
        candidate = expected[0]
        legacy = json.dumps([candidate.get(key) for key in store_module._DETAILS]
                            + [{key: value for key, value in candidate.items() if key != "expiry"}],
                            separators=(",", ":"), allow_nan=False)
        with store._connect() as db:
            db.execute("UPDATE spreads SET details=? WHERE id=?", [legacy, candidate["id"]])
        self.assertEqual(list(store.iter_rows()), expected)
        self.assertEqual(store.get_many([candidate["id"]])[candidate["id"]], candidate)
        self.assertEqual(store.count(column_filters={"put_buy": {"min": candidate["put_buy_strike"]}}),
                         sum(row["put_buy_strike"] >= candidate["put_buy_strike"] for row in expected))
        self.assertEqual(list(store.iter_rows(sort_by="LOWER BREAKEVEN")),
                         sorted(expected, key=lambda row: (row["lower_breakeven"], row["id"])))

    def test_reconstructed_field_invalid_json_values_still_reject_and_rollback(self):
        rows = chain(self.expiry, count=4)
        candidate = scan_ric_candidates(symbol="TEST", spot_last=100, option_rows=rows,
                                        target_breakeven=None, max_cost=None)[0][0]
        for key in store_module._RECONSTRUCTED_FIELDS | {"future_metric"}:
            for invalid in (float("nan"), float("inf"), -float("inf"), object(),
                            [float("nan")], {"nested": object()}):
                with self.subTest(key=key, value_type=type(invalid).__name__):
                    changed = candidate | {key: invalid}
                    store = RICResultStore("TEST", 100, None, None, "NORMAL")
                    try:
                        def invalid_sink(**kwargs):
                            kwargs["candidate_sink"](changed)
                        with self.assertRaises((TypeError, ValueError)):
                            store.add_expiration(self.expiry, rows, invalid_sink)
                        self.assertEqual(len(store), 0)
                        self.assertEqual(store.ranked_expirations, 0)
                        self.assertFalse(store.annual_ids)
                    finally:
                        store._directory.cleanup()

    def test_json_failure_after_flushed_batch_rolls_back_all_rows_before_retry(self):
        rows = chain(self.expiry, count=4)
        candidate = scan_ric_candidates(symbol="TEST", spot_last=100, option_rows=rows,
                                        target_breakeven=None, max_cost=None)[0][0]
        store = RICResultStore("TEST", 100, None, None, "NORMAL")
        self.addCleanup(store._directory.cleanup)
        def emit(*, invalid, **kwargs):
            for index in range(513):
                row = candidate | {"id": candidate["id"] + f"|{index}"}
                if invalid and index == 512:
                    row["max_loss"] = float("nan")
                kwargs["candidate_sink"](row)
        with self.assertRaises(ValueError):
            store.add_expiration(self.expiry, rows, lambda **kwargs: emit(invalid=True, **kwargs))
        self.assertEqual(store.count(column_filters={"side": ["RIC"]}), 0)
        self.assertEqual(store.ranked_expirations, 0)
        self.assertFalse(store.annual_ids)
        self.assertTrue(store.add_expiration(self.expiry, rows, lambda **kwargs: emit(invalid=False, **kwargs)))
        self.assertEqual(len(store), 513)
        self.assertEqual(len(list(store.iter_rows())), 513)

    def test_put_fields_and_lower_breakeven_filter_the_entire_universe(self):
        store, _ = self.store()
        all_rows = list(store.iter_rows())
        filters = {"put_buy": {"min": 96}, "put_sell": {"max": 93},
                   "put_buy_last": {"min": 2.8}, "put_sell_last": {"max": 2.85},
                   "lower_breakeven": {"min": 95, "max": 98}}
        expected = [row for row in all_rows if row["put_buy_strike"] >= 96
                    and row["put_sell_strike"] <= 93 and row["put_buy_last"] >= 2.8
                    and row["put_sell_last"] <= 2.85 and 95 <= row["lower_breakeven"] <= 98]
        self.assertTrue(expected)
        found = list(store.iter_rows(column_filters=filters))
        self.assertEqual(found, expected)
        self.assertEqual(store.count(column_filters=filters), len(expected))
        self.assertEqual(store.column_filter_choices()["side"], ["RIC"])
        self.assertEqual(store.count(column_filters={"side": []}), 0)
        self.assertEqual(store.count(side="RIC"), len(store))
        with self.assertRaises(ValueError):
            store.count(side="CALL")

    def test_each_new_sort_uses_all_retained_put_legs(self):
        store, _ = self.store()
        mapping = {"PUT BUY STRIKE": "put_buy_strike", "PUT SELL STRIKE": "put_sell_strike",
                   "PUT BUY LAST": "put_buy_last", "PUT SELL LAST": "put_sell_last",
                   "LOWER BREAKEVEN": "lower_breakeven"}
        original = list(store.iter_rows())
        for name, key in mapping.items():
            for direction in ("ASC", "DESC"):
                result = list(store.iter_rows(sort_by=name, direction=direction))
                values = [row[key] for row in result]
                self.assertEqual(values, sorted(values, reverse=direction == "DESC"))
                self.assertEqual({row["id"] for row in result}, {row["id"] for row in original})

    def test_strike_distance_helper_uses_all_four_strikes_and_fixed_spot(self):
        row = {"spot_last":100, "put_buy_strike":98, "put_sell_strike":92,
               "buy_strike":101, "sell_strike":104}
        self.assertEqual(ric_strike_distance_pct(row),8)
        self.assertEqual(ric_strike_distance_pct(row,200),54)
        for key in ("spot_last","put_buy_strike","put_sell_strike","buy_strike","sell_strike"):
            for invalid in (None,0,-1,True,float("nan"),float("inf"),"bad"):
                with self.subTest(key=key,invalid=invalid):
                    self.assertIsNone(ric_strike_distance_pct(row|{key:invalid}))

    def test_strike_distance_numeric_filter_covers_all_rows_and_put_outer_wings(self):
        store, _ = self.store()
        all_rows = list(store.iter_rows())
        def decimal_distance(row):
            return max(abs(Decimal(str(row[key]))-Decimal("100")) for key in
                       ("put_buy_strike","put_sell_strike","buy_strike","sell_strike"))
        filters = {"strike_distance_pct":{"min":3,"max":5}}
        expected = [row for row in all_rows if Decimal(3)<=decimal_distance(row)<=Decimal(5)]
        found = list(store.iter_rows(column_filters=filters))
        self.assertTrue(found)
        self.assertEqual(found,expected)
        self.assertEqual(store.count(column_filters=filters),len(expected))
        self.assertEqual(sum(store.counts_by_year(column_filters=filters).values()),len(expected))
        self.assertTrue(any(decimal_distance(row)==5 for row in found))
        outside_put = next(row for row in all_rows if row["put_sell_strike"]<95 and row["sell_strike"]<=105)
        self.assertNotIn(outside_put,found)
        self.assertGreater(ric_strike_distance_pct(outside_put),5)
        self.assertEqual(store.count(column_filters={"strike_distance_pct":{"max":4.999999995}}),
                         store.count(column_filters={"strike_distance_pct":{"max":5}}))
        self.assertLess(store.count(column_filters={"strike_distance_pct":{"max":4.99999}}),
                        store.count(column_filters={"strike_distance_pct":{"max":5}}))

    def test_strike_distance_sort_preserves_identity_ties_and_paged_order(self):
        store, _ = self.store()
        original = list(store.iter_rows())
        for direction in ("ASC","DESC"):
            expected = sorted(original,key=lambda row:row["id"])
            expected.sort(key=ric_strike_distance_pct,reverse=direction=="DESC")
            actual = list(store.iter_rows(sort_by="MAX STRIKE DISTANCE %",direction=direction))
            self.assertEqual(actual,expected)
            self.assertEqual(store.page(sort_by="MAX STRIKE DISTANCE %",direction=direction,offset=25,limit=30),
                             expected[25:55])

    def test_strike_distance_sql_uses_validated_decimal_spot_and_rejects_unsafe_filters(self):
        store = RICResultStore("TEST",100.005,None,None,"NORMAL")
        self.addCleanup(store._directory.cleanup)
        store.add_expiration(self.expiry,chain(self.expiry,5),scan_ric_candidates)
        rows = list(store.iter_rows())
        row = rows[0]
        expected = float(100*max(abs(Decimal(str(row[key]))-Decimal("100.005")) for key in
                                ("put_buy_strike","put_sell_strike","buy_strike","sell_strike"))/Decimal("100.005"))
        self.assertAlmostEqual(ric_strike_distance_pct(row),expected,places=12)
        exact = {"strike_distance_pct":{"min":expected,"max":expected}}
        self.assertIn(row,list(store.iter_rows(column_filters=exact)))
        for bad in ({"max":True},{"max":"5"},{"max":"0); DROP TABLE spreads;--"},
                    {"max":float("inf")},{"min":float("nan")},{"unknown":5},[],None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.count(column_filters={"strike_distance_pct":bad})
        self.assertEqual(len(list(store.iter_rows())),len(rows))
        for invalid in (0,True,float("nan"),"1); DROP TABLE spreads;--"):
            store.spot = invalid
            with self.subTest(spot=invalid), self.assertRaises(ValueError):
                store.count(column_filters={"strike_distance_pct":{"max":5}})

    def test_put_last_sort_retains_missing_last_quotes_without_fabricating_values(self):
        rows = chain(self.expiry, 5)
        for row in rows:
            if row["call_put"] == "PUT" and row["strike"] == 97:
                row["last"] = None
        store = RICResultStore("TEST", 100, None, None, "NORMAL")
        self.addCleanup(store._directory.cleanup)
        store.add_expiration(self.expiry, rows, scan_ric_candidates)
        for name, key in (("PUT BUY LAST", "put_buy_last"), ("PUT SELL LAST", "put_sell_last")):
            ordered = list(store.iter_rows(sort_by=name))
            self.assertTrue(any(row[key] is None for row in ordered))
            known = [row[key] for row in ordered if row[key] is not None]
            self.assertEqual(known, sorted(known))
            self.assertTrue(all(row[key] is None for row in ordered[len(known):]))

    def test_downside_target_and_tolerance_match_either_breakeven(self):
        store, _ = self.store(target=95)
        rows = list(store.iter_rows(sort_by="TARGET DISTANCE"))
        distances = [min(abs(row["breakeven"] - 95), abs(row["lower_breakeven"] - 95)) for row in rows]
        self.assertEqual(distances, sorted(distances))
        first = rows[0]
        self.assertLess(first["lower_breakeven"], 100)
        target = first["lower_breakeven"]
        filters = {"breakeven": {"target": target, "percent": 0}}
        matched = list(store.iter_rows(column_filters=filters))
        self.assertTrue(matched)
        self.assertTrue(all(min(abs(row["breakeven"] - target), abs(row["lower_breakeven"] - target)) < 1e-8
                            for row in matched))
        self.assertEqual(store.count(max_distance=0, column_filters=filters), len(matched))

    def test_fees_do_not_move_quote_aggressiveness_or_get_lost(self):
        store, _ = self.store(fee=2.6)
        self.assertTrue(len(store))
        row = store.page(limit=1)[0]
        self.assertAlmostEqual(row["max_loss"], row["gross_cost"] + 2.6)
        self.assertEqual(row["fee_per_spread"], 2.6)
        values = store.fill_est_values()
        expected = {int(indicative_fill_score(candidate) + .5) for candidate in store.iter_rows()}
        self.assertEqual(values, sorted(expected))
        threshold = values[0]
        self.assertTrue(all(int(indicative_fill_score(candidate) + .5) > threshold
                            for candidate in store.iter_rows(column_filters={"fill_scale": {"gt": threshold}})))
        self.assertEqual(store.min_reward_risk, 2)
        with self.assertRaises(ValueError):
            RICResultStore("TEST", 100, None, None, "NORMAL", fee_per_spread=True)

    def test_incomplete_expiration_write_rolls_back_and_can_resume(self):
        store = RICResultStore("TEST", 100, None, None, "NORMAL")
        self.addCleanup(store._directory.cleanup)
        rows = chain(self.expiry)
        candidate = scan_ric_candidates(symbol="TEST", spot_last=100, option_rows=rows,
                                        target_breakeven=None, max_cost=None)[0][0]
        def fail(**kwargs):
            kwargs["candidate_sink"](candidate)
            raise RuntimeError("synthetic interruption")
        with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            store.add_expiration(self.expiry, rows, fail)
        self.assertEqual(len(store), 0)
        self.assertEqual(store.count(column_filters={"side": ["RIC"]}), 0)
        self.assertEqual(store.ranked_expirations, 0)
        self.assertTrue(store.add_expiration(self.expiry, rows, scan_ric_candidates))
        self.assertFalse(store.add_expiration(self.expiry, rows, scan_ric_candidates))
        self.assertGreater(len(store), 200)

    def test_pruning_removes_expiration_from_every_access_path(self):
        store, _ = self.store(count=5)
        before = len(store)
        self.assertGreater(before, 0)
        self.assertEqual(store.prune_before(self.expiry + timedelta(days=1)), before)
        self.assertEqual(store.annual_ids, set())
        self.assertEqual(store.expirations, [])
        self.assertEqual(store.page(), [])
        self.assertEqual(list(store.iter_rows()), [])
        self.assertEqual(store.ranked_expirations, 0)

    def snapshot(self):
        store, _ = self.store(count=5)
        urls = {"optionstrat_url": lambda row: "https://optionstrat.com/build/long-iron-condor/TEST/" + row["id"],
                "tradingview_url": lambda row: "https://www.tradingview.com/chart/?symbol=" + row["id"]}
        document = filterable_ric_html(store, source="SYNTHETIC", loaded_at=100,
                                       pricing_mode="NORMAL", **urls,
                                       contract_open_interest=lambda row: {prefix: row[prefix + "_open_interest"]
                                            for prefix in ("buy", "sell", "put_buy", "put_sell")})
        data = re.search(r'<script id="vertical-data"[^>]*>(.*?)</script>', document).group(1)
        metadata = json.loads(re.search(r'<script id="vertical-meta"[^>]*>(.*?)</script>', document).group(1))
        rows = json.loads(gzip.decompress(base64.b64decode(data)))
        return store, document, data, metadata, rows

    def test_offline_export_contains_every_four_leg_identity_quote_and_chart_link(self):
        store, document, _, metadata, rows = self.snapshot()
        self.assertEqual(len(rows), len(store))
        self.assertEqual(metadata["count"], len(store))
        for candidate, packed in zip(store.iter_ranked(), rows):
            for key, index in (("put_buy_strike", 27), ("put_sell_strike", 28),
                               ("lower_breakeven", 33), ("put_buy_osi", 39), ("put_sell_osi", 40),
                               ("put_buy_bid", 41), ("put_sell_ask", 44)):
                self.assertEqual(packed[index], candidate[key])
            self.assertEqual(packed[47], {key: value for key, value in candidate.items() if key != "expiry"})
            self.assertEqual(packed[48],ric_strike_distance_pct(candidate))
            self.assertTrue(packed[45].startswith("https://optionstrat.com/build/long-iron-condor/"))
            self.assertTrue(packed[46].startswith("https://www.tradingview.com/chart/"))
        self.assertIn("LOWER BREAKEVEN", document)
        self.assertIn("PUT BUY OSI", document)
        self.assertIn("R.I.C. // OFFLINE", document)
        self.assertIn('[48,"MAX STRIKE DISTANCE %"]',document)
        self.assertNotIn('[1,"TYPE"]',document)
        self.assertNotIn('id="side"',document)

    def test_offline_oi_keeps_all_four_scanner_normalized_whole_floats(self):
        store, _, _, _, rows = self.snapshot()
        for candidate, packed in zip(store.iter_ranked(), rows):
            for prefix, index in (("buy", 6), ("sell", 7), ("put_buy", 31), ("put_sell", 32)):
                value = candidate[prefix + "_open_interest"]
                self.assertIsInstance(value, float)
                self.assertTrue(value.is_integer())
                self.assertEqual(packed[index], int(value))
                self.assertIsInstance(packed[index], int)
        document = filterable_ric_html(
            store, source="SYNTHETIC", loaded_at=100, pricing_mode="NORMAL",
            contract_open_interest=lambda candidate: {
                "buy": 0.0, "sell": 1.5, "put_buy": True, "put_sell": float("inf"),
            },
        )
        data = re.search(r'<script id="vertical-data"[^>]*>(.*?)</script>', document).group(1)
        invalid = json.loads(gzip.decompress(base64.b64decode(data)))
        for packed in invalid:
            self.assertEqual([packed[index] for index in (6, 7, 31, 32)], [0, None, None, None])

    @unittest.skipUnless(shutil.which("node"), "Node is needed to execute the actual offline worker")
    def test_offline_worker_matches_full_store_filter_and_sort(self):
        store, document, data, metadata, _ = self.snapshot()
        filters = {"put_buy": {"min": 97}, "lower_breakeven": {"max": 98.9}}
        expected = list(store.iter_rows(column_filters=filters, sort_by="PUT SELL STRIKE", direction="DESC"))
        worker = document[document.index("function verticalsWorker(){"):document.index("\n(()=>{", document.index("function verticalsWorker(){"))]
        script = "globalThis.onmessage=null;const output=[];globalThis.postMessage=value=>output.push(value);\n" + worker
        script += "\nverticalsWorker();\n(async()=>{\n"
        script += "await onmessage({data:{type:'init',meta:" + json.dumps(metadata) + ",compressed:" + json.dumps(data) + "}});\n"
        state = {"filters": filters, "sort_by": "PUT SELL STRIKE", "direction": "DESC", "page": 0}
        script += "await onmessage({data:{type:'query',revision:1,state:" + json.dumps(state) + "}});\n"
        script += "await new Promise(resolve=>setTimeout(resolve,80));process.stdout.write(JSON.stringify(output));})();"
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, check=True)
        messages = json.loads(result.stdout)
        self.assertFalse([message for message in messages if message["type"] == "error"])
        page = next(message for message in messages if message["type"] == "page")
        self.assertEqual(page["total"], len(expected))
        self.assertEqual([row[21] for row in page["rows"]], [row["id"] for row in expected[:100]])

    @unittest.skipUnless(shutil.which("node"), "Node is needed to execute the actual offline worker")
    def test_offline_strike_filter_sort_and_three_line_spot_cell_match_store(self):
        store, document, data, metadata, _ = self.snapshot()
        filters = {"strike_distance_pct":{"min":3,"max":4}}
        expected = list(store.iter_rows(column_filters=filters,sort_by="MAX STRIKE DISTANCE %",direction="DESC"))
        worker = document[document.index("function verticalsWorker(){"):document.index("\n(()=>{",document.index("function verticalsWorker(){"))]
        script = "globalThis.onmessage=null;const output=[];globalThis.postMessage=value=>output.push(value);\n"+worker
        script += "\nverticalsWorker();\n(async()=>{\n"
        script += "await onmessage({data:{type:'init',meta:"+json.dumps(metadata)+",compressed:"+json.dumps(data)+"}});\n"
        state = {"filters":filters,"sort_by":"MAX STRIKE DISTANCE %","direction":"DESC","page":0}
        script += "await onmessage({data:{type:'query',revision:1,state:"+json.dumps(state)+"}});\n"
        script += "await new Promise(resolve=>setTimeout(resolve,80));"
        script += "await onmessage({data:{type:'query',revision:2,state:{filters:{strike_distance_pct:{max:'bad'}},page:0}}});"
        script += "await new Promise(resolve=>setTimeout(resolve,80));"
        script += "const row=Array(49).fill(null);row[33]=95;row[10]=106;"
        script += "const edgeRow=Array(49).fill(null);edgeRow[33]=19.221;edgeRow[10]=20.779;"
        script += "process.stdout.write(JSON.stringify({output,lines:ricBreakevenLines(row,100),rounding:ricBreakevenLines(edgeRow,20)}));})();"
        execution = subprocess.run([shutil.which("node"),"-e",script],capture_output=True,text=True,check=True)
        response = json.loads(execution.stdout)
        page = next(message for message in response["output"] if message["type"]=="page")
        self.assertEqual(page["total"],len(expected))
        self.assertEqual([row[21] for row in page["rows"]],[row["id"] for row in expected[:100]])
        self.assertTrue(any(message["type"]=="error" and "strike distance" in message["message"]
                            for message in response["output"]))
        self.assertEqual(response["lines"],[{"label":"L","price":95,"percent":"-5.00%"},
                                            {"label":"SPOT","price":100,"percent":""},
                                            {"label":"U","price":106,"percent":"+6.00%"}])
        self.assertEqual([line["percent"] for line in response["rounding"]],["-3.90%","","+3.90%"])
        self.assertIn('line.append(percent,price)',document)
        script_body = re.search(r'<script>\n([\s\S]+)</script>',document).group(1)
        subprocess.run([shutil.which("node"),"--check"],input=script_body,capture_output=True,text=True,check=True)

    @unittest.skipUnless(shutil.which("node"), "Node is needed to execute the actual offline worker")
    def test_offline_blank_strike_bounds_match_sql_without_coercing_blank_to_zero(self):
        store, document, data, metadata, _ = self.snapshot()
        bounds = [{"max":""},{"min":""},{"min":"","max":3},
                  {"min":3,"max":""},{"min":None,"max":""},{"min":0,"max":0}]
        states = [{"filters":{"strike_distance_pct":value},"sort_by":"MAX STRIKE DISTANCE %",
                   "direction":"ASC","page":0} for value in bounds]
        expected = [list(store.iter_rows(column_filters=state["filters"],
                                         sort_by=state["sort_by"],direction=state["direction"]))
                    for state in states]
        self.assertEqual(len(expected[0]),len(store))
        self.assertEqual(expected[-1],[])
        worker = document[document.index("function verticalsWorker(){"):document.index("\n(()=>{",document.index("function verticalsWorker(){"))]
        script = "globalThis.onmessage=null;const output=[];globalThis.postMessage=value=>output.push(value);\n"+worker
        script += "\nverticalsWorker();\n(async()=>{\n"
        script += "await onmessage({data:{type:'init',meta:"+json.dumps(metadata)+",compressed:"+json.dumps(data)+"}});\n"
        script += "let revision=0;for(const state of "+json.dumps(states)+"){"
        script += "await onmessage({data:{type:'query',revision:++revision,state}});"
        script += "await new Promise(resolve=>setTimeout(resolve,80));}"
        script += "process.stdout.write(JSON.stringify(output));})();"
        execution = subprocess.run([shutil.which("node"),"-e",script],capture_output=True,text=True,check=True)
        messages = json.loads(execution.stdout)
        self.assertFalse([message for message in messages if message["type"]=="error"])
        pages = [message for message in messages if message["type"]=="page"]
        self.assertEqual(len(pages),len(expected))
        for page,rows in zip(pages,expected):
            self.assertEqual(page["total"],len(rows))
            self.assertEqual([row[21] for row in page["rows"]],[row["id"] for row in rows[:100]])

    @unittest.skipUnless(shutil.which("node"), "Node is needed for component JavaScript checks")
    def test_private_components_parse_and_allow_only_exact_four_leg_tv_chart(self):
        source = (ROOT / "src/components/ric_spread_scroller_v1/index.html").read_text()
        pricing = (ROOT / "src/components/ric_pricing_preference_v1/index.html").read_text()
        for html in (source, pricing):
            script = re.search(r'<script>([\s\S]+)</script>', html).group(1)
            subprocess.run([shutil.which("node"), "--check"], input=script, capture_output=True, text=True, check=True)
        function = source[source.index("function safeTradingViewUrl("):source.index("function safeOptionStratUrl(")]
        formula = "OPRA:TEST270115P95.0-OPRA:TEST270115P90.0+OPRA:TEST270115C105.0-OPRA:TEST270115C110.0"
        urls = ["https://www.tradingview.com/chart/?symbol=" + formula.replace("+", "%2B"),
                "https://www.tradingview.com/chart/?symbol=OPRA:TEST270115C105.0-OPRA:TEST270115C110.0",
                "https://example.test/chart/?symbol=" + formula.replace("+", "%2B")]
        script = function + "\nprocess.stdout.write(JSON.stringify(" + json.dumps(urls) + ".map(safeTradingViewUrl)));"
        result = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, check=True)
        accepted = json.loads(result.stdout)
        self.assertTrue(accepted[0])
        self.assertEqual(accepted[1:], ["", ""])
        self.assertIn('raj-terminal-ric-pricing-mode-v1', pricing)
        self.assertNotIn('raj-terminal-vo-pricing-mode-v1', pricing)
        self.assertIn('const ROW_HEIGHT=74;', source)


if __name__ == "__main__":
    unittest.main()
