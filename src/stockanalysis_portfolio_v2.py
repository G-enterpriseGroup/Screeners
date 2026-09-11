"""Visual-safe StockAnalysis portfolio charts for Raj's Terminal.

Design goals:
- preserve the original Bloomberg-style side-by-side look Raj preferred
- sector legend lives to the LEFT of its donut; industry legend lives to the RIGHT
- legends are kept INSIDE each Plotly canvas so they cannot be cut off by Streamlit columns
- long legend labels are wrapped before Plotly renders them
- tiny slices are grouped into Other and tiny percentage text is suppressed
- chart height grows with legend density
- full ungrouped exposure remains available in an expander
"""

from __future__ import annotations

import html
import textwrap

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.stockanalysis_portfolio import _classification_frames, _portfolio_rows
from src.theme import BB_BLACK, BB_GREEN, BB_ORANGE, CHART_COLORWAY


MIN_SLICE_PCT = 2.0
MIN_PRINTED_PCT = 3.0
MAX_VISIBLE_SLICES = 12
LEGEND_WRAP_CHARS = 18
LEGEND_MAX_LINES = 3


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
            [
                {
                    label_column: f"Other (<{MIN_SLICE_PCT:.0f}% each)",
                    "Exposure": other_value,
                    "% Exposure": other_value / total * 100 if total else 0.0,
                }
            ]
        )
        major = pd.concat([major, other_row], ignore_index=True)

    major = major.sort_values("Exposure", ascending=False).reset_index(drop=True)
    total_major = float(major["Exposure"].sum())
    major["% Exposure"] = major["Exposure"] / total_major * 100 if total_major else 0.0
    return major


def _chart_colors(count: int) -> list[str]:
    return [CHART_COLORWAY[index % len(CHART_COLORWAY)] for index in range(max(0, count))]


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


def _safe_donut(
    chart: pd.DataFrame,
    label_column: str,
    title: str,
    colors: list[str],
    *,
    legend_side: str,
) -> go.Figure:
    """Create a mirrored donut/legend layout that cannot spill outside its column."""
    full_labels = [str(value) for value in chart[label_column].tolist()]
    display_labels = [_wrap_legend_label(value) for value in full_labels]
    values = chart["Exposure"].tolist()
    percents = chart["% Exposure"].tolist()
    inside_text = [
        f"{value:.1f}%" if float(value) >= MIN_PRINTED_PCT else ""
        for value in percents
    ]

    if legend_side == "left":
        pie_domain = {"x": [0.38, 0.98], "y": [0.08, 0.92]}
        legend = {
            "x": 0.01,
            "xanchor": "left",
            "y": 0.50,
            "yanchor": "middle",
            "orientation": "v",
            "font": {"color": BB_ORANGE, "family": "Courier New", "size": 10},
            "itemsizing": "constant",
            "tracegroupgap": 2,
            "bgcolor": BB_BLACK,
            "borderwidth": 0,
        }
    else:
        pie_domain = {"x": [0.02, 0.62], "y": [0.08, 0.92]}
        legend = {
            "x": 0.66,
            "xanchor": "left",
            "y": 0.50,
            "yanchor": "middle",
            "orientation": "v",
            "font": {"color": BB_ORANGE, "family": "Courier New", "size": 10},
            "itemsizing": "constant",
            "tracegroupgap": 2,
            "bgcolor": BB_BLACK,
            "borderwidth": 0,
        }

    center_x = (pie_domain["x"][0] + pie_domain["x"][1]) / 2
    center_y = (pie_domain["y"][0] + pie_domain["y"][1]) / 2
    safe_height = max(500, min(650, 390 + len(chart) * 20))

    figure = go.Figure(
        go.Pie(
            labels=display_labels,
            values=values,
            customdata=full_labels,
            hole=0.52,
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
        title={
            "text": title,
            "x": 0.5,
            "xanchor": "center",
            "y": 0.985,
            "yanchor": "top",
            "font": {"family": "Courier New", "size": 19, "color": BB_ORANGE},
            "automargin": True,
        },
        height=safe_height,
        autosize=True,
        paper_bgcolor=BB_BLACK,
        plot_bgcolor=BB_BLACK,
        font={"color": BB_ORANGE, "family": "Courier New", "size": 11},
        showlegend=True,
        legend=legend,
        uniformtext_minsize=9,
        uniformtext_mode="hide",
        margin={"l": 14, "r": 14, "t": 68, "b": 34, "autoexpand": True},
        annotations=[
            {
                "text": "PORTFOLIO<br>EXPOSURE",
                "x": center_x,
                "y": center_y,
                "xref": "paper",
                "yref": "paper",
                "showarrow": False,
                "align": "center",
                "font": {"color": BB_GREEN, "size": 12, "family": "Courier New"},
            }
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
    frame: pd.DataFrame,
    label_column: str,
    title: str,
    key: str,
    *,
    legend_side: str,
) -> None:
    if frame is None or frame.empty:
        st.info(f"No {label_column.lower()} exposure could be mapped.")
        return

    chart = _chart_frame(frame, label_column)
    colors = _chart_colors(len(chart))
    st.plotly_chart(
        _safe_donut(
            chart,
            label_column,
            title,
            colors,
            legend_side=legend_side,
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

    # Preserve Raj's preferred original visual: two donuts on one row with the
    # legends flanking the pair. Each legend lives inside its own Plotly paper
    # region and wraps long labels, preventing the clipping seen previously.
    left, right = st.columns(2, gap="small")
    with left:
        _render_chart_panel(
            sector_frame,
            "Sector",
            "SECTOR EXPOSURE",
            f"{key_prefix}_sector_{account_key}_v4",
            legend_side="left",
        )
    with right:
        _render_chart_panel(
            industry_frame,
            "Industry",
            "INDUSTRY EXPOSURE",
            f"{key_prefix}_industry_{account_key}_v4",
            legend_side="right",
        )

    with st.expander("FULL SECTOR + INDUSTRY BREAKDOWN"):
        st.markdown("**SECTORS // ALL EXPOSURES**")
        _detail_table(sector_frame, "Sector")
        st.markdown("**INDUSTRIES / ETF CATEGORIES // ALL EXPOSURES**")
        _detail_table(industry_frame, "Industry")

    st.caption(
        "VISUAL RULE // charts stay side by side on desktop with mirrored legends. Long legend names wrap inside "
        "their own chart panel, slices below 2% are grouped into Other, and percentages below 3% are hover/legend "
        "only. The full ungrouped exposure remains available in the breakdown above. ETF NOTE // StockAnalysis "
        "reports ETF Asset Class/Category rather than one company Industry."
    )
