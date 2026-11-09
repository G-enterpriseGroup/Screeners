"""Focused Acquisition read-only FIFO/cache/source regression tests."""
from __future__ import annotations

from datetime import date, datetime
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


def _blotter_component(app):
    for node in app.get("component_instance"):
        if "acquisition_vertical_blotter" in node.proto.component_name:
            return node
    raise AssertionError("The production Acquisition blotter component was not rendered")


def _blotter_args(app):
    return json.loads(_blotter_component(app).proto.json_args)


def _blotter_table(app):
    import pandas as pd
    args = _blotter_args(app)
    return pd.DataFrame(args["rows"], columns=[column["label"] for column in args["columns"]])


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
        before = next(frame.value for frame in app.dataframe if "Open Shares" in frame.value.columns).copy(deep=True)
        tickers.set_value(["APH","MSFT","DUP"]).run()
        self.assertEqual(self._text(app),
                         "APH 01.05.2025 100 10 B\nAPH 02.05.2025 120 10 S\n"
                         "MSFT 03.05.2025 200 10 B\nMSFT 04.05.2025 210 4 S\n"
                         "DUP 05.05.2025 55 1 B\nDUP 05.05.2025 55 1 B")
        self.assertTrue(before.equals(next(frame.value for frame in app.dataframe if "Open Shares" in frame.value.columns)))
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


class AcquisitionVerticalBlotterTests(unittest.TestCase):
    @staticmethod
    def event(identifier, phase="OPENED", *, qty=1, net=2, fee=1.3, multiplier=100,
              stamp="2026-10-01T10:00:00", cash="premium", **extra):
        if cash == "premium":
            cash = ((-net if phase == "OPENED" else net) * qty * multiplier - fee
                    if multiplier is not None and fee is not None else None)
            cash_basis = "PREMIUM + FEES" if cash is not None else "UNAVAILABLE"
        else:
            cash_basis = "RECORDED CASH" if cash is not None else "UNAVAILABLE"
        row = {"ticker":"SYN","account":"raj","root":"SYN","expiry":date(2027,1,15),
               "type":"CALL","long":100,"short":105,"qty":qty,"net":net,"event":phase,
               "when":datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("America/New_York")),
               "multiplier":multiplier,"currency":("USD","USD"),"subtype":"",
               "ids":[identifier+"-L",identifier+"-S"],"match":"ORDER",
               "fee":fee,"cash":cash,"cash_basis":cash_basis}
        row.update(extra)
        return row

    @staticmethod
    def blotter(events, review=None):
        from src.acquisition_ui import _vertical_blotter
        return _vertical_blotter(events, review or [])

    def test_debit_credit_call_put_profit_loss_and_zero_premium_closes_use_signed_cash(self):
        for side,long,short,opening,closing in (("CALL",100,105,2,3),("PUT",105,100,2,3),
                                                ("CALL",105,100,-2,-.5),("PUT",100,105,-2,-.5),
                                                ("CALL",100,105,2,0)):
            with self.subTest(side=side,opening=opening,closing=closing):
                rows = self.blotter([self.event("o",qty=2,net=opening,fee=2.6,type=side,long=long,short=short),
                    self.event("c","CLOSED",qty=2,net=closing,fee=2.6,type=side,long=long,short=short,
                               stamp="2026-10-02T10:00:00")])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["status"], "CLOSED")
                self.assertAlmostEqual(rows[0]["pnl"], (closing-opening)*200-5.2)
                self.assertEqual(rows[0]["fees"], 5.2)
                self.assertEqual((rows[0]["open_net"],rows[0]["close_net"]),(opening,closing))

    def test_recorded_cash_includes_fees_and_never_subtracts_them_twice(self):
        rows = self.blotter([self.event("o",cash=-202.75,fee=2.75,multiplier=None),
                            self.event("c","CLOSED",cash=305.5,fee=4.5,multiplier=None,
                                       stamp="2026-10-02T10:00:00")])
        self.assertEqual(rows[0]["pnl"], 102.75)
        self.assertEqual(rows[0]["fees"], 7.25)
        self.assertEqual(rows[0]["currency"], "USD")
        self.assertIn("RECORDED CASH", rows[0]["basis"])

    def test_fifo_splits_each_close_across_original_opening_lots_and_conserves_fees(self):
        events = [self.event("a",qty=2,net=2,fee=2.6),
                  self.event("b",net=3,stamp="2026-10-02T10:00:00"),
                  self.event("c","CLOSED",net=4,stamp="2026-10-03T10:00:00"),
                  self.event("d","CLOSED",qty=2,net=5,fee=2.6,stamp="2026-10-04T10:00:00")]
        rows = self.blotter(list(reversed(events)))
        self.assertEqual([row["status"] for row in rows], ["CLOSED"]*3)
        first = next(row for row in rows if row["close_ids"] == ["c-L","c-S"])
        self.assertEqual((first["open_ids"],first["qty"],first["pnl"]),(["a-L","a-S"],1,197.4))
        later = [row for row in rows if row["close_ids"] == ["d-L","d-S"]]
        self.assertEqual([(row["open_ids"],row["qty"],row["pnl"]) for row in later],
                         [(["a-L","a-S"],1,297.4),(["b-L","b-S"],1,197.4)])
        self.assertAlmostEqual(sum(row["fees"] for row in rows), 7.8)

    def test_partial_close_retains_open_quantity_and_overclose_retains_unmatched_quantity(self):
        rows = self.blotter([self.event("o",qty=3,fee=3.9),
                            self.event("c","CLOSED",net=3,stamp="2026-10-02T10:00:00")])
        closed = next(row for row in rows if row["status"] == "CLOSED")
        pending = next(row for row in rows if row["status"] == "OPEN")
        self.assertEqual((closed["qty"],closed["pnl"],closed["fees"]),(1,97.4,2.6))
        self.assertEqual((pending["qty"],pending["pnl"],pending["fees"]),(2,None,2.6))
        rows = self.blotter([self.event("o"),self.event("c","CLOSED",qty=2,fee=2.6,net=3,
                                                       stamp="2026-10-02T10:00:00")])
        unmatched = next(row for row in rows if row["status"] == "UNMATCHED CLOSE")
        self.assertEqual((unmatched["qty"],unmatched["pnl"],unmatched["fees"]),(1,None,1.3))
        self.assertEqual(sum(row["qty"] for row in rows), 2)

    def test_missing_opening_never_becomes_a_zero_cost_profit_or_a_flat_position(self):
        rows = self.blotter([self.event("c","CLOSED",net=3)])
        self.assertEqual(rows[0]["status"], "UNMATCHED CLOSE")
        self.assertIsNone(rows[0]["pnl"])
        self.assertIsNone(rows[0]["opened"])
        self.assertEqual(rows[0]["open_ids"], [])
        rows = self.blotter([self.event("o")])
        self.assertEqual((rows[0]["status"],rows[0]["pnl"]),("OPEN",None))

    def test_exact_account_root_contract_multiplier_currency_and_context_isolation(self):
        for changed in ({"account":"other"},{"root":"SYN1"},{"type":"PUT"},
                        {"expiry":date(2027,2,19)},{"long":101},{"short":110},
                        {"multiplier":10},{"multiplier":None},{"currency":("CAD","CAD")},
                        {"subtype":"ADJUSTED"},{"contract_context":{"deliverable":"OTHER"}}):
            with self.subTest(changed=changed):
                rows = self.blotter([self.event("o"),self.event("c","CLOSED",net=3,
                    stamp="2026-10-02T10:00:00",**changed)])
                self.assertEqual({row["status"] for row in rows}, {"OPEN","UNMATCHED CLOSE"})
                self.assertTrue(all(row["pnl"] is None for row in rows))

    def test_unknown_cash_currency_or_fees_remain_unknown_without_discarding_recorded_cash(self):
        for currency in (("",""),("USD","CAD")):
            with self.subTest(currency=currency):
                rows = self.blotter([self.event("o",currency=currency),
                    self.event("c","CLOSED",net=3,currency=currency,stamp="2026-10-02T10:00:00")])
                self.assertEqual(rows[0]["status"], "CLOSED")
                self.assertIsNone(rows[0]["pnl"])
                self.assertIn("currency", rows[0]["basis"])
        rows = self.blotter([self.event("o",multiplier=None),
            self.event("c","CLOSED",net=3,multiplier=None,stamp="2026-10-02T10:00:00")])
        self.assertIsNone(rows[0]["pnl"])
        rows = self.blotter([self.event("o",cash=-200,fee=None),
            self.event("c","CLOSED",cash=300,fee=None,stamp="2026-10-02T10:00:00")])
        self.assertEqual(rows[0]["pnl"], 100)
        self.assertIsNone(rows[0]["fees"])

    def test_known_nonstandard_multiplier_is_used_by_cash_basis_without_a_100_assumption(self):
        rows = self.blotter([self.event("o",multiplier=10),
            self.event("c","CLOSED",net=3,multiplier=10,stamp="2026-10-02T10:00:00")])
        self.assertEqual(rows[0]["pnl"], 7.4)

    def test_inferred_pairing_survives_matching_and_cannot_become_order_verified(self):
        for phase in ("OPENED","CLOSED"):
            with self.subTest(phase=phase):
                events = [self.event("o"),self.event("c","CLOSED",net=3,stamp="2026-10-02T10:00:00")]
                next(event for event in events if event["event"] == phase)["match"] = "TIME // INFERRED"
                rows = self.blotter(events)
                self.assertEqual(rows[0]["match"], "TIME // INFERRED")
                self.assertIn("inferred", rows[0]["basis"])

    def test_equal_timestamp_open_and_close_do_not_establish_execution_sequence(self):
        events = [self.event("o"),self.event("c","CLOSED",net=3)]
        for sequence in (events,list(reversed(events))):
            rows = self.blotter(sequence)
            self.assertEqual({row["status"] for row in rows}, {"OPEN","UNMATCHED CLOSE"})
            self.assertTrue(all(row["pnl"] is None for row in rows))

    def test_intervening_leg_change_quarantines_future_matches_but_preserves_prior_realized_slices(self):
        events = [self.event("o",qty=2,fee=2.6),
                  self.event("a","CLOSED",net=3,stamp="2026-10-02T10:00:00"),
                  self.event("b","CLOSED",net=4,stamp="2026-10-04T10:00:00")]
        review = [{"ticker":"SYN","account":"raj","root":"SYN","type":"CALL",
                   "expiry":date(2027,1,15),"strike":105,"qty":1,"action":"BTC",
                   "when":datetime(2026,10,3,10,tzinfo=ZoneInfo("America/New_York")),
                   "reason":"Unpaired roll leg"}]
        rows = self.blotter(events, review)
        closed = [row for row in rows if row["status"] == "CLOSED"]
        self.assertEqual([(row["qty"],row["pnl"]) for row in closed], [(1,97.4)])
        self.assertEqual(sum(row["status"] == "REVIEW" for row in rows), 3)
        self.assertTrue(all(row["pnl"] is None for row in rows if row["status"] == "REVIEW"))

    def test_unrelated_review_legs_do_not_block_other_accounts_strikes_types_or_expiries(self):
        events = [self.event("o"),self.event("c","CLOSED",net=3,stamp="2026-10-02T10:00:00")]
        for change in ({"account":"other"},{"ticker":"OTHER"},{"root":"OTHER"},
                       {"type":"PUT"},{"strike":110},{"expiry":date(2027,2,19)},
                       {"currency":("CAD","CAD")},{"multiplier":10},{"subtype":"OTHER"}):
            with self.subTest(change=change):
                leg = {"ticker":"SYN","account":"raj","root":"SYN","type":"CALL",
                       "expiry":date(2027,1,15),"strike":100,"when":None,"reason":"Needs review",**change}
                rows = self.blotter(events,[leg])
                self.assertEqual([row["pnl"] for row in rows if row["status"] == "CLOSED"], [97.4])

    def test_missing_account_or_review_context_cannot_rule_out_a_lifecycle_interruption(self):
        for account in ("raj",""):
            with self.subTest(account=account):
                events = [self.event("o",account=account),self.event("c","CLOSED",net=3,account=account,
                    stamp="2026-10-02T10:00:00")]
                rows = self.blotter(events,[{"ticker":"SYN","account":"raj","root":"",
                    "type":"CALL","expiry":date(2027,1,15),"strike":100,"when":None,
                    "reason":"Contract context unavailable"}])
                self.assertFalse(any(row["status"] == "CLOSED" for row in rows))
                self.assertTrue(all(row["pnl"] is None for row in rows))

    def test_undated_review_or_assignment_expiration_prevents_assumed_lifecycle_completion(self):
        events = [self.event("o"),self.event("c","CLOSED",net=3,stamp="2026-10-04T10:00:00")]
        for reason,when in (("Missing execution date",None),
                            ("Assignment",datetime(2026,10,3,10,tzinfo=ZoneInfo("America/New_York"))),
                            ("Expiration",datetime(2026,10,3,10,tzinfo=ZoneInfo("America/New_York")))):
            with self.subTest(reason=reason):
                rows = self.blotter(events,[{"ticker":"SYN","account":"raj","type":"CALL",
                    "expiry":date(2027,1,15),"strike":100,"when":when,"reason":reason}])
                self.assertFalse(any(row["status"] == "CLOSED" for row in rows))
                self.assertTrue(all(row["pnl"] is None for row in rows))

    def test_equal_time_opening_lots_with_different_cash_basis_are_not_arbitrarily_ordered(self):
        rows = self.blotter([self.event("a",net=2),self.event("b",net=3),
                            self.event("c","CLOSED",net=4,stamp="2026-10-02T10:00:00")])
        self.assertTrue(all(row["status"] == "REVIEW" and row["pnl"] is None for row in rows))

    def test_equal_time_closes_with_different_cash_are_not_arbitrarily_assigned_fifo_lots(self):
        rows = self.blotter([self.event("a",net=2),
                            self.event("b",net=3,stamp="2026-10-02T10:00:00"),
                            self.event("c","CLOSED",net=4,stamp="2026-10-03T10:00:00"),
                            self.event("d","CLOSED",net=5,stamp="2026-10-03T10:00:00")])
        self.assertTrue(all(row["status"] == "REVIEW" and row["pnl"] is None for row in rows))

    def test_rows_are_json_native_and_inputs_unchanged(self):
        from copy import deepcopy
        import json
        events = [self.event("o"),self.event("c","CLOSED",net=3,stamp="2026-10-02T10:00:00")]
        before = deepcopy(events)
        rows = self.blotter(events)
        self.assertEqual(json.loads(json.dumps(rows,allow_nan=False)), rows)
        self.assertEqual(events, before)
        self.assertTrue(rows[0]["opened"].startswith("2026-10-01T10:00:00"))
        self.assertEqual(rows[0]["expiry"], "2027-01-15")
        self.assertEqual(self.blotter([]), [])


