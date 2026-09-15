"""Smart ticker autocomplete + local lookup-frequency cache for Raj's Terminal.

The autocomplete universe is sourced from Nasdaq Trader's public symbol
directories and cached for 24 hours. Successful lookups are ranked by frequency
and recency so commonly used symbols appear first on subsequent visits.

Important performance rule: the selector builds its display labels from ONE
snapshot of the directory/history.  It never calls cached lookup functions once
per option.  This keeps a 10k+ symbol selector fast enough for Streamlit and
prevents the Risk Sizing section from appearing blank while labels are built.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests
import streamlit as st


_CACHE_DIR = Path.home() / ".raj_terminal"
_DIRECTORY_CACHE = _CACHE_DIR / "ticker_directory.json"
_HISTORY_CACHE = _CACHE_DIR / "ticker_history.json"
_LOCK = threading.Lock()

_NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

_FALLBACK_NAMES = {
    "AAPL": "Apple Inc.",
    "AMZN": "Amazon.com, Inc.",
    "BAC": "Bank of America Corporation",
    "GOOG": "Alphabet Inc.",
    "GOOGL": "Alphabet Inc.",
    "GS": "Goldman Sachs Group, Inc.",
    "IWM": "iShares Russell 2000 ETF",
    "LMT": "Lockheed Martin Corporation",
    "META": "Meta Platforms, Inc.",
    "MS": "Morgan Stanley",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "ORCL": "Oracle Corporation",
    "PAVE": "Global X U.S. Infrastructure Development ETF",
    "QQQ": "Invesco QQQ Trust",
    "SGOL": "abrdn Physical Gold Shares ETF",
    "SPY": "SPDR S&P 500 ETF Trust",
    "SPYM": "SPDR Portfolio S&P 500 ETF",
    "SPMO": "Invesco S&P 500 Momentum ETF",
    "TSM": "Taiwan Semiconductor Manufacturing Company Limited",
    "XLF": "Financial Select Sector SPDR Fund",
}


def _ensure_cache_dir() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    _ensure_cache_dir()
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _clean_security_name(value: str) -> str:
    name = " ".join(str(value or "").split()).strip()
    for suffix in (
        " - Common Stock",
        " - Class A Common Stock",
        " - Class B Common Stock",
        " - Ordinary Shares",
        " - American Depositary Shares",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


def _parse_directory(text: str, symbol_field: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    for row in reader:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get(symbol_field) or "").strip().upper()
        if not symbol or symbol.startswith("FILE CREATION TIME"):
            continue
        if str(row.get("Test Issue") or "").strip().upper() == "Y":
            continue
        name = _clean_security_name(str(row.get("Security Name") or ""))
        if name:
            mapping[symbol] = name
    return mapping


@st.cache_data(ttl=86400, show_spinner=False)
def ticker_directory() -> dict[str, str]:
    """Return a cached US ticker->security-name mapping with stale fallback."""
    mapping = dict(_FALLBACK_NAMES)
    downloaded: dict[str, str] = {}
    headers = {
        "User-Agent": "RajsTerminal/1.0 symbol-directory cache",
        "Accept": "text/plain,*/*",
    }

    for url, field in (
        (_NASDAQ_LISTED_URL, "Symbol"),
        (_OTHER_LISTED_URL, "ACT Symbol"),
    ):
        try:
            response = requests.get(url, timeout=6, headers=headers)
            response.raise_for_status()
            downloaded.update(_parse_directory(response.text, field))
        except requests.RequestException:
            continue

    if len(downloaded) >= 500:
        mapping.update(downloaded)
        with _LOCK:
            _write_json(_DIRECTORY_CACHE, mapping)
        return mapping

    with _LOCK:
        stale = _read_json(_DIRECTORY_CACHE, {})
    if isinstance(stale, dict):
        mapping.update({str(k).upper(): str(v) for k, v in stale.items() if k and v})
    return mapping


def lookup_history() -> dict[str, dict[str, Any]]:
    session_value = st.session_state.get("_raj_ticker_history")
    if isinstance(session_value, dict):
        return session_value

    with _LOCK:
        value = _read_json(_HISTORY_CACHE, {})
    history = value if isinstance(value, dict) else {}
    st.session_state["_raj_ticker_history"] = history
    return history


def record_lookup(symbol: str, company_name: str = "") -> None:
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return

    history = dict(lookup_history())
    row = dict(history.get(symbol) or {})
    try:
        count = int(row.get("count") or 0)
    except (TypeError, ValueError):
        count = 0

    directory = ticker_directory()
    name = str(company_name or row.get("name") or directory.get(symbol) or "").strip()
    history[symbol] = {
        "count": count + 1,
        "last_used": time.time(),
        "name": name,
    }
    st.session_state["_raj_ticker_history"] = history
    with _LOCK:
        _write_json(_HISTORY_CACHE, history)


def company_name(symbol: str) -> str:
    symbol = str(symbol or "").strip().upper()
    history = lookup_history()
    cached = history.get(symbol) if isinstance(history, dict) else None
    if isinstance(cached, dict) and cached.get("name"):
        return str(cached["name"])
    return str(ticker_directory().get(symbol) or "")


def _ranked_symbols_from_snapshots(
    directory: dict[str, str],
    history: dict[str, dict[str, Any]],
    current: str = "",
) -> list[str]:
    frequent = sorted(
        (
            (symbol, data)
            for symbol, data in history.items()
            if isinstance(data, dict) and symbol
        ),
        key=lambda item: (
            -int(item[1].get("count") or 0),
            -float(item[1].get("last_used") or 0.0),
            str(item[0]),
        ),
    )

    ordered: list[str] = []
    seen: set[str] = set()

    for symbol, _ in frequent:
        symbol = str(symbol).strip().upper()
        if symbol and symbol not in seen:
            ordered.append(symbol)
            seen.add(symbol)

    current = str(current or "").strip().upper()
    if current and current not in seen:
        ordered.append(current)
        seen.add(current)

    for symbol in sorted(directory):
        if symbol not in seen:
            ordered.append(symbol)
            seen.add(symbol)

    return ordered


def _ranked_symbols(current: str = "") -> list[str]:
    return _ranked_symbols_from_snapshots(ticker_directory(), lookup_history(), current)


def _display_label(symbol: str) -> str:
    """Compatibility formatter for any older callers."""
    symbol = str(symbol or "").strip().upper()
    directory = ticker_directory()
    history = lookup_history()
    row = history.get(symbol) or {}
    name = str((row.get("name") if isinstance(row, dict) else "") or directory.get(symbol) or "").strip()
    try:
        count = int(row.get("count") or 0) if isinstance(row, dict) else 0
    except (TypeError, ValueError):
        count = 0
    suffix = f"  // {count} LOOKUPS" if count else ""
    return f"{symbol} — {name}{suffix}" if name else f"{symbol}{suffix}"


def _build_fast_labels(current: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Build display labels once; return labels + label->symbol + directory."""
    directory = ticker_directory()
    history = lookup_history()
    symbols = _ranked_symbols_from_snapshots(directory, history, current)

    labels: list[str] = []
    label_to_symbol: dict[str, str] = {}
    for symbol in symbols:
        row = history.get(symbol) or {}
        cached_name = str(row.get("name") or "").strip() if isinstance(row, dict) else ""
        name = cached_name or str(directory.get(symbol) or "").strip()
        label = f"{symbol} — {name}" if name else symbol
        labels.append(label)
        label_to_symbol[label] = symbol
    return labels, label_to_symbol, directory


