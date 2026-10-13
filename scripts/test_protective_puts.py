"""Focused regression tests for Protective Puts math."""

from datetime import date

from src.protective_puts import (
    build_put_analysis, etf_equivalent_max_loss, portfolio_risk_slide,
    protective_put_metrics, proxy_put_expiry_pnl, proxy_put_max_loss,
    proxy_put_risk_scenarios,
)


metrics = protective_put_metrics(
    stock_price=100.0,
    strike=95.0,
    premium_per_share=2.0,
    shares=100,
)
assert etf_equivalent_max_loss(
    spot=500, ask=3, strike=480, basket_value=100000,
) == (4600.0, 200.0)
assert etf_equivalent_max_loss(
    spot=500, ask=3, strike=480, basket_value=0,
) == (2300.0, 100.0)
assert etf_equivalent_max_loss(
    spot=500, ask=3, strike=550, basket_value=100000,
) == (0.0, 200.0)
# OptionStrat-style example: 62 QQQ proxy shares and ONE full 100-share PUT.
# At the strike, the put expires worthless; below strike the 38 extra
# put-covered shares make the position profitable as QQQ falls further.
qqq_spot, qqq_k, qqq_ask = 746.04, 900.0, 155.55
qqq_basket = 62 * qqq_spot
assert round(proxy_put_expiry_pnl(
    spot=qqq_spot, strike=qqq_k, ask=qqq_ask, contracts=1,
    basket_value=qqq_basket, expiry_spot=qqq_k,
), 2) == -6009.48
assert tuple(round(v, 2) for v in proxy_put_max_loss(
    spot=qqq_spot, strike=qqq_k, ask=qqq_ask, contracts=1,
    basket_value=qqq_basket,
)) == (6009.48, 900.0)
# One put cannot fully protect 200 ETF-equivalent shares; maximum loss
# instead occurs at ETF=$0, after deducting its entire purchase premium.
assert proxy_put_max_loss(
    spot=100, strike=95, ask=2, contracts=1, basket_value=20000,
) == (10700.0, 0.0)
# Matching 100 shares and one contract has the conventional married-put floor.
assert proxy_put_max_loss(
    spot=100, strike=95, ask=2, contracts=1, basket_value=10000,
) == (700.0, 0.0)
# A beta above one can exhaust the basket before the ETF reaches zero. The
# worst modeled outcome is then the basket-zero corner, not $0 or the strike.
assert proxy_put_max_loss(
    spot=100, strike=95, ask=2, contracts=1,
    basket_value=10000, beta=2,
) == (5700.0, 50.0)

# The slide must show current-value changes in both the checked basket and
# the complete eligible long-equity portfolio. Unchecked equity moves 1:1.
slide = proxy_put_risk_scenarios(
    spot=100, strike=95, ask=2, contracts=6,
    basket_value=60000, portfolio_value=100000,
)
assert slide["Market Move %"].tolist() == [
    -30, -25, -20, -15, -10, -5, -2, 0, 2, 5, 10, 15, 20, 25, 30,
]
down_ten = slide.set_index("Market Move %").loc[-10]
assert down_ten["ETF Price"] == 90
assert down_ten["Basket P/L No Put"] == -6000
assert down_ten["Basket P/L With Put"] == -4200
assert down_ten["Unselected P/L"] == -4000
assert down_ten["Portfolio Value No Put"] == 90000
assert down_ten["Portfolio Value With Put"] == 91800
assert down_ten["Portfolio Change $"] == -8200
assert down_ten["Portfolio Change %"] == -8.2
assert down_ten["Put Benefit $"] == 1800
flat_market = slide.set_index("Market Move %").loc[0]
assert flat_market["Basket P/L With Put"] == -1200
assert flat_market["Unselected P/L"] == 0
assert flat_market["Portfolio Value With Put"] == 98800
assert flat_market["Put Benefit $"] == -1200
up_ten = slide.set_index("Market Move %").loc[10]
assert up_ten["Basket P/L No Put"] == 6000
assert up_ten["Basket P/L With Put"] == 4800
assert up_ten["Unselected P/L"] == 4000
assert up_ten["Portfolio Value With Put"] == 108800
assert up_ten["Portfolio Change %"] == 8.8
different_beta = proxy_put_risk_scenarios(
    spot=100, strike=95, ask=2, contracts=6,
    basket_value=60000, portfolio_value=100000,
    beta=2, moves=[-10],
).iloc[0]
assert different_beta["Basket P/L No Put"] == -12000
assert different_beta["Unselected P/L"] == -4000
assert different_beta["Basket P/L With Put"] == -10200
assert different_beta["Portfolio Value With Put"] == 85800

