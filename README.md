# Screeners

A collection of market screeners. The first screener is a **Municipal Bond Screener** built from free, official ETF holdings data.

## Municipal Bond Screener

The app combines municipal-bond ETF holdings, deduplicates bonds by CUSIP, and exposes filters for:

- Exact CUSIP
- State
- Purchase face value
- Price min/max
- Coupon min/max
- Yield-to-worst min/max
- Maturity range
- Investment-grade evidence
- AMT-exempt evidence
- Non-callable proxy
- New issues
- Sorting by YTW, price, coupon, maturity, or ETF coverage

It also shows:

- CUSIP
- Bond name
- State
- Price
- Coupon
- YTM
- YTW
- YTC
- Maturity
- Investment-grade evidence
- Rating basis
- Source ETFs
- Number of ETFs holding the bond
- Estimated purchase cost
- Annual coupon income

## Data approach

The project intentionally uses **no paid API and no API key**.

It downloads official public holdings files from municipal-bond ETFs and merges them into one security universe. This materially increases coverage over using MUB alone, but it is **not the full U.S. municipal bond market**.

### Rating limitation

The free ETF holdings files generally do not expose a current S&P/Moody's/Fitch rating for each individual CUSIP.

Therefore:

- `Investment Grade = Yes` means the bond appears in an ETF/index whose eligibility rules establish investment-grade status.
- `Rating = IG (>= BBB-/Baa3)` is a classification, **not an invented exact agency rating**.
- Exact AAA / AA / A / BBB ratings should only be added when a legitimate per-CUSIP source is available.

## Run locally

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install:

```bash
pip install -r requirements.txt
```

Run:

```bash
streamlit run streamlit_app.py
```

## Project structure

```text
screeners/
├── streamlit_app.py
├── requirements.txt
├── README.md
├── .gitignore
├── .streamlit/
│   └── config.toml
├── src/
│   └── muni_data.py
└── notebooks/
    └── muni_screener_multi_etf_no_widgets.ipynb
```

## Important limitations

- ETF holdings are not the entire municipal-bond universe.
- Source prices/yields are not guaranteed executable broker quotes.
- `NonCallableProxy` is a screening proxy only.
- Verify call schedules, tax treatment, AMT treatment, ratings, and official terms before purchase.
