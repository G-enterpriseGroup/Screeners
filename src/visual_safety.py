"""Global visual-safety guardrails for Raj's Terminal.

The terminal uses dense Bloomberg-style visuals. This module prevents common
layout defects such as pie-label overflow, tiny-slice text collisions, chart
legends being clipped, annotation text sitting against the figure edge, table
index gutters, excessive card whitespace, and adjacent Streamlit blocks visually
colliding.

It intentionally changes presentation only. Data, calculations, hover values,
and chart ordering are left untouched.
"""

from __future__ import annotations

import html
from typing import Any

import streamlit as st


_MIN_PIE_LABEL_PCT = 2.5
_MIN_TEXT_MARGIN = 36
_TICKET_COLUMNS = [
    "Sequence",
    "Action",
    "Qty",
    "Symbol",
    "Price Type",
    "Price",
    "Condition",
]


_GLOBAL_LAYOUT_CSS = """
<style>
/* ---------- terminal-wide anti-glitch geometry ---------- */
[data-testid="stMainBlockContainer"] {
    overflow-x:clip !important;
}

[data-testid="stHorizontalBlock"] {
    align-items:stretch !important;
}
[data-testid="stHorizontalBlock"] > div,
[data-testid="column"] {
    min-width:0 !important;
}

/* Orange section bands must never collide with the block directly above them. */
h2, h3 {
    box-sizing:border-box !important;
    width:100% !important;
    margin-top:.72rem !important;
    margin-bottom:.42rem !important;
    line-height:1.12 !important;
    overflow-wrap:anywhere !important;
}

/* Keep dense Bloomberg cards compact and equal without clipping their values. */
.bb-number-card,
[data-testid="stMetric"] {
    box-sizing:border-box !important;
    min-width:0 !important;
    overflow:visible !important;
}
.bb-number-card {
    min-height:78px !important;
    padding:.42rem .58rem !important;
}
.bb-number-value {
    font-size:clamp(1.10rem, 1.42vw, 1.65rem) !important;
    line-height:1.12 !important;
    white-space:nowrap !important;
}
.bb-number-label,
.bb-number-detail,
[data-testid="stMetricLabel"],
[data-testid="stMetricDelta"] {
    overflow-wrap:anywhere !important;
}
[data-testid="stMetric"] {
    min-height:78px !important;
    padding:.42rem .58rem !important;
}
[data-testid="stMetricValue"] {
    font-size:clamp(1.05rem, 1.35vw, 1.55rem) !important;
    line-height:1.12 !important;
}

/* Data grids stay inside their parent columns. */
[data-testid="stDataFrame"],
[data-testid="stDataEditor"],
[data-testid="stTable"] {
    box-sizing:border-box !important;
    width:100% !important;
    max-width:100% !important;
    min-width:0 !important;
    margin-top:.18rem !important;
    margin-bottom:.42rem !important;
}

/* Plotly/component canvases may use internal margins but may not escape columns. */
[data-testid="stPlotlyChart"],
[data-testid="stPlotlyChart"] > div,
iframe {
    box-sizing:border-box !important;
    width:100% !important;
    max-width:100% !important;
}

/* Long status/caption text wraps instead of pushing neighboring controls. */
[data-testid="stCaptionContainer"],
.stCaption,
[data-testid="stAlert"],
.terminal-note {
    max-width:100% !important;
    overflow-wrap:anywhere !important;
    word-break:normal !important;
    box-sizing:border-box !important;
}

/* Tiny vertical buffer between a metric/card row and the next major section. */
[data-testid="stHorizontalBlock"] + [data-testid="stMarkdownContainer"] h2,
[data-testid="stHorizontalBlock"] + [data-testid="stMarkdownContainer"] h3 {
    margin-top:.78rem !important;
}

/* ---------- exact simulated E*TRADE ticket ---------- */
.raj-ticket-shell {
    width:100%;
    max-width:100%;
    border:1px solid #fb8b1e;
    background:#000;
    overflow-x:auto;
    box-sizing:border-box;
    margin:.12rem 0 .46rem 0;
}
.raj-ticket-table {
    width:100%;
    min-width:760px;
    table-layout:fixed;
    border-collapse:collapse;
    border-spacing:0;
    font-family:"Courier New",monospace;
    background:#000;
}
.raj-ticket-table th {
    background:#fb8b1e !important;
    color:#000 !important;
    -webkit-text-fill-color:#000 !important;
    font-weight:900;
    text-transform:uppercase;
    text-align:left;
    padding:.46rem .58rem;
    border-right:1px solid #000;
    border-bottom:1px solid #fb8b1e;
    line-height:1.1;
    white-space:nowrap;
}
.raj-ticket-table td {
    background:#000;
    color:#fb8b1e;
    padding:.46rem .58rem;
    border-right:1px solid #fb8b1e;
    border-bottom:1px solid #fb8b1e;
    line-height:1.15;
    vertical-align:middle;
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
}
.raj-ticket-table tr:last-child td { border-bottom:0; }
.raj-ticket-table th:last-child,
.raj-ticket-table td:last-child { border-right:0; }
.raj-ticket-table th:nth-child(1), .raj-ticket-table td:nth-child(1) { width:9%; }
.raj-ticket-table th:nth-child(2), .raj-ticket-table td:nth-child(2) { width:12%; }
.raj-ticket-table th:nth-child(3), .raj-ticket-table td:nth-child(3) { width:9%; text-align:right; }
.raj-ticket-table th:nth-child(4), .raj-ticket-table td:nth-child(4) { width:11%; }
.raj-ticket-table th:nth-child(5), .raj-ticket-table td:nth-child(5) { width:15%; }
.raj-ticket-table th:nth-child(6), .raj-ticket-table td:nth-child(6) { width:14%; text-align:right; }
.raj-ticket-table th:nth-child(7), .raj-ticket-table td:nth-child(7) { width:20%; }
.raj-ticket-money,
.raj-ticket-qty { color:#4af6c3 !important; font-weight:900; }
.raj-ticket-stop { color:#ff433d !important; font-weight:900; }
.raj-ticket-primary { color:#0068ff !important; font-weight:900; }

@media (max-width:1050px) {
    .raj-ticket-table { font-size:.84rem; }
    .raj-ticket-table th, .raj-ticket-table td { padding:.38rem .44rem; }
    .bb-number-value { font-size:1.18rem !important; }
}
</style>
"""


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