# The scenario engine and exact maximum-loss calculator must agree at the
# worst price. It can show losses above 100% of the selected basket if the
# full premium on a whole contract is larger than that basket.
max_loss, worst_spot = proxy_put_max_loss(
    spot=100, strike=95, ask=2, contracts=1, basket_value=20000,
)
at_worst = proxy_put_risk_scenarios(
    spot=100, strike=95, ask=2, contracts=1,
    basket_value=20000, portfolio_value=30000, moves=[0, -100],
)
assert at_worst["Market Move %"].tolist() == [-100, 0]
assert at_worst.iloc[0]["ETF Price"] == worst_spot
assert at_worst.iloc[0]["Basket P/L With Put"] == -max_loss
assert at_worst.iloc[0]["Unselected P/L"] == -10000
assert at_worst.iloc[0]["Portfolio Change $"] == -max_loss - 10000
assert at_worst.iloc[0]["Portfolio Value With Put"] == 9300
basket_only = proxy_put_risk_scenarios(
    spot=100, strike=95, ask=2, contracts=1,
    basket_value=10000, portfolio_value=10000, moves=[-10],
)
assert basket_only.iloc[0]["Portfolio Value With Put"] == 9300
assert basket_only.iloc[0]["Unselected P/L"] == 0
try:
    proxy_put_risk_scenarios(
        spot=100, strike=95, ask=2, contracts=1,
        basket_value=10000, portfolio_value=9999,
    )
    raise AssertionError("Total eligible equity cannot be below its selected basket")
except ValueError:
    pass
assert metrics["contracts"] == 1
assert metrics["put_cost"] == 200.0
assert metrics["max_loss"] == 700.0
assert metrics["max_loss_per_share"] == 7.0
assert abs(metrics["max_loss_pct"] - 7.0) < 1e-9
assert metrics["breakeven"] == 102.0

rows = [
    {
        "call_put": "PUT",
        "strike": 95.0,
        "bid": 1.8,
        "ask": 2.0,
        "last": 1.9,
        "volume": 10,
        "open_interest": 250,
        "osi": "TEST",
    },
    {"call_put": "CALL", "strike": 95.0, "ask": 4.0},
]
frame = build_put_analysis(
    rows,
    expiry=date(2026, 12, 18),
    stock_price=100.0,
    shares=200,
    today=date(2026, 10, 6),
)
assert len(frame) == 1
assert frame.iloc[0]["Contracts"] == 2
assert frame.iloc[0]["Put Cost (Ask)"] == 400.0
assert frame.iloc[0]["Max Loss (Ask)"] == 1400.0
assert abs(frame.iloc[0]["Max Loss % (Ask)"] - 7.0) < 1e-9

risk_slide = portfolio_risk_slide(
    portfolio_value=100000.0,
    selected_puts=[{
        "Spot": 100.0, "Strike": 95.0, "Ask": 2.0, "Contracts": 10,
    }],
)
assert list(risk_slide["Market Move %"]) == [-20,-15,-10,-5,-2,0,2,5,10,15,20]
down20 = risk_slide.loc[risk_slide["Market Move %"] == -20].iloc[0]
flat = risk_slide.loc[risk_slide["Market Move %"] == 0].iloc[0]
up20 = risk_slide.loc[risk_slide["Market Move %"] == 20].iloc[0]
assert down20["Unhedged Portfolio"] == 80000.0
assert down20["Put Intrinsic"] == 15000.0
assert down20["Put Premium"] == 2000.0
assert down20["With Selected Puts"] == 93000.0
assert flat["With Selected Puts"] == 98000.0
assert up20["With Selected Puts"] == 118000.0
assert portfolio_risk_slide(
    portfolio_value=100000.0, selected_puts=[],
)["With Selected Puts"].equals(
    portfolio_risk_slide(
        portfolio_value=100000.0, selected_puts=[],
    )["Unhedged Portfolio"]
)


from src.protective_puts import portfolio_hedge_candidates

proxy_rows = [
    {"call_put": "PUT", "strike": 100.0, "ask": 3.0},
    {"call_put": "PUT", "strike": 95.0, "ask": 1.0},
    {"call_put": "PUT", "strike": 70.0, "ask": 0.1},
    {"call_put": "PUT", "strike": 110.0, "ask": None},
    {"call_put": "CALL", "strike": 100.0, "ask": 2.0},
]
portfolio = portfolio_hedge_candidates(
    proxy_rows, symbol="SPY", expiry=date(2026, 12, 18),
    spot=100.0, exposure=10000.0, stress_pct=15.0, beta=1.0,
    target_loss_pct=0.0, premium_limit_pct=50.0, today=date(2026, 10, 11),
)
assert len(portfolio) == 2, portfolio
cheapest = portfolio.sort_values("Put Cost").iloc[0]
assert cheapest["Strike"] == 95.0
assert cheapest["Contracts"] == 2
assert cheapest["Put Cost"] == 200.0
assert cheapest["Stress Put Payout"] == 2000.0
assert cheapest["Stress Net P&L"] == 300.0
assert cheapest["Stress Remaining Loss"] == 0.0
assert portfolio_hedge_candidates(
    proxy_rows, symbol="SPY", expiry=date(2026, 12, 18),
    spot=100, exposure=10000, stress_pct=15, beta=1,
    target_loss_pct=0, premium_limit_pct=1, today=date(2026, 10, 11),
).empty
assert portfolio_hedge_candidates(
    proxy_rows, symbol="QQQ", expiry=date(2026, 12, 18),
    spot=100, exposure=10000, stress_pct=10, beta=0.5,
    target_loss_pct=5, premium_limit_pct=10, today=date(2026, 10, 11),
).empty


