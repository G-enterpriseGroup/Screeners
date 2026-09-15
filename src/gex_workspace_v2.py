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


def _render_gex_subtab_skin() -> None:
    """Make Streamlit tabs look like unmistakable terminal sub-tabs.

    This CSS is emitted only while the GEX renderer is active, so the second
    navigation row appears only inside the GEX terminal tab. It also styles the
    nested RAW STRIKES tabs consistently.
    """
    st.markdown(
        """
        <style>
        /* GEX SECONDARY NAVIGATION */
        div[data-testid="stTabs"] > div[data-baseweb="tab-list"] {
            gap: .42rem !important;
            padding: .34rem 0 .48rem 0 !important;
            margin: .20rem 0 .58rem 0 !important;
            border-bottom: 1px solid #fb8b1e !important;
            overflow-x: visible !important;
            flex-wrap: wrap !important;
        }

        div[data-testid="stTabs"] button[role="tab"] {
            flex: 0 0 auto !important;
            min-width: 118px !important;
            min-height: 38px !important;
            padding: .46rem .82rem !important;
            margin: 0 !important;
            border: 1px solid #fb8b1e !important;
            border-radius: 0 !important;
            background: #050505 !important;
            color: #fb8b1e !important;
            box-shadow: none !important;
            font-family: "Courier New", monospace !important;
            font-size: .74rem !important;
            font-weight: 900 !important;
            letter-spacing: .02em !important;
            text-transform: uppercase !important;
        }

        div[data-testid="stTabs"] button[role="tab"]:hover {
            background: #171007 !important;
            color: #ffad52 !important;
        }

        div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            background: #fb8b1e !important;
            color: #000000 !important;
            border-color: #fb8b1e !important;
            box-shadow: inset 0 -3px 0 #a64b00 !important;
        }

        div[data-testid="stTabs"] button[role="tab"] * {
            color: inherit !important;
            font-family: "Courier New", monospace !important;
            font-weight: 900 !important;
        }

        /* Hide Streamlit's tiny default active underline: the orange fill is
           now the active-state indicator. */
        div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {
            display: none !important;
        }

        /* Keep the tab content aligned directly beneath the subnav. */
        div[data-testid="stTabs"] div[data-baseweb="tab-panel"] {
            padding-top: .22rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
    _render_gex_subtab_skin()
    _render_gex_v3(client, vault_key, touch)


__all__ = ["render_gex"]
