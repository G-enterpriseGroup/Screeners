"""Compact production Risk Sizing renderer for Raj's Terminal.

The v2 engine remains the single source of sizing/classification math. This
module only controls presentation and production behavior:
- true-cash balance selection
- E*TRADE ASK entry + 5% stop defaults
- ticker/company recognition
- compact no-wasted-space metric cards
- readable compact choice controls instead of fragile dropdown popovers
- live stop percentage and unused-risk percentage
"""

from __future__ import annotations

import html
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


def _unused_risk_value(label: str, value: str, help_text: str) -> tuple[str, str]:
    if str(label).strip().upper() != "UNUSED RISK":
        return value, help_text

    money_pattern = re.compile(r"\$([0-9,]+(?:\.\d+)?)")
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
                return (
                    f"{value} // {unused_pct:.2f}%",
                    f"{help_text} UNUSED RISK % = {unused:,.2f} / {budget:,.2f} x 100 = "
                    f"{unused_pct:.2f}% of the selected risk budget.",
                )
    except (TypeError, ValueError):
        pass
    return value, help_text


def _compact_metric_box(container, label, value, tone="neutral", detail="", help_text=""):
    value, help_text = _unused_risk_value(str(label), str(value), str(help_text))
    color = {
        "positive": "#4af6c3",
        "negative": "#ff433d",
        "blue": "#0068ff",
        "neutral": "#fb8b1e",
    }.get(str(tone), "#fb8b1e")

    help_html = ""
    if help_text:
        safe = html.escape(str(help_text)).replace("\n", "<br>")
        help_html = (
            '<span class="rs8-help" tabindex="0">?'
            f'<span class="rs8-tip">{safe}</span></span>'
        )

    detail_html = ""
    if detail:
        detail_html = (
            f'<div class="rs8-detail" style="color:{color}!important;">'
            f'{html.escape(str(detail))}</div>'
        )

    container.markdown(
        '<div class="rs8-card">'
        '<div class="rs8-head">'
        f'<span class="rs8-label">{html.escape(str(label))}</span>'
        f'{help_html}</div>'
        f'<div class="rs8-value" style="color:{color}!important;">{html.escape(str(value))}</div>'
        f'{detail_html}</div>',
        unsafe_allow_html=True,
    )


