"""E*TRADE-powered Gamma Exposure workspace for Raj's Terminal.

This module ports the user's Google Apps Script GEX logic into Streamlit while
using only authenticated E*TRADE market data. It intentionally does not scrape
or call CBOE. Calls are read-only through the terminal's existing E*TRADE
client/cache.

Model convention (matching the supplied Apps Script):
- Call GEX is signed positive.
- Put GEX is signed negative.
- GEX ~= gamma * open_interest * 100 * spot^2 * 1%.
- Call Wall = strike with maximum call open interest.
- Put Wall = strike with minimum net GEX.
- Gamma Flip = nearest-to-spot zero crossing from a 1,000-step Black-Scholes
  gamma rescan.
This is a positioning estimate, not observed dealer inventory.
"""

from __future__ import annotations

import copy
import html
import inspect
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.etrade_client import ETradeError, option_expiration_dates, quote_summary


CONTRACT_SIZE = 100.0
RISK_FREE_RATE = 0.045
DEFAULT_IV = 0.30
TOP_N = 8
DTE_CHOICES = [7, 14, 21, 30, 45, 60, 90, 120, 180, 365]
DEFAULT_STATE = {
    "revision": 0,
    "tickers": [],
    "global_dte": 45,
    "timezone": "America/New_York",
    "wall_value_mode": "NET_GEX",
    "dte_overrides": {},
    "notes": {},
}

_COMPONENT_PATH = Path(__file__).parent / "components" / "gex_state_v1"
_gex_state_component = components.declare_component(
    "raj_gex_state_v1",
    path=str(_COMPONENT_PATH),
)


@st.cache_resource
def _gex_state_vault() -> dict[str, dict[str, Any]]:
    return {}


@st.cache_resource
def _gex_results_vault() -> dict[str, dict[str, Any]]:
    return {}


def _normalize_ticker(value: Any) -> str:
    text = str(value or "").upper().strip()
    for prefix in ("NYSEARCA:", "NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CBOE:", "ARCA:"):
        text = text.replace(prefix, "")
    return text.replace("^", "").replace(".", "").replace(" ", "")


def _clean_state(raw: Any) -> dict[str, Any]:
    state = copy.deepcopy(DEFAULT_STATE)
    if not isinstance(raw, dict):
        return state
    try:
        state["revision"] = max(0, int(raw.get("revision", 0) or 0))
    except Exception:
        state["revision"] = 0

    tickers: list[str] = []
    for value in raw.get("tickers", []) or []:
        ticker = _normalize_ticker(value)
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    state["tickers"] = tickers[:50]

    try:
        dte = int(raw.get("global_dte", 45) or 45)
    except Exception:
        dte = 45
    state["global_dte"] = dte if dte in DTE_CHOICES else 45

    tz = str(raw.get("timezone") or "America/New_York").strip()
    try:
        ZoneInfo(tz)
        state["timezone"] = tz
    except Exception:
        state["timezone"] = "America/New_York"

    wall_mode = str(raw.get("wall_value_mode") or "NET_GEX").upper().strip()
    state["wall_value_mode"] = (
        wall_mode if wall_mode in {"NET_GEX", "COMPONENT_GEX"} else "NET_GEX"
    )

    overrides: dict[str, int] = {}
    if isinstance(raw.get("dte_overrides"), dict):
        for key, value in raw["dte_overrides"].items():
            ticker = _normalize_ticker(key)
            try:
                value = int(value)
            except Exception:
                continue
            if ticker and value in DTE_CHOICES:
                overrides[ticker] = value
    state["dte_overrides"] = overrides

    notes: dict[str, list[dict[str, str]]] = {}
    if isinstance(raw.get("notes"), dict):
        for key, rows in raw["notes"].items():
            ticker = _normalize_ticker(key)
            if not ticker or not isinstance(rows, list):
                continue
            clean_rows = []
            for row in rows[:100]:
                if not isinstance(row, dict):
                    continue
                note = str(row.get("note") or "").strip()
                updated = str(row.get("updated") or "")
                if note:
                    clean_rows.append({"note": note[:5000], "updated": updated})
            if clean_rows:
                notes[ticker] = clean_rows
    state["notes"] = notes
    return state


def _state_key(vault_key: str) -> str:
    return "gex:" + str(vault_key or "default")[:32]


def _load_state(vault_key: str) -> dict[str, Any]:
    key = _state_key(vault_key)
    session = st.session_state.get("_gex_state")
    if isinstance(session, dict):
        state = _clean_state(session)
    else:
        state = _clean_state(_gex_state_vault().get(key, DEFAULT_STATE))
        st.session_state["_gex_state"] = copy.deepcopy(state)

    browser = _gex_state_component(
        storage_key="raj-terminal-" + key,
        server_state=state,
        key="raj_gex_state_v1",
        default=None,
    )
    if isinstance(browser, dict) and isinstance(browser.get("state"), dict):
        browser_state = _clean_state(browser["state"])
        if browser_state["revision"] > state["revision"] or (
            state["revision"] == 0
            and not state["tickers"]
            and browser_state["tickers"]
        ):
            state = browser_state
            st.session_state["_gex_state"] = copy.deepcopy(state)
            _gex_state_vault()[key] = copy.deepcopy(state)
            st.rerun()
    return state


def _save_state(vault_key: str, state: dict[str, Any]) -> dict[str, Any]:
    state = _clean_state(state)
    state["revision"] = int(state.get("revision", 0) or 0) + 1
    st.session_state["_gex_state"] = copy.deepcopy(state)
    _gex_state_vault()[_state_key(vault_key)] = copy.deepcopy(state)
    return state


def _sync_browser_state(vault_key: str, state: dict[str, Any]) -> None:
    """Write current watchlist/settings/notes to browser localStorage without brokerage data."""
    _gex_state_component(
        storage_key="raj-terminal-" + _state_key(vault_key),
        server_state=_clean_state(state),
        key="raj_gex_state_writer",
        default=None,
    )


