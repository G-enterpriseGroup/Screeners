"""Risk-sizing UI v5: ASK entry defaults, editable 5% stop, commas, cached exposure maps."""

from __future__ import annotations

import re
from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v4 as _v4
from src.stockanalysis_portfolio_v5 import cache_status, render_stockanalysis_portfolio
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


def _unused_risk_metric_box(base_metric_box):
    """Add unused-risk percentage beside the dollar amount without changing sizing math."""
    money_pattern = re.compile(r"\$([0-9,]+(?:\.\d+)?)")

    def wrapped(container, label, value, tone="neutral", detail="", help_text=""):
        if str(label).strip().upper() == "UNUSED RISK":
            try:
                unused_match = money_pattern.search(str(value))
                budget_match = re.search(
                    r"Max Dollar Risk\s+\$([0-9,]+(?:\.\d+)?)",
                    str(help_text),
                    flags=re.IGNORECASE,
                )
                if unused_match and budget_match:
                    unused = float(unused_match.group(1).replace(",", ""))
                    budget = float(budget_match.group(1).replace(",", ""))
                    if budget > 0:
                        unused_pct = unused / budget * 100.0
                        value = f"{value} // {unused_pct:.2f}%"
                        help_text = (
                            f"{help_text} UNUSED RISK % = {unused:,.2f} / {budget:,.2f} x 100 = "
                            f"{unused_pct:.2f}% of the selected risk budget."
                        )
            except (TypeError, ValueError):
                pass
        return base_metric_box(container, label, value, tone, detail, help_text)

    return wrapped


@st.fragment
def render_risk_sizing(
    client,
    *,
    account_picker: Callable[[str], dict[str, Any] | None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
    touch_session: Callable[[], None],
) -> None:
    """Render Risk Sizing as an isolated fragment.

    Streamlit controls inside this page now rerun only Risk Sizing instead of
    rebuilding the entire terminal shell, connection banner, navigation, and
    other tabs. Explicit actions still update the same shared session/cache.
    """
    previous_quote_summary = _v4.quote_summary
    previous_metric_box = _v4._metric_box
    original_dataframe = st.dataframe

    def comma_dataframe(data=None, *args, **kwargs):
        kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
        return original_dataframe(data, *args, **kwargs)

    _v4.quote_summary = _quote_summary_with_risk_defaults
    _v4._metric_box = _unused_risk_metric_box(previous_metric_box)
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
        _v4._metric_box = previous_metric_box
        st.dataframe = original_dataframe

    if st.session_state.pop("_risk_ask_unavailable", False):
        st.warning(
            "E*TRADE ASK UNAVAILABLE // Entry and Stop were not auto-reset. "
            "Pull the quote again when an ask is available or enter the values manually."
        )

    render_stockanalysis_portfolio(
        "risk_sizing_account",
        key_prefix="risk_stockanalysis_cached",
        title="PORTFOLIO SECTOR + INDUSTRY // RISK CONTEXT",
        show_classification_table=False,
    )
    cache = cache_status()
    st.caption(
        "CLASSIFICATION CACHE // LAST-KNOWN-GOOD FALLBACK ENABLED // "
        f"SECTOR/INDUSTRY {cache['classifications']:,} TICKERS // "
        f"ETF LOOK-THROUGHS {cache['lookthroughs']:,}"
    )
