"""Risk-sizing utilities for Raj's Terminal.

The Crown Macro mechanics implemented here are sourced from the user's
Education guide: tactical sleeve 10-20% of investable assets, 1.00 risk at
1-2% of the tactical sleeve, and position size worked backward from the stop.
The user's portfolio classification rule is separate: bonds/CUSIPs are treated
as long-term, and other positions at or above the chosen gain threshold are
long-term; the rest are tactical.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


CROWN_SIZE_MULTIPLIERS = (1.00, 0.75, 0.50, 0.25)
CROWN_DEFAULT_TACTICAL_SLEEVE_PCT = 15.0
CROWN_DEFAULT_RISK_PCT = 1.5
CROWN_TACTICAL_SLEEVE_RANGE = (10.0, 20.0)
CROWN_RISK_RANGE = (1.0, 2.0)

CROWN_DOLLAR_RISK_TABLE = [
    {"Investable Assets": 50_000, "Tactical Sleeve": 7_500, "1.00": 113, "0.75": 84, "0.50": 56, "0.25": 28},
    {"Investable Assets": 100_000, "Tactical Sleeve": 15_000, "1.00": 225, "0.75": 169, "0.50": 113, "0.25": 56},
    {"Investable Assets": 250_000, "Tactical Sleeve": 37_500, "1.00": 563, "0.75": 422, "0.50": 281, "0.25": 141},
    {"Investable Assets": 500_000, "Tactical Sleeve": 75_000, "1.00": 1_125, "0.75": 844, "0.50": 563, "0.25": 281},
    {"Investable Assets": 1_000_000, "Tactical Sleeve": 150_000, "1.00": 2_250, "0.75": 1_688, "0.50": 1_125, "0.25": 563},
    {"Investable Assets": 2_500_000, "Tactical Sleeve": 375_000, "1.00": 5_625, "0.75": 4_219, "0.50": 2_813, "0.25": 1_406},
    {"Investable Assets": 5_000_000, "Tactical Sleeve": 750_000, "1.00": 11_250, "0.75": 8_438, "0.50": 5_625, "0.25": 2_813},
]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(number) or math.isinf(number):
        return float(default)
    return number


def classify_holdings(frame: pd.DataFrame, gain_threshold_pct: float = 5.0) -> pd.DataFrame:
    """Apply the user's long-term/tactical classification rule to holdings."""
    result = frame.copy()
    threshold = float(gain_threshold_pct)

    if "CUSIP" not in result.columns:
        result["CUSIP"] = ""
    if "Gain/Loss %" not in result.columns:
        result["Gain/Loss %"] = 0.0
    if "Type" not in result.columns:
        result["Type"] = ""
    if "Market Value" not in result.columns:
        result["Market Value"] = 0.0

    result["Gain/Loss %"] = pd.to_numeric(result["Gain/Loss %"], errors="coerce").fillna(0.0)
    result["Market Value"] = pd.to_numeric(result["Market Value"], errors="coerce").fillna(0.0)

    classifications = []
    reasons = []
    for _, row in result.iterrows():
        security_type = str(row.get("Type") or "").strip().upper()
        cusip = str(row.get("CUSIP") or "").strip().upper()
        gain_pct = _number(row.get("Gain/Loss %"), 0.0)

        is_fixed_income = (
            security_type in {"BOND", "BONDS", "MUNI", "MUNICIPAL", "FIXEDINCOME", "FIXED INCOME"}
            or bool(cusip)
        )
        if is_fixed_income:
            classifications.append("LONG-TERM")
            reasons.append("BOND / CUSIP")
        elif gain_pct >= threshold:
            classifications.append("LONG-TERM")
            reasons.append(f"GAIN >= {threshold:.2f}%")
        else:
            classifications.append("TACTICAL")
            reasons.append(f"GAIN < {threshold:.2f}%")

    result["Sleeve"] = classifications
    result["Sleeve Rule"] = reasons
    return result


