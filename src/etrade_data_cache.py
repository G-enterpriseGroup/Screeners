"""Shared process-memory cache for Raj's Terminal E*TRADE reads.

The cache is intentionally server-memory only: nothing is written to GitHub,
browser storage, or disk. Entries are namespaced by a SHA-256 digest of the
active OAuth token so data from different E*TRADE sessions cannot share keys.

This cache sits beneath every terminal tab. A quote fetched in Orders can be
reused by Risk Sizing or Bull Debit Spread; holdings/balances loaded in one tab
can be reused by the others. The process-local cache also survives a browser
refresh while the Streamlit process and OAuth session remain alive.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from typing import Any, Callable

import streamlit as st


# Deliberately short for price-sensitive data and long for static metadata.
CACHE_TTLS = {
    "accounts": 15 * 60,
    "balance": 30,
    "portfolio": 90,
    "quote": 5,
    "option_expirations": 6 * 60 * 60,
    "option_chain": 5 * 60,
}

# Old expired objects are pruned eventually even if that exact key is never
# requested again. This prevents large option-chain scans from growing memory.
CACHE_RETENTION_SECONDS = 24 * 60 * 60
MAX_ENTRIES_PER_SESSION = 500


@st.cache_resource
def _cache_vault() -> dict[str, Any]:
    return {
        "entries": {},
        "stats": {},
        "lock": threading.RLock(),
    }


def session_namespace(token: dict[str, Any] | None) -> str:
    """Return a non-reversible cache namespace for the active OAuth token."""
    oauth_token = str((token or {}).get("oauth_token") or "").strip()
    if not oauth_token:
        return ""
    return hashlib.sha256(oauth_token.encode("utf-8")).hexdigest()[:32]


def _copy(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _stats(namespace: str) -> dict[str, int]:
    vault = _cache_vault()
    return vault["stats"].setdefault(
        namespace,
        {"hits": 0, "misses": 0, "api_calls": 0},
    )


def _prune_locked(namespace: str, now: float) -> None:
    vault = _cache_vault()
    entries = vault["entries"]
    namespace_keys = [key for key in entries if key[0] == namespace]

    for key in namespace_keys:
        loaded_at = float(entries[key].get("loaded_at", 0.0) or 0.0)
        if now - loaded_at > CACHE_RETENTION_SECONDS:
            entries.pop(key, None)

    namespace_keys = [key for key in entries if key[0] == namespace]
    if len(namespace_keys) <= MAX_ENTRIES_PER_SESSION:
        return

    namespace_keys.sort(
        key=lambda key: float(entries[key].get("loaded_at", 0.0) or 0.0)
    )
    overflow = len(namespace_keys) - MAX_ENTRIES_PER_SESSION
    for key in namespace_keys[:overflow]:
        entries.pop(key, None)


def clear_session_cache(token: dict[str, Any] | None) -> None:
    namespace = session_namespace(token)
    if not namespace:
        return
    vault = _cache_vault()
    with vault["lock"]:
        for key in [key for key in vault["entries"] if key[0] == namespace]:
            vault["entries"].pop(key, None)
        vault["stats"].pop(namespace, None)


def invalidate_session_cache(
    token: dict[str, Any] | None,
    *resource_types: str,
) -> None:
    namespace = session_namespace(token)
    if not namespace:
        return
    wanted = {str(value) for value in resource_types if value}
    vault = _cache_vault()
    with vault["lock"]:
        for key in list(vault["entries"]):
            if key[0] != namespace:
                continue
            if not wanted or key[1] in wanted:
                vault["entries"].pop(key, None)


def cache_stats(token: dict[str, Any] | None) -> dict[str, int]:
    namespace = session_namespace(token)
    if not namespace:
        return {"hits": 0, "misses": 0, "api_calls": 0, "entries": 0}
    vault = _cache_vault()
    with vault["lock"]:
        values = dict(_stats(namespace))
        values["entries"] = sum(
            1 for key in vault["entries"] if key[0] == namespace
        )
    return values


class CachedETradeClient:
    """Transparent ETradeClient-compatible cache shared by every terminal tab."""

    def __init__(self, client: Any, token: dict[str, Any] | None) -> None:
        self._client = client
        self._namespace = session_namespace(token)

    def _manual_force(self, resource_type: str) -> bool:
        """Honor explicit user refresh buttons while normal reruns use cache."""
        if resource_type not in {"portfolio", "balance"}:
            return False
        return bool(
            st.session_state.get("refresh_holdings", False)
            or st.session_state.get("risk_refresh_portfolio", False)
        )

    def _cached(
        self,
        resource_type: str,
        resource_key: str,
        loader: Callable[[], Any],
        *,
        force_refresh: bool = False,
    ) -> Any:
        if not self._namespace:
            return loader()

        ttl = int(CACHE_TTLS[resource_type])
        now = time.time()
        key = (self._namespace, resource_type, str(resource_key))
        vault = _cache_vault()
        force_refresh = bool(force_refresh or self._manual_force(resource_type))

        with vault["lock"]:
            _prune_locked(self._namespace, now)
            entry = vault["entries"].get(key)
            if not force_refresh and entry is not None:
                age = now - float(entry.get("loaded_at", 0.0) or 0.0)
                if age < ttl:
                    _stats(self._namespace)["hits"] += 1
                    st.session_state["_etrade_cache_last_event"] = {
                        "resource": resource_type,
                        "hit": True,
                        "age": max(0.0, age),
                    }
                    return _copy(entry.get("value"))
            _stats(self._namespace)["misses"] += 1

        # Never hold the shared lock during a network request.
        value = loader()
        loaded_at = time.time()
        with vault["lock"]:
            vault["entries"][key] = {
                "loaded_at": loaded_at,
                "value": _copy(value),
            }
            _stats(self._namespace)["api_calls"] += 1
            st.session_state["_etrade_cache_last_event"] = {
                "resource": resource_type,
                "hit": False,
                "age": 0.0,
            }
        return _copy(value)

    def cache_age(self, resource_type: str, resource_key: str = "") -> float | None:
        key = (self._namespace, str(resource_type), str(resource_key))
        vault = _cache_vault()
        with vault["lock"]:
            entry = vault["entries"].get(key)
            if not entry:
                return None
            return max(0.0, time.time() - float(entry.get("loaded_at", 0.0) or 0.0))

    def invalidate(self, *resource_types: str) -> None:
        if not self._namespace:
            return
        vault = _cache_vault()
        wanted = {str(value) for value in resource_types if value}
        with vault["lock"]:
            for key in list(vault["entries"]):
                if key[0] != self._namespace:
                    continue
                if not wanted or key[1] in wanted:
                    vault["entries"].pop(key, None)

    def renew(self) -> None:
        return self._client.renew()

    def list_accounts(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        return self._cached(
            "accounts",
            "all",
            self._client.list_accounts,
            force_refresh=force_refresh,
        )

    def get_balance(
        self,
        account_id_key: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        account_id_key = str(account_id_key)
        return self._cached(
            "balance",
            account_id_key,
            lambda: self._client.get_balance(account_id_key),
            force_refresh=force_refresh,
        )

    def get_portfolio(
        self,
        account_id_key: str,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        account_id_key = str(account_id_key)
        return self._cached(
            "portfolio",
            account_id_key,
            lambda: self._client.get_portfolio(account_id_key),
            force_refresh=force_refresh,
        )

    def get_quote(self, symbol: str, force_refresh: bool = False) -> dict[str, Any]:
        symbol = str(symbol).strip().upper()
        return self._cached(
            "quote",
            symbol,
            lambda: self._client.get_quote(symbol),
            force_refresh=force_refresh,
        )

    def get_option_expirations(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        symbol = str(symbol).strip().upper()
        return self._cached(
            "option_expirations",
            symbol,
            lambda: self._client.get_option_expirations(symbol),
            force_refresh=force_refresh,
        )

    def get_option_chain(
        self,
        symbol: str,
        expiry_year: int,
        expiry_month: int,
        expiry_day: int,
        no_of_strikes: int | None = 100,
        chain_type: str = "CALLPUT",
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        symbol = str(symbol).strip().upper()
        chain_type = str(chain_type).upper()
        strikes_key = "ALL" if no_of_strikes is None else str(int(no_of_strikes))
        resource_key = (
            f"{symbol}:{int(expiry_year):04d}-{int(expiry_month):02d}-{int(expiry_day):02d}:"
            f"{chain_type}:{strikes_key}"
        )
        return self._cached(
            "option_chain",
            resource_key,
            lambda: self._client.get_option_chain(
                symbol,
                expiry_year,
                expiry_month,
                expiry_day,
                no_of_strikes=no_of_strikes,
                chain_type=chain_type,
            ),
            force_refresh=force_refresh,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
