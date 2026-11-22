"""Reverse Iron Condor pricing, exhaustive scanning and reviewed-order payloads.

R.I.C. owns this four-leg math independently of the two-leg Vertical Options
feature. All quoted rewards use the TOTAL debit, and the eligibility gate uses
the smaller of the two expiration payoffs. Quote-derived limits are proposals,
not complex-book depth or fill probabilities.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from functools import lru_cache
import math
import re
from typing import Any, Callable, Iterable

import numpy as np

from src.vertical_options import (
    build_place_payload,
    extract_order_id,
    new_vertical_client_order_id,
    preview_details,
    vertical_limit_bounds,
)


RIC_PRICING_POLICY = "ric-four-leg-both-wings-v1"
_MIN_REWARD_RISK = 2.0


def _number(value: Any, *, positive: bool = False, signed: bool = False) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (not signed and (result <= 0 if positive else result < 0)):
        return None
    return result


def _minimum(value: Any) -> float:
    result = _number(value, positive=True)
    if result is None:
        raise ValueError("Minimum reward-to-risk ratio must be positive and finite.")
    return max(_MIN_REWARD_RISK, result)


def _fee(value: Any) -> float:
    result = _number(value)
    if result is None:
        raise ValueError("Fee per spread must be nonnegative and finite.")
    return result


def _breakeven_move(value: Any) -> float | None:
    if value is None:
        return None
    result = _number(value, positive=True)
    if isinstance(value, bool) or result is None:
        raise ValueError("Maximum breakeven move must be positive and finite, or None for unlimited.")
    return result


def _breakeven_band(spot: float, percent: float) -> tuple[Decimal, Decimal]:
    with localcontext() as context:
        context.prec = 50
        center = Decimal(str(spot))
        offset = center * Decimal(str(percent)) / 100
        return center - offset, center + offset


def _breakeven_risk_cost(strike: float, side: str,
                         band: tuple[Decimal, Decimal]) -> float:
    with localcontext() as context:
        context.prec = 50
        value = Decimal(str(strike))
        available = value - band[0] if side == "PUT" else band[1] - value
        cost = float(available * 100)
    if not math.isfinite(cost):
        raise ValueError("Maximum breakeven move produces a nonfinite risk ceiling.")
    return cost


def ric_breakeven_max_cost(
    *, spot_last: float, put_buy_strike: float, buy_strike: float,
    max_breakeven_move_pct: float | None = None,
) -> float | None:
    """Total per-condor risk ceiling keeping BOTH breakevens in the fixed spot band.

    The returned dollars include debit and fees, so callers intersect this with
    any existing cost ceiling. None means unlimited; a nonpositive value means
    the inner strikes leave no room for a positive-debit qualifying condor.
    """
    percent = _breakeven_move(max_breakeven_move_pct)
    if percent is None:
        return None
    values = (spot_last, put_buy_strike, buy_strike)
    numbers = [_number(value, positive=True) for value in values]
    if any(isinstance(value, bool) for value in values) or any(value is None for value in numbers):
        raise ValueError("Fixed scan spot and both long strikes must be positive and finite.")
    spot, put, call = numbers
    band = _breakeven_band(spot, percent)
    return min(_breakeven_risk_cost(put, "PUT", band),
               _breakeven_risk_cost(call, "CALL", band))


def new_ric_client_order_id(prefix: str = "RIC") -> str:
    return new_vertical_client_order_id(prefix)


def validate_ric(
    *, buy_strike: float, sell_strike: float, put_buy_strike: float,
    put_sell_strike: float, quantity: int, spot_last: float | None = None,
) -> None:
    """Require long inner / short outer strikes and equal whole-contract lots."""
    strikes = [_number(value, positive=True) for value in
               (put_sell_strike, put_buy_strike, buy_strike, sell_strike)]
    if any(value is None for value in strikes):
        raise ValueError("All four R.I.C. strikes must be positive and finite.")
    if not all(left < right for left, right in zip(strikes, strikes[1:])):
        raise ValueError("R.I.C. requires SHORT PUT < LONG PUT < LONG CALL < SHORT CALL.")
    qty = _number(quantity, positive=True)
    if isinstance(quantity, bool) or qty is None or not qty.is_integer():
        raise ValueError("Quantity must be a positive whole number of condors.")
    if spot_last is not None:
        spot = _number(spot_last, positive=True)
        if spot is None or not strikes[1] < spot < strikes[2]:
            raise ValueError("R.I.C. long put must be below spot and long call above spot.")


def ric_limit_bounds(
    *, width: float, max_cost: float | None, spread_mid: float | None,
    spread_ask: float | None, pricing_mode: str = "NORMAL",
    min_reward_risk: float = _MIN_REWARD_RISK, fee_per_spread: float = 0.0,
) -> tuple[float, float] | None:
    """Cent-safe four-leg debit band with both-wing and optional fee caps.

    With a fee F dollars, risk is 100D + F and each wing reward is
    100W - 100D - F. Thus D <= W/(1+R) - F/100. A missing fee estimate
    means explicitly before-fee economics; it never implies zero broker fees.
    """
    minimum, fee = _minimum(min_reward_risk), _fee(fee_per_spread)
    valid_width = _number(width, positive=True)
    budget = None if max_cost is None else _number(max_cost, positive=True)
    if valid_width is None or (max_cost is not None and budget is None):
        return None
    # Reuse only the unchanged cent-band policy: converting the fee-adjusted
    # width makes its existing ratio cap algebraically identical to ours.
    adjusted_width = valid_width - (1 + minimum) * fee / 100
    adjusted_budget = None if budget is None else budget - fee
    if adjusted_width <= 0 or (adjusted_budget is not None and adjusted_budget <= 0):
        return None
    return vertical_limit_bounds(
        width=adjusted_width, max_cost=adjusted_budget,
        spread_mid=spread_mid, spread_ask=spread_ask,
        pricing_mode=pricing_mode, min_reward_risk=minimum,
    )


def _quotes(row: dict[str, Any]) -> tuple[float, float] | None:
    if not isinstance(row, dict):
        return None
    bid, ask = _number(row.get("bid")), _number(row.get("ask"), positive=True)
    if bid is None or ask is None or bid > ask:
        return None
    multiplier = row.get("multiplier", row.get("contract_multiplier", 100))
    if _number(multiplier, positive=True) != 100:
        return None
    if row.get("adjusted") or row.get("nonstandard"):
        return None
    return bid, ask


def _matching_quote_identities(rows: tuple[dict[str, Any], ...]) -> bool:
    """Validate all supplied contract evidence, including direct requote calls."""
    expected_sides = ("CALL", "CALL", "PUT", "PUT")
    expiries = [row.get("expiry") for row in rows]
    expiries = [value.date() if isinstance(value, datetime) else value for value in expiries]
    if any(value is not None for value in expiries):
        if not all(isinstance(value, date) for value in expiries) or len(set(expiries)) != 1:
            return False
    for row, side in zip(rows, expected_sides):
        if row.get("call_put") is not None and str(row["call_put"]).upper() != side:
            return False
    identities = [row.get("osi") for row in rows]
    if any(identities):
        if not all(identities) or not isinstance(expiries[0], date):
            return False
        roots = [_contract_root(osi, expiry, side, float(row["strike"]))
                 for osi, expiry, side, row in zip(identities, expiries, expected_sides, rows)]
        if None in roots or len(set(roots)) != 1:
            return False
    underlyings = {str(row[key]).strip().upper() for row in rows
                   for key in ("underlying", "underlying_symbol") if row.get(key)}
    return len(underlyings) <= 1


def _greek(row: dict[str, Any], key: str) -> float | None:
    nested = row.get("greeks") or row.get("OptionGreeks") or {}
    return _number(row.get(key, nested.get(key) if isinstance(nested, dict) else None), signed=True)


@dataclass(frozen=True, slots=True)
class _PreparedLeg:
    strike: float
    bid: float
    ask: float
    last: float | None
    interest: float | None
    volume: float | None
    greeks: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class _PreparedWing:
    buy: _PreparedLeg
    sell: _PreparedLeg
    width: float
    market_valid: bool
    buy_fields: dict[str, Any] | None = None
    sell_fields: dict[str, Any] | None = None
    identity: str = ""


def _prepare_leg(row: dict[str, Any], quote: tuple[float, float]) -> _PreparedLeg:
    """Normalize immutable analytics once; this does not approve a quote identity."""
    return _PreparedLeg(
        float(row["strike"]), quote[0], quote[1],
        _number(row.get("last"), positive=True), _number(row.get("open_interest")),
        _number(row.get("volume")),
        tuple(_greek(row, key) for key in ("delta", "gamma", "theta", "vega")),
    )


def _prepared_wing(buy: _PreparedLeg, sell: _PreparedLeg, width: float,
                   buy_fields=None, sell_fields=None, identity="") -> _PreparedWing:
    return _PreparedWing(
        buy, sell, round(width, 8),
        buy.ask - sell.bid > 0 and buy.bid + buy.ask > sell.bid + sell.ask,
        buy_fields, sell_fields, identity,
    )


def price_ric_quote(
    *, buy_call: dict[str, Any], sell_call: dict[str, Any],
    buy_put: dict[str, Any], sell_put: dict[str, Any],
    max_cost: float | None = None, pricing_mode: str = "NORMAL",
    min_reward_risk: float = _MIN_REWARD_RISK, spot_last: float | None = None,
    fee_per_spread: float = 0.0, desktop_fast_fill: bool = True,
) -> dict[str, Any] | None:
    """Price the JOINT four-leg market; never combine separately chosen debits."""
    del desktop_fast_fill  # R.I.C. always uses the current strict cent policy.
    minimum, fee = _minimum(min_reward_risk), _fee(fee_per_spread)
    if pricing_mode not in {"NORMAL", "SMART FILL"}:
        raise ValueError("Invalid R.I.C. pricing mode.")
    try:
        validate_ric(
            buy_strike=buy_call["strike"], sell_strike=sell_call["strike"],
            put_buy_strike=buy_put["strike"], put_sell_strike=sell_put["strike"],
            quantity=1, spot_last=spot_last,
        )
    except (KeyError, TypeError, ValueError):
        return None
    rows = (buy_call, sell_call, buy_put, sell_put)
    quotes = [_quotes(row) for row in rows]
    if any(quote is None for quote in quotes) or not _matching_quote_identities(rows):
        return None
    cb, cs, pb, ps = quotes
    if any(buy[1] - sell[0] <= 0 or buy[0] + buy[1] <= sell[0] + sell[1]
           for buy, sell in ((cb, cs), (pb, ps))):
        return None
    return _price_prepared_ric(
        _prepared_wing(_prepare_leg(buy_call, cb), _prepare_leg(sell_call, cs),
                       float(sell_call["strike"]) - float(buy_call["strike"])),
        _prepared_wing(_prepare_leg(buy_put, pb), _prepare_leg(sell_put, ps),
                       float(buy_put["strike"]) - float(sell_put["strike"])),
        minimum=minimum, fee=fee, max_cost=max_cost, pricing_mode=pricing_mode,
    )


def _price_prepared_ric(call: _PreparedWing, put: _PreparedWing, *, minimum: float,
                        fee: float, max_cost: float | None, pricing_mode: str) -> dict[str, Any] | None:
    """Shared economics for validated direct quotes and scan-local prepared legs.

    Preserve the public pricer's operation order, including cancellation before
    eight-decimal quote rounding. Prepared records never survive the scan that
    validated their contracts and never bypass direct broker requote checks.
    """
    if not call.market_valid or not put.market_valid:
        return None
    cb, cs, pb, ps = call.buy, call.sell, put.buy, put.sell
    bid = round(cb.bid - cs.ask + pb.bid - ps.ask, 8)
    ask = round(cb.ask - cs.bid + pb.ask - ps.bid, 8)
    midpoint = round((bid + ask) / 2, 8)
    call_width, put_width = call.width, put.width
    width = min(call_width, put_width)
    bounds = ric_limit_bounds(
        width=width, max_cost=max_cost, spread_mid=midpoint, spread_ask=ask,
        pricing_mode=pricing_mode, min_reward_risk=minimum, fee_per_spread=fee,
    )
    if bounds is None:
        return None
    lower, upper = bounds
    preferred = math.ceil((midpoint + .35 * (ask - midpoint)) * 100 - 1e-8) / 100
    debit = min(upper, max(lower, preferred))
    cost = round(100 * debit + fee, 8)
    downside = round(100 * (put_width - debit) - fee, 8)
    upside = round(100 * (call_width - debit) - fee, 8)
    if (not all(math.isfinite(value) for value in (cost, downside, upside))
            or min(downside, upside) + 1e-8 < minimum * cost):
        return None
    basis = ("ASK" if abs(debit - ask) < 1e-8 else "SMART") if pricing_mode == "SMART FILL" else "LIMIT"
    rows = (cb, cs, pb, ps)
    interests = [row.interest for row in rows]
    volumes = [row.volume for row in rows]
    liquidity = "UNVERIFIED"
    if all(value is not None for value in interests + volumes):
        liquidity = ("ACTIVE LEGS" if min(interests) >= 100 and min(volumes) >= 20
                     else "THIN LEGS" if min(interests) < 20 or min(volumes) < 5
                     else "MODERATE LEGS")
    lasts = [row.last for row in rows]
    last_debit = (round(lasts[0] - lasts[1] + lasts[2] - lasts[3], 8)
                  if all(value is not None for value in lasts) else None)
    result = {
        "debit": debit, "cost": cost, "gross_cost": round(100 * debit, 2),
        "fee_per_spread": fee, "basis": basis, "grade": 0, "orderable": True,
        "spread_bid": bid, "spread_mid": midpoint, "spread_ask": ask,
        "last_debit": last_debit, "liquidity_signal": liquidity,
        "call_width": call_width, "put_width": put_width, "width": width,
        "max_loss": cost, "max_profit": min(downside, upside),
        "downside_profit": downside, "upside_profit": upside,
        "reward_risk": min(downside, upside) / cost,
        "lower_breakeven": round(pb.strike - debit - fee / 100, 8),
        "upper_breakeven": round(cb.strike + debit + fee / 100, 8),
        "min_open_interest": min(interests) if all(v is not None for v in interests) else None,
        "min_volume": min(volumes) if all(v is not None for v in volumes) else None,
    }
    result["breakeven"] = result["upper_breakeven"]
    for index, key in enumerate(("delta", "gamma", "theta", "vega")):
        values = [row.greeks[index] for row in rows]
        result["net_" + key] = (_number(values[0] - values[1] + values[2] - values[3], signed=True)
                                if all(value is not None for value in values) else None)
    return result


def _contract_root(osi: str, expiry: date, side: str, strike: float) -> str | None:
    """Reject malformed, adjusted and mismatched OCC identities."""
    return _parsed_contract_root(str(osi or ""), expiry, side, strike)


@lru_cache(maxsize=8192)
def _parsed_contract_root(osi: str, expiry: date, side: str, strike: float) -> str | None:
    # Many condors share each contract. Cache only immutable identity parsing,
    # never prices, account state or a prior approval of a quoted market.
    match = re.fullmatch(
        r"([A-Z][A-Z0-9.]*?)-{0,5}(\d{6})([CP])(\d{1,8}(?:\.\d+)?)",
        str(osi or "").upper().replace(" ", "").lstrip("."),
    )
    if not match or match[2] != expiry.strftime("%y%m%d") or match[3] != side[0]:
        return None
    text = match[4]
    value = float(text) / 1000 if text.isdigit() and len(text) == 8 else float(text)
    root = match[1]
    # Adjusted option classes normally add a numeric suffix. Their deliverable
    # cannot be priced with this standard 100-share payoff model.
    if any(char.isdigit() for char in root) or abs(value - strike) >= .0001:
        return None
    return root


def _wing_pairs(rows: list[dict[str, Any]], side: str, *,
                breakeven_band: tuple[Decimal, Decimal] | None = None,
                checkpoint: Callable[[], None] | None = None) -> list[dict[str, Any]]:
    prefixes = ("buy", "sell") if side == "CALL" else ("put_buy", "put_sell")
    fields = ("strike", "osi", "last", "bid", "ask", "open_interest", "volume")
    # These records and two small export maps are per contract, not per wing
    # or four-leg result. Only this scan's normalized, validated rows enter.
    legs = [
        (row, _prepare_leg(row, (row["bid"], row["ask"])),
         {prefixes[0] + "_" + field: row.get(field) for field in fields},
         {prefixes[1] + "_" + field: row.get(field) for field in fields},
         f'{row["strike"]:.6f}',
         _breakeven_risk_cost(row["strike"], side, breakeven_band)
         if breakeven_band is not None else None)
        for row in rows
    ]
    pairs = []
    for index, lower_leg in enumerate(legs):
        if checkpoint is not None:
            checkpoint()
        lower = lower_leg[0]
        for higher_index, higher_leg in enumerate(legs[index + 1:]):
            if checkpoint is not None and higher_index % 1024 == 0:
                checkpoint()
            higher = higher_leg[0]
            if lower["strike"] == higher["strike"]:
                continue
            buy_leg, sell_leg = (lower_leg, higher_leg) if side == "CALL" else (higher_leg, lower_leg)
            if buy_leg[5] is not None and buy_leg[5] <= 0:
                continue  # This long strike already lies on/outside its breakeven boundary.
            buy, sell = buy_leg[0], sell_leg[0]
            bid = buy["bid"] - sell["ask"]
            ask = buy["ask"] - sell["bid"]
            midpoint = (bid + ask) / 2
            if ask <= 0 or midpoint <= 0:
                continue
            width = higher["strike"] - lower["strike"]
            pair = {"buy": buy, "sell": sell, "mid": midpoint, "ask": ask,
                    "width": width,
                    "prepared": _prepared_wing(
                        buy_leg[1], sell_leg[1], width, buy_leg[2], sell_leg[3],
                        lower_leg[4] + "|" + higher_leg[4])}
            if buy_leg[5] is not None:
                pair["breakeven_max_cost"] = buy_leg[5]
            pairs.append(pair)
    return pairs


def _wing_debit_cap(pair: dict[str, Any], minimum: float, fee: float) -> float:
    cap = pair["width"] / (1 + minimum) - fee / 100
    breakeven_cost = pair.get("breakeven_max_cost")
    return cap if breakeven_cost is None else min(cap, breakeven_cost / 100 - fee / 100)


def _pair_tree(pairs: list[dict[str, Any]], minimum: float, fee: float) -> dict[str, Any]:
    """Bound join candidates using midpoint, near-ASK and both-wing headroom.

    Complete eligible output may itself grow quartically. This index avoids a
    blind four-strike Cartesian loop when cost/risk bands rule out whole groups.
    It never drops a wing or limits retained qualifying results.
    """
    node = {
        "mid": min(pair["mid"] for pair in pairs),
        "ask": min(.97 * pair["ask"] for pair in pairs),
        "headroom": max(_wing_debit_cap(pair, minimum, fee) - pair["mid"] for pair in pairs),
        "smart_headroom": max(_wing_debit_cap(pair, minimum, fee) - .97 * pair["ask"] for pair in pairs),
    }
    if len(pairs) <= 32:
        node["pairs"] = pairs
    else:
        by_mid = sorted(pairs, key=lambda pair: pair["mid"])
        middle = len(by_mid) // 2
        node["children"] = (_pair_tree(by_mid[:middle], minimum, fee),
                            _pair_tree(by_mid[middle:], minimum, fee))
    return node


def _compatible_puts(node: dict[str, Any], call: dict[str, Any], *, minimum: float,
                     fee: float, max_cost: float | None, smart: bool):
    cap = min(_wing_debit_cap(call, minimum, fee),
              max_cost / 100 - fee / 100 if max_cost is not None else math.inf)
    if (call["mid"] + node["mid"] > cap + 1e-8
            or node["headroom"] + 1e-8 < call["mid"]):
        return
    if smart and (.97 * call["ask"] + node["ask"] > cap + 1e-8
                  or node["smart_headroom"] + 1e-8 < .97 * call["ask"]):
        return
    for pair in node.get("pairs", ()):
        joint_mid = call["mid"] + pair["mid"]
        joint_cap = min(cap, _wing_debit_cap(pair, minimum, fee))
        if joint_mid <= joint_cap + 1e-8 and (not smart or .97 * (call["ask"] + pair["ask"]) <= joint_cap + 1e-8):
            yield pair
    for child in node.get("children", ()):
        yield from _compatible_puts(child, call, minimum=minimum, fee=fee,
                                    max_cost=max_cost, smart=smart)


def _ric_normalized_groups(option_rows, ticker, spot, checkpoint=None):
    groups: dict[tuple[date, str], dict[tuple[str, float], dict[str, Any] | None]] = {}
    input_contracts = 0
    for raw in option_rows:
        input_contracts += 1
        if checkpoint is not None and input_contracts % 64 == 0:
            checkpoint()
        if not isinstance(raw, dict):
            continue
        expiry = raw.get("expiry")
        if isinstance(expiry, datetime):
            expiry = expiry.date()
        side, strike = str(raw.get("call_put") or "").upper(), _number(raw.get("strike"), positive=True)
        quotes = _quotes(raw)
        if not isinstance(expiry, date) or side not in {"CALL", "PUT"} or strike is None or quotes is None:
            continue
        if (side == "CALL" and strike <= spot) or (side == "PUT" and strike >= spot):
            continue
        root = _contract_root(raw.get("osi", ""), expiry, side, strike)
        if root is None:
            continue
        if any(str(raw[key]).strip().upper() != ticker for key in
               ("underlying", "underlying_symbol") if raw.get(key)):
            continue
        row = {**raw, "strike": strike, "bid": quotes[0], "ask": quotes[1],
               "last": _number(raw.get("last"), positive=True),
               "open_interest": _number(raw.get("open_interest")),
               "volume": _number(raw.get("volume")),
               "osi": str(raw["osi"]).strip(), "expiry": expiry, "call_put": side}
        group, key = groups.setdefault((expiry, root), {}), (side, strike)
        if key not in group:
            group[key] = row
        elif group[key] is not None and (group[key]["bid"], group[key]["ask"], group[key]["osi"]) != (row["bid"], row["ask"], row["osi"]):
            group[key] = None  # Conflicting exact-contract quotes cannot approve a trade.
    return groups, input_contracts


def scan_ric_candidates(
    *, symbol: str, spot_last: float, option_rows: Iterable[dict[str, Any]],
    target_breakeven: float | None, max_cost: float | None,
    max_per_expiration: int | None = 20, pricing_mode: str = "NORMAL",
    min_reward_risk: float = _MIN_REWARD_RISK,
    candidate_sink: Callable[[dict[str, Any]], None] | None = None,
    progress: Callable[[int], None] | None = None,
    desktop_fast_fill: bool = True, fee_per_spread: float = 0.0,
    scan_stats: dict[str, int] | None = None,
    max_breakeven_move_pct: float | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Exhaustively join OTM call/put debit wings of each standard option class."""
    ticker, spot = str(symbol or "").strip().upper(), _number(spot_last, positive=True)
    target = None if target_breakeven is None else _number(target_breakeven, positive=True)
    budget = None if max_cost is None else _number(max_cost, positive=True)
    minimum, fee = _minimum(min_reward_risk), _fee(fee_per_spread)
    if (not ticker or spot is None or (target_breakeven is not None and target is None)
            or (max_cost is not None and budget is None)):
        raise ValueError("Ticker, spot, optional target and cost must be valid positive values.")
    if pricing_mode not in {"NORMAL", "SMART FILL"}:
        raise ValueError("Invalid R.I.C. pricing mode.")
    if max_per_expiration is not None and (type(max_per_expiration) is not int or max_per_expiration < 1):
        raise ValueError("max_per_expiration must be a positive integer or None.")
    if candidate_sink is not None and max_per_expiration is not None:
        raise ValueError("Streaming requires exhaustive scanning without a retention limit.")
    breakeven_percent = _breakeven_move(max_breakeven_move_pct)
    breakeven_band = (_breakeven_band(spot, breakeven_percent)
                      if breakeven_percent is not None else None)
    groups, input_contracts = _ric_normalized_groups(option_rows, ticker, spot)

    retained: dict[date, list[tuple[tuple, dict[str, Any]]]] = {}
    annual: dict[int, tuple[tuple, str]] = {}
    checked = qualified = possible = market_combinations = call_wings = put_wings = 0
    for (expiry, root), group in sorted(groups.items()):
        calls = sorted((row for row in group.values() if row and row["call_put"] == "CALL"), key=lambda row: row["strike"])
        puts = sorted((row for row in group.values() if row and row["call_put"] == "PUT"), key=lambda row: row["strike"])
        call_pairs = _wing_pairs(calls, "CALL", breakeven_band=breakeven_band)
        put_pairs = _wing_pairs(puts, "PUT", breakeven_band=breakeven_band)
        possible += len(calls) * (len(calls) - 1) // 2 * (len(puts) * (len(puts) - 1) // 2)
        call_wings += len(call_pairs)
        put_wings += len(put_pairs)
        market_combinations += len(call_pairs) * len(put_pairs)
        if not call_pairs or not put_pairs:
            continue
        tree = _pair_tree(put_pairs, minimum, fee)
        top = retained.setdefault(expiry, [])
        identity_prefix = "|".join((ticker, expiry.isoformat(), "RIC", root)) + "|"
        for call in call_pairs:
            prepared_call = call["prepared"]
            for put in _compatible_puts(tree, call, minimum=minimum, fee=fee,
                                        max_cost=budget, smart=pricing_mode == "SMART FILL"):
                checked += 1
                if progress is not None and checked % 4096 == 0:
                    progress(checked)
                prepared_put = put["prepared"]
                cost_cap = budget
                if breakeven_band is not None:
                    cost_cap = min(call["breakeven_max_cost"], put["breakeven_max_cost"],
                                   budget if budget is not None else math.inf)
                price = _price_prepared_ric(
                    prepared_call, prepared_put, max_cost=cost_cap,
                    pricing_mode=pricing_mode, minimum=minimum, fee=fee,
                )
                if price is None:
                    continue
                qualified += 1
                candidate = {"symbol": ticker, "spot_last": spot, "expiry": expiry,
                             "year": expiry.year, "call_put": "RIC", "option_root": root,
                             "pricing_mode": pricing_mode, **price}
                candidate.update(prepared_call.buy_fields)
                candidate.update(prepared_call.sell_fields)
                candidate.update(prepared_put.buy_fields)
                candidate.update(prepared_put.sell_fields)
                identity = identity_prefix + prepared_put.identity + "|" + prepared_call.identity
                candidate["id"] = identity
                distance = min(abs(price["upper_breakeven"] - target), abs(price["lower_breakeven"] - target)) if target is not None else 0
                rank = (price["grade"], distance, price["cost"], -price["reward_risk"], expiry,
                        candidate["buy_strike"], candidate["sell_strike"],
                        candidate["put_buy_strike"], candidate["put_sell_strike"], root)
                if expiry.year not in annual or rank < annual[expiry.year][0]:
                    annual[expiry.year] = (rank, identity)
                if candidate_sink is not None:
                    candidate_sink(candidate)
                elif max_per_expiration is None:
                    top.append((rank, candidate))
                elif len(top) < max_per_expiration or rank < top[-1][0]:
                    top.insert(bisect_left(top, rank, key=lambda item: item[0]), (rank, candidate))
                    if len(top) > max_per_expiration:
                        top.pop()
    if progress is not None:
        progress(checked)
    if scan_stats is not None:
        scan_stats.update(input_contracts=input_contracts,
                          valid_contracts=sum(row is not None for group in groups.values() for row in group.values()),
                          option_classes=len(groups), call_wings=call_wings, put_wings=put_wings,
                          possible_condors=possible, market_combinations=market_combinations,
                          priced_condors=checked, qualified_condors=qualified,
                          pruned_combinations=market_combinations - checked)
    ranked = sorted((item for rows in retained.values() for item in rows), key=lambda item: item[0])
    return [candidate for _, candidate in ranked], {identity for _, identity in annual.values()}


