"""Fail-safe production Risk Sizing v10.

OWNERSHIP / EDITING NOTES
-------------------------
THIS IS THE PRODUCTION OWNER for Risk Sizing Part 2 interaction safety.

EDIT THIS FILE for:
- ticker search / ticker-company selection behavior;
- automatic E*TRADE quote loading after ticker selection;
- ASK -> Entry and 5%-below-ASK Stop interaction plumbing;
- Part 2 fail-safe behavior when autocomplete fails;
- narrowly scoped Part 2 layout fixes.

DO NOT edit GEX, OAuth, Holdings, or navigation files to fix Risk Sizing.
DO NOT add module-level assignments such as `st.columns = ...` or
`st.button = ...`. A Risk fix must not replace Streamlit functions globally.

Supporting responsibilities:
- base Risk UI sequence/cards: `src/risk_sizing_ui_v9.py` / `v2.py`;
- formulas: `src/risk_sizing.py`;
- ticker directory/search data: `src/ticker_autocomplete.py`.

This module keeps the v9 calculations/presentation but removes the two fragile
behaviors that could blank Part 2:
1) no global st.columns interception;
2) ticker autocomplete is fail-safe and falls back to the native ticker input.

The ticker control is a direct symbol input. Company / ETF name is rendered in
a separate read-only display box. Entering a ticker automatically fetches
E*TRADE, seeds Entry from ASK and Stop at 5% below ASK, and leaves no manual
quote button.
"""

from __future__ import annotations

import html
import inspect
import math
import time

import streamlit as st

import src.risk_sizing_ui_v9 as _v9
from src.etrade_client import ETradeError


_BASE_RENDER_CSS = _v9._render_css


