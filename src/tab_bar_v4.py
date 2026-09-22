"""Clickable + draggable terminal tab bar for Raj's Terminal.

OWNERSHIP / EDITING NOTES
-------------------------
THIS FILE owns only the TOP TERMINAL NAVIGATION shell.

EDIT HERE for:
- top-level tab sizing / spacing / appearance;
- the Streamlit component frame that contains the top tab bar;
- tab order persistence and active-tab routing state.

DO NOT edit here for:
- Risk Sizing content;
- GEX content/subtabs/tables;
- E*TRADE OAuth layout;
- Holdings content;
- municipal or bull-debit feature content.

Standing layout rule: the navigation must reserve ONLY the pixels it actually
uses. Never leave a default Streamlit component-height spacer below the bar.

V4 is navigation-only: it never renders tab content itself. This prevents
cached helper modules from leaving an old feature scaffold on screen. The main
Streamlit entrypoint owns all tab rendering, including GEX.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import streamlit as st
import streamlit.components.v1 as components

from src.layout_guardrails import install_layout_guardrails


# ==============================
# FEATURE OWNERSHIP
# ==============================

DEFAULT_TAB_ORDER = [
    "HOLDINGS",
    "RISK SIZING",
    "SCHWAB RISK SIZING",
    "GEX",
    "OPTION BOOK",
    "BULL DEBIT SPREAD",
    "MUNI SCREENERS",
    "ORDERS",
]

TAB_DISPLAY_LABELS = {
    # Keep the stable internal route key so existing saved tab state/order survives.
    "RISK SIZING": "E*TRADE RISK SIZING",
}

NAV_FRAME_HEIGHT = 48

_COMPONENT_PATH = Path(__file__).parent / "components" / "terminal_tabs_v3"
_terminal_tabs = components.declare_component(
    "raj_terminal_tabs_v4",
    path=str(_COMPONENT_PATH),
)

install_layout_guardrails()


# ==============================
# TOP NAVIGATION CSS
# ==============================

# IMPORTANT: Custom Streamlit components can reserve a much taller frame while
# loading or rerendering. The terminal tab component itself is only 48px high.
# The zero-height marker emitted immediately before the component lets this CSS
# collapse the EXACT NEXT Streamlit element container instead of guessing the
# browser-specific iframe title.
#
# SAFETY: do not walk upward through parent DOM nodes from inside the iframe.
# Biometric/unlock reruns can remount the component under a different wrapper,
# and forcing ancestor heights can collapse the entire app body.
#
# This is intentionally NAVIGATION-SCOPED. Do not generalize it to all iframes
# because GEX charts, lock-screen components, etc. need their own heights.
st.markdown(
    """
    <style>
    /* ---------- TOP TERMINAL NAV ONLY ---------- */
    .raj-terminal-tabs-v4-frame-anchor {
        display:block !important;
        width:0 !important;
        height:0 !important;
        min-height:0 !important;
        max-height:0 !important;
        overflow:hidden !important;
        margin:0 !important;
        padding:0 !important;
    }

    [data-testid="stElementContainer"]:has(.raj-terminal-tabs-v4-frame-anchor) {
        height:0 !important;
        min-height:0 !important;
        max-height:0 !important;
        margin:0 !important;
        padding:0 !important;
        overflow:hidden !important;
    }

    [data-testid="stElementContainer"]:has(.raj-terminal-tabs-v4-frame-anchor)
    + [data-testid="stElementContainer"],
    [data-testid="stElementContainer"]:has(.raj-terminal-tabs-v4-frame-anchor)
    + [data-testid="stElementContainer"] [data-testid="stCustomComponentV1"],
    [data-testid="stElementContainer"]:has(.raj-terminal-tabs-v4-frame-anchor)
    + [data-testid="stElementContainer"] iframe {
        height:48px !important;
        min-height:48px !important;
        max-height:48px !important;
        margin-top:0 !important;
        margin-bottom:0 !important;
        padding-top:0 !important;
        padding-bottom:0 !important;
        overflow:hidden !important;
        display:block !important;
    }

    iframe[title="raj_terminal_tabs_v4"],
    iframe[title*="raj_terminal_tabs_v4"],
    iframe[title*="raj_terminal_tabs"],
    iframe[src*="raj_terminal_tabs_v4"],
    iframe[src*="terminal_tabs_v3"] {
        height:48px !important;
        min-height:48px !important;
        max-height:48px !important;
        display:block !important;
        margin:0 !important;
        padding:0 !important;
        overflow:hidden !important;
    }

    [data-testid="stElementContainer"]:has(iframe[title="raj_terminal_tabs_v4"]),
    [data-testid="stElementContainer"]:has(iframe[title*="raj_terminal_tabs_v4"]),
    [data-testid="stElementContainer"]:has(iframe[title*="raj_terminal_tabs"]),
    [data-testid="stElementContainer"]:has(iframe[src*="raj_terminal_tabs_v4"]),
    [data-testid="stElementContainer"]:has(iframe[src*="terminal_tabs_v3"]) {
        height:48px !important;
        min-height:48px !important;
        max-height:48px !important;
        margin-top:0 !important;
        margin-bottom:0 !important;
        padding-top:0 !important;
        padding-bottom:0 !important;
        overflow:hidden !important;
    }

    [data-testid="stCustomComponentV1"]:has(iframe[title="raj_terminal_tabs_v4"]),
    [data-testid="stCustomComponentV1"]:has(iframe[title*="raj_terminal_tabs_v4"]),
    [data-testid="stCustomComponentV1"]:has(iframe[title*="raj_terminal_tabs"]),
    [data-testid="stCustomComponentV1"]:has(iframe[src*="raj_terminal_tabs_v4"]),
    [data-testid="stCustomComponentV1"]:has(iframe[src*="terminal_tabs_v3"]) {
        height:48px !important;
        min-height:48px !important;
        max-height:48px !important;
        margin:0 !important;
        padding:0 !important;
        overflow:hidden !important;
    }

    /* Table-header styling historically lived with the tab shell. Keep it
       unchanged here until it is intentionally moved to a shared table theme. */
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


# ==============================
# TAB STATE STORAGE
# ==============================

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


# ==============================
# PUBLIC RENDER ENTRYPOINT
# ==============================

def render_terminal_tab_bar(vault_key: str) -> tuple[list[str], str]:
    """Render navigation only and return the active terminal tab."""
    state = _load(vault_key)
    storage_key = "raj-terminal-tabs-" + str(vault_key)[:16]

    # Zero-height marker for CSS sibling targeting. This guarantees the
    # following component element is the only frame collapsed to 48px.
    st.markdown(
        '<span class="raj-terminal-tabs-v4-frame-anchor"></span>',
        unsafe_allow_html=True,
    )

    result = _terminal_tabs(
        tabs=state["order"],
        active=state["active"],
        display_labels=TAB_DISPLAY_LABELS,
        storage_key=storage_key,
        height=NAV_FRAME_HEIGHT,
        key="raj_terminal_draggable_tabs_v4_h48_option_book_20260922",
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
            return saved["order"], saved["active"]

    acknowledged = _load(vault_key)
    return acknowledged["order"], acknowledged["active"]