def build_ric_preview_payload(
    *, symbol: str, expiry: date, buy_strike: float, sell_strike: float,
    put_buy_strike: float, put_sell_strike: float, quantity: int,
    limit_price: float, order_term: str = "GOOD_FOR_DAY",
    client_order_id: str | None = None, min_reward_risk: float = _MIN_REWARD_RISK,
    fee_per_spread: float = 0.0, buy_osi: str = "", sell_osi: str = "",
    put_buy_osi: str = "", put_sell_osi: str = "",
) -> dict[str, Any]:
    """Build one atomic four-leg NET_DEBIT request for explicit broker preview."""
    ticker = str(symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]*", ticker) or not isinstance(expiry, date):
        raise ValueError("A valid ticker and expiration are required.")
    validate_ric(buy_strike=buy_strike, sell_strike=sell_strike,
                 put_buy_strike=put_buy_strike, put_sell_strike=put_sell_strike,
                 quantity=quantity)
    minimum, fee = _minimum(min_reward_risk), _fee(fee_per_spread)
    debit = _number(limit_price, positive=True)
    if debit is None or abs(debit * 100 - round(debit * 100)) > 1e-6:
        raise ValueError("R.I.C. debit limit must be a positive whole-cent amount.")
    debit = round(debit, 2)
    width = min(float(sell_strike) - float(buy_strike), float(put_buy_strike) - float(put_sell_strike))
    risk = 100 * debit + fee
    if 100 * width - risk + 1e-8 < minimum * risk:
        raise ValueError(f"Both R.I.C. wings must keep risk:reward at 1:{minimum:g} or better.")
    term = str(order_term or "").strip().upper()
    if term not in {"GOOD_FOR_DAY", "GOOD_UNTIL_CANCEL"}:
        raise ValueError("Duration must be DAY or GTC.")
    client_id = str(client_order_id or new_ric_client_order_id()).strip()
    if not client_id.isalnum() or len(client_id) > 20:
        raise ValueError("Client order id must be <=20 alphanumeric characters.")
    legs = (("PUT", "SELL_OPEN", put_sell_strike, put_sell_osi),
            ("PUT", "BUY_OPEN", put_buy_strike, put_buy_osi),
            ("CALL", "BUY_OPEN", buy_strike, buy_osi),
            ("CALL", "SELL_OPEN", sell_strike, sell_osi))
    roots = []
    for side, _, strike, osi in legs:
        root = _contract_root(osi, expiry, side, float(strike)) if osi else ticker
        if root is None:
            raise ValueError("R.I.C. contract identity does not match its displayed leg.")
        roots.append(root)
    if len(set(roots)) != 1:
        raise ValueError("All four R.I.C. legs must use the same standard option root.")
    instruments = [{
        "Product": {"symbol": roots[0], "securityType": "OPTN", "callPut": side,
                    "expiryYear": expiry.year, "expiryMonth": expiry.month,
                    "expiryDay": expiry.day, "strikePrice": float(strike)},
        "orderAction": action, "orderedQuantity": int(quantity), "quantity": int(quantity),
    } for side, action, strike, _ in legs]
    return {"PreviewOrderRequest": {
        "orderType": "SPREADS", "clientOrderId": client_id,
        "Order": [{"allOrNone": False, "priceType": "NET_DEBIT", "limitPrice": debit,
                   "stopPrice": 0.0, "orderTerm": term, "marketSession": "REGULAR",
                   "Instrument": instruments}],
    }}


