"""Focused regression tests for Protective Puts math."""

from datetime import date

from src.protective_puts import build_put_analysis, protective_put_metrics


metrics = protective_put_metrics(
    stock_price=100.0,
    strike=95.0,
    premium_per_share=2.0,
    shares=100,
)
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
assert '"FULL GHOTRA COLUMNS' in ui_source
assert "if not account_key:" in ui_source
assert "SUBMIT {len(selected)} LIVE PUT ORDER(S)" in ui_source

# Canvas editor styling must specify a legible foreground on every chain row.
from src.protective_puts_portfolio_ui import _chain_row_colors

regular = _chain_row_colors(2, False, False, 12)
best = _chain_row_colors(1, False, True, 12)
first = _chain_row_colors(0, True, True, 12)
assert len(regular) == len(best) == len(first) == 12
assert all("background-color:#080808" in cell and "color:#f6b56a" in cell
           for cell in regular), regular
assert all("background-color:#10130d" in cell and "color:#4af6c3" in cell
           for cell in best), best
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

print("PROTECTIVE PUTS TEST: PASS")
