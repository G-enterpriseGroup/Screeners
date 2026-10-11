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
    _annual_cagr,
    _benchmark_analysis,
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
    assert _sector_label("QQQ") == "Nasdaq-100 Benchmark (QQQ)"
    assert _sector_label("SPY") == "S&P 500 Benchmark (SPY)"
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
    assert "<th>R / G</th>" in annual_markup
    assert 'hm-year-counts' in annual_markup
    assert 'R 1</span> <span class="hm-count-green">G 1' in annual_markup
    assert 'R 0</span> <span class="hm-count-green">G 1' in annual_markup
    mixed = _heatmap_markup("COUNT", ["2023", "2024", "2025", "2026"],
                            [("XLK", [-2.0, 0.0, 3.0, None])], annual=True)
    assert 'R 1</span> <span class="hm-count-green">G 1' in mixed
    assert mixed.count('hm-count-red') == 1
    assert "<th>R / G</th>" not in _heatmap_markup("ROLLING", ["1Y"], [("XLK", [5.0])])

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
    # Matched full-year total returns: sector must outperform BOTH ETF
    # benchmarks plus an annually rebalanced equal-weight composite.
    benchmark_dates = pd.to_datetime([f"{year}-12-30" for year in range(2000, 2026)])
    def annual_path(factor):
        return [100.0 * factor ** i for i in range(26)]
    recent = [float("nan")] * 19 + [100.0 * (1.6 ** i) for i in range(7)]
    comparison = pd.DataFrame({
        "SPY": annual_path(1.07),
        "QQQ": annual_path(1.10),
        "XLK": annual_path(1.15),
        "XLE": annual_path(1.03),
        "XLC": recent,
    }, index=benchmark_dates)
    cy, sector_rows = _annual(comparison, ["XLK", "XLE", "XLC"])
    leader, notes, benchmark_rows, message = _benchmark_analysis(comparison, cy, sector_rows)
    assert leader == "XLK", message
    assert len(benchmark_rows) == 3
    assert [name for name, _ in benchmark_rows] == ["SPY", "QQQ", "SPY+QQQ 50/50"]
    assert "SPY" in notes["XLK"] and "QQQ" in notes["XLK"]
    assert "Information ratio" in notes["XLK"]
    assert "2001-2024" in notes["XLK"], notes["XLK"]
    assert "NOT ELIGIBLE" in notes["XLC"]
    near(_annual_cagr([15.0] * 20), 15.0)
    near(_annual_cagr([7.0] * 20), 7.0)
    composite = dict(benchmark_rows)["SPY+QQQ 50/50"]
    near(composite[1], 8.5)
    marked = _heatmap_markup("BENCHMARK", cy, sector_rows + benchmark_rows,
                             annual=True, best_sector=leader, explanations=notes)
    assert marked.count('background:#ffe100;color:#000000') == 1
    assert "BENCHMARK-RELATIVE LEADER" in marked
    assert "SPY" in marked and "QQQ" in marked
    assert 'title="Negative years:' in marked
    assert "No benchmark" not in message

    # Winner is suppressed when it does not beat QQQ, even if sector is green.
    weaker = comparison.copy()
    weaker["XLK"] = annual_path(1.09)
    wc, wr = _annual(weaker, ["XLK", "XLE", "XLC"])
    no_leader, _, _, why = _benchmark_analysis(weaker, wc, wr)
    assert no_leader is None and "NO QUALIFYING SECTOR" in why

    # Backtest: strong old history must NOT hide a losing recent decade.
    recent_years = pd.to_datetime([f"{year}-12-30" for year in range(2000, 2026)])
    old_winner = [100.0]
    for i in range(1, 26):
        old_winner.append(old_winner[-1] * (1.25 if i <= 15 else 1.06))
    old_spy = [100.0 * (1.08 ** i) for i in range(26)]
    old_qqq = [100.0 * (1.12 ** i) for i in range(26)]
    stale = pd.DataFrame({"XLY": old_winner, "SPY": old_spy, "QQQ": old_qqq},
                         index=recent_years)
    stale_years, stale_rows = _annual(stale, ["XLY"])
    stale_leader, stale_notes, _, _ = _benchmark_analysis(stale, stale_years, stale_rows)
    assert stale_leader is None
    assert "RECENT 10Y CHECK" in stale_notes["XLY"]
    assert "NOT ELIGIBLE" in stale_notes["XLY"]

    # Screenshot 2014-2025: rounded annual returns reproduce recent XLY
    # underperformance. Not an independently sourced market-data verification.
    screenshot_xly = [9.5, 9.9, 6.0, 22.8, 1.6, 28.4, 29.6, 27.9, -36.3, 39.6, 26.5, 7.4]
    screenshot_spy = [13.5, 1.2, 12.0, 21.7, -4.6, 31.2, 18.3, 28.7, -18.2, 26.2, 24.9, 17.7]
    screenshot_qqq = [19.2, 9.4, 7.1, 32.7, -0.1, 39.0, 48.4, 27.4, -32.6, 54.9, 25.6, 20.8]
    screenshot_blend = [(a + b) / 2 for a, b in zip(screenshot_spy, screenshot_qqq)]
    assert _annual_cagr(screenshot_xly[-10:]) < _annual_cagr(screenshot_blend[-10:])
    assert _annual_cagr(screenshot_xly[-10:]) < _annual_cagr(screenshot_qqq[-10:])
    assert sum(a > b for a, b in zip(screenshot_xly[-10:], screenshot_blend[-10:])) == 2

    # Insufficient benchmark history never creates a false winner.
    incomplete = comparison.drop(columns=["QQQ"])
    missing_winner, _, available_benchmarks, why = _benchmark_analysis(incomplete, cy, sector_rows)
    assert missing_winner is None
    assert len(available_benchmarks) == 1
    assert "unavailable" in why

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
