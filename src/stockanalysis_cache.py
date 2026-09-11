"""Stale-safe public classification cache for Raj's Terminal.

StockAnalysis/ETF classification data changes slowly, so the terminal keeps the
last successful sector/industry and ETF sector-lookthrough result indefinitely.
A 24h upstream cache still limits normal network traffic, while this layer acts
as stale-if-error protection when StockAnalysis/Yahoo refuses, rate-limits, or
returns an incomplete response.

Only public ticker classification metadata is persisted. No brokerage balances,
account numbers, tokens, or other private E*TRADE data are written here.
"""

from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import streamlit as st


CACHE_VERSION = 1
_DEFAULT_CACHE_PATH = Path.home() / ".cache" / "raj_terminal" / "sector_industry_cache.json"
CACHE_PATH = Path(os.environ.get("RAJ_CLASSIFICATION_CACHE_PATH", str(_DEFAULT_CACHE_PATH)))


def _blank_store() -> dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "classification": {},
        "lookthrough": {},
    }


def _load_disk() -> dict[str, Any]:
    try:
        if not CACHE_PATH.exists():
            return _blank_store()
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _blank_store()
        raw.setdefault("classification", {})
        raw.setdefault("lookthrough", {})
        raw["version"] = CACHE_VERSION
        return raw
    except Exception:
        return _blank_store()


@st.cache_resource(show_spinner=False)
def _cache_state() -> dict[str, Any]:
    return {
        "data": _load_disk(),
        "lock": threading.RLock(),
    }


def _persist_locked(data: dict[str, Any]) -> None:
    """Best-effort atomic persistence; memory cache remains primary if disk fails."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except Exception:
        pass


def _key(symbol: str, security_type: str = "") -> str:
    return f"{str(symbol or '').strip().upper()}|{str(security_type or '').strip().upper()}"


def _classification_is_usable(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = str(value.get("kind") or "").strip().lower()
    sector = str(value.get("sector") or "").strip()
    industry = str(value.get("industry") or "").strip()
    if kind in {"bond", "option", "cash"}:
        return bool(sector and industry)
    if kind == "etf":
        return bool(value.get("category") or value.get("asset_class") or industry not in {"", "Other / Unclassified"})
    if kind == "stock":
        return bool(
            sector not in {"", "Other / Unclassified"}
            and industry not in {"", "Other / Unclassified"}
        )
    return False


def _lookthrough_is_usable(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    weights = value.get("weights")
    if not isinstance(weights, dict) or not weights:
        return False
    positive = {
        str(name): float(weight or 0.0)
        for name, weight in weights.items()
        if float(weight or 0.0) > 0
    }
    meaningful = {
        name: weight
        for name, weight in positive.items()
        if name not in {"Other / Unclassified", "Fund / Mixed"}
    }
    return bool(meaningful)


def _with_cache_source(value: dict[str, Any], source_label: str) -> dict[str, Any]:
    result = deepcopy(value)
    prior = str(result.get("source") or "").strip()
    result["source"] = f"{prior} // {source_label}" if prior else source_label
    result["cached_fallback"] = True
    return result


def cached_classification(
    original: Callable[[str, str], dict[str, Any]],
    symbol: str,
    security_type: str = "",
) -> dict[str, Any]:
    """Use fresh classification when valid; otherwise serve last successful value."""
    key = _key(symbol, security_type)
    state = _cache_state()

    with state["lock"]:
        saved_entry = deepcopy(state["data"].get("classification", {}).get(key))

    try:
        live = original(symbol, security_type)
    except Exception:
        live = None

    if _classification_is_usable(live):
        entry = {"saved_at": time.time(), "value": deepcopy(live)}
        with state["lock"]:
            state["data"].setdefault("classification", {})[key] = entry
            _persist_locked(state["data"])
        return live

    if isinstance(saved_entry, dict) and _classification_is_usable(saved_entry.get("value")):
        return _with_cache_source(saved_entry["value"], "STALE CACHE")

    if isinstance(live, dict):
        return live
    return {
        "symbol": str(symbol or "").strip().upper(),
        "kind": "unknown",
        "sector": "Other / Unclassified",
        "industry": "Other / Unclassified",
        "source": "fallback",
        "url": "",
    }


def cached_lookthrough(
    original: Callable[[str, str], dict[str, Any]],
    symbol: str,
    security_type: str = "",
) -> dict[str, Any]:
    """Use fresh ETF sector weights when valid; otherwise serve the last good map."""
    key = _key(symbol, security_type)
    state = _cache_state()

    with state["lock"]:
        saved_entry = deepcopy(state["data"].get("lookthrough", {}).get(key))

    try:
        live = original(symbol, security_type)
    except Exception:
        live = None

    if _lookthrough_is_usable(live):
        entry = {"saved_at": time.time(), "value": deepcopy(live)}
        with state["lock"]:
            state["data"].setdefault("lookthrough", {})[key] = entry
            _persist_locked(state["data"])
        return live

    if isinstance(saved_entry, dict) and _lookthrough_is_usable(saved_entry.get("value")):
        return _with_cache_source(saved_entry["value"], "STALE CACHE")

    return live if isinstance(live, dict) else {"weights": {}, "source": "fallback"}


def cache_status() -> dict[str, int]:
    state = _cache_state()
    with state["lock"]:
        return {
            "classifications": len(state["data"].get("classification", {})),
            "lookthroughs": len(state["data"].get("lookthrough", {})),
        }