# Portfolio table must match E*TRADE Risk Sizing sleeve and account math.
import pandas as pd
from src.protective_puts_portfolio_ui import _classified_hedge_book, _preferred_account_index

assert _preferred_account_index([
    {"accountName": "Another Account", "accountId": "0001"},
    {"accountName": "Raj Singh", "accountId": "5474"},
]) == 1

holdings = pd.DataFrame([
    {"Symbol": "AAA", "Quantity": 10, "Market Value": 1200, "Gain/Loss": 200,
     "Gain/Loss %": 20.0, "Total Cost": 1000},
    {"Symbol": "AAA", "Quantity": 5, "Market Value": 450, "Gain/Loss": -50,
     "Gain/Loss %": -10.0, "Total Cost": 500},
    {"Symbol": "BBB", "Quantity": 10, "Market Value": 800, "Gain/Loss": -100,
     "Gain/Loss %": -11.11, "Total Cost": 900},
    {"Symbol": "CCC", "Quantity": 20, "Market Value": 1000, "Gain/Loss": -200,
     "Gain/Loss %": -16.67, "Total Cost": 1200},
    {"Symbol": "DDD", "Quantity": 5, "Market Value": 500, "Gain/Loss": None,
     "Gain/Loss %": None, "Total Cost": None},
])
hedge_book = _classified_hedge_book(
    holdings, account_total=10000.0, long_term_tickers={"BBB"},
    gain_threshold=5.0, tactical_pct=15.0,
).set_index("Symbol")
assert len(hedge_book) == 4
assert hedge_book.loc["AAA", "Quantity"] == 15
assert hedge_book.loc["AAA", "Market Value"] == 1650
assert hedge_book.loc["AAA", "Gain/Loss"] == 150
assert abs(hedge_book.loc["AAA", "Gain/Loss %"] - 10.0) < 1e-9
assert hedge_book.loc["AAA", "Sleeve"] == "LONG-TERM"
assert hedge_book.loc["BBB", "Sleeve"] == "LONG-TERM"
assert hedge_book.loc["CCC", "Sleeve"] == "TACTICAL"
assert abs(hedge_book.loc["CCC", "% Tactical Sleeve"] - (1000/1500*100)) < 1e-9
assert abs(hedge_book.loc["AAA", "% Account"] - 16.5) < 1e-9
assert pd.isna(hedge_book.loc["DDD", "Gain/Loss %"])
unknown_nav = _classified_hedge_book(
    holdings, account_total=None, long_term_tickers=set(),
    gain_threshold=5.0, tactical_pct=15.0,
).set_index("Symbol")
assert unknown_nav["% Account"].isna().all()
assert unknown_nav["% Tactical Sleeve"].isna().all()


# A displayed row can move, but its account/ticker selection identity cannot.
from src.protective_puts_portfolio_ui import _sort_hedge_book, _HEDGE_SORT_OPTIONS

sortable = hedge_book.reset_index()
original_symbols = set(sortable["Symbol"])
original_values = sortable.set_index("Symbol")["Market Value"].to_dict()
assert all(
    name in _HEDGE_SORT_OPTIONS
    for name in ("HEDGE", "SLEEVE / %", "SYMBOL", "SHARES", "LAST",
                 "P&L %", "P&L", "VALUE", "% ACCT")
)
for criterion in _HEDGE_SORT_OPTIONS:
    for direction in ("ASC", "DESC"):
        arranged = _sort_hedge_book(sortable, criterion, direction, {"BBB", "CCC"})
        assert set(arranged["Symbol"]) == original_symbols
        assert arranged.set_index("Symbol")["Market Value"].to_dict() == original_values
        assert "_hedge_checked_sort" not in arranged.columns
assert list(_sort_hedge_book(sortable, "SYMBOL", "ASC", set())["Symbol"]) == sorted(original_symbols)
assert list(_sort_hedge_book(sortable, "SYMBOL", "DESC", set())["Symbol"]) == sorted(original_symbols, reverse=True)
assert _sort_hedge_book(sortable, "VALUE", "DESC", set()).iloc[0]["Symbol"] == "AAA"
assert _sort_hedge_book(sortable, "VALUE", "ASC", set()).iloc[0]["Symbol"] == "DDD"
assert set(_sort_hedge_book(sortable, "HEDGE", "DESC", {"BBB", "CCC"}).head(2)["Symbol"]) == {"BBB", "CCC"}
assert set(_sort_hedge_book(sortable, "HEDGE", "ASC", {"BBB", "CCC"}).tail(2)["Symbol"]) == {"BBB", "CCC"}
assert _sort_hedge_book(sortable, "P&L %", "DESC", set()).iloc[-1]["Symbol"] == "DDD"


