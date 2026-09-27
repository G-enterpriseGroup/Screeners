"""Offline interaction regression through the actual v7 → v10 → v9 → v2 Risk route.

Only broker responses and the unrelated sector enrichment are stubbed.
"""
from pathlib import Path
import sys

from bs4 import BeautifulSoup
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.etrade_client import ETradeClient
from src.risk_sizing import stock_position_size
from src.risk_sizing_ui_v9 import _select_true_cash
from src.risk_sizing_ui_v2 import _stock_math_check, _stock_risk_status
FIXTURE = """import streamlit as st
from src.risk_sizing_ui_v7 import render_risk_sizing
import src.risk_sizing_ui_v9 as v9
from src.ticker_autocomplete import company_name
v9.render_stockanalysis_portfolio = lambda *a, **k: None
class FixtureClient:
    def get_portfolio(self, account):
        return [dict(Product=dict(symbol=s,securityType=t),marketValue=mv,totalGain=g,totalGainPct=p,quantity=10,pricePaid=100) for s,t,mv,g,p in [
            ('SGOL','ETF',10000,-500,-5),('SGOL','ETF',5000,-250,-5),('SPY','ETF',20000,800,8),('337158EJ4','BOND',58000,-7.38,-.01),('NVDA','EQ',1125,25,2.3),('QQQ','ETF',-437,-62,-16.7)]]
    def get_quote(self,symbol):
        st.session_state.setdefault("fixture_quotes", []).append(symbol)
        return dict(symbol=symbol,companyName=("State Street SPDR S&P 500 ETF Trust" if symbol == "SPY" else company_name(symbol)),lastTrade=199.98,bid=199.95,ask=200,changeClose=-.5)
    def preview_order(self, account, payload):
        request = payload["PreviewOrderRequest"]
        order = request["Order"][0]
        action = order["Instrument"][0]["orderAction"]
        st.session_state.setdefault("fixture_order_previews", []).append(
            {"account": account, "payload": payload}
        )
        preview_id = 7001 if action == "BUY" else 7002
        return {
            "PreviewOrderResponse": {
                "PreviewIds": [{"previewId": preview_id}],
                "totalOrderValue": 2000.0,
                "estimatedCommission": 0.0,
            }
        }
    def place_order(self, account, payload):
        request = payload["PlaceOrderRequest"]
        order = request["Order"][0]
        action = order["Instrument"][0]["orderAction"]
        st.session_state.setdefault("fixture_order_places", []).append(
            {"account": account, "payload": payload}
        )
        return {
            "PlaceOrderResponse": {
                "OrderIds": [{"orderId": 9001 if action == "BUY" else 9002}]
            }
        }
    def list_orders(self, account, *, status=None, symbol=None, count=100):
        if st.session_state.get("fixture_open_order"):
            return {
                "OrdersResponse": {
                    "Order": [{
                        "orderId": 9100,
                        "OrderDetail": [{
                            "status": "OPEN",
                            "priceType": "STOP",
                            "stopPrice": 190.0,
                            "limitPrice": 0.0,
                            "orderTerm": "GOOD_UNTIL_CANCEL",
                            "placedTime": 1234567890,
                            "Instrument": [{
                                "Product": {"symbol": "SPY", "securityType": "EQ"},
                                "orderAction": "SELL",
                                "orderedQuantity": 10,
                                "filledQuantity": 0,
                            }],
                        }],
                    }]
                }
            }
        partial = bool(st.session_state.get("fixture_partial_fill"))
        filled = 4 if partial else 10
        return {
            "OrdersResponse": {
                "Order": [{
                    "orderId": 9001,
                    "OrderDetail": [{
                        "status": "INDIVIDUAL_FILLS" if partial else "EXECUTED",
                        "Instrument": [{
                            "orderedQuantity": 10,
                            "filledQuantity": filled,
                            "averageExecutionPrice": 199.75,
                        }],
                        "Events": {
                            "Event": [{"name": "ORDER_PARTIAL_FILL" if partial else "ORDER_EXECUTED"}]
                        },
                    }],
                }]
            }
        }
    def cancel_order(self, account, order_id):
        st.session_state.setdefault("fixture_order_cancels", []).append(
            {"account": account, "order_id": int(order_id)}
        )
        return {
            "CancelOrderResponse": {
                "orderId": int(order_id),
                "messages": {
                    "Message": {
                        "code": 5011,
                        "type": "WARNING",
                        "description": "cancel request processing",
                    }
                },
            }
        }
    def lookup(self,*a,**k): return {}
st.session_state['etrade_accounts']=[
    {'accountIdKey':'fixture','accountId':'10005474','accountName':'Raj Singh'},
    {'accountIdKey':'otherkey','accountId':'10001234','accountName':'Secondary Account'},
]
def fixture_balance(client, account, refresh=False):
    st.session_state.setdefault("fixture_balance_refreshes", []).append(bool(refresh))
    return {
        'Computed':{
            'cashAvailableForInvestment':2000,
            'cashBalance':9000,
            'netCash':2000,
            'cashBuyingPower':100000,
            'marginBuyingPower':25000,
        }
    }

def fixture_account_picker(key):
    accounts = st.session_state['etrade_accounts']
    selected = st.selectbox(
        "E*TRADE Account",
        range(len(accounts)),
        index=0,
        format_func=lambda index: accounts[index]['accountName'],
        key=key,
    )
    return accounts[selected]

render_risk_sizing(
    FixtureClient(),
    account_picker=fixture_account_picker,
    refresh_accounts=lambda _:None,
    account_balance=fixture_balance,
    balance_snapshot=lambda p:(100000,100000,98000),
    touch_session=lambda:None,
)
"""

