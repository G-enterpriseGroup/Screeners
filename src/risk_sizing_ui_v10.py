"""Fail-safe production Risk Sizing v10.

OWNERSHIP / EDITING NOTES
-------------------------
THIS IS THE PRODUCTION OWNER for Risk Sizing Part 2 interaction safety.

EDIT THIS FILE for:
- ticker search / ticker-company selection behavior;
- automatic E*TRADE quote loading after ticker selection;
- ASK -> Entry and 5%-below-ASK Stop interaction plumbing;
- Part 2 fail-safe behavior when autocomplete fails;
- narrowly scoped Part 2 layout fixes;
- live E*TRADE stock entry review, placement, fill-check, protective-stop handoff,
  and compact open-order status/cancellation controls.

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
2) ticker entry stays a native symbol input with a separate company display.

The ticker control is a direct symbol input. Company / ETF name is rendered in
a separate read-only display box. Entering a ticker automatically fetches
E*TRADE, seeds Entry from ASK and Stop at 5% below ASK, and leaves no manual
quote button.
"""

from __future__ import annotations

import html
import inspect
import math
import secrets
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import src.risk_sizing_ui_v9 as _v9
from src.etrade_client import ETradeError


_BASE_RENDER_CSS = _v9._render_css

_RISK_STOP_WATCH_COMPONENT_PATH = (
    Path(__file__).parent / "components" / "risk_book_state_v1"
)
_risk_stop_watch_component = components.declare_component(
    "raj_risk_stop_watch_state_v1",
    path=str(_RISK_STOP_WATCH_COMPONENT_PATH),
)


