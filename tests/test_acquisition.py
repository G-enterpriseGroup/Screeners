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

from src.acquisition_ui import _default_account, _fifo_equity, _merge, _pine_rows, _reconcile, _sync_history, _recover_broker_lots, _broker_lot_rows, maybe_auto_sync_acquisition_accounts
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

    def test_auto_account_discovery_creates_new_account_without_repeated_fetch(self):
        accounts = [{"accountIdKey": "RAJ", "accountName": "Raj Singh", "accountId": "5474"}]
        client = FakeClient({"RAJ": []})
        calls = []
        def listing(force_refresh=False):
            calls.append(force_refresh)
            return list(accounts)
        client.list_accounts = listing
        now = 1793120000
        vault = {"lock": __import__("threading").RLock(), "accounts": {}}
        with patch("src.acquisition_ui.st.session_state", {}), patch(
            "src.acquisition_ui.time.time", return_value=now
        ), patch("src.acquisition_ui._history_vault", return_value=vault):
            maybe_auto_sync_acquisition_accounts(client, "ACCESS", "OAUTH1")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(client.calls), 1)
            maybe_auto_sync_acquisition_accounts(client, "ACCESS", "OAUTH1")
            self.assertEqual(len(calls), 1)
            accounts.append({"accountIdKey": "NEW", "accountName": "New Client", "accountId": "1234"})
            client.batches["NEW"] = []
            with patch("src.acquisition_ui.time.time", return_value=now + 901):
                maybe_auto_sync_acquisition_accounts(client, "ACCESS", "OAUTH1")
            self.assertEqual(len(calls), 2)
            self.assertEqual({c[0] for c in client.calls}, {"RAJ", "NEW"})

    def test_default_raj_over_other_accounts(self):
        self.assertEqual(_default_account([
            {"accountName": "Ameka", "accountIdKey": "X", "accountId": "1111"},
            {"accountName": "Raj", "accountIdKey": "Y", "accountId": "5474"},
        ]), "Y")

    def test_raj_individual_account_wins_over_joint_account_mentioning_raj(self):
        accounts = [
            {"accountName": "Raj + Ameka", "accountIdKey": "JOINT", "accountId": "9999"},
            {"accountName": "Ameka", "accountIdKey": "AMEKA", "accountId": "0057"},
            {"accountName": "Raj Singh", "accountIdKey": "RAJ", "accountId": "5474"},
        ]
        self.assertEqual(_default_account(accounts), "RAJ")
        self.assertEqual(_default_account(list(reversed(accounts))), "RAJ")
        accounts[-1]["accountId"] = "1234"
        self.assertEqual(_default_account(accounts), "RAJ")

    def test_private_auth_alert_theme_and_account_scope_are_local(self):
        source = (Path(__file__).resolve().parents[1] / "src/acquisition_ui.py").read_text()
        self.assertIn("st-key-acquisition_root .acq-alert", source)
        self.assertIn("st.session_state[initial_key] = account_scope", source)
        self.assertIn('html.escape(errors["blotter"])', source)

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

    def test_acquisition_default_raj_and_local_compact_theme(self):
        accounts = [
            {"accountIdKey": "other", "accountName": "Individual Brokerage", "accountId": "7022"},
            {"accountIdKey": "raj", "accountName": "Raj Singh", "accountId": "5474"},
        ]
        self.assertEqual(_default_account(accounts), "raj")
        source = (Path(__file__).resolve().parents[1] / "src/acquisition_ui.py").read_text()
        self.assertIn('initial_key = "_acquisition_default_account_initialized"', source)
        self.assertIn('st.session_state[picker_key] = _default_account(accounts)', source)
        self.assertIn(".st-key-acquisition_root [data-testid=\"stAlert\"]", source)
        self.assertNotIn("st.button =", source)

    def test_no_order_calls_and_only_expected_shared_files(self):
        source = (Path(__file__).resolve().parents[1] / "src/acquisition_ui.py").read_text()
        for forbidden in ("client.place_order(", "client.preview_order(", "client.cancel_order(", "st.button ="):
            self.assertNotIn(forbidden, source)


