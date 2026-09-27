"""Focused regression for the read-only Performance analytics.

Run:
    python scripts/test_performance_ui.py

This test is stdlib-only: it stubs UI/data dependencies so the production math
helpers can be imported without a browser or broker credentials.
"""

from __future__ import annotations

from datetime import date, datetime
import importlib.util
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "performance_ui.py"
ET = ZoneInfo("America/New_York")


class DummyStreamlit(types.ModuleType):
    def fragment(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator


st = DummyStreamlit("streamlit")
sys.modules["streamlit"] = st
sys.modules["pandas"] = types.ModuleType("pandas")

plotly = types.ModuleType("plotly")
graph_objects = types.ModuleType("plotly.graph_objects")
plotly.graph_objects = graph_objects
sys.modules["plotly"] = plotly
sys.modules["plotly.graph_objects"] = graph_objects

etrade = types.ModuleType("src.etrade_client")
etrade.ETradeError = RuntimeError
etrade.normalize_position = lambda value: value
etrade.total_account_value = lambda value: None
sys.modules["src.etrade_client"] = etrade

spec = importlib.util.spec_from_file_location("src.performance_ui", SOURCE)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def tx(identifier, day, action, symbol="", qty=0, price=0, fee=0, amount=None, description=""):
    payload = {
        "transactionId": identifier,
        "transactionDate": int(datetime(day.year, day.month, day.day, 12, tzinfo=ET).timestamp() * 1000),
        "transactionType": action,
        "description": description,
    }
    if amount is not None:
        payload["amount"] = amount
    if symbol:
        payload["brokerage"] = {
            "transactionType": action,
            "quantity": qty,
            "price": price,
            "fee": fee,
            "product": {"symbol": symbol, "securityType": "EQ"},
        }
    return payload


rows = [
    tx("1", date(2026, 1, 1), "BUY", "AAPL", 10, 100, 1),
    tx("2", date(2026, 1, 2), "SELL", "AAPL", 10, 110, 1),
    tx("3", date(2026, 1, 3), "BUY", "MSFT", 5, 200),
    tx("4", date(2026, 1, 4), "SELL", "MSFT", 5, 190),
    tx("5", date(2026, 1, 5), "DIVIDEND", amount=20, description="Cash dividend"),
]

events, income = module._build_trade_events(rows)
assert len(events) == 2, events
assert len(income) == 1, income
assert round(sum(row["pnl"] for row in events), 2) == 48.00, events
assert round(sum(row["amount"] for row in income), 2) == 20.00, income

metrics = module._period_metrics(events, income, date(2026, 1, 1), account_value=10_000)
assert round(metrics["net_pnl"], 2) == 68.00, metrics
assert round(metrics["win_rate"], 2) == 50.00, metrics
assert round(metrics["profit_factor"], 2) == 1.96, metrics
assert round(metrics["return_pct"], 2) == 3.40, metrics
assert round(metrics["max_drawdown_pct"], 2) == -0.50, metrics

source = SOURCE.read_text(encoding="utf-8")
for required in (
    '@st.fragment(run_every=_LIVE_REFRESH_SECONDS)',
    'columns=["PERFORMANCE", "TODAY", "MTD", "YTD", "ALL-TIME"]',
    'missing history is never invented',
    'LIVE CUMULATIVE NET P&L',
    'force_refresh=True',
):
    assert required in source, required

for forbidden in ("preview_order(", "place_order(", "cancel_order("):
    assert forbidden not in source, forbidden

print("PASS: Performance FIFO math, scorecard contract, live refresh, history guard, and read-only boundary")
