"""Crown Macro inspired risk-sizing dashboard for Raj's Terminal."""

from __future__ import annotations

import math
from typing import Any, Callable

import pandas as pd
import streamlit as st

from src.etrade_client import ETradeError, normalize_position, quote_summary
from src.risk_sizing import (
    CROWN_DEFAULT_RISK_PCT,
    CROWN_DEFAULT_TACTICAL_SLEEVE_PCT,
    CROWN_DOLLAR_RISK_TABLE,
    CROWN_SIZE_MULTIPLIERS,
    classify_holdings,
    crown_risk_budget,
    defined_risk_contracts,
    sleeve_summary,
    stock_position_size,
)
from src.theme import BB_BLACK, BB_BLUE, BB_GREEN, BB_ORANGE, BB_RED


def _find_text_key(value: Any, wanted: str) -> str:
    wanted = wanted.casefold()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() == wanted and child not in (None, ""):
                return str(child).strip()
        for child in value.values():
            found = _find_text_key(child, wanted)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_text_key(child, wanted)
            if found:
                return found
    return ""


def _normalized_holdings(raw_holdings: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for position in raw_holdings:
        if not isinstance(position, dict):
            continue
        row = normalize_position(position)
        cusip = _find_text_key(position, "cusip")
        if not cusip and str(row.get("Type") or "").upper() == "BOND":
            # Some E*TRADE bond payloads expose the CUSIP as the product symbol.
            possible = str(row.get("Symbol") or "").strip().upper()
            if len(possible) == 9 and possible.isalnum():
                cusip = possible
        # CUSIP remains internal for classification only. It is intentionally
        # not repeated in the dashboard when the bond symbol already is its CUSIP.
        row["CUSIP"] = cusip
        rows.append(row)
    return pd.DataFrame(rows)


def _metric_box(container, label: str, value: str, tone: str = "neutral", detail: str = "") -> None:
    color = {
        "positive": BB_GREEN,
        "negative": BB_RED,
        "blue": BB_BLUE,
        "neutral": BB_ORANGE,
    }.get(tone, BB_ORANGE)
    detail_html = (
        '<div style="font-size:.75rem;margin-top:.15rem;color:' + color + ';">'
        + str(detail)
        + "</div>"
        if detail
        else ""
    )
    container.markdown(
        '<div style="background:' + BB_BLACK + ';border:1px solid ' + BB_ORANGE
        + ';padding:.55rem .7rem;min-height:100px;font-family:Courier New,monospace;">'
        + '<div style="color:' + BB_ORANGE + ';font-size:.78rem;font-weight:900;">'
        + str(label)
        + "</div>"
        + '<div style="color:' + color + ';font-size:1.45rem;font-weight:900;margin-top:.18rem;">'
        + str(value)
        + "</div>"
        + detail_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _money(value: float) -> str:
    return "$" + f"{float(value):,.2f}"


def _percent(value: float) -> str:
    return f"{float(value):,.2f}%"


def _render_crown_reference() -> None:
    st.subheader("Crown Macro Notes & Examples")
    choice = st.selectbox(
        "Notes / Example",
        [
            "Sizing Multipliers",
            "TSM Stop-Based Example",
            "Defined-Risk Options Example",
            "Dollar Risk by Account Size",
            "Conviction vs Sizing",
            "Operating Principles",
        ],
        key="risk_notes_example",
    )

    if choice == "Sizing Multipliers":
        st.markdown(
            "**1.00 = full standard risk // 0.75 = three-quarters // "
            "0.50 = half // 0.25 = quarter.**"
        )
        st.caption(
            "The multiplier is relative to your standard position risk; it is not a direct "
            "percentage of the total portfolio."
        )

    elif choice == "TSM Stop-Based Example":
        st.markdown("**GUIDE EXAMPLE // TSM**")
        example = pd.DataFrame(
            [
                {"Entry": 411.0, "Stop": 360.0, "Risk / Share": 51.0, "Risk Budget": 1500.0, "Sizing": "1.00", "Approx Shares": 29},
                {"Entry": 411.0, "Stop": 360.0, "Risk / Share": 51.0, "Risk Budget": 750.0, "Sizing": "0.50", "Approx Shares": 15},
            ]
        )
        st.dataframe(
            example,
            hide_index=True,
            width="stretch",
            column_config={
                "Entry": st.column_config.NumberColumn(format="$%.2f"),
                "Stop": st.column_config.NumberColumn(format="$%.2f"),
                "Risk / Share": st.column_config.NumberColumn(format="$%.2f"),
                "Risk Budget": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
        st.caption("The guide works backward from the stop: dollar risk budget divided by risk per share.")

    elif choice == "Defined-Risk Options Example":
        st.markdown("**GUIDE EXAMPLE // DEFINED-RISK SPREAD**")
        example = pd.DataFrame(
            [
                {"Max Loss / Spread": 300.0, "Risk Budget": 1500.0, "Sizing": "1.00", "Guide Example": "5 spreads"},
                {"Max Loss / Spread": 300.0, "Risk Budget": 750.0, "Sizing": "0.50", "Guide Example": "2 to 3 spreads"},
            ]
        )
        st.dataframe(
            example,
            hide_index=True,
            width="stretch",
            column_config={
                "Max Loss / Spread": st.column_config.NumberColumn(format="$%.2f"),
                "Risk Budget": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
        st.caption(
            "Raj's Terminal uses the conservative whole-contract floor so the calculated position "
            "does not exceed the selected risk budget."
        )

    elif choice == "Dollar Risk by Account Size":
        st.markdown("**GUIDE TABLE // ASSUMES 15% TACTICAL SLEEVE + 1.5% RISK FOR 1.00**")
        table = pd.DataFrame(CROWN_DOLLAR_RISK_TABLE)
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={
                "Investable Assets": st.column_config.NumberColumn(format="$%.0f"),
                "Tactical Sleeve": st.column_config.NumberColumn(format="$%.0f"),
                "1.00": st.column_config.NumberColumn(format="$%.0f"),
                "0.75": st.column_config.NumberColumn(format="$%.0f"),
                "0.50": st.column_config.NumberColumn(format="$%.0f"),
                "0.25": st.column_config.NumberColumn(format="$%.0f"),
            },
        )

    elif choice == "Conviction vs Sizing":
        st.markdown(
            "**Conviction and size are separate.** A high-conviction idea can still be sized "
            "at 0.50 when the defined dollar risk is large or the stop is wide."
        )
        st.caption(
            "The guide treats conviction as thesis quality and sizing as portfolio-risk control."
        )

    else:
        st.markdown(
            "**DO NOT CHASE ENTRIES // SIZE FOR SURVIVAL // STOPS DEFINE INVALIDATION // "
            "TAKE PROFIT IN STAGES // KEEP THE TACTICAL BOOK SEPARATE FROM THE HOUSE VIEW**"
        )
        st.caption(
            "The guide's default profit-taking behavior is to take half off at T1 and let the "
            "rest run with a raised stop."
        )


def render_risk_sizing(
    client,
    *,
    account_picker: Callable[[str], dict[str, Any] | None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
    touch_session: Callable[[], None],
) -> None:
    st.subheader("Risk Sizing")
    st.caption(
        "CROWN MACRO RISK ENGINE // PORTFOLIO SLEEVE CONTROL // STOP-BASED POSITION SIZING // "
        "RAJ CLASSIFICATION RULE"
    )

    if not client:
        st.info("Connect E*TRADE at the top of the terminal to load the portfolio and size risk.")
        return

    if not st.session_state.get("etrade_accounts"):
        try:
            refresh_accounts(client)
        except ETradeError as exc:
            st.error(str(exc))
            return

    account = account_picker("risk_sizing_account")
    if not account:
        st.info("No E*TRADE account was returned.")
        return

    account_key = str(account.get("accountIdKey", ""))
    refresh_portfolio = st.button(
        "REFRESH RISK PORTFOLIO",
        type="primary",
        key="risk_refresh_portfolio",
    )

    holdings_cache = st.session_state.setdefault("etrade_holdings", {})
    if refresh_portfolio or holdings_cache.get(account_key) is None:
        try:
            holdings_cache[account_key] = client.get_portfolio(account_key)
            account_balance(client, account, refresh=True)
            touch_session()
        except ETradeError as exc:
            st.error(str(exc))
            return

    raw_holdings = holdings_cache.get(account_key) or []
    normalized = _normalized_holdings(raw_holdings)
    if normalized.empty:
        st.info("No positions were returned for the selected account.")
        return

    try:
        balance_payload = account_balance(client, account, refresh=False)
        account_total, cash_available, _ = balance_snapshot(balance_payload)
    except ETradeError:
        account_total = 0.0
        cash_available = 0.0

    market_total = pd.to_numeric(normalized["Market Value"], errors="coerce").fillna(0.0).sum()
    investable_assets = float(account_total or (market_total + cash_available))

    st.markdown("**1 // CLASSIFY THE CURRENT BOOK**")
    threshold_col, sleeve_col, risk_col = st.columns(3)
    with threshold_col:
        gain_threshold = float(
            st.number_input(
                "Long-Term Profit Threshold",
                min_value=-50.0,
                max_value=100.0,
                value=5.0,
                step=0.5,
                format="%.2f",
                help=(
                    "Raj rule: non-bond positions at or above this gain are LONG-TERM; "
                    "positions below it are TACTICAL. Bonds/CUSIPs stay LONG-TERM."
                ),
                key="risk_gain_threshold",
            )
        )
    with sleeve_col:
        tactical_sleeve_pct = float(
            st.number_input(
                "Tactical Sleeve %",
                min_value=1.0,
                max_value=50.0,
                value=CROWN_DEFAULT_TACTICAL_SLEEVE_PCT,
                step=1.0,
                format="%.1f",
                help="Crown guide framework: normally 10% to 20% of total investable assets.",
                key="risk_tactical_sleeve_pct",
            )
        )
    with risk_col:
        full_position_risk_pct = float(
            st.number_input(
                "Risk % of Sleeve for 1.00",
                min_value=0.1,
                max_value=10.0,
                value=CROWN_DEFAULT_RISK_PCT,
                step=0.1,
                format="%.1f",
                help="Crown guide framework: a full 1.00 position risks 1% to 2% of the tactical sleeve.",
                key="risk_full_position_pct",
            )
        )

    classified = classify_holdings(normalized, gain_threshold)
    summary = sleeve_summary(classified, investable_assets, tactical_sleeve_pct)

    if tactical_sleeve_pct < 10.0 or tactical_sleeve_pct > 20.0:
        st.warning("CROWN SLEEVE RANGE FLAG // the guide's stated tactical sleeve range is 10% to 20%.")
    if full_position_risk_pct < 1.0 or full_position_risk_pct > 2.0:
        st.warning("CROWN RISK RANGE FLAG // the guide's stated 1.00 risk cap is 1% to 2% of the tactical sleeve.")

    tactical_usage_pct = (
        summary["tactical_value"] / summary["target_tactical_dollars"] * 100.0
        if summary["target_tactical_dollars"] > 0
        else 0.0
    )

    s1, s2, s3, s4, s5 = st.columns(5)
    _metric_box(s1, "INVESTABLE ASSETS", _money(summary["investable_assets"]), "blue")
    _metric_box(s2, "TARGET TACTICAL SLEEVE", _money(summary["target_tactical_dollars"]), "neutral", _percent(tactical_sleeve_pct))
    current_tone = "negative" if summary["tactical_value"] > summary["target_tactical_dollars"] else "positive"
    _metric_box(
        s3,
        "CURRENT TACTICAL",
        _money(summary["tactical_value"]),
        current_tone,
        f"{tactical_usage_pct:.1f}% OF SLEEVE",
    )
    _metric_box(s4, "LONG-TERM / STRUCTURAL", _money(summary["long_term_value"]), "positive")
    room_tone = "positive" if summary["target_room"] >= 0 else "negative"
    _metric_box(s5, "TACTICAL ROOM", _money(summary["target_room"]), room_tone)

    # Keep the risk-book table decision-focused. CUSIP and security type remain
    # available internally for classification, but duplicating them on screen
    # adds noise (especially when a bond symbol already is the CUSIP).
    view = classified.copy()
    view["Market Value"] = pd.to_numeric(view["Market Value"], errors="coerce").fillna(0.0)
    view["Gain/Loss"] = pd.to_numeric(view["Gain/Loss"], errors="coerce").fillna(0.0)
    view["Gain/Loss %"] = pd.to_numeric(view["Gain/Loss %"], errors="coerce").fillna(0.0)
    view["% Account"] = (
        view["Market Value"].abs() / investable_assets * 100.0
        if investable_assets > 0
        else 0.0
    )
    view["% Tactical Sleeve"] = 0.0
    tactical_mask = view["Sleeve"].astype(str).eq("TACTICAL")
    if summary["target_tactical_dollars"] > 0:
        view.loc[tactical_mask, "% Tactical Sleeve"] = (
            view.loc[tactical_mask, "Market Value"].abs()
            / summary["target_tactical_dollars"]
            * 100.0
        )
    view.loc[~tactical_mask, "% Tactical Sleeve"] = float("nan")

    sleeve_rank = {"TACTICAL": 0, "LONG-TERM": 1}
    view["_sleeve_rank"] = view["Sleeve"].map(sleeve_rank).fillna(2)
    view = view.sort_values(
        ["_sleeve_rank", "Market Value"],
        ascending=[True, False],
        kind="stable",
    )

    view_columns = [
        "Sleeve",
        "Symbol",
        "Gain/Loss %",
        "Gain/Loss",
        "Market Value",
        "% Account",
        "% Tactical Sleeve",
    ]
    view = view[view_columns].reset_index(drop=True)

    def style_risk_book(row):
        styles = [""] * len(row)
        for idx, column in enumerate(row.index):
            value = row.get(column)
            if column == "Sleeve":
                styles[idx] = (
                    "color:" + BB_GREEN + ";font-weight:900;"
                    if str(value) == "LONG-TERM"
                    else "color:" + BB_ORANGE + ";font-weight:900;"
                )
            elif column in {"Gain/Loss %", "Gain/Loss"}:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    numeric = 0.0
                if numeric > 0:
                    styles[idx] = "color:" + BB_GREEN + ";font-weight:900;"
                elif numeric < 0:
                    styles[idx] = "color:" + BB_RED + ";font-weight:900;"
            elif column == "% Tactical Sleeve" and pd.notna(value):
                styles[idx] = "color:" + BB_BLUE + ";font-weight:900;"
        return styles

    st.caption(
        "RISK BOOK // tactical positions first // P&L red/green // % ACCOUNT shows portfolio weight // "
        "% TACTICAL SLEEVE shows how much of your tactical budget each tactical position consumes."
    )
    st.dataframe(
        view.style.apply(style_risk_book, axis=1),
        hide_index=True,
        width="stretch",
        height=min(700, max(250, 34 * len(view) + 42)),
        column_config={
            "Gain/Loss %": st.column_config.NumberColumn(format="%.2f%%"),
            "Gain/Loss": st.column_config.NumberColumn(format="$%.2f"),
            "Market Value": st.column_config.NumberColumn(format="$%.2f"),
            "% Account": st.column_config.NumberColumn(format="%.2f%%"),
            "% Tactical Sleeve": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )
    st.caption(
        "CLASSIFICATION // bonds stay LONG-TERM internally using bond/CUSIP metadata; "
        f"other positions at or above {gain_threshold:.2f}% gain are LONG-TERM; below are TACTICAL."
    )

    st.markdown("**2 // SIZE THE NEXT TRADE**")
    ticker_col, quote_col = st.columns([3.4, 1.2], vertical_alignment="bottom")
    with ticker_col:
        ticker = st.text_input(
            "Ticker",
            value=str(st.session_state.get("risk_ticker", "SPY")),
            key="risk_ticker",
        ).strip().upper()
    with quote_col:
        load_quote = st.button(
            "PULL E*TRADE QUOTE",
            type="primary",
            width="stretch",
            key="risk_pull_quote",
        )

    quote_key = "risk_quote_data"
    if load_quote:
        try:
            quote_data = quote_summary(client.get_quote(ticker))
            st.session_state[quote_key] = quote_data
            st.session_state["risk_quote_symbol"] = ticker
            touch_session()
        except ETradeError as exc:
            st.error(str(exc))

    quote_data = st.session_state.get(quote_key)
    if st.session_state.get("risk_quote_symbol") != ticker:
        quote_data = None

    if quote_data:
        q1, q2, q3, q4 = st.columns(4)
        _metric_box(q1, "LAST", _money(quote_data.get("last") or 0.0), "positive")
        _metric_box(q2, "BID", _money(quote_data.get("bid") or 0.0), "blue")
        _metric_box(q3, "ASK", _money(quote_data.get("ask") or 0.0), "blue")
        change = float(quote_data.get("change") or 0.0)
        _metric_box(q4, "CHANGE", f"{change:+.2f}", "positive" if change >= 0 else "negative")

    structure_col, multiplier_col = st.columns(2)
    with structure_col:
        trade_structure = st.selectbox(
            "Trade Structure",
            ["STOCK / ETF", "DEFINED-RISK OPTION SPREAD"],
            key="risk_trade_structure",
        )
    with multiplier_col:
        size_multiplier = float(
            st.selectbox(
                "Crown Size Multiplier",
                CROWN_SIZE_MULTIPLIERS,
                index=0,
                format_func=lambda value: f"{value:.2f}",
                key="risk_size_multiplier",
            )
        )

    risk_budget = crown_risk_budget(
        investable_assets,
        tactical_sleeve_pct,
        full_position_risk_pct,
        size_multiplier,
    )

    r1, r2, r3 = st.columns(3)
    _metric_box(r1, "1.00 RISK BUDGET", _money(risk_budget["full_risk_budget"]), "neutral")
    _metric_box(r2, "SELECTED SIZE", f"{size_multiplier:.2f}", "blue")
    _metric_box(r3, "MAX DOLLAR RISK", _money(risk_budget["selected_risk_budget"]), "positive")

    if trade_structure == "STOCK / ETF":
        default_entry = float(quote_data.get("last") or 100.0) if quote_data else 100.0
        e1, e2 = st.columns(2)
        with e1:
            entry_price = float(
                st.number_input(
                    "Entry Price",
                    min_value=0.01,
                    value=default_entry,
                    step=0.01,
                    format="%.2f",
                    key="risk_entry_price",
                )
            )
        with e2:
            stop_price = float(
                st.number_input(
                    "Stop Loss",
                    min_value=0.0,
                    value=max(0.01, default_entry * 0.95),
                    step=0.01,
                    format="%.2f",
                    key="risk_stop_price",
                )
            )

        try:
            sized = stock_position_size(
                entry_price,
                stop_price,
                risk_budget["selected_risk_budget"],
            )
            p1, p2, p3, p4, p5 = st.columns(5)
            _metric_box(p1, "RISK / SHARE", _money(sized["risk_per_share"]), "negative")
            _metric_box(p2, "MAX SHARES", f"{sized['shares']:,}", "positive")
            _metric_box(p3, "POSITION NOTIONAL", _money(sized["notional"]), "blue")
            _metric_box(p4, "ACTUAL STOP RISK", _money(sized["actual_risk"]), "negative")
            _metric_box(p5, "UNUSED RISK", _money(sized["unused_risk_budget"]), "neutral")
        except ValueError as exc:
            st.warning(str(exc))

    else:
        max_loss_per_spread = float(
            st.number_input(
                "Maximum Loss per Spread",
                min_value=0.01,
                value=300.0,
                step=25.0,
                format="%.2f",
                key="risk_max_loss_spread",
            )
        )
        try:
            sized = defined_risk_contracts(
                max_loss_per_spread,
                risk_budget["selected_risk_budget"],
            )
            p1, p2, p3, p4 = st.columns(4)
            _metric_box(p1, "MAX LOSS / SPREAD", _money(sized["max_loss_per_spread"]), "negative")
            _metric_box(p2, "MAX SPREADS", f"{sized['contracts']:,}", "positive")
            _metric_box(p3, "ACTUAL MAX RISK", _money(sized["actual_risk"]), "negative")
            _metric_box(p4, "UNUSED RISK", _money(sized["unused_risk_budget"]), "neutral")
        except ValueError as exc:
            st.warning(str(exc))

    st.caption(
        "RISK ENGINE // size is limited by the selected Crown-style dollar-risk budget. "
        "For stocks/ETFs, risk = shares x distance to stop. For defined-risk spreads, max loss is the risk."
    )

    _render_crown_reference()
