"""Smart ticker autocomplete + local lookup-frequency cache for Raj's Terminal.

The autocomplete universe is sourced from Nasdaq Trader's public symbol
directories and cached for 24 hours. Successful lookups are ranked by frequency
and recency so commonly used symbols appear first on subsequent visits.
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
    # Nasdaq Trader appends security-class descriptors to many stock names.
    # Keep useful ETF/fund wording but trim repetitive common-stock suffixes.
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
    """Load lookup history from session first, then the local persistent cache."""
    session_value = st.session_state.get("_raj_ticker_history")
    if isinstance(session_value, dict):
        return session_value

    with _LOCK:
        value = _read_json(_HISTORY_CACHE, {})
    history = value if isinstance(value, dict) else {}
    st.session_state["_raj_ticker_history"] = history
    return history


def record_lookup(symbol: str, company_name: str = "") -> None:
    """Increment a successful ticker lookup and persist its latest display name."""
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


def _ranked_symbols(current: str = "") -> list[str]:
    directory = ticker_directory()
    history = lookup_history()

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


def _display_label(symbol: str) -> str:
    symbol = str(symbol or "").strip().upper()
    name = company_name(symbol)
    row = lookup_history().get(symbol) or {}
    try:
        count = int(row.get("count") or 0) if isinstance(row, dict) else 0
    except (TypeError, ValueError):
        count = 0

    suffix = f"  // {count} LOOKUPS" if count else ""
    return f"{symbol} — {name}{suffix}" if name else f"{symbol}{suffix}"


def smart_ticker_selector(
    original_selectbox,
    *,
    label: str = "Ticker",
    current: str = "SPY",
    key: str = "risk_ticker_smart_selector",
    help_text: str = "",
) -> str:
    """Render a searchable ticker/company selector with frequent symbols first."""
    current = str(current or "SPY").strip().upper() or "SPY"
    options = _ranked_symbols(current)
    if current not in options:
        options.insert(0, current)

    try:
        current_index = options.index(current)
    except ValueError:
        current_index = 0

    selected = original_selectbox(
        label,
        options,
        index=current_index,
        format_func=_display_label,
        key=key,
        help=(
            help_text
            or "Start typing a ticker OR company name. Successful quote lookups are remembered and your most-used symbols are ranked first."
        ),
        placeholder="Type ticker or company name...",
        accept_new_options=True,
    )

    raw = str(selected or current).strip()
    raw_upper = raw.upper()
    directory = ticker_directory()
    if raw_upper in directory or raw_upper in lookup_history():
        return raw_upper

    # If a user enters a company name manually instead of selecting a suggestion,
    # resolve an exact/unique company-name match before falling back to the raw symbol.
    needle = raw.casefold()
    matches = [
        symbol
        for symbol, name in directory.items()
        if needle and needle == str(name).casefold()
    ]
    if len(matches) == 1:
        return matches[0]

    return raw_upper