# Direct ticker + matched put: $0 floor is the exact strike >= basis + ASK test.
# For each expiration choose the cheapest such put; otherwise show real min loss.
from src.protective_puts import cheapest_near_zero_put_by_expiration

direct_rows = [
    {"call_put":"PUT","strike":110.0,"ask":10.0,"osi":"EXP1_PRICY"},
    {"call_put":"PUT","strike":108.0,"ask":8.0,"osi":"EXP1_CHEAP"},
    {"call_put":"PUT","strike":101.0,"ask":2.0,"osi":"EXP1_LOSS"},
    {"call_put":"PUT","strike":106.0,"ask":0.0,"osi":"ZERO_INVALID"},
    {"call_put":"CALL","strike":115.0,"ask":1.0,"osi":"CALL_INVALID"},
]
far_rows = [
    {"call_put":"PUT","strike":100.0,"ask":1.0,"osi":"EXP2_SMALL_LOSS"},
    {"call_put":"PUT","strike":102.0,"ask":4.0,"osi":"EXP2_LARGE_LOSS"},
    {"call_put":"PUT","strike":108.0,"ask":float("nan"),"osi":"INVALID_ASK"},
]
direct_scan = pd.concat([
    build_put_analysis(
        direct_rows, expiry=date(2026,12,18), stock_price=100,
        shares=100, today=date(2026,10,11),
    ),
    build_put_analysis(
        far_rows, expiry=date(2027,1,15), stock_price=100,
        shares=100, today=date(2026,10,11),
    ),
], ignore_index=True)
ranked = cheapest_near_zero_put_by_expiration(direct_scan, target_dte=68)
assert len(ranked) == 2
early = ranked[ranked["Expiration"] == "12-18-26"].iloc[0]
late = ranked[ranked["Expiration"] == "01-15-27"].iloc[0]
assert early["Contract"] == "EXP1_CHEAP", early
assert early["Target Met"] and early["Max Loss (Ask)"] == 0
assert early["Put Cost (Ask)"] == 800
assert early["Priority Expiration"]
assert late["Contract"] == "EXP2_SMALL_LOSS", late
assert not late["Target Met"] and late["Max Loss (Ask)"] == 100
assert late["Status"] == "MINIMUM AVAILABLE LOSS"
assert ranked["Priority Expiration"].sum() == 1
assert cheapest_near_zero_put_by_expiration(pd.DataFrame(), target_dte=60).empty
try:
    cheapest_near_zero_put_by_expiration(direct_scan, target_dte=-1)
    raise AssertionError("negative DTE must be rejected")
except ValueError:
    pass


from src.protective_puts import portfolio_chain_scenario, rank_portfolio_chain
scenario_rows = [
    {"call_put":"PUT","strike":110.0,"ask":1.5},
    {"call_put":"PUT","strike":96.0,"ask":1.0},
    {"call_put":"PUT","strike":80.0,"ask":0.1},
    {"call_put":"PUT","strike":90.0,"ask":0.0},
    {"call_put":"CALL","strike":110.0,"ask":2.0},
]
scenario = portfolio_chain_scenario(
    scenario_rows, symbol="SPY", expiry=date(2026,12,18),
    spot=100, exposure=10000, stress_pct=15, beta=1, today=date(2026,10,11),
)
assert len(scenario) == 3
ranked_scenario = rank_portfolio_chain(scenario, preferred_dte=60)
assert ranked_scenario.iloc[0]["Strike"] == 110.0
assert ranked_scenario.iloc[0]["Stress Remaining Loss"] == 0
assert ranked_scenario.iloc[0]["Priority"]
assert ranked_scenario.iloc[-1]["Stress Remaining Loss"] > 0

# Ghotra 100-share table applies only to the PUT UNDERLYING, not proxy holdings.
from pathlib import Path

legacy_rows = [
    {"call_put": "PUT", "strike": 108, "ask": 8.0,
     "last": 8.2, "bid": 7.7, "implied_volatility": .24, "osi": "SPYP108"},
    {"call_put": "PUT", "strike": 101, "ask": 2.0,
     "last": None, "implied_volatility": None, "osi": "SPYP101"},
]
legacy = portfolio_chain_scenario(
    legacy_rows, symbol="SPY", expiry=date(2026, 12, 18),
    spot=100, exposure=55000, stress_pct=15, beta=1,
    today=date(2026, 10, 11),
)
assert len(legacy) == 2
first = legacy.loc[legacy["Strike"] == 108].iloc[0]
second = legacy.loc[legacy["Strike"] == 101].iloc[0]
assert first["Cost of Put (Ask)"] == 800
assert first["Spot"] == 100
assert first["Max Loss (Ask)"] == 0
assert abs(first["Max Loss (Last)"] - 20) < 1e-9
assert first["Implied Volatility"] == .24
assert "100" in first["Max Loss Calc (Ask)"]
assert second["Max Loss (Ask)"] == 100
assert pd.isna(second["Max Loss (Last)"])
ranked_direct = rank_portfolio_chain(
    legacy, preferred_dte=68, direct_100_share_example=True,
)
assert ranked_direct.iloc[0]["Strike"] == 108
assert ranked_direct.iloc[0]["Priority"]
ui_source = (Path(__file__).resolve().parents[1] /
             "src" / "protective_puts_portfolio_ui.py").read_text(encoding="utf-8")
