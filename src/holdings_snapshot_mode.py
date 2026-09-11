"""Manual/snapshot mode adapter for the Holdings tab.

The legacy holdings renderer already contains the full table and analytics.
This adapter disables its live-polling controls and presents a single manual
refresh action, while preserving all downstream holdings analytics.
"""

from __future__ import annotations

import time
from typing import Callable

import streamlit as st


class _ColumnProxy:
    """Pass-through Streamlit column that removes stale 'Live' metric wording."""

    def __init__(self, column):
        self._column = column

    def __enter__(self):
        self._column.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._column.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name):
        return getattr(self._column, name)

    def metric(self, label, *args, **kwargs):
        label = {
            "Live Account Value": "Account Value",
            "Live Market Value": "Market Value",
        }.get(str(label), label)
        return self._column.metric(label, *args, **kwargs)


def _age_text(seconds: float | int | None) -> str:
    if seconds is None:
        return "unknown age"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s old"
    if seconds < 3600:
        return f"{seconds // 60}m old"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m old"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h old"


def build_manual_holdings_renderer(core_renderer: Callable):
    """Return the holdings renderer with live polling removed from the UI/data path."""
    raw_body = getattr(core_renderer, "__wrapped__", None)
    core_body = raw_body or core_renderer

    def _run_snapshot_body():
        original_toggle = st.toggle
        original_selectbox = st.selectbox
        original_button = st.button
        original_caption = st.caption
        original_warning = st.warning
        original_columns = st.columns

        def snapshot_toggle(label, *args, **kwargs):
            if kwargs.get("key") == "holdings_live_enabled":
                return False
            return original_toggle(label, *args, **kwargs)

        def snapshot_selectbox(label, options, *args, **kwargs):
            if kwargs.get("key") == "holdings_refresh_seconds":
                # The legacy body still expects a value, but live refresh is
                # disabled and this selector is intentionally not rendered.
                return 30
            return original_selectbox(label, options, *args, **kwargs)

        def snapshot_button(label, *args, **kwargs):
            if kwargs.get("key") == "refresh_holdings":
                offline = bool(st.session_state.get("_etrade_offline_mode", False))
                return original_button(
                    "RELOAD CACHED HOLDINGS" if offline else "REFRESH HOLDINGS + BALANCE",
                    *args,
                    **kwargs,
                )
            return original_button(label, *args, **kwargs)

        def snapshot_caption(body, *args, **kwargs):
            text = str(body)
            if text.startswith("HOLDINGS DATA //"):
                offline = bool(st.session_state.get("_etrade_offline_mode", False))
                event = st.session_state.get("_etrade_offline_last_event") or {}
                if offline:
                    text = (
                        "HOLDINGS SNAPSHOT // OFFLINE CACHE // "
                        f"source snapshot {_age_text(event.get('age'))} // NOT LIVE"
                    )
                else:
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

        def snapshot_columns(spec, *args, **kwargs):
            # The first three-column control row in the legacy renderer was
            # LIVE toggle / interval / refresh. Collapse it into one full-width
            # manual refresh button while keeping the body assignment intact.
            if isinstance(spec, (list, tuple)) and list(spec) == [1.3, 1.2, 1.4]:
                spec = [0.001, 0.001, 1.0]
            columns = original_columns(spec, *args, **kwargs)
            return [_ColumnProxy(column) for column in columns]

        st.toggle = snapshot_toggle
        st.selectbox = snapshot_selectbox
        st.button = snapshot_button
        st.caption = snapshot_caption
        st.warning = snapshot_warning
        st.columns = snapshot_columns
        try:
            return core_body()
        finally:
            st.toggle = original_toggle
            st.selectbox = original_selectbox
            st.button = original_button
            st.caption = original_caption
            st.warning = original_warning
            st.columns = original_columns

    # If Streamlit exposes the original function behind @st.fragment, wrap that
    # body in a non-timed fragment. Otherwise call the existing renderer while
    # still forcing live_enabled=False; no E*TRADE polling occurs in either path.
    if raw_body is not None:
        return st.fragment(_run_snapshot_body)
    return _run_snapshot_body