LIQUID_LIMIT_FIXTURE = FIXTURE.replace(
    "st.session_state['etrade_accounts']=",
    "st.session_state['risk_liquid_balance']=1000.0\n"
    "st.session_state['risk_capital_source']='USE LIQUID BALANCE ENTERED'\n"
    "st.session_state['etrade_accounts']=",
)

TACTICAL_LIMIT_FIXTURE = FIXTURE.replace(
    "st.session_state['etrade_accounts']=",
    "st.session_state['risk_capital_source']='USE TACTICAL ROOM'\n"
    "st.session_state['etrade_accounts']=",
)

FILLED_ORDER_FIXTURE = FIXTURE.replace(
    "st.session_state['etrade_accounts']=",
    "st.session_state['_risk_live_entry_order']={"
    "'account_key':'fixture','account_label':'Raj Singh ••••5474',"
    "'symbol':'SPY','quantity':10,'entry_price':200.0,'stop_price':190.0,"
    "'order_id':9001,'placed_at':1.0}\n"
    "st.session_state['etrade_accounts']=",
)

READY_STOP_FIXTURE = FILLED_ORDER_FIXTURE.replace(
    "st.session_state['etrade_accounts']=",
    "st.session_state['_risk_stop_watch_state_v1']={"
    "'revision':3,'updated_at':3.0,'portfolio_saved_at':3.0,'rows':["
    "{'entry_order_id':9001,'account_key':'fixture','account_label':'Raj Singh ••••5474',"
    "'symbol':'SPY','quantity':10,'entry_price':200.0,'stop_price':190.0,'entry_placed_at':1.0,"
    "'status':'READY_TO_SEND','filled':10.0,'ordered':10.0,'average_price':199.75,"
    "'last_checked_at':2.0,'last_error':'','stop_order_id':None,'stop_sent_at':0.0,'events':[]}]}\n"
    "st.session_state['etrade_accounts']=",
)

PARTIAL_ORDER_FIXTURE = FILLED_ORDER_FIXTURE.replace(
    "st.session_state['etrade_accounts']=",
    "st.session_state['fixture_partial_fill']=True\n"
    "st.session_state['_risk_stop_watch_state_v1']={"
    "'revision':2,'updated_at':2.0,'portfolio_saved_at':2.0,'rows':["
    "{'entry_order_id':9001,'account_key':'fixture','account_label':'Raj Singh ••••5474',"
    "'symbol':'SPY','quantity':10,'entry_price':200.0,'stop_price':190.0,'entry_placed_at':1.0,"
    "'status':'PARTIAL_FILL','filled':4.0,'ordered':10.0,'average_price':199.75,"
    "'last_checked_at':2.0,'last_error':'','stop_order_id':None,'stop_sent_at':0.0,'events':[]}]}\n"
    "st.session_state['etrade_accounts']=",
)

PENDING_ORDER_FIXTURE = FIXTURE.replace(
    "st.session_state['etrade_accounts']=",
    "st.session_state['fixture_open_order']=True\n"
    "st.session_state['etrade_accounts']=",
)

STALE_LIQUID_FIXTURE = FIXTURE.replace(
    "st.session_state['etrade_accounts']=",
    "st.session_state['risk_liquid_balance']=9999.0\n"
    "st.session_state['etrade_accounts']=",
)


FALLBACK_FIXTURE = """import streamlit as st
from src.etrade_client import ETradeError
from src.risk_sizing_ui_v7 import render_risk_sizing
import src.risk_sizing_ui_v9 as v9
import src.risk_sizing_ui_v10 as v10
v9.render_stockanalysis_portfolio = lambda *a, **k: None

def fake_yahoo(symbol):
    st.session_state.setdefault("fixture_yahoo_quotes", []).append(symbol)
    return dict(
        symbol=symbol,
        companyName=f"{symbol} Yahoo Fixture",
        lastPrice=321.10,
        bid=321.05,
        ask=321.25,
        changeClose=1.25,
        _risk_quote_source="YAHOO FINANCE",
        _risk_quote_ask_proxy=False,
    )

v10._yfinance_quote_payload = fake_yahoo

class FailingQuoteClient:
    def get_portfolio(self, account):
        return [dict(Product=dict(symbol='SPY',securityType='ETF'),marketValue=10000,totalGain=500,totalGainPct=5,quantity=10,pricePaid=100)]
    def get_quote(self, symbol):
        st.session_state.setdefault("fixture_etrade_attempts", []).append(symbol)
        raise ETradeError("synthetic live quote failure")
    def lookup(self,*a,**k): return {}

st.session_state['etrade_accounts']=[{'accountIdKey':'fallback','accountName':'Fallback test'}]
render_risk_sizing(
    FailingQuoteClient(),
    account_picker=lambda _:st.session_state['etrade_accounts'][0],
    refresh_accounts=lambda _:None,
    account_balance=lambda *a,**k:{'Computed':{'cashBalance':2000}},
    balance_snapshot=lambda p:(100000,2000,98000),
    touch_session=lambda:None,
)
"""

DISCONNECTED_FIXTURE = """import streamlit as st
from src.risk_sizing_ui_v7 import render_risk_sizing
import src.risk_sizing_ui_v10 as v10

def fake_yahoo(symbol):
    st.session_state.setdefault("disconnected_yahoo_quotes", []).append(symbol)
    return dict(
        symbol=symbol,
        companyName=f"{symbol} Yahoo Fixture",
        lastPrice=410.10,
        bid=410.05,
        ask=410.20,
        changeClose=-0.75,
        _risk_quote_source="YAHOO FINANCE",
        _risk_quote_ask_proxy=False,
    )

v10._yfinance_quote_payload = fake_yahoo

def unexpected(*args, **kwargs):
    raise AssertionError("E*TRADE account callbacks must not run without a client")

render_risk_sizing(
    None,
    account_picker=unexpected,
    refresh_accounts=unexpected,
    account_balance=unexpected,
    balance_snapshot=unexpected,
    touch_session=lambda:None,
)
"""