assert 'with st.expander("OPTIONAL HEDGE ASSUMPTIONS"' not in ui_source
assert '"PUT TICKER", value=""' in ui_source
assert '"FULL GHOTRA COLUMNS' not in ui_source
scroller_html = (Path(__file__).resolve().parents[1] /
                 "src" / "components" / "pp_put_chain_scroller_v1" / "index.html").read_text(encoding="utf-8")
assert '"LOSS CALC"' in scroller_html
assert '"QTY / EQ SH"' in scroller_html
assert '"BASKET LOSS ?"' not in scroller_html
assert 'i===10&&row.qty_title' in scroller_html
assert "if not account_key:" in ui_source
assert "SUBMIT {len(selected)} LIVE PUT ORDER(S)" in ui_source

# Canvas editor styling must specify a legible foreground on every chain row.
from src.protective_puts_portfolio_ui import _chain_row_colors

regular = _chain_row_colors(2, False, False, 12)
best = _chain_row_colors(1, False, True, 12)
first = _chain_row_colors(0, True, True, 12)
assert len(regular) == len(best) == len(first) == 12
assert all("background-color:#fb8b1e" in cells[0] and "color:#000000" in cells[0]
           for cells in (regular, best, first))
assert all("background-color:#080808" in cell and "color:#f6b56a" in cell
           for cell in regular[1:]), regular
assert all("background-color:#10130d" in cell and "color:#4af6c3" in cell
           for cell in best[1:]), best
assert all("background-color:#fb8b1e" in cell and "color:#000000" in cell
           for cell in first), first

# The full chain may exceed 4,000 rows; page selection must preserve
# exact contract identity and never silently switch BUY to a different strike.
from src.protective_puts_portfolio_ui import (
    _CHAIN_PAGE_SIZE, _contract_identity, _reconcile_contract_checks,
    _selected_contract_orders,
)
assert _CHAIN_PAGE_SIZE <= 30
rows = []
for i in range(122):
    rows.append({
        "Proxy": "SPY", "Expiration": "12-18-26", "Strike": float(600+i),
        "Contract": f"SPY_P_{600+i}", "Orderable": True,
        "Contracts": 1, "Ask": 2.0,
    })
big_chain = pd.DataFrame(rows)
keys = [_contract_identity(row) for row in rows]
assert len(keys) == len(set(keys))
saved = _reconcile_contract_checks(set(), keys[:25], [True]+[False]*24)
assert saved == {keys[0]}
saved = _reconcile_contract_checks(saved, keys[25:50], [False]*24+[True])
assert saved == {keys[0], keys[49]}, saved
saved = _reconcile_contract_checks(saved, keys[:25], [False]*25)
assert saved == {keys[49]}
orders = _selected_contract_orders(big_chain, saved)
assert len(orders) == 1 and orders[0]["Strike"] == 649.0
try:
    _reconcile_contract_checks(saved, keys[:2], [True])
    raise AssertionError("Mismatch in checked rows must fail")
except ValueError:
    pass
big_chain.loc[49, "Orderable"] = False
try:
    _selected_contract_orders(big_chain, saved)
    raise AssertionError("Unorderable contract must block review")
except ValueError:
    pass

# Portfolio Hedge must fetch every listed future expiration for every entered
# ticker on the explicit Enter scan; later UI edits only reuse cached raw data.
assert "work.extend((sym, spot, expiry) for expiry in future)" in ui_source
assert "for expiry in _sample_expirations(" not in ui_source
assert "LOAD ALL" not in ui_source
assert "REMAINING CHAINS" not in ui_source
assert "BASKET LOSS ?" not in ui_source
assert "LIVE HEDGE BASKET //" in ui_source
assert "ETF-Equivalent Max Loss (Ask)" in ui_source
assert "_render_risk_slide(result, chosen, money, metric_markup)" in ui_source

