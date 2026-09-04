import io
import re
import json
import time
import uuid
from urllib.parse import urljoin

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

pd.set_option("display.max_columns", 80)
pd.set_option("display.max_colwidth", 100)
pd.set_option("display.width", 240)

ISHARES_LIST_URL = "https://www.ishares.com/us/products/etf-investments"

# These are index/term muni ETFs whose published mandate/index is investment-grade.
# This is used only as investment-grade EVIDENCE, not as an exact AAA/AA/A/BBB rating.
KNOWN_IG_ONLY_TICKERS = {
    "MUB", "SUB", "LMUB", "CMF", "NYF",
    "IBMO", "IBMP", "IBMQ", "IBMR", "IBMS",
    "IBMT", "IBMU", "IBMV", "IBMW", "IBMX",
}

# Sources whose published benchmark/mandate is AMT-free.
KNOWN_AMT_FREE_TICKERS = {
    "MUB", "SUB", "LMUB", "CMF", "NYF",
    "IBMO", "IBMP", "IBMQ", "IBMR", "IBMS",
    "IBMT", "IBMU", "IBMV", "IBMW", "IBMX",
}

STATE_PATTERNS = {
    "District of Columbia": [r"\bDISTRICT COLUMBIA\b", r"\bWASHINGTON D C\b", r"\bD C\b"],
    "Puerto Rico": [r"\bPUERTO RICO\b", r"\bP R\b"],
    "Virgin Islands": [r"\bVIRGIN ISLANDS\b"],
    "Guam": [r"\bGUAM\b"],
    "Alabama": [r"\bALABAMA\b", r"\bALA\b"],
    "Alaska": [r"\bALASKA\b"],
    "Arizona": [r"\bARIZONA\b", r"\bARIZ\b"],
    "Arkansas": [r"\bARKANSAS\b", r"\bARK\b"],
    "California": [r"\bCALIFORNIA\b", r"\bCALIF\b"],
    "Colorado": [r"\bCOLORADO\b", r"\bCOLO\b"],
    "Connecticut": [r"\bCONNECTICUT\b", r"\bCONN\b"],
    "Delaware": [r"\bDELAWARE\b", r"\bDEL\b"],
    "Florida": [r"\bFLORIDA\b", r"\bFLA\b"],
    "Georgia": [r"\bGEORGIA\b"],
    "Hawaii": [r"\bHAWAII\b", r"\bHAW\b"],
    "Idaho": [r"\bIDAHO\b"],
    "Illinois": [r"\bILLINOIS\b", r"\bILL\b"],
    "Indiana": [r"\bINDIANA\b", r"\bIND\b"],
    "Iowa": [r"\bIOWA\b"],
    "Kansas": [r"\bKANSAS\b", r"\bKANS\b"],
    "Kentucky": [r"\bKENTUCKY\b", r"\bKY\b"],
    "Louisiana": [r"\bLOUISIANA\b"],
    "Maine": [r"\bMAINE\b"],
    "Maryland": [r"\bMARYLAND\b"],
    "Massachusetts": [r"\bMASSACHUSETTS\b", r"\bMASS\b"],
    "Michigan": [r"\bMICHIGAN\b", r"\bMICH\b"],
    "Minnesota": [r"\bMINNESOTA\b", r"\bMINN\b"],
    "Mississippi": [r"\bMISSISSIPPI\b", r"\bMISS\b"],
    "Missouri": [r"\bMISSOURI\b"],
    "Montana": [r"\bMONTANA\b", r"\bMONT\b"],
    "Nebraska": [r"\bNEBRASKA\b", r"\bNEB\b"],
    "Nevada": [r"\bNEVADA\b", r"\bNEV\b"],
    "New Hampshire": [r"\bNEW HAMPSHIRE\b", r"\bN H\b"],
    "New Jersey": [r"\bNEW JERSEY\b", r"\bN J\b"],
    "New Mexico": [r"\bNEW MEXICO\b", r"\bN MEX\b"],
    "New York": [r"\bNEW YORK\b", r"\bN Y\b"],
    "North Carolina": [r"\bNORTH CAROLINA\b", r"\bN CAR\b"],
    "North Dakota": [r"\bNORTH DAKOTA\b", r"\bN DAK\b"],
    "Ohio": [r"\bOHIO\b"],
    "Oklahoma": [r"\bOKLAHOMA\b", r"\bOKLA\b"],
    "Oregon": [r"\bOREGON\b", r"\bORE\b"],
    "Pennsylvania": [r"\bPENNSYLVANIA\b", r"\bPENN\b"],
    "Rhode Island": [r"\bRHODE ISLAND\b", r"\bR I\b"],
    "South Carolina": [r"\bSOUTH CAROLINA\b", r"\bS CAR\b"],
    "South Dakota": [r"\bSOUTH DAKOTA\b", r"\bS DAK\b"],
    "Tennessee": [r"\bTENNESSEE\b", r"\bTENN\b"],
    "Texas": [r"\bTEXAS\b", r"\bTEX\b"],
    "Utah": [r"\bUTAH\b"],
    "Vermont": [r"\bVERMONT\b", r"\bVT\b"],
    "Virginia": [r"\bVIRGINIA\b"],
    "Washington": [r"\bWASHINGTON\b", r"\bWASH\b"],
    "West Virginia": [r"\bWEST VIRGINIA\b", r"\bW VA\b"],
    "Wisconsin": [r"\bWISCONSIN\b", r"\bWIS\b"],
    "Wyoming": [r"\bWYOMING\b", r"\bWYO\b"],
}


