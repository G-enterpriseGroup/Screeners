"""Offline CBOE GEX source and routing regression checks.

Run: python scripts/test_gex_cboe.py
Requires production requirements. No live CBOE or broker request is made.
"""

from datetime import datetime, timedelta
from pathlib import Path
import copy
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import gex_cboe
from src import gex_ui as core


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
        ), patch.object(core, "_estimate_gamma_flip", return_value=101.25):
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

    def test_net_wall_mode_matches_production_semantics(self):
        with patch.object(
            gex_cboe,
            "_fetch_cboe_chain",
            return_value=(self.payload, self.source_url),
        ), patch.object(core, "_estimate_gamma_flip", return_value=100.0):
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
    print(f"LIVE CBOE PASS // SPY // {len(options)} OPTIONS // SPOT {spot} // {url}")


if __name__ == "__main__":
    if "--live" in sys.argv:
        live_source_smoke()
    else:
        unittest.main()
