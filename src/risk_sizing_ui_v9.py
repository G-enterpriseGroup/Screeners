"""Seamless production Risk Sizing UI for Raj's Terminal.

The v2 renderer remains the math/source-of-truth engine.  This layer changes
only presentation + interaction for Part 2:
- searchable ticker/company selector
- ticker selection automatically pulls the E*TRADE quote
- no manual PULL E*TRADE QUOTE button
- ASK automatically seeds Entry and a 5%-below-ASK Stop
- exactly one live stop-distance badge
- compact structure/size controls and metric cards from the v8 layer

Part 1 portfolio/risk math is intentionally untouched.
"""

from __future__ import annotations

import html
from typing import Any, Callable

import streamlit as st

import src.risk_sizing_ui_v2 as _v2
import src.risk_sizing_ui_v7 as _v8
from src.etrade_client import ETradeError
from src.stockanalysis_portfolio_v5 import cache_status, render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config
from src.ticker_autocomplete import company_name, smart_ticker_selector


def _render_v9_css() -> None:
    _v8._render_css()
    st.markdown(
        """
        <style>
        /* Part 2 ticker is now the single full-width searchable control. */
        [data-testid="stSelectbox"]:has(#risk-v9-search-sentinel){width:100%!important;}

        /* Keep searchable ticker/company options readable and compact. */
        [role="listbox"] [role="option"]{
            font-family:"Courier New",monospace!important;
            font-size:.72rem!important;
        }

        /* Only one stop-distance badge may be visible even if Streamlit keeps
           stale DOM nodes briefly during a rerun. */
        [data-testid="stMarkdownContainer"]:has(.risk-v9-stop-pct)
        ~ [data-testid="stMarkdownContainer"]:has(.risk-v9-stop-pct){
            display:none!important;
        }
        .risk-v9-stop-row{
            display:flex;justify-content:flex-end;align-items:center;
            margin:-.16rem 0 .02rem;font-family:"Courier New",monospace;
        }
        .risk-v9-stop-pct{
            border:1px solid #5d3605;background:#050505;
            padding:.08rem .30rem;font-size:.61rem;font-weight:900;line-height:1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _auto_quote_ticker_input(
    original_selectbox,
    *,
    client,
    touch_session: Callable[[], None],
):
    """Replace v2's ticker text box with searchable ticker/company selection."""

    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_ticker":
            # v2 has other text inputs in future versions; leave them native.
            return st._risk_v9_base_text_input(label, *args, **kwargs)

        current = str(st.session_state.get("risk_ticker") or kwargs.get("value") or "SPY").strip().upper() or "SPY"
        selected = smart_ticker_selector(
            original_selectbox,
            label="Ticker Search",
            current=current,
            key="risk_ticker_smart_v9",
            help_text=(
                "Type a ticker OR company name. Suggestions show SYMBOL — COMPANY NAME. "
                "Selecting one automatically pulls the E*TRADE quote and resets Entry to ASK with Stop 5% below ASK."
            ),
        )
        selected = str(selected or current).strip().upper() or current
        st.session_state["risk_ticker"] = selected

        quote_key = "risk_quote_data"
        quote_symbol = str(st.session_state.get("risk_quote_symbol") or "").strip().upper()
        quote_data = st.session_state.get(quote_key)
        needs_quote = quote_symbol != selected or not isinstance(quote_data, dict) or not quote_data

        if needs_quote:
            # Clear the old symbol first so a failed lookup can never display the
            # previous ticker's quote as though it belonged to the new ticker.
            st.session_state.pop(quote_key, None)
            st.session_state["risk_quote_symbol"] = ""
            try:
                payload = client.get_quote(selected)
                summary = _v2.quote_summary(payload)
                st.session_state[quote_key] = summary
                st.session_state["risk_quote_symbol"] = selected
                touch_session()
            except ETradeError as exc:
                st.session_state["_risk_v9_quote_error"] = str(exc)
            except Exception as exc:
                st.session_state["_risk_v9_quote_error"] = f"Quote load failed for {selected}: {exc}"

        # The formatted selectbox itself already shows SYMBOL — COMPANY NAME.
        # Return only the canonical symbol because v2's calculations expect it.
        return selected

    return wrapped


def _hide_manual_quote_button(original_button):
    """Remove the obsolete PULL E*TRADE QUOTE button without affecting others."""

    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") == "risk_pull_quote":
            return False
        return original_button(label, *args, **kwargs)

    return wrapped


def _full_width_ticker_columns(original_columns, original_container, original_empty):
    """Collapse v2's old ticker+button row into one full-width ticker container."""

    def wrapped(spec, *args, **kwargs):
        try:
            values = list(spec) if not isinstance(spec, int) else []
        except TypeError:
            values = []
        if len(values) == 2 and abs(float(values[0]) - 3.4) < 1e-9 and abs(float(values[1]) - 1.2) < 1e-9:
            return [original_container(), original_empty()]
        return original_columns(spec, *args, **kwargs)

    return wrapped


def _single_stop_number_input(original_number_input):
    """Render one and only one live stop-distance badge per Part 2 render."""
    rendered = False

    def wrapped(label, *args, **kwargs):
        nonlocal rendered
        value = original_number_input(label, *args, **kwargs)
        if kwargs.get("key") != "risk_stop_price" or rendered:
            return value
        rendered = True

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
            '<div class="risk-v9-stop-row">'
            f'<span class="risk-v9-stop-pct" style="color:{pct_color}!important;">{arrow} {html.escape(pct_text)}</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        return value

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
    """Render the v2 risk engine with seamless, dynamic Part 2 controls."""
    _render_v9_css()

    if client is None:
        # Let v2 render its normal disconnected guidance.
        return _v2.render_risk_sizing(
            client,
            account_picker=account_picker,
            refresh_accounts=refresh_accounts,
            account_balance=account_balance,
            balance_snapshot=balance_snapshot,
            touch_session=touch_session,
        )

    previous_quote_summary = _v2.quote_summary
    previous_metric_box = _v2._metric_box
    previous_tooltip_css = _v2._render_tooltip_css

    # Capture the true Streamlit primitives before applying this render's narrow
    # widget adapters.  Store text_input once so nested/hot reruns cannot stack.
    if not hasattr(st, "_risk_v9_base_text_input"):
        st._risk_v9_base_text_input = st.text_input
    if not hasattr(st, "_risk_v9_base_number_input"):
        st._risk_v9_base_number_input = st.number_input
    if not hasattr(st, "_risk_v9_base_button"):
        st._risk_v9_base_button = st.button
    if not hasattr(st, "_risk_v9_base_columns"):
        st._risk_v9_base_columns = st.columns
    if not hasattr(st, "_risk_v9_base_selectbox"):
        st._risk_v9_base_selectbox = st.selectbox

    base_text_input = st._risk_v9_base_text_input
    base_number_input = st._risk_v9_base_number_input
    base_button = st._risk_v9_base_button
    base_columns = st._risk_v9_base_columns
    base_selectbox = st._risk_v9_base_selectbox
    base_radio = st.radio
    base_dataframe = st.dataframe
    base_markdown = st.markdown
    base_warning = st.warning
    base_container = st.container
    base_empty = st.empty

    def comma_dataframe(data=None, *args, **kwargs):
        kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
        return base_dataframe(data, *args, **kwargs)

    def cash_only_snapshot(payload: dict[str, Any]) -> tuple[float, float, float]:
        total, _, market_value = balance_snapshot(payload)
        cash, source, fields = _v8._select_true_cash(payload)
        st.session_state["_risk_true_cash_source"] = source
        st.session_state["_risk_true_cash_fields"] = fields
        return float(total or 0.0), float(cash), float(market_value or 0.0)

    _v2.quote_summary = _v8._quote_summary_with_risk_defaults
    _v2._metric_box = _v8._compact_metric_box
    _v2._render_tooltip_css = _v8._compact_v2_tooltip_css(previous_tooltip_css)

    st.dataframe = comma_dataframe
    st.number_input = _single_stop_number_input(base_number_input)
    st.text_input = _auto_quote_ticker_input(
        base_selectbox,
        client=client,
        touch_session=touch_session,
    )
    st.button = _hide_manual_quote_button(base_button)
    st.columns = _full_width_ticker_columns(base_columns, base_container, base_empty)
    st.selectbox = _v8._compact_selectbox(base_selectbox, base_radio)
    st.markdown = _v8._section_header_markdown(base_markdown)
    st.warning = _v8._compact_warning(base_warning)

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

        st.dataframe = base_dataframe
        st.number_input = base_number_input
        st.text_input = base_text_input
        st.button = base_button
        st.columns = base_columns
        st.selectbox = base_selectbox
        st.markdown = base_markdown
        st.warning = base_warning

    quote_error = st.session_state.pop("_risk_v9_quote_error", None)
    if quote_error:
        st.warning(str(quote_error))

    if st.session_state.pop("_risk_ask_unavailable", False):
        st.warning(
            "E*TRADE ASK UNAVAILABLE // Entry and Stop were not auto-reset because the selected symbol returned no usable ask."
        )

    render_stockanalysis_portfolio(
        "risk_sizing_account",
        key_prefix="risk_stockanalysis_cached_v9",
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
