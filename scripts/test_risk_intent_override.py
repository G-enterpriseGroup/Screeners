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
    _reconcile_risk_intent_state,
    _risk_intent_session_key,
    _risk_intent_storage_key,
    _risk_override_widget_key,
    _set_long_term_override,
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

    # Duplicate holdings/lots must never share one rendered Streamlit key.
    sgol_key_1 = _risk_override_widget_key("acct", "SGOL", "0")
    sgol_key_2 = _risk_override_widget_key("acct", "SGOL", "1")
    assert sgol_key_1 != sgol_key_2

    # Persistence is account-scoped without exposing the raw broker account key.
    storage_key = _risk_intent_storage_key("acct")
    assert storage_key.startswith("raj-terminal-risk-intent-v1:")
    assert "acct" not in storage_key

    # A sold ticker is the one automatic removal case: once it is absent from
    # the holdings symbol set, the persisted check mark is deleted.
    reconciled, changed = _reconcile_risk_intent_state(
        {"revision": 7, "tickers": ["SGOL", "SPY"]},
        ["SPY"],
    )
    assert changed is True
    assert reconciled == {"revision": 8, "tickers": ["SPY"]}

    original_session_state = risk_ui.st.session_state
    try:
        risk_ui.st.session_state = {}
        risk_ui._risk_intent_vault().clear()

        risk_ui.st.session_state[sgol_key_1] = True
        _set_long_term_override("acct", "SGOL", sgol_key_1)
        session_overrides = risk_ui._account_intent_overrides("acct")
        assert session_overrides["SGOL"] == RISK_INTENT_LONG_TERM
        persisted = risk_ui.st.session_state[_risk_intent_session_key("acct")]
        assert persisted["tickers"] == ["SGOL"]
        checked_revision = persisted["revision"]

        risk_ui.st.session_state[sgol_key_2] = False
        _set_long_term_override("acct", "SGOL", sgol_key_2)
        assert "SGOL" not in session_overrides
        persisted = risk_ui.st.session_state[_risk_intent_session_key("acct")]
        assert persisted["tickers"] == []
        assert persisted["revision"] > checked_revision
    finally:
        risk_ui._risk_intent_vault().clear()
        risk_ui.st.session_state = original_session_state

    # Exercise Streamlit's actual checkbox registry with duplicate SGOL lots.
    smoke_path = ROOT / "scripts" / "_tmp_risk_visible_checkbox_app.py"
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
with st.container(key="risk_book_native_grid"):
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

    # Source contract: local native grid only. This intentionally avoids the
    # canvas-rendered CheckboxColumn that disappears under the global black
    # dataframe text theme.
    source = (ROOT / "src" / "risk_sizing_ui_v2.py").read_text(encoding="utf-8")
    assert 'key="risk_book_native_grid"' in source
    assert 'st.checkbox(' in source
    assert 'border:2px solid #fb8b1e!important' in source
    assert 'label:has(input:checked)' in source
    assert 'st.data_editor(' not in source
    assert '"SLEEVE / %"' in source
    assert 'classified["_risk_row_uid"]' in source
    assert '_load_persisted_intent_overrides(' in source
    assert '_sync_risk_intent_browser(' in source

    persistence_component = (
        ROOT / "src" / "components" / "risk_intent_state_v1" / "index.html"
    ).read_text(encoding="utf-8")
    assert "localStorage.getItem" in persistence_component
    assert "localStorage.setItem" in persistence_component

    # Risk Book visual contract: use the same compact typography/rhythm tokens
    # as the production v9 Risk interface instead of ad-hoc tiny table text.
    assert '--risk-book-row-height:48px' in source
    assert '--risk-book-font-size:1.02rem' in source
    assert '--risk-book-cell-pad:8px' in source
    assert 'font-size:var(--risk-book-font-size)' in source
    assert 'grid_spec = [0.40, 0.68, 0.50, 0.42, 0.49, 0.57, 0.38]' in source
    assert 'st.columns(grid_spec, gap=None, vertical_alignment="center")' in source
    assert 'width="stretch"' in source

    print("risk long-term intent override + visible checkbox: PASS")


if __name__ == "__main__":
    main()
