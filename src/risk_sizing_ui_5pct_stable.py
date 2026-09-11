"""Stable Risk Sizing: original ASK entry + editable 5% stop behavior.

This is the pre-auto-invalidation implementation:
- Entry Price = E*TRADE ASK
- Stop Loss = 5% below ASK
- Both remain editable after the quote pull
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v4 as _v4


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
    """Render v4 with ASK-based entry/stop defaults on each explicit quote pull."""
    # Remove any state left over from the abandoned auto-invalidation experiment.
    for key in (
        "_risk_auto_invalidation",
        "_risk_auto_invalidation_error",
        "_risk_pending_entry_ask",
        "_risk_pending_entry_symbol",
        "_risk_force_entry_reseed",
        "_risk_entry_seed_symbol",
        "_risk_entry_seed_ask",
    ):
        st.session_state.pop(key, None)

    previous_quote_summary = _v4.quote_summary
    _v4.quote_summary = _quote_summary_with_risk_defaults
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

    if st.session_state.pop("_risk_ask_unavailable", False):
        st.warning(
            "E*TRADE ASK UNAVAILABLE // Entry and Stop were not auto-reset. "
            "Pull the quote again when an ask is available or enter the values manually."
        )
