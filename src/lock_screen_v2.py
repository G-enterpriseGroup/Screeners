"""Seamless lock screen for Raj's Terminal.

The keypad itself runs entirely in the browser and does not send a Streamlit
event for every digit. Python receives a value only when UNLOCK TERMINAL is
pressed. That removes the repeated page reruns/visual flashing caused by a grid
of normal st.button widgets while preserving the same server-side hash check,
failed-attempt counter, and lockout policy.
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

    # Custom components retain their last value across reruns. Process each
    # explicit UNLOCK submission exactly once so an incorrect code cannot count
    # as multiple failures on unrelated future reruns.
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
        # This is the one intentional full-app rerun: transition from the lock
        # screen into the terminal framework after successful authentication.
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

    # One rerun only after an actual verification attempt so the component gets
    # the new server-side error/lockout state. Digit taps never rerun Streamlit.
    st.rerun()


def install_seamless_lock_screen(namespace: dict[str, Any]) -> None:
    """Install the v2 lock renderer into the active Streamlit entrypoint."""
    namespace["render_app_lock_screen"] = lambda: render_seamless_lock_screen(namespace)
