"""StockAnalysis sector/industry classification for Raj's Terminal.

Stocks:
  https://stockanalysis.com/stocks/{ticker}/company/
  Industry XPath supplied by Raj:
  /html/body/div[1]/div[1]/div[2]/main/div[2]/div[2]/div[1]/table/tbody/tr[4]/td[2]/a
  Sector XPath supplied by Raj:
  /html/body/div[1]/div[1]/div[2]/main/div[2]/div[2]/div[1]/table/tbody/tr[5]/td[2]/a

ETFs:
  https://stockanalysis.com/etf/{ticker}/

The parser prefers semantic row labels (Industry/Sector/Asset Class/Category)
so minor page-layout changes do not break the terminal. For stock pages it also
falls back to rows 4 and 5, matching the supplied XPaths.

Network results are cached for 24 hours because classification changes slowly.
ETF sector look-through uses the terminal's existing sector_profile helper when
available; StockAnalysis ETF Category is used for the industry-style grouping
because an ETF does not have one company industry.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup

from src.etrade_client import normalize_position
from src.sector_data import sector_profile
from src.theme import BB_BLACK, BB_GREEN, BB_ORANGE, CHART_COLORWAY


REQUEST_TIMEOUT = 10
CACHE_SECONDS = 24 * 60 * 60
STOCK_URL = "https://stockanalysis.com/stocks/{ticker}/company/"
ETF_URL = "https://stockanalysis.com/etf/{ticker}/"

STOCK_INDUSTRY_XPATH = "/html/body/div[1]/div[1]/div[2]/main/div[2]/div[2]/div[1]/table/tbody/tr[4]/td[2]/a"
STOCK_SECTOR_XPATH = "/html/body/div[1]/div[1]/div[2]/main/div[2]/div[2]/div[1]/table/tbody/tr[5]/td[2]/a"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _ticker_candidates(symbol: str) -> list[str]:
    symbol = _clean(symbol).lower()
    if not symbol:
        return []
    values = [symbol]
    dashed = symbol.replace(".", "-")
    if dashed not in values:
        values.append(dashed)
    return values


def _label_value_pairs(soup: BeautifulSoup) -> dict[str, str]:
    """Read visible two-column label/value rows without depending on CSS classes."""
    pairs: dict[str, str] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = _clean(cells[0].get_text(" ", strip=True))
        value = _clean(cells[1].get_text(" ", strip=True))
        if label and value:
            pairs[label.casefold()] = value

    # Some StockAnalysis profile fields can be rendered as adjacent blocks
    # instead of literal table rows. Scan visible strings as a second path.
    visible = [_clean(text) for text in soup.stripped_strings]
    wanted = {"industry", "sector", "asset class", "category"}
    for index, text in enumerate(visible[:-1]):
        key = text.casefold()
        if key in wanted and key not in pairs:
            candidate = _clean(visible[index + 1])
            if candidate and candidate.casefold() not in wanted:
                pairs[key] = candidate
    return pairs


def _stock_xpath_fallback(soup: BeautifulSoup) -> tuple[str, str]:
    """Fallback matching Raj's supplied tr[4] Industry / tr[5] Sector XPaths."""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 5:
            continue
        values: list[str] = []
        for row_index in (3, 4):
            cells = rows[row_index].find_all(["th", "td"])
            values.append(_clean(cells[1].get_text(" ", strip=True)) if len(cells) >= 2 else "")
        industry, sector = values[0], values[1]
        if industry or sector:
            return industry, sector
    return "", ""


