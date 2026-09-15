"""Compact E*TRADE connection / OAuth UI for Raj's Terminal.

This module owns the presentation of the connection strip and OAuth completion
panel. The OAuth completion panel deliberately keeps only the two controls the
user needs in one physical Streamlit row:
VERIFICATION CODE | VERIFY AND CONNECT.

No brokerage/session math or token semantics live here; those are injected from
the terminal entrypoint so this stays a pure UI layer.
"""

from __future__ import annotations

import html
import time
from typing import Any, Callable

import streamlit as st


def _render_compact_css() -> None:
    st.markdown(
        """
        <style>
        .etrade-oauth-v3-head{display:flex;align-items:center;justify-content:space-between;gap:.5rem;width:100%;box-sizing:border-box;background:#fb8b1e;color:#000!important;padding:.20rem .42rem;margin:0 0 .18rem 0;font:900 .74rem/1 "Courier New",monospace;letter-spacing:.035em;text-transform:uppercase;}
        .etrade-oauth-v3-head span{color:#000!important;}
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-oauth-v3-head){padding:.28rem .42rem .30rem!important;border-color:#fb8b1e!important;}
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-oauth-v3-head) [data-testid="stVerticalBlock"]{gap:.16rem!important;}
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-oauth-v3-head) [data-testid="stHorizontalBlock"]{gap:.42rem!important;align-items:end!important;}
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-oauth-v3-head) [data-testid="stTextInput"] label{display:none!important;}
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-oauth-v3-head) [data-testid="stTextInput"] div[data-baseweb="input"]>div,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-oauth-v3-head) [data-testid="stTextInput"] input{min-height:34px!important;height:34px!important;}
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-oauth-v3-head) .stButton>button{min-height:34px!important;height:34px!important;padding:.16rem .42rem!important;margin:0!important;font-size:.69rem!important;line-height:1!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_compact_etrade_connection(
    *,
    credentials_factory: Callable[[], tuple[str, str, str]],
    live_client_factory: Callable[[], Any],
    session_timer: Callable[[bool], None],
    begin_authorization: Callable[..., Any],
    complete_authorization: Callable[..., dict[str, Any]],
    etrade_error_type: type[Exception],
    touch_session: Callable[[], None],
    clear_runtime: Callable[..., None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
) -> None:
    _render_compact_css()

    consumer_key, consumer_secret, environment = credentials_factory()
    live_client = live_client_factory()
    connected = live_client is not None
    status_text = "CONNECTED" if connected else (
        f"READY // {str(environment).upper()}" if consumer_key and consumer_secret else "SETUP REQUIRED"
    )

    session_timer(connected)

    status_col, connect_col, renew_col, disconnect_col, lock_col = st.columns(
        [2.8, 1.6, 1.0, 1.1, 0.9], vertical_alignment="center"
    )
    with status_col:
        st.markdown(
            f'<div class="terminal-note">E*TRADE API // {html.escape(status_text)}</div>',
            unsafe_allow_html=True,
        )
    with connect_col:
        if st.button("CONNECT E*TRADE", type="primary", width="stretch", disabled=not (consumer_key and consumer_secret), key="etrade_connect"):
            try:
                request = begin_authorization(consumer_key, consumer_secret, environment)
                st.session_state["etrade_request"] = {
                    "oauth_token": request.oauth_token,
                    "oauth_token_secret": request.oauth_token_secret,
                    "authorization_url": request.authorization_url,
                }
                st.rerun()
            except etrade_error_type as exc:
                st.error(str(exc))
    with renew_col:
        if st.button("RENEW", width="stretch", disabled=not connected, key="etrade_renew"):
            try:
                live_client_factory().renew()
                touch_session()
                st.success("E*TRADE session renewed.")
            except etrade_error_type as exc:
                st.error(str(exc))
    with disconnect_col:
        if st.button("DISCONNECT", width="stretch", disabled=not connected, key="etrade_disconnect"):
            clear_runtime(lock_access=True)
            st.rerun()
    with lock_col:
        if st.button("LOCK", width="stretch", key="etrade_lock_api"):
            clear_runtime(lock_access=True)
            st.rerun()

    if not consumer_key or not consumer_secret:
        st.warning(
            "Add E*TRADE consumer_key and consumer_secret in Streamlit App Settings → Secrets. "
            "Credentials are intentionally excluded from GitHub."
        )

    request = st.session_state.get("etrade_request")
    if not request or connected:
        return

    with st.container(border=True):
        st.markdown(
            '<div class="etrade-oauth-v3-head"><span>COMPLETE E*TRADE AUTHORIZATION</span><span>OAUTH</span></div>',
            unsafe_allow_html=True,
        )
        code_col, verify_col = st.columns([5.4, 1.7], gap="small", vertical_alignment="bottom")
        with code_col:
            verifier = st.text_input(
                "Verification Code",
                placeholder="Enter the code shown by E*TRADE",
                max_chars=12,
                key="etrade_verifier",
                label_visibility="collapsed",
            )
        with verify_col:
            verify_clicked = st.button(
                "VERIFY & CONNECT",
                width="stretch",
                disabled=not verifier.strip(),
                key="etrade_verify",
            )

        if verify_clicked:
            try:
                token = complete_authorization(
                    consumer_key,
                    consumer_secret,
                    request["oauth_token"],
                    request["oauth_token_secret"],
                    verifier,
                    environment,
                )
                token["issued_at"] = time.time()
                st.session_state["etrade_access_token"] = token
                touch_session()
                st.session_state.pop("etrade_request", None)
                refresh_accounts(live_client_factory())
                st.rerun()
            except etrade_error_type as exc:
                st.error(str(exc))


__all__ = ["render_compact_etrade_connection"]
