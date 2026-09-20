"""Raj's Terminal entrypoint.

The large legacy UI/core definitions are loaded from src/terminal_core.py up to
the render boundary. This keeps the terminal framework modular so new tabs can
be added without duplicating the existing analytics code.
"""

from pathlib import Path

import streamlit as st


# Read-only TradingView bridge view. Check this before loading any terminal
# definitions so the bridge can never fall through to the keypad/home page.
_bridge_mode = str(st.query_params.get("gex_bridge", "") or "").strip().lower()
if _bridge_mode in {"1", "true", "latest"}:
    st.set_page_config(
        page_title="Raj GEX Bridge",
        page_icon="📈",
        layout="wide",
    )
    _bridge_path = Path(__file__).parent / "static" / "latest_gex.txt"
    try:
        _bridge_payload = _bridge_path.read_text(encoding="utf-8")
    except Exception:
        _bridge_payload = ""
    st.text_area(
        "RAJ GEX BRIDGE PAYLOAD",
        value=_bridge_payload,
        height=360,
        disabled=True,
        key="raj_gex_bridge_payload",
    )
    st.stop()


_CORE_PATH = Path(__file__).parent / "src" / "terminal_core.py"
_CORE_SOURCE = _CORE_PATH.read_text(encoding="utf-8")
_CORE_MARKER = "\n\nif not _trade_access_unlocked():\n    render_app_lock_screen()\n    st.stop()\n"

if _CORE_MARKER not in _CORE_SOURCE:
    raise RuntimeError("Raj's Terminal core render boundary was not found.")

_CORE_DEFINITIONS = _CORE_SOURCE.split(_CORE_MARKER, 1)[0]
exec(compile(_CORE_DEFINITIONS, str(_CORE_PATH), "exec"), globals())

from src.etrade_connection_ui_v2 import render_compact_etrade_connection
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
from src.gex_workspace_v2 import render_gex as render_gex_workspace
from src.holdings_snapshot_mode import build_manual_holdings_renderer
from src.risk_sizing_ui_v7 import render_risk_sizing
from src.schwab_risk_sizing_ui import render_schwab_risk_sizing
from src.session_persistence import (
    clear_etrade_session,
    restore_etrade_session,
    save_etrade_session,
)
from src.tab_bar_v4 import render_terminal_tab_bar
from src.theme import install_typing_caret_theme


# Shared appearance is emitted after terminal_core has completed set_page_config
# and on every Streamlit rerun/session. Do not gate this with a process-global
# flag: later browser sessions must receive the same dropdown/caret stylesheet.
install_typing_caret_theme()


