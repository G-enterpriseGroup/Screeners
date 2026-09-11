"""Visual-safe StockAnalysis portfolio charts for Raj's Terminal.

This renderer keeps the classification logic from stockanalysis_portfolio.py but
uses a layout designed for dense portfolios:
- sector and industry charts render one-per-row at full terminal width
- tiny slices are grouped into Other so legends remain readable
- percentages stay inside slices only
- the donut reserves a dedicated right-side legend area
- chart height grows with the number of legend entries
- full detailed exposure remains available in an expander
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.stockanalysis_portfolio import _classification_frames, _portfolio_rows
from src.theme import BB_BLACK, BB_GREEN, BB_ORANGE, CHART_COLORWAY


MIN_SLICE_PCT = 2.0
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

    major = work[(work["% Exposure"] >= MIN_SLICE_PCT)].head(MAX_VISIBLE_SLICES).copy()
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


def _safe_donut(frame: pd.DataFrame, label_column: str, title: str) -> go.Figure:
    chart = _chart_frame(frame, label_column)
    values = chart["Exposure"].tolist()
    percents = chart["% Exposure"].tolist()
    text = [f"{value:.1f}%" if value >= 3.0 else "" for value in percents]
    colors = [CHART_COLORWAY[index % len(CHART_COLORWAY)] for index in range(len(chart))]

    # The pie owns only ~62% of horizontal figure space. The remainder is a
    # dedicated legend lane, which prevents long StockAnalysis categories from
    # being chopped off at the right edge.
    pie_domain = {"x": [0.02, 0.62], "y": [0.06, 0.94]}
    center_x = sum(pie_domain["x"]) / 2
    center_y = sum(pie_domain["y"]) / 2

    figure = go.Figure(
        go.Pie(
            labels=chart[label_column],
            values=values,
            hole=0.52,
            sort=False,
            domain=pie_domain,
            marker={"colors": colors, "line": {"color": BB_BLACK, "width": 2}},
            text=text,
            textinfo="text",
            textposition="inside",
            insidetextorientation="horizontal",
            textfont={"family": "Courier New", "size": 12},
            hovertemplate=(
                "%{label}<br>Exposure: $%{value:,.2f}<br>Portfolio: %{percent}<extra></extra>"
            ),
        )
    )

    legend_count = max(1, len(chart))
    safe_height = max(500, min(720, 360 + legend_count * 28))
    figure.update_layout(
        title={
            "text": title,
            "x": 0.5,
            "xanchor": "center",
            "y": 0.98,
            "yanchor": "top",
            "font": {"family": "Courier New", "size": 20, "color": BB_ORANGE},
        },
        height=safe_height,
        autosize=True,
        paper_bgcolor=BB_BLACK,
        plot_bgcolor=BB_BLACK,
        font={"color": BB_ORANGE, "family": "Courier New", "size": 12},
        showlegend=True,
        legend={
            "x": 0.66,
            "xanchor": "left",
            "y": 0.5,
            "yanchor": "middle",
            "orientation": "v",
            "font": {"color": BB_ORANGE, "family": "Courier New", "size": 11},
            "itemsizing": "constant",
            "tracegroupgap": 4,
            "bgcolor": BB_BLACK,
        },
        uniformtext_minsize=10,
        uniformtext_mode="hide",
        margin={"l": 28, "r": 28, "t": 72, "b": 42, "autoexpand": True},
        annotations=[
            {
                "text": "PORTFOLIO<br>EXPOSURE",
                "x": center_x,
                "y": center_y,
                "xref": "paper",
                "yref": "paper",
                "showarrow": False,
                "align": "center",
                "font": {"color": BB_GREEN, "size": 13, "family": "Courier New"},
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


def render_stockanalysis_portfolio(
    account_state_key: str,
    *,
    key_prefix: str,
    title: str = "SECTOR + INDUSTRY EXPOSURE",
    show_classification_table: bool = True,
) -> None:
    """Render full-width sector and industry analytics with no visual clipping."""
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

    # One chart per row. The previous two-column layout was too narrow for a
    # large industry legend and caused the exact clipping shown in the user's
    # screenshot. Full width is intentionally preferred over compactness here.
    if sector_frame.empty:
        st.info("No sector exposure could be mapped.")
    else:
        st.plotly_chart(
            _safe_donut(sector_frame, "Sector", "SECTOR EXPOSURE"),
            width="stretch",
            config={"displayModeBar": False, "responsive": True, "displaylogo": False},
            key=f"{key_prefix}_sector_{account_key}_v2",
        )

    if industry_frame.empty:
        st.info("No industry exposure could be mapped.")
    else:
        st.plotly_chart(
            _safe_donut(industry_frame, "Industry", "INDUSTRY EXPOSURE"),
            width="stretch",
            config={"displayModeBar": False, "responsive": True, "displaylogo": False},
            key=f"{key_prefix}_industry_{account_key}_v2",
        )

    with st.expander("FULL SECTOR + INDUSTRY BREAKDOWN"):
        st.markdown("**SECTORS // ALL EXPOSURES**")
        _detail_table(sector_frame, "Sector")
        st.markdown("**INDUSTRIES / ETF CATEGORIES // ALL EXPOSURES**")
        _detail_table(industry_frame, "Industry")

    st.caption(
        "VISUAL RULE // slices below 2% are grouped into Other on the pie so labels and legends never collide. "
        "The full ungrouped exposure is preserved in the breakdown above. ETF NOTE // StockAnalysis reports ETF "
        "Asset Class/Category rather than one company Industry. The industry pie therefore groups ETFs by "
        "StockAnalysis ETF Category. Sector pie uses ETF sector look-through when available; otherwise it falls "
        "back to the ETF asset class."
    )
