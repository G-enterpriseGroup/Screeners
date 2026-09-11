"""Fresh Holdings snapshot adapter.

New module name is intentional: Streamlit can keep imported Python modules in
memory across browser refreshes. Importing this module from the entrypoint
forces the latest Holdings behavior to load in an already-running worker.
"""

from __future__ import annotations

import time
from typing import Callable

import streamlit as st

from src.stockanalysis_portfolio_v4 import render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config


class _ColumnProxy:
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
    raw_body = getattr(core_renderer, "__wrapped__", None)
    core_body = raw_body or core_renderer

    def _run_snapshot_body():
        original_toggle = st.toggle
        original_selectbox = st.selectbox
        original_button = st.button
        original_caption = st.caption
        original_warning = st.warning
        original_columns = st.columns
        original_dataframe = st.dataframe

        def snapshot_toggle(label, *args, **kwargs):
            if kwargs.get("key") == "holdings_live_enabled":
                return False
            return original_toggle(label, *args, **kwargs)

        def snapshot_selectbox(label, options, *args, **kwargs):
            if kwargs.get("key") == "holdings_refresh_seconds":
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
                    refreshed_at = float(st.session_state.get("etrade_holdings_last_refresh", 0.0) or 0.0)
                    if refreshed_at:
                        age = max(0, int(time.time() - refreshed_at))
                        text = f"HOLDINGS SNAPSHOT // MANUAL REFRESH ONLY // last refresh {age}s ago"
                    else:
                        text = "HOLDINGS SNAPSHOT // MANUAL REFRESH ONLY"
            return original_caption(text, *args, **kwargs)

        def snapshot_warning(body, *args, **kwargs):
            return original_warning(str(body).replace("LIVE REFRESH ERROR //", "REFRESH ERROR //"), *args, **kwargs)

        def snapshot_columns(spec, *args, **kwargs):
            if isinstance(spec, (list, tuple)) and list(spec) == [1.3, 1.2, 1.4]:
                spec = [0.001, 0.001, 1.0]
            columns = original_columns(spec, *args, **kwargs)
            return [_ColumnProxy(column) for column in columns]

        def snapshot_dataframe(data=None, *args, **kwargs):
            kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
            return original_dataframe(data, *args, **kwargs)

        st.toggle = snapshot_toggle
        st.selectbox = snapshot_selectbox
        st.button = snapshot_button
        st.caption = snapshot_caption
        st.warning = snapshot_warning
        st.columns = snapshot_columns
        st.dataframe = snapshot_dataframe
        result = None
        try:
            result = core_body()
        finally:
            st.toggle = original_toggle
            st.selectbox = original_selectbox
            st.button = original_button
            st.caption = original_caption
            st.warning = original_warning
            st.columns = original_columns
            st.dataframe = original_dataframe

        render_stockanalysis_portfolio(
            "holdings_account",
            key_prefix="holdings_stockanalysis_fresh",
            title="STOCKANALYSIS // SECTOR + INDUSTRY EXPOSURE",
            show_classification_table=True,
        )
        return result

    if raw_body is not None:
        return st.fragment(_run_snapshot_body)
    return _run_snapshot_body
