"""Server-memory E*TRADE session persistence for Raj's Terminal.

This keeps an already-authorized E*TRADE OAuth session alive across a browser
page refresh while the Streamlit app process is still running. The user still
must unlock Raj's Terminal with the access code after a refresh. The OAuth
session is restored only when its existing inactivity/hard-expiry timer has not
expired.

When a new authorization is required, this module also prepares the OAuth
request immediately after Raj's Terminal is unlocked. That lets the main
CONNECT E*TRADE control be the actual E*TRADE login link, eliminating the old
extra "OPEN E*TRADE LOGIN" click while preserving the verification-code step.

Nothing is written to GitHub or browser storage. A Streamlit app restart or
redeploy intentionally clears the in-memory OAuth vault and requires E*TRADE
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


def _etrade_secret(name: str, default: str = "") -> str:
    """Read one E*TRADE secret without exposing it to the browser."""
    try:
        group = st.secrets.get("etrade", {})
        getter = getattr(group, "get", None)
        if callable(getter):
            value = getter(name, default)
        else:
            value = group[name] if name in group else default
        return str(value or default).strip()
    except Exception:
        return str(default or "").strip()


def _install_direct_connect_patch() -> None:
    """Turn the existing CONNECT control into the real E*TRADE login link.

    terminal_core owns the connection UI. Rather than duplicating that UI, we
    intercept only its two authorization controls:
      * CONNECT E*TRADE becomes a direct link to the prepared OAuth URL.
      * the now-redundant second OPEN E*TRADE LOGIN link is replaced by a short
        status caption, while the verifier input remains exactly where it is.

    Everything else in Streamlit continues through the original functions.
    """
    if getattr(st, "_raj_direct_etrade_connect_patch", False):
        return

    original_button = st.button
    original_link_button = st.link_button

    def direct_button(label, *args, **kwargs):
        key = kwargs.get("key")
        request = st.session_state.get("etrade_request") or {}
        authorization_url = str(request.get("authorization_url") or "").strip()
        direct_ready = bool(
            st.session_state.get("_raj_direct_etrade_auth_ready", False)
            and authorization_url
            and not st.session_state.get("etrade_access_token")
        )

        if key == "etrade_connect" and direct_ready:
            # Use Streamlit's native link control so the browser treats this as
            # a user-initiated navigation (reliable and popup-blocker safe).
            original_link_button(
                str(label),
                authorization_url,
                type=kwargs.get("type", "secondary"),
                width=kwargs.get("width", "content"),
                disabled=bool(kwargs.get("disabled", False)),
            )
            # Returning False prevents terminal_core from starting a second
            # OAuth request on the same interaction.
            return False

        return original_button(label, *args, **kwargs)

    def direct_link_button(label, url, *args, **kwargs):
        direct_ready = bool(
            st.session_state.get("_raj_direct_etrade_auth_ready", False)
            and (st.session_state.get("etrade_request") or {}).get("authorization_url")
            and not st.session_state.get("etrade_access_token")
        )
        if str(label).strip() == "1 // OPEN E*TRADE LOGIN" and direct_ready:
            st.caption("1 // E*TRADE LOGIN OPENS DIRECTLY FROM CONNECT E*TRADE ABOVE")
            return None
        return original_link_button(label, url, *args, **kwargs)

    st.button = direct_button
    st.link_button = direct_link_button
    st._raj_direct_etrade_connect_patch = True


def _prepare_direct_etrade_login() -> bool:
    """Prepare a request token so CONNECT E*TRADE can open login in one click."""
    if not bool(st.session_state.get("etrade_access_unlocked", False)):
        return False
    if st.session_state.get("etrade_access_token"):
        return False

    existing = st.session_state.get("etrade_request") or {}
    if existing.get("authorization_url"):
        st.session_state["_raj_direct_etrade_auth_ready"] = True
        _install_direct_connect_patch()
        return True

    consumer_key = _etrade_secret("consumer_key")
    consumer_secret = _etrade_secret("consumer_secret")
    environment = _etrade_secret("environment", "live").lower() or "live"
    if not consumer_key or not consumer_secret:
        st.session_state["_raj_direct_etrade_auth_ready"] = False
        return False

    try:
        # Local import avoids coupling the persistence module to the E*TRADE
        # client until an unlocked session actually needs authorization.
        from src.etrade_client import begin_authorization

        request = begin_authorization(consumer_key, consumer_secret, environment)
        st.session_state["etrade_request"] = {
            "oauth_token": request.oauth_token,
            "oauth_token_secret": request.oauth_token_secret,
            "authorization_url": request.authorization_url,
        }
        st.session_state["_raj_direct_etrade_auth_ready"] = True
        _install_direct_connect_patch()
        return True
    except Exception as exc:
        # Preserve the legacy two-step flow as a safe fallback if E*TRADE is
        # temporarily unable to issue the request token.
        st.session_state["_raj_direct_etrade_auth_ready"] = False
        st.session_state["_raj_direct_etrade_auth_error"] = str(exc)[:240]
        return False


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
    """Restore a valid OAuth session, otherwise prime the one-click login flow."""
    vault = _etrade_session_vault()
    saved = vault.get(str(vault_key))
    if not saved:
        _prepare_direct_etrade_login()
        return None

    token = dict(saved.get("token") or {})
    if not token:
        vault.pop(str(vault_key), None)
        _prepare_direct_etrade_login()
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
        _prepare_direct_etrade_login()
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
    st.session_state.pop("_raj_direct_etrade_auth_ready", None)
    st.session_state.pop("_raj_direct_etrade_auth_error", None)