def _terminal_context() -> tuple[Any, str, Any]:
    """Resolve the terminal client/vault without creating a second OAuth stack."""
    candidates: list[dict[str, Any]] = []
    main = sys.modules.get("__main__")
    if main is not None:
        candidates.append(vars(main))
    for frame in inspect.stack()[1:12]:
        candidates.append(frame.frame.f_globals)

    for scope in candidates:
        factory = scope.get("_etrade_client")
        hash_fn = scope.get("_trade_access_code_hash")
        touch = scope.get("_touch_etrade_session")
        if callable(factory):
            try:
                client = factory()
            except Exception:
                client = None
            try:
                vault_key = str(hash_fn()) if callable(hash_fn) else "default"
            except Exception:
                vault_key = "default"
            return client, vault_key, touch
    return None, "default", None


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


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number(value: Any, *keys: str) -> float | None:
    for key in keys:
        raw = _find_key(value, key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_iv(raw: Any) -> float:
    try:
        iv = float(raw)
    except (TypeError, ValueError):
        iv = DEFAULT_IV
    if not math.isfinite(iv) or iv <= 0:
        iv = DEFAULT_IV
    if iv > 5:
        iv /= 100.0
    return max(iv, 0.0001)


def _bs_gamma(spot: float, strike: float, iv: float, dte_days: float, r: float = RISK_FREE_RATE) -> float:
    if spot <= 0 or strike <= 0:
        return 0.0
    sigma = max(float(iv), 0.0001)
    t = max(float(dte_days) / 365.0, 0.5 / 365.0)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) / (spot * sigma * sqrt_t)


def _call_api(client: Any, method: str, *args: Any, force_refresh: bool = False, **kwargs: Any) -> Any:
    fn = getattr(client, method)
    if force_refresh:
        try:
            return fn(*args, force_refresh=True, **kwargs)
        except TypeError:
            pass
    return fn(*args, **kwargs)


def _parse_contracts(chain: dict[str, Any], dte: int, spot: float) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    pairs = _as_list(_find_key(chain, "OptionPair"))
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        for cp, side_name in (("C", "Call"), ("P", "Put")):
            option = pair.get(side_name) or pair.get(side_name.lower())
            if not isinstance(option, dict):
                continue
            strike = _number(option, "strikePrice", "strike")
            oi = _number(option, "openInterest", "open_interest", "oi")
            if strike is None or oi is None or oi <= 0:
                continue

            greeks = (
                option.get("OptionGreeks")
                or option.get("optionGreeks")
                or option.get("optionGreek")
                or {}
            )
            gamma_feed = _number(greeks, "gamma") if isinstance(greeks, dict) else None
            iv_raw = None
            if isinstance(greeks, dict):
                iv_raw = (
                    _find_key(greeks, "iv")
                    or _find_key(greeks, "impliedVolatility")
                    or _find_key(greeks, "implied_volatility")
                )
            if iv_raw is None:
                iv_raw = (
                    _find_key(option, "iv")
                    or _find_key(option, "impliedVolatility")
                    or _find_key(option, "implied_volatility")
                )
            iv = _normalize_iv(iv_raw)
            gamma_now = (
                float(gamma_feed)
                if gamma_feed is not None and gamma_feed > 0
                else _bs_gamma(spot, float(strike), iv, dte)
            )
            contracts.append(
                {
                    "cp": cp,
                    "strike": float(strike),
                    "dte": int(dte),
                    "oi": float(oi),
                    "iv": float(iv),
                    "gamma_now": float(gamma_now),
                }
            )
    return contracts


def _estimate_gamma_flip(contracts: list[dict[str, Any]], spot: float, strikes: list[float]) -> float | None:
    """Vectorized 1,000-step zero-crossing scan matching the supplied Apps Script."""
    if not contracts or not strikes or spot <= 0:
        return None

    c_strikes = np.asarray([c["strike"] for c in contracts], dtype=float)
    c_iv = np.maximum(np.asarray([c["iv"] for c in contracts], dtype=float), 0.0001)
    c_t = np.maximum(np.asarray([c["dte"] for c in contracts], dtype=float) / 365.0, 0.5 / 365.0)
    c_sqrt_t = np.sqrt(c_t)
    c_oi = np.asarray([c["oi"] for c in contracts], dtype=float)
    c_sign = np.asarray([1.0 if c["cp"] == "C" else -1.0 for c in contracts], dtype=float)

    def total_at(test_spot: float) -> float:
        d1 = (
            np.log(test_spot / c_strikes)
            + (RISK_FREE_RATE + 0.5 * c_iv * c_iv) * c_t
        ) / (c_iv * c_sqrt_t)
        gamma = (
            np.exp(-0.5 * d1 * d1)
            / math.sqrt(2.0 * math.pi)
            / (test_spot * c_iv * c_sqrt_t)
        )
        exposure = gamma * c_oi * CONTRACT_SIZE * test_spot * test_spot * 0.01
        return float(np.sum(c_sign * exposure))

    scan_low = max(0.01, min(min(strikes), spot * 0.50))
    scan_high = max(max(strikes), spot * 1.50)
    steps = 1000
    best: float | None = None
    best_distance: float | None = None
    prev_px = scan_low
    prev_gex = total_at(prev_px)

    for i in range(1, steps + 1):
        px = scan_low + (scan_high - scan_low) * i / steps
        now_gex = total_at(px)
        crossed = (
            (prev_gex < 0 < now_gex)
            or (prev_gex > 0 > now_gex)
            or now_gex == 0
        )
        if crossed and abs(now_gex - prev_gex) > 0:
            candidate = prev_px - prev_gex * (px - prev_px) / (now_gex - prev_gex)
            distance = abs(candidate - spot)
            if best_distance is None or distance < best_distance:
                best = candidate
                best_distance = distance
        prev_px, prev_gex = px, now_gex
    return best


