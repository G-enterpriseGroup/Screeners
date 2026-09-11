"""Raj's Terminal entrypoint.

The large legacy UI/core definitions are loaded from src/terminal_core.py up to
the render boundary. This keeps the terminal framework modular so new tabs can
be added without duplicating the existing analytics code.
"""

from pathlib import Path


_CORE_PATH = Path(__file__).parent / "src" / "terminal_core.py"
_CORE_SOURCE = _CORE_PATH.read_text(encoding="utf-8")
_CORE_MARKER = "\n\nif not _trade_access_unlocked():\n    render_app_lock_screen()\n    st.stop()\n"

if _CORE_MARKER not in _CORE_SOURCE:
    raise RuntimeError("Raj's Terminal core render boundary was not found.")

_CORE_DEFINITIONS = _CORE_SOURCE.split(_CORE_MARKER, 1)[0]
exec(compile(_CORE_DEFINITIONS, str(_CORE_PATH), "exec"), globals())

from src.etrade_data_cache import (
    CACHE_TTLS,
    CachedETradeClient,
    cache_stats,
    clear_session_cache,
)
from src.holdings_snapshot_mode import build_manual_holdings_renderer
from src.risk_sizing_ui_v6 import render_risk_sizing
from src.session_persistence import (
    clear_etrade_session,
    restore_etrade_session,
    save_etrade_session,
)
from src.tab_bar import render_terminal_tab_bar


# Preserve references to the core implementations before installing the
# seamless-session/cache adapters below.
_CORE_ETRADE_CLIENT_FACTORY = _etrade_client
_CORE_TOUCH_ETRADE_SESSION = _touch_etrade_session
_CORE_CLEAR_ETRADE_RUNTIME = _clear_etrade_runtime
_CORE_HOLDINGS_RENDERER = render_etrade_holdings


def _etrade_client():
    """Return one transparent shared-cache layer over the authenticated client.

    Every tab calls this same adapter. The cache is server-memory, token-scoped,
    and therefore survives normal Streamlit reruns and browser refreshes while
    the app process + OAuth session remain alive.
    """
    raw_client = _CORE_ETRADE_CLIENT_FACTORY()
    if raw_client is None:
        return None
    return CachedETradeClient(
        raw_client,
        st.session_state.get("etrade_access_token"),
    )


def _persist_active_etrade_session():
    """Save the active OAuth token server-side without extending its timer."""
    return save_etrade_session(
        _trade_access_code_hash(),
        token=st.session_state.get("etrade_access_token"),
        last_activity_at=st.session_state.get("etrade_last_activity_at"),
        inactivity_seconds=ETRADE_INACTIVITY_SECONDS,
        timezone=ETRADE_TIMEZONE,
        accounts=st.session_state.get("etrade_accounts", []),
    )


def _touch_etrade_session():
    """Core activity touch plus persistence for browser-refresh recovery."""
    _CORE_TOUCH_ETRADE_SESSION()
    _persist_active_etrade_session()


def _clear_etrade_runtime(lock_access=False):
    """LOCK preserves session/cache; DISCONNECT intentionally clears both."""
    token_snapshot = st.session_state.get("etrade_access_token")
    if lock_access:
        if bool(st.session_state.get("etrade_disconnect", False)):
            clear_session_cache(token_snapshot)
            clear_etrade_session(_trade_access_code_hash())
        else:
            # A simple terminal LOCK should feel instant when reopened: keep
            # both the still-valid OAuth session and its read-only cache.
            _persist_active_etrade_session()
    return _CORE_CLEAR_ETRADE_RUNTIME(lock_access=lock_access)


def _restore_active_etrade_session_after_unlock():
    """Restore OAuth only after the terminal access code has been accepted."""
    if not _trade_access_unlocked():
        return False
    if st.session_state.get("etrade_access_token"):
        return False

    restored = restore_etrade_session(
        _trade_access_code_hash(),
        inactivity_seconds=ETRADE_INACTIVITY_SECONDS,
        timezone=ETRADE_TIMEZONE,
    )
    if not restored:
        return False

    st.session_state["etrade_access_token"] = restored["token"]
    st.session_state["etrade_last_activity_at"] = restored["last_activity_at"]
    if restored.get("accounts"):
        st.session_state["etrade_accounts"] = restored["accounts"]
    st.session_state["_etrade_restored_after_unlock"] = True
    return True


def _render_cache_status():
    token = st.session_state.get("etrade_access_token")
    if not token:
        return
    stats = cache_stats(token)
    st.caption(
        "SMART CACHE // SHARED ACROSS ALL TABS + BROWSER REFRESH // "
        f"ACCOUNTS {CACHE_TTLS['accounts'] // 60}m // "
        f"HOLDINGS {CACHE_TTLS['portfolio']}s // "
        f"BALANCE {CACHE_TTLS['balance']}s // "
        f"QUOTES {CACHE_TTLS['quote']}s // "
        f"OPTION CHAINS {CACHE_TTLS['option_chain'] // 60}m // "
        f"EXPIRATIONS {CACHE_TTLS['option_expirations'] // 3600}h // "
        f"CACHE HITS {stats['hits']:,} // API FETCHES {stats['api_calls']:,}"
    )


