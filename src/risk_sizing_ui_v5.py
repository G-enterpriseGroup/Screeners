"""Risk-sizing UI v5: ASK entry defaults, editable 5% stop, commas, cached exposure maps."""

from __future__ import annotations

import re
from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v4 as _v4
from src.stockanalysis_portfolio_v5 import cache_status, render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config
from src.ticker_autocomplete import company_name, record_lookup, smart_ticker_selector


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

    symbol = str(summary.get("symbol") or st.session_state.get("risk_ticker") or "").strip().upper()
    if symbol:
        description = str(summary.get("description") or company_name(symbol) or "").strip()
        record_lookup(symbol, description)
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


def _render_stop_loss_css() -> None:
    """Bloomberg-style live stop-distance badge and arrow steppers."""
    st.markdown(
        """
        <style>
        .risk-stop-live-head {
            display:flex;
            flex-wrap:wrap;
            align-items:center;
            justify-content:space-between;
            gap:.30rem .55rem;
            min-width:0;
            max-width:100%;
            margin:0 0 .18rem 0;
            font-family:"Courier New",monospace;
        }
        .risk-stop-live-label {
            color:#fb8b1e !important;
            font-size:1rem;
            line-height:1.05;
            min-width:0;
            overflow-wrap:anywhere;
        }
        .risk-stop-live-pct {
            border:1px solid #fb8b1e;
            background:#050505;
            padding:.12rem .42rem;
            margin-left:auto;
            max-width:100%;
            font-size:.66rem;
            font-weight:900;
            line-height:1.05;
            white-space:normal;
            overflow-wrap:anywhere;
            text-align:right;
        }

        /* Convert only the Stop Loss number-input +/- controls into down/up arrows. */
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) {
            min-width:0 !important;
            max-width:100% !important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button > * {
            display:none !important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button {
            position:relative !important;
            min-width:34px !important;
            flex:0 0 34px !important;
            color:#fb8b1e !important;
            background:#050505 !important;
            border-color:#fb8b1e !important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:first-of-type::after {
            content:"▼";
            color:#fb8b1e !important;
            font-size:.82rem;
            font-weight:900;
            position:absolute;
            inset:0;
            display:flex;
            align-items:center;
            justify-content:center;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:last-of-type::after {
            content:"▲";
            color:#4af6c3 !important;
            font-size:.82rem;
            font-weight:900;
            position:absolute;
            inset:0;
            display:flex;
            align-items:center;
            justify-content:center;
        }

        /* Long ticker + company labels stay inside the smart selector. */
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-width:0 !important;
            max-width:100% !important;
        }
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div > div {
            min-width:0 !important;
            overflow:hidden !important;
            text-overflow:ellipsis !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _live_stop_number_input(original_number_input):
    """Wrap only risk_stop_price with a live percent-from-entry header."""

    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_stop_price":
            return original_number_input(label, *args, **kwargs)

        try:
            entry = float(st.session_state.get("risk_entry_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            entry = 0.0

        raw_stop = st.session_state.get("risk_stop_price", kwargs.get("value", 0.0))
        try:
            stop = float(raw_stop or 0.0)
        except (TypeError, ValueError):
            stop = 0.0

        if entry > 0:
            signed_pct = (entry - stop) / entry * 100.0
            if abs(signed_pct) < 0.005:
                pct_text = "0.00%"
                pct_color = "#fb8b1e"
                arrow = "•"
            elif signed_pct > 0:
                pct_text = f"{abs(signed_pct):.2f}% BELOW ENTRY"
                pct_color = "#ff4343"
                arrow = "▼"
            else:
                pct_text = f"{abs(signed_pct):.2f}% ABOVE ENTRY"
                pct_color = "#4af6c3"
                arrow = "▲"
        else:
            pct_text = "—"
            pct_color = "#fb8b1e"
            arrow = "%"

        st.markdown(
            (
                '<div class="risk-stop-live-head">'
                '<span class="risk-stop-live-label">Stop Loss</span>'
                f'<span class="risk-stop-live-pct" style="color:{pct_color} !important;">'
                f'{arrow} {pct_text}</span>'
                '</div>'
            ),
            unsafe_allow_html=True,
        )

        local_kwargs = dict(kwargs)
        local_kwargs["label_visibility"] = "collapsed"
        return original_number_input(label, *args, **local_kwargs)

    return wrapped


def _smart_ticker_text_input(original_text_input, original_selectbox):
    """Replace only Risk Sizing's ticker text field with searchable smart suggestions."""

    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_ticker":
            return original_text_input(label, *args, **kwargs)

        current = str(
            st.session_state.get("risk_ticker")
            or kwargs.get("value")
            or "SPY"
        ).strip().upper()
        symbol = smart_ticker_selector(
            original_selectbox,
            label=str(label),
            current=current,
            key="risk_ticker_smart_selector",
            help_text=str(kwargs.get("help") or ""),
        )
        st.session_state["risk_ticker"] = symbol
        return symbol

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

    Streamlit controls inside this page rerun only Risk Sizing. The Stop Loss
    input shows its live percentage distance from Entry, and the ticker selector
    searches both symbols and company names while ranking prior lookups first.
    """
    previous_quote_summary = _v4.quote_summary
    previous_metric_box = _v4._metric_box
    original_dataframe = st.dataframe
    original_number_input = st.number_input
    original_text_input = st.text_input
    original_selectbox = st.selectbox

    def comma_dataframe(data=None, *args, **kwargs):
        kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
        return original_dataframe(data, *args, **kwargs)

    _render_stop_loss_css()
    _v4.quote_summary = _quote_summary_with_risk_defaults
    _v4._metric_box = _unused_risk_metric_box(previous_metric_box)
    st.dataframe = comma_dataframe
    st.number_input = _live_stop_number_input(original_number_input)
    st.text_input = _smart_ticker_text_input(original_text_input, original_selectbox)
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
        st.number_input = original_number_input
        st.text_input = original_text_input

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
