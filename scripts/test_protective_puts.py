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
print("PROTECTIVE PUTS TEST: PASS")
