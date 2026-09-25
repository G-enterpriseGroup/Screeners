"""Offline interaction regression through the actual v7 → v10 → v9 → v2 Risk route.

Only broker responses and the unrelated sector enrichment are stubbed.
"""
from pathlib import Path
import sys
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
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
        return dict(symbol=symbol,companyName=company_name(symbol),lastTrade=199.98,bid=199.95,ask=200,changeClose=-.5)
    def lookup(self,*a,**k): return {}
st.session_state['etrade_accounts']=[{'accountIdKey':'fixture','accountName':'Synthetic test portfolio'}]
render_risk_sizing(FixtureClient(), account_picker=lambda _:st.session_state['etrade_accounts'][0], refresh_accounts=lambda _:None, account_balance=lambda *a,**k:{'Computed':{'cashBalance':2000}}, balance_snapshot=lambda p:(100000,2000,98000), touch_session=lambda:None)
"""


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



def main():
    source = (ROOT / "src" / "risk_sizing_ui_v10.py").read_text(encoding="utf-8")
    assert "@st.fragment\ndef render_risk_sizing" in source

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
    assert len(app.checkbox) == 6
    assert app.number_input(key="risk_entry_price").value == 200.0
    assert app.number_input(key="risk_stop_price").value == 190.0
    saved = app.session_state["_risk_book_snapshot_v1"]
    assert saved["account_key"] == "fixture"
    assert len(saved["rows"]) == 6
    assert saved["account_total"] == 100000.0
    assert metric("CURRENT TACTICAL") == "$16,562.00"
    assert metric("MAX SHARES") == "22"
    app.checkbox[0].check().run()
    clean()
    assert app.checkbox[0].value and app.checkbox[1].value
    assert metric("CURRENT TACTICAL") == "$1,562.00"
    assert metric("LONG-TERM / STRUCTURAL") == "$93,000.00"
    assert metric("TACTICAL ROOM") == "$13,438.00"
    assert metric("MAX DOLLAR RISK") == "$225.00"
    app.checkbox[1].uncheck().run()
    clean()
    assert not app.checkbox[0].value and not app.checkbox[1].value
    assert metric("CURRENT TACTICAL") == "$16,562.00"
    app.number_input(key="risk_stop_price").set_value(180.0).run()
    clean()
    assert metric("MAX SHARES") == "11"
    assert app.session_state["fixture_quotes"] == ["SPY"]
    ticker = app.selectbox(key="risk_ticker_smart_v10")
    ticker.select(next(label for label in ticker.options if label.startswith("QQQ —"))).run()
    clean()
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
    assert disconnected.selectbox(key="risk_ticker_smart_v10").value.startswith("SPY")
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

    assert "retry_live_due" in source
    assert "_risk_live_quote_attempt_at" in source
    print("Production Risk route: live persistence, last-session Risk Book memory, E*TRADE-first quote path, disconnected Yahoo fallback, sizing PASS")


if __name__ == "__main__":
    main()