# The virtualized scroller sorts all loaded contracts and keeps exact BUY IDs;
# the prior native 12-row paging widgets must no longer appear.
from src.protective_puts_portfolio_ui import (
    _CHAIN_SORT_COLUMNS, _sort_contract_chain, _chain_cell_markup,
)
sort_rows = pd.DataFrame([
    {"Proxy":"SPY","Expiration":"10-18-26","DTE":7,"Strike":600.0,
     "Bid":1.2,"Ask":1.3,"Cost of Put (Ask)":130.0,"Max Loss (Ask)":250.0,
     "Stress Remaining Loss":150.0,"Contracts":1,"Open Interest":100,
     "Contract":"SPY7", "Priority":False,"Best / Expiration":False},
    {"Proxy":"SPY","Expiration":"12-18-26","DTE":68,"Strike":650.0,
     "Bid":2.2,"Ask":2.3,"Cost of Put (Ask)":230.0,"Max Loss (Ask)":10.0,
     "Stress Remaining Loss":0.0,"Contracts":1,"Open Interest":900,
     "Contract":"SPY68","Priority":True,"Best / Expiration":True},
])
assert "EXPIRATION" in _CHAIN_SORT_COLUMNS
assert _sort_contract_chain(sort_rows, "EXPIRATION", "ASC").iloc[0]["DTE"] == 7
assert _sort_contract_chain(sort_rows, "EXPIRATION", "DESC").iloc[0]["DTE"] == 68
assert _sort_contract_chain(sort_rows, "OPEN INTEREST", "DESC").iloc[0]["Open Interest"] == 900
sort_rows["ETF-Equivalent Max Loss (Ask)"] = [500.0, 20.0]
assert _sort_contract_chain(sort_rows, "MAX LOSS", "ASC").iloc[0]["Strike"] == 650
assert "BASKET LOSS" not in _CHAIN_SORT_COLUMNS
assert "pp-chain-top" in _chain_cell_markup(sort_rows.iloc[1].to_dict())
assert "pp-chain-cells" in _chain_cell_markup(sort_rows.iloc[0].to_dict())
assert "MAX LOSS" in _chain_cell_markup(sort_rows.iloc[0].to_dict()).upper()
assert "100×(" in _chain_cell_markup({**sort_rows.iloc[0].to_dict(), "Max Loss Calc (Ask)": "max(0, (700.00 + 1.30 - 600.00) × 100)"})
feature_src = (Path(__file__).resolve().parents[1] /
               "src" / "protective_puts_portfolio_ui.py").read_text(encoding="utf-8")
assert '("HIGHLIGHTED ONLY", "ALL CONTRACTS")' in feature_src
assert 'index=0, key="pp_chain_highlight_filter"' in feature_src
assert 'by_id[_contract_identity(row)]["annual"]' in feature_src
assert 'sort_key=f"{sort_by}|{direction}|{show_rows}"' in feature_src
assert "_PUT_CHAIN_SCROLL_COMPONENT(" in feature_src
assert 'st.button("◀ PREV"' not in feature_src
assert 'st.button("NEXT ▶"' not in feature_src

assert 'overflow-y:auto' in scroller_html
assert 'scroll.addEventListener("scroll"' in scroller_html
assert '#a3e635' in scroller_html and 'color:#000!important' in scroller_html
assert 'with st.expander("FULL GHOTRA COLUMNS' not in feature_src
# The option-chain remains virtualized; the checked-only RISK SLIDE uses a
# compact, fully visible scenario table beside its profit/loss graph.
chain_src = feature_src.split("def _results(", 1)[1].split("def render_portfolio_hedge(", 1)[0]
slide_src = feature_src.split("def _render_risk_slide(", 1)[1].split("@st.fragment", 1)[0]
assert "st.dataframe(" not in chain_src
assert "_risk_slide_table_markup(scenarios, money)" in slide_src
assert "proxy_put_risk_scenarios(" in slide_src
assert 'key="pp_chain_expiry_choice"' not in feature_src
assert '"Implied Volatility", "Cost of Put (Ask)"' not in feature_src
assert "LOAD ALL" not in feature_src and "REMAINING CHAINS" not in feature_src

from src.protective_puts_portfolio_ui import _risk_slide_table_markup
scenario_markup = _risk_slide_table_markup(
    proxy_put_risk_scenarios(
        spot=100.0, strike=95.0, ask=2.0, contracts=1,
        basket_value=10_000.0, portfolio_value=20_000.0,
    ), lambda value: f"${float(value):,.2f}",
)
assert scenario_markup.count("<tr") == 16  # header plus all 15 requested moves
assert "-30%" in scenario_markup and "+30%" in scenario_markup
assert "CHANGE %" in scenario_markup and "WITH PUT" in scenario_markup
assert "$19,800.00" in scenario_markup  # Flat market: full $200 premium included.



