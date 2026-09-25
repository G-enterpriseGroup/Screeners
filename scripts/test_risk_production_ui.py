"""Offline interaction regression through the actual v7 → v10 → v9 → v2 Risk route.

Only broker responses and the unrelated sector enrichment are stubbed.
"""
from pathlib import Path
import sys

from bs4 import BeautifulSoup
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.risk_sizing import stock_position_size
from src.risk_sizing_ui_v9 import _select_true_cash
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
    def lookup(self,*a,**k): return {}
st.session_state['etrade_accounts']=[{'accountIdKey':'fixture','accountName':'Synthetic test portfolio'}]
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
render_risk_sizing(
    FixtureClient(),
    account_picker=lambda _:st.session_state['etrade_accounts'][0],
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
    source = (ROOT / "src" / "risk_sizing_ui_v10.py").read_text(encoding="utf-8")
    v9_source = (ROOT / "src" / "risk_sizing_ui_v9.py").read_text(encoding="utf-8")
    v2_source = (ROOT / "src" / "risk_sizing_ui_v2.py").read_text(encoding="utf-8")
    assert "@st.fragment\ndef render_risk_sizing" in source
    assert "st.rerun" not in source
    assert "st.rerun" not in v9_source
    assert "st.rerun" not in v2_source
    assert "The quote loads automatically from live E*TRADE first" in v2_source
    assert "Margin buying power is never used as cash." in v2_source
    assert 'st.segmented_control(' in v2_source
    assert 'st.toggle(' not in v2_source
    assert '"USE LIQUID BALANCE ENTERED"' in v2_source
    assert '"USE TACTICAL ROOM"' in v2_source

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
    assert app.segmented_control(key="risk_capital_source").value == "USE LIQUID BALANCE ENTERED"
    assert len(app.segmented_control) == 1
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
    app.checkbox[1].uncheck().run()
    clean()
    assert not app.checkbox[0].value and not app.checkbox[1].value
    assert metric("CURRENT TACTICAL") == "$16,562.00"
    app.number_input(key="risk_stop_price").set_value(180.0).run()
    clean()
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
    assert sum('class="risk-v9-stop-pct"' in x.value for x in app.get("html")) == 1
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
    assert len(liquid_app.segmented_control) == 1
    assert liquid_app.segmented_control(key="risk_capital_source").value == "USE LIQUID BALANCE ENTERED"

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
    assert len(tactical_app.segmented_control) == 1
    assert tactical_app.segmented_control(key="risk_capital_source").value == "USE TACTICAL ROOM"

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
    print("Production Risk route: compact zero-dead-space layout, ticker/company split, Risk Book memory, E*TRADE-first quotes, sizing PASS")


if __name__ == "__main__":
    main()
