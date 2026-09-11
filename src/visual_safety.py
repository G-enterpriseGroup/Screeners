"""Global Plotly visual-safety guardrails for Raj's Terminal.

The terminal uses dense Bloomberg-style visuals. This module prevents common
layout defects such as pie-label overflow, tiny-slice text collisions, chart
legends being clipped, and annotation text sitting against the figure edge.

It intentionally changes only presentation. Data, calculations, hover values,
and chart ordering are left untouched.
"""

from __future__ import annotations

from typing import Any

import streamlit as st


_MIN_PIE_LABEL_PCT = 2.5
_MIN_TEXT_MARGIN = 36


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _sequence(value: Any) -> list[Any]:
    """Convert Plotly tuple/list/numpy/pandas values without truth-value tests."""
    if value is None:
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


def _safe_margin_value(value: Any, minimum: int) -> int:
    try:
        return max(int(value or 0), int(minimum))
    except (TypeError, ValueError):
        return int(minimum)


def harden_plotly_figure(figure):
    """Apply non-destructive layout safety to one Plotly figure."""
    if figure is None or not hasattr(figure, "data"):
        return figure

    pie_traces = []
    has_free_text = False

    for trace in figure.data:
        trace_type = str(getattr(trace, "type", "") or "").lower()

        if trace_type == "pie":
            pie_traces.append(trace)
            values = _sequence(getattr(trace, "values", None))
            numeric_values = [max(0.0, _number(value)) for value in values]
            total = sum(numeric_values)

            # Never let Plotly push tiny percentages outside the donut where
            # they can collide with captions or be cut off by the chart SVG.
            if total > 0:
                safe_text = [
                    f"{value / total * 100:.1f}%"
                    if value / total * 100 >= _MIN_PIE_LABEL_PCT
                    else ""
                    for value in numeric_values
                ]
                trace.update(
                    text=safe_text,
                    textinfo="text",
                    textposition="inside",
                    insidetextorientation="radial",
                    automargin=True,
                )
            else:
                trace.update(
                    textinfo="none",
                    textposition="inside",
                    automargin=True,
                )

            # Hover still carries the full label + exact value even when a tiny
            # slice's printed percentage is intentionally hidden.
            if not getattr(trace, "hovertemplate", None):
                trace.update(
                    hovertemplate=(
                        "%{label}<br>Exposure: $%{value:,.2f}<br>%{percent}<extra></extra>"
                    )
                )

        elif trace_type in {"scatter", "bar", "waterfall", "funnel"}:
            text = getattr(trace, "text", None)
            if text is not None:
                try:
                    has_free_text = bool(len(text))
                except TypeError:
                    has_free_text = bool(text)

    margin = getattr(getattr(figure, "layout", None), "margin", None)
    current_l = getattr(margin, "l", 0) if margin is not None else 0
    current_r = getattr(margin, "r", 0) if margin is not None else 0
    current_t = getattr(margin, "t", 0) if margin is not None else 0
    current_b = getattr(margin, "b", 0) if margin is not None else 0

    if pie_traces:
        label_count = max(
            (len(_sequence(getattr(trace, "labels", None))) for trace in pie_traces),
            default=0,
        )
        current_height = _number(getattr(figure.layout, "height", 0))
        # Give long sector/industry legends enough vertical room instead of
        # allowing the last entries to be clipped at the bottom of the chart.
        safe_height = max(
            int(current_height or 0),
            430 if label_count <= 10 else min(720, 430 + (label_count - 10) * 18),
        )
        figure.update_layout(
            height=safe_height,
            autosize=True,
            uniformtext_minsize=10,
            uniformtext_mode="hide",
            legend={
                "font": {"size": 10},
                "itemsizing": "constant",
                "tracegroupgap": 3,
            },
            margin={
                "l": _safe_margin_value(current_l, 18),
                "r": _safe_margin_value(current_r, 18),
                "t": _safe_margin_value(current_t, 52),
                "b": _safe_margin_value(current_b, 28),
                "autoexpand": True,
            },
            title={"automargin": True},
        )

    elif has_free_text:
        # Price ladders/annotation charts need a little breathing room so top
        # and bottom labels do not get sliced by the SVG boundary.
        figure.update_layout(
            autosize=True,
            margin={
                "l": _safe_margin_value(current_l, 24),
                "r": _safe_margin_value(current_r, 24),
                "t": _safe_margin_value(current_t, _MIN_TEXT_MARGIN),
                "b": _safe_margin_value(current_b, _MIN_TEXT_MARGIN),
                "autoexpand": True,
            },
            title={"automargin": True},
        )

    return figure


def install_streamlit_visual_safety() -> None:
    """Wrap st.plotly_chart once so every terminal tab gets the same guardrails."""
    if getattr(st, "_raj_visual_safety_installed", False):
        return

    original_plotly_chart = st.plotly_chart

    def safe_plotly_chart(figure_or_data, *args, **kwargs):
        try:
            harden_plotly_figure(figure_or_data)
        except Exception:
            # Presentation guardrails must never prevent the underlying chart
            # from rendering if Plotly changes an internal property.
            pass

        config = dict(kwargs.get("config") or {})
        config.setdefault("responsive", True)
        config.setdefault("displaylogo", False)
        kwargs["config"] = config
        return original_plotly_chart(figure_or_data, *args, **kwargs)

    st.plotly_chart = safe_plotly_chart
    st._raj_visual_safety_installed = True
