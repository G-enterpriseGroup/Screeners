"""Visual-safe StockAnalysis portfolio charts for Raj's Terminal.

Design goals:
- preserve the original Bloomberg-style side-by-side look Raj preferred
- sector legend lives to the LEFT of its donut; industry legend lives to the RIGHT
- every chart panel uses the same hidden alignment grid and shared height
- Bloomberg-style orange panel borders + header bars frame each visual
- legends stay inside each Plotly canvas so Streamlit columns cannot clip them
- long legend labels wrap before Plotly renders them
- every visible sector and industry receives its own distinct color
- sector and industry palettes do not reuse colors across the two charts
- tiny slices are grouped into Other and tiny percentage text is suppressed
- full ungrouped exposure remains available in an expander
"""

from __future__ import annotations

import html
import textwrap

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.stockanalysis_portfolio import _classification_frames, _portfolio_rows
from src.theme import BB_BLACK, BB_GREEN, BB_ORANGE


MIN_SLICE_PCT = 2.0
MIN_PRINTED_PCT = 3.0
MAX_VISIBLE_SLICES = 12
LEGEND_WRAP_CHARS = 18
LEGEND_MAX_LINES = 3
DONUT_HOLE = 0.47

# ---------------------------------------------------------------------------
# Bloomberg panel geometry.
# These constants form an invisible alignment grid shared by BOTH charts.
# Keeping every important y-coordinate identical prevents the sector/industry
# panels from drifting when one side has more legend rows than the other.
# ---------------------------------------------------------------------------
PANEL_LEFT = 0.005
PANEL_RIGHT = 0.995
PANEL_BOTTOM = 0.015
PANEL_TOP = 0.995
HEADER_BOTTOM = 0.915
CONTENT_TOP = 0.885
CONTENT_BOTTOM = 0.065
CONTENT_CENTER_Y = (CONTENT_TOP + CONTENT_BOTTOM) / 2
PANEL_MIN_HEIGHT = 560
PANEL_MAX_HEIGHT = 660

# High-contrast qualitative palette designed for the terminal's black background.
# The first 16 colors are reserved for SECTOR; the second 16 are reserved for
# INDUSTRY. With MAX_VISIBLE_SLICES=12 (+ Other), neither chart repeats a color
# and the two charts do not reuse one another's slice colors.
DISTINCT_EXPOSURE_COLORS = [
    # Sector palette
    "#4AF6C3", "#0068FF", "#FB8B1E", "#FF433D",
    "#B26CFF", "#FFE066", "#27D7FF", "#FF5CB8",
    "#7BD23C", "#00A7A7", "#C58CFF", "#FF9F7A",
    "#A1E8AF", "#F45B69", "#1DD3B0", "#A9DEF9",
    # Industry palette — intentionally different from every sector color
    "#E63946", "#2A9D8F", "#F4A261", "#8D5CF6",
    "#E9C46A", "#00B4D8", "#F72585", "#90BE6D",
    "#4361EE", "#FFB703", "#577590", "#D00000",
    "#8338EC", "#06D6A0", "#EF476F", "#118AB2",
]
SECTOR_COLOR_OFFSET = 0
INDUSTRY_COLOR_OFFSET = 16


