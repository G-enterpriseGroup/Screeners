"""Focused offline tests for Protective Puts public fallback normalization."""

from datetime import date

import src.protective_puts_sources as sources


assert sources._number("1,234") == 1234.0
assert sources._number("1.55k") == 1550.0
assert sources._number("-") is None

sample_html = """
<html><body>
<h3>Puts</h3>
<table>
<thead><tr>
<th>Contract Name</th><th>Last Trade Date</th><th>Strike</th><th>Last Price</th>
<th>Bid</th><th>Ask</th><th>Change</th><th>% Change</th><th>Volume</th>
<th>Open Interest</th><th>Implied Volatility</th>
</tr></thead>
<tbody><tr>
<td>SPY261218P00700000</td><td>10/07/2026</td><td>700.00</td><td>4.20</td>
<td>4.10</td><td>4.30</td><td>0.10</td><td>2.00%</td><td>1,234</td>
<td>5.6k</td><td>20.00%</td>
</tr></tbody>
</table>
</body></html>
"""

original_get = sources._get
original_yf = sources._normalize_yfinance_puts
try:
    sources._get = lambda _url: sample_html
    rows = sources._scrape_puts("SPY", date(2026, 12, 18))
    assert len(rows) == 1
    assert rows[0]["call_put"] == "PUT"
    assert rows[0]["strike"] == 700.0
    assert rows[0]["ask"] == 4.3
    assert rows[0]["volume"] == 1234.0
    assert rows[0]["open_interest"] == 5600.0

    sources._normalize_yfinance_puts = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("yf down"))
    fallback_rows, source = sources.public_put_chain("SPY", date(2026, 12, 18))
    assert source == "YAHOO HTML"
    assert fallback_rows[0]["osi"] == "SPY261218P00700000"
finally:
    sources._get = original_get
    sources._normalize_yfinance_puts = original_yf

print("PROTECTIVE PUTS PUBLIC FALLBACK TEST: PASS")