PERSISTED_BOOK_FIXTURE = """import streamlit as st
from src.risk_sizing_ui_v7 import render_risk_sizing
import src.risk_sizing_ui_v2 as v2
import src.risk_sizing_ui_v9 as v9
import src.risk_sizing_ui_v10 as v10

v9.render_stockanalysis_portfolio = lambda *a, **k: None
v2._risk_book_state_component = lambda *a, **k: None

def fake_yahoo(symbol):
    return dict(
        symbol=symbol,
        companyName=f"{symbol} Yahoo Fixture",
        lastPrice=500.00,
        bid=499.90,
        ask=500.10,
        changeClose=2.00,
        _risk_quote_source="YAHOO FINANCE",
        _risk_quote_ask_proxy=False,
    )

v10._yfinance_quote_payload = fake_yahoo
st.session_state["_risk_book_snapshot_v1"] = {
    "revision": 7,
    "saved_at": 1000.0,
    "portfolio_saved_at": 1000.0,
    "account_key": "cached-risk-account",
    "account_name": "Last E*TRADE Session",
    "account_total": 100000.0,
    "cash_available": 5000.0,
    "rows": [
        {"Symbol":"SPY","Type":"ETF","CUSIP":"","Market Value":20000.0,"Gain/Loss":800.0,"Gain/Loss %":8.0},
        {"Symbol":"QQQ","Type":"ETF","CUSIP":"","Market Value":10000.0,"Gain/Loss":-500.0,"Gain/Loss %":-5.0},
    ],
    "settings": {
        "risk_gain_threshold": 6.0,
        "risk_tactical_sleeve_pct": 20.0,
        "risk_full_position_pct": 2.0,
        "risk_ticker": "SPY",
        "risk_trade_structure": "STOCK / ETF",
        "risk_size_multiplier": 1.0,
        "risk_entry_price": 490.0,
        "risk_stop_price": 465.5,
        "_risk_entry_seed_symbol": "SPY",
    },
}

def unexpected(*args, **kwargs):
    raise AssertionError("E*TRADE account callbacks must not run when Risk Book memory is available")

render_risk_sizing(
    None,
    account_picker=unexpected,
    refresh_accounts=unexpected,
    account_balance=unexpected,
    balance_snapshot=unexpected,
    touch_session=lambda:None,
)
"""


PROCESSING_BOOK_FIXTURE = """import streamlit as st
from src.etrade_client import ETradeError
from src.risk_sizing_ui_v7 import render_risk_sizing
import src.risk_sizing_ui_v2 as v2
import src.risk_sizing_ui_v9 as v9
import src.risk_sizing_ui_v10 as v10

v9.render_stockanalysis_portfolio = lambda *a, **k: None
v2._risk_book_state_component = lambda *a, **k: None

def fake_yahoo(symbol):
    return dict(
        symbol=symbol,
        companyName=f"{symbol} Yahoo Fixture",
        lastPrice=501.00,
        bid=500.90,
        ask=501.10,
        changeClose=1.00,
        _risk_quote_source="YAHOO FINANCE",
        _risk_quote_ask_proxy=False,
    )

v10._yfinance_quote_payload = fake_yahoo
st.session_state["_risk_book_snapshot_v1"] = {
    "revision": 8,
    "saved_at": 2000.0,
    "portfolio_saved_at": 2000.0,
    "account_key": "processing-risk-account",
    "account_name": "Processing fallback account",
    "account_total": 125000.0,
    "cash_available": 15000.0,
    "rows": [
        {"Symbol":"SPY","Type":"ETF","CUSIP":"","Market Value":30000.0,"Gain/Loss":1200.0,"Gain/Loss %":4.2},
        {"Symbol":"QQQ","Type":"ETF","CUSIP":"","Market Value":20000.0,"Gain/Loss":-300.0,"Gain/Loss %":-1.5},
    ],
    "settings": {
        "risk_gain_threshold": 5.0,
        "risk_tactical_sleeve_pct": 15.0,
        "risk_full_position_pct": 1.5,
        "risk_ticker": "SPY",
        "risk_trade_structure": "STOCK / ETF",
        "risk_size_multiplier": 1.0,
        "risk_entry_price": 500.0,
        "risk_stop_price": 475.0,
        "_risk_entry_seed_symbol": "SPY",
    },
}

class ProcessingClient:
    def get_quote(self, symbol):
        raise ETradeError("quote still processing")

def refresh_accounts(_client):
    st.session_state["processing_refresh_attempted"] = True
    raise ETradeError("E*TRADE account data is still processing")

def unexpected(*args, **kwargs):
    raise AssertionError("Account-dependent callbacks must not run after account refresh fails")

render_risk_sizing(
    ProcessingClient(),
    account_picker=unexpected,
    refresh_accounts=refresh_accounts,
    account_balance=unexpected,
    balance_snapshot=unexpected,
    touch_session=lambda:None,
)
"""



