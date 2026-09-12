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
    OfflineETradeClient,
    _offline_get,
    _offline_store,
    cache_stats,
    clear_session_cache,
    offline_snapshot_available,
    offline_snapshot_status,
)
from src.holdings_snapshot_mode import build_manual_holdings_renderer
from src.risk_sizing_ui_v5 import render_risk_sizing
from src.session_persistence import (
    clear_etrade_session,
    restore_etrade_session,
    save_etrade_session,
)
from src.tab_bar import render_terminal_tab_bar


# Preserve references to the core implementations before installing the
# seamless-session/cache adapters below.
_CORE_ETRADE_CLIENT_FACTORY = _etrade_client
_CORE_RENDER_ETRADE_CONNECTION = render_etrade_connection
_CORE_TOUCH_ETRADE_SESSION = _touch_etrade_session
_CORE_CLEAR_ETRADE_RUNTIME = _clear_etrade_runtime
_CORE_HOLDINGS_RENDERER = render_etrade_holdings


def _live_etrade_client():
    """Return the authenticated live client wrapped in the shared smart cache."""
    raw_client = _CORE_ETRADE_CLIENT_FACTORY()
    if raw_client is None:
        return None
    return CachedETradeClient(
        raw_client,
        st.session_state.get("etrade_access_token"),
        offline_key=_trade_access_code_hash(),
    )


def _offline_etrade_client():
    """Return a read-only last-known-good client when a snapshot exists."""
    vault_key = _trade_access_code_hash()
    if not offline_snapshot_available(vault_key):
        return None
    return OfflineETradeClient(vault_key)


def _etrade_client():
    """Use live E*TRADE when possible; otherwise transparently use prior snapshots."""
    return _live_etrade_client() or _offline_etrade_client()


def render_etrade_connection():
    """Render connection controls against LIVE OAuth only, never the offline client.

    The rest of Raj's Terminal can use _etrade_client() and therefore fall back
    to cached snapshots. The connection bar must still truthfully show whether
    E*TRADE itself is connected, so the core renderer temporarily sees the
    live-only factory.
    """
    effective_factory = globals()["_etrade_client"]
    globals()["_etrade_client"] = _live_etrade_client
    try:
        return _CORE_RENDER_ETRADE_CONNECTION()
    finally:
        globals()["_etrade_client"] = effective_factory


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
    # Offline reads do not have an OAuth token to extend/persist.
    if st.session_state.get("etrade_access_token"):
        _CORE_TOUCH_ETRADE_SESSION()
        _persist_active_etrade_session()


def _clear_etrade_runtime(lock_access=False):
    """LOCK preserves session/cache; DISCONNECT clears live OAuth, not snapshots."""
    token_snapshot = st.session_state.get("etrade_access_token")
    if lock_access:
        if bool(st.session_state.get("etrade_disconnect", False)):
            # Clear the live token-scoped cache/session. The last-known-good
            # access-code-scoped snapshot is intentionally kept so the terminal
            # remains useful if E*TRADE cannot reconnect later.
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


def _seed_offline_from_session_state():
    """Preserve any old session snapshot without overwriting its true timestamp."""
    vault_key = _trade_access_code_hash()
    now = time.time()
    accounts = st.session_state.get("etrade_accounts") or []
    if accounts and _offline_get(vault_key, "accounts", "all") is None:
        _offline_store(vault_key, "accounts", "all", accounts, loaded_at=now)

    holdings_loaded_at = float(
        st.session_state.get("etrade_holdings_last_refresh", now) or now
    )
    for account_key, value in (st.session_state.get("etrade_holdings") or {}).items():
        if value is not None and _offline_get(vault_key, "portfolio", str(account_key)) is None:
            _offline_store(
                vault_key,
                "portfolio",
                str(account_key),
                value,
                loaded_at=holdings_loaded_at,
            )

    balance_loaded_at = float(
        st.session_state.get("etrade_balance_last_refresh", now) or now
    )
    for account_key, value in (st.session_state.get("etrade_balances") or {}).items():
        if value is not None and _offline_get(vault_key, "balance", str(account_key)) is None:
            _offline_store(
                vault_key,
                "balance",
                str(account_key),
                value,
                loaded_at=balance_loaded_at,
            )


