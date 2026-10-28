"""Offline CBOE GEX source and routing regression checks.

Run: python scripts/test_gex_cboe.py
Requires production requirements. No live CBOE or broker request is made.
"""

from datetime import datetime, timedelta
from pathlib import Path
import copy
import math
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import gex_cboe
from src import gex_ui as core
from src import gex_ui_v3 as ui
from src import gex_ui_v3_base as base
from src import gex_snapshot_cache as memory


def occ(symbol: str, expiry, cp: str, strike: float) -> str:
    return f"{symbol}{expiry.strftime('%y%m%d')}{cp}{int(round(strike * 1000)):08d}"


class CboeGexTests(unittest.TestCase):
    def test_retry_control_uses_cached_payload_and_renders_verified_receipt(self):
        from streamlit.testing.v1 import AppTest
        source = '''
import streamlit as st
from src import gex_ui_v3 as ui
snapshot = ui.cboe_snapshot("retry-control")
snapshot.setdefault("publication", {"payload": "saved-payload", "status": "ERROR",
                                    "error": "GitHub HTTP 403", "exported_at": "source-export"})
ui._render_cboe_publication_status("retry-control")
'''
        with (patch.object(ui, "_gex_github_token", return_value="token"),
              patch.object(ui, "publish_latest_gex_cboe", return_value="verified-commit") as publish,
              patch.object(ui, "build_cboe_gex") as fetch):
            app = AppTest.from_string(source).run()
            self.assertFalse(app.exception)
            self.assertIn("GitHub HTTP 403", app.error[0].value)
            self.assertEqual(len(app.get("download_button")), 1)
            app.button(key="gexv3_cboe_retry_publish").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertIn("GITHUB VERIFIED verified-com", app.caption[0].value)
        publish.assert_called_once_with("token", "saved-payload")
        fetch.assert_not_called()

    def test_cached_publication_retry_survives_restart_without_chain_fetch(self):
        key = "publication-recovery"
        snapshot = gex_cboe.cboe_snapshot(key)
        snapshot["results"] = {"SPY": {"symbol": "SPY", "updated": "2026-10-22T12:00:00-04:00"}}
        snapshot["publication"] = {"payload": '"Ticker: SPY\nCALLWALL,800,1"',
                                   "exported_at": "2026-10-22T12:00:00-04:00"}
        with (patch.object(ui, "_gex_github_token", return_value="token"),
              patch.object(ui, "publish_latest_gex_cboe", side_effect=RuntimeError("GitHub HTTP 401")),
              patch.object(ui.st, "session_state", {})):
            ui._publish_cached_cboe_payload(key)
        self.assertEqual(snapshot["publication"]["status"], "ERROR")
        saved_payload = snapshot["publication"]["payload"]
        snapshot.clear()
        snapshot.update(results={}, failures={}, updated="")
        memory._read_server_snapshot(key)
        self.assertEqual(snapshot["publication"]["payload"], saved_payload)
        with (patch.object(ui, "_gex_github_token", return_value="new-token"),
              patch.object(ui, "build_cboe_gex") as fetch,
              patch.object(ui, "publish_latest_gex_cboe", return_value="verified-commit") as publish,
              patch.object(ui.st, "session_state", {})):
            ui._publish_cached_cboe_payload(key)
        fetch.assert_not_called()
        publish.assert_called_once_with("new-token", saved_payload)
        self.assertEqual(snapshot["publication"]["commit"], "verified-commit")
        self.assertEqual(snapshot["results"]["SPY"]["updated"], "2026-10-22T12:00:00-04:00")
        completed = copy.deepcopy(snapshot["publication"])
        snapshot["publication"].update(status="PENDING")
        snapshot["publication"].pop("completed_at")
        memory._restore(key, {"cboe": {"publication": completed}})
        self.assertEqual(snapshot["publication"]["status"], "OK")

    def test_unchanged_levels_have_different_export_bytes_and_valid_pine_rows(self):
        key = "export-freshness"
        result = {"symbol": "SPY", "gammaFlip": 100,
                  "callWall": {"strike": 110, "value": 10},
                  "putWall": {"strike": 90, "value": -10},
                  "maxCallOi": {"strike": 110, "call_oi": 5},
                  "maxPutOi": {"strike": 90, "put_oi": 5},
                  "topGex": [{"strike": 110, "net_gex": 10}],
                  "snapshotFetchedAt": "2026-10-22T12:00:00-04:00"}
        gex_cboe.cboe_snapshot(key)["results"] = {"SPY": result}
        with (patch.object(ui, "_gex_github_token", return_value="token"),
              patch.object(ui, "publish_latest_gex_cboe", return_value="commit") as publish,
              patch.object(ui.st, "session_state", {}),
              patch.object(ui, "_CBOE_MASTER_A6_STATIC_PATH", memory._SERVER_CACHE_DIR / "cboe.txt")):
            ui._publish_cboe_master_a6(key, {"tickers": ["SPY"]})
            ui._publish_cboe_master_a6(key, {"tickers": ["SPY"]})
        payloads = [call.args[1] for call in publish.call_args_list]
        self.assertNotEqual(payloads[0], payloads[1])
        for payload in payloads:
            self.assertTrue(payload.startswith('"Ticker: SPY\n'))
            self.assertIn("CBOE Exported:", payload)
            self.assertEqual(gex_cboe.cboe_results(key)["SPY"]["snapshotFetchedAt"],
                             "2026-10-22T12:00:00-04:00")
            self.assertEqual(ui._cboe_pine_ticker_issues(payload[1:-1], "SPY")[0], [])

    def test_server_backup_skips_unchanged_writes_and_recovers_corrupt_latest(self):
        key = "server-recovery"
        snapshot = gex_cboe.cboe_snapshot(key)
        snapshot["results"] = {"SPY": {"updated": "2026-10-22T12:00:00-04:00", "spot": 100}}
        self.assertTrue(memory._save_server_snapshot(key))
        path = memory._server_snapshot_path(key)
        with patch.object(memory.os, "replace", wraps=memory.os.replace) as replace:
            self.assertTrue(memory._save_server_snapshot(key))
        replace.assert_not_called()
        snapshot["results"]["SPY"]["spot"] = 101
        self.assertTrue(memory._save_server_snapshot(key))
        path.write_text("broken JSON", encoding="utf-8")
        snapshot["results"].clear()
        memory._read_server_snapshot(key)
        self.assertEqual(snapshot["results"]["SPY"]["spot"], 100)

    def test_cache_includes_morningstar_and_background_results_without_ui_sync(self):
        key = "all-gex-sources"
        basket = gex_cboe.cboe_snapshot(key + ":morningstar")
        basket.update(results={"MSFT": {"spot": 500, "updated": "2026-10-22T12:00:00-04:00"}},
                      tickers=["MSFT"], updated="2026-10-22T12:00:00-04:00")
        etrade = {"QQQ": {"spot": 600, "updated": "2026-10-22T12:00:00-04:00"}}
        self.assertTrue(memory._save_server_snapshot(key, etrade_results=etrade))
        self.assertEqual(core._results(key)["QQQ"]["spot"], 600)

        self.assertTrue(memory._save_server_snapshot(key))  # Unrelated later render retains the result.
        basket.clear()
        basket.update(results={})
        core._results(key).clear()
        memory._read_server_snapshot(key)
        self.assertEqual(basket["tickers"], ["MSFT"])
        self.assertEqual(basket["results"]["MSFT"]["spot"], 500)
        self.assertEqual(core._results(key)["QQQ"]["spot"], 600)
        # A later browser backup must also restore its newer basket/list metadata.
        newer = {"morningstar": {"results": {}, "tickers": ["AAPL"],
                                 "updated": "2026-10-22T13:00:00-04:00"}}
        memory._restore(key, newer)
        self.assertEqual(basket["tickers"], ["AAPL"])
        memory._restore(key, {"morningstar": {"results": 123}})

    def test_disk_failure_does_not_abort_publication_or_destroy_memory(self):
        key = "disk-failure"
        snapshot = gex_cboe.cboe_snapshot(key)
        snapshot["publication"] = {"payload": "payload"}
        with (patch.object(memory, "_SERVER_CACHE_DIR", Path("/dev/null/gex")),
              patch.object(memory.logging, "getLogger"),
              patch.object(ui, "_gex_github_token", return_value="token"),
              patch.object(ui, "publish_latest_gex_cboe", return_value="commit"),
              patch.object(ui.st, "session_state", {})):
            ui._publish_cached_cboe_payload(key)
        self.assertEqual(snapshot["publication"]["status"], "OK")

    def test_snapshot_backup_restores_both_sources_only_after_browser_hydration(self):
        state = {}
        cboe = {"results": {"SPY": {"callWall": {"strike": 110}, "snapshotFetchedAt": "2026-10-21T12:00:00-04:00"}},
                "updated": "2026-10-21T12:00:00-04:00", "failures": {}}
        etrade = {"QQQ": {"putWall": {"strike": 500}, "updated": "2026-10-21T12:00:00-04:00"}}
        with (patch.object(memory.st, "session_state", state),
              patch.object(memory, "_MEMORY") as component,
              patch.object(memory, "cboe_snapshot", side_effect=lambda key: (
                  {"results": {}} if key.endswith(":morningstar") else cboe)),
              patch.object(memory.gex_ui, "_results", return_value=etrade)):
            memory.persist_gex_snapshots("first-vault")
            component.assert_not_called()
            component.return_value = None
            self.assertFalse(memory.hydrate_gex_snapshots("first-vault"))
            self.assertEqual(component.call_args.kwargs["mode"], "read")
            component.return_value = {"source": "empty", "state": None}
            self.assertTrue(memory.hydrate_gex_snapshots("first-vault"))
            memory.persist_gex_snapshots("first-vault")
            stored = copy.deepcopy(component.call_args.kwargs["server_state"])
            first_key = component.call_args.kwargs["storage_key"]
            self.assertEqual(memory._unpack(stored), {"cboe": cboe, "etrade": etrade})
            cboe["results"].clear()
            etrade.clear()
            state.clear()  # Simulate fresh process/session with only browser bytes left.
            component.return_value = {"source": "browser", "state": stored}
            self.assertTrue(memory.hydrate_gex_snapshots("first-vault"))
            self.assertEqual(cboe["results"]["SPY"]["callWall"]["strike"], 110)
            self.assertEqual(etrade["QQQ"]["putWall"]["strike"], 500)
            component.return_value = {"source": "empty", "state": None}
            self.assertTrue(memory.hydrate_gex_snapshots("second-vault"))
            self.assertNotEqual(first_key, component.call_args.kwargs["storage_key"])
            # A browser backup must never replace fresher server calculations.
            cboe["results"]["SPY"]["snapshotFetchedAt"] = "2026-10-21T13:00:00-04:00"
            cboe["results"]["SPY"]["callWall"]["strike"] = 120
            memory._restore("first-vault", memory._unpack(stored))
            self.assertEqual(cboe["results"]["SPY"]["callWall"]["strike"], 120)
            self.assertEqual(memory._unpack({"packed": "broken data"}), {})

    def test_failed_cboe_refresh_keeps_last_success_while_pruning_removed_tickers(self):
        saved = {"results": {"SPY": {"callWall": {"strike": 110}}, "REMOVED": {}}, "failures": {}}
        with (patch.object(ui, "build_cboe_gex", side_effect=ValueError("feed unavailable")),
              patch.object(ui, "cboe_snapshot", return_value=saved),
              patch.object(ui, "save_cboe_refresh") as save,
              patch.object(ui.st, "progress")):
            updated, failures = ui._refresh_cboe_batch("vault", dict(core.DEFAULT_STATE), ["SPY"],
                                                       replace_all=True, publish_master=False)
        self.assertEqual(updated, {})
        self.assertIn("SPY", failures)
        self.assertEqual(saved["results"], {"SPY": {"callWall": {"strike": 110}}})
        self.assertEqual(save.call_args.args[2], failures)

    def setUp(self):
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        cache_patch = patch.object(memory, "_SERVER_CACHE_DIR", Path(cache_dir.name))
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
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

    def test_cboe_tradingview_format_uses_chart_spot_and_removes_garbage(self):
        result = {
            "symbol": "SUN",
            "mode": "BARCHART_STYLE",
            "spot": 74.73,
            "maxDte": 60,
            "contractsUsed": 23,
            "netCurrent": 291787.23,
            "sourceUrl": "https://cdn.cboe.com/api/global/delayed_quotes/options/SUN.json",
            "snapshotFetchedAt": "2026-10-04T16:55:04-04:00",
            "snapshotFingerprint": "abc123",
            "snapshotFingerprintVersion": "CBOE1",
            "snapshotOptionCount": 168,
            "gammaFlip": 70.0913,
            "callWall": {"strike": 80, "value": 124472.87},
            "putWall": {"strike": 72.5, "value": -29353.63},
            "maxCallOi": {"strike": 80, "call_oi": 468},
            "maxPutOi": {"strike": 72.5, "put_oi": 106},
            "topGex": [
                {"strike": 77.5, "net_gex": 154798.78},
                {"strike": 80, "net_gex": 124472.87},
                {"strike": 72.5, "net_gex": -29353.63},
            ],
        }
        block = ui._cboe_tradingview_block(result)
        self.assertIn("Ticker: SUN", block)
        self.assertIn("Source URL: https://cdn.cboe.com/api/global/delayed_quotes/options/SUN.json", block)
        self.assertIn("PASTE EVERYTHING BELOW INTO PINE INPUT: Packed Gamma Levels", block)
        self.assertIn("GFLIP,70.0913,0", block)
        self.assertIn("CALLWALL,80,124472.87", block)
        self.assertIn("PUTWALL,72.5,-29353.63", block)
        self.assertNotIn("\nMode:", block)
        self.assertNotIn("\nSpot:", block)
        self.assertNotIn("\nMax DTE Used:", block)
        self.assertNotIn("\nContracts Used:", block)
        self.assertNotIn("\nNet Current GEX:", block)
        self.assertNotIn("\nCBOE Snapshot Fetched:", block)
        self.assertNotIn("\nCBOE GEX Input Fingerprint:", block)
        self.assertNotIn("\nCBOE Snapshot Option Rows:", block)
        self.assertNotIn("\n------------------------------------------------------------", block)
        self.assertNotIn("\nSPOT,", block)

        issues, count = ui._cboe_pine_ticker_issues(block, "SUN")
        self.assertEqual(issues, [])
        self.assertGreaterEqual(count, 7)

    def test_master_source_defaults_to_cboe_and_round_trips(self):
        state = copy.deepcopy(core.DEFAULT_STATE)
        self.assertEqual(core._clean_state(state)["master_a6_source"], "CBOE")
        state["master_a6_source"] = "ETRADE"
        self.assertEqual(core._clean_state(state)["master_a6_source"], "ETRADE")
        state["master_a6_source"] = "invalid"
        self.assertEqual(core._clean_state(state)["master_a6_source"], "CBOE")

    def test_morningstar_ticker_import_and_full_strike_csv(self):
        import csv
        import io

        self.assertEqual(
            ui._parse_morningstar_tickers(
                "| Ticker |\n| ------ |\n| MSFT |\n| AAPL |\nMSFT; BRK.B"
            ),
            ["MSFT", "AAPL", "BRK.B"],
        )
        with self.assertRaisesRegex(ValueError, "Invalid ticker"):
            ui._parse_morningstar_tickers("MSFT, BAD_TICKER")
        with self.assertRaisesRegex(ValueError, "Paste at least one"):
            ui._parse_morningstar_tickers("| Ticker |\n| --- |")

        result_map = {
            "MSFT": {
                "spot": 500.0, "maxDte": 90, "gammaFlip": 495.0,
                "putWall": {"strike": 480.0},
                "callWall": {"strike": 520.0},
                "netCurrent": 120000.0,
                "contractsUsed": 200, "snapshotFingerprint": "ABCDEF",
                "rawRows": [
                    {"strike": 490.0, "call_oi": 100, "put_oi": 150,
                     "call_gex": 42, "put_gex": -55, "net_gex": -13,
                     "cum_gex": -13},
                    {"strike": 500.0, "call_oi": 200, "put_oi": 175,
                     "call_gex": 60, "put_gex": -30, "net_gex": 30,
                     "cum_gex": 17},
                ],
            },
        }
        rows = list(csv.DictReader(io.StringIO(
            ui._morningstar_gex_csv(["MSFT", "AAPL"], result_map)
        )))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["Ticker"] == "MSFT" for row in rows))
        self.assertTrue(all(row["Source"] == "CBOE DELAYED" for row in rows))
        self.assertEqual([row["Strike"] for row in rows], ["490.0", "500.0"])
        self.assertEqual(rows[0]["Gamma Flip"], "495.0")
        self.assertEqual(rows[0]["CBOE Fingerprint"], "ABCDEF")
        self.assertEqual(rows[1]["Net Strike GEX"], "30")

    def test_cboe_and_morningstar_overview_sort_by_column_without_state_mutation(self):
        state = copy.deepcopy(core.DEFAULT_STATE)
        tickers = ["MSFT", "AAPL", "FAILED"]
        state["tickers"] = list(tickers)
        rows = {
            "MSFT": {
                "spot": 500.0, "maxDte": 60, "gammaFlip": 550.0,
                "putWall": {"strike": 490.0}, "callWall": {"strike": 520.0},
                "snapshotFetchedAt": "2026-10-16T16:35:00-04:00",
            },
            "AAPL": {
                "spot": 250.0, "maxDte": 30, "gammaFlip": 240.0,
                "putWall": {"strike": 220.0}, "callWall": {"strike": 260.0},
                "snapshotFetchedAt": "2026-10-16T16:40:00-04:00",
            },
        }
        sort = ui._gex_overview_sorted_tickers
        self.assertEqual(sort(tickers, rows, state, "WATCHLIST ORDER", "ASC"), tickers)
        self.assertEqual(sort(tickers, rows, state, "TICKER", "ASC"),
                         ["AAPL", "FAILED", "MSFT"])
        self.assertEqual(sort(tickers, rows, state, "SPOT", "DESC"),
                         ["MSFT", "AAPL", "FAILED"])
        self.assertEqual(sort(tickers, rows, state, "PUT WALL", "ASC"),
                         ["AAPL", "MSFT", "FAILED"])
        self.assertEqual(sort(tickers, rows, state, "DTE", "ASC"),
                         ["AAPL", "MSFT", "FAILED"])
        self.assertEqual(sort(tickers, rows, state, "RANGE Δ %", "ASC"),
                         ["MSFT", "AAPL", "FAILED"])
        self.assertEqual(sort(tickers, rows, state, "UPDATED", "DESC"),
                         ["AAPL", "MSFT", "FAILED"])
        self.assertEqual(state["tickers"], tickers)

    def test_morningstar_exports_match_visible_columns_order_and_range(self):
        import csv
        import io

        state = copy.deepcopy(core.DEFAULT_STATE)
        state["tickers"] = ["MSFT", "AAPL", "FAILED"]
        results = {
            "MSFT": {
                "spot": 500.0, "maxDte": 60,
                "gammaFlip": 550.0,
                "putWall": {"strike": 490.0},
                "callWall": {"strike": 520.0},
                "snapshotFetchedAt": "2026-10-16T16:35:00-04:00",
                "snapshotFingerprint": "ABCDEF1234567890",
                "updated": "2026-10-16T16:35:00-04:00",
            },
            "AAPL": {
                "spot": 250.0, "maxDte": 60,
                "gammaFlip": 240.0,
                "putWall": {"strike": 220.0},
                "callWall": {"strike": 260.0},
                "snapshotFetchedAt": "2026-10-16T16:40:00-04:00",
                "snapshotFingerprint": "1234567890ABCDEF",
                "updated": "2026-10-16T16:40:00-04:00",
            },
        }
        sorted_tickers = ui._gex_overview_sorted_tickers(
            state["tickers"], results, state, "SPOT", "ASC"
        )
        sorted_state = copy.deepcopy(state)
        sorted_state["tickers"] = sorted_tickers
        rows = list(csv.reader(io.StringIO(
            ui._morningstar_overview_csv(sorted_tickers, results, sorted_state)
        )))
        self.assertEqual(rows[0], [
            "TICKER", "DTE", "SPOT", "PUT WALL", "GAMMA FLIP",
            "CALL WALL", "RANGE", "UPDATED",
        ])
        self.assertEqual([row[0] for row in rows[1:]],
                         ["AAPL", "MSFT", "FAILED"])
        self.assertEqual(rows[1][2:6],
                         ["$250.00", "$220.00", "$240.00", "$260.00"])
        self.assertIn("POSITIVE GAMMA // Δ +4.17%", rows[1][6])
        self.assertIn("NEGATIVE GAMMA // Δ -9.09%", rows[2][6])
        self.assertIn("FP 1234567890AB", rows[1][7])
        self.assertEqual(rows[3][6], "REFRESH")

        markup = ui._decorate_cboe_overview_markup(
            base._overview_html(sorted_state, results), results,
            include_trash=False,
        )
        exported = ui._morningstar_visual_html(markup, len(sorted_tickers))
        self.assertIn("MORNINGSTAR GEX // CBOE OVERVIEW", exported)
        self.assertIn("gexv3-range-dot", exported)
        self.assertIn("gexv3-range-zone", exported)
        self.assertIn("gexv3-snapshot-fp", exported)
        self.assertIn("print-color-adjust:exact", exported)
        self.assertNotIn("gexv3-trash-link\" href=", exported)
        aapl_pos = exported.index('class="sym">AAPL')
        msft_pos = exported.index('class="sym">MSFT')
        self.assertLess(aapl_pos, msft_pos)

    def test_morningstar_refresh_never_publishes_regular_cboe_bridge(self):
        saved = {"results": {}, "failures": {}}
        result = {"symbol": "MSFT", "spot": 500.0}
        with (
            patch.object(ui, "build_cboe_gex", return_value=result),
            patch.object(ui, "cboe_snapshot", return_value=saved),
            patch.object(ui, "save_cboe_refresh"),
            patch.object(ui, "_publish_cboe_master_a6") as publish,
            patch.object(ui.st, "progress"),
        ):
            updated, failed = ui._refresh_cboe_batch(
                "isolated:morningstar", dict(core.DEFAULT_STATE),
                ["MSFT"], replace_all=True, publish_master=False,
            )
        self.assertEqual(updated, {"MSFT": result})
        self.assertEqual(failed, {})
        publish.assert_not_called()

    def test_both_cboe_export_sets_are_visible_before_tables_and_isolated(self):
        from streamlit.testing.v1 import AppTest

        source = """
import streamlit as st
from src import gex_ui_v3 as ui
state = {"dte_overrides": {}, "global_dte": 45}
result = {
    "SPY": {
        "spot": 600.0, "maxDte": 45, "gammaFlip": 590.0,
        "putWall": {"strike": 580.0}, "callWall": {"strike": 610.0},
        "rawRows": [{"strike": 600.0, "call_gex": 20.0, "put_gex": -10.0,
                     "net_gex": 10.0, "cum_gex": 10.0,
                     "call_oi": 100, "put_oi": 50}],
    },
}
ui._render_gex_exports(
    key_prefix="gex_cboe", file_prefix="cboe_gex",
    displayed_tickers=["SPY"], result_map=result, display_state=state,
    html_markup="<table></table>", html_heading="CBOE OVERVIEW",
)
ui._render_gex_exports(
    key_prefix="gex_morningstar", file_prefix="morningstar_gex",
    displayed_tickers=["SPY"], result_map=result, display_state=state,
    html_markup="<table></table>", html_heading="MORNINGSTAR GEX // CBOE OVERVIEW",
)
"""
        app = AppTest.from_string(source).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.get("download_button")), 6)
        self.assertTrue(all(not widget.disabled for widget in app.get("download_button")))

        # Download controls must remain discoverable even before a successful run.
        source_without_results = source.replace(
            'result_map=result, display_state=state,',
            'result_map={}, display_state=state,',
        )
        empty = AppTest.from_string(source_without_results).run()
        self.assertFalse(empty.exception)
        self.assertEqual(len(empty.get("download_button")), 6)
        self.assertTrue(all(widget.disabled for widget in empty.get("download_button")))

        import inspect
        cboe = inspect.getsource(ui._render_cboe_overview)
        morningstar = inspect.getsource(ui._render_morningstar_gex)
        self.assertLess(
            cboe.index("_render_gex_exports("),
            cboe.index("st.html(_decorate_cboe_overview_markup("),
        )
        self.assertLess(
            morningstar.index("_render_gex_exports("),
            morningstar.index("st.html(markup)"),
        )
        self.assertIn("CBOE OVERVIEW", ui._morningstar_visual_html(
            "<table></table>", 1, heading="CBOE OVERVIEW",
        ))

    def test_compact_gex_screenshot_layout_is_scoped_to_gex_only(self):
        """Prevent blue full-width export banners and phantom build/receipt gaps."""
        root = Path(__file__).resolve().parents[1]
        workspace = (root / "src/gex_workspace_v2.py").read_text(encoding="utf-8")
        base = (root / "src/gex_ui_v3_base.py").read_text(encoding="utf-8")
        ui = (root / "src/gex_ui_v3.py").read_text(encoding="utf-8")

        self.assertIn('with st.container(key="gexv3_workspace", gap=None):', workspace)
        self.assertIn('st.html(_gex_build_badge())', workspace)
        self.assertLess(
            workspace.index('with st.container(key="gexv3_workspace", gap=None):'),
            workspace.index('st.html(_gex_build_badge())'),
        )
        self.assertLess(
            workspace.index('st.html(_gex_build_badge())'),
            workspace.index('_render_with_style_only_html(\n            client,'),
        )
        self.assertIn(
            '.st-key-gexv3_workspace div[data-testid="stDownloadButton"] button',
            base,
        )
        self.assertIn(
            '.st-key-gexv3_workspace div[data-testid="stLinkButton"] a',
            base,
        )
        self.assertIn(
            '.st-key-gexv3_workspace button[data-testid="baseButton-primary"]',
            base,
        )
        self.assertIn('background: #050505 !important;', base)
        self.assertIn('border: 1px solid #fb8b1e !important;', base)
        self.assertIn(
            'cols = st.columns([1.4, 1.35, 1.1, 3.3], gap="small")',
            ui,
        )
        self.assertIn('key=f"{key_prefix}_visual_export"', ui)
        self.assertIn('key=f"{key_prefix}_strikes_export"', ui)
        # Hidden broker UI must stay hidden; do not alter quote/refresh logic.
        self.assertIn('show_etrade_overview = False', base)
        self.assertIn('with cboe_refresh_col:', base)

    def test_cboe_publication_receipt_is_directly_above_add_tickers(self):
        import ast

        root = Path(__file__).resolve().parents[1]
        base_source = (root / "src/gex_ui_v3_base.py").read_text(encoding="utf-8")
        wrapper_source = (root / "src/gex_ui_v3.py").read_text(encoding="utf-8")
        render = next(
            node for node in ast.parse(base_source).body
            if isinstance(node, ast.FunctionDef) and node.name == "render_gex"
        )

        def line_of(name: str) -> int:
            matches = [
                node.lineno for node in ast.walk(render)
                if isinstance(node, ast.Call)
                and (
                    isinstance(node.func, ast.Name) and node.func.id == name
                    or isinstance(node.func, ast.Attribute) and node.func.attr == name
                )
            ]
            self.assertEqual(len(matches), 1, f"Expected one {name} in GEX renderer")
            return matches[0]

        # The previous top-level receipt rendered before the hidden state iframe
        # and CSS-only markup, producing needless space before the ticker entry.
        self.assertLess(line_of("_load_state"), line_of("_render_cboe_publication_status"))
        self.assertLess(line_of("_render_cboe_publication_status"), line_of("text_input"))
        self.assertIn(
            "_proven._base._render_cboe_publication_status = lambda: "
            "_render_cboe_publication_status(vault_key)",
            wrapper_source,
        )
        self.assertIn(
            "_proven._base._render_cboe_publication_status = original_publication_status",
            wrapper_source,
        )
        self.assertNotIn(
            "    _render_cboe_publication_status(vault_key)\\n    st.html(",
            wrapper_source,
        )

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
        self.assertIn("def _cboe_tradingview_block", ui)
        self.assertIn("def _cboe_tradingview_master", ui)
        self.assertIn("if not row.startswith(\"SPOT,\")", ui)
        self.assertIn("parser_text = _cboe_tradingview_master(", ui)
        self.assertIn("def _cache_busted_txt_url", ui)
        self.assertIn("_cache_busted_txt_url(_CBOE_BRIDGE_URL)", ui)
        self.assertIn("_cache_busted_txt_url(_ETRADE_BRIDGE_URL)", ui)
        self.assertIn('"blob/gex-bridge-data/bridge/latest_gex_cboe.txt?plain=1"', ui)
        self.assertIn("def _after_tickers_added", base)
        self.assertIn("_after_tickers_added(vault_key, state, added)", base)
        self.assertIn("_proven._base._after_tickers_added = refresh_added_cboe_tickers", ui)
        self.assertIn("_proven._base._after_tickers_added = original_after_tickers_added", ui)
        self.assertIn("full_existing_snapshot", ui)
        self.assertIn("refresh_tickers = added_tickers if full_existing_snapshot else saved_tickers", ui)
        self.assertIn("replace_all = not full_existing_snapshot", ui)
        self.assertIn("def _publish_current_etrade_master_a6", ui)
        self.assertIn("_proven._base._refresh_symbol = refresh_symbol_with_txt_publish", ui)

    def test_removed_legacy_subtabs_keep_gex_sources_and_session_fallback(self):
        import ast

        root = Path(__file__).resolve().parents[1]
        source = (root / "src/gex_ui_v3_base.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        render = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "render_gex"
        )
        subtabs = next(
            node for node in ast.walk(render)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "subtab_names"
                    for target in node.targets)
        )
        self.assertIsInstance(subtabs.value, ast.BinOp)
        self.assertEqual(ast.literal_eval(subtabs.value.right), [
            "CBOE OVERVIEW", "MORNINGSTAR GEX", "SETTINGS", "NOTES",
        ])
        toggle = next(
            node for node in ast.walk(render)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name)
                    and target.id == "show_etrade_overview"
                    for target in node.targets)
        )
        self.assertIs(ast.literal_eval(toggle.value), False)
        self.assertIn('if show_etrade_overview:', source)
        self.assertIn('with tabs["E*TRADE OVERVIEW"]:', source)
        self.assertIn('add_col, cboe_refresh_col = st.columns(', source)
        self.assertIn('[4.85, 1.15], gap="small"', source)
        self.assertIn('refresh_all = False', source)
        self.assertIn(
            'if show_etrade_overview:\n        with etrade_refresh_col:', source,
        )
        self.assertIn('with cboe_refresh_col:', source)
        render_calls = {
            node.func.id for node in ast.walk(render)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue({
            "_render_overview", "_render_cboe_overview",
            "_render_morningstar_gex", "_render_settings", "_render_notes",
        }.issubset(render_calls))
        self.assertTrue({
            "_render_analytics", "_render_raw", "_render_tradingview",
        }.isdisjoint(render_calls))
        self.assertIn('st.session_state.pop("gexv3_active_subtab", None)', source)
        self.assertIn('on_change=_remember_gex_subtab', source)


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
