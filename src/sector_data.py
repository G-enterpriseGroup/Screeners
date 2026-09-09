"""Best-effort sector look-through for portfolio analytics.

Uses Yahoo Finance JSON endpoints for equity sector metadata and ETF/fund
sector weightings. Results should be cached by the caller because sector
classification changes far less frequently than market prices.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import requests


YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_SUMMARY_BASE = "https://query2.finance.yahoo.com/v10/finance/quoteSummary"
YAHOO_COOKIE_BOOTSTRAP = "https://fc.yahoo.com"
REQUEST_TIMEOUT = 10

_SESSION: requests.Session | None = None
_CRUMB: str | None = None

SECTOR_NAMES = {
    "basicmaterials": "Basic Materials",
    "basic_materials": "Basic Materials",
    "communicationservices": "Communication Services",
    "communication_services": "Communication Services",
    "consumercyclical": "Consumer Cyclical",
    "consumer_cyclical": "Consumer Cyclical",
    "consumerdefensive": "Consumer Defensive",
    "consumer_defensive": "Consumer Defensive",
    "energy": "Energy",
    "financialservices": "Financial Services",
    "financial_services": "Financial Services",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "realestate": "Real Estate",
    "real_estate": "Real Estate",
    "technology": "Technology",
    "utilities": "Utilities",
}


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
            }
        )
    return _SESSION


def _crumb() -> str:
    global _CRUMB
    if _CRUMB:
        return _CRUMB

    session = _session()
    try:
        session.get(YAHOO_COOKIE_BOOTSTRAP, timeout=REQUEST_TIMEOUT)
    except requests.RequestException:
        pass

    response = session.get(YAHOO_CRUMB_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    crumb = response.text.strip()
    if not crumb or "<" in crumb:
        raise RuntimeError("Yahoo Finance did not return a usable crumb.")
    _CRUMB = crumb
    return crumb


def _raw_number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw", value.get("fmt"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sector_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    key = re.sub(r"[^a-z_]", "", text.lower().replace("-", "_").replace(" ", "_"))
    compact = key.replace("_", "")
    return SECTOR_NAMES.get(key) or SECTOR_NAMES.get(compact) or text.replace("_", " ").title()


def _sector_weightings(top_holdings: Any) -> dict[str, float]:
    if not isinstance(top_holdings, dict):
        return {}

    raw = top_holdings.get("sectorWeightings") or top_holdings.get("sectorWeighting")
    if not isinstance(raw, list):
        return {}

    weights: dict[str, float] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            weight = _raw_number(value)
            if weight is None or weight <= 0:
                continue
            label = _sector_label(key)
            if label:
                weights[label] = weights.get(label, 0.0) + float(weight)

    total = sum(weights.values())
    if total > 1.5:
        weights = {key: value / 100.0 for key, value in weights.items()}
        total = sum(weights.values())

    if total > 0:
        # Yahoo fund sector weights sometimes omit a tiny residual allocation.
        weights = {key: value / total for key, value in weights.items()}
    return weights


def _summary_profile(symbol: str) -> dict[str, Any]:
    session = _session()
    response = session.get(
        YAHOO_SUMMARY_BASE + "/" + quote(symbol, safe=""),
        params={
            "modules": "assetProfile,topHoldings,quoteType,price",
            "crumb": _crumb(),
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code in (401, 403):
        global _CRUMB
        _CRUMB = None
        response = session.get(
            YAHOO_SUMMARY_BASE + "/" + quote(symbol, safe=""),
            params={
                "modules": "assetProfile,topHoldings,quoteType,price",
                "crumb": _crumb(),
            },
            timeout=REQUEST_TIMEOUT,
        )
    response.raise_for_status()
    payload = response.json()
    results = (
        payload.get("quoteSummary", {}).get("result")
        if isinstance(payload, dict)
        else None
    )
    if not results:
        return {}
    return results[0] if isinstance(results[0], dict) else {}


def _search_sector(symbol: str) -> str:
    response = _session().get(
        YAHOO_SEARCH_URL,
        params={
            "q": symbol,
            "quotesCount": 8,
            "newsCount": 0,
            "enableFuzzyQuery": "false",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    for item in payload.get("quotes", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("symbol", "")).upper() != symbol.upper():
            continue
        label = _sector_label(item.get("sector"))
        if label:
            return label
    return ""


def sector_profile(symbol: str, security_type: str = "") -> dict[str, Any]:
    """Return normalized sector weights for one security.

    Equities generally return one sector at 100%. ETFs/funds use Yahoo's
    reported sectorWeightings when available, giving a look-through sector mix.
    """
    symbol = str(symbol or "").strip().upper()
    security_type = str(security_type or "").strip().upper()
    if not symbol:
        return {
            "symbol": symbol,
            "weights": {"Other / Unclassified": 1.0},
            "source": "fallback",
            "kind": "unknown",
        }

    try:
        summary = _summary_profile(symbol)
        asset_profile = summary.get("assetProfile") if isinstance(summary, dict) else {}
        asset_profile = asset_profile if isinstance(asset_profile, dict) else {}
        top_holdings = summary.get("topHoldings") if isinstance(summary, dict) else {}
        quote_type = summary.get("quoteType") if isinstance(summary, dict) else {}
        quote_type = quote_type if isinstance(quote_type, dict) else {}

        fund_weights = _sector_weightings(top_holdings)
        if fund_weights:
            return {
                "symbol": symbol,
                "weights": fund_weights,
                "source": "Yahoo Finance sectorWeightings",
                "kind": str(quote_type.get("quoteType") or "FUND"),
            }

        sector = _sector_label(asset_profile.get("sector"))
        if sector:
            return {
                "symbol": symbol,
                "weights": {sector: 1.0},
                "source": "Yahoo Finance assetProfile",
                "kind": str(quote_type.get("quoteType") or security_type or "EQUITY"),
            }

        sector = _search_sector(symbol)
        if sector:
            return {
                "symbol": symbol,
                "weights": {sector: 1.0},
                "source": "Yahoo Finance search",
                "kind": str(quote_type.get("quoteType") or security_type or "EQUITY"),
            }

    except Exception as exc:
        error = str(exc)[:180]
    else:
        error = ""

    fallback = {
        "OPTN": "Options / Derivatives",
        "OPTION": "Options / Derivatives",
        "BOND": "Fixed Income",
        "MF": "Fund / Mixed",
        "MMF": "Cash / Money Market",
    }.get(security_type, "Other / Unclassified")

    return {
        "symbol": symbol,
        "weights": {fallback: 1.0},
        "source": "fallback",
        "kind": security_type or "unknown",
        "error": error,
    }
