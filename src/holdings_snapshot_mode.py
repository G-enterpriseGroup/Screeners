"""Manual/snapshot Holdings adapter for Raj's Terminal.

This adapter:
- keeps Holdings manual-refresh only
- preserves comma formatting
- removes the obsolete legacy PORTFOLIO VISUAL ANALYTICS / sector-map block
- removes the old POSITION ALLOCATION and UNREALIZED P&L bar-chart pair
- appends the newer StockAnalysis sector + industry Bloomberg panels
- uses a last-known-good public classification cache so a StockAnalysis/Yahoo
  failure does not erase previously known sector/industry mappings
"""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from typing import Callable

import streamlit as st

from src.stockanalysis_portfolio_v5 import cache_status, render_stockanalysis_portfolio
from src.terminal_number_format import comma_column_config
from src.visual_safety import install_streamlit_visual_safety


install_streamlit_visual_safety()


_REMOVED_HOLDINGS_CHART_KEYS = {
    "holdings_allocation_chart",
    "holdings_pnl_chart",
}


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

    def plotly_chart(self, figure_or_data, *args, **kwargs):
        """Suppress only the obsolete Holdings allocation/P&L bar-chart pair."""
        if str(kwargs.get("key") or "") in _REMOVED_HOLDINGS_CHART_KEYS:
            return None
        return self._column.plotly_chart(figure_or_data, *args, **kwargs)


class _SuppressedContext(AbstractContextManager):
    """No-output context used for the obsolete legacy sector-warning expander."""

    def __init__(self, state: dict[str, bool]):
        self._state = state

    def __enter__(self):
        self._state["suppress_legacy"] = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._state["suppress_legacy"] = False
        return False


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


def _legacy_sector_figure(figure) -> bool:
    """Identify only the obsolete single-sector chart from the core Holdings UI."""
    try:
        annotations = list(getattr(getattr(figure, "layout", None), "annotations", None) or [])
        for annotation in annotations:
            text = str(getattr(annotation, "text", "") or "").upper().replace("<BR>", " ")
            if "SECTOR" in text and "EXPOSURE" in text:
                return True
        title = getattr(getattr(getattr(figure, "layout", None), "title", None), "text", None)
        if title and "SECTOR" in str(title).upper() and "EXPOSURE" in str(title).upper():
            return True
    except Exception:
        return False
    return False


def _legacy_sector_table(data) -> bool:
    try:
        columns = {str(column) for column in data.columns}
    except Exception:
        return False
    return {"Sector", "Exposure", "% Exposure"}.issubset(columns)


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
        original_table = st.table
        original_markdown = st.markdown
        original_subheader = st.subheader
        original_plotly_chart = st.plotly_chart
        original_expander = st.expander
        original_info = st.info
        original_write = st.write

        legacy_state = {"suppress_legacy": False}

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
            upper = text.upper().strip()
            if legacy_state["suppress_legacy"] or upper.startswith("SECTOR MAP //"):
                return None
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
            if legacy_state["suppress_legacy"]:
                return None
            text = str(body).replace("LIVE REFRESH ERROR //", "REFRESH ERROR //")
            return original_warning(text, *args, **kwargs)

        def snapshot_info(body, *args, **kwargs):
            if legacy_state["suppress_legacy"]:
                return None
            return original_info(body, *args, **kwargs)

        def snapshot_write(*args, **kwargs):
            if legacy_state["suppress_legacy"]:
                return None
            return original_write(*args, **kwargs)

        def snapshot_columns(spec, *args, **kwargs):
            if isinstance(spec, (list, tuple)) and list(spec) == [1.3, 1.2, 1.4]:
                spec = [0.001, 0.001, 1.0]
            columns = original_columns(spec, *args, **kwargs)
            return [_ColumnProxy(column) for column in columns]

        def snapshot_dataframe(data=None, *args, **kwargs):
            if legacy_state["suppress_legacy"] or _legacy_sector_table(data):
                return None
            kwargs["column_config"] = comma_column_config(data, kwargs.get("column_config"))
            return original_dataframe(data, *args, **kwargs)

        def snapshot_table(data=None, *args, **kwargs):
            if legacy_state["suppress_legacy"] or _legacy_sector_table(data):
                return None
            return original_table(data, *args, **kwargs)

        def snapshot_markdown(body, *args, **kwargs):
            text = str(body)
            upper = text.upper()
            if legacy_state["suppress_legacy"]:
                return None
            if "PORTFOLIO VISUAL ANALYTICS" in upper:
                return None
            if "SECTOR EXPOSURE" in upper and "LOOK-THROUGH WHERE AVAILABLE" in upper:
                return None
            return original_markdown(body, *args, **kwargs)

        def snapshot_subheader(body, *args, **kwargs):
            if "PORTFOLIO VISUAL ANALYTICS" in str(body).upper():
                return None
            return original_subheader(body, *args, **kwargs)

        def snapshot_plotly_chart(figure_or_data, *args, **kwargs):
            if _legacy_sector_figure(figure_or_data):
                return None
            if str(kwargs.get("key") or "") in _REMOVED_HOLDINGS_CHART_KEYS:
                return None
            return original_plotly_chart(figure_or_data, *args, **kwargs)

        def snapshot_expander(label, *args, **kwargs):
            if "SECTOR MAP WARNINGS" in str(label).upper():
                return _SuppressedContext(legacy_state)
            return original_expander(label, *args, **kwargs)

        st.toggle = snapshot_toggle
        st.selectbox = snapshot_selectbox
        st.button = snapshot_button
        st.caption = snapshot_caption
        st.warning = snapshot_warning
        st.info = snapshot_info
        st.write = snapshot_write
        st.columns = snapshot_columns
        st.dataframe = snapshot_dataframe
        st.table = snapshot_table
        st.markdown = snapshot_markdown
        st.subheader = snapshot_subheader
        st.plotly_chart = snapshot_plotly_chart
        st.expander = snapshot_expander

        result = None
        try:
            result = core_body()
        finally:
            st.toggle = original_toggle
            st.selectbox = original_selectbox
            st.button = original_button
            st.caption = original_caption
            st.warning = original_warning
            st.info = original_info
            st.write = original_write
            st.columns = original_columns
            st.dataframe = original_dataframe
            st.table = original_table
            st.markdown = original_markdown
            st.subheader = original_subheader
            st.plotly_chart = original_plotly_chart
            st.expander = original_expander

        # Only the newer StockAnalysis sector + industry analytics remain.
        render_stockanalysis_portfolio(
            "holdings_account",
            key_prefix="holdings_stockanalysis_cached",
            title="STOCKANALYSIS // SECTOR + INDUSTRY EXPOSURE",
            show_classification_table=True,
        )
        cache = cache_status()
        st.caption(
            "CLASSIFICATION CACHE // LAST-KNOWN-GOOD FALLBACK ENABLED // "
            f"SECTOR/INDUSTRY {cache['classifications']:,} TICKERS // "
            f"ETF LOOK-THROUGHS {cache['lookthroughs']:,}"
        )
        return result

    if raw_body is not None:
        return st.fragment(_run_snapshot_body)
    return _run_snapshot_body