class AcquisitionVerticalCacheTests(unittest.TestCase):
    @staticmethod
    def rows():
        make = AcquisitionVerticalHistoryTests.option_tx
        rows = [make(1,"BTO",100,3),make(2,"STO",105,1),
                make(3,"STC",100,5,order=1002,stamp="2026-10-02T10:01:02"),
                make(4,"BTC",105,2,order=1002,stamp="2026-10-02T10:01:02")]
        for row,amount in zip(rows,(-300.65,99.35,499.35,-200.65)):
            row["brokerage"]["fee"] = .65
            row["amount"] = amount
        return rows

    @staticmethod
    def vault():
        import threading
        return {"lock":threading.RLock()}

    def test_unchanged_source_uses_one_parser_and_matcher_run_including_reordered_rows(self):
        import src.acquisition_ui as acq
        rows, vault = self.rows(), self.vault()
        with (patch.object(acq,"_history_vault",return_value=vault),
              patch.object(acq,"_vertical_history",wraps=acq._vertical_history) as parser,
              patch.object(acq,"_vertical_blotter",wraps=acq._vertical_blotter) as matcher):
            first = acq._cached_verticals(rows,"access","raj")
            self.assertIs(first, acq._cached_verticals(rows,"access","raj"))
            self.assertIs(first, acq._cached_verticals(list(reversed(rows)),"access","raj"))
            self.assertEqual((parser.call_count,matcher.call_count),(1,1))
            self.assertEqual(first["blotter"][0]["pnl"], 97.4)

    def test_cash_fee_quantity_id_and_algorithm_corrections_invalidate_the_cache(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        vault = self.vault()
        with (patch.object(acq,"_history_vault",return_value=vault),
              patch.object(acq,"_vertical_history",wraps=acq._vertical_history) as parser):
            original = acq._cached_verticals(self.rows(),"access","raj")
            for field in ("amount","fee","quantity","transactionId"):
                with self.subTest(field=field):
                    rows = deepcopy(self.rows())
                    target = rows[0] if field in {"amount","transactionId"} else rows[0]["brokerage"]
                    target[field] += 1
                    changed = acq._cached_verticals(rows,"access","raj")
                    self.assertNotEqual(original["source_fingerprint"], changed["source_fingerprint"])
            self.assertEqual(parser.call_count, 5)
            previous = acq._cached_verticals(self.rows(),"access","raj")
            with patch.object(acq,"_VERTICAL_CACHE_VERSION",acq._VERTICAL_CACHE_VERSION+1):
                changed = acq._cached_verticals(self.rows(),"access","raj")
            self.assertNotEqual(previous["algorithm_version"], changed["algorithm_version"])
            self.assertEqual(parser.call_count, 7)

    def test_process_cache_is_access_and_account_isolated(self):
        import src.acquisition_ui as acq
        with (patch.object(acq,"_history_vault",return_value=self.vault()),
              patch.object(acq,"_vertical_history",wraps=acq._vertical_history) as parser):
            first = acq._cached_verticals(self.rows(),"access","raj")
            other_account = acq._cached_verticals(self.rows(),"access","other")
            other_access = acq._cached_verticals(self.rows(),"other-access","raj")
            self.assertIsNot(first, other_account)
            self.assertIsNot(first, other_access)
            self.assertIs(first, acq._cached_verticals(self.rows(),"access","raj"))
            self.assertEqual(parser.call_count, 3)

    def test_result_checksum_rejects_corrupt_derived_rows_and_rebuilds(self):
        import src.acquisition_ui as acq
        rows, vault = self.rows(), self.vault()
        with patch.object(acq,"_history_vault",return_value=vault):
            first = acq._cached_verticals(rows,"access","raj")
            first["blotter"][0]["pnl"] = 999999
            self.assertFalse(acq._valid_vertical_cache(first,first["source_fingerprint"]))
            with patch.object(acq,"_vertical_history",wraps=acq._vertical_history) as parser:
                repaired = acq._cached_verticals(rows,"access","raj")
            self.assertEqual(parser.call_count, 1)
            self.assertEqual(repaired["blotter"][0]["pnl"], 97.4)

    def test_json_roundtrip_reuses_valid_snapshot_and_restores_event_dates_without_recalculation(self):
        import src.acquisition_ui as acq
        rows, vault = self.rows(), self.vault()
        payload = json.loads(json.dumps(acq._build_vertical_cache(rows),allow_nan=False))
        vault["vertical_cache"] = {("access","raj"):payload}
        with (patch.object(acq,"_history_vault",return_value=vault),
              patch.object(acq,"_vertical_history",side_effect=AssertionError("Warm snapshot must be reused")),
              patch.object(acq,"_vertical_blotter",side_effect=AssertionError("Warm snapshot must be reused"))):
            self.assertIs(acq._cached_verticals(rows,"access","raj"), payload)
        events = acq._vertical_event_rows(payload,"events")
        self.assertIsInstance(events[0]["when"], datetime)
        self.assertIsInstance(events[0]["expiry"], date)
        self.assertIsInstance(payload["events"][0]["when"], str)
        self.assertEqual(payload["blotter"][0]["pnl"], 97.4)

    def test_last_transaction_correction_replaces_original_cash_without_double_counting(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        rows = self.rows()
        corrected = deepcopy(rows[0])
        corrected["amount"] = -350.65
        corrected["brokerage"]["price"] = 3.5
        payload = acq._build_vertical_cache(rows+[corrected])
        self.assertEqual(len(payload["events"]), 2)
        self.assertEqual(payload["blotter"][0]["pnl"], 47.4)
        self.assertEqual(payload["blotter"][0]["open_ids"], ["1","2"])


class AcquisitionVerticalCashMetadataTests(unittest.TestCase):
    def parse(self, rows):
        from src.acquisition_ui import _vertical_history, _vertical_blotter
        events, review = _vertical_history(rows)
        return events, review, _vertical_blotter(events,review)

    def test_explicit_cash_fees_and_contract_identity_survive_event_aggregation(self):
        events, review, rows = self.parse(AcquisitionVerticalCacheTests.rows())
        self.assertEqual(review, [])
        self.assertAlmostEqual(events[0]["cash"], -201.3)
        self.assertEqual(events[0]["fee"], 1.3)
        self.assertEqual((events[0]["account"],events[0]["root"],events[0]["multiplier"]),("raj","SYN",100))
        self.assertEqual(events[0]["currency"], ("USD","USD"))
        self.assertEqual(events[0]["ids"], ["1","2"])
        self.assertEqual(rows[0]["pnl"], 97.4)

    def test_recorded_net_cash_needs_neither_guessed_multiplier_nor_missing_fee_defaults(self):
        for field in ("optionMultiplier","fee"):
            with self.subTest(missing=field):
                raw = AcquisitionVerticalCacheTests.rows()
                for item in raw:
                    item["brokerage"].pop(field)
                events, review, rows = self.parse(raw)
                self.assertEqual(review, [])
                self.assertTrue(all(event["cash_basis"] == "RECORDED CASH" for event in events))
                self.assertEqual(rows[0]["pnl"], 97.4)
                if field == "fee":
                    self.assertIsNone(rows[0]["fees"])

    def test_premium_cash_fallback_requires_explicit_multiplier_and_fees(self):
        raw = AcquisitionVerticalCacheTests.rows()
        for item in raw:
            item.pop("amount")
        events, review, rows = self.parse(raw)
        self.assertEqual(review, [])
        self.assertTrue(all(event["cash_basis"] == "PREMIUM + FEES" for event in events))
        self.assertEqual(rows[0]["pnl"], 97.4)
        for item in raw:
            item["brokerage"].pop("optionMultiplier")
        events, review, rows = self.parse(raw)
        self.assertEqual(review, [])
        self.assertTrue(all(event["cash"] is None for event in events))
        self.assertIsNone(rows[0]["pnl"])

    def test_signed_fee_refunds_increase_premium_derived_cash_and_remain_negative_fees(self):
        raw = AcquisitionVerticalCacheTests.rows()
        for item in raw:
            item.pop("amount")
            item["brokerage"]["fee"] = -.65
        events, review, rows = self.parse(raw)
        self.assertEqual(review, [])
        self.assertEqual(events[0]["fee"], -1.3)
        self.assertAlmostEqual(events[0]["cash"], -198.7)
        self.assertEqual(rows[0]["fees"], -2.6)
        self.assertEqual(rows[0]["pnl"], 102.6)

    def test_inconsistent_recorded_amount_does_not_override_verified_premium_fee_math(self):
        raw = AcquisitionVerticalCacheTests.rows()
        raw[0]["amount"] -= 50
        events, review, rows = self.parse(raw)
        self.assertEqual(review, [])
        self.assertIsNone(events[0]["cash"])
        self.assertEqual(events[0]["cash_basis"], "UNAVAILABLE")
        self.assertIsNone(rows[0]["pnl"])

    def test_assignment_review_retains_contract_and_account_for_lifecycle_quarantine(self):
        raw = AcquisitionVerticalCacheTests.rows()
        assignment = AcquisitionVerticalHistoryTests.option_tx(5,"ASSIGNMENT",100,0,order=0,
                                                               stamp="2026-10-01T15:01:02")
        events, review, rows = self.parse(raw+[assignment])
        self.assertEqual(len(events), 2)
        self.assertEqual(len(review), 1)
        self.assertEqual((review[0]["account"],review[0]["ticker"],review[0]["expiry"],review[0]["strike"]),
                         ("raj","SYN",date(2027,1,15),100))
        self.assertFalse(any(row["status"] == "CLOSED" for row in rows))
        self.assertTrue(all(row["pnl"] is None for row in rows))


class AcquisitionVerticalCopyTests(unittest.TestCase):
    _control = staticmethod(AcquisitionCopySelectionTests._control)
    _check = AcquisitionCopySelectionTests._check
    _text = AcquisitionCopySelectionTests._text
    vertical_mode = "VERTICALS // OPENED + CLOSED"

    @staticmethod
    def _app(history_extension=""):
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
''' + history_extension)

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
        equity_table = next(frame.value for frame in app.dataframe if "Open Shares" in frame.value.columns).copy(deep=True)
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
        self.assertTrue(equity_table.equals(next(frame.value for frame in app.dataframe if "Open Shares" in frame.value.columns)))
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

    def test_realized_cash_table_shows_partial_close_and_remaining_open_without_double_fees(self):
        app = self._app('''
for row in history["raj"]:
    identifier = row.get("transactionId") if isinstance(row,dict) else None
    if identifier in (101,102,103,104):
        row["brokerage"]["fee"] = 1.95 if identifier in (101,102) else .65
        row["amount"] = {101:-901.95,102:298.05,103:399.35,104:-250.65}[identifier]
        if identifier in (101,102):
            row["brokerage"]["quantity"] = 3
''')
        self._mode(app)
        self._tickers(app).set_value(["SYN"]).run()
        text = self._text(app)
        self.assertEqual(len(text.splitlines()), 2)
        self._control(app,"multiselect","BLOTTER TICKERS").set_value(["SYN"]).run()
        table = _blotter_table(app)
        closed = table[table["Status"] == "CLOSED"].iloc[0]
        remaining = table[table["Status"] == "OPEN"].iloc[0]
        self.assertEqual((closed["Contracts"],closed["Realized P&L"],closed["Fees"],closed["Ccy"]),(1,-52.6,2.6,"USD"))
        self.assertEqual((remaining["Contracts"],remaining["Fees"]),(2,2.6))
        import pandas as pd
        self.assertTrue(pd.isna(remaining["Realized P&L"]))
        self.assertEqual(_blotter_args(app)["totals"]["groups"][0]["evidence"][0]["pnl"], -52.6)
        payload = app.session_state["fixture_vault"]["vertical_cache"][("copy-fixture","raj")]
        self._tickers(app).set_value([]).run()
        self.assertEqual(self._text(app), "")
        self.assertTrue(app.session_state["fixture_download"]["disabled"])
        unchanged = _blotter_table(app)
        self.assertTrue(table.equals(unchanged))
        self._tickers(app).set_value(["SYN"]).run()
        self.assertIs(payload, app.session_state["fixture_vault"]["vertical_cache"][("copy-fixture","raj")])
        self.assertEqual(self._text(app), text)
        self.assertEqual(app.session_state["fixture_lot_calls"], [("raj",700)])

    def test_malformed_option_date_stays_reviewed_without_breaking_blotter_date_formats(self):
        app = self._app('''
for row in history["raj"]:
    if isinstance(row,dict) and row.get("transactionId") == 107:
        row["transactionDate"] = "invalid"
''')
        self._mode(app)
        self._tickers(app).set_value(["REVIEW","SYN"]).run()
        self._control(app,"multiselect","BLOTTER TICKERS").set_value(["SYN"]).run()
        self._check(app)
        table = _blotter_table(app)
        self.assertTrue(all(table["Ticker"] == "SYN"))
        review = next(frame.value for frame in app.dataframe if "Reason" in frame.value.columns)
        self.assertEqual(review["Date"].tolist(), ["UNKNOWN"])
        self.assertEqual(app.session_state["fixture_transaction_calls"], [])
        self.assertEqual(app.session_state["fixture_archive_calls"], [])



class AcquisitionTopVerticalBlotterTests(unittest.TestCase):
    _control = staticmethod(AcquisitionCopySelectionTests._control)
    _check = AcquisitionCopySelectionTests._check
    cash_history = '''
history["raj"].extend([
    option(108,"STC","PVT",105,6,1005,"2026-10-03T11:01:02",side="PUT"),
    option(109,"BTC","PVT",100,3,1005,"2026-10-03T11:01:02",side="PUT"),
])
amounts = {101:-300.65,102:99.35,103:399.35,104:-250.65,
           105:-400.65,106:199.35,108:599.35,109:-300.65}
for row in history["raj"]:
    if isinstance(row,dict) and row.get("transactionId") in amounts:
        row["amount"] = amounts[row["transactionId"]]
        row["brokerage"]["fee"] = .65
'''

    def _app(self):
        return AcquisitionVerticalCopyTests._app(self.cash_history)

    @staticmethod
    def _table(app):
        return _blotter_table(app)

    @staticmethod
    def _html(app):
        return "\n".join(node.proto.body for node in app.main if node.type == "html")

    def _column(self, app, label):
        self._control(app,"selectbox","FILTER COLUMN").set_value(label).run()
        self._check(app)

    def _apply(self, app):
        self._control(app,"button","APPLY COLUMN").click().run()
        self._check(app)

    def test_always_first_all_retained_history_survives_copy_modes_and_holdings_failures(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        app = self._app()
        self._check(app)
        self.assertEqual(self._control(app,"selectbox","COPY RECORDS").value,"BOUGHT + SOLD")
        elements = list(app.main)
        self.assertLess(elements.index(_blotter_component(app)),elements.index(app.dataframe[0]))
        self.assertEqual(set(self._table(app)["Ticker"]),{"SYN","PVT"})
        self.assertEqual(self._table(app).set_index("Ticker")["Entry Cost"].to_dict(),{"SYN":201.3,"PVT":201.3})
        self.assertEqual(self._table(app).set_index("Ticker")["Exit Cash"].to_dict(),{"SYN":148.7,"PVT":298.7})
        self.assertNotIn("Entry / Exit",self._table(app).columns)
        self.assertEqual((_blotter_args(app)["total_filtered"],_blotter_args(app)["total_source"]),(2,2))
        self.assertEqual(_blotter_args(app)["totals"]["groups"][0]["entry_cost"],402.6)
        self.assertAlmostEqual(_blotter_args(app)["totals"]["groups"][0]["pnl"],44.8)
        self.assertTrue(any("ALL RETAINED SOURCE HISTORY // 01/01/2023" in item.value for item in app.caption))
        controls = AcquisitionVerticalCopyTests()
        for mode in ("OPEN LOTS // FIFO",controls.vertical_mode,"BOUGHT + SOLD"):
            controls._mode(app,mode)
            self.assertEqual(set(self._table(app)["Ticker"]),{"SYN","PVT"})
        original = AppTest.from_string
        for failure in ("offline","portfolio"):
            def from_string(source, **kwargs):
                if failure == "offline":
                    source = source.replace("acq.render_acquisition(Broker(),","acq.render_acquisition(None,")
                else:
                    source = source.replace('st.session_state["fixture_portfolio_calls"].append(account)',
                                            'raise RuntimeError("synthetic holdings failure")')
                return original(source,**kwargs)
            with self.subTest(failure=failure), patch.object(AppTest,"from_string",side_effect=from_string):
                failed = self._app()
                self._check(failed)
                self.assertEqual(set(self._table(failed)["Ticker"]),{"SYN","PVT"})
                self.assertFalse(any("Open Shares" in table.value for table in failed.dataframe))
                self.assertTrue(any("OFFLINE" in item.value or "synthetic holdings failure" in item.value for item in failed.warning))

    def test_top_ticker_preferences_are_independent_and_empty_survives_account_access_and_tab(self):
        app = self._app()
        ticker = lambda: self._control(app,"multiselect","BLOTTER TICKERS")
        self.assertEqual(ticker().value,["ALL TICKERS"])
        ticker().set_value(["ALL TICKERS","SYN"]).run()
        self.assertEqual(ticker().value,["SYN"])
        self.assertEqual(self._table(app)["Ticker"].tolist(),["SYN"])
        controls = AcquisitionVerticalCopyTests()
        controls._mode(app)
        controls._tickers(app).set_value([]).run()
        self.assertEqual(self._table(app)["Ticker"].tolist(),["SYN"])
        self.assertEqual(controls._text(app),"")
        ticker().set_value([]).run()
        self.assertTrue(self._table(app).empty)
        self.assertEqual((_blotter_args(app)["total_filtered"],_blotter_args(app)["total_source"]),(0,2))
        self.assertEqual(_blotter_args(app)["totals"]["groups"],[])
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("other").run()
        self.assertEqual(ticker().value,["ALL TICKERS"])
        self.assertEqual(self._table(app)["Ticker"].tolist(),["OTH"])
        self._control(app,"selectbox","E*TRADE ACCOUNT").set_value("raj").run()
        self.assertEqual(ticker().value,[])
        app.session_state["fixture_hide_acquisition"] = True
        app.run()
        app.session_state["fixture_hide_acquisition"] = False
        app.run()
        self.assertEqual(ticker().value,[])
        app.session_state["fixture_access"] = "copy-fixture-2"
        app.run()
        self.assertEqual(ticker().value,["ALL TICKERS"])
        app.session_state["fixture_access"] = "copy-fixture"
        app.run()
        self.assertEqual(ticker().value,[])
        self._control(app,"button","RESET FILTERS").click().run()
        self.assertEqual(ticker().value,["ALL TICKERS"])
        self.assertEqual(len(self._table(app)),2)
        self._check(app)

    def test_native_column_rules_combine_ranges_choices_text_dates_and_clear_reset(self):
        import src.acquisition_ui as acq
        app = self._app()
        self.assertEqual(set(self._control(app,"selectbox","FILTER COLUMN").options),set(acq._VERTICAL_FILTER_FIELDS))
        self._control(app,"number_input","MINIMUM").set_value(0.0)
        self._apply(app)
        self.assertEqual(self._table(app)["Ticker"].tolist(),["PVT"])
        self.assertEqual(_blotter_args(app)["totals"]["groups"][0]["pnl"],97.4)
        self._column(app,"Type")
        self._control(app,"selectbox","MATCH").set_value("SELECT VALUES")
        self._control(app,"multiselect","VALUES").set_value(["CALL"])
        self._apply(app)
        self.assertTrue(self._table(app).empty)
        self._control(app,"button","CLEAR COLUMN").click().run()
        self.assertEqual(self._table(app)["Ticker"].tolist(),["PVT"])
        self._column(app,"Spread (L / S)")
        self._control(app,"text_input","VALUE").set_value("put 105 / 100")
        self._apply(app)
        self.assertEqual(self._table(app)["Ticker"].tolist(),["PVT"])
        self._column(app,"Opened")
        self._control(app,"date_input","FROM").set_value(date(2026,10,2))
        self._apply(app)
        self.assertTrue(self._table(app).empty)
        self._control(app,"button","CLEAR COLUMN").click().run()
        self.assertEqual(self._table(app)["Ticker"].tolist(),["PVT"])
        self._column(app,"Entry Cost")
        self._control(app,"number_input","MINIMUM").set_value(300.0)
        self._control(app,"number_input","MAXIMUM").set_value(100.0)
        self._apply(app)
        self.assertTrue(any("ordered filter range" in item.value for item in app.error))
        self.assertEqual(self._table(app)["Ticker"].tolist(),["PVT"])
        self._control(app,"button","RESET FILTERS").click().run()
        self.assertFalse(app.error)
        self.assertEqual(len(self._table(app)),2)
        self.assertAlmostEqual(_blotter_args(app)["totals"]["groups"][0]["pnl"],44.8)

    def test_missing_money_and_explicit_empty_choice_are_not_zero_or_all(self):
        app = AcquisitionVerticalCopyTests._app()
        group = _blotter_args(app)["totals"]["groups"][0]
        self.assertIsNone(group["pnl"])
        self.assertEqual((group["known"]["pnl"],group["missing"]["pnl"]),(0,2))
        self._control(app,"selectbox","VALUE STATUS").set_value("MISSING ONLY")
        self._apply(app)
        self.assertEqual(len(self._table(app)),2)
        self._column(app,"Type")
        self._control(app,"selectbox","MATCH").set_value("SELECT VALUES")
        self._apply(app)
        self.assertTrue(self._table(app).empty)
        self.assertEqual((_blotter_args(app)["total_filtered"],_blotter_args(app)["total_source"]),(0,2))
        self._control(app,"button","CLEAR COLUMN").click().run()
        self.assertEqual(len(self._table(app)),2)
        self._column(app,"Realized P&L")
        self._control(app,"selectbox","VALUE STATUS").set_value("KNOWN ONLY")
        self._apply(app)
        self.assertTrue(self._table(app).empty)
        self._control(app,"button","RESET FILTERS").click().run()
        self.assertEqual(len(self._table(app)),2)
        self._check(app)


    def test_fragment_uses_full_retained_rows_and_never_requests_broker_or_archive_data(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
from datetime import date
from unittest.mock import patch
import src.acquisition_ui as acq
from tests.test_acquisition import AcquisitionVerticalCacheTests
payload = acq._build_vertical_cache(AcquisitionVerticalCacheTests.rows())
original = payload["blotter"][0]
payload["blotter"] = [{**original, "ticker":f"R{index:04d}", "opened":"2010-01-01T10:00:00-05:00"} for index in range(350)]
with (patch.object(acq,"_history_vault",side_effect=AssertionError("Fragment cannot access archives")),
      patch.object(acq,"_sync_history",side_effect=AssertionError("Fragment cannot sync history")),
      patch.object(acq,"_cached_verticals",side_effect=AssertionError("Fragment consumes its existing payload"))):
    acq._render_vertical_blotter(payload,"offline-fixture","raj",date(2010,1,1),date(2026,11,8))
''',default_timeout=15).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(self._table(app)),100)
        args = _blotter_args(app)
        self.assertEqual((args["total_filtered"],args["total_source"],args["page_size"]),(350,350,100))
        self.assertEqual(args["totals"]["groups"][0]["pnl"],34090)
        self.assertTrue(any("01/01/2010" in item.value for item in app.caption))
        self.assertEqual(self._table(app)["Realized P&L"].unique().tolist(),[97.4])
        self.assertEqual(next(column["width"] for column in args["columns"] if column["label"] == "Status"),245)
        app.session_state["_acquisition_blotter_preferences"][args["scope"]]["header_page"] = 3
        app.run()
        self.assertEqual(len(self._table(app)),50)
        self.assertEqual(self._table(app).iloc[0]["Ticker"],"R0300")
        self.assertEqual(_blotter_args(app)["totals"]["groups"][0]["pnl"],34090)
        self._control(app,"multiselect","BLOTTER TICKERS").set_value(["R0349"]).run()
        self.assertFalse(app.exception)
        self.assertEqual(self._table(app)["Ticker"].tolist(),["R0349"])
        self.assertEqual((_blotter_args(app)["total_filtered"],_blotter_args(app)["total_source"],_blotter_args(app)["page"]),(1,350,0))
        self.assertEqual(_blotter_args(app)["totals"]["groups"][0]["pnl"],97.4)
        self._control(app,"button","RESET FILTERS").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(self._table(app)),100)
        self.assertEqual(_blotter_args(app)["totals"]["groups"][0]["pnl"],34090)


