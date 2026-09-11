"""Enhanced Crown-style risk-sizing dashboard with formula tooltips.

The underlying Crown mechanics remain in src.risk_sizing. This UI adds a
step-by-step workflow and hoverable, number-specific calculation explanations.
"""

from __future__ import annotations

import html
from typing import Any, Callable

import pandas as pd
import streamlit as st

from src.etrade_client import ETradeError, quote_summary
from src.risk_sizing import (
    CROWN_DEFAULT_RISK_PCT,
    CROWN_DEFAULT_TACTICAL_SLEEVE_PCT,
    CROWN_SIZE_MULTIPLIERS,
    classify_holdings,
    crown_risk_budget,
    defined_risk_contracts,
    sleeve_summary,
    stock_position_size,
)
from src.risk_sizing_ui import _normalized_holdings, _render_crown_reference
from src.theme import BB_BLACK, BB_BLUE, BB_GREEN, BB_ORANGE, BB_RED


def _money(value: float) -> str:
    return "$" + f"{float(value):,.2f}"


def _percent(value: float) -> str:
    return f"{float(value):,.2f}%"


def _render_tooltip_css() -> None:
    st.markdown(
        """
        <style>
        .rs-card {
            position:relative;
            background:#000000;
            border:1px solid #fb8b1e;
            padding:.55rem .7rem;
            min-height:108px;
            font-family:"Courier New",monospace;
            overflow:visible !important;
        }
        .rs-card-head {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:.45rem;
        }
        .rs-card-label {
            color:#fb8b1e !important;
            font-size:.78rem;
            font-weight:900;
            line-height:1.15;
        }
        .rs-help {
            position:relative;
            display:inline-flex;
            align-items:center;
            justify-content:center;
            width:18px;
            height:18px;
            flex:0 0 18px;
            border:1px solid #fb8b1e;
            border-radius:50% !important;
            color:#fb8b1e !important;
            font-size:12px;
            font-weight:900;
            cursor:help;
            line-height:1;
        }
        .rs-help-tip {
            visibility:hidden;
            opacity:0;
            position:absolute;
            z-index:99999;
            right:-2px;
            top:24px;
            width:330px;
            max-width:70vw;
            padding:.7rem .8rem;
            border:1px solid #fb8b1e;
            background:#080808;
            color:#fb8b1e !important;
            font-family:"Courier New",monospace;
            font-size:.74rem;
            line-height:1.45;
            font-weight:600;
            white-space:normal;
            box-shadow:0 8px 24px rgba(0,0,0,.72);
            pointer-events:none;
        }
        .rs-help:hover .rs-help-tip,
        .rs-help:focus .rs-help-tip {
            visibility:visible;
            opacity:1;
        }
        .rs-card-value {
            font-size:1.45rem;
            font-weight:900;
            margin-top:.22rem;
            line-height:1.15;
        }
        .rs-card-detail {
            font-size:.75rem;
            margin-top:.16rem;
            font-weight:700;
        }
        .rs-howto {
            border:1px solid #fb8b1e;
            background:#030303;
            padding:.7rem .85rem;
            margin:.25rem 0 .8rem 0;
            font-family:"Courier New",monospace;
        }
        .rs-howto strong { color:#4af6c3 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _metric_box(
    container,
    label: str,
    value: str,
    tone: str = "neutral",
    detail: str = "",
    help_text: str = "",
) -> None:
    color = {
        "positive": BB_GREEN,
        "negative": BB_RED,
        "blue": BB_BLUE,
        "neutral": BB_ORANGE,
    }.get(tone, BB_ORANGE)

    help_html = ""
    if help_text:
        safe_help = html.escape(str(help_text)).replace("\n", "<br>")
        help_html = (
            '<span class="rs-help" tabindex="0">?'
            '<span class="rs-help-tip">' + safe_help + "</span></span>"
        )

    detail_html = (
        '<div class="rs-card-detail" style="color:' + color + ';">'
        + html.escape(str(detail))
        + "</div>"
        if detail
        else ""
    )

    container.markdown(
        '<div class="rs-card">'
        '<div class="rs-card-head">'
        '<div class="rs-card-label">' + html.escape(str(label)) + "</div>"
        + help_html
        + "</div>"
        '<div class="rs-card-value" style="color:' + color + ';">'
        + html.escape(str(value))
        + "</div>"
        + detail_html
        + "</div>",
        unsafe_allow_html=True,
    )


def _how_to_use() -> None:
    with st.expander("?  HOW TO USE RISK SIZING // STEP BY STEP", expanded=False):
        st.markdown(
            """
            <div class="rs-howto">
            <strong>1 // CLASSIFY.</strong> Leave the long-term threshold at 5% unless you want to change your rule. Bonds remain structural automatically; non-bond positions below the threshold are tactical.<br><br>
            <strong>2 // SET THE SLEEVE.</strong> Tactical Sleeve % controls how much of the account you allow in tactical positions. Crown's guide describes 10%-20%; this page defaults to 15%.<br><br>
            <strong>3 // SET 1.00 RISK.</strong> Risk % of Sleeve for 1.00 controls how much of the tactical sleeve you are willing to lose if a full-size trade hits its stop. Crown's guide describes 1%-2%; this page defaults to 1.5%.<br><br>
            <strong>4 // CHECK ROOM.</strong> Tactical Room is remaining tactical capital: Target Tactical Sleeve minus Current Tactical. A positive number means room remains; a negative number means the sleeve is over target.<br><br>
            <strong>5 // SIZE A TRADE.</strong> Enter a ticker, pull the E*TRADE quote, choose STOCK/ETF or DEFINED-RISK OPTION SPREAD, then choose 1.00 / 0.75 / 0.50 / 0.25.<br><br>
            <strong>6 // DEFINE THE LOSS.</strong> For stock/ETF, enter Entry and Stop. The terminal calculates risk per share and the maximum whole shares that fit the selected dollar-risk budget. For a defined-risk spread, enter max loss per spread and it calculates the maximum whole spreads.<br><br>
            <strong>7 // VERIFY BOTH LIMITS.</strong> MAX DOLLAR RISK controls how much you may lose. TACTICAL ROOM controls how much tactical capital remains available. They are different limits.
            </div>
            """,
            unsafe_allow_html=True,
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
    _render_tooltip_css()

    st.subheader("Risk Sizing")
    st.caption(
        "CROWN MACRO RISK ENGINE // PORTFOLIO SLEEVE CONTROL // STOP-BASED POSITION SIZING // "
        "RAJ CLASSIFICATION RULE"
    )
    _how_to_use()

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

    market_total = pd.to_numeric(
        normalized["Market Value"], errors="coerce"
    ).fillna(0.0).sum()
    investable_assets = float(account_total or (market_total + cash_available))

    st.markdown("**1 // CLASSIFY THE CURRENT BOOK**")
    threshold_col, sleeve_col, risk_col = st.columns(3)

    current_threshold = float(st.session_state.get("risk_gain_threshold", 5.0))
    current_sleeve = float(
        st.session_state.get("risk_tactical_sleeve_pct", CROWN_DEFAULT_TACTICAL_SLEEVE_PCT)
    )
    current_full_risk = float(
        st.session_state.get("risk_full_position_pct", CROWN_DEFAULT_RISK_PCT)
    )

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
                    f"CURRENT: {current_threshold:.2f}%. Raj's classification rule: bonds/CUSIPs are always LONG-TERM. "
                    f"For other holdings: Gain/Loss % >= {current_threshold:.2f}% => LONG-TERM; below {current_threshold:.2f}% => TACTICAL. "
                    "This is your custom classification rule, not Crown's sizing formula."
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
                help=(
                    f"CURRENT: {current_sleeve:.1f}%. Formula: Target Tactical Sleeve = Investable Assets x {current_sleeve:.1f}%. "
                    "Crown's education guide frames the tactical sleeve at 10%-20% of investable assets; 15% is the default midpoint used here."
                ),
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
                help=(
                    f"CURRENT: {current_full_risk:.1f}%. Formula: 1.00 Risk Budget = Target Tactical Sleeve x {current_full_risk:.1f}%. "
                    "Crown's guide frames a full 1.00 position at 1%-2% risk of the tactical sleeve; 1.5% is the default midpoint."
                ),
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
    _metric_box(
        s1,
        "INVESTABLE ASSETS",
        _money(summary["investable_assets"]),
        "blue",
        help_text=(
            f"CURRENT VALUE: {_money(summary['investable_assets'])}. This is the account base used by the risk engine. "
            "Primary source = E*TRADE total account value. Fallback, if unavailable = holdings market value + cash. "
            "Every sleeve calculation starts from this number."
        ),
    )
    _metric_box(
        s2,
        "TARGET TACTICAL SLEEVE",
        _money(summary["target_tactical_dollars"]),
        "neutral",
        _percent(tactical_sleeve_pct),
        help_text=(
            f"CALC: {_money(summary['investable_assets'])} x {tactical_sleeve_pct:.2f}% = "
            f"{_money(summary['target_tactical_dollars'])}. This is the maximum target capital allocated to the tactical sleeve under your selected setting."
        ),
    )
    current_tone = (
        "negative"
        if summary["tactical_value"] > summary["target_tactical_dollars"]
        else "positive"
    )
    _metric_box(
        s3,
        "CURRENT TACTICAL",
        _money(summary["tactical_value"]),
        current_tone,
        f"{tactical_usage_pct:.1f}% OF SLEEVE",
        help_text=(
            f"CURRENT TACTICAL = sum of absolute market values for positions classified TACTICAL = {_money(summary['tactical_value'])}. "
            f"SLEEVE USED: {_money(summary['tactical_value'])} / {_money(summary['target_tactical_dollars'])} x 100 = {tactical_usage_pct:.1f}%."
        ),
    )
    _metric_box(
        s4,
        "LONG-TERM / STRUCTURAL",
        _money(summary["long_term_value"]),
        "positive",
        help_text=(
            f"CURRENT VALUE: {_money(summary['long_term_value'])}. CALC: sum of absolute market values classified LONG-TERM. "
            f"Bonds/CUSIPs are structural automatically; other holdings qualify when Gain/Loss % >= {gain_threshold:.2f}% under your rule."
        ),
    )
    room_tone = "positive" if summary["target_room"] >= 0 else "negative"
    _metric_box(
        s5,
        "TACTICAL ROOM",
        _money(summary["target_room"]),
        room_tone,
        help_text=(
            f"CALC: Target Tactical Sleeve {_money(summary['target_tactical_dollars'])} - Current Tactical {_money(summary['tactical_value'])} = "
            f"{_money(summary['target_room'])}. Positive = room remains. Negative = tactical exposure is above your selected sleeve target."
        ),
    )

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

    view["_sleeve_rank"] = view["Sleeve"].map({"TACTICAL": 0, "LONG-TERM": 1}).fillna(2)
    view = view.sort_values(
        ["_sleeve_rank", "Market Value"],
        ascending=[True, False],
        kind="stable",
    )
    view = view[
        [
            "Sleeve",
            "Symbol",
            "Gain/Loss %",
            "Gain/Loss",
            "Market Value",
            "% Account",
            "% Tactical Sleeve",
        ]
    ].reset_index(drop=True)

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
        "RISK BOOK // tactical positions first // P&L red/green // % ACCOUNT = portfolio weight // "
        "% TACTICAL SLEEVE = how much of the target tactical sleeve each tactical position uses."
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

    st.markdown("**2 // SIZE THE NEXT TRADE**")
    ticker_col, quote_col = st.columns([3.4, 1.2], vertical_alignment="bottom")
    with ticker_col:
        ticker = st.text_input(
            "Ticker",
            value=str(st.session_state.get("risk_ticker", "SPY")),
            key="risk_ticker",
            help="Enter the symbol you are considering. Pulling the quote is optional; you can still enter your own entry price manually.",
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
        _metric_box(q1, "LAST", _money(quote_data.get("last") or 0.0), "positive", help_text="Latest price returned by the connected E*TRADE quote endpoint. It is a reference for setting your entry; the risk formula uses the Entry Price field below.")
        _metric_box(q2, "BID", _money(quote_data.get("bid") or 0.0), "blue", help_text="Current bid returned by E*TRADE: the highest displayed price buyers are currently bidding, subject to your market-data entitlement and quote timing.")
        _metric_box(q3, "ASK", _money(quote_data.get("ask") or 0.0), "blue", help_text="Current ask returned by E*TRADE: the lowest displayed price sellers are currently asking, subject to your market-data entitlement and quote timing.")
        change = float(quote_data.get("change") or 0.0)
        _metric_box(q4, "CHANGE", f"{change:+.2f}", "positive" if change >= 0 else "negative", help_text=f"E*TRADE-reported price change for {ticker}. This is informational only and does not alter position sizing.")

    structure_col, multiplier_col = st.columns(2)
    with structure_col:
        trade_structure = st.selectbox(
            "Trade Structure",
            ["STOCK / ETF", "DEFINED-RISK OPTION SPREAD"],
            key="risk_trade_structure",
            help="Choose STOCK / ETF when risk is defined by Entry minus Stop. Choose DEFINED-RISK OPTION SPREAD when the trade already has a known maximum dollar loss per spread.",
        )
    with multiplier_col:
        size_multiplier = float(
            st.selectbox(
                "Crown Size Multiplier",
                CROWN_SIZE_MULTIPLIERS,
                index=0,
                format_func=lambda value: f"{value:.2f}",
                key="risk_size_multiplier",
                help="1.00 = full standard risk; 0.75 = three-quarters; 0.50 = half; 0.25 = quarter. It multiplies the 1.00 dollar-risk budget; it is not a percentage of the whole portfolio.",
            )
        )

    risk_budget = crown_risk_budget(
        investable_assets,
        tactical_sleeve_pct,
        full_position_risk_pct,
        size_multiplier,
    )

    r1, r2, r3 = st.columns(3)
    _metric_box(
        r1,
        "1.00 RISK BUDGET",
        _money(risk_budget["full_risk_budget"]),
        "neutral",
        help_text=(
            f"CALC: Investable Assets {_money(investable_assets)} x Tactical Sleeve {tactical_sleeve_pct:.2f}% x Risk {full_position_risk_pct:.2f}% = "
            f"{_money(risk_budget['full_risk_budget'])}. This is the maximum loss budget for a 1.00 trade."
        ),
    )
    _metric_box(
        r2,
        "SELECTED SIZE",
        f"{size_multiplier:.2f}",
        "blue",
        help_text=(
            f"You selected {size_multiplier:.2f}. It scales the full 1.00 risk budget. "
            "Example: 0.50 means half the dollar risk of 1.00."
        ),
    )
    _metric_box(
        r3,
        "MAX DOLLAR RISK",
        _money(risk_budget["selected_risk_budget"]),
        "positive",
        help_text=(
            f"CALC: 1.00 Risk Budget {_money(risk_budget['full_risk_budget'])} x Size {size_multiplier:.2f} = "
            f"{_money(risk_budget['selected_risk_budget'])}. This is the most the sizing engine allows the trade to lose at the defined stop/max-loss point."
        ),
    )

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
                    help="Your planned fill price. Position sizing uses this number, not the live quote card above.",
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
                    help="The price where the trade thesis is invalidated. Risk/share = absolute Entry minus Stop. A wider stop means fewer shares for the same dollar-risk budget.",
                )
            )

        try:
            sized = stock_position_size(
                entry_price,
                stop_price,
                risk_budget["selected_risk_budget"],
            )
            p1, p2, p3, p4, p5 = st.columns(5)
            _metric_box(
                p1,
                "RISK / SHARE",
                _money(sized["risk_per_share"]),
                "negative",
                help_text=f"CALC: |Entry {_money(entry_price)} - Stop {_money(stop_price)}| = {_money(sized['risk_per_share'])} risk per share.",
            )
            _metric_box(
                p2,
                "MAX SHARES",
                f"{sized['shares']:,}",
                "positive",
                help_text=(
                    f"CALC: floor(Max Dollar Risk {_money(risk_budget['selected_risk_budget'])} / Risk per Share {_money(sized['risk_per_share'])}) = {sized['shares']} shares. "
                    "The floor keeps stop-loss risk from exceeding the selected budget."
                ),
            )
            _metric_box(
                p3,
                "POSITION NOTIONAL",
                _money(sized["notional"]),
                "blue",
                help_text=f"CALC: {sized['shares']} shares x Entry {_money(entry_price)} = {_money(sized['notional'])}. This is capital/notional deployed, not the amount you are expected to lose.",
            )
            _metric_box(
                p4,
                "ACTUAL STOP RISK",
                _money(sized["actual_risk"]),
                "negative",
                help_text=f"CALC: {sized['shares']} shares x {_money(sized['risk_per_share'])} risk/share = {_money(sized['actual_risk'])} if the stop is filled at the stop price. Slippage can make realized loss different.",
            )
            _metric_box(
                p5,
                "UNUSED RISK",
                _money(sized["unused_risk_budget"]),
                "neutral",
                help_text=f"CALC: Max Dollar Risk {_money(risk_budget['selected_risk_budget'])} - Actual Stop Risk {_money(sized['actual_risk'])} = {_money(sized['unused_risk_budget'])}. This remainder exists because shares must be whole numbers.",
            )

            if sized["notional"] > max(summary["target_room"], 0.0):
                st.warning(
                    "TACTICAL CAPACITY CHECK // this stop-based position notional is larger than current Tactical Room. "
                    "Risk budget and sleeve room are different limits; review capital deployment before entering the full calculated size."
                )
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
                help="Enter the broker/platform maximum loss for ONE defined-risk spread. Crown's framework treats max loss as the risk for a defined-risk options spread.",
            )
        )
        try:
            sized = defined_risk_contracts(
                max_loss_per_spread,
                risk_budget["selected_risk_budget"],
            )
            p1, p2, p3, p4 = st.columns(4)
            _metric_box(p1, "MAX LOSS / SPREAD", _money(sized["max_loss_per_spread"]), "negative", help_text=f"You entered {_money(sized['max_loss_per_spread'])} as the defined maximum loss for one spread. This becomes the unit risk used by the position-size formula.")
            _metric_box(p2, "MAX SPREADS", f"{sized['contracts']:,}", "positive", help_text=f"CALC: floor(Max Dollar Risk {_money(risk_budget['selected_risk_budget'])} / Max Loss per Spread {_money(sized['max_loss_per_spread'])}) = {sized['contracts']} spreads.")
            _metric_box(p3, "ACTUAL MAX RISK", _money(sized["actual_risk"]), "negative", help_text=f"CALC: {sized['contracts']} spreads x {_money(sized['max_loss_per_spread'])} max loss/spread = {_money(sized['actual_risk'])} maximum modeled risk.")
            _metric_box(p4, "UNUSED RISK", _money(sized["unused_risk_budget"]), "neutral", help_text=f"CALC: Max Dollar Risk {_money(risk_budget['selected_risk_budget'])} - Actual Max Risk {_money(sized['actual_risk'])} = {_money(sized['unused_risk_budget'])}. Remainder stays unused because spread count must be whole.")
        except ValueError as exc:
            st.warning(str(exc))

    st.caption(
        "RISK ENGINE // hover any circled ? on the number cards for the exact formula using the current values. "
        "For stocks/ETFs, risk = shares x distance to stop. For defined-risk spreads, max loss is the risk."
    )

    _render_crown_reference()
