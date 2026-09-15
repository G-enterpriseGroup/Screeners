"""Fresh loader for the full E*TRADE GEX workspace.

The GEX UI is a Streamlit fragment. Widget clicks inside that fragment rerun only
the fragment, so stack-based discovery of the terminal's E*TRADE client can lose
the caller context after the first render. This loader captures the terminal
context during the normal app run and keeps it in a process-memory vault keyed
by the terminal vault key. The fragment then reads that stored context on every
partial rerun, so REFRESH ALL GEX / REFRESH <TICKER> keep using the live cached
E*TRADE client and force-refresh option chains correctly.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from typing import Any

import streamlit as st

import src.gex_ui as _gex_ui


importlib.invalidate_caches()
_gex_ui = importlib.reload(_gex_ui)
_fragment_render_gex = _gex_ui.render_gex


@st.cache_resource
def _terminal_context_vault() -> dict[str, dict[str, Any]]:
    return {}


def _discover_terminal_context() -> tuple[Any, str, Any]:
    """Capture the terminal context while the full Streamlit script is running."""
    candidates: list[dict[str, Any]] = []
    main = sys.modules.get("__main__")
    if main is not None:
        candidates.append(vars(main))
    for frame in inspect.stack()[1:18]:
        candidates.append(frame.frame.f_globals)

    for scope in candidates:
        # GEX refreshes should use LIVE E*TRADE when the entrypoint exposes it.
        # Falling back to _etrade_client keeps compatibility with older builds.
        live_factory = scope.get("_live_etrade_client")
        factory = live_factory if callable(live_factory) else scope.get("_etrade_client")
        hash_fn = scope.get("_trade_access_code_hash")
        touch = scope.get("_touch_etrade_session")
        if not callable(factory):
            continue

        try:
            client = factory()
        except Exception:
            client = None
        try:
            vault_key = str(hash_fn()) if callable(hash_fn) else "default"
        except Exception:
            vault_key = "default"
        return client, vault_key or "default", touch

    return None, "default", None


def _stored_terminal_context() -> tuple[Any, str, Any]:
    """Context resolver used by gex_ui during both full and fragment reruns."""
    vault_key = str(st.session_state.get("_gex_terminal_vault_key") or "default")
    row = _terminal_context_vault().get(vault_key) or {}
    return row.get("client"), vault_key, row.get("touch")


# Important: this replacement stays installed after this wrapper returns.
# On a later fragment-only rerun, gex_ui therefore does not need the original
# streamlit_app.py call stack to find the E*TRADE client again.
_gex_ui._terminal_context = _stored_terminal_context


def render_gex() -> None:
    client, vault_key, touch = _discover_terminal_context()
    vault_key = str(vault_key or "default")
    _terminal_context_vault()[vault_key] = {
        "client": client,
        "touch": touch,
    }
    st.session_state["_gex_terminal_vault_key"] = vault_key

    st.caption("GEX LOADER // LIVE FRAGMENT CONTEXT V3 // REFRESH BUTTONS USE LIVE E*TRADE")
    _fragment_render_gex()


__all__ = ["render_gex"]