# Exact opt-in typed retention; public scalar quote/review math remains above.
class RICBatchUnsupported(ValueError):
    """Request atomic legacy fallback, never omission of an unsupported row."""


@dataclass(frozen=True)
class RICBatchManifest:
    symbol: str
    spot: float
    expiry: date
    root: str
    mode: str
    minimum: float
    fee: float
    target: float | None
    budget: float | None
    max_breakeven_move_pct: float | None
    call_pairs: tuple
    put_pairs: tuple
    call_values: dict
    put_values: dict
    grid: dict | None = None


@dataclass(frozen=True)
class RICBatch:
    manifest: RICBatchManifest
    call_wing: np.ndarray
    put_wing: np.ndarray
    debit_cents: np.ndarray
    scalars: dict

    @property
    def count(self):
        return len(self.debit_cents)


def _ric_round8(values):
    """Python-_ric_round8-equivalent vector division, scalar on ambiguous products.

    For a normal scaled product below 2**50, correctly rounded multiplication
    can change integer rounding only next to a half-integer. Two product ulps
    conservatively isolate that ambiguity. Extreme/nonfinite values are scalar.
    """
    values = np.asarray(values, dtype=np.float64)
    with np.errstate(over='ignore', invalid='ignore'):
        scaled = values * 100000000.0
        result = np.rint(scaled) / 100000000.0
        fraction = scaled - np.floor(scaled)
        hazard = (~np.isfinite(scaled) | (np.abs(scaled) >= 2**50)
                  | (np.abs(fraction - .5) <= 2*np.spacing(np.abs(scaled))))
    for index in np.flatnonzero(hazard):
        result[index] = round(float(values[index]), 8)
    return result