def _preserve_pie_textposition(trace) -> bool:
    """Allow purpose-built charts to opt out of the generic inside-label rule."""
    meta = getattr(trace, "meta", None)
    return isinstance(meta, dict) and bool(meta.get("raj_preserve_textposition"))


def _title_has_text(figure) -> bool:
    """Avoid creating an empty Plotly title object that can render as 'undefined'."""
    title = getattr(getattr(figure, "layout", None), "title", None)
    text = getattr(title, "text", None) if title is not None else None
    return text not in (None, "")


def _frame_from_table_like(data):
    """Return a pandas-like frame from DataFrame or Styler without importing pandas."""
    if data is None:
        return None
    if hasattr(data, "columns") and hasattr(data, "iterrows"):
        return data
    nested = getattr(data, "data", None)
    if hasattr(nested, "columns") and hasattr(nested, "iterrows"):
        return nested
    return None


def _is_simulated_ticket(data) -> bool:
    frame = _frame_from_table_like(data)
    if frame is None:
        return False
    return list(frame.columns) == _TICKET_COLUMNS and len(frame) <= 6


def _ticket_cell(column: str, value: Any, row: Any) -> tuple[str, str]:
    css_class = ""
    if column == "Qty":
        try:
            text = f"{int(float(value)):,}"
        except (TypeError, ValueError):
            text = str(value)
        css_class = "raj-ticket-qty"
    elif column == "Price":
        try:
            text = f"${float(value):,.2f}"
        except (TypeError, ValueError):
            text = str(value)
        css_class = "raj-ticket-money"
        if str(row.get("Price Type", "")).upper() == "STOP":
            css_class = "raj-ticket-stop"
    else:
        text = str(value)
        if column == "Condition" and text.upper() == "PRIMARY":
            css_class = "raj-ticket-primary"
    return html.escape(text), css_class


