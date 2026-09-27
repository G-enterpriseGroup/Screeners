"""Focused regression checks for the read-only Smart Rebalance Portfolio."""

from pathlib import Path

import pandas as pd

from src.rebalance_portfolio_ui import _build_rebalance_plan


ROOT = Path(__file__).resolve().parents[1]


def _settings(**overrides):
    base = {
        "mode": "TO BAND",
        "tactical_band": 2.0,
        "long_term_band": 4.0,
        "min_trade": 500.0,
        "loss_review_trigger": -10.0,
        "hard_max_pct": 25.0,
    }
    base.update(overrides)
    return base


def main() -> None:
    holdings = pd.DataFrame(
        [
            {"Symbol": "NVDA", "Sleeve": "TACTICAL", "Market Value": 150_000.0, "Gain/Loss %": 35.0},
            {"Symbol": "MSFT", "Sleeve": "TACTICAL", "Market Value": 100_000.0, "Gain/Loss %": 12.0},
            {"Symbol": "AMZN", "Sleeve": "TACTICAL", "Market Value": 70_000.0, "Gain/Loss %": -15.0},
            {"Symbol": "GLD", "Sleeve": "LONG-TERM", "Market Value": 50_000.0, "Gain/Loss %": -2.0},
        ]
    )
    config = [
        {"symbol": "NVDA", "target_pct": 10.0, "band_pct": 2.0},
        {"symbol": "MSFT", "target_pct": 10.0, "band_pct": 2.0},
        {"symbol": "AMZN", "target_pct": 10.0, "band_pct": 2.0},
        {"symbol": "GLD", "target_pct": 10.0, "band_pct": 2.0},
    ]
    plan, summary = _build_rebalance_plan(
        holdings,
        account_value=1_000_000.0,
        cash_available=100_000.0,
        config_rows=config,
        settings=_settings(),
    )
    by_symbol = plan.set_index("Symbol")

    assert by_symbol.loc["NVDA", "Action"] == "TRIM"
    assert abs(float(by_symbol.loc["NVDA", "Proposed $"]) + 31_000.0) < 1e-6
    assert by_symbol.loc["MSFT", "Action"] == "HOLD"
    assert by_symbol.loc["AMZN", "Action"] == "REVIEW LOSS"
    assert float(by_symbol.loc["AMZN", "Proposed $"]) == 0.0
    assert by_symbol.loc["GLD", "Action"] == "ADD"
    assert abs(float(by_symbol.loc["GLD", "Proposed $"]) - 31_000.0) < 1e-6
    assert abs(summary["trim_proceeds"] - 31_000.0) < 1e-6
    assert abs(summary["proposed_buys"] - 31_000.0) < 1e-6

    long_term = pd.DataFrame(
        [{"Symbol": "AAPL", "Sleeve": "LONG-TERM", "Market Value": 160_000.0, "Gain/Loss %": 50.0}]
    )
    review, _ = _build_rebalance_plan(
        long_term,
        account_value=1_000_000.0,
        cash_available=0.0,
        config_rows=[{"symbol": "AAPL", "target_pct": 10.0, "band_pct": 4.0}],
        settings=_settings(hard_max_pct=25.0),
    )
    assert review.iloc[0]["Action"] == "LONG-TERM TRIM REVIEW"

    tiny = pd.DataFrame(
        [{"Symbol": "SPY", "Sleeve": "TACTICAL", "Market Value": 100_300.0, "Gain/Loss %": 5.0}]
    )
    held, _ = _build_rebalance_plan(
        tiny,
        account_value=1_000_000.0,
        cash_available=0.0,
        config_rows=[{"symbol": "SPY", "target_pct": 10.0, "band_pct": 0.0}],
        settings=_settings(min_trade=500.0),
    )
    assert held.iloc[0]["Action"] == "HOLD // BELOW MIN"
    assert float(held.iloc[0]["Proposed $"]) == 0.0

    source = (ROOT / "src" / "rebalance_portfolio_ui.py").read_text(encoding="utf-8")
    for forbidden in ("preview_order(", "place_order(", "cancel_order("):
        assert forbidden not in source, forbidden
    for required in (
        'account_picker("risk_sizing_account")',
        "_load_persisted_intent_overrides",
        "RESET TARGETS TO CURRENT",
        "APPLY DEFAULT BANDS",
        "REVIEW LOSS",
        "LONG-TERM TRIM REVIEW",
        "ANALYSIS ONLY",
    ):
        assert required in source, required

    print("REBALANCE PORTFOLIO TESTS: PASS")


if __name__ == "__main__":
    main()
