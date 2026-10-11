"""Focused single-leg put broker payload validation. No external calls."""
from datetime import date

from src.protective_puts_orders import (
    MAX_PUT_CONTRACTS, build_put_preview, build_place_payload,
    selected_put_signature,
)

leg = build_put_preview(
    symbol="SPY", expiry=date(2026, 12, 18), strike=600,
    quantity=2, limit=5.25, client_order_id="PPA123",
)["PreviewOrderRequest"]
assert leg["orderType"] == "OPTN"
assert leg["Order"][0]["priceType"] == "LIMIT"
assert leg["Order"][0]["orderTerm"] == "GOOD_FOR_DAY"
assert leg["Order"][0]["marketSession"] == "REGULAR"
assert leg["Order"][0]["limitPrice"] == 5.25
instrument = leg["Order"][0]["Instrument"][0]
assert instrument["orderAction"] == "BUY_OPEN"
assert instrument["Product"]["callPut"] == "PUT"
assert instrument["orderedQuantity"] == 2
place = build_place_payload({"PreviewOrderRequest": leg}, 123)["PlaceOrderRequest"]
assert place["PreviewIds"][0]["previewId"] == 123
assert place["clientOrderId"] == "PPA123"

selected = [
    {"Proxy":"SPY","Expiration":"12-18-26","Strike":600,"Contracts":2,"Ask":5.25},
    {"Proxy":"QQQ","Expiration":"12-18-26","Strike":500,"Contracts":1,"Ask":4.50},
]
assert selected_put_signature(selected) == selected_put_signature(list(reversed(selected)))
for bad in (0, -1, float("nan"), float("inf")):
    try:
        build_put_preview(symbol="SPY",expiry=date(2026,12,18),
                          strike=600,quantity=1,limit=bad)
        raise AssertionError("invalid premium accepted")
    except ValueError:
        pass
try:
    build_put_preview(symbol="SPY",expiry=date(2026,12,18),
                      strike=600,quantity=MAX_PUT_CONTRACTS+1,limit=1)
    raise AssertionError("oversized quantity accepted")
except ValueError:
    pass

import pandas as pd
from src.protective_puts_portfolio_ui import _selected_order_rows
candidate = pd.DataFrame([
    {"Proxy":"SPY","Expiration":"12-18-26","Strike":600,
     "Contracts":2,"Ask":5.25,"Orderable":True},
    {"Proxy":"SPY","Expiration":"12-18-26","Strike":601,
     "Contracts":250,"Ask":4.50,"Orderable":False},
])
eligible = candidate.copy()
eligible["BUY"] = [True, False]
assert len(_selected_order_rows(candidate, eligible)) == 1
eligible["BUY"] = [False, True]
try:
    _selected_order_rows(candidate, eligible)
    raise AssertionError("oversized hedge selection must not be silently ignored")
except ValueError:
    pass

print("PROTECTIVE PUT LIMIT PREVIEW VALIDATION: PASS")