class AcquisitionCopySelectionTests(unittest.TestCase):
    @staticmethod
    def _app():
        from streamlit.testing.v1 import AppTest
        return AppTest.from_string('''
from datetime import date, datetime
import threading
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo
import streamlit as st
import src.acquisition_ui as acq

def tx(identifier, stamp, action, symbol, quantity, price, security="EQ"):
    return {"transactionId":identifier,
            "transactionDate":int(datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("America/New_York")).timestamp()*1000),
            "brokerage":{"transactionType":action,"product":{"symbol":symbol,"securityType":security},
                         "quantity":quantity,"price":price}}
def position(symbol, quantity, **extra):
    return {"Product":{"symbol":symbol,"securityType":"EQ"},"quantity":quantity,**extra}
accounts = [{"accountIdKey":"raj","accountName":"Raj Singh","accountId":"10005474"},
            {"accountIdKey":"other","accountName":"Other","accountId":"10001234"}]
history = {
    "raj":[tx(1,"2025-01-05","BUY","APH",10,100),
           tx(2,"2025-02-05","SELL","APH",10,120),
           tx(3,"2025-03-05","BUY","MSFT",10,200),
           tx(4,"2025-04-05","SELL","MSFT",4,210),
           tx(5,"2025-05-05","BUY","DUP",1,55),
           tx(6,"2025-05-05","BUY","DUP",1,55),
           tx(7,"2025-06-05","BUY","VTI",2.5,37.12345678,"ETF"),
           tx(8,"2025-07-05","BUY","OPTION",1,3,"OPTN"),
           tx(9,"2025-07-05","SELL_SHORT","SHORT",1,30),
           tx(10,"2025-07-06","BUY_TO_COVER","SHORT",1,29),
           tx(11,"2025-07-05","BUY","BADPRICE",1,float("nan")),
           tx(12,"2025-07-05","BUY","ZERO",1,0),
           tx(13,"2025-07-05","BUY","BADQTY",float("inf"),1),
           tx(14,"2025-07-05","BUY","BAD SYMBOL",1,1),
           {"transactionId":15,"transactionDate":"invalid"},None],
    "other":[tx(21,"2025-01-01","BUY","APH",2,900),
             tx(22,"2025-02-01","SELL","APH",2,910),
             tx(23,"2025-03-01","BUY","ONLY",1,50),
             tx(24,"2025-04-01","SELL","ONLY",1,51)],
}
if "fixture_vault" not in st.session_state:
    st.session_state["fixture_vault"] = {
        "lock":threading.RLock(),"lots":{},
        "accounts":{(access,key):{"rows":rows,"covered_from":date(2023,1,1),
                    "covered_through":date.today(),"synced_at":time.time()}
                    for access in ("copy-fixture","copy-fixture-2") for key,rows in history.items()}}
st.session_state.setdefault("etrade_accounts", accounts)
st.session_state.setdefault("fixture_portfolio_calls", [])
st.session_state.setdefault("fixture_lot_calls", [])
st.session_state.setdefault("fixture_transaction_calls", [])
st.session_state.setdefault("fixture_archive_calls", [])
class Broker:
    is_offline = False
    def get_portfolio(self, account, **kwargs):
        st.session_state["fixture_portfolio_calls"].append(account)
        return ([position("MSFT",6),position("DUP",2),position("VTI",2.5),
                 position("HTD",3,positionId=700)] if account=="raj" else [])
    def get_position_lots(self, account, identifier):
        st.session_state["fixture_lot_calls"].append((account,identifier))
        return {"PositionLot":[{"remainingQty":3,"price":22.47,"acquiredDate":20230712}]}
    def get_transactions(self, account, **kwargs):
        st.session_state["fixture_transaction_calls"].append(account)
        raise AssertionError("Copy controls must use cached transactions")
    def __getattr__(self, name):
        raise AssertionError("Unexpected broker action: "+name)
def archive(*args, **kwargs):
    st.session_state["fixture_archive_calls"].append("unexpected archive access")
    raise AssertionError("Copy controls must not read or write archives")
def download(label, data, **kwargs):
    st.session_state["fixture_download"] = {"text":data,**kwargs}
    return False
def clipboard(*, data, **kwargs):
    st.session_state["fixture_clipboard"] = data
with (patch.object(acq,"_history_vault",return_value=st.session_state["fixture_vault"]),
      patch.object(acq,"_archive_client",return_value=None),
      patch.object(acq,"GitHubAcquisitionArchive",side_effect=archive),
      patch.object(acq,"PrivateTradeBlotter",side_effect=archive),
      patch.object(acq.st,"download_button",side_effect=download),
      patch.object(acq,"_acq_copy",side_effect=clipboard)):
    if not st.session_state.get("fixture_hide_acquisition"):
        acq.render_acquisition(Broker(),vault_key=st.session_state.get("fixture_access","copy-fixture"))
    else:
        st.write("Another terminal tab")
''', default_timeout=15).run()

    def _check(self, app):
        self.assertFalse(app.exception, [error.message for error in app.exception])
        self.assertEqual(app.session_state["fixture_transaction_calls"], [])
        self.assertEqual(app.session_state["fixture_archive_calls"], [])

    @staticmethod
    def _control(app, kind, label):
        return next(widget for widget in getattr(app, kind) if widget.label == label)

    def _text(self, app):
        self._check(app)
        value = self._control(app, "text_area", "PINE SCRIPT TEXT").value
        self.assertEqual(app.session_state["fixture_clipboard"], value)
        self.assertEqual(app.session_state["fixture_download"]["text"], value)
        return value

    def test_closed_aph_and_partial_sales_use_original_trades_without_changing_fifo_table(self):
        app = self._app()
        self._check(app)
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"BOUGHT + SOLD")
        tickers = self._control(app,"multiselect","TICKERS TO INCLUDE")
        self.assertEqual(set(tickers.options), {"APH","DUP","HTD","MSFT","VTI"})
        self.assertEqual(set(tickers.value), {"DUP","HTD","MSFT","VTI"})
        before = app.dataframe[0].value.copy(deep=True)
        tickers.set_value(["APH","MSFT","DUP"]).run()
        self.assertEqual(self._text(app),
                         "APH 01.05.2025 100 10 B\nAPH 02.05.2025 120 10 S\n"
                         "MSFT 03.05.2025 200 10 B\nMSFT 04.05.2025 210 4 S\n"
                         "DUP 05.05.2025 55 1 B\nDUP 05.05.2025 55 1 B")
        self.assertTrue(before.equals(app.dataframe[0].value))
        msft = before[before["Ticker"] == "MSFT"]
        self.assertEqual(msft["Open Shares"].tolist(), [6])
        self.assertEqual(app.session_state["fixture_download"]["file_name"], "acquisition_bought_sold.txt")

    def test_history_omits_malformed_options_shorts_and_never_invents_broker_lot_buys(self):
        app = self._app()
        self._control(app,"multiselect","TICKERS TO INCLUDE").set_value(["HTD","VTI"]).run()
        self.assertEqual(self._text(app), "VTI 06.05.2025 37.12345678 2.5 B")
        self.assertTrue(any("NO RECORDED BUY/SELL TRADES // HTD" in caption.value for caption in app.caption))
        self.assertEqual(app.session_state["fixture_lot_calls"], [("raj",700)])

    def test_open_mode_preserves_reconciled_and_broker_recovered_lots_for_selected_tickers(self):
        app = self._app()
        self._control(app,"multiselect","TICKERS TO INCLUDE").set_value(["HTD","MSFT","APH"]).run()
        self._control(app,"selectbox","COPY RECORDS").set_value("OPEN LOTS // FIFO").run()
        self.assertEqual(self._text(app),
                         "HTD 07.12.2023 22.47 3 B\nMSFT 03.05.2025 200 6 B")
        self.assertEqual(app.session_state["fixture_lot_calls"], [("raj",700)])
        self.assertEqual(app.session_state["fixture_download"]["file_name"], "acquisition_open_fifo.txt")
        self.assertGreater(len(app.session_state["fixture_portfolio_calls"]), 1)

    def test_closed_only_account_can_select_history_without_current_holdings(self):
        app = self._app()
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("other").run()
        tickers = self._control(app,"multiselect","TICKERS TO INCLUDE")
        self.assertEqual(tickers.options, ["APH","ONLY"])
        self.assertEqual(tickers.value, [])
        self.assertEqual(self._text(app), "")
        tickers.set_value(["APH"]).run()
        self.assertEqual(self._text(app), "APH 01.01.2025 900 2 B\nAPH 02.01.2025 910 2 S")
        self.assertEqual(len(app.dataframe), 0)

    def test_account_preferences_and_explicit_empty_selection_survive_widget_cleanup(self):
        app = self._app()
        self._control(app,"selectbox","COPY RECORDS").set_value("OPEN LOTS // FIFO").run()
        self._control(app,"multiselect","TICKERS TO INCLUDE").set_value([]).run()
        self.assertEqual(self._text(app), "")
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("other").run()
        self._control(app,"multiselect","TICKERS TO INCLUDE").set_value(["ONLY"]).run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"BOUGHT + SOLD")
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("raj").run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"OPEN LOTS // FIFO")
        self.assertEqual(self._control(app,"multiselect","TICKERS TO INCLUDE").value, [])
        app.session_state["fixture_hide_acquisition"] = True
        app.run()
        self._check(app)
        app.session_state["fixture_hide_acquisition"] = False
        app.run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"OPEN LOTS // FIFO")
        self.assertEqual(self._control(app,"multiselect","TICKERS TO INCLUDE").value, [])
        self.assertEqual(self._text(app), "")
        self.assertTrue(app.session_state["fixture_download"]["disabled"])
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("other").run()
        self.assertEqual(self._control(app,"multiselect","TICKERS TO INCLUDE").value, ["ONLY"])
        self.assertEqual(self._text(app), "ONLY 03.01.2025 50 1 B\nONLY 04.01.2025 51 1 S")

    def test_copy_preferences_are_isolated_by_terminal_access_identity(self):
        app = self._app()
        self._control(app,"multiselect","TICKERS TO INCLUDE").set_value(["APH"]).run()
        self._control(app,"selectbox","COPY RECORDS").set_value("OPEN LOTS // FIFO").run()
        app.session_state["fixture_access"] = "copy-fixture-2"
        app.run()
        self._check(app)
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"BOUGHT + SOLD")
        self.assertEqual(set(self._control(app,"multiselect","TICKERS TO INCLUDE").value),
                         {"DUP","HTD","MSFT","VTI"})
        self._control(app,"multiselect","TICKERS TO INCLUDE").set_value([]).run()
        app.session_state["fixture_access"] = "copy-fixture"
        app.run()
        self._check(app)
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"OPEN LOTS // FIFO")
        self.assertEqual(self._control(app,"multiselect","TICKERS TO INCLUDE").value, ["APH"])


if __name__ == "__main__":
    unittest.main()