def _render_css_v10() -> None:
    _BASE_RENDER_CSS()
    st.markdown(
        """
        <style>
        /*
           Collapse ONLY the inner ticker/quote row to one visible column.
           IMPORTANT: the marker must be inside the FIRST direct stColumn of the
           horizontal block. A broad :has(.risk-v10-ticker-marker) selector also
           matches ancestor layouts (such as the Risk Book + Part 2 split) and
           can hide the entire Part 2 pane.
        */
        [data-testid="stHorizontalBlock"]:has(
            > [data-testid="stColumn"]:first-child .risk-v10-ticker-marker
        ){
            display:flex!important;
            width:100%!important;
            gap:0!important;
        }
        [data-testid="stHorizontalBlock"]:has(
            > [data-testid="stColumn"]:first-child .risk-v10-ticker-marker
        ) > [data-testid="stColumn"]:first-child{
            width:100%!important;
            min-width:100%!important;
            flex:1 1 100%!important;
        }
        [data-testid="stHorizontalBlock"]:has(
            > [data-testid="stColumn"]:first-child .risk-v10-ticker-marker
        ) > [data-testid="stColumn"]:nth-child(2){
            display:none!important;
        }
        .risk-v10-ticker-marker{display:none!important;}

        /* Prevent long values/help content from forcing sibling controls across
           each other. This changes only layout containment, not appearance. */
        .st-key-risk_part2_panel [data-testid="stColumn"],
        .st-key-risk_part2_panel [data-testid="stElementContainer"]{
            min-width:0!important;
        }
        .st-key-risk_part2_panel .rs9-card{
            width:100%!important;
            max-width:100%!important;
            box-sizing:border-box!important;
        }

        /* Ticker is editable; Company / ETF is a separate display-only box. */
        [data-testid="stHorizontalBlock"]:has(
            > [data-testid="stColumn"]:first-child .risk-v10-ticker-marker
        ) [data-testid="stTextInput"]{width:100%!important;}
        .risk-v10-company-field{
            width:100%;
            min-width:0;
            font-family:"Courier New",monospace;
        }
        .risk-v10-company-label{
            display:flex;
            align-items:center;
            min-height:20px;
            margin:0 0 3px 0;
            color:#fb8b1e!important;
            -webkit-text-fill-color:#fb8b1e!important;
            font-family:"Courier New",monospace;
            font-size:.875rem;
            font-weight:400;
            line-height:1.25;
        }
        .risk-v10-company-box{
            display:flex;
            align-items:center;
            width:100%;
            height:38px;
            min-height:38px;
            box-sizing:border-box;
            padding:0 10px;
            border:1px solid #fb8b1e;
            background:#050505;
            color:#fb8b1e!important;
            -webkit-text-fill-color:#fb8b1e!important;
            font-family:"Courier New",monospace;
            font-size:.80rem;
            font-weight:900;
            line-height:1.15;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _finite_number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


@st.cache_data(ttl=15, show_spinner=False)
def _yfinance_quote_payload(symbol: str) -> dict:
    """Return the latest Yahoo Finance quote snapshot in E*TRADE-like fields."""
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("Ticker is required.")

    # Lazy import keeps normal E*TRADE quote loads fast.
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        info = {}

    try:
        fast = ticker.get_fast_info()
    except Exception:
        fast = {}

    def fast_value(name: str):
        try:
            if isinstance(fast, dict):
                return fast.get(name)
            return getattr(fast, name, None)
        except Exception:
            return None

    last = _finite_number(
        info.get("currentPrice"),
        _finite_number(
            info.get("regularMarketPrice"),
            _finite_number(fast_value("last_price")),
        ),
    )
    previous_close = _finite_number(
        info.get("regularMarketPreviousClose"),
        _finite_number(fast_value("previous_close")),
    )
    bid = _finite_number(info.get("bid"))
    ask = _finite_number(info.get("ask"))

    if last is None and bid is not None and ask is not None:
        last = (bid + ask) / 2.0
    if last is None or last <= 0:
        raise RuntimeError(f"Yahoo Finance returned no usable price for {symbol}.")

    ask_is_proxy = ask is None or ask <= 0
    if ask_is_proxy:
        ask = last
    if bid is None or bid <= 0:
        bid = last

    change = _finite_number(info.get("regularMarketChange"))
    if change is None and previous_close not in (None, 0):
        change = last - previous_close

    return {
        "symbol": symbol,
        "companyName": str(
            info.get("longName")
            or info.get("shortName")
            or _v9.company_name(symbol)
            or ""
        ),
        "lastPrice": last,
        "bid": bid,
        "ask": ask,
        "changeClose": change or 0.0,
        "_risk_quote_source": "YAHOO FINANCE",
        "_risk_quote_ask_proxy": ask_is_proxy,
    }


def _request_live_etrade_quote(client, symbol: str):
    """Force a live E*TRADE quote when the client exposes force_refresh."""
    if client is None or bool(getattr(client, "is_offline", False)):
        raise ETradeError("Live E*TRADE quote connection is unavailable.")

    quote_fn = client.get_quote
    try:
        parameters = inspect.signature(quote_fn).parameters.values()
        accepts_force = any(
            parameter.name == "force_refresh"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_force = False

    if accepts_force:
        return quote_fn(symbol, force_refresh=True)
    return quote_fn(symbol)


def _quote_payload_with_source(payload, source: str, *, ask_proxy: bool = False):
    data = dict(payload or {})
    data["_risk_quote_source"] = source
    data["_risk_quote_ask_proxy"] = bool(ask_proxy)
    return data


def _normalize_risk_ticker_session_value() -> None:
    """Normalize only the Risk ticker input after a native text edit."""
    value = str(st.session_state.get("risk_ticker") or "").strip().upper()
    st.session_state["risk_ticker"] = value or "SPY"


def _risk_company_display_name(symbol: str) -> str:
    """Return quote-derived company/ETF name with directory fallback."""
    symbol = str(symbol or "").strip().upper()
    quote_symbol = str(st.session_state.get("risk_quote_symbol") or "").strip().upper()
    quote_data = st.session_state.get("risk_quote_data")
    if symbol and quote_symbol == symbol and isinstance(quote_data, dict):
        description = str(
            quote_data.get("description")
            or quote_data.get("companyName")
            or ""
        ).strip()
        if description:
            return description
    return str(_v9.company_name(symbol) or "").strip() or "—"


def _render_company_display(container, symbol: str) -> None:
    company = html.escape(_risk_company_display_name(symbol))
    with container:
        st.html(
            '<div class="risk-v10-company-field">'
            '<div class="risk-v10-company-label">Company / ETF</div>'
            f'<div class="risk-v10-company-box" title="{company}">{company}</div>'
            '</div>'
        )


def _safe_auto_quote_ticker_input(original_text_input, original_selectbox, *, client, touch_session):
    """Render dynamic ticker search with E*TRADE-first / Yahoo fallback quotes."""

    def _load_quote(selected: str) -> None:
        selected = str(selected or "").strip().upper()
        if not selected:
            return

        quote_data = st.session_state.get("risk_quote_data")
        quote_symbol = str(st.session_state.get("risk_quote_symbol") or "").strip().upper()
        quote_source = str(st.session_state.get("risk_quote_source") or "").strip().upper()
        seed_symbol = str(st.session_state.get("_risk_entry_seed_symbol") or "").strip().upper()

        live_available = client is not None and not bool(getattr(client, "is_offline", False))
        try:
            last_live_attempt = float(st.session_state.get("_risk_live_quote_attempt_at") or 0.0)
        except (TypeError, ValueError):
            last_live_attempt = 0.0
        retry_live_due = (
            live_available
            and quote_source != "E*TRADE"
            and time.time() - last_live_attempt >= 15.0
        )

        needs_quote = (
            quote_symbol != selected
            or seed_symbol != selected
            or not isinstance(quote_data, dict)
            or not quote_data
            or retry_live_due
        )
        if not needs_quote:
            return

        summary = None
        stale_etrade_payload = None
        live_error = None

        # 1) Always try a forced live E*TRADE quote first when a live client exists.
        if live_available:
            st.session_state["_risk_live_quote_attempt_at"] = time.time()
            try:
                payload = _request_live_etrade_quote(client, selected)
                if bool(st.session_state.get("_etrade_offline_mode", False)):
                    # CachedETradeClient can transparently return a stale snapshot
                    # after a failed live request. Hold it only as the final fallback.
                    stale_etrade_payload = payload
                else:
                    summary = _v9._v2.quote_summary(
                        _quote_payload_with_source(payload, "E*TRADE")
                    )
                    if _finite_number(summary.get("ask"), 0.0) <= 0:
                        stale_etrade_payload = payload
                        summary = None
                    else:
                        touch_session()
            except Exception as exc:
                live_error = exc

        # 2) If E*TRADE is unavailable, failed, or lacked a usable ask, use Yahoo.
        if summary is None:
            try:
                summary = _v9._v2.quote_summary(_yfinance_quote_payload(selected))
            except Exception as yahoo_exc:
                # 3) Preserve the existing last-known E*TRADE behavior only if
                # Yahoo also fails, so a transient dual outage does not blank Part 2.
                try:
                    if stale_etrade_payload is None and client is not None:
                        stale_etrade_payload = client.get_quote(selected)
                    if stale_etrade_payload is not None:
                        summary = _v9._v2.quote_summary(
                            _quote_payload_with_source(
                                stale_etrade_payload,
                                "E*TRADE CACHED",
                            )
                        )
                except Exception:
                    summary = None

                if summary is None:
                    primary = str(live_error or "live E*TRADE unavailable")
                    st.session_state["_risk_v9_quote_error"] = (
                        f"Quote load failed for {selected}: E*TRADE: {primary}; "
                        f"Yahoo Finance: {yahoo_exc}"
                    )
                    st.session_state["risk_quote_symbol"] = ""
                    return

        st.session_state["risk_quote_data"] = summary
        st.session_state["risk_quote_symbol"] = selected
        st.session_state["risk_quote_source"] = str(
            summary.get("_source") or "E*TRADE"
        )
        st.session_state.pop("_risk_v9_quote_error", None)

    def wrapped(label, *args, **kwargs):
        if kwargs.get("key") != "risk_ticker":
            return original_text_input(label, *args, **kwargs)

        st.html('<span class="risk-v10-ticker-marker"></span>')
        current = (
            str(
                st.session_state.get("risk_ticker")
                or kwargs.get("value")
                or "SPY"
            )
            .strip()
            .upper()
            or "SPY"
        )

        # Ticker is the only editable control. Company / ETF is display-only.
        if "risk_ticker" not in st.session_state:
            st.session_state["risk_ticker"] = current

        ticker_col, company_col = st.columns(
            [1.0, 2.35],
            gap="small",
            vertical_alignment="bottom",
        )
        local_kwargs = dict(kwargs)
        local_kwargs.pop("value", None)
        local_kwargs["key"] = "risk_ticker"
        local_kwargs["placeholder"] = "SPY"
        local_kwargs["help"] = (
            "Enter a ticker symbol. The E*TRADE quote, Entry, and 5%-below-ASK "
            "Stop update automatically. Yahoo Finance is used only if live "
            "E*TRADE quote data is unavailable."
        )
        local_kwargs["on_change"] = _normalize_risk_ticker_session_value

        with ticker_col:
            raw = original_text_input("Ticker", *args, **local_kwargs)

        selected = str(raw or current).strip().upper() or current
        _load_quote(selected)
        _render_company_display(company_col, selected)
        return selected
    return wrapped

def _render_disconnected_ticker_fallback() -> None:
    """Keep ticker research usable when no E*TRADE account client exists yet."""
    _render_css_v10()
    st.info(
        "E*TRADE PORTFOLIO CONTEXT UNAVAILABLE // Yahoo Finance quote fallback is active. "
        "Reconnect E*TRADE to restore account-based risk budgets and position sizing."
    )
    st.html('<div class="risk-v9-section">2. SIZE THE NEXT TRADE</div>')

    current = str(st.session_state.get("risk_ticker") or "SPY").strip().upper() or "SPY"
    if "risk_ticker" not in st.session_state:
        st.session_state["risk_ticker"] = current

    ticker_col, company_col = st.columns(
        [1.0, 2.35],
        gap="small",
        vertical_alignment="bottom",
    )
    with ticker_col:
        raw = st.text_input(
            "Ticker",
            key="risk_ticker",
            placeholder="SPY",
            help=(
                "Enter a ticker symbol. Yahoo Finance is being used because no "
                "E*TRADE portfolio client is currently available."
            ),
            on_change=_normalize_risk_ticker_session_value,
        )
    selected = str(raw or current).strip().upper() or current

    quote_data = st.session_state.get("risk_quote_data")
    quote_symbol = str(st.session_state.get("risk_quote_symbol") or "").strip().upper()
    quote_source = str(st.session_state.get("risk_quote_source") or "").strip().upper()
    needs_quote = (
        quote_symbol != selected
        or quote_source != "YAHOO FINANCE"
        or not isinstance(quote_data, dict)
        or not quote_data
    )

    if needs_quote:
        try:
            quote_data = _v9._quote_summary_with_defaults(
                _yfinance_quote_payload(selected)
            )
            st.session_state["risk_quote_data"] = quote_data
            st.session_state["risk_quote_symbol"] = selected
            st.session_state["risk_quote_source"] = "YAHOO FINANCE"
            st.session_state.pop("_risk_v9_quote_error", None)
        except Exception as exc:
            st.session_state["risk_quote_symbol"] = ""
            st.warning(f"Yahoo Finance quote load failed for {selected}: {exc}")
            quote_data = None

    _render_company_display(company_col, selected)

    if quote_data and st.session_state.get("risk_quote_symbol") == selected:
        q1, q2, q3, q4 = st.columns(4, gap="small")
        _v9._compact_metric_box(
            q1,
            "LAST",
            f"${float(quote_data.get('last') or 0.0):,.2f}",
            "positive",
            help_text="Latest price returned by Yahoo Finance. Reconnect E*TRADE for broker-native quotes.",
        )
        _v9._compact_metric_box(
            q2,
            "BID",
            f"${float(quote_data.get('bid') or 0.0):,.2f}",
            "blue",
            help_text="Bid returned by Yahoo Finance when available.",
        )
        _v9._compact_metric_box(
            q3,
            "ASK",
            f"${float(quote_data.get('ask') or 0.0):,.2f}",
            "blue",
            help_text="Ask returned by Yahoo Finance; latest price is used only when Yahoo has no usable ask.",
        )
        change = float(quote_data.get("change") or 0.0)
        _v9._compact_metric_box(
            q4,
            "CHANGE",
            f"{change:+.2f}",
            "positive" if change >= 0 else "negative",
            help_text="Price change returned or derived from Yahoo Finance.",
        )

def _safe_full_width_ticker_columns(original_columns, original_container, original_empty):
    """Do NOT intercept columns; CSS handles the one ticker row."""
    def wrapped(spec, *args, **kwargs):
        return original_columns(spec, *args, **kwargs)
    return wrapped


@st.fragment
def render_risk_sizing(*args, **kwargs):
    """Render Risk Sizing as one reactive fragment, then restore local hooks."""
    previous_css = _v9._render_css
    previous_ticker = _v9._auto_quote_ticker_input
    previous_columns = _v9._full_width_ticker_columns
    previous_subheader = st.subheader
    previous_caption = st.caption

    def filtered_subheader(body, *sub_args, **sub_kwargs):
        if str(body).strip().upper() == "RISK SIZING":
            return None
        return previous_subheader(body, *sub_args, **sub_kwargs)

    def filtered_caption(body, *caption_args, **caption_kwargs):
        if str(body).strip().upper().startswith("CROWN MACRO RISK ENGINE //"):
            return None
        return previous_caption(body, *caption_args, **caption_kwargs)

    _v9._render_css = _render_css_v10
    _v9._auto_quote_ticker_input = _safe_auto_quote_ticker_input
    _v9._full_width_ticker_columns = _safe_full_width_ticker_columns
    st.subheader = filtered_subheader
    st.caption = filtered_caption

    try:
        client = args[0] if args else kwargs.get("client")
        if client is None and _v9._v2._load_persisted_risk_book_snapshot() is None:
            return _render_disconnected_ticker_fallback()
        return _v9.render_risk_sizing(*args, **kwargs)
    finally:
        # REQUIRED: always restore the underlying module hooks so a Risk Sizing
        # fragment rerun cannot leak behavior into another terminal feature.
        _v9._render_css = previous_css
        _v9._auto_quote_ticker_input = previous_ticker
        _v9._full_width_ticker_columns = previous_columns
        st.subheader = previous_subheader
        st.caption = previous_caption


__all__ = ["render_risk_sizing"]