def _get_html(url: str) -> BeautifulSoup:
    response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    if response.status_code == 404:
        raise FileNotFoundError(url)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def stockanalysis_classification(symbol: str, security_type: str = "") -> dict[str, Any]:
    """Return direct StockAnalysis classification for one portfolio security."""
    symbol = _clean(symbol).upper()
    security_type = _clean(security_type).upper()
    if not symbol:
        return {
            "symbol": symbol,
            "kind": "unknown",
            "sector": "Other / Unclassified",
            "industry": "Other / Unclassified",
            "source": "fallback",
            "url": "",
        }

    # Non-equity instruments should not trigger unnecessary web requests.
    if "BOND" in security_type or (len(symbol) == 9 and symbol.isalnum()):
        return {
            "symbol": symbol,
            "kind": "bond",
            "sector": "Fixed Income",
            "industry": "Bonds / Fixed Income",
            "source": "E*TRADE security type",
            "url": "",
        }
    if "OPT" in security_type:
        return {
            "symbol": symbol,
            "kind": "option",
            "sector": "Options / Derivatives",
            "industry": "Options / Derivatives",
            "source": "E*TRADE security type",
            "url": "",
        }
    if security_type in {"MMF", "MONEY MARKET", "CASH"}:
        return {
            "symbol": symbol,
            "kind": "cash",
            "sector": "Cash / Money Market",
            "industry": "Cash / Money Market",
            "source": "E*TRADE security type",
            "url": "",
        }

    last_error = ""
    for slug in _ticker_candidates(symbol):
        url = STOCK_URL.format(ticker=slug)
        try:
            soup = _get_html(url)
            pairs = _label_value_pairs(soup)
            industry = _clean(pairs.get("industry"))
            sector = _clean(pairs.get("sector"))
            if not industry or not sector:
                xpath_industry, xpath_sector = _stock_xpath_fallback(soup)
                industry = industry or xpath_industry
                sector = sector or xpath_sector
            if industry or sector:
                return {
                    "symbol": symbol,
                    "kind": "stock",
                    "sector": sector or "Other / Unclassified",
                    "industry": industry or "Other / Unclassified",
                    "source": "StockAnalysis company profile",
                    "url": url,
                }
        except Exception as exc:
            last_error = str(exc)[:180]

    # ETFs do not expose one company Industry/Sector field. StockAnalysis does
    # expose Asset Class + Category, which we preserve for the classification
    # table and industry-style portfolio grouping.
    for slug in _ticker_candidates(symbol):
        url = ETF_URL.format(ticker=slug)
        try:
            soup = _get_html(url)
            pairs = _label_value_pairs(soup)
            asset_class = _clean(pairs.get("asset class"))
            category = _clean(pairs.get("category"))
            if asset_class or category:
                return {
                    "symbol": symbol,
                    "kind": "etf",
                    "sector": "ETF / Fund",
                    "industry": ("ETF // " + category) if category else "ETF / Fund",
                    "asset_class": asset_class,
                    "category": category,
                    "source": "StockAnalysis ETF overview",
                    "url": url,
                }
        except Exception as exc:
            last_error = str(exc)[:180]

    return {
        "symbol": symbol,
        "kind": "unknown",
        "sector": "Other / Unclassified",
        "industry": "Other / Unclassified",
        "source": "fallback",
        "url": "",
        "error": last_error,
    }


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def _cached_sector_lookthrough(symbol: str, security_type: str = "") -> dict[str, Any]:
    return sector_profile(symbol, security_type)


def _selected_account(account_state_key: str) -> dict[str, Any] | None:
    accounts = st.session_state.get("etrade_accounts") or []
    if not accounts:
        return None
    selected = st.session_state.get(account_state_key)
    if isinstance(selected, int) and 0 <= selected < len(accounts):
        return accounts[selected]
    for account in accounts:
        if str(account.get("accountId", "")).endswith("5474"):
            return account
    return accounts[0]


