"""Pure calculation helpers for the OTOCO simulator."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TradeMetrics:
    entry: float
    stop: float
    target: float
    quantity: int
    risk_per_share: float
    reward_per_share: float
    max_loss: float
    max_profit: float
    reward_risk: float
    position_value: float
    target_rr: float
    target_for_target_rr: float
    stop_for_target_rr: float
    reward_gap_per_share: float

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_trade_metrics(
    entry: float,
    stop: float,
    target: float,
    quantity: int,
    target_rr: float = 2.0,
) -> TradeMetrics:
    """Calculate long-share entry plus OCO exit economics.

    Raises ValueError for an invalid long setup. Keeping this logic separate from
    Streamlit makes it testable and prevents a misleading ratio from being shown.
    """
    entry = float(entry)
    stop = float(stop)
    target = float(target)
    target_rr = float(target_rr)
    quantity = int(quantity)

    if entry <= 0:
        raise ValueError("Entry must be greater than zero.")
    if quantity < 1:
        raise ValueError("Quantity must be at least 1 share.")
    if stop >= entry:
        raise ValueError("For a long trade, the stop must be below the entry.")
    if target <= entry:
        raise ValueError("For a long trade, the target must be above the entry.")
    if target_rr <= 0:
        raise ValueError("Target reward:risk must be greater than zero.")

    risk_per_share = entry - stop
    reward_per_share = target - entry
    reward_risk = reward_per_share / risk_per_share

    return TradeMetrics(
        entry=entry,
        stop=stop,
        target=target,
        quantity=quantity,
        risk_per_share=risk_per_share,
        reward_per_share=reward_per_share,
        max_loss=risk_per_share * quantity,
        max_profit=reward_per_share * quantity,
        reward_risk=reward_risk,
        position_value=entry * quantity,
        target_rr=target_rr,
        target_for_target_rr=entry + target_rr * risk_per_share,
        stop_for_target_rr=entry - reward_per_share / target_rr,
        reward_gap_per_share=reward_per_share - target_rr * risk_per_share,
    )


def risk_sized_quantity(
    portfolio_value: float,
    risk_percent: float,
    entry: float,
    stop: float,
) -> int:
    """Whole-share quantity that keeps planned loss within portfolio risk."""
    portfolio_value = float(portfolio_value)
    risk_percent = float(risk_percent)
    risk_per_share = float(entry) - float(stop)
    if portfolio_value <= 0 or risk_percent <= 0 or risk_per_share <= 0:
        return 0
    return max(0, math.floor((portfolio_value * risk_percent / 100.0) / risk_per_share))

