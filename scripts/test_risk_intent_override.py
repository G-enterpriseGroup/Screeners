"""Regression checks for E*TRADE Risk Book one-off LONG-TERM intent overrides."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.risk_sizing import classify_holdings, sleeve_summary
import src.risk_sizing_ui_v2 as risk_ui
from src.risk_sizing_ui_v2 import (
    RISK_INTENT_AUTO,
    RISK_INTENT_LONG_TERM,
    _apply_intent_overrides,
    _risk_override_widget_key,
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

    # Duplicate-symbol rows/lots must produce distinct widget keys.
    sgol_key_1 = _risk_override_widget_key("acct", "SGOL", "0")
    sgol_key_2 = _risk_override_widget_key("acct", "SGOL", "1")
    assert sgol_key_1 != sgol_key_2

    original_session_state = risk_ui.st.session_state
    try:
        risk_ui.st.session_state = {}
        risk_ui.st.session_state[sgol_key_1] = True
        risk_ui._set_long_term_override("acct", "SGOL", sgol_key_1)
        session_overrides = risk_ui._account_intent_overrides("acct")
        assert session_overrides["SGOL"] == RISK_INTENT_LONG_TERM

        # A second duplicate row reflects the same ticker-level intent without
        # sharing the same widget key.
        risk_ui.st.session_state[sgol_key_2] = True
        assert bool(risk_ui.st.session_state[sgol_key_2]) is True

        risk_ui.st.session_state[sgol_key_1] = False
        risk_ui._set_long_term_override("acct", "SGOL", sgol_key_1)
        assert "SGOL" not in session_overrides
    finally:
        risk_ui.st.session_state = original_session_state

    # Exercise Streamlit's real widget registry with two SGOL rows. This catches
    # the duplicate-element-key crash that PR #39 introduced and verifies that
    # one click synchronizes both duplicate lots without a rerun loop.
    smoke_path = ROOT / "scripts" / "_tmp_risk_duplicate_checkbox_app.py"
    smoke_path.write_text(
        """from __future__ import annotations
import streamlit as st
from src.risk_sizing_ui_v2 import (
    RISK_INTENT_LONG_TERM,
    _account_intent_overrides,
    _risk_override_widget_key,
    _set_long_term_override,
)

account_key = "acct"
overrides = _account_intent_overrides(account_key)
for row_uid in ("0", "1"):
    symbol = "SGOL"
    key = _risk_override_widget_key(account_key, symbol, row_uid)
    selected = str(overrides.get(symbol) or "").upper() == RISK_INTENT_LONG_TERM
    if st.session_state.get(key) != selected:
        st.session_state[key] = selected
    st.checkbox(
        f"{symbol} {row_uid}",
        key=key,
        on_change=_set_long_term_override,
        args=(account_key, symbol, key),
    )
""",
        encoding="utf-8",
    )
    try:
        app = AppTest.from_file(str(smoke_path), default_timeout=10)
        app.run()
        assert not app.exception
        assert len(app.checkbox) == 2
        assert app.checkbox[0].value is False
        assert app.checkbox[1].value is False

        app.checkbox[0].check().run()
        assert not app.exception
        assert app.checkbox[0].value is True
        assert app.checkbox[1].value is True

        app.checkbox[1].uncheck().run()
        assert not app.exception
        assert app.checkbox[0].value is False
        assert app.checkbox[1].value is False
    finally:
        smoke_path.unlink(missing_ok=True)

    print("risk long-term intent override: PASS")


if __name__ == "__main__":
    main()