def _render_css() -> None:
    st.markdown(
        """
        <style>
        .risk-v8-section{
            display:flex;align-items:center;width:100%;box-sizing:border-box;
            margin:.18rem 0 .18rem;padding:.28rem .52rem;border:1px solid #fb8b1e;
            background:#050505;color:#fb8b1e!important;font-family:"Courier New",monospace;
            font-weight:900;line-height:1;
        }
        .risk-v8-section::before{
            content:"−";margin-right:.42rem;color:#fb8b1e!important;
            font-size:.95rem;font-weight:900;
        }

        /* Compact number cards: size to content instead of reserving 100+ px. */
        .rs8-card{
            position:relative;background:#000;border:1px solid #fb8b1e;
            padding:.28rem .42rem .30rem;min-height:0!important;height:auto!important;
            font-family:"Courier New",monospace;overflow:visible!important;
        }
        .rs8-head{display:flex;align-items:center;justify-content:space-between;gap:.32rem;}
        .rs8-label{
            color:#fb8b1e!important;font-size:.64rem;font-weight:900;line-height:1;
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
        }
        .rs8-value{
            font-size:1.05rem;font-weight:900;margin-top:.12rem;line-height:1.02;
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
        }
        .rs8-detail{font-size:.62rem;margin-top:.08rem;font-weight:700;line-height:1.05;}
        .rs8-help{
            position:relative;display:inline-flex;align-items:center;justify-content:center;
            width:14px;height:14px;flex:0 0 14px;border:1px solid #fb8b1e;
            border-radius:50%!important;color:#fb8b1e!important;font-size:9px;
            font-weight:900;cursor:help;line-height:1;
        }
        .rs8-tip{
            visibility:hidden;opacity:0;position:absolute;z-index:999999;right:-2px;top:18px;
            width:285px;max-width:72vw;padding:.48rem .56rem;border:1px solid #fb8b1e;
            background:#080808;color:#fb8b1e!important;font-family:"Courier New",monospace;
            font-size:.66rem;line-height:1.28;font-weight:600;white-space:normal;
            box-shadow:0 8px 24px rgba(0,0,0,.72);pointer-events:none;
        }
        .rs8-help:hover .rs8-tip,.rs8-help:focus .rs8-tip{visibility:visible;opacity:1;}

        /* The v2 CSS is still loaded for the rest of the page; neutralize its card sizing. */
        .rs-card{min-height:0!important;height:auto!important;padding:.28rem .42rem!important;}
        .rs-card-label{font-size:.64rem!important;line-height:1!important;}
        .rs-card-value{font-size:1.05rem!important;margin-top:.12rem!important;line-height:1.02!important;}
        .rs-card-detail{font-size:.62rem!important;margin-top:.08rem!important;}

        /* Compact inputs and keep all select/popover text visible on black. */
        [data-testid="stSelectbox"] div[data-baseweb="select"]>div{
            min-height:38px!important;height:38px!important;background:#050505!important;
            border-color:#fb8b1e!important;
        }
        [data-testid="stSelectbox"] div[data-baseweb="select"] span,
        [data-testid="stSelectbox"] div[data-baseweb="select"] input{
            color:#fb8b1e!important;-webkit-text-fill-color:#fb8b1e!important;
        }
        div[data-baseweb="popover"],div[data-baseweb="popover"]>div{
            background:#050505!important;color:#fb8b1e!important;
        }
        [role="listbox"]{
            background:#050505!important;color:#fb8b1e!important;
            max-height:240px!important;min-height:0!important;padding:.18rem!important;
        }
        [role="option"]{
            background:#050505!important;color:#fb8b1e!important;
            -webkit-text-fill-color:#fb8b1e!important;min-height:30px!important;
            height:auto!important;padding:.32rem .48rem!important;
            font-family:"Courier New",monospace!important;font-weight:800!important;
        }
        [role="option"] *{color:#fb8b1e!important;-webkit-text-fill-color:#fb8b1e!important;}
        [role="option"]:hover,[role="option"][aria-selected="true"]{
            background:#fb8b1e!important;color:#000!important;
        }
        [role="option"]:hover *,[role="option"][aria-selected="true"] *{
            color:#000!important;-webkit-text-fill-color:#000!important;
        }

        /* Part 2 choices are always visible so Firefox/BaseWeb popovers cannot glitch. */
        [data-testid="stRadio"]>div[role="radiogroup"]{
            display:flex!important;flex-wrap:wrap!important;gap:.26rem!important;
        }
        [data-testid="stRadio"] label{
            border:1px solid #5d3605!important;background:#050505!important;
            padding:.22rem .40rem!important;margin:0!important;min-height:30px!important;
        }
        [data-testid="stRadio"] label p{
            color:#fb8b1e!important;font-size:.72rem!important;font-weight:900!important;
            font-family:"Courier New",monospace!important;
        }
        [data-testid="stRadio"] label:has(input:checked){
            background:#fb8b1e!important;border-color:#fb8b1e!important;
        }
        [data-testid="stRadio"] label:has(input:checked) p{color:#000!important;}

        .risk-stop-pct-row{
            display:flex;justify-content:flex-end;align-items:center;
            margin:-.18rem 0 .02rem;font-family:"Courier New",monospace;
        }
        .risk-stop-pct{
            border:1px solid #5d3605;background:#050505;padding:.08rem .30rem;
            font-size:.61rem;font-weight:900;line-height:1;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button>*{display:none!important;}
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button{
            position:relative!important;min-width:28px!important;flex:0 0 28px!important;
            background:#050505!important;border-color:#fb8b1e!important;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:first-of-type::after{
            content:"▼";color:#fb8b1e!important;position:absolute;inset:0;display:flex;
            align-items:center;justify-content:center;font-weight:900;font-size:.64rem;
        }
        div[data-testid="stNumberInput"]:has(input[aria-label="Stop Loss"]) button:last-of-type::after{
            content:"▲";color:#4af6c3!important;position:absolute;inset:0;display:flex;
            align-items:center;justify-content:center;font-weight:900;font-size:.64rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _compact_v2_tooltip_css(base_css):
    def wrapped():
        base_css()
        _render_css()
    return wrapped


def _normalize_risk_ticker_state() -> None:
    value = str(st.session_state.get("risk_ticker") or "").strip().upper()
    st.session_state["risk_ticker"] = value


def _ticker_text_input(original_text_input):
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


def _compact_selectbox(original_selectbox, original_radio):
    """Replace the two glitch-prone Part 2 dropdowns with compact visible choices."""
    def wrapped(label, options, *args, **kwargs):
        key = kwargs.get("key")
        if key not in {"risk_trade_structure", "risk_size_multiplier"}:
            return original_selectbox(label, options, *args, **kwargs)

        choices = list(options)
        visual_key = f"_{key}_choice_v8"
        default_index = int(kwargs.get("index", 0) or 0)
        default_index = min(max(default_index, 0), max(len(choices) - 1, 0))
        current = st.session_state.get(key, choices[default_index] if choices else None)
        selected_index = choices.index(current) if current in choices else default_index

        format_func = kwargs.get("format_func", str)
        help_text = kwargs.get("help")
        value = original_radio(
            label,
            choices,
            index=selected_index,
            format_func=format_func,
            key=visual_key,
            horizontal=True,
            help=help_text,
        )
        st.session_state[key] = value
        return value
    return wrapped


def _stop_number_input(original_number_input):
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
                '<div class="risk-v8-section">1 // CLASSIFY THE CURRENT BOOK</div>',
                unsafe_allow_html=True,
            )
        if text == "**2 // SIZE THE NEXT TRADE**":
            return original_markdown(
                '<div class="risk-v8-section">2 // SIZE THE NEXT TRADE</div>',
                unsafe_allow_html=True,
            )
        return original_markdown(body, *args, **kwargs)
    return wrapped


def _compact_warning(original_warning):
    def wrapped(body, *args, **kwargs):
        text = str(body or "")
        if text.startswith("TACTICAL CAPACITY CHECK"):
            st.markdown(
                '<div style="border:1px solid #7d6500;background:#222200;color:#fb8b1e;'
                'padding:.32rem .50rem;margin:.12rem 0;font:800 .68rem/1.18 Courier New,monospace;">'
                + html.escape(text)
                + "</div>",
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
    original_selectbox = st.selectbox
    original_radio = st.radio
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
    _v2._metric_box = _compact_metric_box
    _v2._render_tooltip_css = _compact_v2_tooltip_css(previous_tooltip_css)

    st.dataframe = comma_dataframe
    st.number_input = _stop_number_input(original_number_input)
    st.text_input = _ticker_text_input(original_text_input)
    st.selectbox = _compact_selectbox(original_selectbox, original_radio)
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
        st.selectbox = original_selectbox
        st.markdown = original_markdown
        st.warning = original_warning

    if st.session_state.pop("_risk_ask_unavailable", False):
        st.warning(
            "E*TRADE ASK UNAVAILABLE // Entry and Stop were not auto-reset. "
            "Pull the quote again when an ask is available or enter the values manually."
        )

    render_stockanalysis_portfolio(
        "risk_sizing_account",
        key_prefix="risk_stockanalysis_cached_v8",
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
