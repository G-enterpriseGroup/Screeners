"""Offline CBOE GEX source and routing regression checks.

Run: python scripts/test_gex_cboe.py
Requires production requirements. No live CBOE or broker request is made.
"""

from datetime import datetime, timedelta
from pathlib import Path
import copy
import math
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import gex_cboe
from src import gex_ui as core
from src import gex_ui_v3_base as base


def occ(symbol: str, expiry, cp: str, strike: float) -> str:
    return f"{symbol}{expiry.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


class CboeGexTests(unittest.TestCase):
    def setUp(self):
        self.today = datetime.now(ZoneInfo("America/New_York")).date()
        self.expiry = self.today + timedelta(days=30)
        self.source_url = "https://cdn.cboe.com/api/global/delayed_quotes/options/SPY.json"
        self.payload = {
            "data": {
                "current_price": 100.0,
                "options": [
                    {
                        "option": occ("SPY", self.expiry, "C", 95.0),
                        "open_interest": 100,
                        "iv": 0.20,
                        "gamma": 0.010,
                    },
                    {
                        "option": occ("SPY", self.expiry, "P", 95.0),
                        "open_interest": 300,
                        "iv": 0.22,
                        "gamma": 0.012,
                    },
                    {
                        "option": occ("SPY", self.expiry, "C", 105.0),
                        "open_interest": 400,
                        "iv": 0.21,
                        "gamma": 0.015,
                    },
                    {
                        "option": occ("SPY", self.expiry, "P", 105.0),
                        "open_interest": 50,
                        "iv": 0.23,
                        "gamma": 0.009,
                    },
                ],
            }
        }

    def test_cboe_result_shape_and_wall_signs(self):
        with patch.object(
            gex_cboe,
            "_fetch_cboe_chain",
            return_value=(self.payload, self.source_url),
        ), patch.object(gex_cboe, "_estimate_apps_script_gamma_flip", return_value=101.25):
            result = gex_cboe.build_cboe_gex(
                "SPY",
                45,
                "America/New_York",
                "COMPONENT_GEX",
            )

        self.assertEqual(result["symbol"], "SPY")
        self.assertEqual(result["spot"], 100.0)
        self.assertEqual(result["contractsUsed"], 4)
        self.assertEqual(result["sourceUrl"], self.source_url)
        self.assertEqual(result["gammaFlip"], 101.25)
        self.assertEqual(result["callWall"]["strike"], 105.0)
        self.assertEqual(result["putWall"]["strike"], 95.0)
        self.assertGreater(result["callWall"]["value"], 0)
        self.assertLess(result["putWall"]["value"], 0)
        self.assertIn("SPOT,100,0", result["packed"])
        self.assertIn("GFLIP,101.25,0", result["packed"])
        self.assertIn("Source URL: " + self.source_url, result["summaryText"])
        self.assertTrue(result["snapshotFingerprint"])
        self.assertEqual(result["snapshotFingerprintVersion"], "CBOE1")
        self.assertEqual(result["snapshotOptionCount"], len(self.payload["data"]["options"]))
        self.assertIn("CBOE Snapshot Fetched:", result["summaryText"])
        self.assertIn(
            "CBOE GEX Input Fingerprint: CBOE1:" + result["snapshotFingerprint"],
            result["summaryText"],
        )
        bridge_summary = base._google_sheets_summary_text(result)
        self.assertIn("CBOE Snapshot Fetched:", bridge_summary)
        self.assertIn(
            "CBOE GEX Input Fingerprint: CBOE1:" + result["snapshotFingerprint"],
            bridge_summary,
        )
        rows = {row["strike"]: row for row in result["rawRows"]}
        self.assertAlmostEqual(rows[95.0]["call_gex"], 10_000.0)
        self.assertAlmostEqual(rows[95.0]["put_gex"], -36_000.0)
        self.assertAlmostEqual(rows[95.0]["net_gex"], -26_000.0)
        self.assertAlmostEqual(rows[105.0]["call_gex"], 60_000.0)
        self.assertAlmostEqual(rows[105.0]["put_gex"], -4_500.0)


    def test_snapshot_fingerprint_is_stable_and_gex_input_sensitive(self):
        first = gex_cboe._snapshot_fingerprint(self.payload)
        reordered = copy.deepcopy(self.payload)
        reordered["data"]["options"] = list(reversed(reordered["data"]["options"]))
        self.assertEqual(first, gex_cboe._snapshot_fingerprint(reordered))

        changed = copy.deepcopy(self.payload)
        changed["data"]["options"][0]["gamma"] = 0.011
        self.assertNotEqual(first, gex_cboe._snapshot_fingerprint(changed))

        irrelevant = copy.deepcopy(self.payload)
        irrelevant["data"]["options"][0]["bid"] = 99.99
        self.assertEqual(first, gex_cboe._snapshot_fingerprint(irrelevant))

    def test_overview_no_longer_injects_iv_rank_column(self):
        root = Path(__file__).resolve().parents[1]
        ui = (root / "src/gex_ui_v3.py").read_text(encoding="utf-8")
        self.assertNotIn("IV RANK / REF", ui)
        self.assertIn("gexv3-snapshot-fp", ui)
        self.assertIn("snapshotFingerprint", ui)

    def test_code_gs_wall_selection_semantics(self):
        payload = copy.deepcopy(self.payload)
        payload["data"]["options"].extend(
            [
                {
                    "option": occ("SPY", self.expiry, "C", 90.0),
                    "open_interest": 50,
                    "iv": 0.20,
                    "gamma": 0.100,
                },
                {
                    "option": occ("SPY", self.expiry, "P", 90.0),
                    "open_interest": 500,
                    "iv": 0.20,
                    "gamma": 0.012,
                },
                {
                    "option": occ("SPY", self.expiry, "C", 110.0),
                    "open_interest": 1000,
                    "iv": 0.20,
                    "gamma": 0.001,
                },
                {
                    "option": occ("SPY", self.expiry, "P", 110.0),
                    "open_interest": 10,
                    "iv": 0.20,
                    "gamma": 0.001,
                },
            ]
        )

        with patch.object(
            gex_cboe,
            "_fetch_cboe_chain",
            return_value=(payload, self.source_url),
        ), patch.object(
            gex_cboe,
            "_estimate_apps_script_gamma_flip",
            return_value=100.0,
        ):
            component = gex_cboe.build_cboe_gex(
                "SPY", 45, "America/New_York", "COMPONENT_GEX"
            )
            net = gex_cboe.build_cboe_gex(
                "SPY", 45, "America/New_York", "NET_GEX"
            )

        # Supplied Code.gs selects CALLWALL by max call OI, not max call GEX.
        self.assertEqual(component["maxCallOi"]["strike"], 110.0)
        self.assertEqual(component["callWall"]["strike"], 110.0)

        # Supplied Code.gs selects PUTWALL by minimum net GEX, not minimum
        # put-component GEX.  90 has the larger put component, while 95 has
        # the more negative combined GEX.
        rows = {row["strike"]: row for row in component["rawRows"]}
        self.assertLess(rows[90.0]["put_gex"], rows[95.0]["put_gex"])
        self.assertLess(rows[95.0]["net_gex"], rows[90.0]["net_gex"])
        self.assertEqual(component["putWall"]["strike"], 95.0)
        self.assertEqual(net["putWall"]["strike"], 95.0)

    def test_gamma_flip_matches_supplied_code_gs_scan(self):
        contracts = [
            {"cp": "P", "strike": 95.0, "dte": 30, "oi": 500.0, "iv": 0.22},
            {"cp": "C", "strike": 105.0, "dte": 30, "oi": 500.0, "iv": 0.22},
        ]
        spot = 100.0

        def total_gex(test_spot):
            total = 0.0
            for contract in contracts:
                sigma = max(float(contract["iv"]), 0.0001)
                t = max(float(contract["dte"]) / 365.0, 0.5 / 365.0)
                sqrt_t = math.sqrt(t)
                d1 = (
                    math.log(test_spot / float(contract["strike"]))
                    + (core.RISK_FREE_RATE + 0.5 * sigma * sigma) * t
                ) / (sigma * sqrt_t)
                gamma = (
                    math.exp(-0.5 * d1 * d1)
                    / math.sqrt(2.0 * math.pi)
                    / (test_spot * sigma * sqrt_t)
                )
                exposure = (
                    gamma
                    * float(contract["oi"])
                    * core.CONTRACT_SIZE
                    * test_spot
                    * test_spot
                    * 0.01
                )
                total += exposure if contract["cp"] == "C" else -exposure
            return total

        strikes = sorted(contract["strike"] for contract in contracts)
        low = max(0.01, min(strikes[0], spot * 0.50))
        high = max(strikes[-1], spot * 1.50)
        expected = None
        best_distance = None
        prev_px = low
        prev_gex = total_gex(prev_px)
        for index in range(1, gex_cboe.GAMMA_FLIP_STEPS + 1):
            px = low + (high - low) * index / gex_cboe.GAMMA_FLIP_STEPS
            now_gex = total_gex(px)
            crossed = (
                (prev_gex < 0 and now_gex > 0)
                or (prev_gex > 0 and now_gex < 0)
                or now_gex == 0
            )
            if crossed and abs(now_gex - prev_gex) > 0:
                candidate = prev_px - prev_gex * (px - prev_px) / (now_gex - prev_gex)
                distance = abs(candidate - spot)
                if best_distance is None or distance < best_distance:
                    expected = candidate
                    best_distance = distance
            prev_px = px
            prev_gex = now_gex

        actual = gex_cboe._estimate_apps_script_gamma_flip(contracts, spot)
        self.assertIsNotNone(expected)
        self.assertIsNotNone(actual)
        self.assertAlmostEqual(actual, expected, places=8)

    def test_net_wall_mode_matches_production_semantics(self):
        with patch.object(
            gex_cboe,
            "_fetch_cboe_chain",
            return_value=(self.payload, self.source_url),
        ), patch.object(gex_cboe, "_estimate_apps_script_gamma_flip", return_value=100.0):
            component = gex_cboe.build_cboe_gex(
                "SPY", 45, "America/New_York", "COMPONENT_GEX"
            )
            net = gex_cboe.build_cboe_gex(
                "SPY", 45, "America/New_York", "NET_GEX"
            )

        self.assertEqual(component["callWall"]["strike"], net["callWall"]["strike"])
        self.assertEqual(component["putWall"]["strike"], net["putWall"]["strike"])
        self.assertNotEqual(component["callWall"]["value"], net["callWall"]["value"])
        self.assertNotEqual(component["putWall"]["value"], net["putWall"]["value"])

    def test_master_source_defaults_to_cboe_and_round_trips(self):
        state = copy.deepcopy(core.DEFAULT_STATE)
        self.assertEqual(core._clean_state(state)["master_a6_source"], "CBOE")
        state["master_a6_source"] = "ETRADE"
        self.assertEqual(core._clean_state(state)["master_a6_source"], "ETRADE")
        state["master_a6_source"] = "invalid"
        self.assertEqual(core._clean_state(state)["master_a6_source"], "CBOE")

    def test_source_urls_and_tabs_are_separate(self):
        root = Path(__file__).resolve().parents[1]
        base = (root / "src/gex_ui_v3_base.py").read_text(encoding="utf-8")
        bridge = (root / "src/gex_github_bridge.py").read_text(encoding="utf-8")
        ui = (root / "src/gex_ui_v3.py").read_text(encoding="utf-8")

        self.assertIn('"E*TRADE OVERVIEW"', base)
        self.assertIn('"CBOE OVERVIEW"', base)
        self.assertIn('PATH = "bridge/latest_gex.txt"', bridge)
        self.assertIn('CBOE_PATH = "bridge/latest_gex_cboe.txt"', bridge)
        self.assertIn('"MASTER A6 SOURCE"', ui)
        self.assertIn("publish_latest_gex_cboe", ui)


def live_source_smoke() -> None:
    payload, url = gex_cboe._fetch_cboe_chain("SPY")
    options = ((payload.get("data") or {}).get("options") or [])
    spot = gex_cboe._extract_spot(payload)
    if not options:
        raise AssertionError("CBOE SPY response contained no options")
    if spot is None or spot <= 0:
        raise AssertionError("CBOE SPY response contained no usable spot price")
    fingerprint = gex_cboe._snapshot_fingerprint(payload)
    print(f"LIVE CBOE PASS // SPY // {len(options)} OPTIONS // SPOT {spot} // FP {fingerprint[:12].upper()} // {url}")


if __name__ == "__main__":
    if "--live" in sys.argv:
        live_source_smoke()
    else:
        unittest.main()