def _render_simulated_ticket(data) -> None:
    """Render the 3-row simulated ticket without Streamlit's blank grid gutter."""
    frame = _frame_from_table_like(data)
    header = "".join(f"<th>{html.escape(column)}</th>" for column in _TICKET_COLUMNS)
    rows = []
    for _, row in frame.iterrows():
        cells = []
        for column in _TICKET_COLUMNS:
            text, css_class = _ticket_cell(column, row.get(column, ""), row)
            class_attr = f' class="{css_class}"' if css_class else ""
            cells.append(f"<td{class_attr}>{text}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        '<div class="raj-ticket-shell"><table class="raj-ticket-table">'
        '<thead><tr>' + header + '</tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table></div>',
        unsafe_allow_html=True,
    )


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

            if _preserve_pie_textposition(trace):
                trace.update(automargin=True)
            elif total > 0:
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
        safe_height = max(
            int(current_height or 0),
            430 if label_count <= 10 else min(720, 430 + (label_count - 10) * 18),
        )
        layout_updates = {
            "height": safe_height,
            "autosize": True,
            "uniformtext_minsize": 10,
            "uniformtext_mode": "hide",
            "legend": {
                "font": {"size": 10},
                "itemsizing": "constant",
                "tracegroupgap": 3,
            },
            "margin": {
                "l": _safe_margin_value(current_l, 18),
                "r": _safe_margin_value(current_r, 18),
                "t": _safe_margin_value(current_t, 52),
                "b": _safe_margin_value(current_b, 28),
                "autoexpand": True,
            },
        }
        if _title_has_text(figure):
            layout_updates["title"] = {"automargin": True}
        figure.update_layout(**layout_updates)

    elif has_free_text:
        layout_updates = {
            "autosize": True,
            "margin": {
                "l": _safe_margin_value(current_l, 24),
                "r": _safe_margin_value(current_r, 24),
                "t": _safe_margin_value(current_t, _MIN_TEXT_MARGIN),
                "b": _safe_margin_value(current_b, _MIN_TEXT_MARGIN),
                "autoexpand": True,
            },
        }
        if _title_has_text(figure):
            layout_updates["title"] = {"automargin": True}
        figure.update_layout(**layout_updates)

    return figure


def install_streamlit_visual_safety() -> None:
    """Install terminal-wide Plotly, table, and layout safety exactly once."""
    if getattr(st, "_raj_visual_safety_installed", False):
        return

    original_plotly_chart = st.plotly_chart
    original_dataframe = st.dataframe

    # Render after the core stylesheet so these geometry fixes win the cascade.
    st.markdown(_GLOBAL_LAYOUT_CSS, unsafe_allow_html=True)

    def safe_plotly_chart(figure_or_data, *args, **kwargs):
        try:
            harden_plotly_figure(figure_or_data)
        except Exception:
            pass

        config = dict(kwargs.get("config") or {})
        config.setdefault("responsive", True)
        config.setdefault("displaylogo", False)
        kwargs["config"] = config
        return original_plotly_chart(figure_or_data, *args, **kwargs)

    def safe_dataframe(data=None, *args, **kwargs):
        if _is_simulated_ticket(data):
            _render_simulated_ticket(data)
            return None
        return original_dataframe(data, *args, **kwargs)

    st.plotly_chart = safe_plotly_chart
    st.dataframe = safe_dataframe
    st._raj_visual_safety_installed = True