def smart_ticker_selector(
    original_selectbox,
    *,
    label: str = "Ticker",
    current: str = "SPY",
    key: str = "risk_ticker_smart_selector",
    help_text: str = "",
) -> str:
    """Fast searchable ticker/company selector.

    Streamlit's selectbox provides the keyboard search.  Options are literal
    `SYMBOL — COMPANY NAME` strings, so typing either the ticker or company name
    filters immediately in the browser.  No expensive per-option format_func is
    used.
    """
    current = str(current or "SPY").strip().upper() or "SPY"
    labels, label_to_symbol, directory = _build_fast_labels(current)

    current_label = next(
        (label for label, symbol in label_to_symbol.items() if symbol == current),
        current,
    )
    try:
        current_index = labels.index(current_label)
    except ValueError:
        labels.insert(0, current_label)
        label_to_symbol[current_label] = current
        current_index = 0

    selected = original_selectbox(
        label,
        labels,
        index=current_index,
        key=key,
        help=(
            help_text
            or "Start typing a ticker OR company name. Choose a result to load it."
        ),
        placeholder="Type ticker or company name...",
        accept_new_options=True,
    )

    raw = str(selected or current_label).strip()
    if raw in label_to_symbol:
        return label_to_symbol[raw]

    # New/manual text may be either SYMBOL, `SYMBOL — NAME`, or exact company name.
    symbol_part = raw.split(" — ", 1)[0].strip().upper()
    if symbol_part in directory or symbol_part in lookup_history():
        return symbol_part

    needle = raw.casefold()
    exact_matches = [
        symbol for symbol, name in directory.items()
        if needle and needle == str(name).casefold()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    return symbol_part or current
