"""Regression checks for E*TRADE Risk Book one-off LONG-TERM intent overrides."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.risk_sizing import (
    classify_holdings,
    is_protected_long_term_holding,
    sleeve_summary,
)
import src.risk_sizing_ui_v2 as risk_ui
from src.risk_sizing_ui_v2 import (
    RISK_INTENT_AUTO,
    RISK_INTENT_LONG_TERM,
    _apply_intent_overrides,
    _reconcile_risk_intent_state,
    _reconcile_visible_long_term_widget_state,
    _risk_intent_session_key,
    _risk_intent_storage_key,
    _risk_override_widget_key,
    _set_long_term_override,
    _sort_risk_book_view,
    _risk_book_export_frame,
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

    protected = pd.DataFrame(
        [
            {
                "Symbol": "337158EJ4",
                "CUSIP": "",
                "Type": "EQ",
                "Gain/Loss %": -25.0,
                "Gain/Loss": -2500.0,
                "Market Value": 10_000.0,
            },
            {
                "Symbol": "MUNIROW",
                "CUSIP": "107431KW7",
                "Type": "",
                "Gain/Loss %": -50.0,
                "Gain/Loss": -5000.0,
                "Market Value": 10_000.0,
            },
        ]
    )
    protected_classified = classify_holdings(protected, 5.0).set_index("Symbol")
    assert protected_classified.loc["337158EJ4", "Sleeve"] == "LONG-TERM"
    assert protected_classified.loc["337158EJ4", "Sleeve Rule"] == "CUSIP / FIXED INCOME // PROTECTED"
    assert protected_classified.loc["MUNIROW", "Sleeve"] == "LONG-TERM"
    assert is_protected_long_term_holding(protected.iloc[0])
    assert is_protected_long_term_holding(protected.iloc[1])
    assert not is_protected_long_term_holding(
        {"Symbol": "NVDA", "CUSIP": "", "Type": "EQ"}
    )

    protected_view = classify_holdings(protected, 5.0)
    protected_view["Intent"] = RISK_INTENT_AUTO
    protected_view["% Tactical Sleeve"] = float("nan")
    protected_view["% Account"] = [10.0, 10.0]
    protected_view["_risk_sort_rank"] = [0, 0]
    protected_view["_risk_row_uid"] = ["p0", "p1"]
    protected_export = _risk_book_export_frame(protected_view)
    assert protected_export["LONG-TERM"].tolist() == ["YES", "YES"]
    protected_sorted = _sort_risk_book_view(protected_view, "LONG-TERM", "DESC")
    assert protected_sorted["Symbol"].tolist() == ["337158EJ4", "MUNIROW"]

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

    # User sorting must preserve the source row UID/widget identity and the
    # ticker-level LONG-TERM intent that is persisted separately from row order.
    sort_view = adjusted.copy()
    sort_view["_risk_sort_rank"] = [0, 1]
    sort_view["_risk_row_uid"] = ["0", "1"]
    sort_view["% Account"] = [10.0, 20.0]
    sort_view["% Tactical Sleeve"] = [float("nan"), float("nan")]
    by_value = _sort_risk_book_view(sort_view, "VALUE", "DESC")
    assert by_value["Symbol"].tolist() == ["SPY", "GLD"]
    assert by_value.set_index("Symbol").loc["GLD", "_risk_row_uid"] == "0"
    by_long_term = _sort_risk_book_view(sort_view, "LONG-TERM", "DESC")
    assert by_long_term.iloc[0]["Symbol"] == "GLD"
    export_frame = _risk_book_export_frame(by_value)
    assert export_frame.columns.tolist() == [
        "LONG-TERM", "SLEEVE / %", "SYMBOL", "P&L %", "P&L", "VALUE", "% ACCT"
    ]
    assert export_frame.loc[export_frame["SYMBOL"] == "GLD", "LONG-TERM"].iloc[0] == "YES"

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

    # A visible checked native checkbox is the final UI source of truth for the
    # current rerun. If callback/browser timing ever leaves account_overrides
    # stale, reconciliation must repair the override before classification and
    # sleeve-summary math are rendered.
    original_session_state = risk_ui.st.session_state
    try:
        risk_ui.st.session_state = {}
        risk_ui._risk_intent_vault().clear()

        glt_key = _risk_override_widget_key("acct", "GLD", "0")
        risk_ui.st.session_state[glt_key] = True
        live_overrides = risk_ui._account_intent_overrides("acct")
        intent_state = {"revision": 0, "tickers": []}
        live_overrides, intent_state = _reconcile_visible_long_term_widget_state(
            "acct",
            holdings,
            live_overrides,
            intent_state,
        )
        assert live_overrides["GLD"] == RISK_INTENT_LONG_TERM
        assert intent_state["tickers"] == ["GLD"]

        repaired = _apply_intent_overrides(
            classify_holdings(holdings, 5.0),
            live_overrides,
        )
        repaired_summary = sleeve_summary(
            repaired,
            investable_assets=100_000.0,
            tactical_sleeve_pct=15.0,
        )
        repaired_by_symbol = repaired.set_index("Symbol")
        assert repaired_by_symbol.loc["GLD", "Sleeve"] == "LONG-TERM"
        assert repaired_summary["tactical_value"] == 0.0
        assert repaired_summary["long_term_value"] == 30_000.0

        # The reverse mismatch must self-heal too: an unchecked visible widget
        # removes a stale manual override and restores the normal classifier.
        risk_ui.st.session_state[glt_key] = False
        live_overrides, intent_state = _reconcile_visible_long_term_widget_state(
            "acct",
            holdings,
            live_overrides,
            intent_state,
        )
        assert "GLD" not in live_overrides
        assert intent_state["tickers"] == []
        restored = _apply_intent_overrides(
            classify_holdings(holdings, 5.0),
            live_overrides,
        ).set_index("Symbol")
        assert restored.loc["GLD", "Sleeve"] == "TACTICAL"

        # Duplicate-lot widgets can briefly disagree on the click rerun. Never
        # let an older sibling widget reverse the callback's ticker-level choice.
        dup = pd.DataFrame(
            [
                {
                    "Symbol": "SGOL",
                    "CUSIP": "",
                    "Type": "ETF",
                    "Gain/Loss %": -1.0,
                    "Gain/Loss": -10.0,
                    "Market Value": 1_000.0,
                },
                {
                    "Symbol": "SGOL",
                    "CUSIP": "",
                    "Type": "ETF",
                    "Gain/Loss %": -2.0,
                    "Gain/Loss": -20.0,
                    "Market Value": 2_000.0,
                },
            ]
        )
        dup_overrides = {"SGOL": RISK_INTENT_LONG_TERM}
        risk_ui.st.session_state[_risk_override_widget_key("acct", "SGOL", "0")] = True
        risk_ui.st.session_state[_risk_override_widget_key("acct", "SGOL", "1")] = False
        dup_overrides, _ = _reconcile_visible_long_term_widget_state(
            "acct",
            dup,
            dup_overrides,
            {"revision": 5, "tickers": ["SGOL"]},
        )
        assert dup_overrides["SGOL"] == RISK_INTENT_LONG_TERM

        # Protected CUSIP/fixed-income rows stay code-level LONG-TERM and must
        # not be converted into a manual browser intent just because their
        # disabled checkbox is visibly checked.
        protected_key = _risk_override_widget_key("acct", "337158EJ4", "0")
        risk_ui.st.session_state[protected_key] = True
        protected_overrides: dict[str, str] = {}
        protected_overrides, protected_state = _reconcile_visible_long_term_widget_state(
            "acct",
            protected.iloc[[0]].reset_index(drop=True),
            protected_overrides,
            {"revision": 0, "tickers": []},
        )
        assert protected_overrides == {}
        assert protected_state == {"revision": 0, "tickers": []}
    finally:
        risk_ui._risk_intent_vault().clear()
        risk_ui.st.session_state = original_session_state

    # Risk editable values must be re-applied after the browser hydration
    # handshake, even if Streamlit rendered widget defaults on the first boot pass.
    original_session_state = risk_ui.st.session_state
    try:
        risk_ui.st.session_state = {}
        snapshot = {
            "revision": 12,
            "saved_at": 12345.0,
            "settings": {
                "risk_gain_threshold": 7.5,
                "risk_entry_price": 411.25,
                "risk_stop_price": 390.0,
                "risk_liquid_balance": 24500.0,
                "risk_ticker": "SPY",
                "risk_capital_source": "USE TACTICAL ROOM",
            },
        }
        risk_ui._restore_risk_book_settings(snapshot)
        assert risk_ui.st.session_state["risk_gain_threshold"] == 7.5
        assert risk_ui.st.session_state["risk_entry_price"] == 411.25
        assert risk_ui.st.session_state["risk_stop_price"] == 390.0
        assert risk_ui.st.session_state["risk_liquid_balance"] == 24500.0
        assert risk_ui.st.session_state["risk_ticker"] == "SPY"
        assert risk_ui.st.session_state["risk_capital_source_tactical"] is True

        # A newer autosaved Risk snapshot in the same live session must never
        # overwrite a ticker/value the user just edited.
        risk_ui.st.session_state["risk_gain_threshold"] = 9.0
        risk_ui.st.session_state["risk_ticker"] = "NVDA"
        newer_snapshot = {
            **snapshot,
            "revision": 13,
            "saved_at": 12346.0,
        }
        risk_ui._restore_risk_book_settings(newer_snapshot)
        assert risk_ui.st.session_state["risk_gain_threshold"] == 9.0
        assert risk_ui.st.session_state["risk_ticker"] == "NVDA"
    finally:
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
    assert 'protected_long_term = is_protected_long_term_holding(source_row)' in source
    assert 'disabled=protected_long_term or not bool(symbol)' in source
    assert 'border:2px solid #fb8b1e!important' in source
    assert 'label:has(input:checked)' in source
    assert 'st.data_editor(' not in source
    assert '"SLEEVE / %"' in source
    assert 'classified["_risk_row_uid"]' in source
    assert '_load_persisted_intent_overrides(' in source
    assert '_sync_risk_intent_browser(' in source
    assert 'mode="read"' in source
    assert 'mode="write"' in source
    assert '_RISK_BOOK_BROWSER_HYDRATED_KEY' in source
    assert '_risk_intent_hydrated_key' in source
    assert 'key="risk_book_sort"' in source
    assert 'key="risk_book_sort_direction"' in source
    assert 'key="risk_book_export_csv"' in source
    assert 'st.download_button(' in source

    persistence_component = (
        ROOT / "src" / "components" / "risk_intent_state_v1" / "index.html"
    ).read_text(encoding="utf-8")
    assert "localStorage.getItem" in persistence_component
    assert "localStorage.setItem" in persistence_component
    assert "const BACKUPS = 4" in persistence_component
    assert 'mode==="read"' in persistence_component
    assert 'if(!Boolean(args.hydrated))' in persistence_component
    assert '::backup:' in persistence_component

    risk_book_component = (
        ROOT / "src" / "components" / "risk_book_state_v1" / "index.html"
    ).read_text(encoding="utf-8")
    assert "const BACKUPS = 4" in risk_book_component
    assert 'mode==="read"' in risk_book_component
    assert 'if(!Boolean(args.hydrated))' in risk_book_component
    assert '::backup:' in risk_book_component

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
