#!/usr/bin/env python3
"""Dependency-free simulated tests for Risk Sizing live-order helpers.

This intentionally does not import Streamlit and never creates an E*TRADE
client. It extracts only the pure payload/fill helpers from the production
Risk module, then exercises them with simulated broker responses.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RISK_UI = ROOT / "src" / "risk_sizing_ui_v10.py"

PURE_FUNCTIONS = {
    "_as_list",
    "_direct_key",
    "_find_key",
    "_collect_key_values",
    "_float_values",
    "_build_equity_preview_payload",
    "_build_place_payload",
    "_matching_order_records",
    "_order_fill_snapshot",
}


def _load_helpers() -> dict:
    tree = ast.parse(RISK_UI.read_text(encoding="utf-8"), filename=str(RISK_UI))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in PURE_FUNCTIONS
    ]
    missing = PURE_FUNCTIONS - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing production helpers: {sorted(missing)}")
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"math": math}
    exec(compile(module, str(RISK_UI), "exec"), namespace)
    return namespace


def main() -> int:
    ns = _load_helpers()
    build_preview = ns["_build_equity_preview_payload"]
    build_place = ns["_build_place_payload"]
    fill_snapshot = ns["_order_fill_snapshot"]

    buy = build_preview(
        symbol="SPY",
        quantity=10,
        action="BUY",
        price_type="LIMIT",
        limit_price=500.25,
        order_term="GOOD_FOR_DAY",
        client_order_id="RBE123",
    )
    buy_request = buy["PreviewOrderRequest"]
    buy_order = buy_request["Order"][0]
    buy_instrument = buy_order["Instrument"][0]
    assert buy_request["orderType"] == "EQ"
    assert buy_order["priceType"] == "LIMIT"
    assert buy_order["limitPrice"] == 500.25
    assert buy_order["orderTerm"] == "GOOD_FOR_DAY"
    assert buy_instrument["orderAction"] == "BUY"
    assert buy_instrument["quantity"] == 10

    placed = build_place(buy, 987654321)
    place_request = placed["PlaceOrderRequest"]
    assert place_request["PreviewIds"] == [{"previewId": 987654321}]
    assert place_request["clientOrderId"] == "RBE123"
    assert place_request["Order"] == buy_request["Order"]

    stop = build_preview(
        symbol="SPY",
        quantity=10,
        action="SELL",
        price_type="STOP",
        stop_price=475.00,
        order_term="GOOD_UNTIL_CANCEL",
        client_order_id="RPS123",
    )
    stop_order = stop["PreviewOrderRequest"]["Order"][0]
    stop_instrument = stop_order["Instrument"][0]
    assert stop_order["priceType"] == "STOP"
    assert stop_order["stopPrice"] == 475.00
    assert stop_order["orderTerm"] == "GOOD_UNTIL_CANCEL"
    assert stop_instrument["orderAction"] == "SELL"

    full_response = {
        "OrdersResponse": {
            "Order": [
                {
                    "orderId": 42,
                    "OrderDetail": [
                        {
                            "status": "EXECUTED",
                            "Instrument": [
                                {
                                    "orderedQuantity": 10,
                                    "filledQuantity": 10,
                                    "averageExecutionPrice": 499.75,
                                }
                            ],
                            "Events": {"Event": [{"name": "ORDER_EXECUTED"}]},
                        }
                    ],
                }
            ]
        }
    }
    full = fill_snapshot(full_response, 42, 10)
    assert full["found"] is True
    assert full["full"] is True
    assert full["partial"] is False
    assert full["filled"] == 10
    assert full["average_price"] == 499.75

    partial_response = {
        "OrdersResponse": {
            "Order": [
                {
                    "orderId": 43,
                    "OrderDetail": [
                        {
                            "status": "INDIVIDUAL_FILLS",
                            "Instrument": [
                                {
                                    "orderedQuantity": 10,
                                    "filledQuantity": 4,
                                    "averageExecutionPrice": 499.50,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }
    partial = fill_snapshot(partial_response, 43, 10)
    assert partial["found"] is True
    assert partial["partial"] is True
    assert partial["full"] is False
    assert partial["filled"] == 4

    missing = fill_snapshot(full_response, 999, 10)
    assert missing["found"] is False
    assert missing["full"] is False

    print("RISK LIVE ORDER SIMULATION: PASS")
    print("BUY LIMIT preview/place payload, GTC SELL STOP payload, full-fill unlock, and partial-fill block verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
