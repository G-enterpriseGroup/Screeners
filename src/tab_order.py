"""Drag-and-drop tab ordering for Raj's Terminal.

The order is stored in process memory keyed by the terminal access-code hash,
so it survives normal Streamlit reruns and browser refreshes while the app
process remains alive. The Streamlit session also mirrors the order for instant
rerenders.
"""

from __future__ import annotations

from typing import Iterable

import streamlit as st
from streamlit_sortables import sort_items


DEFAULT_TAB_ORDER = [
    "HOLDINGS",
    "RISK SIZING",
    "BULL DEBIT SPREAD",
    "MUNI SCREENERS",
    "ORDERS",
]


@st.cache_resource
def _tab_order_vault() -> dict[str, list[str]]:
    return {}


def _sanitize(order: Iterable[str] | None) -> list[str]:
    order = [str(item) for item in (order or [])]
    valid = [item for item in order if item in DEFAULT_TAB_ORDER]
    valid = list(dict.fromkeys(valid))
    for item in DEFAULT_TAB_ORDER:
        if item not in valid:
            valid.append(item)
    return valid


def load_tab_order(vault_key: str) -> list[str]:
    session_order = st.session_state.get("terminal_tab_order")
    if session_order:
        return _sanitize(session_order)

    saved = _tab_order_vault().get(str(vault_key))
    order = _sanitize(saved or DEFAULT_TAB_ORDER)
    st.session_state["terminal_tab_order"] = order
    return order


def save_tab_order(vault_key: str, order: Iterable[str]) -> list[str]:
    clean = _sanitize(order)
    st.session_state["terminal_tab_order"] = clean
    _tab_order_vault()[str(vault_key)] = list(clean)
    return clean


def reset_tab_order(vault_key: str) -> list[str]:
    order = list(DEFAULT_TAB_ORDER)
    st.session_state["terminal_tab_order"] = order
    _tab_order_vault()[str(vault_key)] = list(order)
    return order


def render_tab_order_editor(vault_key: str) -> list[str]:
    """Render a compact Bloomberg-style draggable tab strip and return its order."""
    current = load_tab_order(vault_key)

    with st.expander("↕ DRAG TABS TO REORDER", expanded=False):
        st.caption(
            "DRAG ANY TAB LEFT OR RIGHT // ORDER SAVES AUTOMATICALLY // "
            "PERSISTS ACROSS NORMAL PAGE REFRESHES"
        )

        custom_style = """
        .sortable-component {
            background:#000000 !important;
            padding:0 !important;
            margin:0 !important;
            border:0 !important;
            font-family:'Courier New',monospace !important;
        }
        .sortable-container {
            background:#000000 !important;
            border:0 !important;
            padding:0 !important;
            margin:0 !important;
            min-height:42px !important;
        }
        .sortable-container-body {
            background:#000000 !important;
            border:0 !important;
            padding:2px !important;
            gap:6px !important;
            min-height:42px !important;
            display:flex !important;
            flex-wrap:wrap !important;
            align-items:center !important;
        }
        .sortable-item {
            background:#000000 !important;
            color:#fb8b1e !important;
            border:1px solid #fb8b1e !important;
            border-radius:0 !important;
            padding:7px 12px !important;
            margin:0 !important;
            min-height:34px !important;
            font-family:'Courier New',monospace !important;
            font-size:13px !important;
            font-weight:900 !important;
            cursor:grab !important;
            box-shadow:none !important;
            user-select:none !important;
        }
        .sortable-item:hover {
            background:#0068ff !important;
            color:#ffffff !important;
            border-color:#66adff !important;
        }
        .sortable-item:active {
            cursor:grabbing !important;
            background:#0068ff !important;
            color:#ffffff !important;
        }
        .sortable-container-header { display:none !important; }
        """

        reordered = sort_items(
            current,
            direction="horizontal",
            custom_style=custom_style,
            key="terminal_tab_drag_order",
        )
        reordered = _sanitize(reordered)
        if reordered != current:
            current = save_tab_order(vault_key, reordered)

        left, right = st.columns([1, 5])
        with left:
            if st.button(
                "RESET TAB ORDER",
                key="reset_terminal_tab_order",
                width="stretch",
            ):
                current = reset_tab_order(vault_key)
                st.rerun()
        with right:
            st.caption("CURRENT ORDER // " + "  →  ".join(current))

    return current
