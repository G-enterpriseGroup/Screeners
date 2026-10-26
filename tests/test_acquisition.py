"""Focused Acquisition read-only FIFO/cache/source regression tests."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import shutil
import subprocess
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.acquisition_ui import _default_account, _fifo_equity, _merge, _pine_rows, _reconcile, _sync_history, _parse_position_lots, _recover_unverified_lots
from src.tab_bar_v4 import DEFAULT_TAB_ORDER, _clean_order
from src.etrade_client import ETradeClient, ETradeError


def transaction(identifier, stamp, action, symbol, qty, price, security="EQ"):
    return {
        "transactionId": identifier,
        "transactionDate": int(datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000),
        "brokerage": {
            "transactionType": action,
            "product": {"symbol": symbol, "securityType": security},
            "quantity": qty, "price": price, "fee": 0,
        },
    }


def holding(symbol, qty, security="EQ"):
    return {"Product": {"symbol": symbol, "securityType": security}, "quantity": qty}


class FakeClient:
    def __init__(self, batches):
        self.batches = batches
        self.calls = []
    def get_transactions(self, account_id_key, *, start_date, end_date, sort_order):
        self.calls.append((account_id_key, start_date, end_date, sort_order))
        source = self.batches[account_id_key]
        if isinstance(source, Exception):
            raise source
        return source


class AcquisitionTests(unittest.TestCase):
    def test_fifo_partial_exit_and_pine_export(self):
        rows = [
            transaction(1, "2025-02-28", "BUY", "LMT", 20, 502.21),
            transaction(2, "2025-04-01", "BUY", "LMT", 10, 550.50),
            transaction(3, "2025-06-01", "SELL", "LMT", 25, 580),
            transaction(4, "2025-06-01", "BUY", "LMT", 1, 3.50, "OPTN"),
            transaction(5, "2025-07-01", "BUY", "SONY", 2.5, 22.01),
        ]
        valid, gaps = _reconcile(rows, [holding("LMT", 5), holding("SONY", 2.5)])
        self.assertEqual(gaps, [])
        self.assertEqual(_pine_rows(valid), "LMT 04.01.2025 550.5 5 B\nSONY 07.01.2025 22.01 2.5 B")

    def test_unreconciled_older_lot_and_corporate_action_are_excluded(self):
        rows = [
            transaction(1, "2025-04-01", "SELL", "LMT", 50, 490),
            transaction(2, "2025-04-02", "BUY", "LMT", 10, 500),
        ]
        valid, gaps = _reconcile(rows, [holding("LMT", 10), holding("SPY", 5)])
        self.assertEqual(valid, [])
        self.assertEqual({row["Ticker"] for row in gaps}, {"LMT", "SPY"})

    def test_dedup_keeps_latest_corrected_record(self):
        old = [transaction(17, "2025-01-01", "BUY", "LMT", 1, 100)]
        new = [transaction(17, "2025-01-01", "BUY", "LMT", 1, 105)]
        self.assertEqual(_merge(old, new)[0]["brokerage"]["price"], 105)

    def test_all_accounts_seed_once_then_only_overlapping_incremental_fetch(self):
        accounts = [{"accountIdKey": "RAJ"}, {"accountIdKey": "AMEKA"}]
        fake = FakeClient({
            "RAJ": [transaction(1, "2025-01-01", "BUY", "LMT", 2, 100)],
            "AMEKA": [transaction(2, "2025-02-01", "BUY", "SONY", 3, 20)],
        })
        vault = {"lock": __import__("threading").RLock(), "accounts": {}}
        now = datetime(2026, 10, 26, tzinfo=ZoneInfo("America/New_York"))
        with patch("src.acquisition_ui._history_vault", return_value=vault):
            first, errors = _sync_history(fake, "SECURE", accounts, now=now)
            self.assertFalse(errors)
            self.assertEqual(len(fake.calls), 2)
            second, _ = _sync_history(fake, "SECURE", accounts, now=now)
            self.assertEqual(len(fake.calls), 2)
            third, _ = _sync_history(fake, "SECURE", accounts, now=now, force=True)
            self.assertEqual(len(fake.calls), 4)
            self.assertEqual(fake.calls[2][1].isoformat(), "2026-10-19")
            self.assertEqual(len(third["RAJ"]["rows"]), len(first["RAJ"]["rows"]))
            fake.batches["RAJ"] = RuntimeError("401")
            prev = third["RAJ"]["covered_through"]
            after, errors = _sync_history(fake, "SECURE", accounts, now=now, force=True)
            self.assertIn("RAJ", errors)
            self.assertEqual(after["RAJ"]["covered_through"], prev)

    def test_default_raj_over_other_accounts(self):
        self.assertEqual(_default_account([
            {"accountName": "Ameka", "accountIdKey": "X", "accountId": "1111"},
            {"accountName": "Raj", "accountIdKey": "Y", "accountId": "5474"},
        ]), "Y")

    def test_legacy_navigation_adds_acquisition_beside_performance(self):
        # Prior browser/server layouts lack the newly introduced route.
        old = ["GEX", "PERFORMANCE", "HOLDINGS", "RISK SIZING"]
        restored = _clean_order(old)
        self.assertEqual(restored[:4], ["GEX", "PERFORMANCE", "ACQUISITION", "HOLDINGS"])
        self.assertEqual(restored.count("ACQUISITION"), 1)
        self.assertEqual(_clean_order([]), DEFAULT_TAB_ORDER)
        custom = ["GEX", "ACQUISITION", "PERFORMANCE", "HOLDINGS"]
        self.assertEqual(_clean_order(custom)[:4], custom)

    def test_browser_saved_order_migrates_acquisition_near_performance(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is required for browser navigation JS checks.")
        source = (
            Path(__file__).resolve().parents[1]
            / "src/components/terminal_tabs_v3/index.html"
        ).read_text(encoding="utf-8")
        begin = source.index("    function cleanOrder(candidate, allowed) {")
        end = source.index("\n    function persist()", begin)
        fn = source[begin:end]
        old = ["GEX", "PERFORMANCE", "HOLDINGS", "RISK SIZING"]
        js = (
            fn
            + "\nprocess.stdout.write(JSON.stringify(cleanOrder("
            + json.dumps(old) + ", " + json.dumps(DEFAULT_TAB_ORDER)
            + ")));"
        )
        result = subprocess.run(["node", "-e", js], text=True, capture_output=True, check=True)
        migrated = json.loads(result.stdout)
        self.assertEqual(migrated[:4], ["GEX", "PERFORMANCE", "ACQUISITION", "HOLDINGS"])

    def test_prior_unmatched_sale_does_not_poison_later_verified_buys(self):
        rows = [
            transaction(101, "2024-11-01", "SELL", "MSFT", 25, 401),
            transaction(102, "2025-04-02", "BUY", "MSFT", 8, 405.22),
        ]
        valid, gaps = _reconcile(rows, [holding("MSFT", 8)])
        self.assertFalse(gaps)
        self.assertEqual(_pine_rows(valid), "MSFT 04.02.2025 405.22 8 B")
        # A sale after observed purchases which consumes more than observed
        # inventory is still ambiguous, even when the broker count matches.
        rows = [
            transaction(102, "2025-04-02", "BUY", "MSFT", 8, 405.22),
            transaction(103, "2025-05-02", "SELL", "MSFT", 12, 420),
            transaction(104, "2025-06-02", "BUY", "MSFT", 8, 430),
        ]
        valid, gaps = _reconcile(rows, [holding("MSFT", 8)])
        self.assertFalse(valid)
        self.assertEqual(gaps[0]["Ticker"], "MSFT")

    def test_htd_original_broker_lots_recover_missing_history_and_cache(self):
        def timestamp(ymd):
            return int(datetime.fromisoformat(ymd).replace(
                tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
        payload = {"PortfolioResponse": {"AccountPortfolio": [{"Position": [{
            "positionId": 140,
            "PositionLot": [
                {"positionLotId": 501, "positionId": 140, "acquiredDate": timestamp("2023-04-10"),
                 "remainingQty": 40, "price": 48.125},
                {"positionLotId": 502, "positionId": 140, "acquiredDate": timestamp("2024-06-20"),
                 "remainingQty": 25, "price": 51.75},
            ],
        }]}]}}
        positions = [dict(holding("HTD", 65), positionId=140, totalCost=3218.75)]
        class LotsClient:
            is_offline = False
            def __init__(self):
                self.calls = []
            def get_position_lots(self, key, position_id):
                self.calls.append((key, position_id))
                return payload
        client = LotsClient()
        normal, gaps = _reconcile([], positions)
        self.assertFalse(normal)
        vault = {"lock": __import__("threading").RLock(), "accounts": {}}
        with patch("src.acquisition_ui._history_vault", return_value=vault):
            recovered, missing = _recover_unverified_lots(client, "VAULT", "RAJ", positions, gaps)
            self.assertEqual(missing, [])
            self.assertEqual(len(recovered), 2)
            self.assertEqual(sum(row["qty"] for row in recovered), 65)
            self.assertEqual(_pine_rows(recovered), 
                "HTD 04.10.2023 48.125 40 B\nHTD 06.20.2024 51.75 25 B")
            _recover_unverified_lots(client, "VAULT", "RAJ", positions, gaps)
            self.assertEqual(client.calls, [("RAJ", "140")])
            _recover_unverified_lots(client, "VAULT", "RAJ", positions, gaps, refresh=True)
            self.assertEqual(len(client.calls), 2)

    def test_position_lot_recovery_rejects_incomplete_broker_basis(self):
        position = dict(holding("HTD", 65), positionId=140, totalCost=3333)
        rows, gaps = _reconcile([], [position])
        self.assertEqual(rows, [])
        class Partial:
            def get_position_lots(self, account, position_id):
                return {"PositionLot": [{
                    "positionId": 140, "positionLotId": 1, "remainingQty": 40,
                    "price": 50, "acquiredDate": 1720000000,
                }]}
        with patch("src.acquisition_ui._history_vault", return_value={
            "lock": __import__("threading").RLock(), "accounts": {},
        }):
            recovered, missing = _recover_unverified_lots(
                Partial(), "VAULT", "RAJ", [position], gaps)
        self.assertEqual(recovered, [])
        self.assertEqual(len(missing), 1)
        self.assertEqual(_parse_position_lots({"PositionLot": [{
            "positionId": 140, "remainingQty": 65, "price": 0,
            "acquiredDate": 1720000000,
        }]}, "HTD", "140"), [])
        self.assertEqual(_parse_position_lots({"PositionLot": [{
            "positionId": 141, "remainingQty": 65, "price": 50,
            "acquiredDate": 1720000000,
        }]}, "HTD", "140"), [])

    def test_position_lot_transport_is_read_only_and_validates_identifier(self):
        client = object.__new__(ETradeClient)
        requests = []
        def fake_get(path, params=None, *, allow_no_content=False):
            requests.append((path, allow_no_content))
            return {"PositionLot": []}
        client._get = fake_get
        self.assertEqual(client.get_position_lots("ENCRYPTED", 123), {"PositionLot": []})
        self.assertEqual(requests, [("/v1/accounts/ENCRYPTED/portfolio/123", True)])
        for bad in ("", "bad/path", "-1"):
            with self.assertRaises(ETradeError):
                client.get_position_lots("ENCRYPTED", bad)
        self.assertEqual(len(requests), 1)

    def test_no_order_calls_and_only_expected_shared_files(self):
        source = (Path(__file__).resolve().parents[1] / "src/acquisition_ui.py").read_text()
        for forbidden in ("client.place_order(", "client.preview_order(", "client.cancel_order(", "st.button ="):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
