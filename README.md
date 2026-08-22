# Screeners

A collection of market screeners. The first project is a **Multi-ETF Municipal Bond Screener** built from free, official ETF holdings data.

## Current master

The master Jupyter version is `notebooks/muni_screener_multi_etf_multistate.ipynb`. The Streamlit app uses the same data/filters through `src/muni_data.py`.

### Muni screener filters

- Exact CUSIP lookup
- **Multiple states at once**
- **No State Individual Income Tax** filter
- Purchase face value
- Price min/max
- Coupon min/max
- Yield-to-worst min/max
- Maturity range
- Investment-grade evidence
- AMT-exempt evidence
- Non-callable proxy
- New issues
- Sort by YTW, price, coupon, maturity, or ETF coverage

The no-state-individual-income-tax filter uses Alaska, Florida, Nevada, New Hampshire, South Dakota, Tennessee, Texas, and Wyoming. Washington is intentionally excluded because it taxes certain capital gains, matching the master notebook.

## Data approach

No paid API and no API key are required. The loader discovers current iShares muni ETFs, downloads their official public holdings files, combines them, and deduplicates by CUSIP.

ETF holdings materially expand coverage beyond MUB alone, but **do not represent the entire U.S. municipal bond market**.

### Rating limitation

Free holdings files generally do not expose a current S&P/Moody's/Fitch rating for each CUSIP. Therefore:

- `Investment Grade = Yes` means the bond appears in an investment-grade-only ETF/index.
- `Rating = IG (>= BBB-/Baa3)` is an eligibility classification, not an invented exact agency rating.
- Exact AAA / AA / A / BBB ratings should only be added when a legitimate per-CUSIP source is available.

## Run Streamlit locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Structure

```text
Screeners/
├── streamlit_app.py
├── requirements.txt
├── README.md
├── .streamlit/
│   └── config.toml
├── src/
│   ├── __init__.py
│   └── muni_data.py
└── notebooks/
    └── muni_screener_multi_etf_multistate.ipynb
```

## Important limitations

- ETF holdings are not the full municipal-bond universe.
- Source prices/yields are not guaranteed executable broker quotes.
- `NonCallableProxy` is a screening proxy only.
- Verify call schedules, tax treatment, AMT treatment, ratings, and official terms before purchase.
