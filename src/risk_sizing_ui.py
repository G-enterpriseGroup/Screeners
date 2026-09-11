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

    s1, s2, s3, s4, s5 = st.columns(5)
    _metric_box(s1, "INVESTABLE ASSETS", _money(summary["investable_assets"]), "blue")
    _metric_box(s2, "TARGET TACTICAL SLEEVE", _money(summary["target_tactical_dollars"]), "neutral", _percent(tactical_sleeve_pct))
    current_tone = "negative" if summary["tactical_value"] > summary["target_tactical_dollars"] else "positive"
    _metric_box(s3, "CURRENT TACTICAL", _money(summary["tactical_value"]), current_tone, _percent(summary["tactical_pct_of_assets"]))
    _metric_box(s4, "LONG-TERM / STRUCTURAL", _money(summary["long_term_value"]), "positive")
    room_tone = "positive" if summary["target_room"] >= 0 else "negative"
    _metric_box(s5, "TACTICAL ROOM", _money(summary["target_room"]), room_tone)

    view_columns = [
        "Sleeve",
        "Symbol",
        "Type",
        "CUSIP",
        "Gain/Loss %",
        "Market Value",
        "Gain/Loss",
        "Sleeve Rule",
    ]
    view = classified[[column for column in view_columns if column in classified.columns]].copy()

    def style_sleeve(row):
        sleeve = str(row.get("Sleeve", ""))
        if sleeve == "LONG-TERM":
            return ["color:" + BB_GREEN + ";font-weight:900;" if col == "Sleeve" else "" for col in row.index]
        return ["color:" + BB_ORANGE + ";font-weight:900;" if col == "Sleeve" else "" for col in row.index]

    st.dataframe(
        view.style.apply(style_sleeve, axis=1),
        hide_index=True,
        width="stretch",
        height=min(700, max(250, 34 * len(view) + 42)),
        column_config={
            "Gain/Loss %": st.column_config.NumberColumn(format="%.2f%%"),
            "Market Value": st.column_config.NumberColumn(format="$%.2f"),
            "Gain/Loss": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    st.caption(
        "RAJ CLASSIFICATION // bonds or positions carrying a CUSIP are LONG-TERM regardless of P&L; "
        f"other positions >= {gain_threshold:.2f}% gain are LONG-TERM; below that threshold are TACTICAL. "
        "This classification rule is your custom rule, not a Crown Macro rule."
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
            disabled=not ticker,
        )

    if load_quote:
        try:
            quote = quote_summary(client.get_quote(ticker))
            st.session_state["risk_quote"] = quote
            st.session_state["risk_quote_symbol"] = ticker
            if quote.get("last"):
                st.session_state["risk_entry_price"] = float(quote["last"])
            touch_session()
        except ETradeError as exc:
            st.error(str(exc))

    quote = (
        st.session_state.get("risk_quote")
        if st.session_state.get("risk_quote_symbol") == ticker
        else None
    )
    if quote:
        q1, q2, q3, q4 = st.columns(4)
        _metric_box(q1, "LAST", _money(quote.get("last") or 0.0), "positive")
        _metric_box(q2, "BID", _money(quote.get("bid") or 0.0), "neutral")
        _metric_box(q3, "ASK", _money(quote.get("ask") or 0.0), "neutral")
        change = float(quote.get("change") or 0.0)
        _metric_box(q4, "CHANGE", ("+" if change > 0 else "") + _money(change), "positive" if change > 0 else "negative" if change < 0 else "neutral")

    input1, input2, input3 = st.columns(3)
    with input1:
        size_multiplier = float(
            st.selectbox(
                "Crown Size Multiplier",
                CROWN_SIZE_MULTIPLIERS,
                index=0,
                format_func=lambda value: f"{value:.2f}",
                key="risk_size_multiplier",
            )
        )
    with input2:
        risk_model = st.selectbox(
            "Risk Model",
            ["STOCK / ETF — STOP BASED", "DEFINED-RISK OPTION SPREAD — MAX LOSS"],
            key="risk_model",
        )
    with input3:
        entry_default = float((quote or {}).get("last") or 100.0)
        if "risk_entry_price" not in st.session_state:
            st.session_state["risk_entry_price"] = entry_default
        entry_price = float(
            st.number_input(
                "Entry Price",
                min_value=0.01,
                step=0.01,
                format="%.2f",
                key="risk_entry_price",
            )
        )

    budgets = crown_risk_budget(
        investable_assets,
        tactical_sleeve_pct,
        full_position_risk_pct,
        size_multiplier,
    )

    r1, r2, r3, r4 = st.columns(4)
    _metric_box(r1, "TACTICAL SLEEVE", _money(budgets["tactical_sleeve"]), "blue")
    _metric_box(r2, "1.00 RISK BUDGET", _money(budgets["full_risk_budget"]), "neutral")
    _metric_box(r3, "SELECTED SIZE", f"{size_multiplier:.2f}", "positive")
    _metric_box(r4, "MAX DOLLAR RISK", _money(budgets["selected_risk_budget"]), "positive")

    if risk_model.startswith("STOCK"):
        stop_col, enabled_col = st.columns([2.2, 1.0], vertical_alignment="bottom")
        with enabled_col:
            use_stop = st.toggle(
                "Use Stop Loss",
                value=True,
                key="risk_use_stop",
            )
        with stop_col:
            stop_default = max(0.01, round(entry_price * 0.95, 2))
            if "risk_stop_price" not in st.session_state:
                st.session_state["risk_stop_price"] = stop_default
            stop_price = float(
                st.number_input(
                    "Stop Loss",
                    min_value=0.01,
                    step=0.01,
                    format="%.2f",
                    key="risk_stop_price",
                    disabled=not use_stop,
                )
            )

        if use_stop:
            try:
                sized = stock_position_size(
                    entry_price,
                    stop_price,
                    budgets["selected_risk_budget"],
                )
                x1, x2, x3, x4, x5 = st.columns(5)
                _metric_box(x1, "RISK / SHARE", _money(sized["risk_per_share"]), "negative")
                _metric_box(x2, "MAX SHARES", f"{sized['shares']:,}", "positive")
                _metric_box(x3, "POSITION NOTIONAL", _money(sized["notional"]), "blue")
                _metric_box(x4, "ACTUAL STOP RISK", _money(sized["actual_risk"]), "negative")
                _metric_box(x5, "UNUSED RISK", _money(sized["unused_risk_budget"]), "neutral")

                projected_tactical = summary["tactical_value"] + sized["notional"]
                projected_pct = projected_tactical / investable_assets * 100 if investable_assets else 0.0
                if projected_tactical > summary["target_tactical_dollars"]:
                    st.warning(
                        "TACTICAL SLEEVE CAP // this stock notional would put the custom tactical "
                        f"classification at approximately {projected_pct:.2f}% of investable assets, "
                        f"above the selected {tactical_sleeve_pct:.1f}% sleeve."
                    )
                else:
                    st.success(
                        "RISK CHECK PASSED // calculated whole-share size stays within the selected "
                        "Crown-style dollar risk budget."
                    )
            except ValueError as exc:
                st.error(str(exc))
        else:
            st.info(
                "NO STOP ENTERED // Crown's stock/futures quantity calculation needs a stop distance. "
                "The maximum dollar risk budget above is still valid, but share quantity is not calculated."
            )

    else:
        max_loss_per_spread = float(
            st.number_input(
                "Max Loss per Spread",
                min_value=1.0,
                value=300.0,
                step=25.0,
                format="%.2f",
                key="risk_max_loss_spread",
            )
        )
        try:
            sized = defined_risk_contracts(
                max_loss_per_spread,
                budgets["selected_risk_budget"],
            )
            x1, x2, x3, x4 = st.columns(4)
            _metric_box(x1, "MAX LOSS / SPREAD", _money(sized["max_loss_per_spread"]), "negative")
            _metric_box(x2, "MAX WHOLE SPREADS", f"{sized['contracts']:,}", "positive")
            _metric_box(x3, "ACTUAL MAX RISK", _money(sized["actual_risk"]), "negative")
            _metric_box(x4, "UNUSED RISK", _money(sized["unused_risk_budget"]), "neutral")
            if sized["contracts"] == 0:
                st.warning("RISK BUDGET TOO SMALL // one spread would exceed the selected dollar-risk budget.")
        except ValueError as exc:
            st.error(str(exc))

    st.markdown("**3 // SIZE LADDER FOR THIS PORTFOLIO**")
    ladder_rows = []
    for multiplier in CROWN_SIZE_MULTIPLIERS:
        row_budget = crown_risk_budget(
            investable_assets,
            tactical_sleeve_pct,
            full_position_risk_pct,
            multiplier,
        )
        ladder_rows.append(
            {
                "Crown Size": f"{multiplier:.2f}",
                "Dollar Risk": row_budget["selected_risk_budget"],
                "% of 1.00": multiplier * 100.0,
            }
        )
    ladder = pd.DataFrame(ladder_rows)
    st.dataframe(
        ladder,
        hide_index=True,
        width="stretch",
        column_config={
            "Dollar Risk": st.column_config.NumberColumn(format="$%.2f"),
            "% of 1.00": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )

    _render_crown_reference()

    st.caption(
        "SOURCE IMPLEMENTATION // Crown Macro Education guide supplied by the user. Crown mechanics: "
        "10-20% tactical sleeve; 1.00 risks 1-2% of the tactical sleeve; stock/futures size is worked "
        "backward from stop distance; defined-risk option spreads use max loss as risk. Raj's 5% "
        "long-term/tactical P&L classification is a separate custom portfolio rule. Educational analytics only."
    )