def sleeve_summary(
    frame: pd.DataFrame,
    investable_assets: float,
    tactical_sleeve_pct: float = CROWN_DEFAULT_TACTICAL_SLEEVE_PCT,
) -> dict[str, float]:
    """Summarize current classified exposure versus the selected tactical sleeve."""
    investable_assets = max(0.0, float(investable_assets))
    tactical_sleeve_pct = max(0.0, float(tactical_sleeve_pct))
    target_tactical_dollars = investable_assets * tactical_sleeve_pct / 100.0

    tactical_value = 0.0
    long_term_value = 0.0
    if not frame.empty and "Sleeve" in frame.columns:
        values = pd.to_numeric(frame.get("Market Value"), errors="coerce").fillna(0.0).abs()
        tactical_mask = frame["Sleeve"].astype(str).eq("TACTICAL")
        long_term_mask = frame["Sleeve"].astype(str).eq("LONG-TERM")
        tactical_value = float(values[tactical_mask].sum())
        long_term_value = float(values[long_term_mask].sum())

    tactical_pct_of_assets = (
        tactical_value / investable_assets * 100.0
        if investable_assets > 0
        else 0.0
    )
    target_room = target_tactical_dollars - tactical_value

    return {
        "investable_assets": investable_assets,
        "target_tactical_dollars": target_tactical_dollars,
        "tactical_value": tactical_value,
        "long_term_value": long_term_value,
        "tactical_pct_of_assets": tactical_pct_of_assets,
        "target_room": target_room,
    }


def crown_risk_budget(
    investable_assets: float,
    tactical_sleeve_pct: float = CROWN_DEFAULT_TACTICAL_SLEEVE_PCT,
    full_position_risk_pct: float = CROWN_DEFAULT_RISK_PCT,
    size_multiplier: float = 1.00,
) -> dict[str, float]:
    """Calculate Crown-style dollar risk budget for the selected size multiplier."""
    investable_assets = max(0.0, float(investable_assets))
    tactical_sleeve_pct = max(0.0, float(tactical_sleeve_pct))
    full_position_risk_pct = max(0.0, float(full_position_risk_pct))
    size_multiplier = max(0.0, float(size_multiplier))

    tactical_sleeve = investable_assets * tactical_sleeve_pct / 100.0
    full_risk_budget = tactical_sleeve * full_position_risk_pct / 100.0
    selected_risk_budget = full_risk_budget * size_multiplier

    return {
        "tactical_sleeve": tactical_sleeve,
        "full_risk_budget": full_risk_budget,
        "selected_risk_budget": selected_risk_budget,
        "size_multiplier": size_multiplier,
    }


def stock_position_size(entry_price: float, stop_price: float, risk_budget: float) -> dict[str, float | int]:
    """Work backward from a stock/ETF stop without exceeding the risk budget."""
    entry = float(entry_price)
    stop = float(stop_price)
    budget = max(0.0, float(risk_budget))
    risk_per_share = abs(entry - stop)

    if entry <= 0:
        raise ValueError("Entry price must be greater than zero.")
    if risk_per_share <= 0:
        raise ValueError("Entry and stop must be different prices.")

    raw_shares = budget / risk_per_share if risk_per_share else 0.0
    shares = max(0, int(math.floor(raw_shares)))
    actual_risk = shares * risk_per_share
    notional = shares * entry

    return {
        "entry": entry,
        "stop": stop,
        "risk_per_share": risk_per_share,
        "raw_shares": raw_shares,
        "shares": shares,
        "actual_risk": actual_risk,
        "notional": notional,
        "unused_risk_budget": max(0.0, budget - actual_risk),
    }


def defined_risk_contracts(max_loss_per_spread: float, risk_budget: float) -> dict[str, float | int]:
    """Size a defined-risk option spread using max loss as the risk."""
    max_loss = float(max_loss_per_spread)
    budget = max(0.0, float(risk_budget))
    if max_loss <= 0:
        raise ValueError("Max loss per spread must be greater than zero.")

    raw_contracts = budget / max_loss
    contracts = max(0, int(math.floor(raw_contracts)))
    actual_risk = contracts * max_loss
    return {
        "max_loss_per_spread": max_loss,
        "raw_contracts": raw_contracts,
        "contracts": contracts,
        "actual_risk": actual_risk,
        "unused_risk_budget": max(0.0, budget - actual_risk),
    }
