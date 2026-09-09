"""Streamlit UI for the bull call debit spread optimizer."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.bull_debit_spread import extract_call_rows, scan_bull_call_spreads
from src.etrade_client import ETradeError, option_expiration_dates, quote_summary


CHAIN_CACHE_SECONDS = 5 * 60
EXPIRATION_CACHE_SECONDS = 6 * 60 * 60

BB_BLACK = "#000000"
BB_RED = "#ff433d"
BB_BLUE = "#0068ff"
BB_GREEN = "#4af6c3"
BB_ORANGE = "#fb8b1e"


def _money(value: float) -> str:
    return "$" + f"{float(value):,.2f}"


def _signed_metric(container, label: str, display_value: str, numeric_value: float, detail: str = "") -> None:
    color = BB_GREEN if numeric_value > 0 else BB_RED if numeric_value < 0 else BB_ORANGE
    detail_html = (
        '<div style="font-size:.78rem;margin-top:.12rem;color:' + color + ';font-weight:700;">'
        + str(detail)
        + "</div>"
        if detail
        else ""
    )
    container.markdown(
        '<div style="background:' + BB_BLACK + ';border:1px solid ' + BB_ORANGE
        + ';padding:.55rem .7rem;min-height:104px;font-family:Courier New,monospace;">'
        + '<div style="color:' + BB_ORANGE + ';font-size:.88rem;margin-bottom:.2rem;">'
        + str(label)
        + "</div>"
        + '<div style="color:' + color + ';font-size:1.65rem;font-weight:900;line-height:1.25;">'
        + str(display_value)
        + "</div>"
        + detail_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _expirations(client, symbol: str, touch_session):
    symbol = str(symbol).strip().upper()
    now = time.time()
    cache = st.session_state.setdefault("_bull_expiration_cache", {})
    cached = cache.get(symbol)
    if cached and now - float(cached.get("loaded_at", 0)) < EXPIRATION_CACHE_SECONDS:
        return list(cached["dates"]), True

    payload = client.get_option_expirations(symbol)
    dates = []
    for year, month, day in option_expiration_dates(payload):
        try:
            dates.append(datetime(year, month, day).date())
        except ValueError:
            continue
    dates = sorted(set(dates))
    cache[symbol] = {"loaded_at": now, "dates": dates}
    touch_session()
    return dates, False


def _call_chain(client, symbol: str, expiry, touch_session):
    symbol = str(symbol).strip().upper()
    cache = st.session_state.setdefault("_bull_chain_cache", {})
    cache_key = f"{symbol}:{expiry.isoformat()}"
    now = time.time()
    cached = cache.get(cache_key)
    if cached and now - float(cached.get("loaded_at", 0)) < CHAIN_CACHE_SECONDS:
        return list(cached["rows"]), True

    payload = client.get_option_chain(
        symbol,
        expiry.year,
        expiry.month,
        expiry.day,
        no_of_strikes=None,
        chain_type="CALL",
    )
    rows = extract_call_rows(payload, expiry)
    cache[cache_key] = {"loaded_at": now, "rows": rows}
    touch_session()
    return rows, False


def _render_results(result: dict) -> None:
    candidates = result.get("candidates", [])
    diagnostics = result.get("diagnostics", {})
    constraints = result.get("constraints", {})
    symbol = result.get("symbol", "")
    spot = float(result.get("spot", 0.0) or 0.0)

    st.markdown("### OPTIMIZER OUTPUT")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Underlying", symbol)
    q2.metric("Spot", _money(spot))
    q3.metric("Expirations Scanned", f"{diagnostics.get('expirations', 0):,}")
    q4.metric("Qualified Spreads", f"{diagnostics.get('qualified', 0):,}")

    if not candidates:
        st.error(
            "NO SPREAD MET EVERY FILTER // increase budget, raise maximum break-even, "
            "lower minimum R:R, reduce minimum OI, or change the expiration horizon."
        )
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Spreads Tested", f"{diagnostics.get('spreads_tested', 0):,}")
        d2.metric("BE Rejected", f"{diagnostics.get('break_even_rejected', 0):,}")
        d3.metric("R:R Rejected", f"{diagnostics.get('rr_rejected', 0):,}")
        d4.metric("Budget Rejected", f"{diagnostics.get('budget_rejected', 0):,}")
        return

    winner = candidates[0]
    st.success("WINNER // CHEAPEST QUALIFYING BULL CALL DEBIT SPREAD")

    w1, w2, w3, w4, w5, w6 = st.columns(6)
    w1.metric("Expiration", winner["Expiration"], f"{winner['DTE']} DTE")
    w2.metric("BUY CALL", _money(winner["Buy Call"]))
    w3.metric("SELL CALL", _money(winner["Sell Call"]))
    w4.metric("Break-even", _money(winner["Break-even"]))
    w5.metric("Reward : Risk", f"{winner['Reward : Risk']:.2f}:1")
    w6.metric("TOTAL COST", _money(winner["Total Cost"]), "1 spread / 100 shares")

    p1, p2, p3, p4, p5, p6 = st.columns(6)
    p1.metric("Natural Debit", _money(winner["Natural Debit"]))
    p2.metric("Mid Debit", _money(winner["Mid Debit"]))
    _signed_metric(p3, "Max Profit", "+" + _money(winner["Max Profit"]), winner["Max Profit"])
    _signed_metric(p4, "Max Loss", "-" + _money(winner["Max Loss"]), -winner["Max Loss"])
    p5.metric("Max Contracts", f"{winner['Max Contracts / Budget']:,}", "within budget")
    p6.metric("Budget Deployment", _money(winner["Budget Deployment"]))

    st.caption(
        "WINNER LOGIC // cheapest natural-debit spread that is at or below your "
        + _money(constraints.get("max_break_even", 0))
        + " break-even cap, meets at least "
        + f"{constraints.get('min_reward_risk', 0):.2f}:1 R:R, stays within "
        + _money(constraints.get("budget", 0))
        + ", and expires at/after the selected horizon."
    )

    st.markdown("**EXACT SPREAD TICKET // 1 CONTRACT**")
    ticket = pd.DataFrame(
        [
            {
                "Leg": "1",
                "Action": "BUY TO OPEN",
                "Qty": 1,
                "Type": "CALL",
                "Strike": winner["Buy Call"],
                "Expiration": winner["Expiration"],
                "Reference": "ASK",
                "Price": winner["Long Ask"],
            },
            {
                "Leg": "2",
                "Action": "SELL TO OPEN",
                "Qty": 1,
                "Type": "CALL",
                "Strike": winner["Sell Call"],
                "Expiration": winner["Expiration"],
                "Reference": "BID",
                "Price": winner["Short Bid"],
            },
        ]
    )
    st.dataframe(
        ticket,
        hide_index=True,
        width="stretch",
        column_config={
            "Strike": st.column_config.NumberColumn(format="$%.2f"),
            "Price": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("NET DEBIT", _money(winner["Natural Debit"]))
    c2.metric("CONTRACT MULTIPLIER", "100")
    c3.metric("TOTAL SPREAD COST", _money(winner["Total Cost"]))

    st.markdown("**TOP QUALIFYING CANDIDATES // CHEAPEST FIRST**")
    display = pd.DataFrame(candidates[:30]).reset_index(drop=True)

    cheapest_index = int(display["Total Cost"].idxmin())
    highest_rr_index = int(display["Reward : Risk"].idxmax())

    signals = []
    for row_index in display.index:
        tags = []
        if row_index == cheapest_index:
            tags.append("CHEAPEST")
        if row_index == highest_rr_index:
            tags.append("HIGHEST R:R")
        signals.append(" + ".join(tags))
    display.insert(0, "Signal", signals)

    display_columns = [
        "Signal",
        "Expiration",
        "DTE",
        "Buy Call",
        "Sell Call",
        "Width",
        "Natural Debit",
        "Mid Debit",
        "Break-even",
        "BE Headroom",
        "Reward : Risk",
        "Total Cost",
        "Max Profit",
        "Max Contracts / Budget",
        "Budget Deployment",
        "Long OI",
        "Short OI",
    ]
    display = display[[column for column in display_columns if column in display.columns]]

    def highlight_candidate(row):
        styles = [""] * len(row)
        if row.name == highest_rr_index:
            styles = [
                "background-color:#4af6c3;color:#000000;font-weight:900;"
            ] * len(row)
        elif row.name == cheapest_index:
            styles = [
                "background-color:#0068ff;color:#FFFFFF;font-weight:900;"
            ] * len(row)

        rr_position = row.index.get_loc("Reward : Risk") if "Reward : Risk" in row.index else None
        if rr_position is not None and row.name != highest_rr_index:
            styles[rr_position] = (
                "color:#4af6c3;font-weight:900;"
                + styles[rr_position]
            )
        return styles

    styled_display = display.style.apply(highlight_candidate, axis=1)

    st.caption(
        "TABLE HIGHLIGHTS // BLUE = CHEAPEST QUALIFYING SPREAD // "
        "GREEN = HIGHEST REWARD:RISK // R:R VALUES ARE GREEN"
    )
    st.dataframe(
        styled_display,
        hide_index=True,
        width="stretch",
        height=min(900, max(260, 35 * len(display) + 45)),
        column_config={
            "Buy Call": st.column_config.NumberColumn(format="$%.2f"),
            "Sell Call": st.column_config.NumberColumn(format="$%.2f"),
            "Width": st.column_config.NumberColumn(format="$%.2f"),
            "Natural Debit": st.column_config.NumberColumn(format="$%.2f"),
            "Mid Debit": st.column_config.NumberColumn(format="$%.2f"),
            "Break-even": st.column_config.NumberColumn(format="$%.2f"),
            "BE Headroom": st.column_config.NumberColumn(format="$%.2f"),
            "Reward : Risk": st.column_config.NumberColumn(format="%.2f"),
            "Total Cost": st.column_config.NumberColumn(format="$%.2f"),
            "Max Profit": st.column_config.NumberColumn(format="$%.2f"),
            "Budget Deployment": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    with st.expander("SCAN DIAGNOSTICS"):
        diag_frame = pd.DataFrame(
            [
                {"Metric": "Calls Seen", "Value": diagnostics.get("calls_seen", 0)},
                {"Metric": "Spreads Tested", "Value": diagnostics.get("spreads_tested", 0)},
                {"Metric": "Invalid / Non-debit Quotes", "Value": diagnostics.get("invalid_quotes", 0)},
                {"Metric": "Open Interest Rejected", "Value": diagnostics.get("oi_rejected", 0)},
                {"Metric": "Break-even Rejected", "Value": diagnostics.get("break_even_rejected", 0)},
                {"Metric": "R:R Rejected", "Value": diagnostics.get("rr_rejected", 0)},
                {"Metric": "Budget Rejected", "Value": diagnostics.get("budget_rejected", 0)},
                {"Metric": "Qualified", "Value": diagnostics.get("qualified", 0)},
                {"Metric": "Chain Cache Hits", "Value": diagnostics.get("cache_hits", 0)},
                {"Metric": "Chain Fetch Errors", "Value": diagnostics.get("fetch_errors", 0)},
            ]
        )
        st.dataframe(diag_frame, hide_index=True, width="stretch")


def render_bull_debit_spread(client, touch_session, timezone_name: str = "America/New_York") -> None:
    st.subheader("Bull Debit Spread")
    st.caption(
        "HIGH-TECH BULL CALL DEBIT SPREAD OPTIMIZER // E*TRADE OPTION CHAINS // "
        "READ-ONLY ANALYTICS // NATURAL PRICING = LONG ASK - SHORT BID"
    )

    if not client:
        st.info("Connect E*TRADE at the top of the terminal to scan live option chains.")
        return

    default_symbol = str(st.session_state.get("order_symbol", "NVDA") or "NVDA").upper()

    with st.form("bull_debit_spread_optimizer"):
        i1, i2, i3, i4, i5 = st.columns([1.1, 1.3, 1.15, 1.05, 1.2])
        with i1:
            symbol = st.text_input(
                "Ticker",
                value=default_symbol,
                key="bull_symbol",
            ).strip().upper()
        with i2:
            max_break_even = float(
                st.number_input(
                    "Maximum Break-even",
                    min_value=0.01,
                    value=280.00,
                    step=0.50,
                    format="%.2f",
                    help="Candidate break-even must be at or below this price.",
                    key="bull_max_be",
                )
            )
        with i3:
            year_options = ["ALL YEARS"] + [f"{year} YEAR+" if year == 1 else f"{year} YEARS+" for year in range(1, 11)]
            selected_years = st.selectbox(
                "Minimum Years Out",
                year_options,
                index=1,
                help="Choose ALL YEARS or require expirations at least this many full years out.",
                key="bull_years_out_selector",
            )
            years_out = 0.0 if selected_years == "ALL YEARS" else float(selected_years.split()[0])
        with i4:
            min_rr = float(
                st.number_input(
                    "Minimum R:R",
                    min_value=0.25,
                    max_value=20.0,
                    value=2.0,
                    step=0.25,
                    format="%.2f",
                    key="bull_min_rr",
                )
            )
        with i5:
            budget = float(
                st.number_input(
                    "Trade Budget",
                    min_value=1.0,
                    value=3000.0,
                    step=100.0,
                    format="%.2f",
                    help="Maximum total debit for one spread.",
                    key="bull_budget",
                )
            )

        a1, a2, a3 = st.columns([1.45, 1.15, 2.4])
        with a1:
            expiry_scope = st.selectbox(
                "Expirations to Scan",
                ["ALL ELIGIBLE", "3 NEAREST", "6 NEAREST", "12 NEAREST"],
                index=0,
                key="bull_expiry_scope",
                help="ALL ELIGIBLE scans every listed expiration at or beyond your horizon.",
            )
        with a2:
            min_oi = int(
                st.number_input(
                    "Minimum OI / Leg",
                    min_value=0,
                    value=0,
                    step=10,
                    key="bull_min_oi",
                )
            )
        with a3:
            st.markdown(
                "**ALGO PRIORITY //** FILTER BY HORIZON -> BREAK-EVEN -> R:R -> BUDGET -> "
                "**RANK CHEAPEST TOTAL COST FIRST**"
            )

        run_scan = st.form_submit_button(
            "RUN BULL DEBIT SPREAD OPTIMIZER",
            type="primary",
            width="stretch",
        )

    if run_scan:
        if not symbol:
            st.error("Enter a ticker.")
            return

        try:
            quote_data = quote_summary(client.get_quote(symbol))
            spot = float(quote_data["last"])
            touch_session()

            expirations, expiry_cache_hit = _expirations(client, symbol, touch_session)
            timezone = ZoneInfo(timezone_name)
            today = datetime.now(timezone).date()
            horizon_date = today + timedelta(days=int(round(years_out * 365.25)))
            eligible = [expiry for expiry in expirations if expiry >= horizon_date]

            if not eligible:
                st.error(
                    "No listed E*TRADE expirations were found on or after "
                    + horizon_date.isoformat()
                    + "."
                )
                return

            scope_limits = {
                "3 NEAREST": 3,
                "6 NEAREST": 6,
                "12 NEAREST": 12,
            }
            scan_limit = scope_limits.get(expiry_scope)
            selected_expirations = eligible if scan_limit is None else eligible[:scan_limit]

            st.markdown(
                "**SCANNING "
                + symbol
                + " // "
                + str(len(selected_expirations))
                + " EXPIRATIONS // HORIZON "
                + horizon_date.isoformat()
                + "+ // SPOT "
                + _money(spot)
                + "**"
            )
            progress = st.progress(0.0, text="Initializing option-chain scan...")
            chains = {}
            cache_hits = 0
            fetch_errors = []

            for index, expiry in enumerate(selected_expirations, start=1):
                try:
                    rows, cache_hit = _call_chain(client, symbol, expiry, touch_session)
                    if cache_hit:
                        cache_hits += 1
                    if rows:
                        chains[expiry] = rows
                except ETradeError as exc:
                    fetch_errors.append(expiry.isoformat() + ": " + str(exc))

                progress.progress(
                    index / len(selected_expirations),
                    text=(
                        "Scanning "
                        + expiry.isoformat()
                        + " // "
                        + str(index)
                        + "/"
                        + str(len(selected_expirations))
                    ),
                )

            progress.empty()

            candidates, diagnostics = scan_bull_call_spreads(
                chains,
                max_break_even=max_break_even,
                min_reward_risk=min_rr,
                budget=budget,
                min_open_interest=min_oi,
            )
            diagnostics["cache_hits"] = cache_hits + (1 if expiry_cache_hit else 0)
            diagnostics["fetch_errors"] = len(fetch_errors)

            st.session_state["bull_spread_scan_result"] = {
                "symbol": symbol,
                "spot": spot,
                "candidates": candidates,
                "diagnostics": diagnostics,
                "constraints": {
                    "max_break_even": max_break_even,
                    "years_out": years_out,
                    "horizon_date": horizon_date.isoformat(),
                    "min_reward_risk": min_rr,
                    "budget": budget,
                    "min_open_interest": min_oi,
                    "expiry_scope": expiry_scope,
                },
                "fetch_errors": fetch_errors,
                "scanned_at": datetime.now(timezone).isoformat(),
            }
        except ETradeError as exc:
            st.error(str(exc))

    result = st.session_state.get("bull_spread_scan_result")
    if result:
        _render_results(result)
        errors = result.get("fetch_errors", [])
        if errors:
            with st.expander("CHAIN FETCH WARNINGS"):
                for error in errors:
                    st.warning(error)

        st.caption(
            "Pricing is an estimate using E*TRADE option-chain quotes. Natural debit uses "
            "long ask minus short bid and is intentionally conservative. Actual fills, fees, "
            "assignment, liquidity, and quote timing can change realized cost and outcome."
        )
