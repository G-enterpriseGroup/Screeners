"""Seamless lock screen + compact E*TRADE authorization UI for Raj's Terminal.

The keypad itself runs entirely in the browser and does not send a Streamlit
event for every digit. Python receives a value only when UNLOCK TERMINAL is
pressed. That removes the repeated page reruns/visual flashing caused by a grid
of normal st.button widgets while preserving the same server-side hash check,
failed-attempt counter, and lockout policy.

This module also compacts the E*TRADE OAuth completion panel without replacing
its authorization/session logic. CONNECT E*TRADE still uses the existing
one-click OAuth patch; only the visual layout is tightened.

The E*TRADE connection/status controls are rendered as a utility bar before the
main Raj's Terminal title. The later legacy render call is suppressed within the
same Streamlit pass so the controls never appear twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components


_COMPONENT_PATH = Path(__file__).parent / "components" / "lock_keypad_v1"
_lock_keypad = components.declare_component(
    "raj_terminal_lock_keypad_v1",
    path=str(_COMPONENT_PATH),
)


_COMPACT_AUTH_CSS = """
<style>
/* Compact only the OAuth completion container identified by its verifier input. */
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) {
    border:1px solid #fb8b1e !important;
    background:#000 !important;
    margin-top:.22rem !important;
    margin-bottom:.42rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) > div {
    padding:.34rem .52rem .42rem .52rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stVerticalBlock"] {
    gap:.26rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stHorizontalBlock"] {
    gap:.55rem !important;
    align-items:center !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stTextInput"] {
    margin:0 !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stTextInput"] input {
    min-height:36px !important;
    height:36px !important;
    padding:.15rem .55rem !important;
    font-size:.88rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) .stButton > button,
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) .stLinkButton > a {
    min-height:36px !important;
    height:36px !important;
    padding:.15rem .7rem !important;
    font-size:.86rem !important;
    margin:0 !important;
}
.etrade-auth-compact-head {
    display:flex;
    align-items:center;
    justify-content:space-between;
    min-height:34px;
    padding:.30rem .62rem;
    margin:-.02rem 0 .12rem 0;
    background:#fb8b1e;
    color:#000 !important;
    border:1px solid #fb8b1e;
    font-family:"Courier New",monospace;
    font-size:1.02rem;
    font-weight:900;
    line-height:1;
    letter-spacing:.025em;
    text-transform:uppercase;
    box-sizing:border-box;
}
.etrade-auth-compact-head span {
    color:#000 !important;
}
.etrade-auth-step {
    color:#fb8b1e !important;
    font-family:"Courier New",monospace;
    font-size:.73rem;
    font-weight:800;
    line-height:1.18;
    margin:.05rem 0 .08rem 0;
    white-space:normal;
}
@media (max-width:900px) {
    .etrade-auth-compact-head { font-size:.88rem; }
    .etrade-auth-step { font-size:.68rem; }
}
</style>
"""


def render_seamless_lock_screen(namespace: dict[str, Any]) -> None:
    remaining_fn = namespace["_app_lockout_remaining"]
    verify_fn = namespace["_verify_trade_access_code"]
    failed_fn = namespace["_record_failed_unlock"]
    clear_fn = namespace["_clear_unlock_failures"]
    max_attempts = int(namespace["APP_LOCK_MAX_ATTEMPTS"])

    remaining = int(remaining_fn())
    feedback = st.session_state.get("_app_keypad_feedback_v2") or {}

    result = _lock_keypad(
        remaining=remaining,
        feedback=str(feedback.get("message", "")),
        feedback_tone=str(feedback.get("tone", "")),
        key="raj_terminal_lock_keypad_v1",
        default={"code": "", "nonce": 0},
    )

    if not isinstance(result, dict):
        return

    try:
        nonce = int(result.get("nonce") or 0)
    except (TypeError, ValueError):
        nonce = 0
    code = str(result.get("code") or "")
    last_nonce = int(st.session_state.get("_app_keypad_last_nonce_v2", 0) or 0)

    if nonce <= 0 or nonce == last_nonce or not code:
        return
    st.session_state["_app_keypad_last_nonce_v2"] = nonce

    if remaining_fn() > 0:
        return

    if verify_fn(code):
        st.session_state["etrade_access_unlocked"] = True
        st.session_state.pop("app_access_code", None)
        st.session_state.pop("app_keypad_buffer", None)
        st.session_state.pop("_app_keypad_feedback", None)
        st.session_state.pop("_app_keypad_feedback_v2", None)
        clear_fn()
        st.rerun()

    failed_fn()
    remaining_after = int(remaining_fn())
    if remaining_after > 0:
        st.session_state["_app_keypad_feedback_v2"] = {
            "tone": "error",
            "message": f"TOO MANY FAILED ATTEMPTS // LOCKED FOR {remaining_after} SECONDS",
        }
    else:
        attempts = int(st.session_state.get("app_unlock_failures", 0) or 0)
        left = max(0, max_attempts - attempts)
        st.session_state["_app_keypad_feedback_v2"] = {
            "tone": "error",
            "message": f"INCORRECT ACCESS CODE // {left} ATTEMPTS REMAIN",
        }
    st.rerun()


def _install_compact_etrade_authorization(namespace: dict[str, Any]) -> None:
    """Wrap the existing connection renderer and compact only its OAuth panel."""
    current = namespace.get("render_etrade_connection")
    if not callable(current) or getattr(current, "_raj_compact_auth", False):
        return

    original_renderer = current

    def compact_renderer():
        original_markdown = st.markdown
        original_subheader = st.subheader
        original_columns = st.columns
        original_link_button = st.link_button
        original_caption = st.caption
        original_text_input = st.text_input
        auth_state = {"active": False}

        original_markdown(_COMPACT_AUTH_CSS, unsafe_allow_html=True)

        def compact_subheader(body, *args, **kwargs):
            if str(body).strip().casefold() == "complete e*trade authorization".casefold():
                auth_state["active"] = True
                return original_markdown(
                    '<div class="etrade-auth-compact-head">'
                    '<span>COMPLETE E*TRADE AUTHORIZATION</span>'
                    '<span>OAUTH</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
            return original_subheader(body, *args, **kwargs)

        def compact_columns(spec, *args, **kwargs):
            if auth_state["active"] and isinstance(spec, (list, tuple)) and list(spec) == [1, 1.25]:
                return original_columns([0.72, 2.28], gap="small")
            return original_columns(spec, *args, **kwargs)

        def compact_link_button(label, url, *args, **kwargs):
            direct_ready = bool(
                st.session_state.get("_raj_direct_etrade_auth_ready", False)
                and (st.session_state.get("etrade_request") or {}).get("authorization_url")
                and not st.session_state.get("etrade_access_token")
            )
            if (
                auth_state["active"]
                and str(label).strip() == "1 // OPEN E*TRADE LOGIN"
                and direct_ready
            ):
                return original_markdown(
                    '<div class="etrade-auth-step">'
                    '1 // CONNECT E*TRADE ABOVE OPENS LOGIN DIRECTLY<br>'
                    'APPROVE ACCESS + COPY THE CODE'
                    '</div>',
                    unsafe_allow_html=True,
                )
            return original_link_button(label, url, *args, **kwargs)

        def compact_caption(body, *args, **kwargs):
            text = str(body)
            if auth_state["active"] and text.startswith("Approve access and copy"):
                return None
            return original_caption(body, *args, **kwargs)

        def compact_text_input(label, *args, **kwargs):
            if auth_state["active"] and kwargs.get("key") == "etrade_verifier":
                kwargs = dict(kwargs)
                kwargs["label_visibility"] = "collapsed"
            return original_text_input(label, *args, **kwargs)

        st.subheader = compact_subheader
        st.columns = compact_columns
        st.link_button = compact_link_button
        st.caption = compact_caption
        st.text_input = compact_text_input
        try:
            return original_renderer()
        finally:
            st.subheader = original_subheader
            st.columns = original_columns
            st.link_button = original_link_button
            st.caption = original_caption
            st.text_input = original_text_input

    compact_renderer._raj_compact_auth = True
    namespace["render_etrade_connection"] = compact_renderer


def _install_connection_above_title(namespace: dict[str, Any]) -> None:
    """Render the E*TRADE utility controls before the main terminal title.

    streamlit_app historically calls render_etrade_connection() after st.title().
    Rather than duplicate that large entrypoint, intercept the first title call,
    render the connection block immediately before it, and make the later legacy
    render call a no-op for that same Streamlit pass.

    The base st.title function is restored on every run so repeated Streamlit
    reruns cannot stack wrappers or cause additional refreshes.
    """
    current = namespace.get("render_etrade_connection")
    if not callable(current):
        return

    base_title = getattr(st, "_raj_terminal_base_title", None)
    if not callable(base_title):
        base_title = st.title
        st._raj_terminal_base_title = base_title
    else:
        # Undo a wrapper left behind if a prior run stopped at the lock screen
        # before reaching the title.
        st.title = base_title

    render_state = {"done": False}

    def connection_once():
        if render_state["done"]:
            return None
        render_state["done"] = True
        return current()

    def title_after_connection(*args, **kwargs):
        try:
            connection_once()
            return base_title(*args, **kwargs)
        finally:
            # Never leave Streamlit's global title function wrapped after the
            # title has rendered; this keeps later fragments/reruns predictable.
            st.title = base_title

    connection_once._raj_above_title = True
    namespace["render_etrade_connection"] = connection_once
    st.title = title_after_connection


def install_seamless_lock_screen(namespace: dict[str, Any]) -> None:
    """Install the smooth lock screen, compact OAuth UI, and utility-bar order."""
    namespace["render_app_lock_screen"] = lambda: render_seamless_lock_screen(namespace)
    _install_compact_etrade_authorization(namespace)
    _install_connection_above_title(namespace)
