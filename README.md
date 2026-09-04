# Screeners

A Bloomberg-style Streamlit terminal combining municipal-bond analytics with
read-only E*TRADE account data and a Triggers–OCO trade simulator.

## Top-level tabs

- **Orders:** E*TRADE stock/ETF quote lookup, buy-limit entry, take-profit and
  stop sliders, call/put walls, live reward:risk, and position sizing at 0.5%,
  1%, or a custom percentage of total portfolio value.
- **Holdings:** live E*TRADE balances and positions with CSV export.
- **Muni Screeners:** contains all three original municipal tools unchanged:
  Muni Screener, Tax Exempt Status for NIST, and State Income Tax Muni vs UST.

The Orders tab is simulation-only. No preview-order or place-order API methods
exist in the application.

## E*TRADE setup

The app uses E*TRADE OAuth 1.0a. Store credentials only in Streamlit Community
Cloud under **App Settings → Secrets**:

```toml
[etrade]
consumer_key = "YOUR_NEW_PRODUCTION_CONSUMER_KEY"
consumer_secret = "YOUR_NEW_PRODUCTION_CONSUMER_SECRET"
environment = "live"
```

Select **Connect E*TRADE**, approve the application on E*TRADE, and enter the
verification code shown. E*TRADE access tokens expire at midnight US Eastern;
a same-day inactive token can be reactivated with **Renew**. Access tokens are
kept only in the private Streamlit session and are not written to GitHub.

## Current master

The master Jupyter version is `notebooks/muni_screener_multi_etf_multistate.ipynb`. The Streamlit app uses the same municipal data/filter engine through `src/muni_data.py` and Treasury comparison helpers through `src/treasury_data.py`.

## Tab 1 — Muni Screener

### Filters

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

## Tab 2 — Tax Equivalent: Muni vs U.S. Treasury

Enter:

- Client federal marginal tax bracket (%)
- Client target maturity date
- Optional municipal state(s)
- Optional investment-grade-only filter

The app then:

1. Finds the closest municipal maturities to the client's target date.
2. Lets you choose among the 25 closest municipal candidates.
3. Matches the selected muni to the closest Treasury maturity.
4. Uses muni **Yield to Worst** for the municipal side.
5. Uses WSJ **Asked Yield** when WSJ Treasury Bills / Notes & Bonds data are available.
6. Calculates municipal tax-equivalent yield and Treasury after-tax yield.
7. Shows which security wins after federal tax and by how many basis points.

### Federal tax formulas

```text
Muni tax-equivalent yield = Muni YTW / (1 - federal tax rate)
Treasury after-tax yield  = Treasury yield * (1 - federal tax rate)
```

The first version is intentionally federal-tax-focused. State/local tax treatment, AMT, and other client-specific tax items are not included in the winner calculation.

### Treasury data

`src/treasury_data.py` is **WSJ-first**. It attempts to read the Treasury Bills and Treasury Notes & Bonds quote feeds used by the WSJ Treasury market-data page. It does not bypass authentication, CAPTCHAs, access controls, or paywalls.

If WSJ data are unavailable or its format changes, the app clearly switches to the official **U.S. Treasury Daily Par Yield Curve** as a fallback and labels the result as a benchmark rather than an exact individual Treasury issue quote.

Treasury data are cached for 30 minutes so the app does not repeatedly request the source on every Streamlit rerun.

## Municipal data approach

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
│   ├── muni_data.py
│   └── treasury_data.py
└── notebooks/
    └── muni_screener_multi_etf_multistate.ipynb
```

## Important limitations

- ETF holdings are not the full municipal-bond universe.
- Source prices/yields are not guaranteed executable broker quotes.
- `NonCallableProxy` is a screening proxy only.
- WSJ may change or restrict its public market-data format; the official Treasury curve fallback is therefore retained.
- Verify call schedules, tax treatment, AMT treatment, ratings, Treasury quotes, and official terms before purchase.