def _portfolio_rows(account_state_key: str) -> tuple[pd.DataFrame, str]:
    account = _selected_account(account_state_key)
    if not account:
        return pd.DataFrame(), ""
    account_key = str(account.get("accountIdKey", ""))
    holdings = (st.session_state.get("etrade_holdings") or {}).get(account_key)
    if holdings is None:
        return pd.DataFrame(), account_key

    rows = [normalize_position(position) for position in holdings]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame, account_key

    for column in ("Market Value", "Quantity"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame, account_key


def _classification_frames(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    details: list[dict[str, Any]] = []
    sector_exposure: dict[str, float] = {}
    industry_exposure: dict[str, float] = {}

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
        sector_display = direct_sector
        sector_source = classification.get("source", "fallback")

        if kind == "etf":
            try:
                lookthrough = _cached_sector_lookthrough(symbol, security_type)
                weights = lookthrough.get("weights") if isinstance(lookthrough, dict) else None
            except Exception:
                weights = None
                lookthrough = {}

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
            if usable:
                total_weight = sum(usable.values())
                if total_weight > 0:
                    for sector, weight in usable.items():
                        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + market_value * weight / total_weight
                    sector_display = "ETF LOOK-THROUGH"
                    sector_source = _clean(lookthrough.get("source")) or "ETF look-through"
                else:
                    sector_exposure[direct_sector] = sector_exposure.get(direct_sector, 0.0) + market_value
            else:
                fallback_sector = _clean(classification.get("asset_class")) or direct_sector
                sector_exposure[fallback_sector] = sector_exposure.get(fallback_sector, 0.0) + market_value
                sector_display = fallback_sector
        else:
            sector_exposure[direct_sector] = sector_exposure.get(direct_sector, 0.0) + market_value

        industry_exposure[industry] = industry_exposure.get(industry, 0.0) + market_value
        details.append(
            {
                "Symbol": symbol,
                "Type": security_type,
                "Market Value": market_value,
                "Sector": sector_display,
                "Industry / ETF Category": industry,
                "Classification Source": classification.get("source", "fallback"),
                "Sector Source": sector_source,
            }
        )

    detail_frame = pd.DataFrame(details)
    sector_frame = pd.DataFrame(
        [{"Sector": label, "Exposure": value} for label, value in sector_exposure.items() if value > 0]
    )
    industry_frame = pd.DataFrame(
        [{"Industry": label, "Exposure": value} for label, value in industry_exposure.items() if value > 0]
    )
    for exposure_frame in (sector_frame, industry_frame):
        if not exposure_frame.empty:
            exposure_frame.sort_values("Exposure", ascending=False, inplace=True)
            total = float(exposure_frame["Exposure"].sum())
            exposure_frame["% Exposure"] = exposure_frame["Exposure"] / total * 100 if total else 0.0
            exposure_frame.reset_index(drop=True, inplace=True)
    return detail_frame, sector_frame, industry_frame


def _donut(frame: pd.DataFrame, label_column: str, title: str) -> go.Figure:
    colors = [CHART_COLORWAY[index % len(CHART_COLORWAY)] for index in range(len(frame))]
    figure = go.Figure(
        go.Pie(
            labels=frame[label_column],
            values=frame["Exposure"],
            hole=0.48,
            sort=False,
            marker={"colors": colors, "line": {"color": BB_BLACK, "width": 2}},
            textinfo="percent",
            textfont={"family": "Courier New", "size": 12},
            hovertemplate="%{label}<br>Exposure: $%{value:,.2f}<br>%{percent}<extra></extra>",
        )
    )
    figure.update_layout(
        title=title,
        height=460,
        paper_bgcolor=BB_BLACK,
        plot_bgcolor=BB_BLACK,
        font={"color": BB_ORANGE, "family": "Courier New"},
        legend={"font": {"color": BB_ORANGE}, "orientation": "v"},
        margin={"l": 15, "r": 15, "t": 60, "b": 15},
        annotations=[
            {
                "text": "PORTFOLIO<br>EXPOSURE",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"color": BB_GREEN, "size": 13, "family": "Courier New"},
            }
        ],
    )
    return figure


def render_stockanalysis_portfolio(
    account_state_key: str,
    *,
    key_prefix: str,
    title: str = "SECTOR + INDUSTRY EXPOSURE",
    show_classification_table: bool = True,
) -> None:
    """Render StockAnalysis classification and two portfolio exposure pies."""
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
            column_config={
                "Market Value": st.column_config.NumberColumn(format="$%.2f"),
            },
            key=f"{key_prefix}_classification_{account_key}",
        )

    left, right = st.columns(2)
    with left:
        if sector_frame.empty:
            st.info("No sector exposure could be mapped.")
        else:
            st.plotly_chart(
                _donut(sector_frame, "Sector", "SECTOR EXPOSURE"),
                width="stretch",
                config={"displayModeBar": False},
                key=f"{key_prefix}_sector_{account_key}",
            )
    with right:
        if industry_frame.empty:
            st.info("No industry exposure could be mapped.")
        else:
            st.plotly_chart(
                _donut(industry_frame, "Industry", "INDUSTRY EXPOSURE"),
                width="stretch",
                config={"displayModeBar": False},
                key=f"{key_prefix}_industry_{account_key}",
            )

    st.caption(
        "ETF NOTE // StockAnalysis reports ETF Asset Class/Category rather than one company Industry. "
        "The industry pie therefore groups ETFs by StockAnalysis ETF Category. Sector pie uses ETF sector look-through "
        "when available; otherwise it falls back to the ETF asset class."
    )
