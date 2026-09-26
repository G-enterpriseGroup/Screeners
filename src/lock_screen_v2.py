"""Seamless lock screen + compact E*TRADE authorization UI for Raj's Terminal.

The keypad runs in the browser so digit presses do not rerun Streamlit. It also
supports WebAuthn platform authentication (Touch ID/passkeys on supported Macs)
with server-side challenge/signature verification. The app never receives raw
fingerprint data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from src.passkey_auth import (
    build_authentication_options,
    build_registration_options,
    complete_authentication,
    complete_registration,
    load_touch_id_record,
    restore_touch_id_record,
    seal_touch_id_record,
    webauthn_error,
    webauthn_ready,
)


_COMPONENT_PATH = Path(__file__).parent / "components" / "lock_keypad_v2"
_lock_keypad = components.declare_component(
    "raj_terminal_lock_keypad_v2",
    path=str(_COMPONENT_PATH),
)

_TOUCH_ID_BROWSER_STORAGE_KEY = "raj-terminal-touch-id-memory-v1"


_COMPACT_AUTH_CSS = """
<style>
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) {
    border:1px solid #fb8b1e !important;background:#000 !important;
    margin-top:.22rem !important;margin-bottom:.42rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) > div {
    padding:.34rem .52rem .42rem .52rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stVerticalBlock"] {gap:.26rem !important;}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stHorizontalBlock"] {gap:.55rem !important;align-items:center !important;}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stTextInput"] {margin:0 !important;}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) [data-testid="stTextInput"] input {
    min-height:36px !important;height:36px !important;padding:.15rem .55rem !important;font-size:.88rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) .stButton > button,