def _ric_pair_values(pairs, minimum, fee):
    def array(fn, dtype=np.float64):
        return np.asarray([fn(pair) for pair in pairs], dtype=dtype)
    return {
        'mid': array(lambda p:p['mid']), 'ask':array(lambda p:p['ask']),
        'cap': array(lambda p:_wing_debit_cap(p,minimum,fee)),
        'width': array(lambda p:p['prepared'].width),
        'market_valid':array(lambda p:p['prepared'].market_valid,np.bool_),
        'buy_bid':array(lambda p:p['prepared'].buy.bid),
        'buy_ask':array(lambda p:p['prepared'].buy.ask),
        'sell_bid':array(lambda p:p['prepared'].sell.bid),
        'sell_ask':array(lambda p:p['prepared'].sell.ask),
        'buy_strike':array(lambda p:p['prepared'].buy.strike),
        'sell_strike':array(lambda p:p['prepared'].sell.strike),
        'be_cap':array(lambda p:p.get('breakeven_max_cost',math.inf)),
    }


def ric_batch_values(manifest, call_indices, put_indices, debit_cents, fields=None, _quotes=None, _core=None):
    """Exact canonical numeric fields, no dictionaries per candidate."""
    c,p=manifest.call_values,manifest.put_values
    ci,pi=np.asarray(call_indices),np.asarray(put_indices)
    all_fields={'debit','cost','gross_cost','max_loss','downside_profit','upside_profit','max_profit',
                'reward_risk','lower_breakeven','upper_breakeven','breakeven','width','call_width',
                'put_width','spread_bid','spread_ask','spread_mid','basis_code'}
    wanted=all_fields if fields is None else set(fields)
    if wanted-all_fields:raise ValueError('Unknown descriptor scalar field')
    if manifest.grid is not None:
        provided=dict(_core or {})
        if _quotes is not None:provided.update(zip(('spread_bid','spread_ask','spread_mid'),_quotes))
        return _ric_grid_values(manifest,ci,pi,debit_cents,wanted,provided)
    cache=dict(_core or {})
    if _quotes is not None:cache.update(zip(('spread_bid','spread_ask','spread_mid'),_quotes))
    def value(key):
        if key in cache:return cache[key]
        if key=='debit':answer=np.asarray(debit_cents,dtype=np.float64)/100
        elif key=='gross_cost':answer=np.asarray(debit_cents,dtype=np.float64)
        elif key=='call_width':answer=c['width'][ci]
        elif key=='put_width':answer=p['width'][pi]
        elif key=='width':answer=np.minimum(value('call_width'),value('put_width'))
        elif key=='cost':answer=_ric_round8(100*value('debit')+manifest.fee)
        elif key=='max_loss':answer=value('cost')
        elif key=='downside_profit':answer=_ric_round8(100*(value('put_width')-value('debit'))-manifest.fee)
        elif key=='upside_profit':answer=_ric_round8(100*(value('call_width')-value('debit'))-manifest.fee)
        elif key=='max_profit':answer=np.minimum(value('downside_profit'),value('upside_profit'))
        elif key=='reward_risk':answer=value('max_profit')/value('cost')
        elif key=='lower_breakeven':answer=_ric_round8((p['buy_strike'][pi]-value('debit'))-manifest.fee/100)
        elif key in {'upper_breakeven','breakeven'}:answer=_ric_round8((c['buy_strike'][ci]+value('debit'))+manifest.fee/100)
        elif key=='spread_bid':answer=_ric_round8(((c['buy_bid'][ci]-c['sell_ask'][ci])+p['buy_bid'][pi])-p['sell_ask'][pi])
        elif key=='spread_ask':answer=_ric_round8(((c['buy_ask'][ci]-c['sell_bid'][ci])+p['buy_ask'][pi])-p['sell_bid'][pi])
        elif key=='spread_mid':answer=_ric_round8((value('spread_bid')+value('spread_ask'))/2)
        elif key=='basis_code':answer=(np.where(np.abs(value('debit')-value('spread_ask'))<1e-8,2,1).astype(np.uint8)
                                      if manifest.mode=='SMART FILL' else np.zeros(len(ci),dtype=np.uint8))
        cache[key]=answer
        return answer
    try:
        return {key:value(key) for key in wanted}
    finally:
        del value  # Break recursive closure cycles retaining native arrays.