def _cache_age_text(seconds):
    if seconds is None:
        return "UNKNOWN"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def _portfolio_snapshot_age():
    """Age of the freshest actual holdings snapshot, not metadata like accounts."""
    vault_key = _trade_access_code_hash()
    accounts_entry = _offline_get(vault_key, "accounts", "all")
    accounts = st.session_state.get("etrade_accounts") or (
        (accounts_entry or {}).get("value") or []
    )
    ages = []
    now = time.time()
    for account in accounts:
        account_key = str(account.get("accountIdKey", ""))
        if not account_key:
            continue
        entry = _offline_get(vault_key, "portfolio", account_key)
        if entry:
            ages.append(max(0.0, now - float(entry.get("loaded_at", 0.0) or 0.0)))
    if ages:
        return min(ages)
    status = offline_snapshot_status(vault_key)
    return status.get("newest_age")


def _render_cache_status():
    token = st.session_state.get("etrade_access_token")
    offline = offline_snapshot_status(_trade_access_code_hash())

    if token:
        stats = cache_stats(token)
        st.caption(
            "SMART CACHE // SHARED ACROSS ALL TABS + BROWSER REFRESH // "
            f"ACCOUNTS {CACHE_TTLS['accounts'] // 60}m // "
            f"HOLDINGS {CACHE_TTLS['portfolio']}s // "
            f"BALANCE {CACHE_TTLS['balance']}s // "
            f"QUOTES {CACHE_TTLS['quote']}s // "
            f"OPTION CHAINS {CACHE_TTLS['option_chain'] // 60}m // "
            f"EXPIRATIONS {CACHE_TTLS['option_expirations'] // 3600}h // "
            f"CACHE HITS {stats['hits']:,} // API FETCHES {stats['api_calls']:,} // "
            f"STALE FALLBACKS {stats.get('stale_fallbacks', 0):,}"
        )
        return

    if offline.get("available"):
        resources = offline.get("resources") or {}
        st.caption(
            "LAST-KNOWN E*TRADE VAULT // SERVER-MEMORY ONLY // "
            f"PORTFOLIO SNAPSHOT {_cache_age_text(_portfolio_snapshot_age())} AGO // "
            f"HOLDINGS {resources.get('portfolio', 0)} // "
            f"BALANCES {resources.get('balance', 0)} // "
            f"QUOTES {resources.get('quote', 0)} // "
            f"OPTION CHAINS {resources.get('option_chain', 0)}"
        )


def _render_offline_snapshot_notice():
    live_client = _live_etrade_client()
    status = offline_snapshot_status(_trade_access_code_hash())
    using_offline = live_client is None and bool(status.get("available"))
    fallback_event = st.session_state.get("_etrade_offline_mode", False)
    if not using_offline and not fallback_event:
        return

    age = _portfolio_snapshot_age()
    st.warning(
        "E*TRADE OFFLINE SNAPSHOT MODE // live E*TRADE is unavailable, so Raj's Terminal is using "
        f"your last-known cached brokerage data (portfolio snapshot {_cache_age_text(age)} ago). "
        "Cached holdings, balances, prior quotes, option expirations, and option chains remain readable. "
        "Values may be stale until E*TRADE reconnects."
    )