div[data-testid="stVerticalBlockBorderWrapper"]:has(input[placeholder*="Enter the code shown by E*TRADE"]) .stLinkButton > a {
    min-height:36px !important;height:36px !important;padding:.15rem .7rem !important;font-size:.86rem !important;margin:0 !important;
}
.etrade-auth-compact-head {
    display:flex;align-items:center;justify-content:space-between;min-height:34px;
    padding:.30rem .62rem;margin:-.02rem 0 .12rem 0;background:#fb8b1e;color:#000 !important;
    border:1px solid #fb8b1e;font-family:"Courier New",monospace;font-size:1.02rem;
    font-weight:900;line-height:1;letter-spacing:.025em;text-transform:uppercase;box-sizing:border-box;
}
.etrade-auth-compact-head span {color:#000 !important;}
.etrade-auth-step {color:#fb8b1e !important;font-family:"Courier New",monospace;font-size:.73rem;font-weight:800;line-height:1.18;margin:.05rem 0 .08rem 0;white-space:normal;}
@media (max-width:900px) {.etrade-auth-compact-head{font-size:.88rem}.etrade-auth-step{font-size:.68rem}}
</style>
"""


def _current_app_url() -> str:
    """Return the browser-facing app URL used as the WebAuthn origin."""
    try:
        value = str(st.context.url or "").strip()
        if value:
            return value
    except Exception:
        pass

    try:
        headers = st.context.headers
        host = str(headers.get("host") or "").strip()
        proto = str(headers.get("x-forwarded-proto") or "https").split(",", 1)[0].strip()
        if host:
            return f"{proto}://{host}"
    except Exception:
        pass
    return ""


def _credential_identity_seed(namespace: dict[str, Any]) -> str:
    try:
        return str(namespace["_trade_access_code_hash"]())
    except Exception:
        return "raj-terminal-single-user"


def _credential_memory_secret(namespace: dict[str, Any]) -> str:
    """Return a stable server-only secret for sealing browser credential memory."""
    secret_fn = namespace.get("_secret_value")
    if not callable(secret_fn):
        return ""
    try:
        dedicated = str(secret_fn("security", "touch_id_memory_secret", "") or "").strip()
    except Exception:
        dedicated = ""
    if dedicated:
        return dedicated
    try:
        return str(secret_fn("etrade", "consumer_secret", "") or "").strip()
    except Exception:
        return ""


def _clear_touch_id_session() -> None:
    for key in (
        "_touchid_registration_options",
        "_touchid_registration_challenge",
        "_touchid_authentication_options",
        "_touchid_authentication_challenge",
    ):
        st.session_state.pop(key, None)


def _set_feedback(tone: str, message: str) -> None:
    st.session_state["_app_keypad_feedback_v2"] = {"tone": tone, "message": message}


def _unlock(namespace: dict[str, Any]) -> None:
    st.session_state["etrade_access_unlocked"] = True
    st.session_state.pop("app_access_code", None)
    st.session_state.pop("app_keypad_buffer", None)
    st.session_state.pop("_app_keypad_feedback", None)
    st.session_state.pop("_app_keypad_feedback_v2", None)
    _clear_touch_id_session()
    namespace["_clear_unlock_failures"]()
    st.rerun()


def _ensure_authentication_options(app_url: str, record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record or not webauthn_ready() or not app_url:
        return None
    existing = st.session_state.get("_touchid_authentication_options")
    challenge = st.session_state.get("_touchid_authentication_challenge")
    if isinstance(existing, dict) and isinstance(challenge, (bytes, bytearray)):
        return existing
    try:
        options, challenge = build_authentication_options(app_url, record)
    except Exception as exc:
        _set_feedback("error", f"TOUCH ID PREP FAILED // {exc}")
        return None
    st.session_state["_touchid_authentication_options"] = options
    st.session_state["_touchid_authentication_challenge"] = challenge
    return options


def render_seamless_lock_screen(namespace: dict[str, Any]) -> None:
    remaining_fn = namespace["_app_lockout_remaining"]
    verify_fn = namespace["_verify_trade_access_code"]
    failed_fn = namespace["_record_failed_unlock"]
    clear_fn = namespace["_clear_unlock_failures"]
    max_attempts = int(namespace["APP_LOCK_MAX_ATTEMPTS"])

    remaining = int(remaining_fn())
    feedback = st.session_state.get("_app_keypad_feedback_v2") or {}
    app_url = _current_app_url()
    identity_seed = _credential_identity_seed(namespace)
    memory_secret = _credential_memory_secret(namespace)
    touch_record = load_touch_id_record(app_url) if app_url and webauthn_ready() else None
    persist_then_unlock = bool(st.session_state.get("_touchid_persist_then_unlock", False))
    registration_options = st.session_state.get("_touchid_registration_options")
    authentication_options = (
        None
        if persist_then_unlock
        else _ensure_authentication_options(app_url, touch_record)
    )
    touch_memory = None
    if touch_record and app_url:
        try:
            touch_memory = seal_touch_id_record(app_url, touch_record, memory_secret)
        except Exception:
            touch_memory = None
    clear_browser_touch_memory = bool(
        st.session_state.pop("_touchid_clear_browser_memory", False)
    )
    if not webauthn_ready() and not feedback:
        feedback = {
            "tone": "error",
            "message": f"TOUCH ID BACKEND UNAVAILABLE // {webauthn_error() or 'WEBAUTHN NOT INSTALLED'}",
        }

    result = _lock_keypad(
        remaining=remaining,
        feedback=str(feedback.get("message", "")),
        feedback_tone=str(feedback.get("tone", "")),
        touch_id_registered=bool(touch_record),
        registration_options=registration_options if isinstance(registration_options, dict) else None,
        authentication_options=authentication_options if isinstance(authentication_options, dict) else None,
        touch_id_storage_key=_TOUCH_ID_BROWSER_STORAGE_KEY,
        touch_id_memory=touch_memory if isinstance(touch_memory, dict) else None,
        touch_id_persist_required=persist_then_unlock,
        clear_touch_id_memory=clear_browser_touch_memory,
        key="raj_terminal_lock_keypad_v2",
        default={"action": "", "code": "", "credential": None, "nonce": 0},
    )

    if not isinstance(result, dict):
        return
    try:
        nonce = int(result.get("nonce") or 0)
    except (TypeError, ValueError):
        nonce = 0
    last_nonce = int(st.session_state.get("_app_keypad_last_nonce_v2", 0) or 0)
    if nonce <= 0 or nonce == last_nonce:
        return
    st.session_state["_app_keypad_last_nonce_v2"] = nonce

    if remaining_fn() > 0:
        return

    action = str(result.get("action") or "code")
    code = str(result.get("code") or "")
    credential = result.get("credential")

    if action == "restore_touch_id_memory":
        restored = restore_touch_id_record(app_url, result.get("touch_id_memory"), memory_secret)
        if not restored:
            st.session_state["_touchid_clear_browser_memory"] = True
            _set_feedback(
                "error",
                "TOUCH ID MEMORY COULD NOT BE VERIFIED // ENTER ACCESS CODE ONCE TO RE-ENROLL",
            )
        else:
            _clear_touch_id_session()
            _set_feedback("ok", "TOUCH ID MEMORY RESTORED // VERIFYING MAC TOUCH ID")
        st.rerun()

    elif action == "touch_id_memory_saved":
        if st.session_state.pop("_touchid_persist_then_unlock", False):
            _unlock(namespace)
        return

    elif action == "code":
        if not code:
            return
        if verify_fn(code):
            clear_fn()
            _unlock(namespace)
        failed_fn()

    elif action == "begin_registration":
        if not code:
            _set_feedback("error", "ENTER ACCESS CODE FIRST // THEN SET UP TOUCH ID")
            st.rerun()
        if not verify_fn(code):
            failed_fn()
        elif not app_url or not webauthn_ready():
            _set_feedback("error", "TOUCH ID CANNOT START // SECURE WEB AUTHENTICATION IS UNAVAILABLE")
            st.rerun()
        else:
            try:
                options, challenge = build_registration_options(
                    app_url,
                    _credential_identity_seed(namespace),
                )
                st.session_state["_touchid_registration_options"] = options
                st.session_state["_touchid_registration_challenge"] = challenge
                st.session_state.pop("_touchid_authentication_options", None)
                st.session_state.pop("_touchid_authentication_challenge", None)
                _set_feedback("ok", "ACCESS CODE VERIFIED // CLICK CONFIRM TOUCH ID SETUP")
                clear_fn()
            except Exception as exc:
                _set_feedback("error", f"TOUCH ID SETUP FAILED // {exc}")
            st.rerun()

    elif action == "finish_registration":
        challenge = st.session_state.get("_touchid_registration_challenge")
        if not isinstance(challenge, (bytes, bytearray)) or not isinstance(credential, dict):
            _clear_touch_id_session()
            _set_feedback("error", "TOUCH ID SETUP EXPIRED // ENTER THE ACCESS CODE AND TRY AGAIN")
            st.rerun()
        try:
            complete_registration(app_url, bytes(challenge), credential)
        except Exception as exc:
            _clear_touch_id_session()
            _set_feedback("error", f"TOUCH ID VERIFICATION FAILED // {exc}")
            st.rerun()
        _clear_touch_id_session()
        st.session_state["_touchid_persist_then_unlock"] = True
        _set_feedback("ok", "TOUCH ID ENROLLED // SAVING REBOOT-SAFE PASSKEY MEMORY")
        clear_fn()
        st.rerun()

    elif action == "authenticate":
        challenge = st.session_state.get("_touchid_authentication_challenge")
        if (
            not touch_record
            or not isinstance(challenge, (bytes, bytearray))
            or not isinstance(credential, dict)
        ):
            st.session_state.pop("_touchid_authentication_options", None)
            st.session_state.pop("_touchid_authentication_challenge", None)
            _set_feedback("error", "TOUCH ID REQUEST EXPIRED // TRY AGAIN")
            st.rerun()
        try:
            complete_authentication(app_url, bytes(challenge), credential, touch_record)
        except Exception as exc:
            st.session_state.pop("_touchid_authentication_options", None)
            st.session_state.pop("_touchid_authentication_challenge", None)
            _set_feedback("error", f"TOUCH ID VERIFICATION FAILED // {exc}")
            st.rerun()
        st.session_state.pop("_touchid_authentication_options", None)
        st.session_state.pop("_touchid_authentication_challenge", None)
        st.session_state["_touchid_persist_then_unlock"] = True
        _set_feedback("ok", "TOUCH ID VERIFIED // SAVING UPDATED PASSKEY MEMORY")
        clear_fn()
        st.rerun()

    else:
        return

    remaining_after = int(remaining_fn())
    if remaining_after > 0:
        _set_feedback("error", f"TOO MANY FAILED ATTEMPTS // LOCKED FOR {remaining_after} SECONDS")
    else:
        attempts = int(st.session_state.get("app_unlock_failures", 0) or 0)
        left = max(0, max_attempts - attempts)
        _set_feedback("error", f"INCORRECT ACCESS CODE // {left} ATTEMPTS REMAIN")
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
                    '<div class="etrade-auth-compact-head"><span>COMPLETE E*TRADE AUTHORIZATION</span><span>OAUTH</span></div>',
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
            if auth_state["active"] and str(label).strip() == "1 // OPEN E*TRADE LOGIN" and direct_ready:
                return original_markdown(
                    '<div class="etrade-auth-step">1 // CONNECT E*TRADE ABOVE OPENS LOGIN DIRECTLY<br>APPROVE ACCESS + COPY THE CODE</div>',
                    unsafe_allow_html=True,
                )
            return original_link_button(label, url, *args, **kwargs)

        def compact_caption(body, *args, **kwargs):
            if auth_state["active"] and str(body).startswith("Approve access and copy"):
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
    current = namespace.get("render_etrade_connection")
    if not callable(current):
        return
    base_title = getattr(st, "_raj_terminal_base_title", None)
    if not callable(base_title):
        base_title = st.title
        st._raj_terminal_base_title = base_title
    else:
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
            st.title = base_title

    connection_once._raj_above_title = True
    namespace["render_etrade_connection"] = connection_once
    st.title = title_after_connection


def install_seamless_lock_screen(namespace: dict[str, Any]) -> None:
    """Install smooth keypad, Touch ID/passkeys, compact OAuth, and utility-bar order."""
    namespace["render_app_lock_screen"] = lambda: render_seamless_lock_screen(namespace)
    _install_compact_etrade_authorization(namespace)
    _install_connection_above_title(namespace)