def _ric_grid_values(manifest,ci,pi,cents,wanted,provided=None):
    grid=manifest.grid;c,p=grid['call'],grid['put'];fee=grid['fee_c']
    n=np.asarray(cents,dtype=np.int64);cache=dict(provided or {})
    def value(key):
        if key in cache:return cache[key]
        if key=='debit':answer=n/100
        elif key=='gross_cost':answer=n.astype(np.float64)
        elif key=='call_width':answer=c['width'][ci]/100
        elif key=='put_width':answer=p['width'][pi]/100
        elif key=='width':answer=np.minimum(c['width'][ci],p['width'][pi])/100
        elif key in {'cost','max_loss'}:answer=(100*n+fee)/100
        elif key=='downside_profit':answer=(100*(p['width'][pi]-n)-fee)/100
        elif key=='upside_profit':answer=(100*(c['width'][ci]-n)-fee)/100
        elif key=='max_profit':answer=np.minimum(value('downside_profit'),value('upside_profit'))
        elif key=='reward_risk':answer=value('max_profit')/value('cost')
        elif key=='lower_breakeven':answer=(100*p['buy_strike'][pi]-100*n-fee)/10000
        elif key in {'upper_breakeven','breakeven'}:answer=(100*c['buy_strike'][ci]+100*n+fee)/10000
        elif key=='spread_bid':answer=_ric_grid_bid(manifest,ci,pi,c['buy_bid'][ci]-c['sell_ask'][ci]+p['buy_bid'][pi]-p['sell_ask'][pi])
        elif key=='spread_ask':answer=(c['buy_ask'][ci]-c['sell_bid'][ci]+p['buy_ask'][pi]-p['sell_bid'][pi])/100
        elif key=='spread_mid':answer=((c['buy_bid'][ci]-c['sell_ask'][ci]+p['buy_bid'][pi]-p['sell_ask'][pi])+
                                       (c['buy_ask'][ci]-c['sell_bid'][ci]+p['buy_ask'][pi]-p['sell_bid'][pi]))/200
        elif key=='basis_code':answer=(np.where(value('debit')==value('spread_ask'),2,1).astype(np.uint8)
                                      if manifest.mode=='SMART FILL' else np.zeros(len(n),dtype=np.uint8))
        cache[key]=answer;return answer
    try:
        return {key:value(key) for key in wanted}
    finally:
        del value  # Reclaim native arrays immediately on success or failure.