class AcquisitionVerticalDisplayTests(unittest.TestCase):
    def test_passed_expiry_labels_only_unsettled_rows_without_mutating_cached_cash(self):
        from copy import deepcopy
        import pandas as pd
        from src.acquisition_ui import _vertical_blotter_frame
        original = AcquisitionVerticalBlotterTests.blotter([
            AcquisitionVerticalBlotterTests.event("open", qty=2),
            AcquisitionVerticalBlotterTests.event("close", "CLOSED", net=3,
                                                  stamp="2026-10-02T10:00:00"),
        ])
        opened = next(row for row in original if row["status"] == "OPEN")
        closed = next(row for row in original if row["status"] == "CLOSED")
        report = [*original, {**opened, "ticker": "REVIEW", "status": "REVIEW", "match": "TIME // INFERRED"},
                  {**closed, "ticker": "REVIEW_CLOSE", "status": "REVIEW", "pnl": None},
                  {**opened, "ticker": "TODAY", "expiry": "2027-02-01"},
                  {**opened, "ticker": "FUTURE", "expiry": "2027-02-02"},
                  {**opened, "ticker": "SETTLED", "status": "EXPIRED", "pnl": -201.3,
                   "close_net": 0, "closed": "2027-01-15T16:00:00-05:00"}]
        before = deepcopy(report)
        frame = _vertical_blotter_frame(report, today=date(2027, 2, 1))
        self.assertEqual(report, before)
        self.assertEqual(sorted(frame[frame["Ticker"] == "SYN"]["Status"].tolist()), ["CLOSED", "EXPIRED // UNVERIFIED"])
        review = frame[frame["Ticker"] == "REVIEW"].iloc[0]
        self.assertEqual(review["Status"], "EXPIRED // REVIEW // INFERRED")
        self.assertTrue(pd.isna(review["Realized P&L"]))
        self.assertEqual(frame[frame["Ticker"] == "REVIEW_CLOSE"].iloc[0]["Status"], "REVIEW")
        self.assertEqual(frame[frame["Ticker"] == "TODAY"].iloc[0]["Status"], "OPEN")
        self.assertEqual(frame[frame["Ticker"] == "FUTURE"].iloc[0]["Status"], "OPEN")
        settled = frame[frame["Ticker"] == "SETTLED"].iloc[0]
        self.assertEqual((settled["Status"], settled["Realized P&L"], settled["Expiry"]),
                         ("EXPIRED", -201.3, "01/15/2027"))

    def test_native_styler_keeps_signed_financial_colors_formats_and_numeric_sort_data(self):
        import pandas as pd
        from types import SimpleNamespace
        from src.acquisition_ui import _style_vertical_blotter
        from src.theme import _style_dataframe_body
        from streamlit.dataframe_util import convert_arrow_bytes_to_pandas_df
        from streamlit.elements.lib.pandas_styler_utils import marshall_styler
        frame = pd.DataFrame({"Realized P&L": [-52.6, 97.4, 0.0, None],
                              "Fees": [2.6, 1.3, 0.0, None], "Contracts": [1.0, 3.0, 2.0, None]})
        before = frame.copy(deep=True)
        styled = _style_dataframe_body(_style_vertical_blotter(frame).set_uuid("acq_pnl"))
        proto = SimpleNamespace(styler=SimpleNamespace(uuid=""))
        marshall_styler(proto, styled, "acq_pnl")
        display = convert_arrow_bytes_to_pandas_df(proto.styler.display_values)
        self.assertEqual(display["Realized P&L"].tolist(), ["-52.60", "+97.40", "0.00", ""])
        self.assertEqual(display["Fees"].tolist(), ["2.60", "1.30", "0.00", ""])
        self.assertEqual(display["Contracts"].tolist(), ["1", "3", "2", ""])
        self.assertIn("#T_acq_pnl_row0_col0 { color: #ff433d }", proto.styler.styles)
        self.assertIn("#T_acq_pnl_row1_col0 { color: #4af6c3 }", proto.styler.styles)
        self.assertIn("color: #fb8b1e", proto.styler.styles)
        self.assertTrue(frame.equals(before))
        self.assertTrue(pd.api.types.is_numeric_dtype(styled.data["Realized P&L"]))
        self.assertEqual(styled.data.sort_values("Realized P&L")["Realized P&L"].dropna().tolist(),
                         [-52.6, 0.0, 97.4])

    def test_production_owner_sends_numeric_loss_gain_to_scoped_component_preserving_copy_and_network_calls(self):
        controls = AcquisitionVerticalCopyTests()
        app = controls._app('''
history["raj"].extend([
    option(108,"STC","PVT",105,6,1005,"2026-10-03T11:01:02",side="PUT"),
    option(109,"BTC","PVT",100,3,1005,"2026-10-03T11:01:02",side="PUT"),
])
amounts = {101:-300.65,102:99.35,103:399.35,104:-250.65,
           105:-400.65,106:199.35,108:599.35,109:-300.65}
for row in history["raj"]:
    if isinstance(row,dict) and row.get("transactionId") in amounts:
        row["amount"] = amounts[row["transactionId"]]
        row["brokerage"]["fee"] = .65
''')
        controls._mode(app)
        controls._tickers(app).set_value(["SYN", "PVT"]).run()
        text = controls._text(app)
        self.assertEqual(len(text.splitlines()), 4)
        table = _blotter_table(app)
        self.assertEqual(table.set_index("Ticker")["Realized P&L"].to_dict(), {"SYN": -52.6, "PVT": 97.4})
        self.assertEqual(table["Contracts"].tolist(), [1, 1])
        self.assertTrue(all(table["Fees"] == 2.6))
        component = (Path(__file__).resolve().parents[1] / "src/components/acquisition_vertical_blotter_v1/index.html").read_text()
        self.assertIn("#ff433d", component)
        self.assertIn("#4af6c3", component)
        self.assertTrue(any("GREEN +" in caption.value and "blank P&L = unknown" in caption.value
                            for caption in app.caption))
        self.assertTrue(any("broker-confirmed expiry and its record date" in caption.value for caption in app.caption))
        self.assertAlmostEqual(_blotter_args(app)["totals"]["groups"][0]["evidence"][0]["pnl"],44.8)
        self.assertEqual(app.session_state["fixture_download"]["text"], text)
        self.assertEqual(app.session_state["fixture_transaction_calls"], [])
        self.assertEqual(app.session_state["fixture_archive_calls"], [])