def main():
    # Verify cancel transport without OAuth/network access.
    cancel_calls = []
    cancel_client = object.__new__(ETradeClient)
    cancel_client._put = lambda path, payload: cancel_calls.append((path, payload)) or {"ok": True}
    assert cancel_client.cancel_order("fixture-key", 9100) == {"ok": True}
    assert cancel_calls == [
        (
            "/v1/accounts/fixture-key/orders/cancel",
            {"CancelOrderRequest": {"orderId": 9100}},
        )
    ]

    source = (ROOT / "src" / "risk_sizing_ui_v10.py").read_text(encoding="utf-8")
    v9_source = (ROOT / "src" / "risk_sizing_ui_v9.py").read_text(encoding="utf-8")
    v2_source = (ROOT / "src" / "risk_sizing_ui_v2.py").read_text(encoding="utf-8")
    assert "@st.fragment\ndef render_risk_sizing" in source
    assert "st.rerun()" not in source
    assert "st.rerun" not in v9_source
    assert "st.rerun" not in v2_source
    assert "The quote loads automatically from live E*TRADE first" in v2_source
    assert "Margin buying power is never used as cash." in v2_source
    assert 'st.segmented_control(' not in v2_source
    assert 'st.toggle(' in v2_source
    assert '"USE LIQUID BALANCE ENTERED"' in v2_source
    assert '"USE TACTICAL ROOM"' in v2_source
    assert '"OVERUSED RISK" if overused else "UNUSED RISK"' in v2_source
    assert 'f"{risk_state_label} // TARGET STOP {target_stop_text}"' in v2_source
    assert 'target_share_count = sized["capital_limited_shares"]' in v2_source
    assert 'math.ceil((candidate - 1e-9) * 100.0) / 100.0' in v2_source
    assert 'max_value=max_long_stop' in v2_source
    assert 'STOP SAFETY // Stop Loss must remain below Entry Price' in v2_source
    assert 'STOP SAFETY // INVALID STOP' in v2_source
    assert 'Sizing is blocked until the stop is valid.' in v2_source
    assert 'MATH CHECK: {status}' in v2_source
    assert 'def _render_target_stop_copy(target_stop: float)' in v2_source
    assert 'components.html(' in v2_source
    assert 'navigator.clipboard.writeText(value)' in v2_source
    assert 'height=24' in v2_source and 'width=24' in v2_source
    assert '_TARGET_STOP_COPY_SCRIPT' not in v9_source
    assert 'rs9-copy-target' not in v9_source
    assert 'key="risk_stop_distance_pct"' in v9_source
    assert '"▼ % BELOW ENTRY"' in v9_source
    assert 'step=0.25' in v9_source
    assert 'on_change=_sync_stop_from_distance_pct' in v9_source
    assert '.st-key-risk_stop_distance_pct input' in v9_source
    assert 'risk-v9-stop-pct' not in v9_source
    assert 'key="risk_book_sort"' in v2_source
    assert 'key="risk_book_export_csv"' in v2_source
    assert '"CHECK FULL FILL + SEND PROTECTIVE STOP"' not in source
    assert '"REVIEW + SEND READY PROTECTIVE STOP"' in source
    assert 'def maybe_auto_watch_risk_entries(' in source
    assert '6. PROTECTION WATCH LOG' in source
    assert '5. PENDING / OPEN E*TRADE ORDERS' in source
    assert 'client.cancel_order(account_key, order_id)' in source
    assert 'E*TRADE\'S PUBLIC API DOES NOT EXPOSE BROKER-SAVED DRAFT ORDERS' in source
    assert '"CHECK ENTRY FILL IN E*TRADE"' not in source
    assert '"REVIEW PROTECTIVE STOP WITH E*TRADE"' not in source

    # CSS-only style payloads must use st.html so Streamlit routes them outside
    # the visible flex stack instead of reserving empty rows above Risk content.
    assert 'def _render_css_v10()' in source and '    st.html(\n        """\n        <style>' in source
    assert 'def _render_css()' in v9_source and '    st.html(\n        """\n        <style>' in v9_source
    assert 'def _render_tooltip_css()' in v2_source and '    st.html(\n        """\n        <style>' in v2_source

    # Browser-persistence components stay functional but must be removed from
    # normal document flow so their 0px iframes cannot create vertical gaps.
    for shell in (
        "risk_book_state_reader_shell",
        "risk_book_state_writer_shell",
        "risk_intent_state_reader_shell",
        "risk_intent_state_writer_shell",
    ):
        assert shell in v2_source
    assert '[data-testid="stLayoutWrapper"]:has(> .st-key-risk_intent_state_reader_shell)' in v2_source
    assert "position:absolute !important;" in v2_source

    app = AppTest.from_string(FIXTURE, default_timeout=30).run()
    def clean():
        assert not app.exception, [e.message for e in app.exception]
    def metric(label):
        from bs4 import BeautifulSoup
        for item in app.get("html"):
            dom = BeautifulSoup(item.value, "html.parser")
            name = dom.select_one(".rs9-label")
            if name and name.text == label:
                return dom.select_one(".rs9-value").text
        raise AssertionError(label)
    clean()
    live_headers = [
        BeautifulSoup(item.value, "html.parser").get_text(" ", strip=True)
        for item in app.get("html")
        if "risk-v9-section" in item.value
    ]
    assert sum(text == "3. PICK E*TRADE ACCOUNT" for text in live_headers) == 1
    assert sum(text == "4. REVIEW + SEND ORDER" for text in live_headers) == 1
    assert sum(text == "5. PENDING / OPEN E*TRADE ORDERS" for text in live_headers) == 1
    assert sum(text == "6. PROTECTION WATCH LOG" for text in live_headers) == 1
    assert app.selectbox(key="risk_sizing_account").value == 0
    assert app.selectbox(key="risk_live_order_account_key").value == "fixture"

    # Main E*TRADE Account -> Part 3 Order Account.
    app.selectbox(key="risk_sizing_account").select(1).run()
    clean()
    assert app.selectbox(key="risk_sizing_account").value == 1
    assert app.selectbox(key="risk_live_order_account_key").value == "otherkey"

    # Part 3 Order Account -> main E*TRADE Account.
    app.selectbox(key="risk_live_order_account_key").select("fixture").run()
    clean()
    assert app.selectbox(key="risk_live_order_account_key").value == "fixture"
    assert app.selectbox(key="risk_sizing_account").value == 0

    # Part 4: sending the BUY arms the persistent protection watcher, but
    # still places only the BUY LIMIT during that user action.
    order_app = AppTest.from_string(FIXTURE, default_timeout=30).run()
    assert not order_app.exception, [e.message for e in order_app.exception]
    order_app.button(key="risk_live_preview_entry").click().run()
    assert not order_app.exception, [e.message for e in order_app.exception]
    order_app.checkbox(key="risk_live_entry_confirm").check().run()
    order_app.button(key="risk_live_send_entry").click().run()
    assert not order_app.exception, [e.message for e in order_app.exception]
    assert order_app.session_state["_risk_live_entry_order"]["order_id"] == 9001
    assert len(order_app.session_state["fixture_order_places"]) == 1
    first_place = order_app.session_state["fixture_order_places"][0]
    first_order = first_place["payload"]["PlaceOrderRequest"]["Order"][0]
    assert first_place["account"] == "fixture"
    assert first_order["priceType"] == "LIMIT"
    assert first_order["Instrument"][0]["orderAction"] == "BUY"
    assert first_order["Instrument"][0]["quantity"] == 10
    watch_state = order_app.session_state["_risk_stop_watch_state_v1"]
    assert watch_state["rows"][0]["entry_order_id"] == 9001
    assert watch_state["rows"][0]["status"] == "ARMED"

    # A previously auto-detected full fill exposes the exact stop as READY TO
    # SEND. The user's click contemporaneously rechecks the fill, previews, and
    # places the GTC SELL STOP.
    filled_stop_app = AppTest.from_string(READY_STOP_FIXTURE, default_timeout=30).run()
    assert not filled_stop_app.exception, [e.message for e in filled_stop_app.exception]
    filled_stop_app.button(key="risk_live_send_ready_stop").click().run()
    assert not filled_stop_app.exception, [e.message for e in filled_stop_app.exception]
    assert filled_stop_app.session_state["_risk_live_stop_order"]["order_id"] == 9002
    assert len(filled_stop_app.session_state["fixture_order_places"]) == 1
    stop_place = filled_stop_app.session_state["fixture_order_places"][0]
    stop_order = stop_place["payload"]["PlaceOrderRequest"]["Order"][0]
    assert stop_place["account"] == "fixture"
    assert stop_order["priceType"] == "STOP"
    assert stop_order["stopPrice"] == 190.0
    assert stop_order["orderTerm"] == "GOOD_UNTIL_CANCEL"
    assert stop_order["Instrument"][0]["orderAction"] == "SELL"
    assert stop_order["Instrument"][0]["quantity"] == 10
    assert filled_stop_app.session_state["_risk_stop_watch_state_v1"]["rows"][0]["status"] == "STOP_SENT"
    assert filled_stop_app.session_state["_risk_stop_watch_state_v1"]["rows"][0]["stop_order_id"] == 9002

    # Partial fills stay auto-tracked and must not expose/send the full planned stop.
    partial_order_app = AppTest.from_string(PARTIAL_ORDER_FIXTURE, default_timeout=30).run()
    assert not partial_order_app.exception, [e.message for e in partial_order_app.exception]
    assert partial_order_app.session_state["_risk_stop_watch_state_v1"]["rows"][0]["status"] == "PARTIAL_FILL"
    assert "risk_live_send_ready_stop" not in [button.key for button in partial_order_app.button]
    assert "_risk_live_stop_order" not in partial_order_app.session_state
    assert "fixture_order_places" not in partial_order_app.session_state

    # Part 5 must list live OPEN orders and require a deliberate second click
    # before calling the broker cancellation endpoint.
    pending_order_app = AppTest.from_string(PENDING_ORDER_FIXTURE, default_timeout=30).run()
    assert not pending_order_app.exception, [e.message for e in pending_order_app.exception]
    pending_cache = pending_order_app.session_state["_risk_live_pending_orders"]
    assert pending_cache["account_key"] == "fixture"
    assert len(pending_cache["rows"]) == 1
    assert pending_cache["rows"][0]["order_id"] == 9100
    assert pending_cache["rows"][0]["status"] == "OPEN"
    assert "fixture_order_cancels" not in pending_order_app.session_state

    pending_order_app.button(key="risk_live_cancel_order_fixture_9100").click().run()
    assert not pending_order_app.exception, [e.message for e in pending_order_app.exception]
    assert pending_order_app.session_state["_risk_live_cancel_confirm_order"] == "9100"
    assert "fixture_order_cancels" not in pending_order_app.session_state

    pending_order_app.button(key="risk_live_confirm_cancel_fixture_9100").click().run()
    assert not pending_order_app.exception, [e.message for e in pending_order_app.exception]
    assert pending_order_app.session_state["fixture_order_cancels"] == [
        {"account": "fixture", "order_id": 9100}
    ]
    assert pending_order_app.session_state["_risk_live_pending_orders"]["rows"][0]["status"] == "CANCEL_REQUESTED"
    assert "_risk_live_cancel_confirm_order" not in pending_order_app.session_state

    margin_payload = {
        "Computed": {
            "cashAvailableForInvestment": 100000.00,
            "cashBalance": 9876.54,
            "netCash": 9876.54,
            "cashBuyingPower": 100000.00,
            "marginBuyingPower": 50000.00,
            "marginBalance": -15000.00,
        }
    }
    selected_cash, selected_source, cash_fields = _select_true_cash(margin_payload)
    assert selected_cash == 100000.00
    assert selected_source == "cashAvailableForInvestment"
    assert cash_fields["cashAvailableForInvestment"] == 100000.00
    assert cash_fields["marginBuyingPower"] == 50000.00
    zero_cash, zero_source, _ = _select_true_cash(
        {"Computed": {"cashAvailableForInvestment": 0.0, "cashBalance": 9000.0, "marginBuyingPower": 40000.0}}
    )
    assert zero_cash == 0.0
    assert zero_source == "cashAvailableForInvestment"
    fallback_cash, fallback_source, _ = _select_true_cash(
        {"Computed": {"netCash": 4321.0, "marginBuyingPower": 50000.0}}
    )
    assert fallback_cash == 4321.0
    assert fallback_source == "netCash"

    assert len(app.checkbox) == 6
    assert app.number_input(key="risk_entry_price").value == 200.0
    assert app.number_input(key="risk_stop_price").value == 190.0
    assert app.number_input(key="risk_stop_distance_pct").value == 5.0
    html_values = [item.value for item in app.get("html")]
    assert any("risk-v10-company-box" in value and "State Street SPDR S&amp;P 500 ETF Trust" in value for value in html_values)
    saved = app.session_state["_risk_book_snapshot_v1"]
    assert saved["account_key"] == "fixture"
    assert len(saved["rows"]) == 6
    assert saved["account_total"] == 100000.0
    assert metric("CURRENT TACTICAL") == "$16,562.00"
    assert metric("MAX SHARES") == "10"
    assert app.number_input(key="risk_liquid_balance").value == 2000.0
    assert app.session_state["_risk_true_cash_source"] == "cashAvailableForInvestment"
    assert app.session_state["_risk_true_cash_fields"]["cashAvailableForInvestment"] == 2000.0
    assert app.session_state["_risk_true_cash_fields"]["marginBuyingPower"] == 25000.0
    assert app.session_state["fixture_balance_refreshes"]
    assert all(app.session_state["fixture_balance_refreshes"])
    assert len(app.toggle) == 1
    assert app.toggle(key="risk_capital_source_tactical").value is False
    capital_switch_html = "\n".join(item.value for item in app.get("html"))
    assert "Capital Source // USING" in capital_switch_html
    assert "$2,000.00" in capital_switch_html
    assert "Liquid Bal." in capital_switch_html
    assert "Tact Room." in capital_switch_html
    assert app.selectbox(key="risk_book_sort").value == "DEFAULT"
    assert app.selectbox(key="risk_book_sort_direction").value == "DESC"
    app.selectbox(key="risk_book_sort").select("VALUE").run()
    clean()
    assert app.selectbox(key="risk_book_sort").value == "VALUE"
    app.selectbox(key="risk_book_sort_direction").select("ASC").run()
    clean()
    assert app.selectbox(key="risk_book_sort_direction").value == "ASC"
    app.selectbox(key="risk_book_sort").select("DEFAULT").run()
    clean()
    assert saved["settings"]["risk_liquid_balance"] == 2000.0
    assert saved["settings"]["risk_capital_source"] == "USE LIQUID BALANCE ENTERED"
    app.checkbox[0].check().run()
    clean()
    assert app.checkbox[0].value and app.checkbox[1].value
    assert metric("CURRENT TACTICAL") == "$1,562.00"
    assert metric("LONG-TERM / STRUCTURAL") == "$93,000.00"
    assert metric("TACTICAL ROOM") == "$13,438.00"
    assert metric("MAX DOLLAR RISK") == "$225.00"
    assert metric("MAX SHARES") == "10"
    risk_status_dom = next(
        BeautifulSoup(item.value, "html.parser")
        for item in app.get("html")
        if (
            (dom := BeautifulSoup(item.value, "html.parser")).select_one(".rs9-label")
            and dom.select_one(".rs9-label").text.startswith("UNUSED RISK // TARGET STOP")
        )
    )
    assert risk_status_dom.select_one(".rs9-label").text == "UNUSED RISK // TARGET STOP $177.50"
    assert risk_status_dom.select_one(".rs9-value").text == "$125.00 (55.56%)"
    risk_tip = risk_status_dom.select_one(".rs9-tip")
    assert risk_tip is not None
    assert "MATH CHECK: PASS" in risk_tip.text
    assert "Max shares: min(" in risk_tip.text
    assert "CHECK SCOPE: arithmetic only" in risk_tip.text
    app.checkbox[1].uncheck().run()
    clean()
    assert not app.checkbox[0].value and not app.checkbox[1].value
    assert metric("CURRENT TACTICAL") == "$16,562.00"
    app.number_input(key="risk_stop_price").set_value(180.0).run()
    clean()
    assert metric("MAX SHARES") == "10"
    assert app.number_input(key="risk_stop_distance_pct").value == 10.0
    app.number_input(key="risk_stop_distance_pct").set_value(7.5).run()
    clean()
    assert app.number_input(key="risk_stop_price").value == 185.0
    assert app.number_input(key="risk_stop_distance_pct").value == 7.5
    assert metric("MAX SHARES") == "10"
    assert app.session_state["fixture_quotes"] == ["SPY"]
    ticker = app.text_input(key="risk_ticker")
    assert ticker.value == "SPY"
    ticker.set_value("qqq").run()
    clean()
    assert app.text_input(key="risk_ticker").value == "QQQ"
    assert app.session_state["fixture_quotes"] == ["SPY", "QQQ"]
    assert app.number_input(key="risk_entry_price").value == 200.0
    assert app.number_input(key="risk_stop_price").value == 190.0
    assert app.number_input(key="risk_stop_distance_pct").value == 5.0
    assert not any('risk-v9-stop-pct' in x.value for x in app.get("html"))
    assert not any(b.key == "risk_pull_quote" for b in app.button)
    app.selectbox(key="risk_trade_structure").select("DEFINED-RISK OPTION SPREAD").run()
    clean()
    assert metric("MAX SPREADS") == "0"
    app.number_input(key="risk_max_loss_spread").set_value(100.0).run()
    clean()
    assert metric("MAX SPREADS") == "2"
    assert metric("ACTUAL MAX RISK") == "$200.00"

    risk_only = stock_position_size(771.32, 770.00, 66.73)
    liquid_limited = stock_position_size(771.32, 770.00, 66.73, capital_limit=5000.00)
    room_limited = stock_position_size(771.32, 770.00, 66.73, capital_limit=1026.30)
    assert risk_only["shares"] == 50
    assert liquid_limited["shares"] == 6
    assert liquid_limited["notional"] == 4627.92
    assert room_limited["shares"] == 1
    assert room_limited["notional"] == 771.32

    unused_label, unused_amount, unused_pct, unused_target_stop = _stock_risk_status(
        200.0, 10, 225.0, 100.0
    )
    assert unused_label == "UNUSED RISK"
    assert unused_amount == 125.0
    assert round(unused_pct, 2) == 55.56
    assert unused_target_stop == 177.5

    overused_label, overused_amount, overused_pct, overused_target_stop = _stock_risk_status(
        200.0, 10, 225.0, 250.0
    )
    assert overused_label == "OVERUSED RISK"
    assert overused_amount == 25.0
    assert round(overused_pct, 2) == 11.11
    assert overused_target_stop == 177.5

    # Regression for the live feedback loop Raj reported. With a $2,078.15
    # capital cap, two shares are fundable at a $771.35 entry. The exact target
    # stop is $704.575; displaying $704.57 would overuse the $133.55 budget by
    # one cent and make MAX SHARES fall to one. The safe target must be $704.58
    # and must remain stable after it is entered.
    stable_entry = 771.35
    stable_budget = 133.55
    stable_capital = 2078.15
    stable_initial = stock_position_size(
        stable_entry, 732.78, stable_budget, capital_limit=stable_capital
    )
    assert stable_initial["shares"] == 2
    assert stable_initial["capital_limited_shares"] == 2
    _, _, _, stable_target = _stock_risk_status(
        stable_entry,
        stable_initial["capital_limited_shares"],
        stable_budget,
        stable_initial["actual_risk"],
    )
    assert stable_target == 704.58
    stable_retargeted = stock_position_size(
        stable_entry, stable_target, stable_budget, capital_limit=stable_capital
    )
    assert stable_retargeted["shares"] == 2
    assert stable_retargeted["actual_risk"] <= stable_budget
    _, _, _, stable_target_again = _stock_risk_status(
        stable_entry,
        stable_retargeted["capital_limited_shares"],
        stable_budget,
        stable_retargeted["actual_risk"],
    )
    assert stable_target_again == stable_target
    stable_check, stable_check_text = _stock_math_check(
        stable_entry,
        stable_target,
        stable_budget,
        stable_capital,
        stable_retargeted,
        stable_retargeted["capital_limited_shares"],
        stable_target,
    )
    assert stable_check
    assert "MATH CHECK: PASS" in stable_check_text
    assert "Target stop: 2 x ($771.35 - $704.58) = $133.54 modeled risk <= $133.55 budget" in stable_check_text

    # Long stock safety: even when AppTest attempts an out-of-range Stop Loss,
    # the rendered control must remain strictly below Entry Price.
    stop_guard = AppTest.from_string(FIXTURE, default_timeout=30).run()
    assert not stop_guard.exception, [e.message for e in stop_guard.exception]
    stop_guard.number_input(key="risk_stop_price").set_value(250.0).run()
    assert not stop_guard.exception, [e.message for e in stop_guard.exception]
    guarded_stop = float(stop_guard.number_input(key="risk_stop_price").value)
    assert guarded_stop < 200.0
    assert guarded_stop <= 199.99

    liquid_app = AppTest.from_string(LIQUID_LIMIT_FIXTURE, default_timeout=30).run()
    assert not liquid_app.exception, [e.message for e in liquid_app.exception]
    liquid_metric = lambda label: next(
        BeautifulSoup(item.value, "html.parser").select_one(".rs9-value").text
        for item in liquid_app.get("html")
        if (
            (dom := BeautifulSoup(item.value, "html.parser")).select_one(".rs9-label")
            and dom.select_one(".rs9-label").text == label
        )
    )
    assert liquid_metric("MAX SHARES") == "10"
    assert liquid_metric("POSITION NOTIONAL") == "$2,000.00"
    assert liquid_app.number_input(key="risk_liquid_balance").value == 2000.0
    assert len(liquid_app.toggle) == 1
    assert liquid_app.toggle(key="risk_capital_source_tactical").value is False
    assert liquid_app.session_state["risk_capital_source"] == "USE LIQUID BALANCE ENTERED"
    liquid_switch_html = "\n".join(item.value for item in liquid_app.get("html"))
    assert "Capital Source // USING" in liquid_switch_html
    assert "$2,000.00" in liquid_switch_html

    tactical_app = AppTest.from_string(TACTICAL_LIMIT_FIXTURE, default_timeout=30).run()
    assert not tactical_app.exception, [e.message for e in tactical_app.exception]
    tactical_metric = lambda label: next(
        BeautifulSoup(item.value, "html.parser").select_one(".rs9-value").text
        for item in tactical_app.get("html")
        if (
            (dom := BeautifulSoup(item.value, "html.parser")).select_one(".rs9-label")
            and dom.select_one(".rs9-label").text == label
        )
    )
    assert tactical_metric("MAX SHARES") == "0"
    assert tactical_metric("POSITION NOTIONAL") == "$0.00"
    assert len(tactical_app.toggle) == 1
    assert tactical_app.toggle(key="risk_capital_source_tactical").value is True
    assert tactical_app.session_state["risk_capital_source"] == "USE TACTICAL ROOM"
    tactical_switch_html = "\n".join(item.value for item in tactical_app.get("html"))
    assert "Capital Source // USING" in tactical_switch_html
    assert "$0.00" in tactical_switch_html

    stale_liquid = AppTest.from_string(STALE_LIQUID_FIXTURE, default_timeout=30).run()
    assert not stale_liquid.exception, [e.message for e in stale_liquid.exception]
    assert stale_liquid.number_input(key="risk_liquid_balance").value == 2000.0
    assert stale_liquid.session_state["_risk_true_cash_source"] == "cashAvailableForInvestment"
    assert all(stale_liquid.session_state["fixture_balance_refreshes"])

    manual = AppTest.from_string(FIXTURE, default_timeout=30).run()
    manual.number_input(key="risk_liquid_balance").set_value(1234.0).run()
    manual.number_input(key="risk_stop_price").set_value(185.0).run()
    assert not manual.exception
    assert manual.number_input(key="risk_liquid_balance").value == 1234.0
    manual.button(key="risk_refresh_portfolio").click().run()
    assert not manual.exception
    assert manual.number_input(key="risk_liquid_balance").value == 2000.0
    dynamic_fixture = FIXTURE.replace(
        "'cashAvailableForInvestment':2000",
        "'cashAvailableForInvestment':st.session_state.get('fixture_cash', 2000)",
    ).replace("'accountIdKey':'fixture'", "'accountIdKey':st.session_state.get('fixture_account', 'fixture')")
    dynamic = AppTest.from_string(dynamic_fixture, default_timeout=30).run()
    dynamic.session_state['fixture_cash'] = 3000.0
    dynamic.run()
    assert dynamic.number_input(key="risk_liquid_balance").value == 3000.0
    dynamic.number_input(key="risk_liquid_balance").set_value(1234.0).run()
    dynamic.session_state['fixture_cash'] = 4000.0
    dynamic.run()
    assert dynamic.number_input(key="risk_liquid_balance").value == 1234.0
    dynamic.session_state['fixture_account'] = 'second-account'
    dynamic.run()
    assert not dynamic.exception
    assert dynamic.number_input(key="risk_liquid_balance").value == 4000.0
    assert _select_true_cash({"Computed": {"marginBuyingPower": 50000}})[0] == 0
    assert _select_true_cash({"Computed": {"cashAvailableForInvestment": -20, "cashBalance": 9000}})[0] == -20

    fallback = AppTest.from_string(FALLBACK_FIXTURE, default_timeout=30).run()
    assert not fallback.exception, [e.message for e in fallback.exception]
    assert fallback.session_state["fixture_etrade_attempts"] == ["SPY"]
    assert fallback.session_state["fixture_yahoo_quotes"] == ["SPY"]
    assert fallback.session_state["risk_quote_source"] == "YAHOO FINANCE"
    assert fallback.number_input(key="risk_entry_price").value == 321.25
    assert fallback.number_input(key="risk_stop_price").value == 305.19
    fallback.number_input(key="risk_stop_price").set_value(300.0).run()
    assert not fallback.exception, [e.message for e in fallback.exception]
    assert fallback.session_state["fixture_etrade_attempts"] == ["SPY"]
    assert fallback.session_state["fixture_yahoo_quotes"] == ["SPY"]

    disconnected = AppTest.from_string(DISCONNECTED_FIXTURE, default_timeout=30).run()
    assert not disconnected.exception, [e.message for e in disconnected.exception]
    assert disconnected.session_state["risk_quote_source"] == "YAHOO FINANCE"
    assert disconnected.session_state["disconnected_yahoo_quotes"] == ["SPY"]
    assert disconnected.session_state["risk_quote_symbol"] == "SPY"
    assert disconnected.session_state["risk_entry_price"] == 410.20
    assert disconnected.session_state["risk_stop_price"] == 389.69
    assert disconnected.text_input(key="risk_ticker").value == "SPY"
    assert len(disconnected.number_input) == 0

    remembered = AppTest.from_string(PERSISTED_BOOK_FIXTURE, default_timeout=30).run()
    assert not remembered.exception, [e.message for e in remembered.exception]
    assert len(remembered.checkbox) == 2
    assert remembered.number_input(key="risk_gain_threshold").value == 6.0
    assert remembered.number_input(key="risk_tactical_sleeve_pct").value == 20.0
    assert remembered.number_input(key="risk_full_position_pct").value == 2.0
    assert remembered.session_state["_risk_book_snapshot_v1"]["account_key"] == "cached-risk-account"
    assert remembered.session_state["risk_quote_source"] == "YAHOO FINANCE"
    memory_caption = [str(item.value) for item in remembered.caption]
    assert any("RISK BOOK MEMORY" in value for value in memory_caption)

    processing = AppTest.from_string(PROCESSING_BOOK_FIXTURE, default_timeout=30).run()
    assert not processing.exception, [e.message for e in processing.exception]
    assert processing.session_state["processing_refresh_attempted"] is True
    assert processing.session_state["_risk_book_snapshot_v1"]["account_key"] == "processing-risk-account"
    assert len(processing.checkbox) == 2
    assert processing.number_input(key="risk_tactical_sleeve_pct").value == 15.0
    processing_captions = [str(item.value) for item in processing.caption]
    assert any("RISK BOOK MEMORY" in value for value in processing_captions)

    assert "retry_live_due" in source
    assert "_risk_live_quote_attempt_at" in source
    assert 'class="risk-v10-company-box"' in source
    assert 'original_text_input("Ticker"' in source
    assert "risk_ticker_smart_v10" not in source
    assert "previous_next_trade" not in source
    assert "_v9._v2._render_next_trade =" not in source
    assert '3. PICK E*TRADE ACCOUNT' in source
    assert '4. REVIEW + SEND ORDER' in source
    assert '_RISK_ORDER_DEFAULT_ACCOUNT_SUFFIX = "5474"' in source
    assert 'on_change=_sync_main_picker_from_order_picker' in source
    assert 'main_key = _main_risk_account_key()' in source
    assert '_enforce_locked_account_picker_sync()' in source
    assert 'class="risk-v9-capacity-warning"' in v9_source
    print("Production Risk route: compact zero-dead-space layout, ticker/company split, Risk Book memory, E*TRADE-first quotes, sizing PASS")


if __name__ == "__main__":
    main()