def _ric_grid_bid(manifest,ci,pi,integer_bid):
    """Keep the scalar round8 sign when a mathematically zero bid cancels."""
    answer=integer_bid/100
    zero=integer_bid==0
    if np.any(zero):
        c,p=manifest.call_values,manifest.put_values
        cidx,pidx=ci[zero],pi[zero]
        raw=((c['buy_bid'][cidx]-c['sell_ask'][cidx])+p['buy_bid'][pidx])-p['sell_ask'][pidx]
        answer[zero]=np.copysign(0.0,raw)
    return answer


def _ric_cent_grid(c,p,spot,minimum,fee,budget,percent):
    """Conservative proved domain; all other inputs use the exact float lane.

    Bounds keep all normal quote/width operations below _ric_round8's5e-9 half-step
    and all integer-cent ceil/floor residual errors below the1e-8 correction.
    """
    if percent is not None or spot>2048 or not minimum.is_integer() or minimum>16 or fee>100:
        return None
    fee_c=round(fee*100)
    if fee_c/100!=fee:return None
    budget_c=None
    if budget is not None:
        if budget>1000000:return None
        budget_c=round(budget*100)
        if budget_c/100!=budget:return None
    output={}
    for label,values in (('call',c),('put',p)):
        grid={}
        for key in ('buy_bid','buy_ask','sell_bid','sell_ask','buy_strike','sell_strike'):
            numbers=values[key]
            limit=8192 if key.endswith('strike') else 4096
            if np.any(np.abs(numbers)>limit):return None
            integers=np.rint(numbers*100).astype(np.int64)
            if not np.array_equal(integers/100,numbers):return None
            grid[key]=integers
        widths=np.abs(grid['buy_strike']-grid['sell_strike'])
        if not np.array_equal(widths/100,values['width']):return None
        grid['width']=widths;output[label]=grid
    return dict(**output,fee_c=fee_c,budget_c=budget_c,ratio=int(minimum))


