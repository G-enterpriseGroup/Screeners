"""Offline regression coverage for terminal-wide yfinance options fallback."""

from datetime import date
from types import SimpleNamespace

import pandas as pd

import src.yfinance_options as yf_options
from src.etrade_client import option_expiration_dates, quote_summary
from src.option_book import extract_option_rows


class FakeTicker:
    options = ("2026-11-06", "2026-12-18")

    def __init__(self, symbol):
        self.symbol = symbol
        self.fast_info = {"last_price": 100.0, "bid": 99.9, "ask": 100.1}
        self.info = {"longName": "Fake Corp"}

    def history(self, period="5d", auto_adjust=False):
        return pd.DataFrame({"Close": [100.0]})

    def option_chain(self, expiry):
        calls = pd.DataFrame(
            [
                {
                    "contractSymbol": "FAKE261106C00100000",
                    "strike": 100.0,
                    "lastPrice": 5.1,
                    "bid": 5.0,
                    "ask": 5.2,
                    "volume": 100,
                    "openInterest": 2500,
                    "impliedVolatility": 0.25,
                },
                {
                    "contractSymbol": "FAKE261106C00105000",
                    "strike": 105.0,
                    "lastPrice": 2.7,
                    "bid": 2.6,
                    "ask": 2.8,
                    "volume": 80,
                    "openInterest": 1800,
                    "impliedVolatility": 0.27,
                },
            ]
        )
        puts = pd.DataFrame(
            [
                {
                    "contractSymbol": "FAKE261106P00100000",
                    "strike": 100.0,
                    "lastPrice": 4.8,
                    "bid": 4.7,
                    "ask": 4.9,
                    "volume": 90,
                    "openInterest": 2300,
                    "impliedVolatility": 0.26,
                },
                {
                    "contractSymbol": "FAKE261106P00095000",
                    "strike": 95.0,
                    "lastPrice": 2.4,
                    "bid": 2.3,
                    "ask": 2.5,
                    "volume": 70,
                    "openInterest": 1700,
                    "impliedVolatility": 0.28,
                },
            ]
        )
        return SimpleNamespace(calls=calls, puts=puts)


original_ticker = yf_options.yf.Ticker
yf_options.yf.Ticker = FakeTicker
try:
    client = yf_options.options_market_client(None)
    assert client.is_yfinance_options_fallback is True
    quote = quote_summary(client.get_quote("FAKE"))
    assert quote["last"] == 100.0
    assert quote["description"] == "Fake Corp"

    expirations = option_expiration_dates(client.get_option_expirations("FAKE"))
    assert expirations == [(2026, 11, 6), (2026, 12, 18)]

    payload = client.get_option_chain(
        "FAKE", 2026, 11, 6, no_of_strikes=None, chain_type="CALLPUT"
    )
    rows = extract_option_rows(payload, date(2026, 11, 6))
    assert len(rows) == 4
    assert {row["call_put"] for row in rows} == {"CALL", "PUT"}

    pairs = payload["OptionChainResponse"]["OptionPair"]
    greek_rows = []
    for pair in pairs:
        for side in ("Call", "Put"):
            if side in pair:
                greek_rows.append(pair[side]["OptionGreeks"])
    assert greek_rows
    assert all(float(row["iv"]) > 0 for row in greek_rows)
    assert all(float(row["gamma"]) > 0 for row in greek_rows)
    assert all(float(row["vega"]) > 0 for row in greek_rows)

    calls_only = client.get_option_chain(
        "FAKE", 2026, 11, 6, no_of_strikes=1, chain_type="CALL"
    )
    call_pairs = calls_only["OptionChainResponse"]["OptionPair"]
    assert len(call_pairs) == 1
    assert "Call" in call_pairs[0] and "Put" not in call_pairs[0]
finally:
    yf_options.yf.Ticker = original_ticker

print("YFINANCE OPTIONS FALLBACK TEST: PASS")
