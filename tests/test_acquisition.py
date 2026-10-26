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

from src.acquisition_ui import _default_account, _fifo_equity, _merge, _pine_rows, _reconcile, _sync_history, _broker_lots, _recover_lots
from src.tab_bar_v4 import DEFAULT_TAB_ORDER, _clean_order


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
            transaction(1, "2025-04-01", "BUY", "LMT", 5, 480),
            transaction(2, "2025-04-02", "SELL", "LMT", 10, 490),
            transaction(3, "2025-04-03", "BUY", "LMT", 10, 500),
        ]
        valid, gaps = _reconcile(rows, [holding("LMT", 10), holding("SPY", 5)])
        self.assertEqual(valid, [])
        self.assertEqual({row["Ticker"] for row in gaps}, {"LMT", "SPY"})

    def test_msft_prior_unknown_sale_then_current_buys_reconcile(self):
        # A sale from before the available opening history must not
        # permanently poison later verified purchases.
        tx = [
            transaction(1, "2024-10-28", "SELL", "MSFT", 25, 410),
            transaction(2, "2025-03-01", "BUY", "MSFT", 8, 390),
        ]
        verified, gaps = _reconcile(tx, [holding("MSFT", 8)])
        self.assertFalse(gaps)
        self.assertEqual(_pine_rows(verified), "MSFT 03.01.2025 390 8 B")

    def test_broker_tax_lots_recover_missing_held_shares(self):
        ts = int(datetime(2023, 2, 28, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
        response = {"PortfolioResponse": {"AccountPortfolio": [{"Position": [{
            "positionLot": [
                {"positionLotId": 71, "acquiredDate": ts, "remainingQty": 45, "price": 95.12},
                {"positionLotId": 72, "acquiredDate": ts, "remainingQty": 20, "price": 102},
            ]
        }]}]}}
        result = _broker_lots(response, "HTD", 65)
        self.assertEqual(sum(x["qty"] for x in result), 65)
        self.assertEqual(_pine_rows(result), "HTD 02.28.2023 95.12 45 B\nHTD 02.28.2023 102 20 B")
        self.assertFalse(_broker_lots(response, "HTD", 66))
        response["PortfolioResponse"]["AccountPortfolio"][0]["Position"][0]["positionLot"][0]["acquiredDate"] = None
        self.assertFalse(_broker_lots(response, "HTD", 65))

    def test_broker_recovery_fetches_unresolved_only_and_reuses_cache(self):
        stamp = int(datetime(2023, 3, 1, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
        class Client:
            def __init__(self):
                self.calls = []
            def get_position_lots(self, account, position):
                self.calls.append((account, position))
                return {"positionLot": [{"positionLotId": 4, "acquiredDate": stamp,
                                         "remainingQty": 65, "price": 88}]}
        client = Client()
        positions = [dict(holding("HTD", 65), positionId=991)]
        gaps = [{"Ticker": "HTD", "Held": 65, "FIFO Found": 0, "Reason": "missing"}]
        vault = {"lock": __import__("threading").RLock(), "accounts": {}}
        with patch("src.acquisition_ui._history_vault", return_value=vault):
            first, remaining = _recover_lots(client, "vault", "RAJ", positions, gaps)
            again, _ = _recover_lots(client, "vault", "RAJ", positions, gaps)
        self.assertFalse(remaining)
        self.assertEqual(len(first), 1)
        self.assertEqual(_pine_rows(first), _pine_rows(again))
        self.assertEqual(client.calls, [("RAJ", 991)])

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

    def test_no_order_calls_and_only_expected_shared_files(self):
        source = (Path(__file__).resolve().parents[1] / "src/acquisition_ui.py").read_text()
        for forbidden in ("client.place_order(", "client.preview_order(", "client.cancel_order(", "st.button ="):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