def infer_state(name):
    text = " " + re.sub(r"[^A-Z0-9]+", " ", str(name).upper()).strip() + " "

    priority = [
        "District of Columbia", "Puerto Rico", "Virgin Islands",
        "Guam", "West Virginia"
    ]

    for state in priority:
        for pattern in STATE_PATTERNS[state]:
            if re.search(pattern, text):
                return state

    for state, patterns in STATE_PATTERNS.items():
        if state in priority:
            continue
        for pattern in patterns:
            if re.search(pattern, text):
                return state

    return "Unknown"


def to_num(series):
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
    )
    cleaned = cleaned.mask(cleaned.isin(["-", "", "nan"]), np.nan)
    return pd.to_numeric(cleaned, errors="coerce")


def _clean_ticker(text):
    text = str(text).strip().upper()
    return text if re.fullmatch(r"[A-Z]{2,5}", text) else None


def discover_ishares_muni_etfs(session):
    """
    Discover current iShares ETFs whose product name contains Muni or Municipal.
    This avoids hardcoding only MUB/SUB/LMUB and automatically picks up
    iBonds, state funds, active funds, and high-yield muni ETFs.
    """
    r = session.get(ISHARES_LIST_URL, timeout=45)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    found = {}

    for tr in soup.find_all("tr"):
        row_text = " ".join(tr.stripped_strings)
        low = row_text.lower()

        if "muni" not in low and "municipal" not in low:
            continue

        links = tr.find_all("a", href=True)
        product_links = [
            a for a in links
            if "/us/products/" in a.get("href", "")
        ]

        if not product_links:
            continue

        ticker = None
        name = None
        product_url = None

        for a in product_links:
            txt = " ".join(a.stripped_strings).strip()
            maybe = _clean_ticker(txt)
            if maybe:
                ticker = maybe

        for a in product_links:
            txt = " ".join(a.stripped_strings).strip()
            if "muni" in txt.lower() or "municipal" in txt.lower():
                name = txt
                product_url = urljoin(ISHARES_LIST_URL, a["href"])
                break

        if product_url is None:
            a = product_links[0]
            product_url = urljoin(ISHARES_LIST_URL, a["href"])

        if ticker and product_url:
            found[ticker] = {
                "Ticker": ticker,
                "Fund Name": name or row_text,
                "Product URL": product_url,
            }

    core_fallback = {
        "MUB": (
            "iShares National Muni Bond ETF",
            "https://www.ishares.com/us/products/239766/ishares-national-muni-bond-etf",
        ),
        "SUB": (
            "iShares Short-Term National Muni Bond ETF",
            "https://www.ishares.com/us/products/239772/ishares-shortterm-national-amtfree-muni-bond-etf",
        ),
        "LMUB": (
            "iShares Long-Term National Muni Bond ETF",
            "https://www.ishares.com/us/products/342170/ishares-long-term-national-muni-bond-etf",
        ),
        "CMF": (
            "iShares California Muni Bond ETF",
            "https://www.ishares.com/us/products/239731/ishares-california-muni-bond-etf",
        ),
    }

    for ticker, (name, url) in core_fallback.items():
        found.setdefault(
            ticker,
            {"Ticker": ticker, "Fund Name": name, "Product URL": url},
        )

    return pd.DataFrame(found.values()).sort_values("Ticker").reset_index(drop=True)


