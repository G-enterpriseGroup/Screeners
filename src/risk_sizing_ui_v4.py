"""Risk-sizing UI v4: true cash balance beside Tactical Room.

This adapter keeps the full v2 Crown risk-sizing engine, but promotes E*TRADE's
actual cash balance into the main metric row. It intentionally does NOT use
cashAvailableForInvestment, cashBuyingPower, marginBuyingPower, or day-trading
buying power because those can reflect credit/purchasing power rather than cash
actually held in the account.

It also installs the compact Bloomberg layout used by Raj's Terminal so dense
risk screens waste less vertical space and selectors are visually distinct.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import streamlit as st

from src.etrade_client import find_number


_V2_PATH = Path(__file__).with_name("risk_sizing_ui_v2.py")
_SOURCE = _V2_PATH.read_text(encoding="utf-8")

_OLD_COLUMNS = "    s1, s2, s3, s4, s5 = st.columns(5)\n"
_NEW_COLUMNS = "    s1, s2, s3, s4, s5, s6 = st.columns(6)\n"
if _OLD_COLUMNS not in _SOURCE:
    raise RuntimeError("Risk-sizing v2 metric-row marker was not found.")
_SOURCE = _SOURCE.replace(_OLD_COLUMNS, _NEW_COLUMNS, 1)

_OLD_ROOM_BLOCK = '''    _metric_box(
        s5,
        "TACTICAL ROOM",
        _money(summary["target_room"]),
        room_tone,
        help_text=(
            f"CALC: Target Tactical Sleeve {_money(summary['target_tactical_dollars'])} - Current Tactical {_money(summary['tactical_value'])} = "
            f"{_money(summary['target_room'])}. Positive = room remains. Negative = tactical exposure is above your selected sleeve target."
        ),
    )
'''

_NEW_ROOM_BLOCK = _OLD_ROOM_BLOCK + '''    cash_pct = cash_available / investable_assets * 100.0 if investable_assets else 0.0
    cash_tone = "positive" if cash_available >= 0 else "negative"
    cash_source = st.session_state.get("_risk_true_cash_source", "UNAVAILABLE")
    cash_fields = st.session_state.get("_risk_true_cash_fields", {})
    raw_cash_text = ", ".join(
        f"{name}={_money(value)}"
        for name, value in cash_fields.items()
        if value is not None
    ) or "No cash-only E*TRADE field was returned."
    _metric_box(
        s6,
        "CASH BALANCE",
        _money(cash_available),
        cash_tone,
        f"{cash_pct:.2f}% OF ACCOUNT // {cash_source}",
        help_text=(
            f"E*TRADE TRUE CASH: {_money(cash_available)}. FIELD USED: {cash_source}. "
            f"RAW CASH-ONLY FIELDS: {raw_cash_text}. "
            "The selector prefers a meaningful non-zero cashBalance; if E*TRADE returns cashBalance=0, it automatically checks netCash, then moneyMktBalance, then settledCashForInvestment. "
            "It intentionally EXCLUDES cashAvailableForInvestment, cashBuyingPower, marginBuyingPower, totalAvailableForWithdrawal, and day-trading buying power so margin credit is never labeled as cash."
        ),
    )
'''

if _OLD_ROOM_BLOCK not in _SOURCE:
    raise RuntimeError("Risk-sizing v2 Tactical Room block was not found.")
_SOURCE = _SOURCE.replace(_OLD_ROOM_BLOCK, _NEW_ROOM_BLOCK, 1)

# Execute the enhanced v2 module in this module namespace. This keeps every
# existing sizing control/example intact while making this adapter lightweight.
exec(compile(_SOURCE, str(_V2_PATH), "exec"), globals())

_BASE_RENDER_RISK_SIZING = render_risk_sizing


def _render_compact_terminal_css() -> None:
    """Reduce dead space while preserving the existing text and typography."""
    st.markdown(
        """
        <style>
        /* Metric/quote cards: keep the same text, remove the empty vertical box area. */
        .rs-card {
            min-height:0 !important;
            height:auto !important;
            padding:.24rem .48rem !important;
            margin:0 !important;
            box-sizing:border-box !important;
        }
        .rs-card-head {
            gap:.28rem !important;
            min-height:15px !important;
            margin:0 !important;
        }
        .rs-card-label {
            font-size:.70rem !important;
            line-height:1.05 !important;
            margin:0 !important;
        }
        .rs-card-value {
            font-size:1.18rem !important;
            margin:.04rem 0 0 0 !important;
            line-height:1.08 !important;
        }
        .rs-card-detail {
            font-size:.64rem !important;
            margin:.04rem 0 0 0 !important;
            line-height:1.05 !important;
        }
        .rs-help {
            width:15px !important;
            height:15px !important;
            flex:0 0 15px !important;
            font-size:10px !important;
        }
        .rs-help-tip {
            top:19px !important;
            width:300px !important;
        }

        /* Streamlit wraps each HTML card in extra blocks; collapse those wrappers too. */
        [data-testid="stMarkdownContainer"]:has(.rs-card),
        [data-testid="stMarkdownContainer"]:has(.rs-card) > div,
        [data-testid="stMarkdownContainer"]:has(.rs-card) p {
            margin-top:0 !important;
            margin-bottom:0 !important;
            padding-top:0 !important;
            padding-bottom:0 !important;
        }
        [data-testid="stHorizontalBlock"]:has(.rs-card) {
            gap:.65rem !important;
            margin-top:.12rem !important;
            margin-bottom:.12rem !important;
        }

        /* Inputs stay readable but use less height. */
        [data-testid="stNumberInput"] input,
        [data-testid="stTextInput"] input {
            min-height:34px !important;
            height:34px !important;
            padding-top:.2rem !important;
            padding-bottom:.2rem !important;
        }
        [data-testid="stNumberInput"] button {
            min-height:34px !important;
            height:34px !important;
        }
        [data-testid="stNumberInput"],
        [data-testid="stTextInput"],
        [data-testid="stSelectbox"] {
            margin-bottom:.10rem !important;
        }

        /* Dropdowns stay visually distinct from normal fields. */
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            background:#0068ff !important;
            border-color:#fb8b1e !important;
            min-height:36px !important;
            height:36px !important;
        }
        [data-testid="stSelectbox"] div[data-baseweb="select"] span,
        [data-testid="stSelectbox"] div[data-baseweb="select"] svg {
            color:#ffffff !important;
            -webkit-text-fill-color:#ffffff !important;
        }
        div[role="listbox"] {
            background:#000000 !important;
            border:1px solid #0068ff !important;
        }
        div[role="option"] {
            background:#000000 !important;
            color:#fb8b1e !important;
            min-height:32px !important;
        }
        div[role="option"] * { color:#fb8b1e !important; }
        div[role="option"]:hover,
        div[role="option"][aria-selected="true"] {
            background:#0068ff !important;
            color:#ffffff !important;
        }
        div[role="option"]:hover *,
        div[role="option"][aria-selected="true"] * {
            color:#ffffff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _section(payload: dict[str, Any], *names: str) -> dict[str, Any]:
    wanted = {str(name).casefold() for name in names}
    for node in _walk_dicts(payload):
        for key, child in node.items():
            if str(key).casefold() in wanted and isinstance(child, dict):
                return child
    return {}


def _number(section: dict[str, Any], key: str) -> float | None:
    if not isinstance(section, dict):
        return None
    wanted = str(key).casefold()
    for actual_key, value in section.items():
        if str(actual_key).casefold() != wanted:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _true_cash_fields(payload: dict[str, Any]) -> dict[str, float | None]:
    """Extract only cash-balance fields from E*TRADE's balance response."""
    computed = _section(payload, "Computed", "ComputedBalance", "computedBalance")
    cash_section = _section(payload, "Cash", "cash")

    fields = {
        "cashBalance": _number(computed, "cashBalance"),
        "netCash": _number(computed, "netCash"),
        "moneyMktBalance": _number(cash_section, "moneyMktBalance"),
        "settledCashForInvestment": _number(computed, "settledCashForInvestment"),
        "unSettledCashForInvestment": _number(computed, "unSettledCashForInvestment"),
    }

    for key in list(fields):
        if fields[key] is None:
            fields[key] = find_number(payload, key)
    return fields


def _select_true_cash(payload: dict[str, Any]) -> tuple[float, str, dict[str, float | None]]:
    """Choose E*TRADE's best actual-cash field without letting a zero block fallback."""
    fields = _true_cash_fields(payload)
    priority = (
        "cashBalance",
        "netCash",
        "moneyMktBalance",
        "settledCashForInvestment",
    )

    for key in priority:
        value = fields.get(key)
        if value is not None and abs(float(value)) >= 0.005:
            return float(value), key, fields

    for key in priority:
        value = fields.get(key)
        if value is not None:
            return float(value), key, fields

    return 0.0, "NO CASH FIELD RETURNED", fields


def _true_cash_snapshot(
    payload: dict[str, Any],
    base_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
) -> tuple[float, float, float]:
    """Return total account value, actual cash balance, and market value."""
    total, _, market_value = base_snapshot(payload)
    cash, source, fields = _select_true_cash(payload)
    st.session_state["_risk_true_cash_source"] = source
    st.session_state["_risk_true_cash_fields"] = fields
    return float(total or 0.0), float(cash), float(market_value or 0.0)


def render_risk_sizing(
    client,
    *,
    account_picker: Callable[[str], dict[str, Any] | None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
    touch_session: Callable[[], None],
) -> None:
    _render_compact_terminal_css()

    def cash_only_snapshot(payload: dict[str, Any]) -> tuple[float, float, float]:
        return _true_cash_snapshot(payload, balance_snapshot)

    _BASE_RENDER_RISK_SIZING(
        client,
        account_picker=account_picker,
        refresh_accounts=refresh_accounts,
        account_balance=account_balance,
        balance_snapshot=cash_only_snapshot,
        touch_session=touch_session,
    )
