"""Fail-safe production Risk Sizing v10.

This module keeps the v9 calculations/presentation but removes the two fragile
behaviors that could blank Part 2:
1) no global st.columns interception;
2) ticker autocomplete is fail-safe and falls back to the native ticker input.

The ticker selector uses the optimized `SYMBOL — COMPANY NAME` options from
`ticker_autocomplete.py`. Selecting a ticker automatically fetches E*TRADE,
seeds Entry from ASK and Stop at 5% below ASK, and leaves no manual quote button.
"""

from __future__ import annotations

import streamlit as st

import src.risk_sizing_ui_v9 as _v9
from src.etrade_client import ETradeError
from src.ticker_autocomplete import smart_ticker_selector


_BASE_RENDER_CSS = _v9._render_css


def _render_css_v10() -> None:
    _BASE_RENDER_CSS()
    st.markdown(
        """
        <style>
        /* The ticker row is the only row collapsed to one column.  Do this in
           CSS instead of replacing st.columns globally. */
        [data-testid="stHorizontalBlock"]:has(.risk-v10-ticker-marker){
            display:block!important;
            width:100%!important;
        }
        [data-testid="stHorizontalBlock"]:has(.risk-v10-ticker-marker)
        > [data-testid="stColumn"]:first-child{
            width:100%!important;
            min-width:100%!important;
            flex:1 1 100%!important;
        }
        [data-testid="stHorizontalBlock"]:has(.risk-v10-ticker-marker)
        > [data-testid="stColumn"]:nth-child(2){
            display:none!important;
        }
        .risk-v10-ticker-marker{display:none!important;}

        /* Keep the ticker search compact and obviously searchable. */
        [data-testid="stHorizontalBlock"]:has(.risk-v10-ticker-marker)
        [data-testid="stSelectbox"]{width:100%!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _safe_auto_quote_ticker_input(original_text_input, original_selectbox, *, client, touch_session):
    """Render dynamic ticker search without ever being allowed to blank Part 2."""

    def _load_quote(selected: str) -> None:
        selected = str(selected or "").strip().upper()
        if not selected or client is None:
            return

        quote_data = st.session_state.get("risk_quote_data")
        quote_symbol = str(st.session_state.get("risk_quote_symbol") or "").strip().upper()
        seed_symbol = str(st.session_state.get("_risk_entry_seed_symbol") or "").strip().upper()
        needs_quote = (
            quote_symbol != selected
            or seed_symbol != selected
            or not isinstance(quote_data, dict)
            or not quote_data
        )
        if not needs_quote:
            return

        try:
            summary = _v9._v2.quote_summary(client.get_quote(selected))
            st.session_state["risk_quote_data"] = summary
            st.session_state["risk_quote_symbol"] = selected
            st.session_state.pop("_risk_v9_quote_error", None)
            touch_session()
        except ETradeError as exc:
            st.session_state["_risk_v9_quote_error"] = str(exc)
        except Exception as exc:
            st.session_state["_risk_v9_quote_error"] = f"Quote load failed for {selected}: {exc}"

    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_ticker":
            return original_text_input(label, *args, **kwargs)

        st.markdown('<span class="risk-v10-ticker-marker"></span>', unsafe_allow_html=True)
        current = str(st.session_state.get("risk_ticker") or kwargs.get("value") or "SPY").strip().upper() or "SPY"

        # Primary path: searchable SYMBOL — COMPANY NAME selector.
        try:
            selected = smart_ticker_selector(
                original_selectbox,
                label="Ticker Search",
                current=current,
                key="risk_ticker_smart_v10",
                help_text=(
                    "Type a ticker or company name. Choose a result and the E*TRADE quote, Entry, "
                    "and 5%-below-ASK Stop update automatically."
                ),
            )
            selected = str(selected or current).strip().upper() or current
            st.session_state["risk_ticker"] = selected
            _load_quote(selected)
            return selected
        except Exception as exc:
            # Part 2 must still render.  The native field is the permanent
            # emergency fallback if Streamlit's selectbox changes behavior.
            st.session_state["_risk_v10_selector_error"] = str(exc)
            local_kwargs = dict(kwargs)
            local_kwargs["placeholder"] = "Type ticker and press Enter"
            raw = original_text_input(label, *args, **local_kwargs)
            selected = str(raw or current).strip().upper() or current
            _load_quote(selected)
            return selected

    return wrapped


def _safe_full_width_ticker_columns(original_columns, original_container, original_empty):
    """Do NOT intercept columns; CSS handles the one ticker row."""
    def wrapped(spec, *args, **kwargs):
        return original_columns(spec, *args, **kwargs)
    return wrapped


def render_risk_sizing(*args, **kwargs):
    previous_css = _v9._render_css
    previous_ticker = _v9._auto_quote_ticker_input
    previous_columns = _v9._full_width_ticker_columns

    _v9._render_css = _render_css_v10
    _v9._auto_quote_ticker_input = _safe_auto_quote_ticker_input
    _v9._full_width_ticker_columns = _safe_full_width_ticker_columns

    try:
        return _v9.render_risk_sizing(*args, **kwargs)
    finally:
        _v9._render_css = previous_css
        _v9._auto_quote_ticker_input = previous_ticker
        _v9._full_width_ticker_columns = previous_columns


__all__ = ["render_risk_sizing"]
