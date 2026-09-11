"""Fresh exposure-chart adapter used to defeat stale Streamlit module state.

This module deliberately has a new import path. It keeps the V3 Bloomberg
sector/industry implementation, but renders through a fresh DeltaGenerator
placeholder instead of the globally wrapped ``st.plotly_chart`` function.
That prevents an older in-memory visual-safety wrapper from rewriting the
intentional outside leader labels or manufacturing an empty title.
"""

from __future__ import annotations

import streamlit as st

import src.stockanalysis_portfolio_v3 as _v3


def _fresh_chart_panel(
    chart,
    label_column: str,
    title: str,
    key: str,
    *,
    legend_side: str,
    palette_offset: int,
    panel_height: int,
) -> None:
    if chart is None or chart.empty:
        st.info(f"No {label_column.lower()} exposure could be mapped.")
        return

    colors = _v3._chart_colors(len(chart), palette_offset=palette_offset)
    figure = _v3._safe_donut(
        chart,
        label_column,
        title,
        colors,
        legend_side=legend_side,
        panel_height=panel_height,
    )

    # Explicit blank title text prevents any older Plotly/Streamlit renderer
    # from surfacing a missing title as literal "undefined".
    figure.update_layout(title={"text": ""})

    # Use a fresh placeholder DeltaGenerator. This bypasses any stale global
    # monkeypatch of ``st.plotly_chart`` that may still exist in a long-running
    # Streamlit worker after a Git deploy.
    slot = st.empty()
    slot.plotly_chart(
        figure,
        width="stretch",
        config={"displayModeBar": False, "responsive": True, "displaylogo": False},
        key=key,
    )


def render_stockanalysis_portfolio(*args, **kwargs) -> None:
    """Render V3 data/hover logic with the fresh no-stale-wrapper chart path."""
    previous = _v3._render_chart_panel
    _v3._render_chart_panel = _fresh_chart_panel
    try:
        return _v3.render_stockanalysis_portfolio(*args, **kwargs)
    finally:
        _v3._render_chart_panel = previous