def _chart_frame(frame: pd.DataFrame, label_column: str) -> pd.DataFrame:
    """Keep major exposures visible and combine tiny tails into one safe slice."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[label_column, "Exposure", "% Exposure"])

    work = frame[[label_column, "Exposure"]].copy()
    work["Exposure"] = pd.to_numeric(work["Exposure"], errors="coerce").fillna(0.0)
    work = work[work["Exposure"] > 0].sort_values("Exposure", ascending=False).reset_index(drop=True)
    if work.empty:
        return work

    total = float(work["Exposure"].sum())
    work["% Exposure"] = work["Exposure"] / total * 100 if total else 0.0

    major = work[work["% Exposure"] >= MIN_SLICE_PCT].head(MAX_VISIBLE_SLICES).copy()
    major_indexes = set(major.index.tolist())
    tail = work[~work.index.isin(major_indexes)]

    if not tail.empty:
        other_value = float(tail["Exposure"].sum())
        other_row = pd.DataFrame(
            [{
                label_column: f"Other (<{MIN_SLICE_PCT:.0f}% each)",
                "Exposure": other_value,
                "% Exposure": other_value / total * 100 if total else 0.0,
            }]
        )
        major = pd.concat([major, other_row], ignore_index=True)

    major = major.sort_values("Exposure", ascending=False).reset_index(drop=True)
    total_major = float(major["Exposure"].sum())
    major["% Exposure"] = major["Exposure"] / total_major * 100 if total_major else 0.0
    return major


def _chart_colors(count: int, *, palette_offset: int) -> list[str]:
    """Return unique, non-repeating colors for one exposure chart."""
    count = max(0, int(count))
    palette_end = palette_offset + count
    if palette_end <= len(DISTINCT_EXPOSURE_COLORS):
        return DISTINCT_EXPOSURE_COLORS[palette_offset:palette_end]

    colors = list(DISTINCT_EXPOSURE_COLORS[palette_offset:])
    remaining = count - len(colors)
    for index in range(remaining):
        hue = (17 + (index * 137.508) + palette_offset * 11) % 360
        colors.append(f"hsl({hue:.1f}, 78%, 58%)")
    return colors


def _wrap_legend_label(value: object) -> str:
    """Wrap long Plotly legend text so it stays inside a half-width panel."""
    raw = str(value or "").strip()
    if not raw:
        return "—"

    chunks = textwrap.wrap(
        raw,
        width=LEGEND_WRAP_CHARS,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [raw]

    if len(chunks) > LEGEND_MAX_LINES:
        chunks = chunks[:LEGEND_MAX_LINES]
        last = chunks[-1].rstrip()
        if not last.endswith("…"):
            chunks[-1] = (last[:-1] if len(last) >= LEGEND_WRAP_CHARS else last) + "…"

    return "<br>".join(html.escape(chunk) for chunk in chunks)


def _shared_panel_height(sector_chart: pd.DataFrame, industry_chart: pd.DataFrame) -> int:
    """Use one height for both cards so their headers/borders/baselines align."""
    item_count = max(len(sector_chart), len(industry_chart), 1)
    return max(PANEL_MIN_HEIGHT, min(PANEL_MAX_HEIGHT, 490 + item_count * 13))


def _panel_shapes(center_x: float) -> list[dict]:
    """Visible Bloomberg frame plus invisible guides used as alignment rails."""
    transparent = "rgba(0,0,0,0)"
    return [
        # Outer orange Bloomberg frame.
        {
            "type": "rect",
            "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT,
            "y0": PANEL_BOTTOM, "y1": PANEL_TOP,
            "line": {"color": BB_ORANGE, "width": 1.4},
            "fillcolor": "rgba(0,0,0,0)",
            "layer": "above",
        },
        # Solid orange terminal header bar.
        {
            "type": "rect",
            "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT,
            "y0": HEADER_BOTTOM, "y1": PANEL_TOP,
            "line": {"color": BB_ORANGE, "width": 1},
            "fillcolor": BB_ORANGE,
            "layer": "above",
        },
        # Subtle body/header separator.
        {
            "type": "line",
            "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT,
            "y0": HEADER_BOTTOM, "y1": HEADER_BOTTOM,
            "line": {"color": BB_ORANGE, "width": 1.2},
            "layer": "above",
        },
        # Hidden alignment rails: same horizontal centerline + donut centerline
        # on both cards. They are intentionally transparent in production.
        {
            "type": "line",
            "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT,
            "y0": CONTENT_CENTER_Y, "y1": CONTENT_CENTER_Y,
            "line": {"color": transparent, "width": 1},
            "layer": "below",
        },
        {
            "type": "line",
            "xref": "paper", "yref": "paper",
            "x0": center_x, "x1": center_x,
            "y0": CONTENT_BOTTOM, "y1": CONTENT_TOP,
            "line": {"color": transparent, "width": 1},
            "layer": "below",
        },
    ]


def _safe_donut(
    chart: pd.DataFrame,
    label_column: str,
    title: str,
    colors: list[str],
    *,
    legend_side: str,
    panel_height: int,
) -> go.Figure:
    """Create one precisely aligned Bloomberg exposure panel."""
    full_labels = [str(value) for value in chart[label_column].tolist()]
    display_labels = [_wrap_legend_label(value) for value in full_labels]
    values = chart["Exposure"].tolist()
    percents = chart["% Exposure"].tolist()
    inside_text = [
        f"{value:.1f}%" if float(value) >= MIN_PRINTED_PCT else ""
        for value in percents
    ]

    # Identical y-domain on both cards is the key visual alignment rule.
    if legend_side == "left":
        pie_domain = {"x": [0.38, 0.98], "y": [CONTENT_BOTTOM, CONTENT_TOP]}
        legend = {
            "x": 0.018, "xanchor": "left",
            "y": CONTENT_CENTER_Y, "yanchor": "middle",
            "orientation": "v",
            "font": {"color": BB_ORANGE, "family": "Courier New", "size": 10},
            "itemsizing": "constant",
            "tracegroupgap": 2,
            "bgcolor": BB_BLACK,
            "borderwidth": 0,
        }
    else:
        pie_domain = {"x": [0.02, 0.62], "y": [CONTENT_BOTTOM, CONTENT_TOP]}
        legend = {
            "x": 0.655, "xanchor": "left",
            "y": CONTENT_CENTER_Y, "yanchor": "middle",
            "orientation": "v",
            "font": {"color": BB_ORANGE, "family": "Courier New", "size": 10},
            "itemsizing": "constant",
            "tracegroupgap": 2,
            "bgcolor": BB_BLACK,
            "borderwidth": 0,
        }

    center_x = (pie_domain["x"][0] + pie_domain["x"][1]) / 2
    center_y = (pie_domain["y"][0] + pie_domain["y"][1]) / 2

    figure = go.Figure(
        go.Pie(
            labels=display_labels,
            values=values,
            customdata=full_labels,
            hole=DONUT_HOLE,
            sort=False,
            domain=pie_domain,
            marker={"colors": colors, "line": {"color": BB_BLACK, "width": 2}},
            text=inside_text,
            textinfo="text",
            textposition="inside",
            insidetextorientation="horizontal",
            textfont={"family": "Courier New", "size": 11},
            hovertemplate=(
                "%{customdata}<br>Exposure: $%{value:,.2f}<br>Portfolio: %{percent}<extra></extra>"
            ),
            showlegend=True,
        )
    )

    figure.update_layout(
        height=panel_height,
        autosize=True,
        paper_bgcolor=BB_BLACK,
        plot_bgcolor=BB_BLACK,
        font={"color": BB_ORANGE, "family": "Courier New", "size": 11},
        showlegend=True,
        legend=legend,
        uniformtext_minsize=9,
        uniformtext_mode="hide",
        margin={"l": 5, "r": 5, "t": 5, "b": 5, "autoexpand": False},
        shapes=_panel_shapes(center_x),
        annotations=[
            # Bloomberg-style header title.
            {
                "text": f"<b>{html.escape(title)}</b>",
                "x": 0.025, "y": (HEADER_BOTTOM + PANEL_TOP) / 2,
                "xref": "paper", "yref": "paper",
                "xanchor": "left", "yanchor": "middle",
                "showarrow": False,
                "font": {"color": BB_BLACK, "size": 16, "family": "Courier New"},
            },
            # Small right-side status tag gives the card a terminal-panel feel.
            {
                "text": "MV WEIGHTED",
                "x": 0.975, "y": (HEADER_BOTTOM + PANEL_TOP) / 2,
                "xref": "paper", "yref": "paper",
                "xanchor": "right", "yanchor": "middle",
                "showarrow": False,
                "font": {"color": BB_BLACK, "size": 9, "family": "Courier New"},
            },
            # Exact donut-center annotation.
            {
                "text": "PORTFOLIO<br>EXPOSURE",
                "x": center_x, "y": center_y,
                "xref": "paper", "yref": "paper",
                "xanchor": "center", "yanchor": "middle",
                "showarrow": False,
                "align": "center",
                "width": 110,
                "font": {"color": BB_GREEN, "size": 12, "family": "Courier New"},
            },
        ],
    )
    return figure


def _detail_table(frame: pd.DataFrame, label_column: str) -> None:
    if frame is None or frame.empty:
        return
    detail = frame[[label_column, "Exposure", "% Exposure"]].copy()
    detail["Exposure"] = pd.to_numeric(detail["Exposure"], errors="coerce")
    detail["% Exposure"] = pd.to_numeric(detail["% Exposure"], errors="coerce")
    st.dataframe(
        detail,
        hide_index=True,
        width="stretch",
        column_config={
            "Exposure": st.column_config.NumberColumn(format="$%.2f"),
            "% Exposure": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )


def _render_chart_panel(
    chart: pd.DataFrame,
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

    colors = _chart_colors(len(chart), palette_offset=palette_offset)
    st.plotly_chart(
        _safe_donut(
            chart,
            label_column,
            title,
            colors,
            legend_side=legend_side,
            panel_height=panel_height,
        ),
        width="stretch",
        config={
            "displayModeBar": False,
            "responsive": True,
            "displaylogo": False,
        },
        key=key,
    )


def render_stockanalysis_portfolio(
    account_state_key: str,
    *,
    key_prefix: str,
    title: str = "SECTOR + INDUSTRY EXPOSURE",
    show_classification_table: bool = True,
) -> None:
    """Render responsive side-by-side sector and industry analytics."""
    frame, account_key = _portfolio_rows(account_state_key)
    if frame.empty:
        return

    st.subheader(title)
    st.caption(
        "STOCK CLASSIFICATION // StockAnalysis company profile // INDUSTRY = supplied tr[4] XPath // "
        "SECTOR = supplied tr[5] XPath // ETF profile = StockAnalysis ETF overview // classifications cached 24h"
    )

    with st.spinner("MAPPING STOCKANALYSIS SECTORS + INDUSTRIES..."):
        detail_frame, sector_frame, industry_frame = _classification_frames(frame)

    if detail_frame.empty:
        st.info("Sector / industry classification is not available for this portfolio snapshot yet.")
        return

    if show_classification_table:
        table = detail_frame.copy()
        table["Market Value"] = pd.to_numeric(table["Market Value"], errors="coerce")
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={"Market Value": st.column_config.NumberColumn(format="$%.2f")},
            key=f"{key_prefix}_classification_{account_key}",
        )

    sector_chart = _chart_frame(sector_frame, "Sector")
    industry_chart = _chart_frame(industry_frame, "Industry")
    panel_height = _shared_panel_height(sector_chart, industry_chart)

    # Two equal-width cards with a shared height and shared hidden alignment
    # grid. The medium gutter keeps the orange card borders visually separate.
    left, right = st.columns(2, gap="medium")
    with left:
        _render_chart_panel(
            sector_chart,
            "Sector",
            "SECTOR EXPOSURE",
            f"{key_prefix}_sector_{account_key}_v7",
            legend_side="left",
            palette_offset=SECTOR_COLOR_OFFSET,
            panel_height=panel_height,
        )
    with right:
        _render_chart_panel(
            industry_chart,
            "Industry",
            "INDUSTRY EXPOSURE",
            f"{key_prefix}_industry_{account_key}_v7",
            legend_side="right",
            palette_offset=INDUSTRY_COLOR_OFFSET,
            panel_height=panel_height,
        )

    with st.expander("FULL SECTOR + INDUSTRY BREAKDOWN"):
        st.markdown("**SECTORS // ALL EXPOSURES**")
        _detail_table(sector_frame, "Sector")
        st.markdown("**INDUSTRIES / ETF CATEGORIES // ALL EXPOSURES**")
        _detail_table(industry_frame, "Industry")

    st.caption(
        "BLOOMBERG PANEL // mirrored side-by-side exposure cards // market-value weighted // shared alignment grid // "
        "each visible sector/industry has a distinct color // slices below 2% grouped into Other // percentages below "
        "3% shown on hover/breakdown only. Full ungrouped exposure remains available above."
    )
