"""Bloomberg-style StockAnalysis portfolio exposure panels.

V3 keeps the preferred side-by-side layout while adding:
- distinct sector/industry colors
- framed Bloomberg headers and shared alignment geometry
- 2%-3% slice percentages outside the donut with Plotly leader lines
- contributor hover showing tickers and dollar market-value contribution
- comma-formatted dollar tables
- no empty Plotly title objects (global safety also guards this)
"""

from __future__ import annotations

import html
import textwrap
from collections import defaultdict
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.stockanalysis_portfolio import (
    _cached_sector_lookthrough,
    _classification_frames,
    _clean,
    _portfolio_rows,
    stockanalysis_classification,
)
from src.theme import BB_BLACK, BB_GREEN, BB_ORANGE


MIN_SLICE_PCT = 2.0
MIN_PRINTED_PCT = 3.0
MAX_VISIBLE_SLICES = 12
LEGEND_WRAP_CHARS = 18
LEGEND_MAX_LINES = 3
DONUT_HOLE = 0.47

PANEL_LEFT = 0.005
PANEL_RIGHT = 0.995
PANEL_BOTTOM = 0.015
PANEL_TOP = 0.995
HEADER_BOTTOM = 0.915
CONTENT_TOP = 0.875
CONTENT_BOTTOM = 0.075
CONTENT_CENTER_Y = (CONTENT_TOP + CONTENT_BOTTOM) / 2
PANEL_MIN_HEIGHT = 575
PANEL_MAX_HEIGHT = 690

DISTINCT_EXPOSURE_COLORS = [
    # Sector-only palette
    "#4AF6C3", "#0068FF", "#FB8B1E", "#FF433D",
    "#B26CFF", "#FFE066", "#27D7FF", "#FF5CB8",
    "#7BD23C", "#00A7A7", "#C58CFF", "#FF9F7A",
    "#A1E8AF", "#F45B69", "#1DD3B0", "#A9DEF9",
    # Industry-only palette
    "#E63946", "#2A9D8F", "#F4A261", "#8D5CF6",
    "#E9C46A", "#00B4D8", "#F72585", "#90BE6D",
    "#4361EE", "#FFB703", "#577590", "#D00000",
    "#8338EC", "#06D6A0", "#EF476F", "#118AB2",
]
SECTOR_COLOR_OFFSET = 0
INDUSTRY_COLOR_OFFSET = 16


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _chart_colors(count: int, *, palette_offset: int) -> list[str]:
    count = max(0, int(count))
    end = palette_offset + count
    if end <= len(DISTINCT_EXPOSURE_COLORS):
        return DISTINCT_EXPOSURE_COLORS[palette_offset:end]
    colors = list(DISTINCT_EXPOSURE_COLORS[palette_offset:])
    for index in range(count - len(colors)):
        hue = (17 + index * 137.508 + palette_offset * 11) % 360
        colors.append(f"hsl({hue:.1f}, 78%, 58%)")
    return colors


