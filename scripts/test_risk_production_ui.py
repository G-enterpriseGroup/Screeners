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

    print("Production Risk route: fragment ownership, E*TRADE-first quote path, Yahoo fallback, sizing PASS")


if __name__ == "__main__":
    main()
