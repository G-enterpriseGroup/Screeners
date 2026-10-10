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
    _annual,
    _calendar_returns,
    _clean_state,
    _export_csv,
    _extract_close,
    _monthly,
    _overview,
    _period_return,
    _sector_month,
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
    assert _clean_state({"tickers": []})["tickers"] == []
    assert _clean_state({"tickers": ["xlk", "XLK", "qqq"], "focus": "qqq"})["tickers"] == ["XLK", "QQQ"]

    days = pd.to_datetime(["2024-12-30", "2024-12-31", "2025-01-02", "2025-02-03"])
    xlk = pd.Series([100.0, 110.0, 121.0, 108.9], index=days)
    xlre = pd.Series([100.0, 105.0], index=days[-2:])
    annual_xlk = _calendar_returns(xlk, "year")
    near(annual_xlk[2024], 10.0)
    near(annual_xlk[2025], -10.0)
    month_xlk = _calendar_returns(xlk, "month")
    near(month_xlk[(2025, 1)], 10.0)
    near(month_xlk[(2025, 2)], -10.0)
    near(_period_return(xlk, "MAX"), 8.9)
    near(_period_return(xlk, "1D"), -10.0)
    near(_period_return(xlk, "YTD"), -10.0)
    near(_period_return(xlk, "5Y"), 8.9)  # available-history fallback

    prices = pd.DataFrame({"XLK": xlk, "XLRE": xlre})
    cols, rows = _annual(prices, ["XLK", "XLRE"])
    assert cols == ["XLK", "XLRE"]
    assert rows[0][0] == "2025"
    assert rows[1][0] == "2024"
    assert rows[1][1][1] is None  # newer fund has no pre-inception history
    mcols, mrows = _monthly(prices, "XLRE")
    assert len(mcols) == 12
    assert [r[0] for r in mrows] == ["2025"]
    scol, srows = _sector_month(prices, ["XLK", "XLRE"], 2024)
    assert srows[1][1][11] is None
    ocols, orows = _overview(prices, ["XLK", "XLRE"])
    assert ocols[-1] == "MAX"
    assert len(orows) == 2
    assert b"TICKER_OR_YEAR" in _export_csv(ocols, orows)
    assert _shade(-5, 10)[0] != _shade(5, 10)[0]
    assert _shade(np.nan, 10)[0] == "#181818"

    raw = pd.DataFrame({("XLK", "Close"): [100.0, 110.0],
                        ("XLK", "Open"): [99.0, 109.0]}, index=days[:2])
    assert list(_extract_close(raw, "XLK", False)) == [100.0, 110.0]
    print("PASS: Heatmaps symbol validation, inception coverage, daily/monthly/annual returns, exports, gradient.")


if __name__ == "__main__":
    run()
