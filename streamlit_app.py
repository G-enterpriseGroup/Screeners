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

from src.risk_sizing_ui import render_risk_sizing


if not _trade_access_unlocked():
    render_app_lock_screen()
    st.stop()

st.title("Raj's Terminal")
st.caption(
    "Bloomberg-style municipal analytics, E*TRADE holdings, Crown-style risk sizing, "
    "bull debit-spread optimization, and a live Triggers-OCO simulator."
)

render_etrade_connection()

(
    orders_tab,
    holdings_tab,
    risk_sizing_tab,
    bull_spread_tab,
    muni_screeners_tab,
) = st.tabs(
    [
        "ORDERS",
        "HOLDINGS",
        "RISK SIZING",
        "BULL DEBIT SPREAD",
        "MUNI SCREENERS",
    ]
)

with orders_tab:
    orders_left, orders_center, orders_right = st.columns([1.4, 5.2, 1.4])
    with orders_center:
        render_order_simulator()

with holdings_tab:
    render_etrade_holdings()

with risk_sizing_tab:
    render_risk_sizing(
        _etrade_client(),
        account_picker=_account_picker,
        refresh_accounts=_refresh_accounts,
        account_balance=_account_balance,
        balance_snapshot=_balance_snapshot,
        touch_session=_touch_etrade_session,
    )

with bull_spread_tab:
    render_bull_debit_spread(
        _etrade_client(),
        _touch_etrade_session,
        timezone_name="America/New_York",
    )

with muni_screeners_tab:
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