# Preserve references to the core implementations before installing the
# seamless-session/cache adapters below.
_CORE_ETRADE_CLIENT_FACTORY = _etrade_client
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
    """Render the production E*TRADE strip and compact one-row OAuth panel."""
    return render_compact_etrade_connection(
        credentials_factory=_etrade_credentials,
        live_client_factory=_live_etrade_client,
        session_timer=_render_etrade_session_timer,
        begin_authorization=begin_authorization,
        complete_authorization=complete_authorization,
        etrade_error_type=ETradeError,
        touch_session=_touch_etrade_session,
        clear_runtime=_clear_etrade_runtime,
        refresh_accounts=_refresh_accounts,
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
    if st.session_state.get("etrade_access_token"):
        _CORE_TOUCH_ETRADE_SESSION()
        _persist_active_etrade_session()


def _clear_etrade_runtime(lock_access=False):
    """LOCK preserves session/cache; DISCONNECT clears live OAuth, not snapshots."""
    token_snapshot = st.session_state.get("etrade_access_token")
    if lock_access:
        if bool(st.session_state.get("etrade_disconnect", False)):
            clear_session_cache(token_snapshot)
            clear_etrade_session(_trade_access_code_hash())
        else:
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

    holdings_loaded_at = float(st.session_state.get("etrade_holdings_last_refresh", now) or now)
    for account_key, value in (st.session_state.get("etrade_holdings") or {}).items():
        if value is not None and _offline_get(vault_key, "portfolio", str(account_key)) is None:
            _offline_store(vault_key, "portfolio", str(account_key), value, loaded_at=holdings_loaded_at)

    balance_loaded_at = float(st.session_state.get("etrade_balance_last_refresh", now) or now)
    for account_key, value in (st.session_state.get("etrade_balances") or {}).items():
        if value is not None and _offline_get(vault_key, "balance", str(account_key)) is None:
            _offline_store(vault_key, "balance", str(account_key), value, loaded_at=balance_loaded_at)


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
    accounts = st.session_state.get("etrade_accounts") or ((accounts_entry or {}).get("value") or [])
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
    """Keep cache diagnostics available internally without wasting terminal space."""
    return None


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
                '<div class="keypad-display">'
                f'<div class="keypad-dots">{html.escape(dots)}</div>'
                f'<div class="keypad-meta">{len(buffer)} DIGITS ENTERED</div>'
                '</div>',
                unsafe_allow_html=True,
            )

            pressed = None
            for digits in (("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9")):
                cols = st.columns(3, gap="small")
                for col, digit in zip(cols, digits):
                    with col:
                        if st.button(digit, width="stretch", disabled=remaining > 0, key=f"app_keypad_digit_{digit}"):
                            pressed = digit

            bottom = st.columns(3, gap="small")
            with bottom[0]:
                clear_pressed = st.button("CLR", width="stretch", disabled=remaining > 0 or not buffer, key="app_keypad_clear")
            with bottom[1]:
                zero_pressed = st.button("0", width="stretch", disabled=remaining > 0, key="app_keypad_digit_0")
            with bottom[2]:
                back_pressed = st.button("⌫", width="stretch", disabled=remaining > 0 or not buffer, key="app_keypad_backspace")

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

            unlock = st.button("UNLOCK TERMINAL", type="primary", width="stretch", disabled=(remaining > 0 or not buffer), key="app_keypad_unlock")

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
                    st.session_state["_app_keypad_feedback"] = ("error", f"TOO MANY FAILED ATTEMPTS // LOCKED FOR {remaining_after} SECONDS")
                else:
                    attempts = int(st.session_state.get("app_unlock_failures", 0) or 0)
                    left_attempts = max(0, APP_LOCK_MAX_ATTEMPTS - attempts)
                    st.session_state["_app_keypad_feedback"] = ("error", f"INCORRECT ACCESS CODE // {left_attempts} ATTEMPTS REMAIN")
                st.rerun()


# ==============================
# TOP-LEVEL TAB PAGE HEADERS
# ==============================
# One renderer owns the page title/subtitle chrome for every top-level terminal
# tab. Feature renderers keep their own internal sections, but their historical
# first header is suppressed only for the duration of that feature render.
# This prevents Holdings/Risk/Bull/Muni/Orders/GEX from drifting visually.

_TERMINAL_PAGE_HEADER_CSS = """
<style>
.terminal-page-header-shell {
    width:100%;
    margin:.08rem 0 .38rem 0;
    font-family:"Courier New",monospace;
}
.terminal-page-header-title {
    width:100%;
    box-sizing:border-box;
    background:#fb8b1e;
    color:#000000 !important;
    padding:.34rem .58rem;
    font-size:1.08rem;
    line-height:1.08;
    font-weight:900;
    letter-spacing:.04em;
    text-transform:uppercase;
}
.terminal-page-header-subtitle {
    color:#b87621 !important;
    padding:.20rem .04rem 0 .04rem;
    font-size:.69rem;
    line-height:1.18;
    font-weight:800;
    letter-spacing:.015em;
    text-transform:none;
}
</style>
"""

