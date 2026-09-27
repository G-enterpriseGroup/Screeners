"""Connected-state Streamlit smoke test for Smart Rebalance Portfolio.

Run:
    python scripts/test_rebalance_connected_ui.py

Uses only simulated E*TRADE holdings/balance data. It never creates a broker client
and never invokes order preview/place/cancel methods.
"""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


APP_SOURCE = r'''
from src.rebalance_portfolio_ui import render_rebalance_portfolio

ACCOUNT = {
    "accountIdKey": "SIMULATED-RAJ-ACCOUNT",
    "accountId": "00005474",
    "accountName": "RAJ TEST",
}

class SimulatedClient:
    def get_portfolio(self, account_key):
        assert account_key == ACCOUNT["accountIdKey"]
        return [
            {
                "Product": {"symbol": "NVDA", "securityType": "EQ"},
                "quantity": 100.0,
                "lastTrade": 1500.0,
                "marketValue": 150000.0,
                "totalCost": 100000.0,
                "totalGain": 50000.0,
                "totalGainPct": 50.0,
                "pctOfPortfolio": 15.0,
            },
            {
                "Product": {"symbol": "GLD", "securityType": "EQ"},
                "quantity": 1000.0,
                "lastTrade": 50.0,
                "marketValue": 50000.0,
                "totalCost": 51000.0,
                "totalGain": -1000.0,
                "totalGainPct": -1.960784,
                "pctOfPortfolio": 5.0,
            },
        ]

client = SimulatedClient()

def account_picker(_key):
    return ACCOUNT

def refresh_accounts(_client):
    return [ACCOUNT]

def account_balance(_client, _account, refresh=False):
    return {"simulated": True, "refresh": bool(refresh)}

def balance_snapshot(_payload):
    return 1_000_000.0, 100_000.0, 900_000.0

def touch_session():
    return None

render_rebalance_portfolio(
    client,
    account_picker=account_picker,
    refresh_accounts=refresh_accounts,
    account_balance=account_balance,
    balance_snapshot=balance_snapshot,
    touch_session=touch_session,
)
'''


def main() -> None:
    app = AppTest.from_string(APP_SOURCE, default_timeout=60)
    # Components have no browser in AppTest. Prove the initial restore gate,
    # then provide explicit hydration/save acknowledgements like the real bridge.
    app.run()
    assert any("Restoring saved" in item.value for item in app.info)
    saved = {}
    def bridge(**kwargs):
        key = kwargs["storage_key"]
        if kwargs.get("read_only"):
            return {"ready": True, "available": True, "state": saved.get(key)}
        saved[key] = kwargs["server_state"]
        return {"ready": True, "available": True, "saved_revision": saved[key]["revision"]}
    with patch("src.rebalance_portfolio_ui._rebalance_state_component", side_effect=bridge):
        app.run()
    assert not app.exception, [item.message for item in app.exception]

    labels = {button.label for button in app.button}
    for expected in (
        "REFRESH REBALANCER",
        "RESET TARGETS TO CURRENT",
        "APPLY DEFAULT BANDS",
    ):
        assert expected in labels, (expected, sorted(labels))

    html = "\n".join(item.value for item in app.get("html"))
    for expected in (
        "ACCOUNT + LIVE BOOK",
        "SMART RULES",
        "TARGETS + BANDS",
        "SMART REBALANCE PLAN",
        "ACCOUNT VALUE",
        "CASH AVAILABLE",
        "ANALYSIS ONLY",
    ):
        assert expected in html, expected

    # Both target rules and the resulting plan use feature-local Risk tables.
    assert 'class="reb-table"' in html, "rebalance plan table missing"
    with patch("src.rebalance_portfolio_ui._rebalance_state_component", side_effect=bridge):
        band = next(item for item in app.number_input if item.label == "Band ± percentage points")
        band.set_value(3.5).run()
        assert not app.exception
        assert next(row for row in next(iter(saved.values()))["rows"] if row["symbol"] == "NVDA")["band_pct"] == 3.5
        # Consecutive settings changes must not be undone by revision hydration.
        next(item for item in app.number_input if item.label == "Minimum Rebalance Trade $").set_value(1200.0).run()
        next(item for item in app.number_input if item.label == "Loss Review Trigger %").set_value(-15.0).run()
        assert not app.exception
        state = next(iter(saved.values()))
        assert state["settings"]["min_trade"] == 1200.0
        assert state["settings"]["loss_review_trigger"] == -15.0
        restored = AppTest.from_string(APP_SOURCE, default_timeout=60).run()
        assert not restored.exception
        assert next(item for item in restored.number_input if item.label == "Band ± percentage points").value == 3.5
        assert next(item for item in restored.number_input if item.label == "Minimum Rebalance Trade $").value == 1200.0


    print("REBALANCE CONNECTED UI SMOKE: PASS")


if __name__ == "__main__":
    main()