def find_holdings_csv_url(session, product_url):
    """
    Read the official iShares product page and extract its own
    'Download Holdings CSV' link instead of guessing the data URL.
    """
    r = session.get(product_url, timeout=45)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings).lower()
        href = a.get("href", "")

        if "download holdings csv" in text:
            return urljoin(product_url, href)

        if "filetype=csv" in href.lower() and "holding" in href.lower():
            return urljoin(product_url, href)

    base = product_url.rstrip("/")
    return base + "/latest-holdings.csv"


def parse_ishares_holdings_csv(content, ticker, fund_name, product_url):
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Name,Sector,Asset Class"):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Holdings CSV header was not found")

    as_of = pd.NaT
    for line in lines[:header_idx]:
        if line.startswith("Fund Holdings as of"):
            raw = line.split(",", 1)[1].strip().strip('"')
            as_of = pd.to_datetime(raw, errors="coerce")
            break

    frame = pd.read_csv(
        io.StringIO("\n".join(lines[header_idx:])),
        dtype=str,
        on_bad_lines="skip",
    )

    if "CUSIP" not in frame.columns:
        raise ValueError("CUSIP column missing")

    frame["CUSIP"] = frame["CUSIP"].astype(str).str.strip().str.upper()
    frame = frame[
        frame["CUSIP"].str.fullmatch(r"[0-9A-Z]{9}", na=False)
    ].copy()

    if "Asset Class" in frame.columns:
        frame = frame[frame["Asset Class"].eq("Fixed Income")].copy()

    numeric_cols = [
        "Market Value", "Weight (%)", "Notional Value", "Par Value",
        "Price", "Duration", "YTM (%)", "Coupon (%)", "Mod. Duration",
        "Yield to Call (%)", "Yield to Worst (%)",
        "Real Duration", "Real YTM (%)",
    ]

    for col in numeric_cols:
        if col in frame.columns:
            frame[col] = to_num(frame[col])

    for col in ["Maturity", "Accrual Date", "Effective Date"]:
        if col in frame.columns:
            frame[col] = pd.to_datetime(frame[col], errors="coerce")

    frame["Source ETF"] = ticker
    frame["Source Fund"] = fund_name
    frame["Source Product URL"] = product_url
    frame["Source As Of"] = as_of

    if ticker in {"CMF", "CALI"}:
        frame["State"] = "California"
    elif ticker == "NYF":
        frame["State"] = "New York"
    else:
        frame["State"] = frame["Name"].map(infer_state)

    frame["IG Source"] = ticker in KNOWN_IG_ONLY_TICKERS
    frame["AMT-Free Source"] = ticker in KNOWN_AMT_FREE_TICKERS

    return frame


def _first_valid(series):
    for x in series:
        if pd.notna(x) and str(x) != "":
            return x
    return np.nan


def _median_valid(series):
    vals = pd.to_numeric(series, errors="coerce").dropna()
    return vals.median() if len(vals) else np.nan


def _latest_date(series):
    vals = pd.to_datetime(series, errors="coerce").dropna()
    return vals.max() if len(vals) else pd.NaT


def _earliest_date(series):
    vals = pd.to_datetime(series, errors="coerce").dropna()
    return vals.min() if len(vals) else pd.NaT


def _join_unique(series):
    vals = sorted({
        str(x).strip()
        for x in series
        if pd.notna(x) and str(x).strip()
    })
    return ", ".join(vals)


