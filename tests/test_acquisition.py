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

from src.acquisition_ui import _default_account, _fifo_equity, _merge, _pine_rows, _reconcile, _sync_history, _recover_broker_lots, _broker_lot_rows
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
        # Current LMT shares exceed reconstructed open buys: the missing older
        # shares are still in the book, unlike fully closed older MSFT sells.
        valid, gaps = _reconcile(rows, [holding("LMT", 15), holding("SPY", 5)])
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

    def test_msft_prior_oversell_does_not_hide_verified_open_eight_shares(self):
        rows = [
            transaction(1, "2025-01-01", "SELL", "MSFT", 40, 400),
            transaction(2, "2025-03-01", "BUY", "MSFT", 5, 410.15),
            transaction(3, "2026-06-01", "BUY", "MSFT", 3, 415.55),
        ]
        verified, gaps = _reconcile(rows, [holding("MSFT", 8)])
        self.assertEqual(gaps, [])
        self.assertEqual(sum(lot["qty"] for lot in verified), 8)
        self.assertTrue(all(lot["source"] == "FIFO" for lot in verified))
        self.assertEqual(_pine_rows(verified), "MSFT 03.01.2025 410.15 5 B\nMSFT 06.01.2026 415.55 3 B")

    def test_htd_65_missing_from_transaction_window_recovers_real_broker_lots(self):
        class Broker:
            def __init__(self):
                self.calls = []
            def get_position_lots(self, account_key, position_id):
                self.calls.append((account_key, position_id))
                return {"PortfolioResponse": {"AccountPortfolio": {"Position": [{
                    "PositionLot": [
                        {"positionLotId": 1, "remainingQty": 40, "price": 22.47, "acquiredDate": 20240115},
                        {"positionLotId": 2, "remainingQty": 25, "price": 23.03, "acquiredDate": 20230712},
                    ]
                }]}}}

        raw_portfolio = [{**holding("HTD", 65), "positionId": 1000001}]
        known, gaps = _reconcile([], raw_portfolio)
        self.assertEqual(gaps[0]["FIFO Found"], 0)
        vault = {"lock": __import__("threading").RLock(), "accounts": {}, "lots": {}}
        broker = Broker()
        with patch("src.acquisition_ui._history_vault", return_value=vault):
            open_lots, unresolved = _recover_broker_lots(
                broker, "MY_VAULT", "RAJ_ACCOUNT", raw_portfolio, known, gaps,
            )
            again, unresolved_again = _recover_broker_lots(
                broker, "MY_VAULT", "RAJ_ACCOUNT", raw_portfolio, known, gaps,
            )
        self.assertEqual(unresolved, [])
        self.assertEqual(unresolved_again, [])
        self.assertEqual(broker.calls, [("RAJ_ACCOUNT", 1000001)])
        self.assertEqual(len(open_lots), 2)
        self.assertEqual(sum(row["qty"] for row in open_lots), 65)
        self.assertEqual(_pine_rows(open_lots), "HTD 07.12.2023 23.03 25 B\nHTD 01.15.2024 22.47 40 B")
        self.assertTrue(all(row["source"] == "E*TRADE LOT" for row in again))

    def test_bad_or_incomplete_broker_lots_are_never_exported(self):
        self.assertEqual(_broker_lot_rows({"PositionLot": [
            {"remainingQty": 65, "price": 22.3, "acquiredDate": -68400000}
        ]}, "HTD"), [])
        self.assertEqual(_broker_lot_rows({"PositionLot": [
            {"remainingQty": 65, "price": 22.3}
        ]}, "HTD"), [])
        portfolio = [{**holding("HTD", 65), "positionId": 57}]
        known, gaps = _reconcile([], portfolio)
        class Incomplete:
            def get_position_lots(self, account_key, position_id):
                return {"PositionLot": [{"remainingQty": 64, "price": 24, "acquiredDate": 20240505}]}
        with patch("src.acquisition_ui._history_vault", return_value={"lock": __import__("threading").RLock(), "accounts": {}}):
            valid, remain = _recover_broker_lots(Incomplete(), "ONE", "RAJ", portfolio, known, gaps)
        self.assertEqual(valid, [])
        self.assertEqual(len(remain), 1)

    def test_position_lot_transport_is_read_only_and_account_scoped(self):
        client = ETradeClient.__new__(ETradeClient)
        calls = []
        client._get = lambda path, **kwargs: calls.append((path, kwargs)) or {"PositionLot": []}
        self.assertEqual(client.get_position_lots("account+key", 123), {"PositionLot": []})
        self.assertEqual(calls[0][0], "/v1/accounts/account%2Bkey/portfolio/123")
        self.assertTrue(calls[0][1]["allow_no_content"])
        with self.assertRaises(ETradeError):
            client.get_position_lots("account+key", "../orders")

    def test_broker_lots_are_never_fetched_for_options(self):
        class Broker:
            def get_position_lots(self, account_key, position_id):
                self.fail("Should never request an option lot")
        portfolio = [{**holding("MSFT", 1, "OPTN"), "positionId": 80}]
        known, gaps = _reconcile([], portfolio)
        self.assertEqual((known, gaps), ([], []))

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
