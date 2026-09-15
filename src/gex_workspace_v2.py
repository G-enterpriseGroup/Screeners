"""GEX loader for Raj's Terminal.

This module routes the GEX terminal tab to the compact multi-ticker v3
workspace and captures the existing terminal E*TRADE context during the normal
Streamlit run. It also suppresses three temporary top-of-terminal diagnostic
captions that are no longer wanted in the production UI.
"""

from __future__ import annotations

import inspect
import sys
from typing import Any

import streamlit as st

from src.gex_ui_v3 import render_gex as _render_gex_v3


# Remove temporary production clutter without hiding useful captions elsewhere.
if not hasattr(st, "_raj_terminal_original_caption"):
    st._raj_terminal_original_caption = st.caption

_ORIGINAL_CAPTION = st._raj_terminal_original_caption


def _production_caption(body, *args, **kwargs):
    text = str(body or "").strip()
    hidden_prefixes = (
        "BUILD // RISK-V7 DIRECT",
        "Bloomberg-style municipal analytics",
        "SMART CACHE // SHARED ACROSS ALL TABS",
    )
    if any(text.startswith(prefix) for prefix in hidden_prefixes):
        return None
    return _ORIGINAL_CAPTION(body, *args, **kwargs)


st.caption = _production_caption


def _discover_terminal_context() -> tuple[Any, str, Any]:
    """Resolve the live terminal client, access-code vault key, and touch hook."""
    candidates: list[dict[str, Any]] = []
    main = sys.modules.get("__main__")
    if main is not None:
        candidates.append(vars(main))
    for frame in inspect.stack()[1:18]:
        candidates.append(frame.frame.f_globals)

    for scope in candidates:
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


def render_gex() -> None:
    client, vault_key, touch = _discover_terminal_context()
    _render_gex_v3(client, vault_key, touch)


__all__ = ["render_gex"]
