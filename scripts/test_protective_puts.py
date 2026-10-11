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

print("PROTECTIVE PUTS TEST: PASS")