def load_all_ishares_munis():
    """
    Downloads all currently discoverable iShares muni ETF holdings,
    then deduplicates by CUSIP.

    Returns:
        df          = one row per CUSIP
        source_rows = all raw ETF holding rows
        etf_status  = success/failure audit by ETF
        as_of       = latest source date
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/140 Safari/537.36"
        ),
        "Accept": "text/html,text/csv,text/plain,*/*",
    })

    funds = discover_ishares_muni_etfs(session)
    all_frames = []
    status_rows = []

    print(f"Discovered {len(funds)} iShares muni ETFs. Downloading holdings...")

    for _, row in funds.iterrows():
        ticker = row["Ticker"]
        name = row["Fund Name"]
        product_url = row["Product URL"]

        try:
            csv_url = find_holdings_csv_url(session, product_url)
            rr = session.get(csv_url, timeout=60)
            rr.raise_for_status()

            frame = parse_ishares_holdings_csv(
                rr.content,
                ticker=ticker,
                fund_name=name,
                product_url=product_url,
            )

            all_frames.append(frame)
            status_rows.append({
                "Ticker": ticker,
                "Fund Name": name,
                "Status": "OK",
                "Rows": len(frame),
                "As Of": frame["Source As Of"].max(),
            })
            print(f"  ✓ {ticker:<5} {len(frame):>6,} rows")

        except Exception as e:
            status_rows.append({
                "Ticker": ticker,
                "Fund Name": name,
                "Status": "FAILED",
                "Rows": 0,
                "As Of": pd.NaT,
                "Error": str(e)[:180],
            })
            print(f"  ✗ {ticker:<5} {str(e)[:100]}")

        time.sleep(0.25)

    if not all_frames:
        raise RuntimeError(
            "No iShares holdings files loaded. "
            "Check your internet connection or BlackRock page format."
        )

    source_rows = pd.concat(all_frames, ignore_index=True, sort=False)
    etf_status = pd.DataFrame(status_rows)

    ytc = source_rows.get(
        "Yield to Call (%)",
        pd.Series(np.nan, index=source_rows.index),
    )
    ytw = source_rows.get(
        "Yield to Worst (%)",
        pd.Series(np.nan, index=source_rows.index),
    )
    ytm = source_rows.get(
        "YTM (%)",
        pd.Series(np.nan, index=source_rows.index),
    )

    source_rows["NonCallableProxy"] = (
        ytc.isna() & ((ytw - ytm).abs() <= 0.01)
    )

    grouped_rows = []

    for cusip, g in source_rows.groupby("CUSIP", sort=False):
        ig_sources = sorted(g.loc[g["IG Source"], "Source ETF"].unique())
        amt_sources = sorted(
            g.loc[g["AMT-Free Source"], "Source ETF"].unique()
        )

        states = [
            x for x in g["State"].dropna().astype(str).unique()
            if x and x != "Unknown"
        ]

        state = states[0] if len(set(states)) == 1 else (
            _first_valid(g["State"]) if states else "Unknown"
        )

        noncall_values = g["NonCallableProxy"].dropna()
        noncall_proxy = (
            bool(noncall_values.all()) if len(noncall_values) else False
        )

        grouped_rows.append({
            "CUSIP": cusip,
            "Name": _first_valid(g["Name"]),
            "State": state,
            "Sector": _first_valid(g["Sector"]) if "Sector" in g else np.nan,
            "Price": _median_valid(g["Price"]) if "Price" in g else np.nan,
            "Coupon (%)": _median_valid(g["Coupon (%)"]) if "Coupon (%)" in g else np.nan,
            "YTM (%)": _median_valid(g["YTM (%)"]) if "YTM (%)" in g else np.nan,
            "Yield to Worst (%)": _median_valid(g["Yield to Worst (%)"]) if "Yield to Worst (%)" in g else np.nan,
            "Yield to Call (%)": _median_valid(g["Yield to Call (%)"]) if "Yield to Call (%)" in g else np.nan,
            "Maturity": _latest_date(g["Maturity"]) if "Maturity" in g else pd.NaT,
            "Effective Date": _earliest_date(g["Effective Date"]) if "Effective Date" in g else pd.NaT,
            "ETF Par Held ($)": pd.to_numeric(
                g.get("Par Value", pd.Series(dtype=float)),
                errors="coerce",
            ).sum(min_count=1),
            "Source ETFs": _join_unique(g["Source ETF"]),
            "Source Count": g["Source ETF"].nunique(),
            "Latest Source Date": _latest_date(g["Source As Of"]),
            "Investment Grade": "Yes" if ig_sources else "Unknown",
            "IG Evidence ETFs": ", ".join(ig_sources),
            "Rating": "IG (>= BBB-/Baa3)" if ig_sources else "Unknown",
            "Rating Basis": (
                "Investment-grade ETF/index eligibility"
                if ig_sources
                else "No free per-CUSIP rating in source files"
            ),
            "Federal Tax Exempt": "Likely",
            "AMT Exempt": "Yes" if amt_sources else "Unknown",
            "AMT Evidence ETFs": ", ".join(amt_sources),
            "NonCallableProxy": noncall_proxy,
        })

    df = pd.DataFrame(grouped_rows)
    df = df[df["CUSIP"].str.fullmatch(r"[0-9A-Z]{9}", na=False)].copy()
    df = df.sort_values(["CUSIP"]).reset_index(drop=True)

    as_of_dates = pd.to_datetime(
        source_rows["Source As Of"], errors="coerce"
    ).dropna()

    as_of = (
        as_of_dates.max().strftime("%Y-%m-%d")
        if len(as_of_dates)
        else pd.Timestamp.today().strftime("%Y-%m-%d")
    )

    return df, source_rows, etf_status, as_of


def screen_munis(
    df,
    cusip=None,
    states=None,
    purchase_face=5000,
    price_min=None,
    price_max=None,
    coupon_min=None,
    coupon_max=None,
    ytw_min=None,
    ytw_max=None,
    maturity_from=None,
    maturity_to=None,
    investment_grade_only=False,
    amt_exempt_only=False,
    non_callable_only=False,
    new_issue_only=False,
    new_issue_days=60,
    as_of=None,
    sort_by="YTW: High → Low",
):
    """Filter the consolidated muni universe. Exact CUSIP lookup overrides other filters."""
    out = df.copy()
    cusip = (cusip or "").strip().upper()

    if cusip:
        out = out[out["CUSIP"].eq(cusip)].copy()
    else:
        if states:
            if isinstance(states, str):
                states = [states]
            out = out[out["State"].isin(states)]

        def apply_range(frame, col, low, high):
            if low is not None:
                frame = frame[frame[col].ge(low)]
            if high is not None:
                frame = frame[frame[col].le(high)]
            return frame

        out = apply_range(out, "Price", price_min, price_max)
        out = apply_range(out, "Coupon (%)", coupon_min, coupon_max)
        out = apply_range(out, "Yield to Worst (%)", ytw_min, ytw_max)

        if maturity_from is not None:
            out = out[
                pd.to_datetime(out["Maturity"], errors="coerce")
                .ge(pd.Timestamp(maturity_from))
            ]

        if maturity_to is not None:
            out = out[
                pd.to_datetime(out["Maturity"], errors="coerce")
                .le(pd.Timestamp(maturity_to))
            ]

        if investment_grade_only:
            out = out[out["Investment Grade"].eq("Yes")]

        if amt_exempt_only:
            out = out[out["AMT Exempt"].eq("Yes")]

        if non_callable_only:
            out = out[out["NonCallableProxy"].fillna(False)]

        if new_issue_only:
            ref_date = (
                pd.Timestamp(as_of)
                if as_of
                else pd.Timestamp.today().normalize()
            )
            cutoff = ref_date - pd.Timedelta(days=int(new_issue_days))
            eff = pd.to_datetime(out["Effective Date"], errors="coerce")
            out = out[eff.ge(cutoff) & eff.le(ref_date)]

        sort_map = {
            "YTW: High → Low": ("Yield to Worst (%)", False),
            "Price: Low → High": ("Price", True),
            "Coupon: High → Low": ("Coupon (%)", False),
            "Maturity: Soonest": ("Maturity", True),
            "ETF Coverage: High → Low": ("Source Count", False),
        }

        if sort_by in sort_map:
            col, ascending = sort_map[sort_by]
            out = out.sort_values(
                col,
                ascending=ascending,
                na_position="last",
            )

    out = out.copy()
    out["Purchase Face ($)"] = float(purchase_face)
    out["Est. Principal Cost ($)"] = (
        out["Price"] / 100.0 * float(purchase_face)
    )
    out["Annual Coupon Income ($)"] = (
        out["Coupon (%)"] / 100.0 * float(purchase_face)
    )

    return out.reset_index(drop=True)