class AcquisitionBlotterHeaderEventTests(unittest.TestCase):
    @staticmethod
    def preferences():
        return {"tickers":None,"rules":[],"revision":0,"error":None,
                "header_rules":[],"header_sort":None,"header_page":0,"header_revision":0,"header_error":None}

    def state_event(self, **changes):
        import src.acquisition_ui as acq
        preferences = self.preferences()
        state = {"_acquisition_blotter_preferences":{"scope-a":preferences,"scope-b":self.preferences()}}
        event = {"scope":"scope-a","context":acq._blotter_component_context("source-a",preferences),
                 "revision":1,"filters":[],"sort":None,"page":0}
        event.update(changes)
        state["table-widget"] = event
        return state, preferences, event

    @staticmethod
    def apply(state, records, source="source-a"):
        import src.acquisition_ui as acq
        with (patch.object(acq.st,"session_state",state),
              patch.object(acq,"_sync_history",side_effect=AssertionError("Header controls cannot sync")),
              patch.object(acq,"_history_vault",side_effect=AssertionError("Header controls cannot read archives")),
              patch.object(acq,"_cached_verticals",side_effect=AssertionError("Header controls cannot recalculate source"))):
            acq._apply_blotter_table_event("scope-a","table-widget",records,source)

    def test_valid_header_rules_sort_and_page_are_acknowledged_without_touching_native_filters_or_cash(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        records = AcquisitionVerticalFilterTests.records()
        before = deepcopy(records)
        rules = [{"field":"Realized P&L","op":"gte","value":0},
                 {"field":"Entry Cost","op":"between","min":0,"max":300}]
        state, preferences, event = self.state_event(filters=rules,sort={"field":"Realized P&L","direction":"desc"},page=2)
        preferences["rules"] = [{"field":"Ticker","op":"in","values":["SYN"]}]
        event["context"] = acq._blotter_component_context("source-a",preferences)
        self.apply(state,records)
        self.assertEqual((preferences["header_rules"],preferences["header_sort"],preferences["header_page"]),
                         (rules,{"field":"Realized P&L","direction":"desc"},2))
        self.assertEqual(preferences["header_revision"],1)
        self.assertIsNone(preferences["header_error"])
        self.assertEqual(preferences["rules"],[{"field":"Ticker","op":"in","values":["SYN"]}])
        selected = acq._filter_vertical_records(records,preferences["rules"]+preferences["header_rules"])
        self.assertEqual(len(selected),1)
        self.assertEqual(acq._vertical_filtered_totals(selected)["groups"][0]["pnl"],97.4)
        self.assertEqual(records,before)

    def test_stale_scope_source_context_revision_and_boolean_revision_are_ignored(self):
        from copy import deepcopy
        records = AcquisitionVerticalFilterTests.records()
        for change,source in (({"scope":"scope-b"},"source-a"),({"context":"stale"},"source-a"),
                              ({},"source-b"),({"revision":0},"source-a"),({"revision":2},"source-a"),
                              ({"revision":True},"source-a")):
            with self.subTest(change=change,source=source):
                state, preferences, event = self.state_event(**change)
                before = deepcopy(state["_acquisition_blotter_preferences"])
                self.apply(state,records,source)
                self.assertEqual(state["_acquisition_blotter_preferences"],before)

    def test_context_changes_for_source_native_selection_rules_or_reset_but_not_header_ack(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        original = self.preferences()
        context = acq._blotter_component_context("source-a",original)
        for change in ({"tickers":[]},{"tickers":["SYN"]},{"rules":[{"field":"Type","op":"eq","value":"PUT"}]},{"revision":1}):
            with self.subTest(change=change):
                preferences = {**deepcopy(original),**change}
                self.assertNotEqual(context,acq._blotter_component_context("source-a",preferences))
        self.assertNotEqual(context,acq._blotter_component_context("source-b",original))
        original.update(header_revision=5,header_page=3,header_sort={"field":"Ticker","direction":"asc"})
        self.assertEqual(context,acq._blotter_component_context("source-a",original))

    def test_rejected_current_event_is_acknowledged_with_error_and_preserves_last_valid_view(self):
        records = AcquisitionVerticalFilterTests.records()
        bad = [{"filters":[{"field":"Type","op":"eq","value":"CALL"}]},
               {"filters":[{"field":"Realized P&L","op":"between","min":2,"max":1}]},
               {"filters":[{"field":"Ticker","op":"eq","value":"SYN"}]*25},
               {"filters":[{"field":"Realized P&L","op":"gte","value":float("inf")}]},
               {"sort":{"field":"Type","direction":"asc"}},
               {"sort":{"field":"Ticker","direction":"ASC"}},
               {"sort":{"field":"Ticker","direction":[]}},
               {"page":-1},{"page":True},{"page":.5},{"rows":[{"Realized P&L":999999}]},
               {"totals":{"pnl":999999}}]
        for change in bad:
            with self.subTest(change=change):
                state, preferences, event = self.state_event(**change)
                preferences.update(header_rules=[{"field":"Entry Cost","op":"gte","value":0}],header_page=1)
                self.apply(state,records)
                self.assertEqual(preferences["header_rules"],[{"field":"Entry Cost","op":"gte","value":0}])
                self.assertEqual(preferences["header_page"],1)
                self.assertEqual(preferences["header_revision"],1)
                self.assertTrue(preferences["header_error"])
                self.assertEqual(state["_acquisition_blotter_preferences"]["scope-b"],self.preferences())

    def test_fresh_valid_event_recovers_after_rejection_and_empty_in_is_not_all(self):
        import src.acquisition_ui as acq
        records = AcquisitionVerticalFilterTests.records()
        state, preferences, event = self.state_event(page=-1)
        self.apply(state,records)
        event.update(revision=2,page=0,filters=[{"field":"Ticker","op":"in","values":[]}])
        self.apply(state,records)
        self.assertEqual(preferences["header_revision"],2)
        self.assertIsNone(preferences["header_error"])
        selected = acq._filter_vertical_records(records,preferences["header_rules"])
        self.assertEqual(selected,[])
        self.assertEqual(acq._vertical_filtered_totals(selected)["groups"],[])

    def test_native_reset_clears_header_controls_and_invalidates_old_context_without_reusing_revision(self):
        import src.acquisition_ui as acq
        state, preferences, event = self.state_event()
        preferences.update(tickers=["SYN"],rules=[{"field":"Ticker","op":"eq","value":"SYN"}],
                           header_rules=[{"field":"Fees","op":"gte","value":1}],
                           header_sort={"field":"Ticker","direction":"desc"},header_page=2,
                           header_error="invalid range",header_revision=7)
        event.update(context=acq._blotter_component_context("source-a",preferences),revision=8)
        with patch.object(acq.st,"session_state",state):
            acq._reset_blotter_filters("scope-a","ticker-widget")
        self.assertEqual((preferences["tickers"],preferences["rules"],preferences["header_rules"],preferences["header_sort"],preferences["header_page"]),
                         (None,[],[],None,0))
        self.assertEqual(preferences["header_revision"],7)
        self.assertIsNone(preferences["header_error"])
        self.assertEqual(state["ticker-widget"],["ALL TICKERS"])
        self.apply(state,AcquisitionVerticalFilterTests.records())
        self.assertEqual(preferences["header_revision"],7)

    def test_numeric_date_and_text_sort_are_stable_with_missing_values_last_in_both_directions(self):
        import src.acquisition_ui as acq
        records = [{"ticker":"b","pnl":None,"opened":None,"id":0},
                   {"ticker":"A","pnl":2,"opened":"2026-02-01T10:00:00-05:00","id":1},
                   {"ticker":"a","pnl":0,"opened":"2025-12-01T10:00:00-05:00","id":2},
                   {"ticker":"c","pnl":2,"opened":"invalid","id":3}]
        for direction,expected in (("asc",[2,1,3,0]),("desc",[1,3,2,0])):
            self.assertEqual([row["id"] for row in acq._sort_vertical_records(records,{"field":"Realized P&L","direction":direction})],expected)
        self.assertEqual([row["id"] for row in acq._sort_vertical_records(records,{"field":"Opened","direction":"asc"})],[2,1,0,3])
        self.assertEqual([row["id"] for row in acq._sort_vertical_records(records,{"field":"Ticker","direction":"asc"})],[1,2,0,3])
        self.assertEqual([row["id"] for row in records],[0,1,2,3])
        self.assertEqual(acq._sort_vertical_records(records,None),records)
        with self.assertRaises(ValueError):
            acq._sort_vertical_records(records,{"field":"Basis","direction":"asc"})

    def test_header_callback_filters_the_fragment_and_full_totals_then_native_reset_restores_all_rows(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
from datetime import date
from unittest.mock import patch
import hashlib
import json
import streamlit as st
import src.acquisition_ui as acq
from tests.test_acquisition import AcquisitionVerticalCacheTests
payload = acq._build_vertical_cache(AcquisitionVerticalCacheTests.rows())
original = payload["blotter"][0]
payload["blotter"] = [{**original,"ticker":f"R{index:04d}"} for index in range(350)]
scope = hashlib.sha256(json.dumps(["header-fixture","raj"]).encode()).hexdigest()[:16]
def apply_header():
    preferences = st.session_state["_acquisition_blotter_preferences"][scope]
    st.session_state["synthetic_header_event"] = {
        "scope":scope,"context":acq._blotter_component_context(payload["source_fingerprint"],preferences),
        "revision":preferences["header_revision"]+1,
        "filters":[{"field":"Ticker","op":"in","values":["R0349"]}],
        "sort":{"field":"Realized P&L","direction":"desc"},"page":0}
    acq._apply_blotter_table_event(scope,"synthetic_header_event",acq._vertical_filter_records(payload),payload["source_fingerprint"])
with (patch.object(acq,"_history_vault",side_effect=AssertionError("Header controls cannot read archives")),
      patch.object(acq,"_sync_history",side_effect=AssertionError("Header controls cannot request broker data")),
      patch.object(acq,"_cached_verticals",side_effect=AssertionError("Header controls cannot recompute source"))):
    st.button("APPLY SYNTHETIC HEADER",on_click=apply_header)
    acq._render_vertical_blotter(payload,"header-fixture","raj",date(2026,10,1),date(2026,11,8))
''',default_timeout=15).run()
        self.assertFalse(app.exception)
        initial = _blotter_args(app)
        self.assertEqual((len(initial["rows"]),initial["total_filtered"]),(100,350))
        self.assertEqual(initial["totals"]["groups"][0]["pnl"],34090)
        next(button for button in app.button if button.label == "APPLY SYNTHETIC HEADER").click().run()
        self.assertFalse(app.exception)
        selected = _blotter_args(app)
        self.assertEqual((selected["ack_revision"],selected["total_filtered"],selected["total_source"]),(1,1,350))
        self.assertEqual(selected["header_rules"],[{"field":"Ticker","op":"in","values":["R0349"]}])
        self.assertEqual(_blotter_table(app)["Ticker"].tolist(),["R0349"])
        self.assertEqual(selected["totals"]["groups"][0]["pnl"],97.4)
        next(button for button in app.button if button.label == "RESET FILTERS").click().run()
        self.assertFalse(app.exception)
        restored = _blotter_args(app)
        self.assertEqual((len(restored["rows"]),restored["total_filtered"],restored["ack_revision"]),(100,350,1))
        self.assertEqual((restored["header_rules"],restored["sort"],restored["page"]),([],None,0))
        self.assertNotEqual(restored["context"],selected["context"])
        self.assertEqual(restored["totals"]["groups"][0]["pnl"],34090)

    def test_server_totals_preserve_python_rounding_numeric_values_zero_and_unknown_per_currency_and_evidence(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
from datetime import date
import src.acquisition_ui as acq
from tests.test_acquisition import AcquisitionVerticalCacheTests
payload = acq._build_vertical_cache(AcquisitionVerticalCacheTests.rows())
original = payload["blotter"][0]
payload["blotter"] = [
    {**original,"ticker":"ROUND","currency":"USD","pnl":1.125},
    {**original,"ticker":"ZERO","currency":"USD","pnl":0,"match":"TIME // INFERRED"},
    {**original,"ticker":"UNKNOWN","currency":"CAD","status":"OPEN","closed":None,"pnl":None,"fees":None},
]
acq._render_vertical_blotter(payload,"format-fixture","raj",date(2026,10,1),date(2026,11,8))
''',default_timeout=15).run()
        self.assertFalse(app.exception)
        groups = {group["currency"]:group for group in _blotter_args(app)["totals"]["groups"]}
        usd, cad = groups["USD"], groups["CAD"]
        self.assertEqual((usd["pnl"],usd["gains"],usd["losses"]),(1.125,1.125,0))
        self.assertEqual((usd["display"]["pnl"],usd["display"]["gains"],usd["display"]["losses"]),("+1.12","+1.12","0.00"))
        evidence = {subtotal["match"]:subtotal for subtotal in usd["evidence"]}
        self.assertEqual((evidence["ORDER"]["pnl"],evidence["ORDER"]["display"]["pnl"]),(1.125,"+1.12"))
        self.assertEqual((evidence["TIME // INFERRED"]["pnl"],evidence["TIME // INFERRED"]["display"]["pnl"]),(0,"0.00"))
        self.assertIsNone(cad["pnl"])
        self.assertEqual(cad["display"]["pnl"],"—")
        self.assertEqual((cad["known"]["pnl"],cad["missing"]["pnl"]),(0,1))
        for group in groups.values():
            for subtotal in [group,*group["evidence"]]:
                self.assertEqual(subtotal["display"]["contracts"],str(int(subtotal["contracts"])))
                for field in ("pnl","gains","losses","entry_cost","exit_cash","fees"):
                    if subtotal[field] is None:
                        self.assertEqual(subtotal["display"][field],"—")
                    elif subtotal[field] == 0:
                        self.assertEqual(subtotal["display"][field],"0.00")

    def test_component_keeps_column_headers_rows_and_totals_in_one_accessible_table(self):
        from html.parser import HTMLParser
        class Structure(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.elements = {}
            def handle_starttag(self,tag,attributes):
                attributes = dict(attributes)
                if attributes.get("id"):
                    self.elements[attributes["id"]] = (tag,attributes,tuple(self.stack))
                if tag not in {"meta","input","br","hr","link","img","col"}:
                    self.stack.append((tag,attributes.get("id")))
            def handle_endtag(self,tag):
                if self.stack and self.stack[-1][0] == tag:
                    self.stack.pop()
        parser = Structure()
        parser.feed((Path(__file__).resolve().parents[1] / "src/components/acquisition_vertical_blotter_v1/index.html").read_text())
        for identifier in ("columns","headers","rows","totals"):
            self.assertIn(("table","vertical-table"),parser.elements[identifier][2])
        self.assertEqual(parser.elements["totals"][0],"tfoot")
        self.assertEqual(parser.elements["filter-panel"][1]["role"],"dialog")
        self.assertEqual(parser.elements["grand-summary"][1]["aria-label"],"Realized P&L grand totals")
        self.assertIn(("main","blotter"),parser.elements["filter-panel"][2])


class AcquisitionPnlCacheValidationTests(unittest.TestCase):
    def test_two_digit_history_expiry_matches_four_digit_contract_and_review_context(self):
        from src.acquisition_ui import _build_vertical_cache
        raw = AcquisitionVerticalCacheTests.rows()
        for item in raw[:2]:
            item["brokerage"]["product"]["expiryYear"] = 27
        payload = _build_vertical_cache(raw)
        self.assertEqual({event["expiry"] for event in payload["events"]}, {"2027-01-15"})
        self.assertEqual(payload["blotter"][0]["pnl"], 97.4)
        assignment = AcquisitionVerticalHistoryTests.option_tx(5,"ASSIGNMENT",100,0,order=0,
                                                               stamp="2026-10-01T15:01:02",expiry=(27,1,15))
        payload = _build_vertical_cache(raw + [assignment])
        self.assertEqual(payload["review"][0]["expiry"], "2027-01-15")
        self.assertTrue(all(row["pnl"] is None for row in payload["blotter"]))
        for item in raw:
            item["brokerage"]["product"]["expiryYear"] = True
        payload = _build_vertical_cache(raw)
        self.assertEqual(payload["events"], [])
        self.assertEqual(len(payload["review"]), 4)

    def test_malformed_cached_numbers_are_rejected_before_rendering(self):
        from src.acquisition_ui import _build_vertical_cache, _vertical_result_fingerprint, _valid_vertical_cache
        from copy import deepcopy
        valid = _build_vertical_cache(AcquisitionVerticalCacheTests.rows())
        for key, field, value in (("events", "net", "invalid"), ("blotter", "long", "invalid"),
                                  ("blotter", "pnl", True)):
            with self.subTest(key=key, field=field):
                broken = deepcopy(valid)
                broken[key][0][field] = value
                broken["result_fingerprint"] = _vertical_result_fingerprint(broken)
                self.assertFalse(_valid_vertical_cache(broken, broken["source_fingerprint"]))

    def test_boolean_multiplier_cannot_supply_a_cash_pnl_basis(self):
        from src.acquisition_ui import _vertical_history
        option = AcquisitionVerticalHistoryTests.option_tx
        events, review = _vertical_history([
            option(1, "BTO", 100, 3, multiplier=True),
            option(2, "STO", 105, 1, multiplier=True),
        ])
        self.assertEqual(events, [])
        self.assertEqual(len(review), 2)

    def test_structurally_corrupt_cache_is_rebuilt_even_with_a_valid_result_checksum(self):
        from src.acquisition_ui import _build_vertical_cache, _vertical_result_fingerprint, _valid_vertical_cache
        from copy import deepcopy
        valid = _build_vertical_cache([])
        self.assertTrue(_valid_vertical_cache(valid, valid["source_fingerprint"]))
        for key, row in (("events", {}), ("review", {}), ("blotter", {})):
            with self.subTest(key=key):
                broken = deepcopy(valid)
                broken[key] = [row]
                broken["result_fingerprint"] = _vertical_result_fingerprint(broken)
                self.assertFalse(_valid_vertical_cache(broken, broken["source_fingerprint"]))


class AcquisitionVerticalFilterTests(unittest.TestCase):
    @staticmethod
    def payload():
        import src.acquisition_ui as acq
        rows = AcquisitionVerticalCacheTests.rows()
        for row in rows[:2]:
            row["brokerage"]["quantity"] = 3
            row["brokerage"]["fee"] = 1.95
        rows[0]["amount"], rows[1]["amount"] = -901.95, 298.05
        return acq._build_vertical_cache(rows)

    @staticmethod
    def records(payload=None):
        from src.acquisition_ui import _vertical_filter_records
        return _vertical_filter_records(payload or AcquisitionVerticalFilterTests.payload(),today=date(2027,2,1))

    def test_partial_cash_enrichment_allocates_source_event_once_and_leaves_quotes_unchanged(self):
        from src.acquisition_ui import _vertical_filtered_totals
        rows = self.records()
        closed = next(row for row in rows if row["status"] == "CLOSED")
        pending = next(row for row in rows if row["status"] == "OPEN")
        self.assertAlmostEqual(closed["entry_cost"], 201.3)
        self.assertAlmostEqual(pending["entry_cost"], 402.6)
        self.assertAlmostEqual(closed["entry_cash"], -201.3)
        self.assertAlmostEqual(closed["exit_cash"], 298.7)
        self.assertIsNone(pending["exit_cash"])
        self.assertEqual(closed["open_net"], 2)
        self.assertEqual(pending["display_status"], "EXPIRED // UNVERIFIED")
        totals = _vertical_filtered_totals(rows)
        self.assertEqual(totals["rows"], 2)
        self.assertEqual(totals["contracts"], 3)
        self.assertAlmostEqual(totals["groups"][0]["entry_cost"], 603.9)
        self.assertEqual(totals["groups"][0]["missing"]["exit_cash"], 1)

    def test_credit_cost_and_confirmed_expiry_exit_cash_preserve_real_signed_cash(self):
        import src.acquisition_ui as acq
        raw = AcquisitionVerticalExpirationTests.opening(long=105,short=100,long_price=1,short_price=3)
        proof = AcquisitionVerticalExpirationTests.expiration(long=105,short=100)
        payload = acq._build_vertical_cache(raw+proof)
        row = self.records(payload)[0]
        self.assertAlmostEqual(row["entry_cash"], 198.7)
        self.assertAlmostEqual(row["entry_cost"], -198.7)
        self.assertEqual(row["exit_cash"], 0)
        self.assertEqual(row["pnl"], 198.7)
        payload["review"].pop()
        self.assertIsNone(self.records(payload)[0]["exit_cash"])

    def test_one_close_across_distinct_fifo_opening_costs_keeps_each_cash_slice_separate(self):
        import src.acquisition_ui as acq
        first = AcquisitionVerticalExpirationTests.opening(qty=2)
        second = AcquisitionVerticalExpirationTests.opening(long_price=4)
        for row in second:
            row["transactionId"] += 10
            row["brokerage"]["orderNo"] += 10
            row["transactionDate"] += 3600000
        closing = AcquisitionVerticalCacheTests.rows()[2:]
        for row in closing:
            row["brokerage"]["quantity"] = 3
            row["brokerage"]["fee"] = 1.95
        closing[0]["amount"], closing[1]["amount"] = 1498.05, -601.95
        rows = self.records(acq._build_vertical_cache(first+second+closing))
        self.assertEqual([(row["qty"],row["entry_cost"],row["exit_cash"]) for row in rows],
                         [(2,402.6,597.4),(1,301.3,298.7)])
        totals = acq._vertical_filtered_totals(rows)["groups"][0]
        self.assertAlmostEqual(totals["entry_cost"], 703.9)
        self.assertAlmostEqual(totals["exit_cash"], 896.1)
        filtered = acq._filter_vertical_records(rows,[{"field":"Entry Cost","op":"between","min":300,"max":350}])
        self.assertEqual([row["open_ids"] for row in filtered], [["11","12"]])
        totals = acq._vertical_filtered_totals(filtered)["groups"][0]
        self.assertEqual((totals["rows"],totals["entry_cost"],totals["exit_cash"]),(1,301.3,298.7))

    def test_source_id_collision_missing_identity_or_quantity_never_invents_cash(self):
        from copy import deepcopy
        payload = self.payload()
        for changed in ("collision","reused ID","missing IDs","ticker","type","expiry","strike","date","qty","currency"):
            with self.subTest(changed=changed):
                broken = deepcopy(payload)
                if changed == "collision": broken["events"].append(deepcopy(broken["events"][0]))
                elif changed == "reused ID": broken["events"][1]["ids"][0] = broken["events"][0]["ids"][0]
                else:
                    row = broken["blotter"][0]
                    if changed == "missing IDs": row["open_ids"] = ["missing"]
                    elif changed == "ticker": row["ticker"] = "OTHER"
                    elif changed == "type": row["type"] = "PUT"
                    elif changed == "expiry": row["expiry"] = "2028-01-15"
                    elif changed == "strike": row["long"] = 101
                    elif changed == "date": row["opened"] = "2026-10-02T10:01:02-04:00"
                    elif changed == "currency": row["currency"] = "CAD"
                    else: row["qty"] = 100
                self.assertIsNone(self.records(broken)[0]["entry_cash"])

    def test_unknown_cash_stays_missing_and_enrichment_does_not_mutate_cached_payload(self):
        from copy import deepcopy
        payload = self.payload()
        before = deepcopy(payload)
        rows = self.records(payload)
        rows[0]["open_ids"].append("presentation-only")
        self.assertEqual(payload, before)
        self.assertEqual(json.loads(json.dumps(self.records(payload),allow_nan=False)),self.records(payload))
        payload["events"][0]["cash"] = None
        self.assertTrue(all(row["entry_cash"] is None for row in self.records(payload)))
        payload["blotter"].append({"long":None,"short":105})
        self.assertEqual(len(self.records(payload)), 2)

    def test_every_displayed_and_advanced_column_has_a_working_typed_filter(self):
        from src.acquisition_ui import _VERTICAL_FILTER_FIELDS, _filter_vertical_records
        row = next(row for row in self.records() if row["status"] == "CLOSED")
        for label,(key,kind) in _VERTICAL_FILTER_FIELDS.items():
            with self.subTest(column=label):
                value = row[key]
                rule = ({"field":label,"op":"between","min":value,"max":value} if kind in {"number","date"}
                        else {"field":label,"op":"eq","value":value})
                self.assertEqual(_filter_vertical_records([row],[rule]), [row])

    def test_intersecting_ticker_cost_profit_and_status_filters_recalculate_exact_visible_totals(self):
        from src.acquisition_ui import _filter_vertical_records, _vertical_filtered_totals
        rows = _filter_vertical_records(self.records(),[
            {"field":"Ticker","op":"in","values":["syn"]},
            {"field":"Entry Cost","op":"between","min":0,"max":300},
            {"field":"pnl","op":"gte","value":0},
            {"field":"Lifecycle Status","op":"eq","value":"CLOSED"}])
        totals = _vertical_filtered_totals(rows)
        self.assertEqual((totals["rows"],totals["contracts"]),(1,1))
        self.assertAlmostEqual(totals["groups"][0]["entry_cost"], 201.3)
        self.assertEqual(totals["groups"][0]["pnl"], 97.4)
        boundary = _filter_vertical_records(self.records(),[{"field":"entry_cost","op":"between","min":201.3,"max":201.3}])
        self.assertEqual(len(boundary), 1)

    def test_missing_numeric_and_date_values_fail_bounds_but_explicit_zero_survives(self):
        from src.acquisition_ui import _filter_vertical_records
        rows = [{"pnl":value,"opened":stamp} for value,stamp in (
            (None,None),(0,"2026-01-01T10:00:00-05:00"),(1,"invalid"),(float("nan"),None),(True,None))]
        self.assertEqual(_filter_vertical_records(rows,[{"field":"pnl","op":"between","min":0,"max":0}]),[rows[1]])
        self.assertEqual(_filter_vertical_records(rows,[{"field":"opened","op":"between","min":"2026-01-01","max":"2026-01-01"}]),[rows[1]])
        self.assertEqual(len(_filter_vertical_records(rows,[{"field":"pnl","op":"is_missing"}])),3)
        self.assertEqual(_filter_vertical_records(rows,[{"field":"pnl","op":"ne","value":0}]),[rows[2]])

    def test_text_matching_is_literal_case_insensitive_and_empty_selection_is_retained(self):
        from src.acquisition_ui import _filter_vertical_records, _vertical_filtered_totals
        rows = [{"ticker":"A.B"},{"ticker":"ABC"}]
        self.assertEqual(_filter_vertical_records(rows,[{"field":"ticker","op":"contains","value":"."}]),[rows[0]])
        self.assertEqual(_filter_vertical_records(rows,[{"field":"ticker","op":"eq","value":"a.b"}]),[rows[0]])
        self.assertEqual(_filter_vertical_records(rows,[]),rows)
        self.assertEqual(_filter_vertical_records(rows,[{"field":"ticker","op":"in","values":[]}]),[])
        self.assertEqual(_vertical_filtered_totals([]),{"rows":0,"contracts":0,"groups":[]})
        missing_ids = [{"open_ids":[],"close_ids":[]}]
        self.assertEqual(_filter_vertical_records(missing_ids,[{"field":"Opening IDs","op":"is_missing"}]),missing_ids)
        self.assertEqual(_filter_vertical_records(missing_ids,[{"field":"Closing IDs","op":"is_known"}]),[])

    def test_invalid_fields_operations_nonfinite_values_and_reversed_ranges_are_rejected(self):
        from src.acquisition_ui import _filter_vertical_records
        for rule in ({"field":"invalid","op":"eq","value":1},{"field":"pnl","op":"eval","value":1},
                     {"field":[],"op":"eq","value":1},{"field":"pnl","op":[]},
                     {"field":"pnl","op":"gte","value":float("inf")},{"field":"pnl","op":"lte","value":True},
                     {"field":"pnl","op":"between","min":2,"max":1},{"field":"opened","op":"gte","value":"invalid"},
                     {"field":"ticker","op":"between","min":"A","max":"Z"},{"field":"pnl","op":"contains","value":"0"}):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                _filter_vertical_records([], [rule])

    def test_currency_and_inferred_totals_stay_separate_with_unknown_counts_and_zero_preserved(self):
        from src.acquisition_ui import _vertical_filtered_totals
        rows = [{"currency":"USD","match":"ORDER","status":"CLOSED","qty":1,"pnl":100,"fees":1},
                {"currency":"USD","match":"TIME // INFERRED","status":"EXPIRED","qty":1,"pnl":-20,"fees":None},
                {"currency":"CAD","match":"ORDER","status":"CLOSED","qty":1,"pnl":5,"fees":0},
                {"currency":"USD","match":"ORDER","status":"OPEN","qty":2,"pnl":999,"fees":None}]
        totals = _vertical_filtered_totals(rows)
        groups = {group["currency"]:group for group in totals["groups"]}
        self.assertEqual((groups["USD"]["pnl"],groups["USD"]["gains"],groups["USD"]["losses"]),(80,100,-20))
        self.assertEqual({item["match"]:item["pnl"] for item in groups["USD"]["evidence"]}, {"ORDER":100,"TIME // INFERRED":-20})
        self.assertEqual(groups["USD"]["missing"]["pnl"], 1)
        self.assertEqual(groups["CAD"]["fees"], 0)
        unknown = _vertical_filtered_totals([{"currency":"USD","status":"OPEN","qty":1,"pnl":None}])["groups"][0]
        self.assertIsNone(unknown["pnl"])
        self.assertIsNone(unknown["gains"])
        zero = _vertical_filtered_totals([{"currency":"USD","status":"CLOSED","qty":1,"pnl":0}])["groups"][0]
        self.assertEqual((zero["pnl"],zero["gains"],zero["losses"]),(0,0,0))


class AcquisitionVerticalExpirationTests(unittest.TestCase):
    @staticmethod
    def opening(*, qty=1, side="CALL", long=100, short=105, long_price=3, short_price=1):
        make = AcquisitionVerticalHistoryTests.option_tx
        rows = [make(1,"BTO",long,long_price,qty,side=side),make(2,"STO",short,short_price,qty,side=side)]
        for row,sign,price in zip(rows,(-1,1),(long_price,short_price)):
            row["brokerage"]["fee"] = .65*qty
            row["amount"] = sign*price*qty*100-.65*qty
        return rows

    @staticmethod
    def expiration(*, qty=1, side="CALL", long=100, short=105, stamp="2027-01-16T10:00:00"):
        make = AcquisitionVerticalHistoryTests.option_tx
        rows = [make(5,"Option Expired",long,0,-qty,side=side,order=None,stamp=stamp),
                make(6,"Option Expired",short,0,qty,side=side,order=None,stamp=stamp)]
        for row in rows:
            row["amount"] = 0
            row["brokerage"]["fee"] = 0
            row["brokerage"].pop("optionMultiplier")
            row["brokerage"]["product"]["expiryYear"] = 27
        return rows

    @staticmethod
    def parse(rows):
        from src.acquisition_ui import _vertical_history, _vertical_blotter
        events, review = _vertical_history(rows)
        return events, review, _vertical_blotter(events,review)

    def test_broker_confirmed_worthless_debit_and_credit_call_put_spreads_realize_signed_opening_cash(self):
        for side,long,short,long_price,short_price,pnl in (
                ("CALL",100,105,3,1,-201.3),("PUT",105,100,3,1,-201.3),
                ("CALL",105,100,1,3,198.7),("PUT",100,105,1,3,198.7)):
            with self.subTest(side=side,long=long,short=short):
                events, review, rows = self.parse(self.opening(side=side,long=long,short=short,
                    long_price=long_price,short_price=short_price)+self.expiration(side=side,long=long,short=short))
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event"], "OPENED")
                self.assertEqual([leg["lifecycle"] for leg in review], ["EXPIRATION","EXPIRATION"])
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]["status"],rows[0]["qty"],rows[0]["pnl"],rows[0]["close_net"]),
                                 ("EXPIRED",1,pnl,0))
                self.assertEqual(rows[0]["fees"], 1.3)
                self.assertEqual(rows[0]["close_ids"], ["5","6"])
                self.assertTrue(rows[0]["closed"].startswith("2027-01-16T10:00:00"))

    def test_partial_close_on_expiry_day_then_confirmed_remaining_expiration_ignores_bookkeeping_clock_order(self):
        raw = self.opening(qty=3)
        closing = AcquisitionVerticalCacheTests.rows()[2:]
        for row in closing:
            row["transactionDate"] = int(datetime(2027,1,15,15,tzinfo=ZoneInfo("America/New_York")).timestamp()*1000)
        events, review, rows = self.parse(raw+closing+self.expiration(qty=2,stamp="2027-01-15T08:00:00"))
        self.assertEqual([event["event"] for event in events], ["OPENED","CLOSED"])
        self.assertEqual({row["status"] for row in rows}, {"CLOSED","EXPIRED"})
        closed = next(row for row in rows if row["status"] == "CLOSED")
        expired = next(row for row in rows if row["status"] == "EXPIRED")
        self.assertEqual((closed["qty"],closed["pnl"]),(1,97.4))
        self.assertEqual((expired["qty"],expired["pnl"]),(2,-402.6))
        self.assertAlmostEqual(sum(row["fees"] for row in rows), 5.2)

    def test_expiration_allocates_multiple_exact_fifo_openings_without_losing_original_cash_basis(self):
        raw = self.opening()
        second = self.opening(long_price=4)
        for row in second:
            row["transactionId"] += 10
            row["brokerage"]["orderNo"] += 10
            row["transactionDate"] += 86400000
        events, review, rows = self.parse(raw+second+self.expiration(qty=2))
        self.assertEqual(len(events), 2)
        self.assertEqual([(row["status"],row["qty"],row["pnl"]) for row in rows],
                         [("EXPIRED",1,-201.3),("EXPIRED",1,-301.3)])
        self.assertEqual({tuple(row["open_ids"]) for row in rows}, {("1","2"),("11","12")})

    def test_partial_expiration_never_values_the_unconfirmed_remaining_quantity(self):
        events, review, rows = self.parse(self.opening(qty=3)+self.expiration())
        self.assertEqual({row["status"] for row in rows}, {"EXPIRED","OPEN"})
        self.assertEqual([(row["qty"],row["pnl"]) for row in rows if row["status"] == "EXPIRED"], [(1,-201.3)])
        self.assertEqual([(row["qty"],row["pnl"]) for row in rows if row["status"] == "OPEN"], [(2,None)])

    def test_separate_expiration_leg_posting_dates_resolve_unique_inventory_with_latest_evidence_date(self):
        evidence = self.expiration()
        evidence[1]["transactionDate"] += 2*86400000+1000
        events, review, rows = self.parse(self.opening()+evidence)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"],rows[0]["pnl"]),("EXPIRED",-201.3))
        self.assertTrue(rows[0]["closed"].startswith("2027-01-18T10:00:01"))

    def test_incomplete_unbalanced_uncovered_or_wrong_signed_expiration_stays_unvalued(self):
        from copy import deepcopy
        one_sided = self.expiration()[:1]
        unbalanced = self.expiration(qty=2)
        unbalanced[1]["brokerage"]["quantity"] = 1
        reversed_sides = self.expiration()
        for row in reversed_sides:
            row["brokerage"]["quantity"] *= -1
        for opening,evidence in ((self.opening(),one_sided),(self.opening(qty=2),unbalanced),
                                 (self.opening(),self.expiration(qty=2)),([],self.expiration()),
                                 (self.opening(),reversed_sides)):
            with self.subTest(openings=len(opening),evidence=deepcopy(evidence)):
                events, review, rows = self.parse(opening+evidence)
                self.assertFalse(any(row["status"] == "EXPIRED" for row in rows))
                self.assertTrue(all(row["pnl"] is None for row in rows))
                self.assertTrue(any(row["status"] == "REVIEW" for row in rows))

    def test_shared_contract_across_different_verticals_prevents_iteration_based_expiry_assignment(self):
        raw = self.opening()
        second = self.opening(short=110)
        for row in second:
            row["transactionId"] += 10
            row["brokerage"]["orderNo"] += 10
            row["transactionDate"] += 86400000
        evidence = self.expiration(qty=2)
        evidence[1]["brokerage"]["quantity"] = 1
        extra_short = self.expiration(short=110)[1]
        extra_short["transactionId"] = 7
        events, review, rows = self.parse(raw+second+evidence+[extra_short])
        self.assertEqual(len(events), 2)
        self.assertFalse(any(row["status"] == "EXPIRED" for row in rows))
        self.assertTrue(all(row["pnl"] is None for row in rows))

    def test_expiry_requires_explicit_action_zero_cash_full_identity_valid_date_and_no_reversal_or_exercise(self):
        for change in ("missing amount","nonzero amount","nonzero price","missing expiry","invalid date",
                       "early date","exercise","assignment","reversal","conflicting trade","description only",
                       "bool quantity","fractional quantity","currency","missing currency","currency conversion",
                       "invalid fee","root","osi"):
            with self.subTest(change=change):
                evidence = self.expiration()
                broken = evidence[0]
                broker = broken["brokerage"]
                if change == "missing amount": broken.pop("amount")
                elif change == "nonzero amount": broken["amount"] = -1
                elif change == "nonzero price": broker["price"] = 1
                elif change == "missing expiry": broker["product"].pop("expiryDay")
                elif change == "invalid date": broken["transactionDate"] = "invalid"
                elif change == "early date": broken["transactionDate"] -= 3*86400000
                elif change == "exercise": broker["transactionType"] = "Option Exercised"
                elif change == "assignment": broker["transactionType"] = "Assignment"
                elif change == "reversal": broken["reversalFlag"] = True
                elif change == "conflicting trade": broker["orderAction"] = "STC"
                elif change == "description only":
                    broker["transactionType"] = "Other"
                    broker["description"] = "Option Expired"
                elif change == "bool quantity": broker["quantity"] = True
                elif change == "fractional quantity": broker["quantity"] = -.5
                elif change == "currency": broker["currency"] = "CAD"
                elif change == "missing currency": broker.pop("currency")
                elif change == "currency conversion":
                    broker["settlementCurrency"] = "USD"
                    broker["paymentCurrency"] = "CAD"
                elif change == "invalid fee": broker["fee"] = float("nan")
                elif change == "root": broker["product"]["optionRootSymbol"] = "OTHER"
                else: broken["osiKey"] = "SYN---270115C00101000"
                events, review, rows = self.parse(self.opening()+evidence)
                self.assertFalse(any(row["status"] == "EXPIRED" for row in rows))
                self.assertTrue(all(row["pnl"] is None for row in rows))

    def test_top_level_broker_expired_enum_needs_no_order_or_intraday_execution_time(self):
        evidence = self.expiration(stamp="2027-01-16T00:00:00")
        for row in evidence:
            row["transactionType"] = row["brokerage"].pop("transactionType")
            row["description"] = "SYN JAN 15 2027 OPTION CONTRACT"
        events, review, rows = self.parse(self.opening()+evidence)
        self.assertEqual((rows[0]["status"],rows[0]["pnl"]),("EXPIRED",-201.3))
        self.assertTrue(all(leg["lifecycle"] == "EXPIRATION" for leg in review))

    def test_past_contract_date_without_explicit_expiration_never_becomes_zero_value_loss(self):
        events, review, rows = self.parse(self.opening())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"],rows[0]["pnl"],rows[0]["closed"]),("OPEN",None,None))

    def test_later_date_close_cannot_bypass_expiration_evidence_but_earlier_realized_cash_is_preserved(self):
        for stamp,expected in (("2027-01-14T15:00:00",97.4),("2027-01-17T15:00:00",None)):
            with self.subTest(stamp=stamp):
                closing = AcquisitionVerticalCacheTests.rows()[2:]
                for row in closing:
                    row["transactionDate"] = int(datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("America/New_York")).timestamp()*1000)
                events, review, rows = self.parse(self.opening()+closing+self.expiration())
                realized = [row["pnl"] for row in rows if row["status"] == "CLOSED"]
                self.assertEqual(realized, [expected] if expected is not None else [])
                self.assertFalse(any(row["status"] == "EXPIRED" for row in rows))

    def test_expiration_correction_replaces_prior_quantity_and_preserves_json_cache_contract(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        evidence = self.expiration(qty=2)
        stale = deepcopy(evidence[0])
        stale["brokerage"]["quantity"] = -1
        payload = acq._build_vertical_cache(self.opening(qty=2)+[stale]+evidence)
        self.assertEqual([(row["status"],row["qty"],row["pnl"]) for row in payload["blotter"]],[("EXPIRED",2,-402.6)])
        self.assertTrue(acq._valid_vertical_cache(payload,payload["source_fingerprint"]))
        self.assertEqual(json.loads(json.dumps(payload,allow_nan=False)), payload)

    def test_after_expiry_close_cannot_shift_fifo_basis_to_a_different_opening_lot(self):
        raw = self.opening()
        second = self.opening(long_price=4)
        for row in second:
            row["transactionId"] += 10
            row["brokerage"]["orderNo"] += 10
            row["transactionDate"] += 86400000
        closing = AcquisitionVerticalCacheTests.rows()[2:]
        for row in closing:
            row["transactionDate"] = int(datetime(2027,1,17,15,tzinfo=ZoneInfo("America/New_York")).timestamp()*1000)
        events, review, rows = self.parse(raw+second+closing+self.expiration())
        self.assertFalse(any(row["status"] in {"CLOSED","EXPIRED"} for row in rows))
        self.assertTrue(all(row["pnl"] is None for row in rows))
        self.assertEqual({tuple(row["open_ids"]) for row in rows if row["opened"]}, {("1","2"),("11","12")})


class AcquisitionOptionSettlementEvidenceTests(unittest.TestCase):
    aliases = {action:action for action in ("BTO","STO","STC","BTC")}

    @staticmethod
    def settled_rows():
        rows = AcquisitionVerticalCacheTests.rows()
        for row in rows:
            row["brokerage"].pop("orderNo",None)
            row["brokerage"]["quantity"] = 1 if row["brokerage"]["transactionType"] in {"BTO","BTC"} else -1
            row["postDate"] = row["transactionDate"] + 86400000
            row["brokerage"]["settlementDate"] = row["postDate"]
        return rows

    @staticmethod
    def provisional(row,identifier):
        from copy import deepcopy
        row = deepcopy(row)
        action = row["brokerage"]["transactionType"]
        row["transactionId"] = identifier
        row["transactionDate"] -= 1000
        row["postDate"] = 0
        row["brokerage"]["settlementDate"] = 0
        row["brokerage"]["fee"] = 0
        row["transactionType"] = row["brokerage"]["transactionType"] = "Bought" if action in {"BTO","BTC"} else "Sold"
        row["description"] = "SYN OPTION " + ("OPENING" if action in {"BTO","STO"} else "CLOSING")
        return row

    def test_reciprocal_unique_posted_aliases_preserve_raw_rows_cash_and_superseded_ids(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        settled = self.settled_rows()
        pending = [self.provisional(row,100+row["transactionId"]) for row in settled]
        raw = pending + settled
        before = deepcopy(raw)
        retained, aliases = acq._settled_option_rows(raw,self.aliases)
        self.assertEqual([row["transactionId"] for row in retained],[1,2,3,4])
        self.assertEqual(aliases,{str(index):[str(index+100)] for index in range(1,5)})
        events, review = acq._vertical_history(raw)
        self.assertEqual(review,[])
        self.assertEqual([event["ids"] for event in events],[["1","2"],["3","4"]])
        self.assertEqual([event["superseded_ids"] for event in events],[["101","102"],["103","104"]])
        self.assertTrue(all(event["match"] == "TIME // INFERRED" for event in events))
        self.assertEqual(acq._vertical_blotter(events,review)[0]["pnl"],97.4)
        self.assertEqual(raw,before)

    def test_alias_matching_requires_unique_reciprocal_cardinality_and_distinct_transaction_ids(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        settled = self.settled_rows()[0]
        pending = self.provisional(settled,101)
        for rows in ([pending,settled,{**deepcopy(settled),"transactionId":11}],
                     [pending,{**deepcopy(pending),"transactionId":102},settled],
                     [{**deepcopy(pending),"transactionId":1},settled]):
            with self.subTest(ids=[row["transactionId"] for row in rows]):
                retained, aliases = acq._settled_option_rows(rows,self.aliases)
                self.assertEqual(retained,rows)
                self.assertEqual(aliases,{})

    def test_aliases_never_discard_economically_different_or_cross_contract_pending_rows(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        settled = self.settled_rows()[0]
        for target,key,value in (("tx","accountIdKey","other"),("tx","amount",-301.65),
                                 ("tx","transactionDate",settled["transactionDate"]-86400000),
                                 ("broker","quantity",2),("broker","price",3.01),
                                 ("broker","currency","CAD"),("broker","optionMultiplier",10),
                                 ("broker","orderNo",900),("product","strikePrice",110),
                                 ("product","callPut","PUT"),("product","expiryDay",22),
                                 ("product","optionRootSymbol","OTHER")):
            with self.subTest(target=target,key=key):
                pending = self.provisional(settled,101)
                obj = pending if target=="tx" else pending["brokerage"] if target=="broker" else pending["brokerage"]["product"]
                obj[key] = value
                raw = [pending,deepcopy(settled)]
                retained, aliases = acq._settled_option_rows(raw,self.aliases)
                self.assertEqual(retained,raw)
                self.assertEqual(aliases,{})

    def test_malformed_generic_or_nontrade_records_remain_visible_and_block_unsafe_lifecycle_profit(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        settled = self.settled_rows()
        for change in ({"description":"SYN OPTION"},{"description":"CANCELED SYN OPTION OPENING"},
                       {"description":"EXERCISED SYN OPTION OPENING"},{"reversalFlag":True},
                       {"postDate":True},{"amount":None}):
            with self.subTest(change=change):
                pending = self.provisional(settled[0],101)
                pending.update(change)
                rows = [pending,*deepcopy(settled)]
                retained, aliases = acq._settled_option_rows(rows,self.aliases)
                self.assertEqual(retained,rows)
                self.assertEqual(aliases,{})
                events, review = acq._vertical_history(rows)
                self.assertTrue(review)
                self.assertFalse(any(row["pnl"] is not None for row in acq._vertical_blotter(events,review)))

    def test_pending_alias_with_conflicting_osi_or_contract_context_cannot_supersede_a_settled_row(self):
        import src.acquisition_ui as acq
        for field,original,conflict in (("osiKey","SYN---270115C00100000","OTHER---270115C00100000"),
                                       ("osiSymbol","SYN---270115C00100000","SYN---270115C00105000"),
                                       ("contract_context",{"deliverable":"standard"},{"deliverable":"adjusted"})):
            with self.subTest(field=field):
                settled = self.settled_rows()[0]
                settled[field] = original
                pending = self.provisional(settled,101)
                retained,aliases = acq._settled_option_rows([pending,settled],self.aliases)
                self.assertEqual([row["transactionId"] for row in retained],[1])
                self.assertEqual(aliases,{"1":["101"]})
                pending[field] = conflict
                retained,aliases = acq._settled_option_rows([pending,settled],self.aliases)
                self.assertEqual([row["transactionId"] for row in retained],[101,1])
                self.assertEqual(aliases,{})

    def test_nontrade_or_reversal_in_any_alias_action_or_description_preserves_the_review_barrier(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        for location in ("top","brokerage"):
            for field in ("transactionType","orderAction","transactionShortDesc","description","transactionDescription"):
                for action in ("Option Assignment","Option Exercised","Option Expired","Reversal","Canceled"):
                    with self.subTest(location=location,field=field,action=action):
                        settled = self.settled_rows()
                        pending = self.provisional(settled[0],101)
                        target = pending if location=="top" else pending["brokerage"]
                        target[field] = action + (" OPENING" if field=="description" else "")
                        raw = [pending,*settled]
                        before = deepcopy(raw)
                        retained,aliases = acq._settled_option_rows(raw,self.aliases)
                        self.assertEqual(retained,raw)
                        self.assertEqual(aliases,{})
                        events,review = acq._vertical_history(raw)
                        self.assertTrue(review)
                        self.assertFalse(any(row["pnl"] is not None for row in acq._vertical_blotter(events,review)))
                        self.assertEqual(raw,before)

    def test_ordinary_contract_expires_date_descriptions_preserve_opening_and_closing_aliases(self):
        import src.acquisition_ui as acq
        settled = self.settled_rows()
        pending = [self.provisional(row,100+row["transactionId"]) for row in settled]
        for row in [*pending,*settled]:
            phase = "OPENING" if row["transactionId"] in {1,2,101,102} else "CLOSING"
            text = "CALL SYN EXPIRES 01/15/2027 " + phase
            row["description"] = text
            row["brokerage"]["transactionDescription"] = text
        retained,aliases = acq._settled_option_rows([*pending,*settled],self.aliases)
        self.assertEqual([row["transactionId"] for row in retained],[1,2,3,4])
        self.assertEqual(aliases,{str(index):[str(index+100)] for index in range(1,5)})
        events,review = acq._vertical_history([*pending,*settled])
        self.assertEqual(review,[])
        self.assertEqual(len(events),2)
        self.assertEqual(acq._vertical_blotter(events,review)[0]["pnl"],97.4)

    def test_prior_algorithm_two_report_is_rebuilt_from_existing_raw_history(self):
        import src.acquisition_ui as acq
        rows, vault = self.settled_rows(), AcquisitionVerticalCacheTests.vault()
        old = acq._build_vertical_cache(rows)
        old["algorithm_version"] = 2
        vault["vertical_cache"] = {("access","raj"):old}
        with (patch.object(acq,"_history_vault",return_value=vault),
              patch.object(acq,"_vertical_history",wraps=acq._vertical_history) as parser):
            rebuilt = acq._cached_verticals(rows,"access","raj")
            self.assertEqual(parser.call_count,1)
            self.assertEqual(rebuilt["algorithm_version"],3)
            self.assertIsNot(rebuilt,old)
            self.assertEqual(rebuilt["blotter"][0]["pnl"],97.4)


class AcquisitionOptionDetailEvidenceTests(unittest.TestCase):
    rows = staticmethod(AcquisitionOptionSettlementEvidenceTests.settled_rows)

    @staticmethod
    def response(tx,order=9001,wrapped=True):
        from copy import deepcopy
        detail = deepcopy(tx)
        detail.pop("_acquisition_option_detail",None)
        if order is not None:
            detail["brokerage"]["orderNo"] = order
        return {"TransactionDetailsResponse":detail} if wrapped else detail

    @classmethod
    def broker(cls,rows):
        from types import SimpleNamespace
        from unittest.mock import Mock
        by_id = {str(row["transactionId"]):row for row in rows}
        return SimpleNamespace(is_offline=False,get_transaction_details=Mock(
            side_effect=lambda account,identifier,**kwargs:cls.response(by_id[str(identifier)],9001 if int(identifier)<3 else 9002)))

    def test_broker_details_upgrade_actual_shared_order_without_changing_source_cash_or_fields(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        raw = self.rows()
        raw[0]["storeId"] = 7
        before = deepcopy(raw)
        client = self.broker(raw)
        hydrated,error = acq._hydrate_option_details(client,"raj",raw)
        self.assertEqual(error,"")
        self.assertEqual(raw,before)
        self.assertEqual(client.get_transaction_details.call_count,4)
        client.get_transaction_details.assert_any_call("raj","1",store_id=7)
        client.get_transaction_details.assert_any_call("raj","2",store_id=0)
        for original,verified in zip(raw,hydrated):
            self.assertEqual({k:v for k,v in verified.items() if k!="_acquisition_option_detail"},original)
            self.assertIsInstance(verified["_acquisition_option_detail"]["payload_json"],str)
            self.assertEqual(acq._option_source_digest(verified),acq._option_source_digest(original))
        events,review = acq._vertical_history(hydrated)
        self.assertEqual(review,[])
        self.assertEqual([(event["match"],event["order"]) for event in events],[("ORDER","9001"),("ORDER","9002")])
        self.assertEqual(acq._vertical_blotter(events,review)[0]["pnl"],97.4)

    def test_strict_detail_identity_action_and_economics_reject_mismatched_broker_responses(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        source = self.rows()[0]
        self.assertTrue(acq._option_detail_matches(source,self.response(source)))
        self.assertTrue(acq._option_detail_matches(source,self.response(source,wrapped=False)))
        for target,key,value in (("tx","transactionId",999),("tx","accountIdKey","other"),
                                 ("tx","transactionDate",source["transactionDate"]+1000),
                                 ("tx","amount",-301.65),("tx","reversalFlag",True),
                                 ("broker","quantity",-1),("broker","price",3.01),
                                 ("broker","optionMultiplier",10),
                                 ("broker","currency","CAD"),("broker","transactionType","STO"),
                                 ("broker","orderAction","STO"),("broker","transactionType","Option Exercised"),
                                 ("product","symbol","OTHER"),("product","callPut","PUT"),
                                 ("product","strikePrice",101),("product","expiryDay",22),
                                 ("product","optionRootSymbol","OTHER"),("product","securityType","EQ")):
            with self.subTest(target=target,key=key,value=value):
                detail = self.response(source,wrapped=False)
                obj = detail if target=="tx" else detail["brokerage"] if target=="broker" else detail["brokerage"]["product"]
                obj[key] = value
                self.assertFalse(acq._option_detail_matches(source,{"TransactionDetailsResponse":detail}))
        for field in ("transactionId","accountIdKey","transactionDate","amount"):
            detail = self.response(source,wrapped=False)
            detail.pop(field)
            self.assertFalse(acq._option_detail_matches(source,detail))
        for response in (None,[],{"TransactionDetailsResponse":[]},{}):
            self.assertFalse(acq._option_detail_matches(source,response))
        normalized_year = self.response(source,wrapped=False)
        normalized_year["brokerage"]["product"]["expiryYear"] = 27
        self.assertTrue(acq._option_detail_matches(source,normalized_year))
        self.assertNotIn("_acquisition_option_detail",source)

    def test_same_execution_can_have_generic_detail_action_or_revised_fee_without_overwriting_cash(self):
        import src.acquisition_ui as acq
        source = self.rows()[0]
        for action in (None,"Bought","BUY"):
            with self.subTest(action=action):
                detail = self.response(source,wrapped=False)
                detail["brokerage"].pop("transactionType")
                if action is not None:
                    detail["brokerage"]["transactionType"] = action
                detail["brokerage"]["fee"] = 0
                self.assertTrue(acq._option_detail_matches(source,detail))
        for action in ("Sold","SELL"):
            detail = self.response(source,wrapped=False)
            detail["brokerage"]["transactionType"] = action
            self.assertFalse(acq._option_detail_matches(source,detail))
        self.assertEqual((source["amount"],source["brokerage"]["fee"]),(-300.65,.65))

    def test_every_raw_and_detail_action_field_rejects_nontrade_or_opposite_generic_direction(self):
        import src.acquisition_ui as acq
        for side in ("raw","detail"):
            for location in ("top","brokerage"):
                for field in ("transactionType","orderAction","transactionShortDesc"):
                    for action in ("Option Assignment","Option Expired","Option Exercised","Sold"):
                        with self.subTest(side=side,location=location,field=field,action=action):
                            raw = self.rows()[0]
                            detail = self.response(raw,wrapped=False)
                            target = raw if side=="raw" else detail
                            if location=="brokerage":
                                target = target["brokerage"]
                            target[field] = action
                            self.assertFalse(acq._option_detail_matches(raw,detail))

    def test_conflicting_supplied_osi_identity_cannot_prove_an_order(self):
        import src.acquisition_ui as acq
        source = self.rows()[0]
        source["osiKey"] = "SYN---270115C00100000"
        source["brokerage"]["product"]["productId"] = {"symbol":"SYN---270115C00100000"}
        self.assertTrue(acq._option_detail_matches(source,self.response(source)))
        for target in ("osiKey","productId"):
            detail = self.response(source,wrapped=False)
            if target=="osiKey":
                detail["osiKey"] = "OTHER---270115C00100000"
            else:
                detail["brokerage"]["product"]["productId"]["symbol"] = "SYN---270115C00105000"
            self.assertFalse(acq._option_detail_matches(source,detail))

    def test_persisted_matching_proof_skips_network_after_json_reload_and_force_when_order_is_known(self):
        import src.acquisition_ui as acq
        raw = self.rows()
        client = self.broker(raw)
        hydrated,error = acq._hydrate_option_details(client,"raj",raw)
        restored = json.loads(json.dumps(hydrated))
        client.get_transaction_details.reset_mock()
        again,error = acq._hydrate_option_details(client,"raj",restored)
        self.assertEqual(error,"")
        self.assertEqual(again,restored)
        acq._hydrate_option_details(client,"raj",restored,force=True)
        client.get_transaction_details.assert_not_called()
        self.assertEqual(acq._option_detail_order(restored[0]),"9001")

    def test_partial_order_proof_preserves_time_pair_until_every_leg_is_confirmed_and_conflicts_stay_reviewed(self):
        import src.acquisition_ui as acq
        for orders,expected in (((9001,None),"TIME // INFERRED"),((9001,9001),"ORDER"),((9001,9002),None)):
            with self.subTest(orders=orders):
                rows = self.rows()[:2]
                for row,order in zip(rows,orders):
                    if order is not None:
                        row["_acquisition_option_detail"] = {"base_digest":acq._option_source_digest(row),
                            "payload_json":json.dumps(self.response(row,order))}
                events,review = acq._vertical_history(rows)
                if expected is None:
                    self.assertEqual(events,[])
                    self.assertEqual(len(review),2)
                else:
                    self.assertEqual(review,[])
                    self.assertEqual(len(events),1)
                    self.assertEqual(events[0]["match"],expected)

    def test_missing_zero_boolean_or_non_numeric_order_never_upgrades_inference_and_does_not_eagerly_retry(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import src.acquisition_ui as acq
        for order in (None,0,"0",True,"-1","bad"):
            with self.subTest(order=order):
                raw = self.rows()[:2]
                client = SimpleNamespace(is_offline=False,get_transaction_details=Mock(
                    side_effect=lambda account,identifier,**kwargs:self.response(raw[int(identifier)-1],order)))
                hydrated,error = acq._hydrate_option_details(client,"raj",raw)
                self.assertEqual(error,"")
                self.assertEqual(acq._option_detail_order(hydrated[0]),"")
                events,review = acq._vertical_history(hydrated)
                self.assertEqual(review,[])
                self.assertEqual(events[0]["match"],"TIME // INFERRED")
                client.get_transaction_details.reset_mock()
                acq._hydrate_option_details(client,"raj",hydrated)
                client.get_transaction_details.assert_not_called()
                acq._hydrate_option_details(client,"raj",hydrated,force=True)
                self.assertEqual(client.get_transaction_details.call_count,2)

    def test_source_correction_invalidates_only_changed_proof_and_merge_keeps_unchanged_evidence(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        raw = self.rows()
        hydrated,error = acq._hydrate_option_details(self.broker(raw),"raj",raw)
        merged = acq._merge(hydrated,deepcopy(raw))
        self.assertEqual(merged,hydrated)
        for target,key,value in (("tx","amount",-301.65),("tx","transactionId",51),
                                 ("tx","accountIdKey","other"),("broker","fee",.75),
                                 ("broker","quantity",2)):
            with self.subTest(target=target,key=key):
                corrected = deepcopy(hydrated[0])
                (corrected if target=="tx" else corrected["brokerage"])[key] = value
                self.assertEqual(acq._option_detail_order(corrected),"")
        corrected = deepcopy(raw)
        corrected[0]["amount"] -= 1
        merged = acq._merge(hydrated,corrected)
        self.assertNotIn("_acquisition_option_detail",merged[0])
        self.assertEqual(merged[1:],hydrated[1:])
        client = self.broker(corrected)
        updated,error = acq._hydrate_option_details(client,"raj",merged)
        self.assertEqual(error,"")
        client.get_transaction_details.assert_called_once_with("raj","1",store_id=0)
        self.assertEqual(acq._option_detail_order(updated[0]),"9001")

    def test_partial_hydration_failure_preserves_success_and_raw_cash_without_partial_false_order_pair(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from unittest.mock import Mock
        import src.acquisition_ui as acq
        raw = self.rows()
        before = deepcopy(raw)
        for bad in (ETradeError("temporary"),{"TransactionDetailsResponse":{}}):
            with self.subTest(bad=type(bad).__name__):
                client = SimpleNamespace(is_offline=False,get_transaction_details=Mock(
                    side_effect=[self.response(raw[2],9002),bad]))
                output,error = acq._hydrate_option_details(client,"raj",raw)
                self.assertTrue(error)
                self.assertEqual(raw,before)
                self.assertEqual(len(output),len(raw))
                self.assertEqual(acq._option_detail_order(output[2]),"9002")
                if isinstance(bad,dict):
                    self.assertEqual(output[3]["_acquisition_option_detail"]["rejection"],"DETAIL_MISMATCH")
                    self.assertNotIn("payload_json",output[3]["_acquisition_option_detail"])
                else:
                    self.assertNotIn("_acquisition_option_detail",output[3])
                self.assertEqual([row["amount"] for row in output],[row["amount"] for row in raw])
                events,review = acq._vertical_history(output)
                self.assertEqual(review,[])
                self.assertEqual([(event["event"],event["match"]) for event in events],
                                 [("OPENED","TIME // INFERRED"),("CLOSED","TIME // INFERRED")])

    def test_same_source_stale_no_order_proof_cannot_erase_stronger_confirmed_order(self):
        from copy import deepcopy
        import src.acquisition_ui as acq
        def proven(row,order):
            output = deepcopy(row)
            output["_acquisition_option_detail"] = {"base_digest":acq._option_source_digest(output),
                "payload_json":json.dumps(self.response(output,order))}
            return output
        source = self.rows()[0]
        known,stale = proven(source,9001),proven(source,None)
        before = deepcopy((known,stale))
        merged = acq._merge([known],[stale])[0]
        self.assertEqual(acq._option_detail_order(merged),"9001")
        self.assertEqual({key:value for key,value in merged.items() if key!="_acquisition_option_detail"},source)
        self.assertEqual((known,stale),before)
        self.assertEqual(acq._option_detail_order(acq._merge([known],[proven(source,9002)])[0]),"9002")
        corrected = deepcopy(source)
        corrected["amount"] -= 1
        fresh = proven(corrected,None)
        merged = acq._merge([known],[fresh])[0]
        self.assertEqual(merged,fresh)
        self.assertEqual(acq._option_detail_order(merged),"")
        self.assertNotEqual(acq._option_source_digest(merged),acq._option_source_digest(known))

    def test_old_detail_failure_cannot_starve_newer_proof_or_new_incoming_executions(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from unittest.mock import Mock
        import src.acquisition_ui as acq
        recent = self.rows()
        old = deepcopy(recent[0])
        old.update(transactionId=77,transactionDate=int(datetime(2020,4,1,tzinfo=ZoneInfo("America/New_York")).timestamp()*1000))
        raw = [old,*recent]
        before = deepcopy(raw)
        def response(account,identifier,**kwargs):
            if identifier=="77":
                raise ETradeError("Broker no longer exposes this detail")
            source = next(row for row in raw if str(row["transactionId"])==identifier)
            return self.response(source,9001 if int(identifier)<3 else 9002)
        client = SimpleNamespace(is_offline=False,get_transaction_details=Mock(side_effect=response))
        output,error = acq._hydrate_option_details(client,"raj",raw)
        self.assertTrue(error)
        self.assertEqual(raw,before)
        self.assertEqual([row["transactionId"] for row in output],[77,1,2,3,4])
        self.assertEqual([call.args[1] for call in client.get_transaction_details.call_args_list],["3","4","1","2","77"])
        self.assertNotIn("_acquisition_option_detail",output[0])
        self.assertEqual([acq._option_detail_order(row) for row in output[1:]],["9001","9001","9002","9002"])
        incoming = deepcopy(recent[0])
        incoming.update(transactionId=5,transactionDate=recent[-1]["transactionDate"]+86400000)
        raw.append(incoming)
        client.get_transaction_details.reset_mock()
        refreshed,error = acq._hydrate_option_details(client,"raj",[*output,incoming])
        self.assertTrue(error)
        self.assertEqual([call.args[1] for call in client.get_transaction_details.call_args_list],["5","77"])
        self.assertEqual([row["transactionId"] for row in refreshed],[77,1,2,3,4,5])
        self.assertEqual(acq._option_detail_order(refreshed[-1]),"9002")

    def test_sync_retries_failed_detail_reads_after_backoff_without_another_history_scan(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import src.acquisition_ui as acq
        raw = self.rows()
        vault = AcquisitionVerticalCacheTests.vault()
        vault["accounts"] = {("access","raj"):{"rows":raw,"covered_from":date(2026,10,1),
                            "covered_through":date(2026,11,9),"synced_at":1000}}
        client = SimpleNamespace(is_offline=False,get_transactions=Mock(side_effect=AssertionError("Warm history must not rescan")),
                                 get_transaction_details=Mock(side_effect=ETradeError("temporary")))
        with (patch.object(acq,"_history_vault",return_value=vault),patch.object(acq,"_archive_client",return_value=None),
              patch.object(acq.time,"time",return_value=1000) as clock):
            for _ in range(2):
                acq._sync_history(client,"access",[{"accountIdKey":"raj"}],now=datetime(2026,11,9,tzinfo=ZoneInfo("America/New_York")))
            self.assertEqual(client.get_transaction_details.call_count,1)
            self.assertTrue(vault["option_detail_error"][("access","raj")])
            self.assertEqual(vault["accounts"][("access","raj")]["rows"],raw)
            clock.return_value = 2801
            acq._sync_history(client,"access",[{"accountIdKey":"raj"}],now=datetime(2026,11,9,tzinfo=ZoneInfo("America/New_York")))
            self.assertEqual(client.get_transaction_details.call_count,2)
            client.get_transactions.assert_not_called()

    def test_mismatched_detail_is_persisted_as_opaque_diagnostic_without_proof_cash_or_checkpoint_changes(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from unittest.mock import Mock
        import src.acquisition_ui as acq
        raw = self.rows()
        before = deepcopy(raw)
        bad = self.response(raw[2],9002)
        bad["TransactionDetailsResponse"]["amount"] = 999999
        vault = AcquisitionVerticalCacheTests.vault()
        scope = ("access","raj")
        saved = {"rows":raw,"covered_from":date(2026,10,1),"covered_through":date(2026,11,9),"synced_at":1000}
        vault.update(accounts={scope:saved},archive_loaded={scope},archive_dirty={scope:False})
        client = SimpleNamespace(is_offline=False,get_transactions=Mock(side_effect=AssertionError("Warm source must not rescan")),
                                 get_transaction_details=Mock(return_value=bad))
        archive = SimpleNamespace(save=Mock(side_effect=lambda account,snapshot,merge:snapshot))
        with (patch.object(acq,"_history_vault",return_value=vault),patch.object(acq,"_archive_client",return_value=archive),
              patch.object(acq.ArchiveConfig,"from_runtime",return_value=None),patch.object(acq.time,"time",return_value=1000)):
            for _ in range(2):
                snapshots,errors = acq._sync_history(client,"access",[{"accountIdKey":"raj"}],
                    now=datetime(2026,11,9,tzinfo=ZoneInfo("America/New_York")))
        self.assertEqual(errors,{})
        client.get_transactions.assert_not_called()
        client.get_transaction_details.assert_called_once_with("raj","3",store_id=0)
        archive.save.assert_called_once()
        published = archive.save.call_args.args[1]
        self.assertEqual((published["covered_from"],published["covered_through"],published["synced_at"]),
                         ("2026-10-01","2026-11-09",1000))
        enriched = published["rows"][2]
        metadata = enriched["_acquisition_option_detail"]
        self.assertEqual(metadata["rejection"],"DETAIL_MISMATCH")
        self.assertEqual(json.loads(metadata["rejected_payload_json"]),bad)
        self.assertNotIn("payload_json",metadata)
        self.assertIsNone(acq._cached_option_detail(enriched))
        self.assertEqual(acq._option_detail_order(enriched),"")
        self.assertEqual(acq._option_source_digest(enriched),acq._option_source_digest(raw[2]))
        self.assertEqual(acq._find(enriched,"amount"),499.35)
        self.assertEqual(acq._build_vertical_cache(published["rows"])["blotter"][0]["pnl"],97.4)
        self.assertEqual(snapshots["raj"]["rows"],published["rows"])
        self.assertFalse(vault["archive_dirty"][scope])
        self.assertTrue(vault["option_detail_error"][scope])
        self.assertEqual(vault["option_detail_retry_at"][scope],2800)
        self.assertEqual(raw,before)

    def test_malformed_brokerage_and_boolean_execution_dates_are_ineligible_without_detail_requests(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from unittest.mock import Mock
        import src.acquisition_ui as acq
        valid = self.rows()[0]
        boolean_date = {**deepcopy(valid),"transactionDate":True}
        malformed = {**deepcopy(valid),"brokerage":[deepcopy(valid["brokerage"])]}
        rows = [None,[],{"brokerage":"invalid"},boolean_date,malformed]
        client = SimpleNamespace(is_offline=False,get_transaction_details=Mock(side_effect=AssertionError("Invalid source must not fetch")))
        for row in rows:
            self.assertFalse(acq._needs_option_detail(row))
        output,error = acq._hydrate_option_details(client,"raj",rows)
        self.assertEqual((output,error),(rows,""))
        client.get_transaction_details.assert_not_called()


class AcquisitionTransactionDetailTransportTests(unittest.TestCase):
    def test_fixed_get_details_path_quoted_account_store_and_dictionary_response(self):
        from unittest.mock import Mock
        client = object.__new__(ETradeClient)
        response = {"TransactionDetailsResponse":{"brokerage":{"orderNo":900}}}
        client._get = Mock(return_value=response)
        self.assertIs(client.get_transaction_details(" A/B +?# "," 00123 ",store_id=7),response)
        client._get.assert_called_once_with("/v1/accounts/A%2FB%20%2B%3F%23/transactions/00123",{"storeId":7})
        client._get.reset_mock()
        client.get_transaction_details("account",123)
        client._get.assert_called_once_with("/v1/accounts/account/transactions/123",{"storeId":0})
        client._get = Mock(return_value=[])
        with self.assertRaises(ETradeError):
            client.get_transaction_details("account",123)

    def test_invalid_detail_identifiers_and_store_values_never_make_transport_calls(self):
        from unittest.mock import Mock
        client = object.__new__(ETradeClient)
        client._get = Mock()
        cases = [("",123,0),(None,123,0),("account",True,0),("account",0,0),
                 ("account","000",0),("account","-1",0),("account","1.0",0),
                 ("account","../123",0),("account","123?x=1",0),
                 ("account","https://example.invalid/123",0),("account","١",0),
                 ("account",123,True),("account",123,-1),("account",123,"0"),("account",123,0.0)]
        for account,identifier,store in cases:
            with self.subTest(account=account,identifier=identifier,store=store):
                with self.assertRaises(ETradeError):
                    client.get_transaction_details(account,identifier,store_id=store)
                client._get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
