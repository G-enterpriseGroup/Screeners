"""Complete R.I.C. retention, four-leg filters and offline snapshot regressions."""
from __future__ import annotations

import base64
from datetime import date, timedelta
import gzip
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from src.reverse_iron_condor import scan_ric_candidates
from src.reverse_iron_condor_store import RICResultStore, filterable_ric_html, indicative_fill_score


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
            self.assertTrue(packed[45].startswith("https://optionstrat.com/build/long-iron-condor/"))
            self.assertTrue(packed[46].startswith("https://www.tradingview.com/chart/"))
        self.assertIn("LOWER BREAKEVEN", document)
        self.assertIn("PUT BUY OSI", document)
        self.assertIn("R.I.C. // OFFLINE", document)

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