def render_app_lock_screen():
    """Bloomberg-style lock screen with a click-only numeric keypad."""
    remaining = _app_lockout_remaining()
    buffer_key = "app_keypad_buffer"
    buffer = str(st.session_state.get(buffer_key, ""))

    st.markdown(
        """
        <style>
        .keypad-display {
            border:1px solid #fb8b1e;
            background:#030303;
            color:#fb8b1e !important;
            font-family:'Courier New',monospace;
            font-weight:900;
            text-align:center;
            padding:.72rem .55rem;
            margin:.1rem 0 .65rem 0;
            min-height:58px;
            display:flex;
            flex-direction:column;
            justify-content:center;
        }
        .keypad-dots {
            color:#4af6c3 !important;
            font-size:1.5rem;
            line-height:1.05;
            letter-spacing:.18em;
            min-height:1.5rem;
        }
        .keypad-meta {
            color:#fb8b1e !important;
            font-size:.68rem;
            margin-top:.28rem;
            letter-spacing:.05em;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([2.1, 1.0], gap="large")

    with left:
        st.markdown(
            """
            <div class="app-lock-panel" style="width:100%;">
              <div class="app-lock-header">RAJ'S TERMINAL // SECURE ACCESS</div>
              <div class="app-lock-body">
                <div class="app-lock-icon">▣</div>
                <div class="app-lock-title">FRAMEWORK LOCKED</div>
                <div class="app-lock-copy">
                  Authentication is required before the terminal, E*TRADE connection,
                  holdings, orders, municipal screeners, or account data are rendered.
                </div>
                <div class="app-lock-status">
                  SESSION SECURITY // API CREDENTIALS REMAIN SERVER-SIDE // TERMINAL ACCESS DISABLED
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="terminal-note">INPUT METHOD // ON-SCREEN NUMERIC KEYPAD // NO KEYBOARD REQUIRED</div>',
            unsafe_allow_html=True,
        )

    with right:
        with st.container(border=True):
            st.subheader("Access Keypad")
            dots = "●" * len(buffer) if buffer else "—"
            st.markdown(
                (
                    '<div class="keypad-display">'
                    f'<div class="keypad-dots">{html.escape(dots)}</div>'
                    f'<div class="keypad-meta">{len(buffer)} DIGITS ENTERED</div>'
                    '</div>'
                ),
                unsafe_allow_html=True,
            )

            pressed = None
            for row_index, digits in enumerate((("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9"))):
                cols = st.columns(3, gap="small")
                for col, digit in zip(cols, digits):
                    with col:
                        if st.button(
                            digit,
                            width="stretch",
                            disabled=remaining > 0,
                            key=f"app_keypad_digit_{digit}",
                        ):
                            pressed = digit

            bottom = st.columns(3, gap="small")
            with bottom[0]:
                clear_pressed = st.button(
                    "CLR",
                    width="stretch",
                    disabled=remaining > 0 or not buffer,
                    key="app_keypad_clear",
                )
            with bottom[1]:
                zero_pressed = st.button(
                    "0",
                    width="stretch",
                    disabled=remaining > 0,
                    key="app_keypad_digit_0",
                )
            with bottom[2]:
                back_pressed = st.button(
                    "⌫",
                    width="stretch",
                    disabled=remaining > 0 or not buffer,
                    key="app_keypad_backspace",
                )

            if pressed is not None:
                if len(buffer) < 32:
                    st.session_state[buffer_key] = buffer + pressed
                st.rerun()
            if zero_pressed:
                if len(buffer) < 32:
                    st.session_state[buffer_key] = buffer + "0"
                st.rerun()
            if clear_pressed:
                st.session_state[buffer_key] = ""
                st.rerun()
            if back_pressed:
                st.session_state[buffer_key] = buffer[:-1]
                st.rerun()

            unlock = st.button(
                "UNLOCK TERMINAL",
                type="primary",
                width="stretch",
                disabled=(remaining > 0 or not buffer),
                key="app_keypad_unlock",
            )

            feedback = st.session_state.pop("_app_keypad_feedback", None)
            if remaining > 0:
                st.error(f"SECURITY LOCKOUT // TRY AGAIN IN {remaining} SECONDS")
            elif feedback:
                tone, message = feedback
                getattr(st, tone)(message)

            if unlock:
                if _verify_trade_access_code(buffer):
                    st.session_state["etrade_access_unlocked"] = True
                    st.session_state.pop(buffer_key, None)
                    st.session_state.pop("app_access_code", None)
                    st.session_state.pop("_app_keypad_feedback", None)
                    _clear_unlock_failures()
                    st.rerun()

                _record_failed_unlock()
                st.session_state[buffer_key] = ""
                remaining_after = _app_lockout_remaining()
                if remaining_after > 0:
                    st.session_state["_app_keypad_feedback"] = (
                        "error",
                        f"TOO MANY FAILED ATTEMPTS // LOCKED FOR {remaining_after} SECONDS",
                    )
                else:
                    attempts = int(st.session_state.get("app_unlock_failures", 0) or 0)
                    left_attempts = max(0, APP_LOCK_MAX_ATTEMPTS - attempts)
                    st.session_state["_app_keypad_feedback"] = (
                        "error",
                        f"INCORRECT ACCESS CODE // {left_attempts} ATTEMPTS REMAIN",
                    )
                st.rerun()


# Holdings is snapshot/manual-refresh mode. The underlying E*TRADE call still
# passes through the shared cache; pressing REFRESH HOLDINGS + BALANCE is
# detected by CachedETradeClient and intentionally bypasses the normal TTL.
# If that live refresh fails, the cache layer returns the prior snapshot.
render_etrade_holdings = build_manual_holdings_renderer(_CORE_HOLDINGS_RENDERER)

# On a hard browser refresh, Streamlit session_state may be new. The user still
# enters the terminal code, but an E*TRADE OAuth session is recovered from
# server memory when its original inactivity/midnight timer is still active.
_restore_active_etrade_session_after_unlock()


if not _trade_access_unlocked():
    render_app_lock_screen()
    st.stop()

# Capture any already-loaded portfolio data before connection controls or tab
# renderers have a chance to clear/replace session values.
_seed_offline_from_session_state()

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
_render_offline_snapshot_notice()

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
