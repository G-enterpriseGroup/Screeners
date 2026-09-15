"""Risk-sizing UI v6: restore the complete SIZE THE NEXT TRADE workflow.

This version rebuilds the active Risk Sizing renderer from the known-good v2
engine instead of inheriting v4's transformed second expander. Section 1 remains
collapsible; Section 2 is rendered directly and always visible so ticker, quote,
structure, multiplier, risk budget, entry/stop and calculated share/spread
sizing cannot disappear behind a broken or stale expander transform.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v4 as _v4
from src.stockanalysis_portfolio_v5 import cache_status, render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config
from src.ticker_autocomplete import company_name, record_lookup, smart_ticker_selector


_V2_PATH = Path(__file__).with_name("risk_sizing_ui_v2.py")
_SOURCE = _V2_PATH.read_text(encoding="utf-8")

# Preserve v4's sixth TRUE CASH metric beside Tactical Room.
if _v4._OLD_COLUMNS not in _SOURCE:
    raise RuntimeError("Risk-sizing v2 metric-row marker was not found for v6.")
_SOURCE = _SOURCE.replace(_v4._OLD_COLUMNS, _v4._NEW_COLUMNS, 1)

if _v4._OLD_ROOM_BLOCK not in _SOURCE:
    raise RuntimeError("Risk-sizing v2 Tactical Room marker was not found for v6.")
_SOURCE = _SOURCE.replace(_v4._OLD_ROOM_BLOCK, _v4._NEW_ROOM_BLOCK, 1)

# Keep only Section 1 collapsible. Section 2 stays as original v2 source and is
# therefore guaranteed to contain the complete trade-sizing workflow.
_SOURCE = _v4._wrap_expander_section(
    _SOURCE,
    '    st.markdown("**1 // CLASSIFY THE CURRENT BOOK**")\n',
    '    st.markdown("**2 // SIZE THE NEXT TRADE**")\n',
    "1 // CLASSIFY THE CURRENT BOOK",
    "risk_classify_section",
)

exec(compile(_SOURCE, str(_V2_PATH), "exec"), globals())

_BASE_RENDER_RISK_SIZING = render_risk_sizing
_ORIGINAL_QUOTE_SUMMARY = quote_summary


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


def _render_v6_css() -> None:
    _v4._render_compact_terminal_css()
    st.markdown(
        """
        <style>
        .risk-size-visible-head {
            display:flex;
            align-items:center;
            gap:.55rem;
            width:100%;
            box-sizing:border-box;
            margin:.45rem 0 .35rem 0;
            padding:.42rem .72rem;
            border:1px solid #fb8b1e;
            background:#050505;
            color:#fb8b1e !important;
            font-family:"Courier New",monospace;
            font-weight:900;
            line-height:1.15;
        }
        .risk-size-visible-head::before {
            content:"−";
            color:#fb8b1e !important;
            font-size:1.15rem;
            font-weight:900;
        }
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
            text-align:right;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button > * {
            display:none !important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button {
            position:relative !important;
            min-width:34px !important;
            flex:0 0 34px !important;
            background:#050505 !important;
            border-color:#fb8b1e !important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:first-of-type::after {
            content:"▼";
            color:#fb8b1e !important;
            position:absolute;
            inset:0;
            display:flex;
            align-items:center;
            justify-content:center;
            font-weight:900;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:last-of-type::after {
            content:"▲";
            color:#4af6c3 !important;
            position:absolute;
            inset:0;
            display:flex;
            align-items:center;
            justify-content:center;
            font-weight:900;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _live_stop_number_input(original_number_input):
    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_stop_price":
            return original_number_input(label, *args, **kwargs)

        try:
            entry = float(st.session_state.get("risk_entry_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            entry = 0.0
        try:
            stop = float(st.session_state.get("risk_stop_price", kwargs.get("value", 0.0)) or 0.0)
        except (TypeError, ValueError):
            stop = 0.0

        if entry > 0:
            signed_pct = (entry - stop) / entry * 100.0
            if abs(signed_pct) < 0.005:
                pct_text, pct_color, arrow = "0.00%", "#fb8b1e", "•"
            elif signed_pct > 0:
                pct_text, pct_color, arrow = f"{abs(signed_pct):.2f}% BELOW ENTRY", "#ff4343", "▼"
            else:
                pct_text, pct_color, arrow = f"{abs(signed_pct):.2f}% ABOVE ENTRY", "#4af6c3", "▲"
        else:
            pct_text, pct_color, arrow = "—", "#fb8b1e", "%"

        st.markdown(
            '<div class="risk-stop-live-head">'
            '<span class="risk-stop-live-label">Stop Loss</span>'
            f'<span class="risk-stop-live-pct" style="color:{pct_color} !important;">{arrow} {pct_text}</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        local_kwargs = dict(kwargs)
        local_kwargs["label_visibility"] = "collapsed"
        return original_number_input(label, *args, **local_kwargs)

    return wrapped


def _smart_ticker_text_input(original_text_input, original_selectbox):
    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_ticker":
            return original_text_input(label, *args, **kwargs)

        current = str(
            st.session_state.get("risk_ticker") or kwargs.get("value") or "SPY"
        ).strip().upper()
        symbol = smart_ticker_selector(
            original_selectbox,
            label=str(label),
            current=current,
            key="risk_ticker_smart_selector_v6",
            help_text=str(kwargs.get("help") or ""),
        )
        st.session_state["risk_ticker"] = symbol
        return symbol

    return wrapped


def _section_two_header_markdown(original_markdown):
    def wrapped(body, *args, **kwargs):
        if str(body).strip() == "**2 // SIZE THE NEXT TRADE**":
            return original_markdown(
                '<div class="risk-size-visible-head">2 // SIZE THE NEXT TRADE</div>',
                unsafe_allow_html=True,
            )
        return original_markdown(body, *args, **kwargs)
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
    """Render the full Risk Sizing workflow with Section 2 always visible."""
    _render_v6_css()

    previous_quote_summary = globals()["quote_summary"]
    previous_metric_box = globals()["_metric_box"]
    original_dataframe = st.dataframe
    original_number_input = st.number_input
    original_text_input = st.text_input
    original_selectbox = st.selectbox
    original_markdown = st.markdown

    def comma_dataframe(data=None, *args, **kwargs):
        kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
        return original_dataframe(data, *args, **kwargs)

    def cash_only_snapshot(payload: dict[str, Any]) -> tuple[float, float, float]:
        return _v4._true_cash_snapshot(payload, balance_snapshot)

    globals()["quote_summary"] = _quote_summary_with_risk_defaults
    globals()["_metric_box"] = _unused_risk_metric_box(previous_metric_box)
    st.dataframe = comma_dataframe
    st.number_input = _live_stop_number_input(original_number_input)
    st.text_input = _smart_ticker_text_input(original_text_input, original_selectbox)
    st.markdown = _section_two_header_markdown(original_markdown)

    try:
        _BASE_RENDER_RISK_SIZING(
            client,
            account_picker=account_picker,
            refresh_accounts=refresh_accounts,
            account_balance=account_balance,
            balance_snapshot=cash_only_snapshot,
            touch_session=touch_session,
        )
    finally:
        globals()["quote_summary"] = previous_quote_summary
        globals()["_metric_box"] = previous_metric_box
        st.dataframe = original_dataframe
        st.number_input = original_number_input
        st.text_input = original_text_input
        st.markdown = original_markdown

    if st.session_state.pop("_risk_ask_unavailable", False):
        st.warning(
            "E*TRADE ASK UNAVAILABLE // Entry and Stop were not auto-reset. "
            "Pull the quote again when an ask is available or enter the values manually."
        )

    render_stockanalysis_portfolio(
        "risk_sizing_account",
        key_prefix="risk_stockanalysis_cached_v6",
        title="PORTFOLIO SECTOR + INDUSTRY // RISK CONTEXT",
        show_classification_table=False,
    )
    cache = cache_status()
    st.caption(
        "CLASSIFICATION CACHE // LAST-KNOWN-GOOD FALLBACK ENABLED // "
        f"SECTOR/INDUSTRY {cache['classifications']:,} TICKERS // "
        f"ETF LOOK-THROUGHS {cache['lookthroughs']:,}"
    )