def _fmt_num(value: Any, decimals: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    text = f"{number:.{decimals}f}".rstrip("0").rstrip(".")
    return text


def _spot_range_visual(spot: float, put_wall: float, gamma_flip: float | None, call_wall: float) -> str:
    if not spot or not put_wall or not call_wall:
        return "Missing wall data"
    low, high = min(put_wall, call_wall), max(put_wall, call_wall)
    bar_length = 18
    if high == low:
        position = bar_length // 2
    else:
        position = round(((spot - low) / (high - low)) * bar_length)
    position = max(0, min(bar_length, position))
    if low < spot < high:
        status = "INSIDE"
    elif spot >= high:
        status = "ABOVE RANGE"
    elif spot <= low:
        status = "BELOW RANGE"
    else:
        status = "CHECK RANGE"
    gf = f" | GFlip {_fmt_num(gamma_flip, 2)}" if gamma_flip and gamma_flip > 0 else ""
    return f"{_fmt_num(low, 2)} {'━' * position}●{'━' * (bar_length - position)} {_fmt_num(high, 2)} | {status}{gf}"


def _build_gex(
    client: Any,
    symbol: str,
    max_dte: int,
    timezone_name: str,
    wall_value_mode: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    symbol = _normalize_ticker(symbol)
    if not symbol:
        raise ETradeError("Enter a ticker first.")
    if client is None:
        raise ETradeError("Connect E*TRADE first, or seed a cached option-chain snapshot.")

    quote = quote_summary(_call_api(client, "get_quote", symbol, force_refresh=force_refresh))
    spot = float(quote["last"])
    expiration_payload = _call_api(
        client,
        "get_option_expirations",
        symbol,
        force_refresh=force_refresh,
    )
    expirations = option_expiration_dates(expiration_payload)
    tz = ZoneInfo(timezone_name)
    today = datetime.now(tz).date()

    eligible: list[tuple[int, int, int, int]] = []
    for year, month, day in expirations:
        expiry = datetime(year, month, day, tzinfo=tz).date()
        dte = max((expiry - today).days, 0)
        if 0 <= dte <= int(max_dte):
            eligible.append((year, month, day, dte))

    if not eligible:
        raise ETradeError(
            f"No E*TRADE option expirations were found at or below {max_dte} DTE for {symbol}."
        )

    contracts: list[dict[str, Any]] = []
    expiries_used: list[str] = []
    chain_errors: list[str] = []
    for year, month, day, dte in eligible:
        try:
            chain = _call_api(
                client,
                "get_option_chain",
                symbol,
                year,
                month,
                day,
                no_of_strikes=100,
                chain_type="CALLPUT",
                force_refresh=force_refresh,
            )
            parsed = _parse_contracts(chain, dte, spot)
            if parsed:
                contracts.extend(parsed)
                expiries_used.append(f"{year:04d}-{month:02d}-{day:02d}")
        except Exception as exc:
            chain_errors.append(f"{year:04d}-{month:02d}-{day:02d}: {exc}")

    if not contracts:
        detail = f" Last chain error: {chain_errors[-1]}" if chain_errors else ""
        raise ETradeError(
            f"No usable E*TRADE option contracts with open interest were found for {symbol}.{detail}"
        )

    agg: dict[float, dict[str, float]] = {}
    for contract in contracts:
        strike = contract["strike"]
        row = agg.setdefault(
            strike,
            {
                "strike": strike,
                "call_oi": 0.0,
                "put_oi": 0.0,
                "call_gex": 0.0,
                "put_gex": 0.0,
                "net_gex": 0.0,
                "cum_gex": 0.0,
                "rank": 0,
            },
        )
        exposure = (
            contract["gamma_now"]
            * contract["oi"]
            * CONTRACT_SIZE
            * spot
            * spot
            * 0.01
        )
        if contract["cp"] == "C":
            row["call_oi"] += contract["oi"]
            row["call_gex"] += exposure
            row["net_gex"] += exposure
        else:
            row["put_oi"] += contract["oi"]
            row["put_gex"] -= exposure
            row["net_gex"] -= exposure

    rows = sorted(agg.values(), key=lambda row: row["strike"])
    cumulative = 0.0
    for row in rows:
        cumulative += row["net_gex"]
        row["cum_gex"] = cumulative

    ranked = sorted(rows, key=lambda row: abs(row["net_gex"]), reverse=True)
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank

    max_call_oi = max(rows, key=lambda row: row["call_oi"])
    max_put_oi = max(rows, key=lambda row: row["put_oi"])
    call_wall_raw = max_call_oi
    put_wall_raw = min(rows, key=lambda row: row["net_gex"])

    gamma_flip = _estimate_gamma_flip(
        contracts,
        spot,
        [float(row["strike"]) for row in rows],
    )

    wall_value_mode = str(wall_value_mode or "NET_GEX").upper()
    if wall_value_mode == "COMPONENT_GEX":
        call_wall_value = call_wall_raw["call_gex"]
        put_wall_value = put_wall_raw["put_gex"]
    else:
        call_wall_value = call_wall_raw["net_gex"]
        put_wall_value = put_wall_raw["net_gex"]

    call_wall = {"strike": call_wall_raw["strike"], "value": call_wall_value}
    put_wall = {"strike": put_wall_raw["strike"], "value": put_wall_value}
    top_gex = ranked[:TOP_N]
    net_current = sum(row["net_gex"] for row in rows)

    packed_rows = [f"SPOT,{_fmt_num(spot)},0"]
    if gamma_flip:
        packed_rows.append(f"GFLIP,{_fmt_num(gamma_flip)},0")
    packed_rows.extend(
        [
            f"CALLWALL,{_fmt_num(call_wall['strike'])},{_fmt_num(call_wall['value'], 2)}",
            f"PUTWALL,{_fmt_num(put_wall['strike'])},{_fmt_num(put_wall['value'], 2)}",
            f"MAXCALLOI,{_fmt_num(max_call_oi['strike'])},{_fmt_num(max_call_oi['call_oi'], 0)}",
            f"MAXPUTOI,{_fmt_num(max_put_oi['strike'])},{_fmt_num(max_put_oi['put_oi'], 0)}",
        ]
    )
    for index, values in enumerate(top_gex, 1):
        tag = "GEXPOS" if values["net_gex"] >= 0 else "GEXNEG"
        packed_rows.append(
            f"{tag}{index},{_fmt_num(values['strike'])},{_fmt_num(values['net_gex'], 2)}"
        )
    packed = "\n".join(packed_rows)

    updated = datetime.now(tz)
    summary_text = (
        f"Ticker: {symbol}\n"
        f"Mode: BARCHART_STYLE\n"
        f"Spot: {_fmt_num(spot)}\n"
        f"Max DTE Used: {max_dte}\n"
        f"Contracts Used: {len(contracts)}\n"
        f"Net Current GEX: {net_current:,.2f}\n"
        f"Source URL: E*TRADE API /v1/market/optionchains\n\n"
        "PASTE EVERYTHING BELOW INTO PINE INPUT: Packed Gamma Levels\n"
        "------------------------------------------------------------\n"
        f"{packed}\n"
    )

    return {
        "symbol": symbol,
        "mode": "BARCHART_STYLE",
        "spot": spot,
        "maxDte": int(max_dte),
        "contractsUsed": len(contracts),
        "netCurrent": net_current,
        "sourceUrl": "E*TRADE API /v1/market/optionchains",
        "gammaFlip": gamma_flip,
        "callWall": call_wall,
        "putWall": put_wall,
        "maxCallOi": max_call_oi,
        "maxPutOi": max_put_oi,
        "topGex": top_gex,
        "packed": packed,
        "summaryText": summary_text,
        "rawRows": rows,
        "spotRangeVisual": _spot_range_visual(
            spot,
            put_wall["strike"],
            gamma_flip,
            call_wall["strike"],
        ),
        "updated": updated.isoformat(),
        "expiriesUsed": expiries_used,
        "chainErrors": chain_errors,
    }


def _results(vault_key: str) -> dict[str, Any]:
    return _gex_results_vault().setdefault(_state_key(vault_key), {})


def _refresh_one(
    client: Any,
    vault_key: str,
    state: dict[str, Any],
    symbol: str,
    touch_session: Any,
    *,
    force_refresh: bool = True,
) -> dict[str, Any]:
    symbol = _normalize_ticker(symbol)
    dte = int(state.get("dte_overrides", {}).get(symbol, state["global_dte"]))
    result = _build_gex(
        client,
        symbol,
        dte,
        state["timezone"],
        state["wall_value_mode"],
        force_refresh=force_refresh,
    )
    _results(vault_key)[symbol] = result
    if callable(touch_session):
        try:
            touch_session()
        except Exception:
            pass
    return result


def _ticker_table(state: dict[str, Any], result_map: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ticker in state["tickers"]:
        result = result_map.get(ticker)
        if not result:
            rows.append(
                {
                    "Ticker": ticker,
                    "Instructions / Last Ran": "CLICK REFRESH GEX",
                    "DTE Used": int(state["dte_overrides"].get(ticker, state["global_dte"])),
                    "Spot": None,
                    "Put Wall": None,
                    "Put Wall GEX": None,
                    "Gamma Flip": None,
                    "Call Wall": None,
                    "Call Wall GEX": None,
                    "Top GEX Strike": None,
                    "Top GEX Value": None,
                    "Spot Range Visual": "WAITING FOR E*TRADE OPTIONS",
                }
            )
            continue
        top = result["topGex"][0] if result.get("topGex") else None
        try:
            stamp = datetime.fromisoformat(result["updated"]).strftime("%m/%d/%Y %I:%M:%S %p")
        except Exception:
            stamp = str(result.get("updated") or "")
        rows.append(
            {
                "Ticker": ticker,
                "Instructions / Last Ran": f"E*TRADE // {stamp}",
                "DTE Used": result["maxDte"],
                "Spot": result["spot"],
                "Put Wall": result["putWall"]["strike"],
                "Put Wall GEX": result["putWall"]["value"],
                "Gamma Flip": result["gammaFlip"],
                "Call Wall": result["callWall"]["strike"],
                "Call Wall GEX": result["callWall"]["value"],
                "Top GEX Strike": top["strike"] if top else None,
                "Top GEX Value": top["net_gex"] if top else None,
                "Spot Range Visual": result["spotRangeVisual"],
            }
        )
    return pd.DataFrame(rows)


def _summary_table(result_map: dict[str, Any], tickers: list[str]) -> pd.DataFrame:
    rows = []
    for ticker in tickers:
        result = result_map.get(ticker)
        if not result:
            continue
        rows.append(
            {
                "Ticker": ticker,
                "Mode": result["mode"],
                "Spot": result["spot"],
                "Max DTE Used": result["maxDte"],
                "Contracts Used": result["contractsUsed"],
                "Net Current GEX": result["netCurrent"],
                "Source URL": result["sourceUrl"],
                "Gamma Flip": result["gammaFlip"],
                "Call Wall": result["callWall"]["strike"],
                "Call Wall Value": result["callWall"]["value"],
                "Put Wall": result["putWall"]["strike"],
                "Put Wall Value": result["putWall"]["value"],
                "Max Call OI Strike": result["maxCallOi"]["strike"],
                "Max Call OI": result["maxCallOi"]["call_oi"],
                "Max Put OI Strike": result["maxPutOi"]["strike"],
                "Max Put OI": result["maxPutOi"]["put_oi"],
                "Updated": result["updated"],
            }
        )
    return pd.DataFrame(rows)


def _raw_table(result: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Ticker": result["symbol"],
                "Strike": row["strike"],
                "Call OI": row["call_oi"],
                "Put OI": row["put_oi"],
                "Call GEX": row["call_gex"],
                "Put GEX": row["put_gex"],
                "Net GEX": row["net_gex"],
                "Cumulative Net GEX": row["cum_gex"],
                "Abs Net GEX Rank": row["rank"],
            }
            for row in result["rawRows"]
        ]
    )


def _plot_gex(result: dict[str, Any]) -> go.Figure:
    rows = result["rawRows"]
    colors = ["#4af6c3" if row["net_gex"] >= 0 else "#ff433d" for row in rows]
    fig = go.Figure(
        go.Bar(
            x=[row["strike"] for row in rows],
            y=[row["net_gex"] for row in rows],
            marker_color=colors,
            customdata=[
                [row["call_oi"], row["put_oi"], row["call_gex"], row["put_gex"]]
                for row in rows
            ],
            hovertemplate=(
                "Strike %{x:.2f}<br>"
                "Net GEX %{y:,.0f}<br>"
                "Call OI %{customdata[0]:,.0f}<br>"
                "Put OI %{customdata[1]:,.0f}<br>"
                "Call GEX %{customdata[2]:,.0f}<br>"
                "Put GEX %{customdata[3]:,.0f}<extra></extra>"
            ),
        )
    )
    spot = result["spot"]
    call_wall = result["callWall"]["strike"]
    put_wall = result["putWall"]["strike"]
    gamma_flip = result["gammaFlip"]
    fig.add_vline(x=spot, line_width=2, line_color="#0068ff", annotation_text="SPOT")
    fig.add_vline(x=call_wall, line_width=2, line_color="#4af6c3", annotation_text="CALL WALL")
    fig.add_vline(x=put_wall, line_width=2, line_color="#ff433d", annotation_text="PUT WALL")
    if gamma_flip:
        fig.add_vline(x=gamma_flip, line_width=2, line_dash="dot", line_color="#fb8b1e", annotation_text="GAMMA FLIP")
    fig.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=35, b=40),
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        font=dict(family="Courier New, monospace", color="#fb8b1e"),
        xaxis=dict(title="STRIKE", gridcolor="#1c1c1c", zeroline=False),
        yaxis=dict(title="NET GEX", gridcolor="#1c1c1c", zerolinecolor="#fb8b1e"),
        showlegend=False,
        hoverlabel=dict(bgcolor="#050505", bordercolor="#fb8b1e", font_color="#ffffff"),
    )
    return fig


