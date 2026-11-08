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
    def _app(history_extension=""):
        from streamlit.testing.v1 import AppTest
        source = '''
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
'''
        source = source.replace('\nif "fixture_vault" not in st.session_state:',
                                history_extension + '\nif "fixture_vault" not in st.session_state:')
        return AppTest.from_string(source, default_timeout=15).run()

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


class AcquisitionVerticalHistoryTests(unittest.TestCase):
    @staticmethod
    def option_tx(identifier, action, strike, price, qty=1, *, symbol="SYN", side="CALL",
                  expiry=(2027,1,15), stamp="2026-10-01T10:01:02", order=1001,
                  order_field="orderNo", root="SYN", multiplier=100, currency="USD", account="raj"):
        product = {"symbol":symbol,"securityType":"OPTN","callPut":side,"strikePrice":strike,
                   "expiryYear":expiry[0],"expiryMonth":expiry[1],"expiryDay":expiry[2]}
        if root is not None:
            product["optionRootSymbol"] = root
        brokerage = {"transactionType":action,"product":product,"quantity":qty,"price":price,
                     "optionMultiplier":multiplier,"currency":currency}
        if order is not None:
            brokerage[order_field] = order
        return {"transactionId":identifier,"accountIdKey":account,
                "transactionDate":int(datetime.fromisoformat(stamp).replace(
                    tzinfo=ZoneInfo("America/New_York")).timestamp()*1000),"brokerage":brokerage}

    def parse(self, rows):
        from src.acquisition_ui import _vertical_history
        return _vertical_history(rows)

    def test_opened_and_closed_call_verticals_keep_original_legs_and_cash_flow(self):
        rows = [self.option_tx(1,"BUY_OPEN",100,3.25,2),
                self.option_tx(2,"SELL_OPEN",105,1.5,2),
                self.option_tx(3,"SELL_CLOSE",100,4,2,order=1002,stamp="2026-10-02T11:00:01"),
                self.option_tx(4,"BUY_CLOSE",105,2.75,2,order=1002,stamp="2026-10-02T11:00:01")]
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual(len(events), 2)
        self.assertEqual([(event["event"],event["long"],event["short"],event["qty"],
                           event["net"],event["cash_flow"],event["match"],event["order"])
                          for event in events],
                         [("OPENED",100,105,2,1.75,"DEBIT","ORDER","1001"),
                          ("CLOSED",100,105,2,1.25,"CREDIT","ORDER","1002")])
        self.assertTrue(all(event["ticker"] == "SYN" and event["type"] == "CALL" for event in events))
        self.assertEqual(events[0]["expiry"].isoformat(), "2027-01-15")
        self.assertIsInstance(events[0]["when"], datetime)
        self.assertLess(events[0]["when"], events[1]["when"])

    def test_puts_credit_structures_and_zero_price_closing_fills_are_preserved(self):
        cases = [
            ("PUT","BUY_OPEN","SELL_OPEN",110,100,5,2,"OPENED",3,"DEBIT"),
            ("CALL","BUY_OPEN","SELL_OPEN",110,100,2,4,"OPENED",-2,"CREDIT"),
            ("PUT","SELL_CLOSE","BUY_CLOSE",110,100,0,0,"CLOSED",0,"EVEN"),
            ("CALL","SELL_CLOSE","BUY_CLOSE",110,100,1,3,"CLOSED",-2,"DEBIT"),
        ]
        for side,long_action,short_action,long_strike,short_strike,long_price,short_price,event,net,cash in cases:
            with self.subTest(case=(side,event,cash)):
                events, review = self.parse([
                    self.option_tx(1,long_action,long_strike,long_price,side=side),
                    self.option_tx(2,short_action,short_strike,short_price,side=side)])
                self.assertEqual(review, [])
                self.assertEqual(len(events), 1)
                self.assertEqual((events[0]["event"],events[0]["type"],events[0]["net"],
                                  events[0]["cash_flow"]),(event,side,net,cash))
                self.assertEqual((events[0]["long"],events[0]["short"]),(long_strike,short_strike))

    def test_explicit_open_close_action_and_order_number_aliases(self):
        aliases = [("BUY_OPEN","SELL_OPEN","OPENED"),("BTO","STO","OPENED"),
                   ("BUY_TO_OPEN","SELL_TO_OPEN","OPENED"),("BOUGHT_TO_OPEN","SOLD_TO_OPEN","OPENED"),
                   ("SELL_CLOSE","BUY_CLOSE","CLOSED"),("STC","BTC","CLOSED"),
                   ("SELL_TO_CLOSE","BUY_TO_CLOSE","CLOSED"),("SOLD_TO_CLOSE","BOUGHT_TO_CLOSE","CLOSED")]
        for long_action,short_action,event in aliases:
            with self.subTest(actions=(long_action,short_action)):
                events, review = self.parse([self.option_tx(1,long_action,100,3),
                                             self.option_tx(2,short_action,105,1)])
                self.assertEqual(review, [])
                self.assertEqual([row["event"] for row in events], [event])
        for field in ("orderNo","orderId","orderNumber"):
            with self.subTest(order_field=field):
                events, review = self.parse([self.option_tx(1,"BTO",100,3,order_field=field),
                                             self.option_tx(2,"STO",105,1,order_field=field)])
                self.assertEqual(review, [])
                self.assertEqual(events[0]["order"], "1001")

    def test_balanced_split_fills_use_weighted_prices_and_preserve_distinct_ids(self):
        rows = [self.option_tx(1,"BTO",100,3), self.option_tx(2,"BTO",100,4,2),
                self.option_tx(3,"STO",105,1,2), self.option_tx(4,"STO",105,2)]
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["qty"], 3)
        self.assertAlmostEqual(events[0]["long_price"], 11/3)
        self.assertAlmostEqual(events[0]["short_price"], 4/3)
        self.assertAlmostEqual(events[0]["net"], 7/3)
        identical = [self.option_tx(1,"BTO",100,3),self.option_tx(2,"BTO",100,3),
                     self.option_tx(3,"STO",105,1),self.option_tx(4,"STO",105,1)]
        events, review = self.parse(identical)
        self.assertEqual(review, [])
        self.assertEqual(events[0]["qty"], 2)

    def test_last_corrected_transaction_id_wins_without_mutating_cached_rows(self):
        from copy import deepcopy
        rows = [self.option_tx(1,"BTO",100,3),self.option_tx(2,"STO",105,1),
                self.option_tx(1,"BTO",100,4)]
        before = deepcopy(rows)
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual((events[0]["qty"],events[0]["long_price"],events[0]["net"]),(1,4,3))
        self.assertEqual(rows, before)

    def test_orderless_pair_requires_exact_nonmidnight_timestamp_and_is_labeled_inferred(self):
        events, review = self.parse([self.option_tx(1,"BTO",100,3,order=None),
                                     self.option_tx(2,"STO",105,1,order=None)])
        self.assertEqual(review, [])
        self.assertEqual(events[0]["match"], "TIME // INFERRED")
        for first,second in (("2026-10-01T00:00:00","2026-10-01T00:00:00"),
                             ("2026-10-01T20:00:00","2026-10-01T20:00:00"),
                             ("2026-10-01T10:01:02","2026-10-01T10:01:03")):
            with self.subTest(stamps=(first,second)):
                events, review = self.parse([self.option_tx(1,"BTO",100,3,order=None,stamp=first),
                                             self.option_tx(2,"STO",105,1,order=None,stamp=second)])
                self.assertEqual(events, [])
                self.assertEqual(len(review), 2)

    def test_mismatched_contract_context_or_account_never_forms_a_spread(self):
        for changed in ({"symbol":"OTHER"},{"root":"SYN1"},{"side":"PUT"},
                        {"expiry":(2027,2,19)},{"multiplier":10},{"currency":"CAD"},
                        {"account":"other"},{"stamp":"2026-10-02T10:01:02"},
                        {"order":1002}):
            with self.subTest(changed=changed):
                events, review = self.parse([self.option_tx(1,"BTO",100,3),
                                             self.option_tx(2,"STO",105,1,**changed)])
                self.assertEqual(events, [])
                self.assertEqual(len(review), 2)

    def test_direct_product_symbol_wins_over_unrelated_nested_symbol(self):
        rows = [self.option_tx(1,"BTO",100,3),self.option_tx(2,"STO",105,1)]
        rows = [{"unrelated":{"symbol":"WRONG"},**row} for row in rows]
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual(events[0]["ticker"], "SYN")

    def test_unequal_or_ambiguous_contract_groups_are_wholly_reviewed(self):
        for rows in ([self.option_tx(1,"BTO",100,3,2),self.option_tx(2,"STO",105,1)],
                     [self.option_tx(1,"BTO",100,3),self.option_tx(2,"STO",105,1),
                      self.option_tx(3,"BTO",110,3),self.option_tx(4,"STO",115,1)],
                     [self.option_tx(1,"BTO",100,3,2),self.option_tx(2,"STO",105,1),
                      self.option_tx(3,"STO",110,.5)]):
            with self.subTest(strikes=[row["brokerage"]["product"]["strikePrice"] for row in rows]):
                events, review = self.parse(rows)
                self.assertEqual(events, [])
                self.assertEqual(len(review), len(rows))
                self.assertTrue(all(row["reason"] for row in review))

    def test_partial_close_and_missing_older_open_are_events_without_lifetime_claims(self):
        rows = [self.option_tx(1,"BTO",100,3,3),self.option_tx(2,"STO",105,1,3),
                self.option_tx(3,"STC",100,4,1,order=1002,stamp="2026-10-02T10:01:02"),
                self.option_tx(4,"BTC",105,2,1,order=1002,stamp="2026-10-02T10:01:02")]
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual([(row["event"],row["qty"]) for row in events],[("OPENED",3),("CLOSED",1)])
        closed, review = self.parse(rows[2:])
        self.assertEqual(review, [])
        self.assertEqual([(row["event"],row["qty"]) for row in closed],[("CLOSED",1)])

    def test_same_order_roll_pairs_each_expiry_and_open_close_event_separately(self):
        rows = [self.option_tx(1,"STC",100,2),self.option_tx(2,"BTC",105,.5),
                self.option_tx(3,"BTO",100,3,expiry=(2027,2,19)),
                self.option_tx(4,"STO",105,1,expiry=(2027,2,19))]
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual({(row["event"],row["expiry"].isoformat()) for row in events},
                         {("CLOSED","2027-01-15"),("OPENED","2027-02-19")})

    def test_signed_quantities_are_absolute_but_fractional_and_invalid_fills_are_reviewed(self):
        events, review = self.parse([self.option_tx(1,"BTO",100,3,-2),
                                     self.option_tx(2,"STO",105,1,-2)])
        self.assertEqual(review, [])
        self.assertEqual(events[0]["qty"], 2)
        for price,qty in ((-1,1),(float("nan"),1),(float("inf"),1),(3,0),(3,.5),
                          (3,float("nan")),(3,float("inf"))):
            with self.subTest(price=price,qty=qty):
                events, review = self.parse([self.option_tx(1,"BTO",100,price,qty),
                                             self.option_tx(2,"STO",105,1)])
                self.assertEqual(events, [])
                self.assertTrue(review)
                self.assertTrue(all(row["reason"] for row in review))

    def test_missing_identity_invalid_date_and_unexplicit_actions_never_become_verticals(self):
        for change in ("expiry","strike","symbol","side","date","generic buy","exercise"):
            with self.subTest(change=change):
                broken = self.option_tx(1,"BTO",100,3)
                product = broken["brokerage"]["product"]
                if change == "expiry": product.pop("expiryYear")
                elif change == "strike": product.pop("strikePrice")
                elif change == "symbol": product.pop("symbol")
                elif change == "side": product.pop("callPut")
                elif change == "date": broken["transactionDate"] = "invalid"
                elif change == "generic buy": broken["brokerage"]["transactionType"] = "BUY"
                else: broken["brokerage"]["transactionType"] = "EXERCISE"
                events, review = self.parse([broken,self.option_tx(2,"STO",105,1)])
                self.assertEqual(events, [])
                self.assertTrue(review)
                self.assertTrue(all(row["reason"] for row in review))

    def test_invalid_option_leg_blocks_false_pair_in_the_same_order(self):
        broken = self.option_tx(3,"BUY",110,2)
        events, review = self.parse([self.option_tx(1,"BTO",100,3),
                                     self.option_tx(2,"STO",105,1),broken])
        self.assertEqual(events, [])
        self.assertEqual(len(review), 3)
        expected_keys = {"ticker","when","expiry","type","strike","action","qty","reason"}
        self.assertTrue(all(expected_keys <= row.keys() for row in review))

    def test_malformed_date_on_third_leg_blocks_valid_looking_pair_with_the_same_account_order(self):
        for missing in (True,False):
            with self.subTest(missing=missing):
                broken = self.option_tx(3,"BTO",110,2)
                if missing:
                    broken.pop("transactionDate")
                else:
                    broken["transactionDate"] = "invalid"
                events, review = self.parse([self.option_tx(1,"BTO",100,3),
                                             self.option_tx(2,"STO",105,1),broken])
                self.assertEqual(events, [])
                self.assertEqual(len(review), 3)
                self.assertTrue(all(row["reason"] for row in review))

    def test_reversal_text_is_not_an_option_execution(self):
        events, review = self.parse([self.option_tx(1,"REVERSE BUY_OPEN",100,3),
                                     self.option_tx(2,"STO",105,1)])
        self.assertEqual(events, [])
        self.assertEqual(len(review), 2)

    def test_nontrade_option_types_cannot_become_executions_from_an_order_action_hint(self):
        for kind in ("EXERCISE","ASSIGNMENT","EXPIRATION"):
            with self.subTest(kind=kind):
                broken = self.option_tx(1,kind,100,0)
                broken["brokerage"]["orderAction"] = "BTO"
                events, review = self.parse([broken,self.option_tx(2,"STO",105,0)])
                self.assertEqual(events, [])
                self.assertEqual(len(review), 2)

    def test_option_sold_short_and_bought_to_cover_aliases_pair_without_affecting_share_history(self):
        rows = [self.option_tx(1,"Bought To Open",100,3,order=None),
                self.option_tx(2,"Sold Short",105,1,order=None),
                self.option_tx(3,"Sold To Close",100,4,order=None,stamp="2026-10-02T10:01:02"),
                self.option_tx(4,"Bought To Cover",105,2,order=None,stamp="2026-10-02T10:01:02")]
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual([(row["event"],row["match"]) for row in events],
                         [("OPENED","TIME // INFERRED"),("CLOSED","TIME // INFERRED")])
        from src.acquisition_ui import _equity_trade
        self.assertTrue(all(_equity_trade(row) is None for row in rows))

    def test_contract_product_id_symbols_validate_identity_without_separating_the_two_legs(self):
        from copy import deepcopy
        rows = [self.option_tx(1,"BTO",100,3),self.option_tx(2,"STO",105,1)]
        for row,osi in zip(rows,("SYN---270115C00100000","SYN---270115C00105000")):
            row["brokerage"]["product"]["productId"] = {"symbol":osi}
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual((events[0]["ticker"],events[0]["long"],events[0]["short"]),("SYN",100,105))
        broken = deepcopy(rows)
        broken[1]["brokerage"]["product"]["productId"]["symbol"] = "SYN---270115C00106000"
        events, review = self.parse(broken)
        self.assertEqual(events, [])
        self.assertEqual(len(review), 2)

    def test_all_supplied_osi_identities_must_agree_with_each_other_and_the_contract(self):
        from copy import deepcopy
        rows = [self.option_tx(1,"BTO",100,3,root=None),self.option_tx(2,"STO",105,1,root=None)]
        for row,osi in zip(rows,("SYN---270115C00100000","SYN---270115C00105000")):
            row["osiKey"] = osi
            row["brokerage"]["product"]["productId"] = {"symbol":osi}
        events, review = self.parse(rows)
        self.assertEqual(review, [])
        self.assertEqual((events[0]["long"],events[0]["short"]),(100,105))
        broken = deepcopy(rows)
        broken[0]["brokerage"]["product"]["productId"]["symbol"] = "OTHER---270115C00100000"
        events, review = self.parse(broken)
        self.assertEqual(events, [])
        self.assertEqual(len(review), 2)
        self.assertTrue(all(row["reason"] for row in review))

    def test_ticker_action_aliases_in_contract_descriptions_do_not_override_explicit_actions(self):
        for symbol in ("BTC","STC"):
            with self.subTest(symbol=symbol):
                rows = [self.option_tx(1,"BTO",100,3,symbol=symbol,root=symbol),
                        self.option_tx(2,"STO",105,1,symbol=symbol,root=symbol)]
                for row in rows:
                    row["brokerage"]["description"] = symbol + " JAN 15 2027 CALL CONTRACT"
                events, review = self.parse(rows)
                self.assertEqual(review, [])
                self.assertEqual(events[0]["ticker"], symbol)
                rows[0]["brokerage"]["transactionType"] = "BUY"
                events, review = self.parse(rows)
                self.assertEqual(events, [])
                self.assertEqual(len(review), 2)

    def test_boolean_order_dates_and_reversal_flags_are_reviewed_instead_of_paired(self):
        for field,value in (("transactionDate",True),("orderNo",True),
                            ("reversalFlag",True),("isReversal","Y"),("reversed",1)):
            with self.subTest(field=field):
                broken = self.option_tx(1,"BTO",100,3)
                (broken["brokerage"] if field == "orderNo" else broken)[field] = value
                events, review = self.parse([broken,self.option_tx(2,"STO",105,1)])
                self.assertEqual(events, [])
                self.assertEqual(len(review), 2)

    def test_share_transactions_are_not_option_review_rows_or_spread_events(self):
        events, review = self.parse([transaction(1,"2025-01-01","BUY","APH",10,100),
                                     transaction(2,"2025-02-01","SELL","APH",10,110)])
        self.assertEqual((events,review), ([],[]))


