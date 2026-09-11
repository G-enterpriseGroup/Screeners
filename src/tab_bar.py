"""Clickable + draggable terminal tab bar for Raj's Terminal.

The Python side is the authoritative state. A component event is saved first,
then Streamlit immediately reruns once so the component highlight and the
rendered page can never be based on different tab states.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import streamlit as st
import streamlit.components.v1 as components


DEFAULT_TAB_ORDER = [
    "HOLDINGS",
    "RISK SIZING",
    "BULL DEBIT SPREAD",
    "MUNI SCREENERS",
    "ORDERS",
]

_COMPONENT_PATH = Path(__file__).parent / "components" / "terminal_tabs_v3"
_terminal_tabs = components.declare_component(
    "raj_terminal_tabs_v3",
    path=str(_COMPONENT_PATH),
)


# This module is imported after the legacy terminal-core stylesheet, so keep
# terminal-wide table-header overrides here.  All table/data-grid headers use
# the same solid Bloomberg-orange band with heavy black mono type as the
# exposure-panel headers instead of the old black/orange-outline treatment.
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
    }

    [data-testid="stDataFrame"] [role="columnheader"] *,
    [data-testid="stDataEditor"] [role="columnheader"] * {
        color:#000 !important;
        -webkit-text-fill-color:#000 !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
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
    }

    [data-testid="stTable"] thead tr th * {
        color:#000 !important;
        -webkit-text-fill-color:#000 !important;
        font-family:"Courier New",monospace !important;
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


def render_terminal_tab_bar(vault_key: str) -> tuple[list[str], str]:
    """Render terminal navigation with one-state-per-frame synchronization.

    Streamlit sends component args before Python receives the component's newest
    value. Without an acknowledgement rerun, the browser can highlight the old
    tab while Python renders the newly selected page. Any real state change is
    therefore saved first and followed by an immediate synchronization rerun.
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
        proposed = {
            "order": _clean_order(result.get("order", state["order"])),
            "active": "",
        }
        proposed["active"] = _clean_active(
            result.get("active", state["active"]),
            proposed["order"],
        )

        if not _same_state(proposed, state):
            _save(vault_key, proposed["order"], proposed["active"])
            # Synchronization barrier: never render page content in a frame
            # whose tab component was called with the previous active/order.
            st.rerun()

    acknowledged = _load(vault_key)
    return acknowledged["order"], acknowledged["active"]
