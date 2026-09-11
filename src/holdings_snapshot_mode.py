"""Manual/snapshot mode adapter for the Holdings tab.

The legacy holdings renderer already contains the full table and analytics.
This adapter disables its live-polling controls and presents a single manual
refresh action, while preserving all downstream holdings analytics.
"""

from __future__ import annotations

import time
from typing import Callable

import streamlit as st


def build_manual_holdings_renderer(core_renderer: Callable):
    """Return the holdings renderer with live polling removed from the UI/data path."""
    core_body = getattr(core_renderer, "__wrapped__", core_renderer)

    @st.fragment
    def render_manual_holdings():
        original_toggle = st.toggle
        original_selectbox = st.selectbox
        original_button = st.button
        original_caption = st.caption
        original_warning = st.warning

        def snapshot_toggle(label, *args, **kwargs):
            if kwargs.get("key") == "holdings_live_enabled":
                return False
            return original_toggle(label, *args, **kwargs)

        def snapshot_selectbox(label, options, *args, **kwargs):
            if kwargs.get("key") == "holdings_refresh_seconds":
                # The core body still expects a value, but this setting is hidden
                # and live refresh is disabled above.
                return 30
            return original_selectbox(label, options, *args, **kwargs)

        def snapshot_button(label, *args, **kwargs):
            if kwargs.get("key") == "refresh_holdings":
                return original_button(
                    "REFRESH HOLDINGS + BALANCE",
                    *args,
                    **kwargs,
                )
            return original_button(label, *args, **kwargs)

        def snapshot_caption(body, *args, **kwargs):
            text = str(body)
            if text.startswith("HOLDINGS DATA //"):
                refreshed_at = float(
                    st.session_state.get("etrade_holdings_last_refresh", 0.0) or 0.0
                )
                if refreshed_at:
                    age = max(0, int(time.time() - refreshed_at))
                    text = (
                        "HOLDINGS SNAPSHOT // MANUAL REFRESH ONLY // "
                        f"last refresh {age}s ago"
                    )
                else:
                    text = "HOLDINGS SNAPSHOT // MANUAL REFRESH ONLY"
            return original_caption(text, *args, **kwargs)

        def snapshot_warning(body, *args, **kwargs):
            text = str(body).replace("LIVE REFRESH ERROR //", "REFRESH ERROR //")
            return original_warning(text, *args, **kwargs)

        st.toggle = snapshot_toggle
        st.selectbox = snapshot_selectbox
        st.button = snapshot_button
        st.caption = snapshot_caption
        st.warning = snapshot_warning
        try:
            return core_body()
        finally:
            st.toggle = original_toggle
            st.selectbox = original_selectbox
            st.button = original_button
            st.caption = original_caption
            st.warning = original_warning

    return render_manual_holdings
