"""Stable, compact Risk Sizing renderer for Raj's Terminal.

The known-good v2 engine remains the source of the sizing math. This layer only
adds the production behavior Raj wants: true-cash selection, ASK-based entry +
5% stop defaults, unused-risk percentage, ticker/company recognition, compact
cards, and a single clean Stop Loss control.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v2 as _v2
from src.etrade_client import find_number
from src.stockanalysis_portfolio_v5 import cache_status, render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config
from src.ticker_autocomplete import company_name, record_lookup


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
        .risk-v7-section {
            display:flex;align-items:center;width:100%;box-sizing:border-box;
            margin:.28rem 0 .24rem;padding:.34rem .58rem;border:1px solid #fb8b1e;
            background:#050505;color:#fb8b1e!important;font-family:"Courier New",monospace;
            font-weight:900;line-height:1.05;
        }
        .risk-v7-section::before {
            content:"−";margin-right:.45rem;color:#fb8b1e!important;
            font-size:1rem;font-weight:900;
        }
        .risk-stop-pct-row {
            display:flex;justify-content:flex-end;align-items:center;
            margin:-.28rem 0 .08rem;font-family:"Courier New",monospace;
        }
        .risk-stop-pct {
            border:1px solid #5d3605;background:#050505;padding:.10rem .36rem;
            font-size:.64rem;font-weight:900;line-height:1.05;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button > * {
            display:none!important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button {
            position:relative!important;min-width:30px!important;flex:0 0 30px!important;
            background:#050505!important;border-color:#fb8b1e!important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:first-of-type::after {
            content:"▼";color:#fb8b1e!important;position:absolute;inset:0;display:flex;
            align-items:center;justify-content:center;font-weight:900;font-size:.68rem;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:last-of-type::after {
            content:"▲";color:#4af6c3!important;position:absolute;inset:0;display:flex;
            align-items:center;justify-content:center;font-weight:900;font-size:.68rem;
        }
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-width:0!important;max-width:100%!important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _compact_v2_tooltip_css(base_css):
    """Render v2 styling, then override only Risk Sizing card dimensions."""
    def wrapped():
        base_css()
        st.markdown(
            """
            <style>
            .rs-card {
                min-height:76px!important;
                padding:.36rem .54rem!important;
            }
            .rs-card-label {
                font-size:.69rem!important;
                line-height:1.02!important;
            }
            .rs-card-value {
                font-size:1.18rem!important;
                margin-top:.10rem!important;
                line-height:1.05!important;
            }
            .rs-card-detail {
                font-size:.66rem!important;
                margin-top:.08rem!important;
            }
            .rs-help {
                width:16px!important;height:16px!important;flex-basis:16px!important;
                font-size:10px!important;
            }
            .rs-help-tip {
                top:20px!important;width:300px!important;padding:.55rem .65rem!important;
                font-size:.69rem!important;line-height:1.32!important;
            }
            .rs-howto {
                padding:.48rem .65rem!important;margin:.18rem 0 .5rem!important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
    return wrapped


def _normalize_risk_ticker_state() -> None:
    value = str(st.session_state.get("risk_ticker") or "").strip().upper()
    st.session_state["risk_ticker"] = value


def _ticker_text_input(original_text_input):
    """Native input with safe company-name recognition; no giant autocomplete widget."""
    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_ticker":
            return original_text_input(label, *args, **kwargs)

        current = str(st.session_state.get("risk_ticker") or kwargs.get("value") or "SPY").strip().upper()
        known_name = company_name(current) if current else ""
        display_label = "Ticker"
        if current and known_name:
            display_label = f"Ticker // {current} — {known_name}"
        elif current:
            display_label = f"Ticker // {current}"

        local_kwargs = dict(kwargs)
        local_kwargs["on_change"] = _normalize_risk_ticker_state
        local_kwargs["placeholder"] = "Type ticker, then press Enter"
        raw = original_text_input(display_label, *args, **local_kwargs)
        return str(raw or "").strip().upper()

    return wrapped


def _stop_number_input(original_number_input):
    """Keep exactly one native Stop Loss label and place the live % below it."""
    def wrapped(label, *args, **kwargs):
        value = original_number_input(label, *args, **kwargs)
        if kwargs.get("key") != "risk_stop_price":
            return value

        try:
            entry = float(st.session_state.get("risk_entry_price", 0.0) or 0.0)
            stop = float(value or 0.0)
        except (TypeError, ValueError):
            entry, stop = 0.0, 0.0

        if entry > 0:
            signed_pct = (entry - stop) / entry * 100.0
            if abs(signed_pct) < 0.005:
                pct_text, pct_color, arrow = "0.00%", "#fb8b1e", "•"
            elif signed_pct > 0:
                pct_text, pct_color, arrow = f"{abs(signed_pct):.2f}% BELOW ENTRY", "#ff5757", "▼"
            else:
                pct_text, pct_color, arrow = f"{abs(signed_pct):.2f}% ABOVE ENTRY", "#4af6c3", "▲"
        else:
            pct_text, pct_color, arrow = "—", "#fb8b1e", "%"

        st.markdown(
            '<div class="risk-stop-pct-row">'
            f'<span class="risk-stop-pct" style="color:{pct_color}!important;">{arrow} {pct_text}</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        return value

    return wrapped


def _section_header_markdown(original_markdown):
    def wrapped(body, *args, **kwargs):
        text = str(body).strip()
        if text == "**1 // CLASSIFY THE CURRENT BOOK**":
            return original_markdown(
                '<div class="risk-v7-section">1 // CLASSIFY THE CURRENT BOOK</div>',
                unsafe_allow_html=True,
            )
        if text == "**2 // SIZE THE NEXT TRADE**":
            return original_markdown(
                '<div class="risk-v7-section">2 // SIZE THE NEXT TRADE</div>',
                unsafe_allow_html=True,
            )
        return original_markdown(body, *args, **kwargs)
    return wrapped


def _compact_warning(original_warning):
    def wrapped(body, *args, **kwargs):
        text = str(body or "")
        if text.startswith("TACTICAL CAPACITY CHECK"):
            st.markdown(
                '<div style="border:1px solid #7d6500;background:#292900;color:#fb8b1e;'
                'padding:.42rem .62rem;margin:.18rem 0;font:800 .72rem/1.25 Courier New,monospace;">'
                + text
                + '</div>',
                unsafe_allow_html=True,
            )
            return None
        return original_warning(body, *args, **kwargs)
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
    _render_css()

    previous_quote_summary = _v2.quote_summary
    previous_metric_box = _v2._metric_box
    previous_tooltip_css = _v2._render_tooltip_css
    original_dataframe = st.dataframe
    original_number_input = st.number_input
    original_text_input = st.text_input
    original_markdown = st.markdown
    original_warning = st.warning

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
    _v2._render_tooltip_css = _compact_v2_tooltip_css(previous_tooltip_css)
    st.dataframe = comma_dataframe
    st.number_input = _stop_number_input(original_number_input)
    st.text_input = _ticker_text_input(original_text_input)
    st.markdown = _section_header_markdown(original_markdown)
    st.warning = _compact_warning(original_warning)

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
        _v2._render_tooltip_css = previous_tooltip_css
        st.dataframe = original_dataframe
        st.number_input = original_number_input
        st.text_input = original_text_input
        st.markdown = original_markdown
        st.warning = original_warning

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
