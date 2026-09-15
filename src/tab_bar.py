"""Clickable + draggable terminal tab bar for Raj's Terminal.

The browser component applies tab selection/reordering optimistically, while the
Python side persists that state immediately. We intentionally avoid a second
explicit st.rerun() after the component event so each click/drag produces only
the single Streamlit rerun that the component itself already triggers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import streamlit as st
import streamlit.components.v1 as components

from src.layout_guardrails import install_layout_guardrails


DEFAULT_TAB_ORDER = [
    "HOLDINGS",
    "RISK SIZING",
    "GEX",
    "BULL DEBIT SPREAD",
    "MUNI SCREENERS",
    "ORDERS",
]

_COMPONENT_PATH = Path(__file__).parent / "components" / "terminal_tabs_v3"
_terminal_tabs = components.declare_component(
    "raj_terminal_tabs_v3",
    path=str(_COMPONENT_PATH),
)


# Install after the legacy terminal-core stylesheet so anti-overlap geometry and
# icon-font restoration win the cascade across every tab.
install_layout_guardrails()


# This module is imported after the legacy terminal-core stylesheet, so keep
# terminal-wide table-header overrides here. All table/data-grid headers use
# the same solid Bloomberg-orange band with heavy black mono type as the
# exposure-panel headers instead of the old black/orange-outline treatment.
# IMPORTANT: do not force Courier New on every descendant; Streamlit can use
# Material icon spans inside headers, and overriding their font turns icon names
# into visible text that can overlap the label.
st.markdown(
    """
    <style>
    [data-testid="stDataFrame"] [role="columnheader"],
    [data-testid="stDataEditor"] [role="columnheader"] {
        background:#fb8b1e !important;
        color:#000 !important;
        -webkit-text-fill-color:#000 !important;
        border-color:#000 !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        text-transform:uppercase !important;
        min-width:0 !important;
    }

    [data-testid="stDataFrame"] [role="columnheader"] *,
    [data-testid="stDataEditor"] [role="columnheader"] * {
        color:#000 !important;
        -webkit-text-fill-color:#000 !important;
        font-weight:900 !important;
        min-width:0 !important;
    }

    [data-testid="stTable"] thead,
    [data-testid="stTable"] thead tr,
    [data-testid="stTable"] thead tr th {
        background:#fb8b1e !important;
        color:#000 !important;
        -webkit-text-fill-color:#000 !important;
        border-color:#000 !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        text-transform:uppercase !important;
        min-width:0 !important;
        overflow-wrap:anywhere !important;
    }

    [data-testid="stTable"] thead tr th * {
        color:#000 !important;
        -webkit-text-fill-color:#000 !important;
        font-weight:900 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def _tab_state_vault() -> dict[str, dict[str, Any]]:
    return {}


def _clean_order(order: Iterable[str] | None) -> list[str]:
    candidate = [str(item) for item in (order or [])]
    out: list[str] = []
    for item in candidate:
        if item in DEFAULT_TAB_ORDER and item not in out:
            out.append(item)
    for item in DEFAULT_TAB_ORDER:
        if item not in out:
            out.append(item)
    return out


def _clean_active(active: Any, order: list[str]) -> str:
    active = str(active or "")
    return active if active in order else order[0]


def _same_state(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        list(left.get("order") or []) == list(right.get("order") or [])
        and str(left.get("active") or "") == str(right.get("active") or "")
    )


def _load(vault_key: str) -> dict[str, Any]:
    session = st.session_state.get("terminal_tab_state")
    if isinstance(session, dict):
        order = _clean_order(session.get("order"))
        active = _clean_active(session.get("active"), order)
        return {"order": order, "active": active}

    saved = _tab_state_vault().get(str(vault_key), {})
    order = _clean_order(saved.get("order") or DEFAULT_TAB_ORDER)
    active = _clean_active(saved.get("active") or order[0], order)
    state = {"order": order, "active": active}
    st.session_state["terminal_tab_state"] = state
    return state


def _save(vault_key: str, order: Iterable[str], active: Any) -> dict[str, Any]:
    clean_order = _clean_order(order)
    clean_active = _clean_active(active, clean_order)
    state = {"order": clean_order, "active": clean_active}
    st.session_state["terminal_tab_state"] = state
    _tab_state_vault()[str(vault_key)] = dict(state)
    return state


def _render_builtin_tab(active: str) -> None:
    """Render modular tabs that live outside the legacy app switch.

    GEX used to be a one-line scaffold. Streamlit/Python can keep an already
    imported module alive across hot reruns, so after replacing that scaffold a
    worker could continue showing the old placeholder. On the first GEX render
    of each browser session, explicitly invalidate import caches and reload the
    module from disk. This guarantees the full current E*TRADE GEX workspace is
    what the user sees without paying the reload cost on every interaction.
    """
    if active != "GEX":
        return

    import importlib
    import src.gex_ui as gex_ui

    reload_key = "_raj_gex_full_workspace_reload_v2"
    if not st.session_state.get(reload_key):
        importlib.invalidate_caches()
        gex_ui = importlib.reload(gex_ui)
        st.session_state[reload_key] = True

    renderer = getattr(gex_ui, "render_gex", None)
    if not callable(renderer):
        st.error("GEX WORKSPACE LOAD ERROR // render_gex() is unavailable")
        return
    renderer()


def render_terminal_tab_bar(vault_key: str) -> tuple[list[str], str]:
    """Render the terminal tabs with one Streamlit pass per interaction.

    The component already fires a Streamlit rerun when it sends a select/reorder
    event. Its JavaScript keeps the user's optimistic highlight/order while
    waiting for Python acknowledgement, so a second explicit st.rerun() here is
    redundant and creates the visible flash/double-refresh effect. Persist the
    returned state and use it immediately in this same render pass instead.
    """
    state = _load(vault_key)
    storage_key = "raj-terminal-tabs-" + str(vault_key)[:16]

    result = _terminal_tabs(
        tabs=state["order"],
        active=state["active"],
        storage_key=storage_key,
        key="raj_terminal_draggable_tabs_v3",
        default={
            "order": state["order"],
            "active": state["active"],
            "action": "init",
            "event_id": 0,
        },
    )

    if isinstance(result, dict):
        proposed_order = _clean_order(result.get("order", state["order"]))
        proposed_active = _clean_active(
            result.get("active", state["active"]),
            proposed_order,
        )
        proposed = {"order": proposed_order, "active": proposed_active}

        if not _same_state(proposed, state):
            saved = _save(vault_key, proposed_order, proposed_active)
            _render_builtin_tab(saved["active"])
            return saved["order"], saved["active"]

    acknowledged = _load(vault_key)
    _render_builtin_tab(acknowledged["active"])
    return acknowledged["order"], acknowledged["active"]