def _render_css_v10() -> None:
    _BASE_RENDER_CSS()
    st.html(
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

        /* Parts 3/4: explicit standalone stack below Part 2. No negative
           margins, fixed content heights, absolute positioning, or overlap. */
        .st-key-risk_live_order_panel{
            width:100%!important;
            margin:8px 0 0 0!important;
            padding:0!important;
            overflow:visible!important;
        }
        .st-key-risk_live_order_panel > [data-testid="stVerticalBlock"],
        .st-key-risk_live_order_panel [data-testid="stVerticalBlock"]{
            gap:6px!important;
            overflow:visible!important;
        }
        .st-key-risk_live_order_panel [data-testid="stElementContainer"],
        .st-key-risk_live_order_panel [data-testid="stHtml"]{
            min-height:0!important;
            overflow:visible!important;
        }
        .st-key-risk_live_order_panel [data-testid="stAlert"]{
            margin:0!important;
            height:auto!important;
            min-height:0!important;
            overflow:visible!important;
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
        """
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
    """Render direct ticker entry with E*TRADE-first / Yahoo fallback quotes."""

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


# ==============================
# LIVE E*TRADE STOCK ORDER WORKFLOW
# ==============================

_RISK_ENTRY_REVIEW_KEY = "_risk_live_entry_review"
_RISK_ENTRY_ORDER_KEY = "_risk_live_entry_order"
_RISK_ENTRY_UNCERTAIN_KEY = "_risk_live_entry_uncertain"
_RISK_STOP_REVIEW_KEY = "_risk_live_stop_review"
_RISK_STOP_ORDER_KEY = "_risk_live_stop_order"
_RISK_STOP_UNCERTAIN_KEY = "_risk_live_stop_uncertain"
_RISK_FILL_KEY = "_risk_live_entry_fill"
_RISK_ENTRY_CONFIRM_KEY = "risk_live_entry_confirm"
_RISK_STOP_CONFIRM_KEY = "risk_live_stop_confirm"
_RISK_PENDING_ORDERS_KEY = "_risk_live_pending_orders"
_RISK_CANCEL_CONFIRM_KEY = "_risk_live_cancel_confirm_order"
_RISK_STOP_WATCH_SESSION_KEY = "_risk_stop_watch_state_v1"
_RISK_STOP_WATCH_STORAGE_KEY = "raj-terminal-risk-stop-watch-v1"
_RISK_STOP_WATCH_READY = "READY_TO_SEND"
_RISK_STOP_WATCH_ACTIVE = {"ARMED", "WAITING_FILL", "PARTIAL_FILL"}
_RISK_STOP_WATCH_POLL_SECONDS = 5
_PREVIEW_FRESH_SECONDS = 150.0


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _direct_key(value, wanted: str):
    if not isinstance(value, dict):
        return None
    wanted = str(wanted).casefold()
    for key, child in value.items():
        if str(key).casefold() == wanted:
            return child
    return None


def _find_key(value, wanted: str):
    found = _direct_key(value, wanted)
    if found is not None:
        return found
    if isinstance(value, dict):
        for child in value.values():
            found = _find_key(child, wanted)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, wanted)
            if found is not None:
                return found
    return None


def _collect_key_values(value, wanted: str) -> list:
    values = []
    wanted = str(wanted).casefold()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() == wanted:
                values.extend(_as_list(child))
            values.extend(_collect_key_values(child, wanted))
    elif isinstance(value, list):
        for child in value:
            values.extend(_collect_key_values(child, wanted))
    return values


def _float_values(value, wanted: str) -> list[float]:
    numbers = []
    for child in _collect_key_values(value, wanted):
        try:
            number = float(child)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            numbers.append(number)
    return numbers


def _new_risk_client_order_id(prefix: str) -> str:
    """Return an E*TRADE-safe <=20-character alphanumeric id."""
    clean_prefix = "".join(ch for ch in str(prefix).upper() if ch.isalnum())[:3] or "RS"
    return clean_prefix + secrets.token_hex(8).upper()


_RISK_ORDER_ACCOUNT_KEY = "risk_live_order_account_key"
_RISK_ORDER_DEFAULT_ACCOUNT_SUFFIX = "5474"


def _order_accounts() -> list[dict]:
    return [
        account
        for account in (st.session_state.get("etrade_accounts") or [])
        if isinstance(account, dict) and str(account.get("accountIdKey") or "").strip()
    ]


def _main_risk_account_key() -> str:
    raw_accounts = st.session_state.get("etrade_accounts") or []
    try:
        risk_index = int(st.session_state.get("risk_sizing_account"))
    except (TypeError, ValueError):
        return ""
    if 0 <= risk_index < len(raw_accounts):
        account = raw_accounts[risk_index]
        if isinstance(account, dict):
            return str(account.get("accountIdKey") or "").strip()
    return ""


def _risk_account_index_for_key(account_key: str) -> int | None:
    wanted = str(account_key or "").strip()
    if not wanted:
        return None
    for index, account in enumerate(st.session_state.get("etrade_accounts") or []):
        if not isinstance(account, dict):
            continue
        if str(account.get("accountIdKey") or "").strip() == wanted:
            return index
    return None


def _locked_order_account_key() -> str:
    return (
        str((st.session_state.get(_RISK_ENTRY_ORDER_KEY) or {}).get("account_key") or "").strip()
        or str((st.session_state.get(_RISK_ENTRY_UNCERTAIN_KEY) or {}).get("account_key") or "").strip()
        or str((st.session_state.get(_RISK_STOP_ORDER_KEY) or {}).get("account_key") or "").strip()
        or str((st.session_state.get(_RISK_STOP_UNCERTAIN_KEY) or {}).get("account_key") or "").strip()
    )


def _default_order_account_key(accounts: list[dict]) -> str:
    main_key = _main_risk_account_key()
    if main_key:
        return main_key
    for account in accounts:
        if str(account.get("accountId") or "").strip().endswith(_RISK_ORDER_DEFAULT_ACCOUNT_SUFFIX):
            return str(account.get("accountIdKey") or "").strip()
    return str(accounts[0].get("accountIdKey") or "").strip() if accounts else ""


def _clear_order_review_state() -> None:
    for key in (
        _RISK_ENTRY_REVIEW_KEY,
        _RISK_STOP_REVIEW_KEY,
        _RISK_FILL_KEY,
        _RISK_ENTRY_CONFIRM_KEY,
        _RISK_STOP_CONFIRM_KEY,
    ):
        st.session_state.pop(key, None)


def _sync_main_picker_from_order_picker() -> None:
    selected_key = str(st.session_state.get(_RISK_ORDER_ACCOUNT_KEY) or "").strip()
    selected_index = _risk_account_index_for_key(selected_key)
    if selected_index is not None:
        st.session_state["risk_sizing_account"] = selected_index
    _clear_order_review_state()


def _enforce_locked_account_picker_sync() -> None:
    """Keep both selectors on the submitted-order account while the flow is locked."""
    locked_key = _locked_order_account_key()
    if not locked_key:
        return
    locked_index = _risk_account_index_for_key(locked_key)
    if locked_index is None:
        return
    st.session_state["risk_sizing_account"] = locked_index
    st.session_state[_RISK_ORDER_ACCOUNT_KEY] = locked_key


def _render_order_account_picker() -> dict | None:
    accounts = _order_accounts()
    if not accounts:
        return None

    by_key = {
        str(account.get("accountIdKey") or "").strip(): account
        for account in accounts
    }
    options = list(by_key)
    locked_order = (
        st.session_state.get(_RISK_ENTRY_ORDER_KEY)
        or st.session_state.get(_RISK_ENTRY_UNCERTAIN_KEY)
        or st.session_state.get(_RISK_STOP_ORDER_KEY)
        or st.session_state.get(_RISK_STOP_UNCERTAIN_KEY)
    )
    locked_key = _locked_order_account_key()
    main_key = _main_risk_account_key()
    current = str(st.session_state.get(_RISK_ORDER_ACCOUNT_KEY) or "").strip()

    # The main E*TRADE Risk picker is the source of truth during normal sizing.
    # If the user changes Part 3, its callback updates risk_sizing_account first,
    # so the next fragment run brings both selectors back to the same account.
    desired = locked_key if locked_key in by_key else main_key
    if desired not in by_key:
        desired = _default_order_account_key(accounts)
    if current != desired:
        st.session_state[_RISK_ORDER_ACCOUNT_KEY] = desired

    selected_key = st.selectbox(
        "Order Account",
        options,
        key=_RISK_ORDER_ACCOUNT_KEY,
        format_func=lambda key: _risk_account_label(by_key[key]),
        on_change=_sync_main_picker_from_order_picker,
        disabled=bool(locked_order),
        help=(
            "Choose the E*TRADE account that will receive the live order. "
            "Raj's account ending 5474 is the default when available. "
            "After a live entry is submitted, this account stays locked for the protective-stop workflow."
        ),
    )
    return by_key.get(str(selected_key))


def _risk_account_label(account: dict) -> str:
    name = str(
        account.get("accountName")
        or account.get("accountDesc")
        or "E*TRADE ACCOUNT"
    ).strip()
    account_id = str(account.get("accountId") or "").strip()
    suffix = account_id[-4:] if account_id else ""
    return f"{name} ••••{suffix}" if suffix else name


def _build_equity_preview_payload(
    *,
    symbol: str,
    quantity: int,
    action: str,
    price_type: str,
    limit_price: float = 0.0,
    stop_price: float = 0.0,
    order_term: str,
    client_order_id: str,
) -> dict:
    symbol = str(symbol or "").strip().upper()
    action = str(action or "").strip().upper()
    price_type = str(price_type or "").strip().upper()
    order_term = str(order_term or "").strip().upper()
    client_order_id = str(client_order_id or "").strip().upper()
    quantity = int(quantity or 0)
    if not symbol or quantity <= 0:
        raise ValueError("Ticker and whole-share quantity are required.")
    if action not in {"BUY", "SELL"}:
        raise ValueError("Risk Sizing supports BUY entry and SELL protective-stop orders only.")
    if price_type not in {"LIMIT", "STOP"}:
        raise ValueError("Risk Sizing live orders support LIMIT entry and STOP protection only.")
    if order_term not in {"GOOD_FOR_DAY", "GOOD_UNTIL_CANCEL"}:
        raise ValueError("Unsupported E*TRADE order duration.")
    if not client_order_id.isalnum() or len(client_order_id) > 20:
        raise ValueError("Client order id must be <=20 alphanumeric characters.")

    order = {
        "allOrNone": False,
        "priceType": price_type,
        "limitPrice": 0.0,
        "stopPrice": 0.0,
        "orderTerm": order_term,
        "marketSession": "REGULAR",
        "Instrument": [
            {
                "Product": {"securityType": "EQ", "symbol": symbol},
                "orderAction": action,
                "quantityType": "QUANTITY",
                "quantity": quantity,
            }
        ],
    }
    if price_type == "LIMIT":
        limit_value = float(limit_price)
        if not math.isfinite(limit_value) or limit_value <= 0:
            raise ValueError("Entry limit price must be positive.")
        order["limitPrice"] = round(limit_value, 2)
    else:
        stop_value = float(stop_price)
        if not math.isfinite(stop_value) or stop_value <= 0:
            raise ValueError("Protective stop price must be positive.")
        order["stopPrice"] = round(stop_value, 2)

    return {
        "PreviewOrderRequest": {
            "orderType": "EQ",
            "clientOrderId": client_order_id,
            "Order": [order],
        }
    }


def _preview_details(payload: dict) -> dict:
    response = _find_key(payload, "PreviewOrderResponse")
    response = response if isinstance(response, dict) else payload
    preview_id = _find_key(response, "previewId")
    messages = []
    for item in _collect_key_values(response, "Message"):
        if isinstance(item, dict):
            parts = [
                str(part)
                for part in (
                    item.get("type"),
                    item.get("code"),
                    item.get("description") or item.get("message"),
                )
                if part not in (None, "")
            ]
            if parts:
                messages.append(" // ".join(parts))
        elif item not in (None, ""):
            messages.append(str(item))
    return {
        "preview_id": preview_id,
        "messages": messages,
        "total_order_value": _finite_number(_find_key(response, "totalOrderValue")),
        "estimated_commission": _finite_number(_find_key(response, "estimatedCommission")),
    }


def _build_place_payload(preview_payload: dict, preview_id) -> dict:
    request = preview_payload.get("PreviewOrderRequest")
    if not isinstance(request, dict) or preview_id in (None, ""):
        raise ValueError("A successful E*TRADE preview is required before placement.")
    return {
        "PlaceOrderRequest": {
            "orderType": request.get("orderType"),
            "clientOrderId": request.get("clientOrderId"),
            "PreviewIds": [{"previewId": preview_id}],
            "Order": request.get("Order") or [],
        }
    }


def _extract_order_id(payload: dict):
    order_ids = _find_key(payload, "OrderIds")
    for item in _as_list(order_ids):
        if isinstance(item, dict):
            order_id = _direct_key(item, "orderId")
        else:
            order_id = item
        if order_id not in (None, ""):
            return order_id
    return _find_key(payload, "orderId")


def _matching_order_records(value, order_id) -> list[dict]:
    wanted = str(order_id)
    records = []
    if isinstance(value, dict):
        direct_id = _direct_key(value, "orderId")
        if direct_id is not None and str(direct_id) == wanted:
            records.append(value)
        for child in value.values():
            records.extend(_matching_order_records(child, order_id))
    elif isinstance(value, list):
        for child in value:
            records.extend(_matching_order_records(child, order_id))
    return records


def _order_fill_snapshot(payload: dict, order_id, expected_quantity: int) -> dict:
    records = _matching_order_records(payload, order_id)
    if not records:
        return {
            "found": False,
            "full": False,
            "partial": False,
            "status": "NOT FOUND",
            "filled": 0.0,
            "ordered": float(expected_quantity),
            "average_price": None,
        }

    record = max(records, key=lambda item: len(str(item)))
    statuses = [str(item).upper() for item in _collect_key_values(record, "status") if item not in (None, "")]
    events = [str(item).upper() for item in _collect_key_values(record, "name") if item not in (None, "")]
    ordered_values = _float_values(record, "orderedQuantity")
    filled_values = _float_values(record, "filledQuantity")
    average_values = _float_values(record, "averageExecutionPrice")

    ordered = max(ordered_values) if ordered_values else float(expected_quantity)
    filled = max(filled_values) if filled_values else 0.0
    status = statuses[0] if statuses else ("ORDER_EXECUTED" if "ORDER_EXECUTED" in events else "OPEN")
    executed = status == "EXECUTED" or "ORDER_EXECUTED" in events or "DONE_TRADE_EXECUTED" in events
    partial = status == "INDIVIDUAL_FILLS" or (filled > 0 and ordered > 0 and filled + 1e-9 < ordered)
    full = not partial and ((ordered > 0 and filled + 1e-9 >= ordered) or executed)
    if full and filled <= 0:
        filled = ordered
    return {
        "found": True,
        "full": bool(full),
        "partial": bool(partial),
        "status": status,
        "filled": filled,
        "ordered": ordered,
        "average_price": max(average_values) if average_values else None,
    }


def _pending_order_rows(payload: dict) -> list[dict]:
    """Normalize cancellable/pending E*TRADE order records for compact display."""
    response = _find_key(payload, "OrdersResponse")
    response = response if isinstance(response, dict) else payload
    raw_orders = _direct_key(response, "Order") if isinstance(response, dict) else None
    pending_statuses = {"OPEN", "INDIVIDUAL_FILLS", "CANCEL_REQUESTED"}
    rows_by_id: dict[str, dict] = {}

    for order in _as_list(raw_orders):
        if not isinstance(order, dict):
            continue
        order_id = _direct_key(order, "orderId")
        if order_id in (None, ""):
            order_id = _direct_key(order, "orderNumber")
        if order_id in (None, ""):
            continue

        details = _direct_key(order, "OrderDetail")
        if details is None:
            details = _direct_key(order, "orderDetail")
        for detail in _as_list(details or order):
            if not isinstance(detail, dict):
                continue
            status = str(_direct_key(detail, "status") or "").strip().upper()
            if status not in pending_statuses:
                continue

            instruments = _direct_key(detail, "Instrument")
            if instruments is None:
                instruments = _direct_key(detail, "instrument")
            instrument = next(
                (item for item in _as_list(instruments) if isinstance(item, dict)),
                {},
            )
            product = _direct_key(instrument, "Product")
            product = product if isinstance(product, dict) else {}

            symbol = str(
                _direct_key(product, "symbol")
                or _direct_key(instrument, "symbol")
                or _direct_key(instrument, "symbolDescription")
                or "—"
            ).strip().upper()
            action = str(_direct_key(instrument, "orderAction") or "—").strip().upper()
            quantity = (
                _direct_key(instrument, "orderedQuantity")
                or _direct_key(instrument, "quantity")
                or 0
            )
            filled = _direct_key(instrument, "filledQuantity") or 0
            price_type = str(_direct_key(detail, "priceType") or "—").strip().upper()
            order_term = str(_direct_key(detail, "orderTerm") or "—").strip().upper()
            limit_price = _direct_key(detail, "limitPrice")
            stop_price = _direct_key(detail, "stopPrice")
            placed_time = _direct_key(detail, "placedTime") or 0

            row = {
                "order_id": order_id,
                "status": status,
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "filled": filled,
                "price_type": price_type,
                "order_term": order_term,
                "limit_price": limit_price,
                "stop_price": stop_price,
                "placed_time": placed_time,
            }
            rows_by_id[str(order_id)] = row

    def sort_value(row: dict) -> float:
        try:
            return float(row.get("placed_time") or 0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(rows_by_id.values(), key=sort_value, reverse=True)


def _load_pending_order_rows(client, account_key: str, touch_session) -> list[dict]:
    """Fetch all broker statuses that may still require user action."""
    merged: dict[str, dict] = {}
    for status in ("OPEN", "INDIVIDUAL_FILLS", "CANCEL_REQUESTED"):
        payload = client.list_orders(account_key, status=status, count=100)
        for row in _pending_order_rows(payload):
            merged[str(row["order_id"])] = row
    touch_session()

    def sort_value(row: dict) -> float:
        try:
            return float(row.get("placed_time") or 0)
        except (TypeError, ValueError):
            return 0.0

    return sorted(merged.values(), key=sort_value, reverse=True)


def _pending_order_account() -> dict | None:
    accounts = _order_accounts()
    if not accounts:
        return None
    by_key = {
        str(account.get("accountIdKey") or "").strip(): account
        for account in accounts
    }
    selected_key = (
        _locked_order_account_key()
        or str(st.session_state.get(_RISK_ORDER_ACCOUNT_KEY) or "").strip()
        or _main_risk_account_key()
        or _default_order_account_key(accounts)
    )
    return by_key.get(selected_key)


def _format_pending_order_price(row: dict) -> str:
    price_type = str(row.get("price_type") or "—").upper()
    try:
        limit_price = float(row.get("limit_price") or 0.0)
    except (TypeError, ValueError):
        limit_price = 0.0
    try:
        stop_price = float(row.get("stop_price") or 0.0)
    except (TypeError, ValueError):
        stop_price = 0.0

    if price_type == "LIMIT" and limit_price > 0:
        return f"LIMIT USD {limit_price:,.2f}"
    if price_type == "STOP" and stop_price > 0:
        return f"STOP USD {stop_price:,.2f}"
    if price_type == "STOP_LIMIT":
        return f"STOP USD {stop_price:,.2f} / LIMIT USD {limit_price:,.2f}"
    return price_type


def _mark_cached_cancel_requested(account_key: str, order_id) -> None:
    cache = st.session_state.get(_RISK_PENDING_ORDERS_KEY)
    if not isinstance(cache, dict) or cache.get("account_key") != account_key:
        return
    for row in cache.get("rows") or []:
        if str(row.get("order_id")) == str(order_id):
            row["status"] = "CANCEL_REQUESTED"


def _render_pending_orders_panel(client, touch_session) -> None:
    """Render open E*TRADE orders at the bottom of Risk with safe cancellation."""
    st.html('<div class="risk-v9-section">5. PENDING / OPEN E*TRADE ORDERS</div>')
    st.caption(
        "LIVE BROKER ORDERS ONLY // OPEN, PARTIAL, AND CANCEL-REQUESTED // "
        "E*TRADE'S PUBLIC API DOES NOT EXPOSE BROKER-SAVED DRAFT ORDERS"
    )

    if client is None or not hasattr(client, "list_orders"):
        st.info("PENDING ORDERS UNAVAILABLE // connect live E*TRADE.")
        return

    account = _pending_order_account()
    if account is None:
        st.info("PENDING ORDERS UNAVAILABLE // select an E*TRADE account first.")
        return

    account_key = str(account.get("accountIdKey") or "").strip()
    account_label = _risk_account_label(account)
    cache = st.session_state.get(_RISK_PENDING_ORDERS_KEY)
    needs_load = not isinstance(cache, dict) or cache.get("account_key") != account_key

    refresh_clicked = st.button(
        "REFRESH PENDING ORDERS",
        key="risk_live_refresh_pending_orders",
        width="stretch",
    )
    if needs_load or refresh_clicked:
        try:
            rows = _load_pending_order_rows(client, account_key, touch_session)
            cache = {"account_key": account_key, "rows": rows, "loaded_at": time.time()}
            st.session_state[_RISK_PENDING_ORDERS_KEY] = cache
            if refresh_clicked:
                st.session_state.pop(_RISK_CANCEL_CONFIRM_KEY, None)
        except Exception as exc:
            st.error(f"PENDING ORDER REFRESH FAILED // {exc}")
            if not isinstance(cache, dict) or cache.get("account_key") != account_key:
                return

    rows = list((cache or {}).get("rows") or [])
    st.caption(f"ACCOUNT {account_label} // {len(rows)} PENDING / OPEN ORDER(S)")
    if not rows:
        st.info("NO OPEN, PARTIALLY FILLED, OR CANCEL-REQUESTED E*TRADE ORDERS.")
        return

    for row in rows:
        order_id = row.get("order_id")
        status = str(row.get("status") or "UNKNOWN").upper()
        symbol = str(row.get("symbol") or "—")
        action = str(row.get("action") or "—")
        try:
            quantity = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        try:
            filled = float(row.get("filled") or 0)
        except (TypeError, ValueError):
            filled = 0.0
        qty_text = f"{quantity:g}"
        if status == "INDIVIDUAL_FILLS" and filled > 0:
            qty_text = f"{filled:g}/{quantity:g}"

        info_col, cancel_col = st.columns([6, 1.35], gap="small")
        info_col.caption(
            f"ORDER {order_id} // {status} // {action} {qty_text} {symbol} // "
            f"{_format_pending_order_price(row)} // {str(row.get('order_term') or '—')}"
        )

        cancellable = status in {"OPEN", "INDIVIDUAL_FILLS"}
        if cancellable and hasattr(client, "cancel_order"):
            if cancel_col.button(
                "CANCEL",
                key=f"risk_live_cancel_order_{account_key}_{order_id}",
                width="stretch",
            ):
                st.session_state[_RISK_CANCEL_CONFIRM_KEY] = str(order_id)
        else:
            cancel_col.caption("CANCEL PENDING" if status == "CANCEL_REQUESTED" else "—")

        if str(st.session_state.get(_RISK_CANCEL_CONFIRM_KEY) or "") != str(order_id):
            continue

        if action == "SELL" and str(row.get("price_type") or "").upper() == "STOP":
            st.warning(
                f"PROTECTIVE STOP WARNING // Canceling order {order_id} removes this live stop protection."
            )
        else:
            st.warning(
                f"CONFIRM CANCEL // E*TRADE order {order_id} // {action} {qty_text} {symbol}"
            )

        confirm_col, keep_col = st.columns([2, 1], gap="small")
        if confirm_col.button(
            f"CONFIRM CANCEL ORDER {order_id}",
            type="primary",
            key=f"risk_live_confirm_cancel_{account_key}_{order_id}",
            width="stretch",
        ):
            try:
                client.cancel_order(account_key, order_id)
                touch_session()
                _mark_cached_cancel_requested(account_key, order_id)
                st.session_state.pop(_RISK_CANCEL_CONFIRM_KEY, None)
                st.success(
                    f"CANCEL REQUEST SUBMITTED // E*TRADE ORDER {order_id} // "
                    "refresh pending orders to verify final broker status."
                )
            except Exception as exc:
                st.error(f"CANCEL REQUEST FAILED // E*TRADE ORDER {order_id} // {exc}")

        if keep_col.button(
            "KEEP ORDER",
            key=f"risk_live_keep_order_{account_key}_{order_id}",
            width="stretch",
        ):
            st.session_state.pop(_RISK_CANCEL_CONFIRM_KEY, None)


def _preview_is_fresh(review: dict | None) -> bool:
    if not isinstance(review, dict):
        return False
    try:
        age = time.time() - float(review.get("previewed_at") or 0.0)
    except (TypeError, ValueError):
        return False
    return 0.0 <= age <= _PREVIEW_FRESH_SECONDS


def _clear_risk_order_workflow() -> None:
    for key in (
        _RISK_ENTRY_REVIEW_KEY,
        _RISK_ENTRY_ORDER_KEY,
        _RISK_ENTRY_UNCERTAIN_KEY,
        _RISK_STOP_REVIEW_KEY,
        _RISK_STOP_ORDER_KEY,
        _RISK_STOP_UNCERTAIN_KEY,
        _RISK_FILL_KEY,
        _RISK_ENTRY_CONFIRM_KEY,
        _RISK_STOP_CONFIRM_KEY,
    ):
        st.session_state.pop(key, None)


def _current_stock_order_context(trade_kwargs: dict, account: dict | None) -> dict | None:
    if str(st.session_state.get("risk_trade_structure") or "").upper() != "STOCK / ETF":
        return None

    if not account:
        return None
    account_key = str(account.get("accountIdKey") or "").strip()
    symbol = str(st.session_state.get("risk_ticker") or "").strip().upper()
    try:
        entry_price = float(st.session_state.get("risk_entry_price") or 0.0)
        stop_price = float(st.session_state.get("risk_stop_price") or 0.0)
        size_multiplier = float(st.session_state.get("risk_size_multiplier") or 1.0)
        investable_assets = float(trade_kwargs.get("investable_assets") or 0.0)
        tactical_sleeve_pct = float(trade_kwargs.get("tactical_sleeve_pct") or 0.0)
        full_position_risk_pct = float(trade_kwargs.get("full_position_risk_pct") or 0.0)
    except (TypeError, ValueError):
        return None
    if not account_key or not symbol or entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        return None

    summary = trade_kwargs.get("summary") or {}
    cash_available = float(trade_kwargs.get("cash_available") or 0.0)
    if bool(st.session_state.get("risk_capital_source_tactical")):
        capital_limit = max(0.0, float(summary.get("target_room") or 0.0))
    else:
        try:
            liquid = float(st.session_state.get("risk_liquid_balance"))
        except (TypeError, ValueError):
            liquid = cash_available
        capital_limit = max(0.0, liquid)

    try:
        risk_budget = _v9._v2.crown_risk_budget(
            investable_assets,
            tactical_sleeve_pct,
            full_position_risk_pct,
            size_multiplier,
        )
        sized = _v9._v2.stock_position_size(
            entry_price,
            stop_price,
            risk_budget["selected_risk_budget"],
            capital_limit=capital_limit,
        )
    except (KeyError, TypeError, ValueError):
        return None
    shares = int(sized.get("shares") or 0)
    if shares <= 0:
        return None

    fingerprint = (
        account_key,
        symbol,
        shares,
        round(entry_price, 2),
        round(stop_price, 2),
    )
    return {
        "account_key": account_key,
        "account_label": _risk_account_label(account),
        "symbol": symbol,
        "quantity": shares,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "fingerprint": fingerprint,
    }


def _render_preview_messages(review: dict) -> None:
    details = review.get("details") or {}
    total = details.get("total_order_value")
    commission = details.get("estimated_commission")
    if total is not None or commission is not None:
        parts = []
        if total is not None:
            parts.append(f"EST. VALUE USD {float(total):,.2f}")
        if commission is not None:
            parts.append(f"EST. COMMISSION USD {float(commission):,.2f}")
        st.caption("E*TRADE PREVIEW // " + " // ".join(parts))
    for message in details.get("messages") or []:
        st.warning("E*TRADE PREVIEW // " + str(message))


def _preview_live_order(client, account_key: str, preview_payload: dict, touch_session) -> dict:
    response = client.preview_order(account_key, preview_payload)
    touch_session()
    details = _preview_details(response)
    if details.get("preview_id") in (None, ""):
        raise ETradeError("E*TRADE preview returned no preview ID; nothing can be submitted.")
    return {
        "preview_payload": preview_payload,
        "place_payload": _build_place_payload(preview_payload, details["preview_id"]),
        "details": details,
        "previewed_at": time.time(),
    }


def _preview_protective_stop(client, entry_order: dict, touch_session) -> dict:
    """Broker-preview the exact GTC stop tied to one fully filled Risk entry."""
    stop_fingerprint = (
        str(entry_order["account_key"]),
        str(entry_order["order_id"]),
        str(entry_order["symbol"]),
        int(entry_order["quantity"]),
        round(float(entry_order["stop_price"]), 2),
    )
    stop_preview_payload = _build_equity_preview_payload(
        symbol=entry_order["symbol"],
        quantity=int(entry_order["quantity"]),
        action="SELL",
        price_type="STOP",
        stop_price=float(entry_order["stop_price"]),
        order_term="GOOD_UNTIL_CANCEL",
        client_order_id=_new_risk_client_order_id("RPS"),
    )
    stop_review = _preview_live_order(
        client,
        entry_order["account_key"],
        stop_preview_payload,
        touch_session,
    )
    stop_review.update(
        {
            "fingerprint": stop_fingerprint,
            "account_key": entry_order["account_key"],
            "account_label": entry_order["account_label"],
            "symbol": entry_order["symbol"],
            "quantity": int(entry_order["quantity"]),
            "stop_price": float(entry_order["stop_price"]),
        }
    )
    return stop_review


def _place_reviewed_protective_stop(client, stop_review: dict, touch_session) -> bool:
    """Place one user-authorized protective stop after a successful broker preview."""
    uncertain_stop = {
        key: stop_review[key]
        for key in ("account_key", "account_label", "symbol", "quantity", "stop_price")
    }
    st.session_state[_RISK_STOP_UNCERTAIN_KEY] = uncertain_stop
    try:
        placed_stop = client.place_order(
            stop_review["account_key"],
            stop_review["place_payload"],
        )
        touch_session()
        stop_order_id = _extract_order_id(placed_stop)
        if stop_order_id in (None, ""):
            raise ETradeError("E*TRADE returned no order ID after protective-stop placement.")
        st.session_state[_RISK_STOP_ORDER_KEY] = {
            **uncertain_stop,
            "order_id": stop_order_id,
            "placed_at": time.time(),
        }
        st.session_state.pop(_RISK_STOP_UNCERTAIN_KEY, None)
        st.session_state.pop(_RISK_STOP_REVIEW_KEY, None)
        st.session_state.pop(_RISK_STOP_CONFIRM_KEY, None)
        return True
    except Exception as exc:
        st.error(
            "STOP SUBMISSION STATUS UNCERTAIN // "
            + str(exc)
            + " // Check E*TRADE Orders before resetting or retrying."
        )
        return False


def _render_stop_submitted_confirmation(stop_order: dict) -> None:
    """Show the broker order id immediately without forcing another rerun."""
    st.success(
        f"PROTECTIVE STOP SUBMITTED // E*TRADE ORDER {stop_order['order_id']} // "
        f"SELL {int(stop_order['quantity']):,} {stop_order['symbol']} @ STOP "
        f"USD {float(stop_order['stop_price']):,.2f} // GTC"
    )


def _render_live_order_workflow(client, touch_session, trade_kwargs: dict) -> None:
    st.html('<div class="risk-v9-section">3. PICK E*TRADE ACCOUNT</div>')

    entry_order = st.session_state.get(_RISK_ENTRY_ORDER_KEY)
    entry_uncertain = st.session_state.get(_RISK_ENTRY_UNCERTAIN_KEY)
    stop_order = st.session_state.get(_RISK_STOP_ORDER_KEY)
    stop_uncertain = st.session_state.get(_RISK_STOP_UNCERTAIN_KEY)

    if client is None:
        st.warning("ACCOUNT PICKER BLOCKED // connect E*TRADE to load order accounts.")
        st.html('<div class="risk-v9-section">4. REVIEW + SEND ORDER</div>')
        st.info("LIVE ORDERING BLOCKED // connect E*TRADE before reviewing or sending an order.")
        return

    order_account = _render_order_account_picker()
    if order_account is None:
        st.warning("ACCOUNT PICKER BLOCKED // no E*TRADE accounts are currently available.")
        st.html('<div class="risk-v9-section">4. REVIEW + SEND ORDER</div>')
        st.info("LIVE ORDERING BLOCKED // refresh or reconnect E*TRADE account data.")
        return

    st.html('<div class="risk-v9-section">4. REVIEW + SEND ORDER</div>')
    st.caption(
        "PROTECTED ENTRY WORKFLOW // BUY LIMIT FIRST // AFTER A FULL FILL, "
        "ONE ACTION RECHECKS THE FILL + SENDS THE GTC SELL STOP"
    )

    if entry_uncertain and not entry_order:
        st.error(
            "ENTRY SUBMISSION STATUS UNCERTAIN // Check E*TRADE Orders before doing anything else. "
            "The terminal has blocked retries so it cannot accidentally duplicate the buy."
        )
        st.caption(
            f"PENDING CHECK // {entry_uncertain.get('account_label','')} // "
            f"BUY {entry_uncertain.get('quantity',0)} {entry_uncertain.get('symbol','')} "
            f"LIMIT USD {float(entry_uncertain.get('entry_price') or 0):,.2f}"
        )
        if st.button(
            "I CHECKED E*TRADE ORDERS — RESET ENTRY WORKFLOW",
            key="risk_live_reset_uncertain_entry",
            width="stretch",
        ):
            _clear_risk_order_workflow()
            st.rerun(scope="fragment")
        return

    if not entry_order:
        context = _current_stock_order_context(trade_kwargs, order_account)
        if context is None:
            st.info("LIVE ORDER BLOCKED // valid STOCK / ETF sizing with at least 1 MAX SHARE is required.")
            return

        review = st.session_state.get(_RISK_ENTRY_REVIEW_KEY)
        if isinstance(review, dict) and review.get("fingerprint") != context["fingerprint"]:
            st.session_state.pop(_RISK_ENTRY_REVIEW_KEY, None)
            st.session_state.pop(_RISK_ENTRY_CONFIRM_KEY, None)
            review = None

        st.caption(
            f"ACCOUNT {context['account_label']} // BUY {context['quantity']:,} {context['symbol']} // "
            f"LIMIT USD {context['entry_price']:,.2f} // DAY // PLANNED STOP USD {context['stop_price']:,.2f}"
        )
        if st.button(
            "REVIEW BUY LIMIT WITH E*TRADE",
            type="primary",
            key="risk_live_preview_entry",
            width="stretch",
        ):
            try:
                client_order_id = _new_risk_client_order_id("RBE")
                preview_payload = _build_equity_preview_payload(
                    symbol=context["symbol"],
                    quantity=context["quantity"],
                    action="BUY",
                    price_type="LIMIT",
                    limit_price=context["entry_price"],
                    order_term="GOOD_FOR_DAY",
                    client_order_id=client_order_id,
                )
                review = _preview_live_order(
                    client,
                    context["account_key"],
                    preview_payload,
                    touch_session,
                )
                review.update(context)
                st.session_state[_RISK_ENTRY_REVIEW_KEY] = review
                st.session_state.pop(_RISK_ENTRY_CONFIRM_KEY, None)
            except Exception as exc:
                st.error(f"E*TRADE ENTRY PREVIEW FAILED // {exc}")
                return

        review = st.session_state.get(_RISK_ENTRY_REVIEW_KEY)
        if not review:
            return
        if not _preview_is_fresh(review):
            st.session_state.pop(_RISK_ENTRY_REVIEW_KEY, None)
            st.session_state.pop(_RISK_ENTRY_CONFIRM_KEY, None)
            st.warning("ENTRY PREVIEW EXPIRED // Review again before sending. E*TRADE preview IDs are short-lived.")
            return

        st.success("FINAL REVIEW // E*TRADE preview accepted. This next action submits a LIVE buy order.")
        st.caption(
            f"{review['account_label']} // BUY {int(review['quantity']):,} {review['symbol']} // "
            f"LIMIT USD {float(review['entry_price']):,.2f} // DAY // "
            f"PLANNED GTC STOP USD {float(review['stop_price']):,.2f} // "
            "STOP SENDS ONLY AFTER E*TRADE CONFIRMS THE FULL FILL"
        )
        _render_preview_messages(review)
        confirmed = st.checkbox(
            "I CONFIRM THIS LIVE BUY LIMIT AND THE PLANNED PROTECTIVE STOP SHOWN ABOVE",
            key=_RISK_ENTRY_CONFIRM_KEY,
        )
        if st.button(
            "SEND LIVE BUY LIMIT // STOP FOLLOWS AFTER FULL FILL",
            type="primary",
            key="risk_live_send_entry",
            width="stretch",
            disabled=not confirmed,
        ):
            if not _preview_is_fresh(review):
                st.session_state.pop(_RISK_ENTRY_REVIEW_KEY, None)
                st.session_state.pop(_RISK_ENTRY_CONFIRM_KEY, None)
                st.error("ENTRY PREVIEW EXPIRED // Review again. No live order was sent.")
                return
            uncertain = {
                key: review[key]
                for key in ("account_key", "account_label", "symbol", "quantity", "entry_price", "stop_price")
            }
            st.session_state[_RISK_ENTRY_UNCERTAIN_KEY] = uncertain
            try:
                placed = client.place_order(review["account_key"], review["place_payload"])
                touch_session()
                order_id = _extract_order_id(placed)
                if order_id in (None, ""):
                    raise ETradeError("E*TRADE returned no order ID after placement.")
                st.session_state[_RISK_ENTRY_ORDER_KEY] = {
                    **uncertain,
                    "order_id": order_id,
                    "placed_at": time.time(),
                }
                st.session_state.pop(_RISK_ENTRY_UNCERTAIN_KEY, None)
                st.session_state.pop(_RISK_ENTRY_REVIEW_KEY, None)
                st.session_state.pop(_RISK_ENTRY_CONFIRM_KEY, None)
                st.session_state.pop(_RISK_FILL_KEY, None)
                st.rerun(scope="fragment")
            except Exception as exc:
                st.error(
                    "ENTRY SUBMISSION STATUS UNCERTAIN // "
                    + str(exc)
                    + " // Check E*TRADE Orders before resetting or retrying."
                )
            return
        return

    st.success(
        f"ENTRY SUBMITTED // E*TRADE ORDER {entry_order['order_id']} // "
        f"{entry_order['account_label']} // BUY {int(entry_order['quantity']):,} "
        f"{entry_order['symbol']} @ LIMIT USD {float(entry_order['entry_price']):,.2f}"
    )

    if stop_uncertain and not stop_order:
        st.error(
            "STOP SUBMISSION STATUS UNCERTAIN // Check E*TRADE Orders before any retry. "
            "The terminal has blocked duplicate protective-stop submission."
        )
        if st.button(
            "I CHECKED E*TRADE ORDERS — RESET STOP REVIEW",
            key="risk_live_reset_uncertain_stop",
            width="stretch",
        ):
            st.session_state.pop(_RISK_STOP_UNCERTAIN_KEY, None)
            st.session_state.pop(_RISK_STOP_REVIEW_KEY, None)
            st.session_state.pop(_RISK_STOP_CONFIRM_KEY, None)
            st.rerun(scope="fragment")
        return

    if stop_order:
        _render_stop_submitted_confirmation(stop_order)
        st.caption(
            "WORKFLOW COMPLETE // Clearing this terminal workflow does not cancel either E*TRADE order."
        )
        if st.button(
            "CLEAR TERMINAL ORDER WORKFLOW",
            key="risk_live_clear_complete",
            width="stretch",
        ):
            _clear_risk_order_workflow()
            st.rerun(scope="fragment")
        return

    st.caption(
        "PROTECTION HANDOFF // Click once after the entry is submitted. "
        "The terminal rechecks E*TRADE; only a confirmed full fill can trigger the exact planned GTC stop. "
        "Open or partial fills remain blocked."
    )
    if st.button(
        "CHECK FULL FILL + SEND PROTECTIVE STOP",
        type="primary",
        key="risk_live_check_fill_send_stop",
        width="stretch",
    ):
        try:
            orders = client.list_orders(
                entry_order["account_key"],
                symbol=entry_order["symbol"],
                count=100,
            )
            touch_session()
            fill = _order_fill_snapshot(
                orders,
                entry_order["order_id"],
                int(entry_order["quantity"]),
            )
            st.session_state[_RISK_FILL_KEY] = fill
        except Exception as exc:
            st.error(f"E*TRADE FILL CHECK FAILED // {exc}")
            return

        if fill.get("full") and not fill.get("partial"):
            try:
                stop_review = _preview_protective_stop(
                    client,
                    entry_order,
                    touch_session,
                )
                st.session_state[_RISK_STOP_REVIEW_KEY] = stop_review
                st.session_state.pop(_RISK_STOP_CONFIRM_KEY, None)
            except Exception as exc:
                st.error(f"E*TRADE STOP PREVIEW FAILED // {exc}")
                return

            if not (stop_review.get("details") or {}).get("messages"):
                if _place_reviewed_protective_stop(client, stop_review, touch_session):
                    _render_stop_submitted_confirmation(
                        st.session_state[_RISK_STOP_ORDER_KEY]
                    )
                return

            st.warning(
                "E*TRADE STOP PREVIEW RETURNED MESSAGE(S) // "
                "The stop was NOT sent. Review the broker message below before confirming."
            )

    fill = st.session_state.get(_RISK_FILL_KEY)
    if not isinstance(fill, dict):
        st.info(
            "PROTECTIVE STOP PENDING // Use CHECK FULL FILL + SEND PROTECTIVE STOP. "
            "No stop is sent unless E*TRADE confirms the complete entry fill during that action."
        )
        return
    if not fill.get("found"):
        st.warning(
            "ENTRY ORDER NOT FOUND IN THE CURRENT E*TRADE ORDER LIST // Stop remains blocked. "
            "Check E*TRADE Orders before taking further action."
        )
        return

    status = str(fill.get("status") or "UNKNOWN").upper()
    if fill.get("partial"):
        st.warning(
            f"PARTIAL FILL // {float(fill.get('filled') or 0):g} of "
            f"{float(fill.get('ordered') or entry_order['quantity']):g} shares // "
            "Protective stop remains blocked. Manage the partial fill in E*TRADE."
        )
        return
    if not fill.get("full"):
        if status in {"CANCELLED", "EXPIRED", "REJECTED"}:
            st.error(f"ENTRY {status} // Protective stop remains blocked.")
            if st.button(
                "CLEAR TERMINAL ORDER WORKFLOW",
                key="risk_live_clear_terminal_entry",
                width="stretch",
            ):
                _clear_risk_order_workflow()
                st.rerun(scope="fragment")
        else:
            st.info(
                f"ENTRY STATUS {status} // Protective stop was NOT sent. "
                "Click CHECK FULL FILL + SEND PROTECTIVE STOP again after the entry fills."
            )
        return

    avg_price = fill.get("average_price")
    avg_text = f" @ AVG USD {float(avg_price):,.2f}" if avg_price is not None else ""
    st.success(
        f"ENTRY FULLY FILLED // {int(entry_order['quantity']):,} {entry_order['symbol']}{avg_text}"
    )

    stop_review = st.session_state.get(_RISK_STOP_REVIEW_KEY)
    if not isinstance(stop_review, dict):
        st.info(
            "FULL FILL CONFIRMED // Click CHECK FULL FILL + SEND PROTECTIVE STOP "
            "to refresh the broker preview and submit the exact planned stop."
        )
        return
    if not _preview_is_fresh(stop_review):
        st.session_state.pop(_RISK_STOP_REVIEW_KEY, None)
        st.session_state.pop(_RISK_STOP_CONFIRM_KEY, None)
        st.warning(
            "STOP PREVIEW EXPIRED // No stop was sent. "
            "Click CHECK FULL FILL + SEND PROTECTIVE STOP again."
        )
        return

    st.warning(
        "STOP PREVIEW PAUSED FOR BROKER MESSAGE REVIEW // "
        "No protective stop has been sent yet."
    )
    st.caption(
        f"{stop_review['account_label']} // SELL {int(stop_review['quantity']):,} "
        f"{stop_review['symbol']} // STOP USD {float(stop_review['stop_price']):,.2f} // GTC"
    )
    _render_preview_messages(stop_review)
    stop_confirmed = st.checkbox(
        "I REVIEWED THE E*TRADE MESSAGE(S) AND CONFIRM THIS LIVE PROTECTIVE STOP",
        key=_RISK_STOP_CONFIRM_KEY,
    )
    if st.button(
        "SEND LIVE PROTECTIVE STOP AFTER MESSAGE REVIEW",
        type="primary",
        key="risk_live_send_stop",
        width="stretch",
        disabled=not stop_confirmed,
    ):
        if not _preview_is_fresh(stop_review):
            st.session_state.pop(_RISK_STOP_REVIEW_KEY, None)
            st.session_state.pop(_RISK_STOP_CONFIRM_KEY, None)
            st.error("STOP PREVIEW EXPIRED // No live stop was sent.")
            return
        if _place_reviewed_protective_stop(client, stop_review, touch_session):
            _render_stop_submitted_confirmation(
                st.session_state[_RISK_STOP_ORDER_KEY]
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

    client = args[0] if args else kwargs.get("client")
    touch_session = kwargs.get("touch_session") or (lambda: None)

    # This executes before the shared E*TRADE Account widget is instantiated.
    # A submitted live order owns the account until its protective-stop flow is
    # cleared, so both visible account selectors stay on the same broker account.
    _enforce_locked_account_picker_sync()

    def render_live_order_sections(trade_context):
        with st.container(key="risk_live_order_panel"):
            _render_live_order_workflow(client, touch_session, trade_context)
            _render_pending_orders_panel(client, touch_session)

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
        if client is None and _v9._v2._load_persisted_risk_book_snapshot() is None:
            return _render_disconnected_ticker_fallback()
        render_kwargs = dict(kwargs)
        render_kwargs["after_next_trade"] = render_live_order_sections
        return _v9.render_risk_sizing(*args, **render_kwargs)
    finally:
        # REQUIRED: always restore the underlying module hooks so a Risk Sizing
        # fragment rerun cannot leak behavior into another terminal feature.
        _v9._render_css = previous_css
        _v9._auto_quote_ticker_input = previous_ticker
        _v9._full_width_ticker_columns = previous_columns
        st.subheader = previous_subheader
        st.caption = previous_caption


__all__ = ["render_risk_sizing"]