# Annual winner is based on ASK-premium-inclusive 100-share max loss for
# each underlying/calendar year, independent of visible scroll window or sort.
from src.protective_puts_portfolio_ui import _annual_lowest_loss_ids, _chain_scroll_values
annual_rows = pd.DataFrame([
    {"Proxy":"SPY","Expiration":"12-18-26","DTE":68,"Strike":600.0,
     "Contract":"SPY26A","Ask":3.0,"Cost of Put (Ask)":300.0,
     "Max Loss (Ask)":10.0,"Orderable":True},
    {"Proxy":"SPY","Expiration":"11-20-26","DTE":40,"Strike":610.0,
     "Contract":"SPY26B","Ask":2.0,"Cost of Put (Ask)":200.0,
     "Max Loss (Ask)":10.0,"Orderable":True},
    {"Proxy":"SPY","Expiration":"01-15-27","DTE":96,"Strike":620.0,
     "Contract":"SPY27A","Ask":4.0,"Cost of Put (Ask)":400.0,
     "Max Loss (Ask)":0.0,"Orderable":True},
    {"Proxy":"SPY","Expiration":"02-19-27","DTE":131,"Strike":625.0,
     "Contract":"SPY27BAD","Ask":0.0,"Cost of Put (Ask)":0.0,
     "Max Loss (Ask)":0.0,"Orderable":True},
    {"Proxy":"QQQ","Expiration":"12-18-26","DTE":68,"Strike":500.0,
     "Contract":"QQQ26A","Ask":1.0,"Cost of Put (Ask)":100.0,
     "Max Loss (Ask)":5.0,"Orderable":True},
    {"Proxy":"QQQ","Expiration":"12-18-26","DTE":68,"Strike":505.0,
     "Contract":"QQQ26BLOCKED","Ask":2.0,"Cost of Put (Ask)":200.0,
     "Max Loss (Ask)":0.0,"Orderable":False},
])
best_ids = _annual_lowest_loss_ids(annual_rows)
assert best_ids == {
    _contract_identity(annual_rows.iloc[i].to_dict()) for i in (1,2,4)
}, best_ids
assert not _annual_lowest_loss_ids(annual_rows.iloc[0:0])
sample_display = dict(sort_rows.iloc[0].to_dict())
sample_display["Max Loss Calc (Ask)"] = "max(0, (700.00 + 1.30 - 600.00) × 100)"
sample_display["Spot"] = 500.0
direct_payload = _chain_scroll_values(sample_display)
portfolio_payload = _chain_scroll_values(sample_display, hedge_exposure=100000.0)
assert "100×(" in direct_payload["values"][9]
assert direct_payload["values"][10] == "1 / 100 SH"
assert portfolio_payload["values"][10] == "1 / 200.0 SH"
assert portfolio_payload["values"][8] == "$40,130.00"  # Only 100 of 200 proxy shares are put-covered.
assert "200.0000 SHARES" in portfolio_payload["calc"]
assert "BASKET $100,000.00" in portfolio_payload["qty_title"]
assert len(portfolio_payload["values"]) == 12
positive_proxy = {**sample_display, "Spot": 700.0}
positive_loss = _chain_scroll_values(positive_proxy, hedge_exposure=100000.0)
assert positive_loss["values"][8] == "$40,130.00"  # One PUT covers only 100 of 142.9 proxy shares.
assert "P/L ETF@0" in positive_loss["calc"]
assert "P/L ETF@STRIKE" in positive_loss["calc"]
assert "if not chosen:" in ui_source  # The risk slide is absent until a PUT is checked.
assert "MARKET / ETF MOVE (%)" in ui_source
from src.protective_puts_portfolio_ui import _selected_symbols
assert _selected_symbols(["SPY", "QQQ", "SPY", "BAD INPUT"]) == ["QQQ", "SPY"]
assert '_load_selection(account_key)' in ui_source
assert 'checked_symbols = set(memory["tickers"]) & set(book["Symbol"].tolist())' in ui_source


# A migrated Streamlit session can retain the Raj-default marker while the
# account selector widget key has expired. Do not raise KeyError or switch a
# valid manually chosen account on subsequent reruns.
from src.protective_puts_portfolio_ui import _safe_account_index
test_accounts = [
    {"accountName": "Another Account", "accountIdKey": "test-other"},
    {"accountName": "Raj Singh", "accountIdKey": "test-primary"},
]
assert _safe_account_index(test_accounts, None, True) == 1
assert _safe_account_index(test_accounts, 99, True) == 1
assert _safe_account_index(test_accounts, "1", True) == 1
assert _safe_account_index(test_accounts, 0, True) == 0
assert _safe_account_index(test_accounts, 0, False) == 1

single_ui_source = (Path(__file__).resolve().parents[1] /
                    "src" / "protective_puts_ui.py").read_text(encoding="utf-8")
assert 'st.session_state["pp_protection_mode"] = "PORTFOLIO HEDGE"' in single_ui_source
assert "scan_dates = list(expirations)" in single_ui_source
assert '"raw_chains": raw_chains' in single_ui_source
assert '_pp_single_quote_snapshot' in single_ui_source
assert "run the Protective Put scan again to refresh the table" not in single_ui_source

assert '"BASKET LOSS ?"' not in scroller_html
assert "displayed integer PUT quantity (100 underlying shares per contract)" in scroller_html

app_source = (Path(__file__).resolve().parents[1] /
              "streamlit_app.py").read_text(encoding="utf-8")
