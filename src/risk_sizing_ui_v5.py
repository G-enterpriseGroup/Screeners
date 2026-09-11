"""Risk-sizing UI v5: ask-price entry defaults with editable 5% stop.

This adapter keeps the full v4 Risk Sizing interface, but makes the quote pull
initialize STOCK / ETF sizing from an executable-side reference:

- Entry Price = E*TRADE ASK
- Stop Loss = 5% below that ASK

Those values are only reset when the user explicitly pulls a quote. After the
quote loads, both fields remain normal editable Streamlit inputs.
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v4 as _v4
from src.stockanalysis_portfolio_v3 import render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config


_ORIGINAL_QUOTE_SUMMARY = _v4.quote_summary


def _quote_summary_with_risk_defaults(payload):
    """Normalize the E*TRADE quote and seed the editable sizing inputs."""
    summary = _ORIGINAL_QUOTE_SUMMARY(payload)

    try:
        ask = float(summary.get("ask") or 0.0)
    except (TypeError, ValueError):
        ask = 0.0

    if ask > 0:
        st.session_state["risk_entry_price"] = round(ask, 2)
        st.session_state["risk_stop_price"] = round(ask * 0.95, 2)
        st.session_state["_risk_entry_source"] = "E*TRADE ASK"
        st.session_state.pop("_risk_ask_unavailable", None)
    else:
        st.session_state["_risk_ask_unavailable"] = True

    return summary


def render_risk_sizing(
    client,
    *,
    account_picker: Callable[[str], dict[str, Any] | None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
    touch_session: Callable[[], None],
) -> None:
    """Render v4 with ASK defaults and comma-formatted numeric tables."""
    previous_quote_summary = _v4.quote_summary
    original_dataframe = st.dataframe

    def comma_dataframe(data=None, *args, **kwargs):
        kwargs["column_config"] = comma_column_config(
            data,
            kwargs.get("column_config"),
        )
        return original_dataframe(data, *args, **kwargs)

    _v4.quote_summary = _quote_summary_with_risk_defaults
    st.dataframe = comma_dataframe
    try:
        _v4.render_risk_sizing(
            client,
            account_picker=account_picker,
            refresh_accounts=refresh_accounts,
            account_balance=account_balance,
            balance_snapshot=balance_snapshot,
            touch_session=touch_session,
        )
    finally:
        _v4.quote_summary = previous_quote_summary
        st.dataframe = original_dataframe

    if st.session_state.pop("_risk_ask_unavailable", False):
        st.warning(
            "E*TRADE ASK UNAVAILABLE // Entry and Stop were not auto-reset. "
            "Pull the quote again when an ask is available or enter the values manually."
        )

    render_stockanalysis_portfolio(
        "risk_sizing_account",
        key_prefix="risk_stockanalysis",
        title="PORTFOLIO SECTOR + INDUSTRY // RISK CONTEXT",
        show_classification_table=False,
    )
