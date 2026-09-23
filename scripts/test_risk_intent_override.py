"""Regression checks for E*TRADE Risk Book one-off LONG-TERM intent overrides."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.risk_sizing import classify_holdings, sleeve_summary
import src.risk_sizing_ui_v2 as risk_ui
from src.risk_sizing_ui_v2 import (
    RISK_INTENT_AUTO,
    RISK_INTENT_LONG_TERM,
    _apply_intent_overrides,
    _apply_risk_editor_changes,
    _risk_editor_key,
)


def main() -> None:
    holdings = pd.DataFrame(
        [
            {
                "Symbol": "GLD",
                "CUSIP": "",
                "Type": "ETF",
                "Gain/Loss %": -5.0,
                "Gain/Loss": -500.0,
                "Market Value": 10_000.0,
            },
            {
                "Symbol": "SPY",
                "CUSIP": "",
                "Type": "ETF",
                "Gain/Loss %": 8.0,
                "Gain/Loss": 800.0,
                "Market Value": 20_000.0,
            },
        ]
    )

    classified = classify_holdings(holdings, 5.0)
    by_symbol = classified.set_index("Symbol")
    assert by_symbol.loc["GLD", "Sleeve"] == "TACTICAL"
    assert by_symbol.loc["SPY", "Sleeve"] == "LONG-TERM"

    overrides = {"GLD": RISK_INTENT_LONG_TERM}
    adjusted = _apply_intent_overrides(classified, overrides)
    adjusted_by_symbol = adjusted.set_index("Symbol")
    assert adjusted_by_symbol.loc["GLD", "Intent"] == RISK_INTENT_LONG_TERM
    assert adjusted_by_symbol.loc["GLD", "Sleeve"] == "LONG-TERM"
    assert adjusted_by_symbol.loc["GLD", "Sleeve Rule"] == "MANUAL LONG-TERM INTENT"
    assert adjusted_by_symbol.loc["SPY", "Intent"] == RISK_INTENT_AUTO

    summary = sleeve_summary(adjusted, investable_assets=100_000.0, tactical_sleeve_pct=15.0)
    assert summary["tactical_value"] == 0.0
    assert summary["long_term_value"] == 30_000.0
    assert summary["target_room"] == 15_000.0

    # The integrated editor callback must update ticker-level intent before the
    # full Streamlit rerun, then rotate the widget key so stale row edits cannot
    # be applied to a different holding.
    original_session_state = risk_ui.st.session_state
    try:
        risk_ui.st.session_state = {}
        editor_key = _risk_editor_key("acct")
        risk_ui.st.session_state[editor_key] = {
            "edited_rows": {0: {"Long-Term?": True}}
        }
        _apply_risk_editor_changes("acct", editor_key, ("SGOL", "SGOL"))
        session_overrides = risk_ui._account_intent_overrides("acct")
        assert session_overrides["SGOL"] == RISK_INTENT_LONG_TERM

        next_editor_key = _risk_editor_key("acct")
        assert next_editor_key != editor_key

        # Duplicate SGOL lots intentionally share ticker-level intent. Unchecking
        # either displayed row removes the override for both lots.
        risk_ui.st.session_state[next_editor_key] = {
            "edited_rows": {1: {"Long-Term?": False}}
        }
        _apply_risk_editor_changes("acct", next_editor_key, ("SGOL", "SGOL"))
        assert "SGOL" not in session_overrides
        assert _risk_editor_key("acct") != next_editor_key
    finally:
        risk_ui.st.session_state = original_session_state

    # Source-level layout contract: one integrated table/editor, no detached
    # checkbox strip, and Sleeve + % Tactical Sleeve are presented together.
    source = (ROOT / "src" / "risk_sizing_ui_v2.py").read_text(encoding="utf-8")
    assert "risk_book_grid" not in source
    assert "risk_book_override_controls" not in source
    assert 'st.data_editor(' in source
    assert 'on_change=_apply_risk_editor_changes' in source
    assert '"Sleeve / % Tactical"' in source
    assert '"SLEEVE / % TACTICAL"' in source
    assert 'row_height=35' in source

    print("risk long-term intent override: PASS")


if __name__ == "__main__":
    main()
