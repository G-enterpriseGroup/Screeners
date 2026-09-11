"""Fresh Risk Sizing adapter with ASK entry, editable 5% stop, commas, and V4 charts.

The new module path intentionally bypasses stale Streamlit/Python module caches
that can survive a browser refresh in a long-running worker.
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v4 as _v4
from src.stockanalysis_portfolio_v4 import render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config


_ORIGINAL_QUOTE_SUMMARY = _v4.quote_summary


def _quote_summary_with_risk_defaults(payload):
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
    previous_quote_summary = _v4.quote_summary
    original_dataframe = st.dataframe

    def comma_dataframe(data=None, *args, **kwargs):
        kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
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
        key_prefix="risk_stockanalysis_fresh",
        title="PORTFOLIO SECTOR + INDUSTRY // RISK CONTEXT",
        show_classification_table=False,
    )