_TERMINAL_PAGE_HEADERS = {
    "HOLDINGS": (
        "E*TRADE HOLDINGS",
        "BROKERAGE POSITIONS // MANUAL SNAPSHOT // E*TRADE ACCOUNT",
    ),
    "RISK SIZING": (
        "E*TRADE RISK SIZING",
        "CROWN MACRO RISK ENGINE // E*TRADE HOLDINGS + QUOTES // PORTFOLIO SLEEVE CONTROL // STOP-BASED POSITION SIZING // RAJ CLASSIFICATION RULE",
    ),
    "SCHWAB RISK SIZING": (
        "SCHWAB RISK SIZING",
        "CROWN MACRO RISK ENGINE // SCHWAB API-READY ROUTE // SEPARATE HOLDINGS + QUOTES // SAME RISK FORMULAS",
    ),
    "BULL DEBIT SPREAD": (
        "BULL DEBIT SPREAD",
        "HIGH-TECH BULL CALL DEBIT SPREAD OPTIMIZER // E*TRADE OPTION CHAINS // READ-ONLY ANALYTICS // NATURAL PRICING = LONG ASK - SHORT BID",
    ),
    "MUNI SCREENERS": (
        "MUNI SCREENERS",
        "MUNICIPAL BOND SCREENING // TAX-EXEMPT STATUS // STATE TAX COMPARISON",
    ),
    "ORDERS": (
        "TRIGGERS — OCO ORDER SIMULATOR",
        "BUY LIMIT → WHEN FILLED, ACTIVATES A TAKE-PROFIT LIMIT AND STOP-MARKET EXIT // SIMULATION ONLY // NO ORDER CAN BE TRANSMITTED",
    ),
    "GEX": (
        "GEX // MULTI-TICKER GAMMA WORKSPACE",
        "BARCHART_STYLE // CALLS +GEX // PUTS −GEX // GAMMA × OI × 100 × SPOT² × 1% // INDIVIDUAL DTE // PACKED A6 // NOTES HISTORY",
    ),
}

_LEGACY_PAGE_TITLES = {
    "HOLDINGS": {"E*TRADE HOLDINGS"},
    "RISK SIZING": {"RISK SIZING"},
    "SCHWAB RISK SIZING": {"RISK SIZING"},
    "BULL DEBIT SPREAD": {"BULL DEBIT SPREAD"},
    "ORDERS": {"TRIGGERS – OCO ORDER SIMULATOR", "TRIGGERS — OCO ORDER SIMULATOR"},
}

_LEGACY_PAGE_CAPTION_PREFIXES = {
    "RISK SIZING": ("CROWN MACRO RISK ENGINE //",),
    "SCHWAB RISK SIZING": ("CROWN MACRO RISK ENGINE //",),
    "BULL DEBIT SPREAD": ("HIGH-TECH BULL CALL DEBIT SPREAD OPTIMIZER //",),
    "ORDERS": ("BUY LIMIT → WHEN FILLED, ACTIVATES A TAKE-PROFIT LIMIT AND STOP-MARKET EXIT.",),
}

_LEGACY_PAGE_MARKDOWN_FRAGMENTS = {
    "GEX": ("class=\"gexv3-head\"", "class=\"gexv3-sub\""),
}


def _render_terminal_page_header(active_tab: str) -> None:
    """Render exactly one compact, consistent header for the active top-level tab."""
    spec = _TERMINAL_PAGE_HEADERS.get(str(active_tab))
    if not spec:
        return
    if not getattr(st, "_raj_page_header_css_installed", False):
        st.html(_TERMINAL_PAGE_HEADER_CSS)
        st._raj_page_header_css_installed = True

    title, subtitle = spec
    markup = (
        '<div class="terminal-page-header-shell">'
        '<div class="terminal-page-header-title">' + html.escape(title) + "</div>"
        '<div class="terminal-page-header-subtitle">' + html.escape(subtitle) + "</div>"
        "</div>"
    )
    st.markdown(markup, unsafe_allow_html=True)


def _render_without_legacy_page_header(active_tab: str, renderer):
    """Suppress only the feature's old first title/subtitle while it renders.

    The temporary wrappers are installed inside this function and always
    restored in ``finally``. Internal feature subheaders/captions remain intact.
    """
    original_subheader = st.subheader
    original_caption = st.caption
    original_markdown = st.markdown

    titles = {str(value).strip().upper() for value in _LEGACY_PAGE_TITLES.get(active_tab, set())}
    caption_prefixes = tuple(
        str(value).strip().upper()
        for value in _LEGACY_PAGE_CAPTION_PREFIXES.get(active_tab, ())
    )
    markdown_fragments = tuple(_LEGACY_PAGE_MARKDOWN_FRAGMENTS.get(active_tab, ()))

    def filtered_subheader(body, *args, **kwargs):
        if str(body).strip().upper() in titles:
            return None
        return original_subheader(body, *args, **kwargs)

    def filtered_caption(body, *args, **kwargs):
        upper = str(body).strip().upper()
        if any(upper.startswith(prefix) for prefix in caption_prefixes):
            return None
        return original_caption(body, *args, **kwargs)

    def filtered_markdown(body, *args, **kwargs):
        text = str(body)
        if any(fragment in text for fragment in markdown_fragments):
            return None
        return original_markdown(body, *args, **kwargs)

    st.subheader = filtered_subheader
    st.caption = filtered_caption
    st.markdown = filtered_markdown
    try:
        return renderer()
    finally:
        st.subheader = original_subheader
        st.caption = original_caption
        st.markdown = original_markdown


