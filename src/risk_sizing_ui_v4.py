"""Risk-sizing UI v4: true cash balance beside Tactical Room.

This adapter keeps the full v2 Crown risk-sizing engine, but promotes E*TRADE's
actual cash balance into the main metric row. It intentionally does NOT use
cashAvailableForInvestment, cashBuyingPower, or marginBuyingPower because those
can reflect purchasing power rather than cash actually held in the account.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

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
    _metric_box(
        s6,
        "CASH BALANCE",
        _money(cash_available),
        cash_tone,
        f"{cash_pct:.2f}% OF ACCOUNT",
        help_text=(
            f"E*TRADE TRUE CASH: {_money(cash_available)}. Source priority = Computed.cashBalance, then netCash / moneyMktBalance fallback. "
            "This card intentionally EXCLUDES cashAvailableForInvestment, cashBuyingPower, marginBuyingPower, and day-trading buying power so margin purchasing power is not shown as cash."
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


def _true_cash_snapshot(
    payload: dict[str, Any],
    base_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
) -> tuple[float, float, float]:
    """Return total account value, real cash balance, and market value.

    E*TRADE documents cashBalance as the current cash balance. We deliberately
    avoid all buying-power fields here so a margin account cannot inflate the
    displayed cash figure.
    """
    total, _, market_value = base_snapshot(payload)

    cash = find_number(payload, "cashBalance")
    if cash is None:
        cash = find_number(payload, "netCash")
    if cash is None:
        cash = find_number(payload, "moneyMktBalance")
    if cash is None:
        # settledCashForInvestment is conservative and still excludes margin
        # buying power, so it is a safe last-resort cash-only fallback.
        cash = find_number(payload, "settledCashForInvestment")

    return float(total or 0.0), float(cash or 0.0), float(market_value or 0.0)


def render_risk_sizing(
    client,
    *,
    account_picker: Callable[[str], dict[str, Any] | None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
    touch_session: Callable[[], None],
) -> None:
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
