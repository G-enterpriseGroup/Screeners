"""Bull call debit spread optimizer utilities.

Pure calculation helpers used by Raj's Terminal. Pricing is conservative:
the long call is valued at the ask and the short call at the bid.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from src.etrade_client import find_number


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _find_key(value: Any, wanted: str) -> Any:
    wanted = wanted.casefold()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() == wanted:
                return child
        for child in value.values():
            found = _find_key(child, wanted)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, wanted)
            if found is not None:
                return found
    return None


def extract_call_rows(payload: dict[str, Any], expiry: date) -> list[dict[str, Any]]:
    """Normalize one E*TRADE call chain into strike-level rows."""
    pairs = _as_list(_find_key(payload, "OptionPair"))
    rows: list[dict[str, Any]] = []

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        option = pair.get("Call") or pair.get("call")
        if not isinstance(option, dict):
            continue

        strike = find_number(option, "strikePrice", "strike")
        if strike is None:
            continue

        greeks = (
            option.get("OptionGreeks")
            or option.get("optionGreeks")
            or option.get("optionGreek")
            or {}
        )
        greeks = greeks if isinstance(greeks, dict) else {}

        rows.append(
            {
                "expiry": expiry,
                "strike": float(strike),
                "bid": find_number(option, "bid"),
                "ask": find_number(option, "ask"),
                "last": find_number(option, "lastPrice", "lastTrade"),
                "open_interest": find_number(option, "openInterest") or 0.0,
                "volume": find_number(option, "volume") or 0.0,
                "delta": find_number(greeks, "delta"),
                "gamma": find_number(greeks, "gamma"),
                "iv": find_number(greeks, "iv"),
                "osi": str(
                    option.get("osiKey")
                    or option.get("displaySymbol")
                    or option.get("symbol")
                    or ""
                ),
            }
        )

    return sorted(rows, key=lambda row: row["strike"])


def scan_bull_call_spreads(
    chains: dict[date, list[dict[str, Any]]],
    *,
    max_break_even: float,
    min_reward_risk: float,
    budget: float,
    min_open_interest: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return qualifying bull call debit spreads ranked by cheapest total cost.

    One spread = one long call + one short call = 100-share multiplier.
    Natural debit = long ask - short bid.
    """
    diagnostics = {
        "expirations": len(chains),
        "calls_seen": 0,
        "spreads_tested": 0,
        "invalid_quotes": 0,
        "oi_rejected": 0,
        "break_even_rejected": 0,
        "rr_rejected": 0,
        "budget_rejected": 0,
        "qualified": 0,
    }
    candidates: list[dict[str, Any]] = []

    budget = float(budget)
    max_break_even = float(max_break_even)
    min_reward_risk = float(min_reward_risk)
    min_open_interest = max(0, int(min_open_interest))

    for expiry, rows in sorted(chains.items()):
        rows = sorted(rows, key=lambda row: row["strike"])
        diagnostics["calls_seen"] += len(rows)

        for long_index, long_leg in enumerate(rows[:-1]):
            long_strike = float(long_leg["strike"])
            long_ask = long_leg.get("ask")
            if long_ask is None or float(long_ask) <= 0:
                diagnostics["invalid_quotes"] += max(1, len(rows) - long_index - 1)
                continue

            # BE = long strike + debit. Since debit is positive, any long strike
            # already above the requested BE can never qualify.
            if long_strike >= max_break_even:
                continue

            for short_leg in rows[long_index + 1 :]:
                diagnostics["spreads_tested"] += 1

                short_strike = float(short_leg["strike"])
                width = short_strike - long_strike
                short_bid = short_leg.get("bid")
                if width <= 0 or short_bid is None or float(short_bid) < 0:
                    diagnostics["invalid_quotes"] += 1
                    continue

                if (
                    float(long_leg.get("open_interest") or 0) < min_open_interest
                    or float(short_leg.get("open_interest") or 0) < min_open_interest
                ):
                    diagnostics["oi_rejected"] += 1
                    continue

                debit = float(long_ask) - float(short_bid)
                if debit <= 0 or debit >= width:
                    diagnostics["invalid_quotes"] += 1
                    continue

                break_even = long_strike + debit
                if break_even > max_break_even:
                    diagnostics["break_even_rejected"] += 1
                    continue

                max_loss = debit * 100.0
                max_profit = (width - debit) * 100.0
                reward_risk = max_profit / max_loss if max_loss > 0 else 0.0

                if reward_risk < min_reward_risk:
                    diagnostics["rr_rejected"] += 1
                    continue
                if max_loss > budget:
                    diagnostics["budget_rejected"] += 1
                    continue

                long_bid = long_leg.get("bid")
                short_ask = short_leg.get("ask")
                long_mid = (
                    (float(long_bid) + float(long_ask)) / 2.0
                    if long_bid is not None and float(long_bid) >= 0
                    else float(long_ask)
                )
                short_mid = (
                    (float(short_bid) + float(short_ask)) / 2.0
                    if short_ask is not None and float(short_ask) >= 0
                    else float(short_bid)
                )
                midpoint_debit = max(0.0, long_mid - short_mid)

                max_contracts = max(1, int(math.floor(budget / max_loss)))
                deployed_cost = max_loss * max_contracts

                candidates.append(
                    {
                        "Expiration": expiry.isoformat(),
                        "DTE": (expiry - date.today()).days,
                        "Buy Call": long_strike,
                        "Sell Call": short_strike,
                        "Width": width,
                        "Long Ask": float(long_ask),
                        "Short Bid": float(short_bid),
                        "Natural Debit": debit,
                        "Mid Debit": midpoint_debit,
                        "Break-even": break_even,
                        "BE Headroom": max_break_even - break_even,
                        "Max Loss": max_loss,
                        "Max Profit": max_profit,
                        "Reward : Risk": reward_risk,
                        "Total Cost": max_loss,
                        "Max Contracts / Budget": max_contracts,
                        "Budget Deployment": deployed_cost,
                        "Long OI": int(float(long_leg.get("open_interest") or 0)),
                        "Short OI": int(float(short_leg.get("open_interest") or 0)),
                        "Long Delta": long_leg.get("delta"),
                        "Short Delta": short_leg.get("delta"),
                        "Long IV": long_leg.get("iv"),
                        "Short IV": short_leg.get("iv"),
                        "Long OSI": long_leg.get("osi") or "",
                        "Short OSI": short_leg.get("osi") or "",
                    }
                )

    candidates.sort(
        key=lambda row: (
            float(row["Total Cost"]),
            -float(row["Reward : Risk"]),
            float(row["BE Headroom"]),
            int(row["DTE"]),
        )
    )
    diagnostics["qualified"] = len(candidates)
    return candidates, diagnostics