# Holdings is snapshot/manual-refresh mode. The underlying E*TRADE call still
# passes through the shared cache; pressing REFRESH HOLDINGS + BALANCE is
# detected by CachedETradeClient and intentionally bypasses the cache once.
render_etrade_holdings = build_manual_holdings_renderer(_CORE_HOLDINGS_RENDERER)

# On a hard browser refresh, Streamlit session_state may be new. The user still
# enters the terminal code, but an E*TRADE OAuth session is recovered from
# server memory when its original inactivity/midnight timer is still active.
_restore_active_etrade_session_after_unlock()


if not _trade_access_unlocked():
    render_app_lock_screen()
    st.stop()

st.title("Raj's Terminal")
st.caption(
    "Bloomberg-style municipal analytics, E*TRADE holdings, Crown-style risk sizing, "
    "bull debit-spread optimization, and a live Triggers-OCO simulator."
)

if st.session_state.pop("_etrade_restored_after_unlock", False):
    st.success(
        "E*TRADE SESSION RESTORED // existing OAuth session is still active // "
        "no E*TRADE reconnect required"
    )

render_etrade_connection()

# Keep the latest active token/account snapshot available for a future browser
# refresh without changing the E*TRADE inactivity clock.
_persist_active_etrade_session()
_render_cache_status()

# These ARE the terminal tabs: click to open; drag left/right to reorder.
# Order and active tab are persisted in server memory and browser localStorage.
tab_order, active_tab = render_terminal_tab_bar(_trade_access_code_hash())

if active_tab == "HOLDINGS":
    render_etrade_holdings()

elif active_tab == "RISK SIZING":
    render_risk_sizing(
        _etrade_client(),
        account_picker=_account_picker,
        refresh_accounts=_refresh_accounts,
        account_balance=_account_balance,
        balance_snapshot=_balance_snapshot,
        touch_session=_touch_etrade_session,
    )

elif active_tab == "BULL DEBIT SPREAD":
    render_bull_debit_spread(
        _etrade_client(),
        _touch_etrade_session,
        timezone_name="America/New_York",
    )

elif active_tab == "MUNI SCREENERS":
    load_col, refresh_col, _ = st.columns([1.5, 1.4, 3.1])
    with load_col:
        load_muni_clicked = st.button(
            "LOAD MUNI SCREENERS",
            type="primary",
            key="load_muni_data",
            width="stretch",
        )
    with refresh_col:
        refresh_muni_clicked = st.button(
            "REFRESH MUNI DATA",
            key="refresh_muni_data",
            width="stretch",
        )

    if refresh_muni_clicked:
        st.session_state.pop(MUNI_SESSION_KEY, None)
        st.session_state.pop(MUNI_SESSION_AT_KEY, None)

    muni_bundle = None
    should_load_munis = (
        load_muni_clicked
        or refresh_muni_clicked
        or _session_muni_cache_is_valid()
    )

    if should_load_munis:
        try:
            muni_bundle, used_session_cache = load_muni_universe()
        except Exception as exc:
            st.error(f"Municipal data load failed: {exc}")
    else:
        st.info(
            "Municipal data is paused. Select LOAD MUNI SCREENERS when you are ready."
        )

    if muni_bundle is not None:
        df, source_rows, etf_status, as_of = muni_bundle

        if used_session_cache:
            age_minutes = int(
                (time.time() - st.session_state[MUNI_SESSION_AT_KEY]) / 60
            )
            st.caption(
                f"DATA ENGINE // SESSION CACHE READY • {len(df):,} CUSIPs "
                f"• loaded {age_minutes} min ago"
            )

        tab1, tab2, tab3 = st.tabs(
            [
                "MUNI SCREENER",
                "TAX EXEMPT STATUS FOR NIST",
                "STATE INCOME TAX // MUNI vs UST",
            ]
        )

        with tab1:
            render_muni_screener(
                df,
                source_rows,
                etf_status,
                as_of,
            )

        with tab2:
            render_nist_comparison(df)

        with tab3:
            render_state_income_tax_comparison(df)

        st.caption(
            "Important: ETF holdings do not cover every outstanding U.S. municipal bond. "
            "Source prices/yields are not guaranteed executable broker quotes. Verify call schedules, "
            "tax treatment, AMT treatment, ratings, Treasury quotes, and official terms before trading."
        )

elif active_tab == "ORDERS":
    orders_left, orders_center, orders_right = st.columns([1.4, 5.2, 1.4])
    with orders_center:
        render_order_simulator()
