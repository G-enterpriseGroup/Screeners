"""Stable Risk Sizing v7.

This renderer deliberately avoids the v4/v6 source-rewrite/expander chain.
It calls the known-good v2 sizing engine directly, keeps both primary sections
always visible, and layers the current ASK defaults, smart ticker selector,
live stop percentage, unused-risk percentage, true-cash balance selection,
comma formatting, and cached sector/industry context on top.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v2 as _v2
from src.etrade_client import find_number
from src.stockanalysis_portfolio_v5 import cache_status, render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config
from src.ticker_autocomplete import company_name, record_lookup, smart_ticker_selector


_ORIGINAL_QUOTE_SUMMARY = _v2.quote_summary


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


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _section(payload: dict[str, Any], *names: str) -> dict[str, Any]:
    wanted = {str(name).casefold() for name in names}
    for node in _walk_dicts(payload):
        for key, child in node.items():
            if str(key).casefold() in wanted and isinstance(child, dict):
                return child
    return {}


def _number(section: dict[str, Any], key: str) -> float | None:
    wanted = str(key).casefold()
    for actual_key, value in (section or {}).items():
        if str(actual_key).casefold() != wanted:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _true_cash_fields(payload: dict[str, Any]) -> dict[str, float | None]:
    computed = _section(payload, "Computed", "ComputedBalance", "computedBalance")
    cash_section = _section(payload, "Cash", "cash")
    fields = {
        "cashBalance": _number(computed, "cashBalance"),
        "netCash": _number(computed, "netCash"),
        "moneyMktBalance": _number(cash_section, "moneyMktBalance"),
        "settledCashForInvestment": _number(computed, "settledCashForInvestment"),
        "unSettledCashForInvestment": _number(computed, "unSettledCashForInvestment"),
    }
    for key in list(fields):
        if fields[key] is None:
            fields[key] = find_number(payload, key)
    return fields


def _select_true_cash(payload: dict[str, Any]) -> tuple[float, str, dict[str, float | None]]:
    fields = _true_cash_fields(payload)
    priority = ("cashBalance", "netCash", "moneyMktBalance", "settledCashForInvestment")
    for key in priority:
        value = fields.get(key)
        if value is not None and abs(float(value)) >= 0.005:
            return float(value), key, fields
    for key in priority:
        value = fields.get(key)
        if value is not None:
            return float(value), key, fields
    return 0.0, "NO CASH FIELD RETURNED", fields


def _render_css() -> None:
    st.markdown(
        """
        <style>
        .risk-v7-build {
            border:1px solid #4af6c3;
            color:#4af6c3 !important;
            background:#020202;
            padding:.28rem .55rem;
            margin:.12rem 0 .42rem 0;
            font-family:"Courier New",monospace;
            font-weight:900;
            font-size:.72rem;
        }
        .risk-v7-section {
            display:flex;
            align-items:center;
            width:100%;
            box-sizing:border-box;
            margin:.38rem 0 .32rem 0;
            padding:.43rem .72rem;
            border:1px solid #fb8b1e;
            background:#050505;
            color:#fb8b1e !important;
            font-family:"Courier New",monospace;
            font-weight:900;
            line-height:1.15;
        }
        .risk-v7-section::before {
            content:"−";
            margin-right:.55rem;
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
        .risk-stop-live-label { color:#fb8b1e !important; font-size:1rem; }
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
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button > * { display:none !important; }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button {
            position:relative !important;
            min-width:34px !important;
            flex:0 0 34px !important;
            background:#050505 !important;
            border-color:#fb8b1e !important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:first-of-type::after {
            content:"▼"; color:#fb8b1e !important; position:absolute; inset:0;
            display:flex; align-items:center; justify-content:center; font-weight:900;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:last-of-type::after {
            content:"▲"; color:#4af6c3 !important; position:absolute; inset:0;
            display:flex; align-items:center; justify-content:center; font-weight:900;
        }
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-width:0 !important;
            max-width:100% !important;
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
        current = str(st.session_state.get("risk_ticker") or kwargs.get("value") or "SPY").strip().upper()
        symbol = smart_ticker_selector(
            original_selectbox,
            label=str(label),
            current=current,
            key="risk_ticker_smart_selector_v7",
            help_text=str(kwargs.get("help") or ""),
        )
        st.session_state["risk_ticker"] = symbol
        return symbol
    return wrapped


def _section_header_markdown(original_markdown):
    def wrapped(body, *args, **kwargs):
        text = str(body).strip()
        if text == "**1 // CLASSIFY THE CURRENT BOOK**":
            return original_markdown('<div class="risk-v7-section">1 // CLASSIFY THE CURRENT BOOK</div>', unsafe_allow_html=True)
        if text == "**2 // SIZE THE NEXT TRADE**":
            return original_markdown('<div class="risk-v7-section">2 // SIZE THE NEXT TRADE</div>', unsafe_allow_html=True)
        return original_markdown(body, *args, **kwargs)
    return wrapped


def render_risk_sizing(
    client,
    *,
    account_picker: Callable[[str], dict[str, Any] | None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
    touch_session: Callable[[], None],
) -> None:
    """Render the complete v2 trade-sizing workflow with no expander nesting."""
    _render_css()
    st.markdown(
        '<div class="risk-v7-build">RISK ENGINE BUILD // V7 DIRECT // SECTION 2 ALWAYS VISIBLE</div>',
        unsafe_allow_html=True,
    )

    previous_quote_summary = _v2.quote_summary
    previous_metric_box = _v2._metric_box
    original_dataframe = st.dataframe
    original_number_input = st.number_input
    original_text_input = st.text_input
    original_selectbox = st.selectbox
    original_markdown = st.markdown

    def comma_dataframe(data=None, *args, **kwargs):
        kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
        return original_dataframe(data, *args, **kwargs)

    def cash_only_snapshot(payload: dict[str, Any]) -> tuple[float, float, float]:
        total, _, market_value = balance_snapshot(payload)
        cash, source, fields = _select_true_cash(payload)
        st.session_state["_risk_true_cash_source"] = source
        st.session_state["_risk_true_cash_fields"] = fields
        return float(total or 0.0), float(cash), float(market_value or 0.0)

    _v2.quote_summary = _quote_summary_with_risk_defaults
    _v2._metric_box = _unused_risk_metric_box(previous_metric_box)
    st.dataframe = comma_dataframe
    st.number_input = _live_stop_number_input(original_number_input)
    st.text_input = _smart_ticker_text_input(original_text_input, original_selectbox)
    st.markdown = _section_header_markdown(original_markdown)

    try:
        _v2.render_risk_sizing(
            client,
            account_picker=account_picker,
            refresh_accounts=refresh_accounts,
            account_balance=account_balance,
            balance_snapshot=cash_only_snapshot,
            touch_session=touch_session,
        )
    finally:
        _v2.quote_summary = previous_quote_summary
        _v2._metric_box = previous_metric_box
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
        key_prefix="risk_stockanalysis_cached_v7",
        title="PORTFOLIO SECTOR + INDUSTRY // RISK CONTEXT",
        show_classification_table=False,
    )
    cache = cache_status()
    st.caption(
        "CLASSIFICATION CACHE // LAST-KNOWN-GOOD FALLBACK ENABLED // "
        f"SECTOR/INDUSTRY {cache['classifications']:,} TICKERS // "
        f"ETF LOOK-THROUGHS {cache['lookthroughs']:,}"
    )


__all__ = ["render_risk_sizing"]