assert "def _cache_all_account_holdings():" in app_source
assert "_etrade_holdings_cached_account_keys" in app_source
background = app_source.split('with st.container(key="terminal_background_hooks"', 1)[1]
assert "_cache_all_account_holdings()" in background.split('if active_tab == "HOLDINGS"', 1)[0]

print("PROTECTIVE PUTS TEST: PASS")

# OptionStrat checked-PUT links carry the exact PUT quantity/ASK and a
# rounded proxy ETF stock leg, without pretending to hold the real basket.
from src.protective_puts_portfolio_ui import _optionstrat_protective_put_url

qqq_link = _optionstrat_protective_put_url({
    "Proxy": "QQQ", "Expiration": "01-15-27", "Strike": 900.0,
    "Spot": 746.04, "Ask": 155.55, "Contracts": 1,
}, hedge_exposure=62 * 746.04)
assert qqq_link == ("https://optionstrat.com/build/protective-put/QQQ/"
                    "x62@746.04,.QQQ270115P900x1@155.55"), qqq_link
spy_link = _optionstrat_protective_put_url({
    "Proxy": "SPY", "Expiration": "12-18-26", "Strike": 550.5,
    "Spot": 600.0, "Ask": 1.25, "Contracts": 2,
})
assert spy_link.endswith("/x200@600.00,.SPY261218P550.5x2@1.25"), spy_link
assert _optionstrat_protective_put_url({
    "Proxy": "QQQ/foo", "Expiration": "01-15-27", "Strike": 900.0,
    "Spot": 746.04, "Ask": 155.55, "Contracts": 1,
}) == ""

# Durable Protective Put snapshots retain quantities, account isolation and
# true source time; malformed/older data cannot erase a good remembered book.
from src.protective_puts_holdings import clean_holdings_memory, remember_holdings

cached_accounts = [
    {"accountIdKey": "raj-cache", "accountName": "Raj Singh", "accountId": "private-number"},
    {"accountIdKey": "other-cache", "accountName": "Other"},
]
cached_row = {
    "Symbol": "AAA", "Type": "EQ", "Quantity": 10, "Last": 100,
    "Market Value": 1000, "Gain/Loss": 100, "Gain/Loss %": 11.11,
    "Total Cost": 900, "CUSIP": "", "oauth_token": "must-never-be-saved",
}
cached_memory = remember_holdings(
    {}, cached_accounts, "raj-cache", pd.DataFrame([cached_row]), 1000, 1500,
)
cached_memory = remember_holdings(
    cached_memory, cached_accounts, "other-cache",
    pd.DataFrame([{**cached_row, "Symbol": "BBB", "Quantity": 5}]), 1200, 2000,
)
assert cached_memory["holdings"]["raj-cache"]["rows"][0]["Quantity"] == 10
assert cached_memory["holdings"]["other-cache"]["rows"][0]["Symbol"] == "BBB"
assert "oauth_token" not in repr(cached_memory)
assert "private-number" not in repr(cached_memory)
older_memory = remember_holdings(
    cached_memory, cached_accounts, "raj-cache", pd.DataFrame(), 900,
)
assert older_memory == cached_memory
malformed_memory = remember_holdings(
    cached_memory, cached_accounts, "raj-cache", pd.DataFrame([{"garbage": 1}]), 1400,
)
assert malformed_memory == cached_memory
partial_memory = remember_holdings(
    cached_memory, cached_accounts, "raj-cache",
    pd.DataFrame([cached_row, {"garbage": 1}]), 1400,
)
assert partial_memory == cached_memory
same_source_memory = remember_holdings(
    cached_memory, cached_accounts, "raj-cache", pd.DataFrame([cached_row]), 1000.001,
)
assert same_source_memory == cached_memory
updated_memory = remember_holdings(
    cached_memory, cached_accounts, "raj-cache",
    pd.DataFrame([{**cached_row, "Quantity": 25, "Market Value": 2500}]), 1400,
)
assert updated_memory["holdings"]["raj-cache"]["rows"][0]["Quantity"] == 25
assert updated_memory["holdings"]["raj-cache"]["account_total"] == 1500
assert updated_memory["holdings"]["other-cache"] == cached_memory["holdings"]["other-cache"]
empty_memory = remember_holdings(
    updated_memory, cached_accounts, "raj-cache", pd.DataFrame(), 1600,
)
assert empty_memory["holdings"]["raj-cache"]["rows"] == []
assert empty_memory["holdings"]["other-cache"] == cached_memory["holdings"]["other-cache"]
assert clean_holdings_memory(None)["holdings"] == {}
assert clean_holdings_memory({"accounts": "corrupted", "holdings": []})["accounts"] == []
finite_memory = clean_holdings_memory({
    **cached_memory,
    "holdings": {"raj-cache": {"rows": [{**cached_row, "Gain/Loss": float("nan")}]}},
})
assert finite_memory["holdings"]["raj-cache"]["rows"][0]["Gain/Loss"] is None
print("PROTECTIVE PUTS DURABLE HOLDINGS: PASS")