def _ric_price_grid(manifest,ci,pi,fields):
    grid=manifest.grid;c,p=grid['call'],grid['put'];fee_c=grid['fee_c'];r=grid['ratio']
    b=c['buy_bid'][ci]-c['sell_ask'][ci]+p['buy_bid'][pi]-p['sell_ask'][pi]
    a=c['buy_ask'][ci]-c['sell_bid'][ci]+p['buy_ask'][pi]-p['sell_bid'][pi]
    width=np.minimum(c['width'][ci],p['width'][pi])
    lower=np.maximum(1,(b+a+1)//2)
    if manifest.mode=='SMART FILL':lower=np.maximum(lower,(97*a+99)//100)
    upper=np.minimum(a,(100*width-(1+r)*fee_c)//(100*(1+r)))
    if grid['budget_c'] is not None:upper=np.minimum(upper,(grid['budget_c']-fee_c)//100)
    cents=np.minimum(upper,np.maximum(lower,(13*b+27*a+39)//40))
    keep=(manifest.call_values['market_valid'][ci]&manifest.put_values['market_valid'][pi]
          &(b+a>=0)&(a>0)&(lower<=upper)&(cents>0))
    ci,pi,n=ci[keep],pi[keep],cents[keep]
    cost_c=100*n+fee_c
    down_c=100*(p['width'][pi]-n)-fee_c
    up_c=100*(c['width'][ci]-n)-fee_c
    good=np.minimum(down_c,up_c)>=r*cost_c
    ci,pi,n=ci[good],pi[good],n[good]
    core={'cost':cost_c[good]/100,'downside_profit':down_c[good]/100,'upside_profit':up_c[good]/100}
    core['max_profit']=np.minimum(core['downside_profit'],core['upside_profit'])
    quote=(_ric_grid_bid(manifest,ci,pi,b[keep][good]),a[keep][good]/100,(b[keep][good]+a[keep][good])/200)
    values=ric_batch_values(manifest,ci,pi,n,fields=fields,_quotes=quote,_core=core)
    return ci,pi,n.astype(np.uint32),values


def _ric_batch_cost_cap(manifest, ci, pi):
    cap=np.full(len(ci),math.inf if manifest.budget is None else manifest.budget)
    if manifest.max_breakeven_move_pct is not None:
        cap=np.minimum(cap,np.minimum(manifest.call_values['be_cap'][ci],manifest.put_values['be_cap'][pi]))
    return cap


def _ric_price_batch(manifest, ci, pi, fields=None):
    if manifest.grid is not None:return _ric_price_grid(manifest,ci,pi,fields)
    c,p=manifest.call_values,manifest.put_values
    bid=_ric_round8(((c['buy_bid'][ci]-c['sell_ask'][ci])+p['buy_bid'][pi])-p['sell_ask'][pi])
    ask=_ric_round8(((c['buy_ask'][ci]-c['sell_bid'][ci])+p['buy_ask'][pi])-p['sell_bid'][pi])
    mid=_ric_round8((bid+ask)/2)
    width=np.minimum(c['width'][ci],p['width'][pi])
    cap=_ric_batch_cost_cap(manifest,ci,pi)
    # Exact arithmetic order of ric_limit_bounds -> vertical_limit_bounds.
    adjusted_width=width-(1+manifest.minimum)*manifest.fee/100
    adjusted_budget=cap-manifest.fee
    lower=np.maximum(.01,mid)
    if manifest.mode=='SMART FILL': lower=np.maximum(lower,.97*ask)
    upper=np.minimum(ask,adjusted_width/(1+manifest.minimum))
    upper=np.minimum(upper,adjusted_budget/100)
    with np.errstate(invalid='ignore',over='ignore'):
        lower_cents=np.ceil(lower*100-1e-8)
        upper_cents=np.floor(upper*100+1e-8)
        preferred=np.ceil((mid+.35*(ask-mid))*100-1e-8)
        cents=np.minimum(upper_cents,np.maximum(lower_cents,preferred))
    keep=(c['market_valid'][ci]&p['market_valid'][pi]&np.isfinite(mid)&np.isfinite(ask)
          &(mid>=0)&(ask>0)&(mid<=ask)&(adjusted_width>0)&(adjusted_budget>0)
          &(lower_cents<=upper_cents)&np.isfinite(cents)&(cents>0))
    ci,pi,cents=ci[keep],pi[keep],cents[keep]
    if np.any(cents>=2**64):
        raise RICBatchUnsupported('Cent limit requires arbitrary-precision scalar retention.')
    cents=cents.astype(np.uint32 if not len(cents) or cents.max()<=np.iinfo(np.uint32).max else np.uint64)
    core=ric_batch_values(manifest,ci,pi,cents,fields={'cost','downside_profit','upside_profit'})
    profit=np.minimum(core['downside_profit'],core['upside_profit'])
    good=(np.isfinite(core['cost'])&np.isfinite(core['downside_profit'])&np.isfinite(core['upside_profit'])
          &(profit+1e-8>=manifest.minimum*core['cost']))
    original_keep=np.flatnonzero(keep)[good]
    ci,pi,cents=ci[good],pi[good],cents[good]
    core={key:value[good] for key,value in core.items()};core['max_profit']=profit[good]
    values=ric_batch_values(manifest,ci,pi,cents,fields=fields,
                              _quotes=(bid[original_keep],ask[original_keep],mid[original_keep]),_core=core)
    return ci,pi,cents,values


def ric_batch_candidate(manifest, call_index, put_index, debit_cents):
    call=manifest.call_pairs[int(call_index)]['prepared']
    put=manifest.put_pairs[int(put_index)]['prepared']
    cap=manifest.budget
    if manifest.max_breakeven_move_pct is not None:
        cap=min(manifest.call_pairs[int(call_index)]['breakeven_max_cost'],
                manifest.put_pairs[int(put_index)]['breakeven_max_cost'],
                cap if cap is not None else math.inf)
    price=_price_prepared_ric(call,put,minimum=manifest.minimum,fee=manifest.fee,
                                   max_cost=cap,pricing_mode=manifest.mode)
    if price is None or int(round(price['debit']*100))!=int(debit_cents):
        raise AssertionError('Vector descriptor disagrees with frozen canonical scalar price')
    candidate=dict(symbol=manifest.symbol,spot_last=manifest.spot,expiry=manifest.expiry,
                   year=manifest.expiry.year,call_put='RIC',option_root=manifest.root,
                   pricing_mode=manifest.mode,**price)
    candidate.update(call.buy_fields);candidate.update(call.sell_fields)
    candidate.update(put.buy_fields);candidate.update(put.sell_fields)
    candidate['id']='|'.join((manifest.symbol,manifest.expiry.isoformat(),'RIC',manifest.root,put.identity,call.identity))
    return candidate


def scan_ric_batches(*,symbol,spot_last,option_rows,batch_sink,pricing_mode='NORMAL',
                 min_reward_risk=2,fee_per_spread=0,max_cost=None,target_breakeven=None,
                 max_breakeven_move_pct=None,checkpoint=None,max_chunk=65536,scalar_fields=None,
                 desktop_fast_fill=True,scan_stats=None):
    """Opt-in exact typed joins; preserve every qualifier without row dictionaries."""
    del desktop_fast_fill
    if checkpoint is not None and not callable(checkpoint):
        raise ValueError("Checkpoint must be callable.")
    if type(max_chunk) is not int or not 1 <= max_chunk <= 65536:
        raise ValueError("Typed join chunks must contain 1..65536 combinations.")
    if not callable(batch_sink):
        raise ValueError("Typed scanning requires a batch sink.")
    checkpoint=checkpoint or (lambda:None)
    ticker=str(symbol or '').strip().upper();spot=_number(spot_last,positive=True)
    minimum=_minimum(min_reward_risk);fee=_fee(fee_per_spread)
    target=None if target_breakeven is None else _number(target_breakeven,positive=True)
    budget=None if max_cost is None else _number(max_cost,positive=True)
    percent=_breakeven_move(max_breakeven_move_pct)
    if not ticker or spot is None or (target_breakeven is not None and target is None) or (max_cost is not None and budget is None):
        raise ValueError('Invalid canonical parameters')
    if pricing_mode not in {'NORMAL','SMART FILL'}: raise ValueError('Invalid mode')
    if spot>1000000 or minimum>1000000 or fee>1000000000:
        raise RICBatchUnsupported('Magnitude requires the canonical scalar fallback.')
    band=_breakeven_band(spot,percent) if percent is not None else None
    groups,input_count=_ric_normalized_groups(option_rows,ticker,spot,checkpoint)
    stats=scan_stats if scan_stats is not None else {}
    stats.update(input_contracts=input_count,valid_contracts=sum(row is not None for g in groups.values() for row in g.values()),
               option_classes=len(groups),call_wings=0,put_wings=0,possible_condors=0,market_combinations=0,
               priced_condors=0,qualified_condors=0,pruned_combinations=0,batches=0,grid_classes=0,grid_qualified=0)
    for (expiry,root),group in sorted(groups.items()):
        checkpoint()
        calls=sorted((r for r in group.values() if r and r['call_put']=='CALL'),key=lambda r:r['strike'])
        puts=sorted((r for r in group.values() if r and r['call_put']=='PUT'),key=lambda r:r['strike'])
        if any(row['strike']>1000000000 or max(row['bid'],row['ask'])>1000000 for row in calls+puts):
            raise RICBatchUnsupported('Extreme contract values require the canonical scalar fallback.')
        cp=_wing_pairs(calls,'CALL',breakeven_band=band,checkpoint=checkpoint);checkpoint()
        pp=_wing_pairs(puts,'PUT',breakeven_band=band,checkpoint=checkpoint);checkpoint()
        # This is exactly the leaf traversal order of the canonical midpoint tree.
        if len(pp)>32:pp=sorted(pp,key=lambda p:p['mid'])
        if (len({pair['prepared'].identity for pair in cp})!=len(cp)
                or len({pair['prepared'].identity for pair in pp})!=len(pp)):
            raise RICBatchUnsupported('Formatted strike identities require atomic scalar fallback.')
        stats['possible_condors']+=math.comb(len(calls),2)*math.comb(len(puts),2)
        stats['call_wings']+=len(cp);stats['put_wings']+=len(pp)
        stats['market_combinations']+=len(cp)*len(pp)
        if not cp or not pp:continue
        c,p=_ric_pair_values(cp,minimum,fee),_ric_pair_values(pp,minimum,fee)
        grid=_ric_cent_grid(c,p,spot,minimum,fee,budget,percent)
        stats['grid_classes']+=grid is not None
        manifest=RICBatchManifest(ticker,spot,expiry,root,pricing_mode,minimum,fee,target,budget,percent,tuple(cp),tuple(pp),c,p,grid)
        cbatch=max(1,max_chunk//len(pp))
        for ca in range(0,len(cp),cbatch):
            cz=min(len(cp),ca+cbatch)
            for pa in range(0,len(pp),max_chunk):
                checkpoint();pz=min(len(pp),pa+max_chunk)
                ci=np.arange(ca,cz,dtype=np.uint32 if len(cp)<=np.iinfo(np.uint32).max else np.uint64)
                pi=np.arange(pa,pz,dtype=np.uint32 if len(pp)<=np.iinfo(np.uint32).max else np.uint64)
                callcap=np.minimum(c['cap'][ci],math.inf if budget is None else budget/100-fee/100)
                jointcap=np.minimum(callcap[:,None],p['cap'][pi][None,:])
                eligible=c['mid'][ci,None]+p['mid'][pi][None,:]<=jointcap+1e-8
                if pricing_mode=='SMART FILL':eligible&=.97*(c['ask'][ci,None]+p['ask'][pi][None,:])<=jointcap+1e-8
                local_c,local_p=np.nonzero(eligible)
                count=len(local_c);stats['priced_condors']+=count
                if count:
                    cc,pc,dc,values=_ric_price_batch(manifest,ci[local_c],pi[local_p],scalar_fields)
                    if len(dc):
                        arrays=[cc,pc,dc,*values.values()]
                        for array in arrays:array.flags.writeable=False
                        batch_sink(RICBatch(manifest,cc,pc,dc,values))
                        stats['qualified_condors']+=len(dc);stats['batches']+=1
                        if grid is not None:stats['grid_qualified']+=len(dc)
                checkpoint()
    stats['pruned_combinations']=stats['market_combinations']-stats['priced_condors']
    return stats


scan_ric_batches._ric_scalar_calculator = scan_ric_candidates
scan_ric_candidates._ric_batch_calculator = scan_ric_batches