render_etrade_holdings = build_manual_holdings_renderer(_CORE_HOLDINGS_RENDERER)
_restore_active_etrade_session_after_unlock()

if not _trade_access_unlocked():
    render_app_lock_screen()
    st.stop()

_seed_offline_from_session_state()

st.title("Raj's Terminal")

if st.session_state.pop("_etrade_restored_after_unlock", False):
    st.success("E*TRADE SESSION RESTORED // existing OAuth session is still active // no E*TRADE reconnect required")

render_etrade_connection()
_persist_active_etrade_session()
_render_cache_status()
_render_offline_snapshot_notice()

tab_order, active_tab = render_terminal_tab_bar(_trade_access_code_hash())
_render_terminal_page_header(active_tab)

if active_tab == "HOLDINGS":
    _render_without_legacy_page_header("HOLDINGS", render_etrade_holdings)

elif active_tab == "RISK SIZING":
    _render_without_legacy_page_header(
        "RISK SIZING",
        lambda: render_risk_sizing(
            _etrade_client(),
            account_picker=_account_picker,
            refresh_accounts=_refresh_accounts,
            account_balance=_account_balance,
            balance_snapshot=_balance_snapshot,
            touch_session=_touch_etrade_session,
        ),
    )

elif active_tab == "SCHWAB RISK SIZING":
    _render_without_legacy_page_header(
        "SCHWAB RISK SIZING",
        render_schwab_risk_sizing,
    )

elif active_tab == "GEX":
    _render_without_legacy_page_header("GEX", render_gex_workspace)

elif active_tab == "BULL DEBIT SPREAD":
    _render_without_legacy_page_header(
        "BULL DEBIT SPREAD",
        lambda: render_bull_debit_spread(
            _etrade_client(),
            _touch_etrade_session,
            timezone_name="America/New_York",
        ),
    )

elif active_tab == "MUNI SCREENERS":
    load_col, refresh_col, _ = st.columns([1.5, 1.4, 3.1])
    with load_col:
        load_muni_clicked = st.button("LOAD MUNI SCREENERS", type="primary", key="load_muni_data", width="stretch")
    with refresh_col:
        refresh_muni_clicked = st.button("REFRESH MUNI DATA", key="refresh_muni_data", width="stretch")

    if refresh_muni_clicked:
        st.session_state.pop(MUNI_SESSION_KEY, None)
        st.session_state.pop(MUNI_SESSION_AT_KEY, None)

    muni_bundle = None
    should_load_munis = load_muni_clicked or refresh_muni_clicked or _session_muni_cache_is_valid()

    if should_load_munis:
        try:
            muni_bundle, used_session_cache = load_muni_universe()
        except Exception as exc:
            st.error(f"Municipal data load failed: {exc}")
    else:
        st.info("Municipal data is paused. Select LOAD MUNI SCREENERS when you are ready.")

    if muni_bundle is not None:
        df, source_rows, etf_status, as_of = muni_bundle
        if used_session_cache:
            age_minutes = int((time.time() - st.session_state[MUNI_SESSION_AT_KEY]) / 60)
            st.caption(f"DATA ENGINE // SESSION CACHE READY • {len(df):,} CUSIPs • loaded {age_minutes} min ago")

        tab1, tab2, tab3 = st.tabs([
            "MUNI SCREENER",
            "TAX EXEMPT STATUS FOR NIST",
            "STATE INCOME TAX // MUNI vs UST",
        ])
        with tab1:
            render_muni_screener(df, source_rows, etf_status, as_of)
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
        _render_without_legacy_page_header("ORDERS", render_order_simulator)
