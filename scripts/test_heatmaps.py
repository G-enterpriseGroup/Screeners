"""Offline regression checks for the isolated Heatmaps feature.

Run: PYTHONPATH=. python scripts/test_heatmaps.py
No broker credentials and no network access required.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.heatmaps_ui import (
    DEFAULT_TICKERS,
    SECTOR_NAMES,
    VIEWS,
    _annual,
    _best_positive_sector,
    _calendar_returns,
    _clean_state,
    _export_csv,
    _extract_close,
    _heatmap_markup,
    _monthly,
    _overview,
    _period_return,
    _sector_month,
    _sector_label,
    _shade,
    _symbol,
)


def near(actual, expected):
    assert math.isclose(actual, expected, abs_tol=1e-7), (actual, expected)


def run() -> None:
    assert len(DEFAULT_TICKERS) == 11
    assert set(DEFAULT_TICKERS) == {
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"
    }
    assert _symbol("NYSEARCA:xlk") == "XLK"
    assert _symbol("brk.b") == "BRK-B"
    assert not _symbol("<script>")
    assert not _symbol("bad ticker")
    assert _clean_state({})["tickers"] == list(DEFAULT_TICKERS)
    assert VIEWS[0] == "ANNUAL HISTORY"
    assert _clean_state({})["view"] == "ANNUAL HISTORY"
    assert _clean_state({"view": "SECTOR PERFORMANCE"})["view"] == "ANNUAL HISTORY"
    assert _clean_state({"state_version": 2, "view": "SECTOR PERFORMANCE"})["view"] == "ANNUAL HISTORY"
    assert _clean_state({"state_version": 3, "view": "ROLLING PERFORMANCE"})["view"] == "ROLLING PERFORMANCE"
    assert _sector_label("XLK") == "Technology (XLK)"
    assert _sector_label("XLY") == "Consumer Discretionary (XLY)"
    assert _sector_label("QQQ") == "QQQ"
    assert len(SECTOR_NAMES) == 11
    assert _clean_state({"tickers": []})["tickers"] == []
    assert _clean_state({"tickers": ["xlk", "XLK", "qqq"], "focus": "qqq"})["tickers"] == ["XLK", "QQQ"]

    days = pd.to_datetime(["2024-12-30", "2024-12-31", "2025-01-02", "2025-02-03"])
    xlk = pd.Series([100.0, 110.0, 121.0, 108.9], index=days)
    xlre = pd.Series([100.0, 105.0], index=days[-2:])
    annual_xlk = _calendar_returns(xlk, "year")
    near(annual_xlk[2024], 10.0)
    near(annual_xlk[2025], -1.0)
    month_xlk = _calendar_returns(xlk, "month")
    near(month_xlk[(2025, 1)], 10.0)
    near(month_xlk[(2025, 2)], -10.0)
    near(_period_return(xlk, "MAX"), 8.9)
    near(_period_return(xlk, "1D"), -10.0)
    near(_period_return(xlk, "YTD"), -1.0)
    near(_period_return(xlk, "5Y"), 8.9)  # available-history fallback
    week = pd.Series([100.0, 108.0, 120.0], index=pd.to_datetime(
        ["2025-01-01", "2025-01-02", "2025-01-09"]))
    near(_period_return(week, "1W"), (120.0 / 108.0 - 1.0) * 100.0)

    prices = pd.DataFrame({"XLK": xlk, "XLRE": xlre})
    cols, rows = _annual(prices, ["XLK", "XLRE"])
    assert cols == ["2024", "2025"]
    assert [label for label, _ in rows] == ["XLK", "XLRE"]
    assert rows[1][1][0] is None  # no pre-inception returns
    annual_markup = _heatmap_markup("ANNUAL", cols, rows, annual=True)
    assert "Technology (XLK)" in annual_markup
    assert "Real Estate (XLRE)" in annual_markup
    assert "<th>2024</th>" in annual_markup and "<th>2025</th>" in annual_markup
    csv_annual = _export_csv(cols, rows, annual=True).decode()
    assert "Technology (XLK)" in csv_annual
    assert "Real Estate (XLRE)" in csv_annual
    assert "SECTOR_OR_TICKER,2024,2025" in csv_annual
    past = pd.Series(
        [100.0, 105.0, 110.0, 115.0],
        index=pd.to_datetime(["2000-12-29", "2001-06-01", "2001-12-31", "2002-01-02"]),
    )
    pcols, prows = _annual(pd.DataFrame({"XLK": past}), ["XLK"])
    assert pcols == ["2000", "2001", "2002"]
    assert [label for label, _ in prows] == ["XLK"]
    assert "2001" in _heatmap_markup("BY YEAR", pcols, prows, annual=True)
    years = pd.to_datetime(["2000-12-29", "2001-12-31", "2002-12-31",
                            "2003-12-31", "2004-12-31", "2005-01-05"])
    comparison = pd.DataFrame({"XLK": [100, 120, 140, 160, 170, 171],
                               "XLE": [100, 110, 100, 110, 100, 105],
                               "XLRE": [float("nan"), float("nan"), 100, 110, 120, 121]},
                              index=years)
    yc, yr = _annual(comparison, ["XLK", "XLE", "XLRE"])
    winner = _best_positive_sector(comparison, yc, yr)
    assert winner == "XLK"
    highlighted = _heatmap_markup("ANNUAL", yc, yr, annual=True, best_sector=winner)
    assert highlighted.count('background:#ffe100;color:#000000') == 1
    assert 'Technology (XLK)</td>' in highlighted
    assert _best_positive_sector(comparison[["XLE"]], yc, [yr[1]]) is None
    mcols, mrows = _monthly(prices, "XLRE")
    assert len(mcols) == 12
    assert [r[0] for r in mrows] == ["2025"]
    scol, srows = _sector_month(prices, ["XLK", "XLRE"], 2024)
    assert srows[1][1][11] is None
    ocols, orows = _overview(prices, ["XLK", "XLRE"])
    assert ocols[-1] == "MAX"
    assert len(orows) == 2
    assert "Technology (XLK)" in _heatmap_markup("OVERVIEW", ocols, orows)
    assert b"TICKER_OR_YEAR" in _export_csv(ocols, orows)
    assert _shade(-5, 10)[0] != _shade(5, 10)[0]
    assert _shade(np.nan, 10)[0] == "#181818"

    raw = pd.DataFrame({("XLK", "Close"): [100.0, 110.0],
                        ("XLK", "Open"): [99.0, 109.0]}, index=days[:2])
    assert list(_extract_close(raw, "XLK", False)) == [100.0, 110.0]
    print("PASS: Heatmaps symbol validation, inception coverage, daily/monthly/annual returns, exports, gradient.")


if __name__ == "__main__":
    run()