def _wrap_legend_label(value: object) -> str:
    raw = str(value or "").strip() or "—"
    chunks = textwrap.wrap(
        raw,
        width=LEGEND_WRAP_CHARS,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [raw]
    if len(chunks) > LEGEND_MAX_LINES:
        chunks = chunks[:LEGEND_MAX_LINES]
        chunks[-1] = chunks[-1].rstrip() + "…"
    return "<br>".join(html.escape(chunk) for chunk in chunks)


def _add_contributor(store: dict[str, dict[str, float]], bucket: str, symbol: str, value: float) -> None:
    if not bucket or not symbol or value <= 0:
        return
    store[bucket][symbol] = store[bucket].get(symbol, 0.0) + float(value)


def _contributor_maps(frame: pd.DataFrame) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Map each sector/industry to the holdings that create its exposure."""
    sector_members: dict[str, dict[str, float]] = defaultdict(dict)
    industry_members: dict[str, dict[str, float]] = defaultdict(dict)

    for _, row in frame.iterrows():
        symbol = _clean(row.get("Symbol")).upper()
        security_type = _clean(row.get("Type")).upper()
        try:
            market_value = abs(float(row.get("Market Value") or 0.0))
        except (TypeError, ValueError):
            market_value = 0.0
        if not symbol or market_value <= 0:
            continue

        classification = stockanalysis_classification(symbol, security_type)
        kind = classification.get("kind", "unknown")
        direct_sector = _clean(classification.get("sector")) or "Other / Unclassified"
        industry = _clean(classification.get("industry")) or "Other / Unclassified"

        if kind == "etf":
            try:
                lookthrough = _cached_sector_lookthrough(symbol, security_type)
                weights = lookthrough.get("weights") if isinstance(lookthrough, dict) else None
            except Exception:
                weights = None
            usable = {
                _clean(name): float(weight)
                for name, weight in (weights or {}).items()
                if _clean(name) and float(weight or 0.0) > 0
            }
            usable = {
                name: weight
                for name, weight in usable.items()
                if name not in {"Other / Unclassified", "Fund / Mixed"}
            }
            total_weight = sum(usable.values())
            if total_weight > 0:
                for sector, weight in usable.items():
                    contribution = market_value * weight / total_weight
                    _add_contributor(sector_members, sector, symbol, contribution)
            else:
                fallback_sector = _clean(classification.get("asset_class")) or direct_sector
                _add_contributor(sector_members, fallback_sector, symbol, market_value)
        else:
            _add_contributor(sector_members, direct_sector, symbol, market_value)

        _add_contributor(industry_members, industry, symbol, market_value)

    return dict(sector_members), dict(industry_members)


def _contributors_html(members: dict[str, float]) -> str:
    if not members:
        return "Contributors: unavailable"
    rows = sorted(members.items(), key=lambda item: item[1], reverse=True)
    lines = ["<b>HOLDINGS</b>"]
    for symbol, value in rows:
        lines.append(f"{html.escape(symbol)} &nbsp; {_money(value)}")
    return "<br>".join(lines)


def _chart_frame(
    frame: pd.DataFrame,
    label_column: str,
    contributor_map: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Group tiny categories while preserving contributor hover detail."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[label_column, "Exposure", "% Exposure", "Contributors"])

    work = frame[[label_column, "Exposure"]].copy()
    work["Exposure"] = pd.to_numeric(work["Exposure"], errors="coerce").fillna(0.0)
    work = work[work["Exposure"] > 0].sort_values("Exposure", ascending=False).reset_index(drop=True)
    total = float(work["Exposure"].sum())
    work["% Exposure"] = work["Exposure"] / total * 100 if total else 0.0
    work["Contributors"] = work[label_column].map(
        lambda label: dict(contributor_map.get(str(label), {}))
    )

    major = work[work["% Exposure"] >= MIN_SLICE_PCT].head(MAX_VISIBLE_SLICES).copy()
    major_indexes = set(major.index.tolist())
    tail = work[~work.index.isin(major_indexes)]

    if not tail.empty:
        other_members: dict[str, float] = {}
        for members in tail["Contributors"]:
            for symbol, value in (members or {}).items():
                other_members[symbol] = other_members.get(symbol, 0.0) + float(value)
        other_value = float(tail["Exposure"].sum())
        major = pd.concat(
            [
                major,
                pd.DataFrame(
                    [{
                        label_column: f"Other (<{MIN_SLICE_PCT:.0f}% each)",
                        "Exposure": other_value,
                        "% Exposure": other_value / total * 100 if total else 0.0,
                        "Contributors": other_members,
                    }]
                ),
            ],
            ignore_index=True,
        )

    major = major.sort_values("Exposure", ascending=False).reset_index(drop=True)
    major_total = float(major["Exposure"].sum())
    major["% Exposure"] = major["Exposure"] / major_total * 100 if major_total else 0.0
    return major


def _shared_panel_height(sector_chart: pd.DataFrame, industry_chart: pd.DataFrame) -> int:
    item_count = max(len(sector_chart), len(industry_chart), 1)
    return max(PANEL_MIN_HEIGHT, min(PANEL_MAX_HEIGHT, 500 + item_count * 14))


def _panel_shapes(center_x: float) -> list[dict]:
    transparent = "rgba(0,0,0,0)"
    return [
        {
            "type": "rect", "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT, "y0": PANEL_BOTTOM, "y1": PANEL_TOP,
            "line": {"color": BB_ORANGE, "width": 1.4},
            "fillcolor": "rgba(0,0,0,0)", "layer": "above",
        },
        {
            "type": "rect", "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT, "y0": HEADER_BOTTOM, "y1": PANEL_TOP,
            "line": {"color": BB_ORANGE, "width": 1},
            "fillcolor": BB_ORANGE, "layer": "above",
        },
        {
            "type": "line", "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT, "y0": HEADER_BOTTOM, "y1": HEADER_BOTTOM,
            "line": {"color": BB_ORANGE, "width": 1.2}, "layer": "above",
        },
        # Invisible alignment rails shared by both cards.
        {
            "type": "line", "xref": "paper", "yref": "paper",
            "x0": PANEL_LEFT, "x1": PANEL_RIGHT,
            "y0": CONTENT_CENTER_Y, "y1": CONTENT_CENTER_Y,
            "line": {"color": transparent, "width": 1}, "layer": "below",
        },
        {
            "type": "line", "xref": "paper", "yref": "paper",
            "x0": center_x, "x1": center_x,
            "y0": CONTENT_BOTTOM, "y1": CONTENT_TOP,
            "line": {"color": transparent, "width": 1}, "layer": "below",
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
    full_labels = [str(value) for value in chart[label_column].tolist()]
    display_labels = [_wrap_legend_label(value) for value in full_labels]
    values = chart["Exposure"].tolist()
    percents = chart["% Exposure"].tolist()
    contributor_html = [_contributors_html(value or {}) for value in chart["Contributors"]]

    # Large slices read inside. 2%-3% slices get Plotly's outside label + leader
    # line so they remain visible without crowding the donut.
    text = [f"{float(value):.1f}%" for value in percents]
    textposition = [
        "inside" if float(value) >= MIN_PRINTED_PCT else "outside"
        for value in percents
    ]

    if legend_side == "left":
        pie_domain = {"x": [0.40, 0.96], "y": [CONTENT_BOTTOM, CONTENT_TOP]}
        legend = {
            "x": 0.018, "xanchor": "left",
            "y": CONTENT_CENTER_Y, "yanchor": "middle",
        }
    else:
        pie_domain = {"x": [0.04, 0.60], "y": [CONTENT_BOTTOM, CONTENT_TOP]}
        legend = {
            "x": 0.65, "xanchor": "left",
            "y": CONTENT_CENTER_Y, "yanchor": "middle",
        }
    legend.update({
        "orientation": "v",
        "font": {"color": BB_ORANGE, "family": "Courier New", "size": 10},
        "itemsizing": "constant", "tracegroupgap": 2,
        "bgcolor": BB_BLACK, "borderwidth": 0,
    })

    center_x = (pie_domain["x"][0] + pie_domain["x"][1]) / 2
    center_y = (pie_domain["y"][0] + pie_domain["y"][1]) / 2
    customdata = [[label, contributors] for label, contributors in zip(full_labels, contributor_html)]

    figure = go.Figure(
        go.Pie(
            labels=display_labels,
            values=values,
            customdata=customdata,
            meta={"raj_preserve_textposition": True},
            hole=DONUT_HOLE,
            sort=False,
            domain=pie_domain,
            marker={"colors": colors, "line": {"color": BB_BLACK, "width": 2}},
            text=text,
            textinfo="text",
            textposition=textposition,
            insidetextorientation="horizontal",
            textfont={"family": "Courier New", "size": 11},
            outsidetextfont={"family": "Courier New", "size": 11, "color": BB_ORANGE},
            automargin=True,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Exposure: $%{value:,.2f}<br>Portfolio: %{percent}<br><br>"
                "%{customdata[1]}<extra></extra>"
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
        margin={"l": 16, "r": 16, "t": 8, "b": 8, "autoexpand": True},
        shapes=_panel_shapes(center_x),
        annotations=[
            {
                "text": f"<b>{html.escape(title)}</b>",
                "x": 0.025, "y": (HEADER_BOTTOM + PANEL_TOP) / 2,
                "xref": "paper", "yref": "paper",
                "xanchor": "left", "yanchor": "middle",
                "showarrow": False,
                "font": {"color": BB_BLACK, "size": 16, "family": "Courier New"},
            },
            {
                "text": "MV WEIGHTED",
                "x": 0.975, "y": (HEADER_BOTTOM + PANEL_TOP) / 2,
                "xref": "paper", "yref": "paper",
                "xanchor": "right", "yanchor": "middle",
                "showarrow": False,
                "font": {"color": BB_BLACK, "size": 9, "family": "Courier New"},
            },
            {
                "text": "PORTFOLIO<br>EXPOSURE",
                "x": center_x, "y": center_y,
                "xref": "paper", "yref": "paper",
                "xanchor": "center", "yanchor": "middle",
                "showarrow": False, "align": "center", "width": 110,
                "font": {"color": BB_GREEN, "size": 12, "family": "Courier New"},
            },
        ],
    )
    return figure


def _detail_table(frame: pd.DataFrame, label_column: str) -> None:
    if frame is None or frame.empty:
        return
    detail = frame[[label_column, "Exposure", "% Exposure"]].copy()
    st.dataframe(
        detail,
        hide_index=True,
        width="stretch",
        column_config={
            "Exposure": st.column_config.NumberColumn(format="dollar"),
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
        config={"displayModeBar": False, "responsive": True, "displaylogo": False},
        key=key,
    )


def render_stockanalysis_portfolio(
    account_state_key: str,
    *,
    key_prefix: str,
    title: str = "SECTOR + INDUSTRY EXPOSURE",
    show_classification_table: bool = True,
) -> None:
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
        sector_members, industry_members = _contributor_maps(frame)

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
            column_config={"Market Value": st.column_config.NumberColumn(format="dollar")},
            key=f"{key_prefix}_classification_{account_key}",
        )

    sector_chart = _chart_frame(sector_frame, "Sector", sector_members)
    industry_chart = _chart_frame(industry_frame, "Industry", industry_members)
    panel_height = _shared_panel_height(sector_chart, industry_chart)

    left, right = st.columns(2, gap="medium")
    with left:
        _render_chart_panel(
            sector_chart, "Sector", "SECTOR EXPOSURE",
            f"{key_prefix}_sector_{account_key}_v8",
            legend_side="left", palette_offset=SECTOR_COLOR_OFFSET,
            panel_height=panel_height,
        )
    with right:
        _render_chart_panel(
            industry_chart, "Industry", "INDUSTRY EXPOSURE",
            f"{key_prefix}_industry_{account_key}_v8",
            legend_side="right", palette_offset=INDUSTRY_COLOR_OFFSET,
            panel_height=panel_height,
        )

    with st.expander("FULL SECTOR + INDUSTRY BREAKDOWN"):
        st.markdown("**SECTORS // ALL EXPOSURES**")
        _detail_table(sector_frame, "Sector")
        st.markdown("**INDUSTRIES / ETF CATEGORIES // ALL EXPOSURES**")
        _detail_table(industry_frame, "Industry")

    st.caption(
        "BLOOMBERG PANEL // market-value weighted // hover any slice for contributing tickers + market values // "
        "2%-3% slices use outside percentage labels with leader lines // <2% grouped into Other // full ungrouped "
        "exposure remains available above."
    )