class AcquisitionVerticalCopyTests(unittest.TestCase):
    _control = staticmethod(AcquisitionCopySelectionTests._control)
    _check = AcquisitionCopySelectionTests._check
    _text = AcquisitionCopySelectionTests._text
    vertical_mode = "VERTICALS // OPENED + CLOSED"

    @staticmethod
    def _app():
        return AcquisitionCopySelectionTests._app('''
def option(identifier, action, symbol, strike, price, order, stamp="2026-10-01T10:01:02", side="CALL"):
    row = tx(identifier,stamp,action,symbol,1,price,"OPTN")
    row["brokerage"]["product"].update(callPut=side,strikePrice=strike,
                                      expiryYear=2027,expiryMonth=1,expiryDay=15,
                                      optionRootSymbol=symbol)
    row["brokerage"].update(orderNo=order,optionMultiplier=100,currency="USD")
    return row
history["raj"].extend([
    option(101,"BTO","SYN",100,3,1001), option(102,"STO","SYN",105,1,1001),
    option(103,"STC","SYN",100,4,1002,"2026-10-02T11:01:02"),
    option(104,"BTC","SYN",105,2.5,1002,"2026-10-02T11:01:02"),
    option(105,"BTO","PVT",105,4,1003,side="PUT"),
    option(106,"STO","PVT",100,2,1003,side="PUT"),
    option(107,"BTO","REVIEW",100,3,1004),
])
history["other"].extend([
    option(201,"STC","OTH",50,2,2001), option(202,"BTC","OTH",55,.5,2001),
])
''')

    def _mode(self, app, mode=None):
        return self._control(app,"selectbox","COPY RECORDS").set_value(mode or self.vertical_mode).run()

    def _tickers(self, app):
        return self._control(app,"multiselect","TICKERS TO INCLUDE")

    def test_vertical_selection_controls_table_copy_and_download_without_changing_equity_outputs(self):
        app = self._app()
        self._check(app)
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"BOUGHT + SOLD")
        self.assertEqual(set(self._tickers(app).options), {"APH","DUP","HTD","MSFT","VTI"})
        stock_text = self._text(app)
        equity_table = app.dataframe[0].value.copy(deep=True)
        self._mode(app)
        self.assertEqual(set(self._tickers(app).options), {"OPTION","PVT","REVIEW","SYN"})
        self.assertEqual(set(self._tickers(app).value), set(self._tickers(app).options))
        self._tickers(app).set_value(["SYN"]).run()
        text = self._text(app)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertTrue(all(line.startswith("SYN ") for line in text.splitlines()))
        self.assertIn(" OPENED ", text)
        self.assertIn(" CLOSED ", text)
        self.assertEqual(app.session_state["fixture_download"]["file_name"],"acquisition_verticals.txt")
        vertical_table = next(frame.value for frame in app.dataframe if "Event" in frame.value.columns)
        self.assertEqual(vertical_table["Ticker"].tolist(), ["SYN","SYN"])
        self.assertEqual(vertical_table["Event"].tolist(), ["OPENED","CLOSED"])
        self.assertEqual(vertical_table["Long"].tolist(), [100,100])
        self.assertEqual(vertical_table["Short"].tolist(), [105,105])
        self.assertEqual(vertical_table["Contracts"].tolist(), [1,1])
        self.assertTrue(equity_table.equals(app.dataframe[0].value))
        self._mode(app,"BOUGHT + SOLD")
        self.assertEqual(self._text(app), stock_text)
        self.assertEqual(app.session_state["fixture_download"]["file_name"],"acquisition_bought_sold.txt")
        self.assertEqual(app.session_state["fixture_lot_calls"], [("raj",700)])

    def test_stock_and_vertical_preferences_are_separate_and_empty_survives_tab_cleanup(self):
        app = self._app()
        self._tickers(app).set_value(["APH"]).run()
        stock_text = self._text(app)
        self._mode(app)
        self._tickers(app).set_value(["PVT"]).run()
        self.assertTrue(self._text(app).startswith("PVT "))
        self._mode(app,"OPEN LOTS // FIFO")
        self.assertEqual(self._tickers(app).value, ["APH"])
        self.assertEqual(self._text(app), "")
        self._mode(app,"BOUGHT + SOLD")
        self.assertEqual(self._text(app), stock_text)
        self._mode(app)
        self.assertEqual(self._tickers(app).value, ["PVT"])
        self._tickers(app).set_value([]).run()
        self.assertEqual(self._text(app), "")
        app.session_state["fixture_hide_acquisition"] = True
        app.run()
        self._check(app)
        app.session_state["fixture_hide_acquisition"] = False
        app.run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,self.vertical_mode)
        self.assertEqual(self._tickers(app).value, [])
        self.assertEqual(self._text(app), "")
        self.assertTrue(app.session_state["fixture_download"]["disabled"])
        self.assertEqual(app.session_state["fixture_lot_calls"], [("raj",700)])

    def test_vertical_preferences_are_isolated_by_account_and_terminal_access(self):
        app = self._app()
        self._mode(app)
        self._tickers(app).set_value(["SYN"]).run()
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("other").run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"BOUGHT + SOLD")
        self._mode(app)
        self.assertEqual(self._tickers(app).options, ["OTH"])
        self.assertEqual(self._tickers(app).value, ["OTH"])
        self._tickers(app).set_value([]).run()
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("raj").run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,self.vertical_mode)
        self.assertEqual(self._tickers(app).value, ["SYN"])
        app.session_state["fixture_access"] = "copy-fixture-2"
        app.run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"BOUGHT + SOLD")
        self._mode(app)
        self.assertEqual(set(self._tickers(app).value), {"OPTION","PVT","REVIEW","SYN"})
        self._tickers(app).set_value(["PVT"]).run()
        app.session_state["fixture_access"] = "copy-fixture"
        app.run()
        self.assertEqual(self._tickers(app).value, ["SYN"])
        self.assertTrue(self._text(app).startswith("SYN "))
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("other").run()
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,self.vertical_mode)
        self.assertEqual(self._tickers(app).value, [])
        self.assertEqual(self._text(app), "")

    def test_closed_only_option_history_is_selectable_and_unpaired_legs_are_reviewed_without_copy(self):
        app = self._app()
        self._mode(app)
        self._tickers(app).set_value(["REVIEW"]).run()
        self.assertEqual(self._text(app), "")
        review_table = next(frame.value for frame in app.dataframe if "Reason" in frame.value.columns)
        self.assertEqual(review_table["Ticker"].tolist(), ["REVIEW"])
        self.assertEqual(review_table["Action"].tolist(), ["BTO"])
        self.assertTrue(review_table["Reason"].iloc[0])
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("other").run()
        self._mode(app)
        text = self._text(app)
        self.assertTrue(text.startswith("OTH "))
        self.assertIn(" CLOSED ", text)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertEqual(app.session_state["fixture_download"]["file_name"],"acquisition_verticals.txt")
        self.assertFalse(any("Open Shares" in frame.value.columns for frame in app.dataframe))


if __name__ == "__main__":
    unittest.main()
