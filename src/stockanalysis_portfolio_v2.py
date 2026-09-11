"""Visual-safe StockAnalysis portfolio charts for Raj's Terminal.

Design goals:
- sector and industry stay side by side on normal desktop widths
- Plotly owns only the donut; long labels are rendered in a separate HTML legend
  below each chart so SVG clipping cannot cut them off
- Streamlit columns retain their normal responsive behavior on narrow screens
- tiny slices are grouped into Other so labels remain readable
- percentages are printed only when there is enough room inside the slice
- full detailed exposure remains available in an expander
"""

from __future__ import annotations

import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.stockanalysis_portfolio import _classification_frames, _portfolio_rows
from src.theme import BB_BLACK, BB_GREEN, BB_ORANGE, CHART_COLORWAY


MIN_SLICE_PCT = 2.0
MIN_PRINTED_PCT = 3.0
MAX_VISIBLE_SLICES = 12


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


def _safe_donut(chart: pd.DataFrame, label_column: str, title: str, colors: list[str]) -> go.Figure:
    values = chart["Exposure"].tolist()
    percents = chart["% Exposure"].tolist()
    text = [f"{value:.1f}%" if value >= MIN_PRINTED_PCT else "" for value in percents]

    # Long category names are deliberately NOT put in Plotly's legend. The
    # external HTML legend below the chart can wrap naturally and can never be
    # clipped by Plotly's SVG viewport.
    figure = go.Figure(
        go.Pie(
            labels=chart[label_column],
            values=values,
            hole=0.53,
            sort=False,
            domain={"x": [0.08, 0.92], "y": [0.05, 0.93]},
            marker={"colors": colors, "line": {"color": BB_BLACK, "width": 2}},
            text=text,
            textinfo="text",
            textposition="inside",
            insidetextorientation="horizontal",
            textfont={"family": "Courier New", "size": 12},
            hovertemplate=(
                "%{label}<br>Exposure: $%{value:,.2f}<br>Portfolio: %{percent}<extra></extra>"
            ),
            showlegend=False,
        )
    )

    figure.update_layout(
        title={
            "text": title,
            "x": 0.5,
            "xanchor": "center",
            "y": 0.98,
            "yanchor": "top",
            "font": {"family": "Courier New", "size": 20, "color": BB_ORANGE},
            "automargin": True,
        },
        height=430,
        autosize=True,
        paper_bgcolor=BB_BLACK,
        plot_bgcolor=BB_BLACK,
        font={"color": BB_ORANGE, "family": "Courier New", "size": 12},
        showlegend=False,
        uniformtext_minsize=10,
        uniformtext_mode="hide",
        margin={"l": 18, "r": 18, "t": 62, "b": 24, "autoexpand": True},
        annotations=[
            {
                "text": "PORTFOLIO<br>EXPOSURE",
                "x": 0.5,
                "y": 0.49,
                "xref": "paper",
                "yref": "paper",
                "showarrow": False,
                "align": "center",
                "font": {"color": BB_GREEN, "size": 13, "family": "Courier New"},
            }
        ],
    )
    return figure


def _legend_html(chart: pd.DataFrame, label_column: str, colors: list[str]) -> str:
    """Two-column wrapping legend that lives outside Plotly's clip region."""
    items: list[str] = []
    for index, row in chart.iterrows():
        color = colors[index % len(colors)] if colors else BB_ORANGE
        label = html.escape(str(row[label_column]))
        pct = float(row["% Exposure"] or 0.0)
        exposure = float(row["Exposure"] or 0.0)
        items.append(
            "<div style='display:grid;grid-template-columns:12px minmax(0,1fr) auto;"
            "align-items:start;column-gap:.42rem;min-width:0;padding:.18rem 0;'>"
            f"<span style='width:10px;height:10px;background:{color};margin-top:.24rem;display:block;'></span>"
            "<span style='min-width:0;color:#fb8b1e;font-family:Courier New,monospace;"
            "font-size:.78rem;line-height:1.25;white-space:normal;overflow-wrap:anywhere;word-break:normal;'>"
            f"{label}</span>"
            "<span style='color:#4af6c3;font-family:Courier New,monospace;font-size:.76rem;"
            "line-height:1.25;white-space:nowrap;text-align:right;' "
            f"title='${exposure:,.2f}'>{pct:.1f}%</span>"
            "</div>"
        )

    return (
        "<div style='border-top:1px solid #fb8b1e;margin-top:-.15rem;padding:.48rem .18rem .12rem .18rem;'>"
        "<div style='display:grid;grid-template-columns:repeat(2,minmax(0,1fr));"
        "column-gap:.85rem;row-gap:.06rem;width:100%;min-width:0;'>"
        + "".join(items)
        + "</div></div>"
    )


def _render_chart_panel(frame: pd.DataFrame, label_column: str, title: str, key: str) -> None:
    if frame is None or frame.empty:
        st.info(f"No {label_column.lower()} exposure could be mapped.")
        return

    chart = _chart_frame(frame, label_column)
    colors = _chart_colors(len(chart))
    st.plotly_chart(
        _safe_donut(chart, label_column, title, colors),
        width="stretch",
        config={
            "displayModeBar": False,
            "responsive": True,
            "displaylogo": False,
        },
        key=key,
    )
    st.markdown(_legend_html(chart, label_column, colors), unsafe_allow_html=True)


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

    # Streamlit columns are responsive and wrap on narrow viewports. On desktop
    # the requested sector/industry pair remains side by side. Each Plotly chart
    # is width='stretch' so it follows the actual column width instead of using a
    # fixed canvas that can overflow its parent.
    left, right = st.columns(2, gap="medium")
    with left:
        _render_chart_panel(
            sector_frame,
            "Sector",
            "SECTOR EXPOSURE",
            f"{key_prefix}_sector_{account_key}_v3",
        )
    with right:
        _render_chart_panel(
            industry_frame,
            "Industry",
            "INDUSTRY EXPOSURE",
            f"{key_prefix}_industry_{account_key}_v3",
        )

    with st.expander("FULL SECTOR + INDUSTRY BREAKDOWN"):
        st.markdown("**SECTORS // ALL EXPOSURES**")
        _detail_table(sector_frame, "Sector")
        st.markdown("**INDUSTRIES / ETF CATEGORIES // ALL EXPOSURES**")
        _detail_table(industry_frame, "Industry")

    st.caption(
        "VISUAL RULE // charts stay side by side on normal desktop widths; long legend text lives outside the "
        "Plotly SVG so it wraps instead of clipping. Slices below 2% are grouped into Other on the pie and the "
        "full ungrouped exposure remains in the breakdown. ETF NOTE // StockAnalysis reports ETF Asset Class/Category "
        "rather than one company Industry. The industry pie therefore groups ETFs by StockAnalysis ETF Category. "
        "Sector pie uses ETF sector look-through when available; otherwise it falls back to the ETF asset class."
    )
