"""Server-memory E*TRADE session persistence for Raj's Terminal.

This keeps an already-authorized E*TRADE OAuth session alive across a browser
page refresh while the Streamlit app process is still running. The user still
must unlock Raj's Terminal with the access code after a refresh. The OAuth
session is restored only when its existing inactivity/hard-expiry timer has not
expired.

Nothing is written to GitHub or browser storage. A Streamlit app restart or
redeploy intentionally clears this in-memory vault and requires E*TRADE
authorization again.
"""

from __future__ import annotations

import time
from datetime import datetime, time as datetime_time, timedelta
from typing import Any

import streamlit as st


@st.cache_resource
def _etrade_session_vault() -> dict[str, dict[str, Any]]:
    """Process-local vault shared across Streamlit page refreshes."""
    return {}


def _expiry_times(
    token: dict[str, Any],
    last_activity_at: float,
    inactivity_seconds: int,
    timezone,
) -> tuple[float, float]:
    now = time.time()
    issued_at = float(token.get("issued_at", now) or now)
    issued_et = datetime.fromtimestamp(issued_at, timezone)
    midnight_et = datetime.combine(
        issued_et.date() + timedelta(days=1),
        datetime_time.min,
        tzinfo=timezone,
    )
    inactivity_expiry = float(last_activity_at) + int(inactivity_seconds)
    hard_expiry = midnight_et.timestamp()
    return min(inactivity_expiry, hard_expiry), hard_expiry


def save_etrade_session(
    vault_key: str,
    *,
    token: dict[str, Any] | None,
    last_activity_at: float | None,
    inactivity_seconds: int,
    timezone,
    accounts: list[dict[str, Any]] | None = None,
) -> bool:
    """Save an active OAuth session in process memory without extending it."""
    if not token:
        return False

    now = time.time()
    token_copy = dict(token)
    issued_at = float(token_copy.get("issued_at", now) or now)
    token_copy["issued_at"] = issued_at
    activity = float(last_activity_at or issued_at)
    expiry, hard_expiry = _expiry_times(
        token_copy,
        activity,
        inactivity_seconds,
        timezone,
    )

    vault = _etrade_session_vault()
    if now >= expiry or now >= hard_expiry:
        vault.pop(str(vault_key), None)
        return False

    vault[str(vault_key)] = {
        "token": token_copy,
        "last_activity_at": activity,
        "accounts": [dict(account) for account in (accounts or [])],
        "saved_at": now,
    }
    return True


def restore_etrade_session(
    vault_key: str,
    *,
    inactivity_seconds: int,
    timezone,
) -> dict[str, Any] | None:
    """Return the saved session only when its original timer is still active."""
    vault = _etrade_session_vault()
    saved = vault.get(str(vault_key))
    if not saved:
        return None

    token = dict(saved.get("token") or {})
    if not token:
        vault.pop(str(vault_key), None)
        return None

    now = time.time()
    issued_at = float(token.get("issued_at", now) or now)
    activity = float(saved.get("last_activity_at", issued_at) or issued_at)
    expiry, hard_expiry = _expiry_times(
        token,
        activity,
        inactivity_seconds,
        timezone,
    )
    if now >= expiry or now >= hard_expiry:
        vault.pop(str(vault_key), None)
        return None

    return {
        "token": token,
        "last_activity_at": activity,
        "accounts": [dict(account) for account in (saved.get("accounts") or [])],
        "expires_at": expiry,
        "hard_expires_at": hard_expiry,
    }


def clear_etrade_session(vault_key: str) -> None:
    """Forget the cached OAuth session after an explicit disconnect."""
    _etrade_session_vault().pop(str(vault_key), None)