def _master_bridge(result_map: dict[str, Any], tickers: list[str]) -> str:
    blocks = [result_map[ticker]["summaryText"].strip() for ticker in tickers if ticker in result_map]
    return "\n\n".join(blocks).strip()


def _render_metric(label: str, value: str, tone: str = "green", detail: str = "") -> None:
    cls = {"green": "gex-green", "red": "gex-red", "blue": "gex-blue"}.get(tone, "gex-orange")
    st.markdown(
        f"""
        <div class="gex-metric">
          <div class="gex-metric-label">{html.escape(label)}</div>
          <div class="gex-metric-value {cls}">{html.escape(value)}</div>
          <div class="gex-metric-detail">{html.escape(detail)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_copy_button(text: str, label: str = "COPY A6 TO CLIPBOARD") -> None:
    """Client-side copy button with the same clipboard fallback used by the supplied sidebar."""
    payload = json.dumps(str(text or ""))
    label_payload = json.dumps(str(label))
    components.html(
        f"""
        <!doctype html><html><head><meta charset="utf-8"><style>
        html,body{{margin:0;padding:0;background:#000;font-family:"Courier New",monospace;overflow:hidden}}
        button{{width:100%;height:42px;border:1px solid #66adff;background:#0068ff;color:#fff;
        font:900 14px "Courier New",monospace;cursor:pointer;box-shadow:0 3px 0 #003579}}
        button:active{{transform:translateY(2px);box-shadow:none}}
        #msg{{height:16px;margin-top:4px;color:#4af6c3;font:700 10px "Courier New",monospace;text-align:center}}
        </style></head><body><button id="copy"></button><div id="msg"></div><script>
        const text={payload}; const label={label_payload}; const btn=document.getElementById("copy");
        const msg=document.getElementById("msg"); btn.textContent=label;
        function fallback(){{
          const a=document.createElement("textarea");a.value=text;a.style.position="fixed";a.style.left="-9999px";
          document.body.appendChild(a);a.focus();a.select();let ok=false;
          try{{ok=document.execCommand("copy")}}catch(_e){{ok=false}}document.body.removeChild(a);return ok;
        }}
        btn.onclick=async()=>{{
          let ok=false;
          try{{if(navigator.clipboard&&window.isSecureContext){{await navigator.clipboard.writeText(text);ok=true}}}}catch(_e){{}}
          if(!ok) ok=fallback();
          msg.textContent=ok?"COPIED // PASTE INTO TRADINGVIEW":"COPY BLOCKED // USE CODE BOX COPY ICON";
        }};
        </script></body></html>
        """,
        height=64,
        scrolling=False,
    )


def _render_css() -> None:
    st.markdown(
        """
        <style>
        .gex-header{
            background:#fb8b1e;color:#000!important;border:1px solid #fb8b1e;
            padding:.48rem .72rem;margin:.34rem 0 .24rem;font-family:"Courier New",monospace;
            font-size:1.45rem;line-height:1.05;font-weight:900;letter-spacing:.035em;text-transform:uppercase;
        }
        .gex-sub{color:#c87816!important;font-family:"Courier New",monospace;font-size:.78rem;
            font-weight:800;line-height:1.25;margin:0 0 .55rem}
        .gex-ribbon{border:1px solid #fb8b1e;background:#020202;padding:.48rem .62rem;margin:.18rem 0 .55rem;
            font:800 .75rem/1.3 "Courier New",monospace;color:#fb8b1e!important}
        .gex-ticker-card{border:1px solid #fb8b1e;background:#020202;padding:.5rem .58rem;min-height:70px}
        .gex-ticker-symbol{font:900 1.18rem/1 "Courier New",monospace;color:#4af6c3!important}
        .gex-ticker-meta{font:700 .67rem/1.25 "Courier New",monospace;color:#c87816!important;margin-top:.3rem}
        .gex-metric{border:1px solid #fb8b1e;background:#000;padding:.45rem .58rem;min-height:86px}
        .gex-metric-label{color:#fb8b1e!important;font:900 .68rem/1.05 "Courier New",monospace;text-transform:uppercase}
        .gex-metric-value{font:900 1.4rem/1.1 "Courier New",monospace;margin-top:.18rem;white-space:nowrap}
        .gex-metric-detail{color:#c87816!important;font:700 .62rem/1.15 "Courier New",monospace;margin-top:.18rem}
        .gex-green{color:#4af6c3!important}.gex-red{color:#ff433d!important}.gex-blue{color:#0068ff!important}.gex-orange{color:#fb8b1e!important}
        .gex-section{background:#fb8b1e;color:#000!important;padding:.34rem .5rem;margin:.68rem 0 .4rem;
            font:900 .96rem/1.05 "Courier New",monospace;text-transform:uppercase}
        div[data-testid="stDataFrame"]{max-width:100%!important;overflow:hidden!important}
        div[data-testid="stVerticalBlock"]{min-width:0!important}
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.fragment
def render_gex() -> None:
    _render_css()
    client, vault_key, touch_session = _terminal_context()
    state = _load_state(vault_key)
    result_map = _results(vault_key)

    if st.session_state.pop("_gex_clear_new_ticker", False):
        st.session_state["gex_new_ticker"] = ""
    if st.session_state.pop("_gex_clear_note_text", False):
        st.session_state["gex_note_text"] = ""

    st.markdown('<div class="gex-header">GEX // E*TRADE OPTIONS ENGINE</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="gex-sub">GAMMA EXPOSURE // PERSISTENT WATCHLIST // PER-TICKER DTE // TRADINGVIEW A6 BRIDGE // NO CBOE</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="gex-ribbon">MODEL // CALLS +GEX // PUTS −GEX // GAMMA × OI × 100 × SPOT² × 1% // '
        'CALL WALL = MAX CALL OI // PUT WALL = MOST NEGATIVE NET GEX // GAMMA FLIP = 1,000-STEP RESCAN</div>',
        unsafe_allow_html=True,
    )

    add_col, dte_col, wall_col, refresh_col = st.columns([2.15, 1.0, 1.2, 1.2], gap="small")
    with add_col:
        add_input, add_button = st.columns([4.2, 1], gap="small")
        with add_input:
            new_ticker = st.text_input(
                "ADD TICKER",
                key="gex_new_ticker",
                placeholder="SPY, QQQ, NVDA...",
            )
        with add_button:
            st.write("")
            add_clicked = st.button(
                "＋",
                key="gex_add_ticker",
                help="Add ticker to the saved GEX watchlist",
                width="stretch",
            )
    with dte_col:
        global_dte = st.selectbox(
            "GLOBAL MAX DTE",
            DTE_CHOICES,
            index=DTE_CHOICES.index(state["global_dte"]),
            key="gex_global_dte",
        )
    with wall_col:
        wall_mode_label = st.selectbox(
            "WALL VALUE",
            ["NET", "COMPONENT"],
            index=0 if state["wall_value_mode"] == "NET_GEX" else 1,
            key="gex_wall_mode",
            help="NET = combined call + put GEX at the wall strike. COMPONENT = side-only GEX.",
        )
    with refresh_col:
        st.write("")
        refresh_all = st.button(
            "REFRESH ALL GEX",
            type="primary",
            width="stretch",
            key="gex_refresh_all",
            disabled=not state["tickers"],
        )

    with st.expander("＋ ADVANCED GEX SETTINGS // TIMEZONE + MODEL", expanded=False):
        tz_col, model_col = st.columns([1.2, 2.8], gap="small")
        with tz_col:
            timezone_input = st.text_input(
                "TIMEZONE",
                value=state["timezone"],
                key="gex_timezone_input",
                help="IANA timezone used for DTE/date logic. The supplied Apps Script default is America/New_York.",
            )
        with model_col:
            st.caption(
                "OUTPUT MODE // BARCHART_STYLE // CONTRACT SIZE 100 // RISK-FREE RATE 4.50% // "
                "DEFAULT IV 30% WHEN E*TRADE GREEKS/IV ARE MISSING // TOP 8 ABSOLUTE NET-GEX STRIKES"
            )

    changed = False
    normalized_new = _normalize_ticker(new_ticker)
    if add_clicked:
        if not normalized_new:
            st.warning("ENTER A TICKER FIRST")
        elif normalized_new in state["tickers"]:
            st.info(f"{normalized_new} IS ALREADY IN YOUR GEX WATCHLIST")
        else:
            state["tickers"].append(normalized_new)
            state = _save_state(vault_key, state)
            st.session_state["_gex_clear_new_ticker"] = True
            st.rerun()

    desired_wall_mode = "NET_GEX" if wall_mode_label == "NET" else "COMPONENT_GEX"
    desired_timezone = str(timezone_input or "").strip() or "America/New_York"
    timezone_changed = desired_timezone != state["timezone"]
    if timezone_changed:
        try:
            ZoneInfo(desired_timezone)
        except Exception:
            st.warning(f"INVALID TIMEZONE // {desired_timezone} // keeping {state['timezone']}")
            timezone_changed = False

    global_changed = int(global_dte) != int(state["global_dte"])
    wall_changed = desired_wall_mode != state["wall_value_mode"]
    if global_changed or wall_changed or timezone_changed:
        old_global = int(state["global_dte"])
        state["global_dte"] = int(global_dte)
        state["wall_value_mode"] = desired_wall_mode
        if timezone_changed:
            state["timezone"] = desired_timezone
        state = _save_state(vault_key, state)
        if wall_changed or timezone_changed:
            result_map.clear()
        elif global_changed:
            for ticker in list(result_map):
                if ticker not in state["dte_overrides"] and old_global != int(global_dte):
                    result_map.pop(ticker, None)
        changed = True

    if not state["tickers"]:
        _sync_browser_state(vault_key, state)
        st.info("ADD YOUR FIRST TICKER ABOVE // the watchlist is saved in this browser and restored on future visits.")
        return

    st.markdown('<div class="gex-section">WATCHLIST // SAVED TICKERS</div>', unsafe_allow_html=True)
    ticker_cols = st.columns(min(4, max(1, len(state["tickers"]))), gap="small")
    remove_ticker = None
    for index, ticker in enumerate(state["tickers"]):
        with ticker_cols[index % len(ticker_cols)]:
            dte_used = state["dte_overrides"].get(ticker, state["global_dte"])
            result = result_map.get(ticker)
            result_text = (
                f"SPOT ${result['spot']:,.2f} // {result['contractsUsed']:,} CONTRACTS"
                if result
                else "NOT REFRESHED YET"
            )
            st.markdown(
                f'<div class="gex-ticker-card"><div class="gex-ticker-symbol">{html.escape(ticker)}</div>'
                f'<div class="gex-ticker-meta">≤ {int(dte_used)} DTE // {html.escape(result_text)}</div></div>',
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns([3, 1], gap="small")
            with c1:
                choices = ["GLOBAL"] + [str(value) for value in DTE_CHOICES]
                current = str(state["dte_overrides"].get(ticker, "GLOBAL"))
                selected = st.selectbox(
                    f"{ticker} DTE",
                    choices,
                    index=choices.index(current) if current in choices else 0,
                    key=f"gex_dte_{ticker}",
                    label_visibility="collapsed",
                )
                current_override = state["dte_overrides"].get(ticker)
                desired_override = None if selected == "GLOBAL" else int(selected)
                if desired_override != current_override:
                    if desired_override is None:
                        state["dte_overrides"].pop(ticker, None)
                    else:
                        state["dte_overrides"][ticker] = desired_override
                    state = _save_state(vault_key, state)
                    result_map.pop(ticker, None)
                    changed = True
            with c2:
                if st.button(
                    "🗑",
                    key=f"gex_trash_{ticker}",
                    help=f"Remove {ticker}",
                    width="stretch",
                ):
                    remove_ticker = ticker

    if remove_ticker:
        state["tickers"] = [ticker for ticker in state["tickers"] if ticker != remove_ticker]
        state["dte_overrides"].pop(remove_ticker, None)
        state = _save_state(vault_key, state)
        result_map.pop(remove_ticker, None)
        st.rerun()

    if refresh_all:
        if client is None:
            st.error("E*TRADE OPTIONS UNAVAILABLE // connect E*TRADE first or seed the cache once.")
        else:
            failures = []
            progress = st.progress(0.0, text="REFRESHING E*TRADE OPTION CHAINS...")
            for index, ticker in enumerate(state["tickers"], 1):
                try:
                    _refresh_one(
                        client,
                        vault_key,
                        state,
                        ticker,
                        touch_session,
                        force_refresh=True,
                    )
                except Exception as exc:
                    failures.append(f"{ticker}: {exc}")
                progress.progress(index / len(state["tickers"]), text=f"GEX // {ticker} // {index}/{len(state['tickers'])}")
            progress.empty()
            if failures:
                st.warning("SOME TICKERS COULD NOT REFRESH // " + " | ".join(failures[:5]))
            else:
                st.success("GEX REFRESH COMPLETE // E*TRADE OPTION CHAINS UPDATED")

    st.markdown('<div class="gex-section">TICKERS DASHBOARD // GOOGLE SHEET COLUMNS PORTED 1:1</div>', unsafe_allow_html=True)
    dashboard = _ticker_table(state, result_map)
    st.dataframe(
        dashboard,
        hide_index=True,
        width="stretch",
        height=min(520, 44 + 36 * max(1, len(dashboard))),
        column_config={
            "DTE Used": st.column_config.NumberColumn(format="%d"),
            "Spot": st.column_config.NumberColumn(format="dollar"),
            "Put Wall": st.column_config.NumberColumn(format="dollar"),
            "Put Wall GEX": st.column_config.NumberColumn(format="localized"),
            "Gamma Flip": st.column_config.NumberColumn(format="dollar"),
            "Call Wall": st.column_config.NumberColumn(format="dollar"),
            "Call Wall GEX": st.column_config.NumberColumn(format="localized"),
            "Top GEX Strike": st.column_config.NumberColumn(format="dollar"),
            "Top GEX Value": st.column_config.NumberColumn(format="localized"),
        },
        key="gex_tickers_dashboard",
    )

    available = [ticker for ticker in state["tickers"] if ticker in result_map]
    if not available:
        _sync_browser_state(vault_key, state)
        st.caption("REFRESH GEX TO POPULATE WALLS, GAMMA FLIP, RAW STRIKE DATA, AND TRADINGVIEW BRIDGE.")
        return

    st.markdown('<div class="gex-section">TICKER ANALYTICS</div>', unsafe_allow_html=True)
    selected_ticker = st.selectbox(
        "ANALYZE TICKER",
        available,
        key="gex_selected_ticker",
    )
    result = result_map[selected_ticker]
    one_refresh_col, source_col = st.columns([1.2, 4.8], gap="small")
    with one_refresh_col:
        if st.button(
            f"REFRESH {selected_ticker}",
            type="primary",
            width="stretch",
            key=f"gex_refresh_one_{selected_ticker}",
        ):
            try:
                result = _refresh_one(
                    client,
                    vault_key,
                    state,
                    selected_ticker,
                    touch_session,
                    force_refresh=True,
                )
                st.success(f"{selected_ticker} GEX REFRESHED")
            except Exception as exc:
                st.error(f"{selected_ticker} GEX REFRESH FAILED // {exc}")
    with source_col:
        st.caption(
            f"SOURCE // E*TRADE API // EXPIRIES USED {len(result.get('expiriesUsed', [])):,} // "
            f"CONTRACTS {result['contractsUsed']:,} // MAX DTE {result['maxDte']} // "
            f"WALL MODE {state['wall_value_mode']}"
        )

    m1, m2, m3, m4, m5, m6 = st.columns(6, gap="small")
    with m1:
        _render_metric("Spot", f"${result['spot']:,.2f}", "blue")
    with m2:
        _render_metric(
            "Put Wall",
            f"${result['putWall']['strike']:,.2f}",
            "red",
            f"GEX {result['putWall']['value']:,.0f}",
        )
    with m3:
        _render_metric(
            "Gamma Flip",
            "—" if result["gammaFlip"] is None else f"${result['gammaFlip']:,.2f}",
            "orange",
        )
    with m4:
        _render_metric(
            "Call Wall",
            f"${result['callWall']['strike']:,.2f}",
            "green",
            f"GEX {result['callWall']['value']:,.0f}",
        )
    with m5:
        _render_metric(
            "Net Current GEX",
            f"{result['netCurrent']:,.0f}",
            "green" if result["netCurrent"] >= 0 else "red",
        )
    with m6:
        _render_metric(
            "Contracts",
            f"{result['contractsUsed']:,}",
            "blue",
            f"{len(result.get('expiriesUsed', []))} expiries",
        )

    st.plotly_chart(
        _plot_gex(result),
        width="stretch",
        config={"displayModeBar": False, "responsive": True},
        key=f"gex_plot_{selected_ticker}",
    )

    detail_left, detail_right = st.columns([1.15, 1], gap="medium")
    with detail_left:
        st.markdown('<div class="gex-section">RAW STRIKE DATA</div>', unsafe_allow_html=True)
        raw_df = _raw_table(result)
        st.dataframe(
            raw_df,
            hide_index=True,
            width="stretch",
            height=430,
            column_config={
                "Strike": st.column_config.NumberColumn(format="dollar"),
                "Call OI": st.column_config.NumberColumn(format="localized"),
                "Put OI": st.column_config.NumberColumn(format="localized"),
                "Call GEX": st.column_config.NumberColumn(format="localized"),
                "Put GEX": st.column_config.NumberColumn(format="localized"),
                "Net GEX": st.column_config.NumberColumn(format="localized"),
                "Cumulative Net GEX": st.column_config.NumberColumn(format="localized"),
                "Abs Net GEX Rank": st.column_config.NumberColumn(format="%d"),
            },
            key=f"gex_raw_{selected_ticker}",
        )
    with detail_right:
        st.markdown('<div class="gex-section">FULL SUMMARY</div>', unsafe_allow_html=True)
        summary_df = _summary_table(result_map, state["tickers"])
        st.dataframe(
            summary_df,
            hide_index=True,
            width="stretch",
            height=min(430, 44 + 36 * max(1, len(summary_df))),
            column_config={
                "Spot": st.column_config.NumberColumn(format="dollar"),
                "Contracts Used": st.column_config.NumberColumn(format="localized"),
                "Net Current GEX": st.column_config.NumberColumn(format="localized"),
                "Gamma Flip": st.column_config.NumberColumn(format="dollar"),
                "Call Wall": st.column_config.NumberColumn(format="dollar"),
                "Call Wall Value": st.column_config.NumberColumn(format="localized"),
                "Put Wall": st.column_config.NumberColumn(format="dollar"),
                "Put Wall Value": st.column_config.NumberColumn(format="localized"),
                "Max Call OI Strike": st.column_config.NumberColumn(format="dollar"),
                "Max Call OI": st.column_config.NumberColumn(format="localized"),
                "Max Put OI Strike": st.column_config.NumberColumn(format="dollar"),
                "Max Put OI": st.column_config.NumberColumn(format="localized"),
            },
            key="gex_summary_table",
        )

    st.markdown('<div class="gex-section">TRADINGVIEW BRIDGE // MASTER A6 BLOCK</div>', unsafe_allow_html=True)
    master = _master_bridge(result_map, state["tickers"])
    bridge_options = ["MASTER A6"] + available
    bridge_pick = st.selectbox(
        "BRIDGE BLOCK",
        bridge_options,
        key="gex_bridge_pick",
        help="MASTER A6 mirrors the Google Apps Script TradingView Bridge cell A6. Pick a ticker for only that ticker block.",
    )
    bridge_text = master if bridge_pick == "MASTER A6" else result_map[bridge_pick]["summaryText"].strip()
    st.code(bridge_text or "REFRESH GEX FIRST", language=None, wrap_lines=True)
    bridge_copy_col, bridge_download_col = st.columns(2, gap="small")
    with bridge_copy_col:
        _render_copy_button(
            bridge_text,
            "COPY MASTER A6" if bridge_pick == "MASTER A6" else f"COPY {bridge_pick} BLOCK",
        )
    with bridge_download_col:
        st.download_button(
            "DOWNLOAD TRADINGVIEW BLOCK",
            data=bridge_text,
            file_name="raj_terminal_gex_A6.txt" if bridge_pick == "MASTER A6" else f"{bridge_pick}_gex.txt",
            mime="text/plain",
            width="stretch",
            key="gex_download_bridge",
            disabled=not bool(bridge_text),
        )
    st.caption(
        "TRADINGVIEW BRIDGE // COPY THE CODE BLOCK ABOVE INTO YOUR PINE INPUT NAMED “Packed Gamma Levels”. "
        "MASTER A6 contains every refreshed ticker, matching the supplied Google Apps Script bridge behavior."
    )

    st.markdown('<div class="gex-section">TICKER NOTES // HISTORY MODE</div>', unsafe_allow_html=True)
    note_col, history_col = st.columns([1, 1], gap="medium")
    with note_col:
        note_ticker = st.selectbox("NOTE TICKER", state["tickers"], key="gex_note_ticker")
        note_text = st.text_area(
            "NEW NOTE",
            key="gex_note_text",
            height=135,
            placeholder="Type note here... saving creates a new history entry and never overwrites prior notes.",
        )
        if st.button(
            "SAVE NEW NOTE",
            width="stretch",
            key="gex_save_note",
            disabled=not bool(note_text.strip()),
        ):
            history = state["notes"].setdefault(note_ticker, [])
            history.insert(
                0,
                {
                    "note": note_text.strip(),
                    "updated": datetime.now(ZoneInfo(state["timezone"])).isoformat(),
                },
            )
            state = _save_state(vault_key, state)
            st.session_state["_gex_clear_note_text"] = True
            st.rerun()
    with history_col:
        st.caption(f"{note_ticker} NOTE HISTORY // newest first")
        history = state["notes"].get(note_ticker, [])
        if not history:
            st.info("NO SAVED NOTES FOR THIS TICKER")
        else:
            for index, row in enumerate(history[:20], 1):
                st.markdown(
                    f"**{index:02d} // {html.escape(str(row.get('updated') or ''))}**  \n"
                    f"{html.escape(str(row.get('note') or ''))}"
                )

    _sync_browser_state(vault_key, state)
    if changed:
        st.caption("GEX SETTINGS UPDATED // persistent browser state synchronized.")

    st.caption(
        "GEX MODEL NOTE // This reproduces the supplied Apps Script convention using E*TRADE option-chain Greeks/open interest. "
        "It estimates positioning; E*TRADE does not provide actual dealer inventory, so the sign convention is modeled rather than observed."
    )
