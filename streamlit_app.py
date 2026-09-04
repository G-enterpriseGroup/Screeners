import html
import math
import re
import time
from contextlib import redirect_stdout
from datetime import datetime, time as datetime_time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from src.etrade_client import (
    ETradeClient,
    ETradeError,
    begin_authorization,
    complete_authorization,
    find_number,
    normalize_position,
    quote_summary,
    total_account_value,
)
from src.muni_data import load_all_ishares_munis, screen_munis
from src.trade_math import calculate_trade_metrics, risk_sized_quantity
from src.treasury_data import (
    load_treasury_quotes,
    nearest_muni_candidates,
    nearest_treasury,
    tax_equivalent_comparison,
)


NO_INDIVIDUAL_INCOME_TAX_STATES = {
    "Alaska",
    "Florida",
    "Nevada",
    "New Hampshire",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Washington",
    "Wyoming",
}

ALL_US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
]

MUNI_SESSION_KEY = "_muni_data_bundle"
MUNI_SESSION_AT_KEY = "_muni_data_loaded_at"
MUNI_TTL_SECONDS = 12 * 60 * 60
DEFAULT_ETRADE_ACCOUNT_SUFFIX = "5474"
ETRADE_INACTIVITY_SECONDS = 2 * 60 * 60
ETRADE_TIMEZONE = ZoneInfo("America/New_York")


st.set_page_config(
    page_title="MuniX Screen — by Raj",
    page_icon="📊",
    layout="wide",
)


st.markdown(
    """
    <style>
    :root {
        --bb-orange:#FF8C00;
        --bb-black:#000000;
        --bb-dark:#0A0A0A;
        --bb-dim:#A85C00;
        --bb-blue:#0068FF;
        --bb-blue-hover:#2388FF;
        --bb-blue-active:#0047B3;
        --bb-green:#00D084;
        --bb-red:#FF3B30;
    }

    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    [data-testid="stHeader"] {
        background:#000 !important;
        color:var(--bb-orange) !important;
    }

    * {
        border-radius:0 !important;
    }

    h1 {
        color:var(--bb-orange) !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        letter-spacing:.04em;
        text-transform:uppercase;
    }

    h2, h3 {
        background:var(--bb-orange) !important;
        color:#000 !important;
        padding:.32rem .55rem !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        text-transform:uppercase;
        letter-spacing:.04em;
    }

    p, label, .stCaption, [data-testid="stMarkdownContainer"] {
        color:var(--bb-orange) !important;
    }

    [data-testid="stMetric"] {
        background:#000 !important;
        border:1px solid var(--bb-orange) !important;
        padding:.55rem .7rem !important;
    }

    [data-testid="stMetric"] * {
        font-family:"Courier New",monospace !important;
    }

    [data-testid="stMetricLabel"] *,
    [data-testid="stMetricDeltaDescription"] * {
        color:var(--bb-orange) !important;
    }

    [data-testid="stMetricValue"] * {
        color:var(--bb-green) !important;
    }

    [data-testid="stMetricDelta"] * {
        color:var(--bb-green) !important;
    }

    [data-testid="stMetricDelta"]:has([data-testid="stMetricDeltaIcon-Down"]) * {
        color:var(--bb-red) !important;
    }

    [data-testid="stMetricDelta"]:has([data-testid="stMetricDeltaIcon-Up"]) * {
        color:var(--bb-green) !important;
    }

    .bb-number-card {
        background:#000;
        border:1px solid var(--bb-orange);
        min-height:104px;
        padding:.55rem .7rem;
        font-family:"Courier New",monospace;
    }

    .bb-number-label {
        color:var(--bb-orange) !important;
        font-size:.88rem;
        margin-bottom:.2rem;
    }

    .bb-number-value {
        font-size:1.65rem;
        font-weight:700;
        line-height:1.25;
    }

    .bb-number-detail {
        font-size:.82rem;
        margin-top:.15rem;
    }

    .bb-positive { color:var(--bb-green) !important; }
    .bb-negative { color:var(--bb-red) !important; }
    .bb-neutral { color:var(--bb-orange) !important; }

    .bb-quote-strip {
        display:grid;
        grid-template-columns:repeat(4,minmax(0,1fr));
        border:1px solid var(--bb-orange);
        background:#000;
        margin:.15rem 0 .25rem 0;
        font-family:"Courier New",monospace;
    }

    .bb-quote-cell {
        min-width:0;
        padding:.38rem .58rem .34rem .58rem;
        border-right:1px solid var(--bb-orange);
    }

    .bb-quote-cell:last-child { border-right:0; }

    .bb-quote-label {
        color:var(--bb-orange);
        font-size:.73rem;
        font-weight:700;
        line-height:1.05;
        text-transform:uppercase;
    }

    .bb-quote-value {
        font-size:1.38rem;
        font-weight:900;
        line-height:1.15;
        white-space:nowrap;
    }

    .bb-quote-detail {
        font-size:.72rem;
        line-height:1.05;
        min-height:.76rem;
        white-space:nowrap;
    }

    @media (max-width:850px) {
        .bb-quote-strip { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .bb-quote-cell:nth-child(2) { border-right:0; }
        .bb-quote-cell:nth-child(-n+2) { border-bottom:1px solid var(--bb-orange); }
    }

    div[data-baseweb="input"] > div,
    div[data-baseweb="select"] > div,
    div[data-baseweb="base-input"] {
        background:#000 !important;
        border-color:var(--bb-orange) !important;
        color:var(--bb-orange) !important;
    }

    input, textarea {
        color:var(--bb-orange) !important;
        -webkit-text-fill-color:var(--bb-orange) !important;
        caret-color:var(--bb-orange) !important;
        font-family:"Courier New",monospace !important;
    }

    input::placeholder, textarea::placeholder {
        color:var(--bb-dim) !important;
        opacity:1 !important;
    }

    div[data-baseweb="select"] span {
        color:var(--bb-orange) !important;
    }

    div[data-baseweb="tag"] {
        background:var(--bb-orange) !important;
    }

    div[data-baseweb="tag"] span {
        color:#000 !important;
        font-weight:900 !important;
    }

    [data-testid="stCheckbox"] label *,
    [data-testid="stRadio"] label * {
        color:var(--bb-orange) !important;
    }

    .stButton > button,
    .stDownloadButton > button,
    .stLinkButton > a {
        background:var(--bb-blue) !important;
        color:#FFF !important;
        border:1px solid #66ADFF !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        text-transform:uppercase;
        box-shadow:0 3px 0 #003579, 0 0 12px rgba(0,104,255,.30) !important;
        transition:transform 70ms ease, background 70ms ease, box-shadow 70ms ease !important;
    }

    .stButton > button *,
    .stDownloadButton > button *,
    .stLinkButton > a * {
        color:#FFF !important;
    }

    .stButton > button:hover:not(:disabled),
    .stDownloadButton > button:hover:not(:disabled),
    .stLinkButton > a:hover {
        background:var(--bb-blue-hover) !important;
        border-color:#A8D2FF !important;
        box-shadow:0 3px 0 #003579, 0 0 18px rgba(35,136,255,.55) !important;
    }

    .stButton > button:active:not(:disabled),
    .stDownloadButton > button:active:not(:disabled),
    .stLinkButton > a:active {
        background:var(--bb-blue-active) !important;
        transform:translateY(3px) scale(.99) !important;
        box-shadow:inset 0 2px 5px rgba(0,0,0,.55), 0 0 8px rgba(0,104,255,.35) !important;
    }

    .stButton > button:focus-visible,
    .stDownloadButton > button:focus-visible,
    .stLinkButton > a:focus-visible {
        outline:2px solid #FFF !important;
        outline-offset:2px !important;
    }

    .stButton > button:disabled,
    .stDownloadButton > button:disabled {
        opacity:.45 !important;
        box-shadow:none !important;
    }

    [data-testid="stExpander"],
    [data-testid="stStatusWidget"] {
        border:1px solid var(--bb-orange) !important;
        background:#000 !important;
    }

    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary * {
        color:var(--bb-orange) !important;
        font-weight:900 !important;
    }

    [data-testid="stDataFrame"] {
        border:1px solid var(--bb-orange) !important;
        outline:1px solid var(--bb-orange) !important;
        outline-offset:-1px !important;
        background:#000 !important;
    }

    [data-testid="stDataFrame"] [role="columnheader"] {
        background:#000 !important;
        color:var(--bb-orange) !important;
        font-weight:900 !important;
        border-color:var(--bb-orange) !important;
    }

    [data-testid="stDataFrame"] [role="columnheader"] * {
        color:var(--bb-orange) !important;
        font-weight:900 !important;
    }

    [data-testid="stDataFrame"] [role="gridcell"] {
        background:#000 !important;
        border-color:#7a4300 !important;
    }

    /* Static one-row match tables: no filler rows, identical natural sizing. */
    [data-testid="stTable"] {
        border:1px solid var(--bb-orange) !important;
        background:#000 !important;
        overflow:hidden !important;
    }

    [data-testid="stTable"] table {
        width:100% !important;
        border-collapse:collapse !important;
        background:#000 !important;
        font-family:"Courier New",monospace !important;
    }

    [data-testid="stTable"] thead tr th {
        background:#000 !important;
        color:var(--bb-orange) !important;
        border:1px solid var(--bb-orange) !important;
        font-weight:900 !important;
        white-space:nowrap !important;
        padding:10px 9px !important;
    }

    [data-testid="stTable"] tbody tr td {
        background:#000 !important;
        color:var(--bb-orange) !important;
        border:1px solid var(--bb-orange) !important;
        white-space:nowrap !important;
        padding:10px 9px !important;
    }

    [data-testid="stProgress"] > div > div > div > div {
        background:var(--bb-orange) !important;
    }

    button[data-baseweb="tab"] {
        border:1px solid var(--bb-orange) !important;
        color:var(--bb-orange) !important;
        background:#000 !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        padding-left:16px !important;
        padding-right:16px !important;
    }

    button[data-baseweb="tab"][aria-selected="true"] {
        color:#000 !important;
        background:var(--bb-orange) !important;
    }

    button[data-baseweb="tab"][aria-selected="true"] * {
        color:#000 !important;
    }

    .processing-terminal {
        border:1px solid var(--bb-orange);
        background:#030303;
        margin:.25rem 0 .45rem 0;
        font-family:"Courier New",monospace;
        box-shadow:inset 0 0 18px rgba(255,140,0,.08);
    }

    .processing-terminal-title {
        background:var(--bb-orange);
        color:#000;
        font-weight:900;
        padding:4px 8px;
        letter-spacing:.08em;
        font-size:.78rem;
    }

    .processing-terminal pre {
        color:var(--bb-orange) !important;
        background:#030303 !important;
        margin:0 !important;
        padding:8px 10px !important;
        min-height:82px;
        max-height:190px;
        overflow-y:auto;
        white-space:pre-wrap;
        font-family:"Courier New",monospace !important;
        font-size:.78rem;
        line-height:1.35;
    }

    .winner-box {
        background:var(--bb-orange);
        color:#000;
        border:2px solid var(--bb-orange);
        padding:10px 14px;
        font-family:"Courier New",monospace;
        font-size:1.05rem;
        font-weight:900;
        letter-spacing:.05em;
        text-transform:uppercase;
        margin:.5rem 0 1rem 0;
    }

    .terminal-note {
        border:1px solid var(--bb-orange);
        padding:8px 10px;
        background:#030303;
        color:var(--bb-orange);
        font-family:"Courier New",monospace;
        font-size:.82rem;
        margin:.4rem 0;
    }

    [data-testid="stAlert"] {
        background:var(--bb-dark) !important;
        border:1px solid var(--bb-orange) !important;
    }

    [data-testid="stAlert"] * {
        color:var(--bb-orange) !important;
    }

    hr {
        border-color:var(--bb-orange) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


class ProcessingConsole:
    def __init__(self, placeholder, progress_bar, max_lines=11):
        self.placeholder = placeholder
        self.progress_bar = progress_bar
        self.max_lines = max_lines
        self.lines = []
        self.partial = ""
        self.total_etfs = 0
        self.completed_etfs = 0

    def _render(self):
        body = "\n".join(self.lines[-self.max_lines:])
        self.placeholder.markdown(
            (
                '<div class="processing-terminal">'
                '<div class="processing-terminal-title">PROCESSING LOG // DATA ENGINE</div>'
                f"<pre>{html.escape(body)}</pre>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )

    def _consume_line(self, line):
        line = line.strip()
        if not line:
            return

        stamp = datetime.now().strftime("%H:%M:%S")
        self.lines.append(f"{stamp}  {line}")

        discovered = re.search(r"Discovered\s+(\d+)\s+iShares muni ETFs", line)
        if discovered:
            self.total_etfs = int(discovered.group(1))
            self.progress_bar.progress(
                5,
                text=f"Discovered {self.total_etfs} municipal ETF sources",
            )

        if "✓" in line or "✗" in line:
            self.completed_etfs += 1
            if self.total_etfs:
                pct = 5 + int(88 * self.completed_etfs / self.total_etfs)
                self.progress_bar.progress(
                    min(pct, 93),
                    text=f"Processing ETF holdings {self.completed_etfs}/{self.total_etfs}",
                )

        self._render()

    def add(self, message):
        self._consume_line(message)

    def write(self, text):
        text = str(text)
        self.partial += text
        while "\n" in self.partial:
            line, self.partial = self.partial.split("\n", 1)
            self._consume_line(line)
        return len(text)

    def flush(self):
        if self.partial.strip():
            self._consume_line(self.partial)
        self.partial = ""


def _session_muni_cache_is_valid():
    loaded_at = st.session_state.get(MUNI_SESSION_AT_KEY)
    bundle = st.session_state.get(MUNI_SESSION_KEY)
    if bundle is None or loaded_at is None:
        return False
    return (time.time() - float(loaded_at)) < MUNI_TTL_SECONDS


def load_muni_universe():
    if _session_muni_cache_is_valid():
        return st.session_state[MUNI_SESSION_KEY], True

    with st.status("DATA ENGINE // INITIALIZING", expanded=True) as loader_status:
        progress_bar = st.progress(0, text="Loading municipal ETF holdings...")
        log_placeholder = st.empty()
        console = ProcessingConsole(log_placeholder, progress_bar)
        console.add("SESSION CACHE MISS // starting municipal data engine")
        started = time.perf_counter()

        try:
            with redirect_stdout(console):
                bundle = load_all_ishares_munis()

            console.flush()
            df, source_rows, etf_status, as_of = bundle
            elapsed = time.perf_counter() - started
            loaded_now = int((etf_status["Status"] == "OK").sum())

            console.add(
                f"MERGE // deduplicated holdings into {len(df):,} unique CUSIPs"
            )
            console.add(
                f"READY // {loaded_now} ETF sources loaded in {elapsed:.1f}s"
            )
            progress_bar.progress(
                100,
                text=f"READY • {len(df):,} unique municipal CUSIPs",
            )

            st.session_state[MUNI_SESSION_KEY] = bundle
            st.session_state[MUNI_SESSION_AT_KEY] = time.time()
            loader_status.update(
                label=f"DATA ENGINE // READY • {len(df):,} CUSIPs",
                state="complete",
                expanded=False,
            )
            return bundle, False

        except Exception as exc:
            console.flush()
            console.add(f"ERROR // {type(exc).__name__}: {exc}")
            loader_status.update(
                label="DATA ENGINE // ERROR",
                state="error",
                expanded=True,
            )
            raise


@st.cache_data(ttl="30m", show_spinner=False)
def load_treasury_market():
    return load_treasury_quotes()


def render_copy_cusips(cusips, title="COPY CUSIPs"):
    clean = []
    for value in cusips:
        value = str(value).strip().upper()
        if value and value not in clean:
            clean.append(value)

    if not clean:
        return

    with st.expander(f"{title} // {len(clean):,}"):
        st.caption("Use the copy icon. Left = one per line; right = comma-separated.")
        left, right = st.columns(2)
        with left:
            st.markdown("**ONE PER LINE**")
            st.code("\n".join(clean), language=None)
        with right:
            st.markdown("**COMMA-SEPARATED**")
            st.code(", ".join(clean), language=None)


def render_treasury_loader(prefix):
    c1, c2, _ = st.columns([1.35, 1.2, 3.45])

    with c1:
        load_clicked = st.button(
            "Load / Refresh Treasurys",
            type="primary",
            width="stretch",
            key=f"{prefix}_load_treasury",
        )

    with c2:
        if st.button(
            "Clear Treasury Cache",
            width="stretch",
            key=f"{prefix}_clear_treasury",
        ):
            load_treasury_market.clear()
            st.session_state.pop("treasury_quotes", None)
            st.session_state.pop("treasury_meta", None)
            st.rerun()

    if load_clicked:
        with st.status("TREASURY ENGINE // CONNECTING", expanded=True) as status:
            p = st.progress(10, text="Opening WSJ U.S. Treasury Quotes...")
            try:
                quotes, meta = load_treasury_market()
                st.session_state["treasury_quotes"] = quotes
                st.session_state["treasury_meta"] = meta
                p.progress(100, text=f"READY • {len(quotes):,} Treasury rows")
                status.update(
                    label=f"TREASURY ENGINE // READY • {meta.get('source', 'SOURCE')}",
                    state="complete",
                    expanded=False,
                )
            except Exception as exc:
                status.update(
                    label="TREASURY ENGINE // ERROR",
                    state="error",
                    expanded=True,
                )
                st.error(f"Treasury data load failed: {exc}")

    return (
        st.session_state.get("treasury_quotes"),
        st.session_state.get("treasury_meta"),
    )


def render_treasury_source(meta):
    if meta.get("fallback"):
        st.warning(
            "WSJ individual Treasury rows were unavailable, so the official U.S. "
            "Treasury daily par yield curve is being used as a fallback."
        )

    st.caption(
        f"Treasury source: {meta.get('source', 'Unknown')} | "
        f"Data as of: {meta.get('as_of', '')}. {meta.get('note', '')}"
    )


def render_muni_screener(df, source_rows, etf_status, as_of):
    loaded_etfs = int((etf_status["Status"] == "OK").sum())
    failed_etfs = int((etf_status["Status"] != "OK").sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Unique CUSIPs", f"{len(df):,}")
    m2.metric("Raw ETF rows", f"{len(source_rows):,}")
    m3.metric("ETFs loaded", f"{loaded_etfs:,}")
    m4.metric("Latest source date", str(as_of))

    if failed_etfs:
        st.warning(
            f"{failed_etfs} ETF source(s) failed to load. Open Source Status below."
        )

    with st.expander("Source status"):
        st.dataframe(
            etf_status.sort_values(["Status", "Ticker"]).reset_index(drop=True),
            width="stretch",
            hide_index=True,
        )

    st.subheader("Filters")

    row1 = st.columns([1.2, 1.6, 1.0, 1.0, 1.0])
    with row1[0]:
        cusip = st.text_input(
            "CUSIP",
            placeholder="e.g. 107431KW7",
            key="screen_cusip",
        ).strip().upper()

    available_states = sorted(
        x for x in df["State"].dropna().astype(str).unique()
        if x and x != "Unknown"
    )

    with row1[1]:
        states = st.multiselect(
            "States",
            available_states,
            placeholder="All states",
            key="screen_states",
        )

    with row1[2]:
        purchase_face = st.number_input(
            "Purchase face ($)",
            min_value=1000,
            max_value=10_000_000,
            value=5000,
            step=1000,
            key="screen_face",
        )

    with row1[3]:
        price_min = st.number_input(
            "Price min",
            min_value=0.0,
            max_value=200.0,
            value=None,
            step=0.01,
            placeholder="No minimum",
            key="screen_price_min",
        )

    with row1[4]:
        price_max = st.number_input(
            "Price max",
            min_value=0.0,
            max_value=200.0,
            value=None,
            step=0.01,
            placeholder="No maximum",
            key="screen_price_max",
        )

    row2 = st.columns(5)
    with row2[0]:
        coupon_min = st.number_input(
            "Coupon min (%)",
            min_value=0.0,
            max_value=20.0,
            value=None,
            step=0.01,
            key="screen_coupon_min",
        )
    with row2[1]:
        coupon_max = st.number_input(
            "Coupon max (%)",
            min_value=0.0,
            max_value=20.0,
            value=None,
            step=0.01,
            key="screen_coupon_max",
        )
    with row2[2]:
        ytw_min = st.number_input(
            "YTW min (%)",
            min_value=-10.0,
            max_value=50.0,
            value=None,
            step=0.01,
            key="screen_ytw_min",
        )
    with row2[3]:
        ytw_max = st.number_input(
            "YTW max (%)",
            min_value=-10.0,
            max_value=50.0,
            value=None,
            step=0.01,
            key="screen_ytw_max",
        )
    with row2[4]:
        sort_by = st.selectbox(
            "Sort",
            [
                "YTW: High → Low",
                "Price: Low → High",
                "Coupon: High → Low",
                "Maturity: Soonest",
                "ETF Coverage: High → Low",
            ],
            key="screen_sort",
        )

    row3 = st.columns(5)
    with row3[0]:
        use_mfrom = st.checkbox("Minimum maturity", key="screen_use_mfrom")
        maturity_from = (
            st.date_input("Maturity from", key="screen_mfrom")
            if use_mfrom else None
        )
    with row3[1]:
        use_mto = st.checkbox("Maximum maturity", key="screen_use_mto")
        maturity_to = (
            st.date_input("Maturity to", key="screen_mto")
            if use_mto else None
        )
    with row3[2]:
        ig_only = st.checkbox("Investment grade", key="screen_ig")
    with row3[3]:
        amt_only = st.checkbox("AMT-exempt evidence", key="screen_amt")
    with row3[4]:
        noncall = st.checkbox("Non-callable proxy", key="screen_noncall")

    row4 = st.columns([1.35, 1, 1, 2.65])
    with row4[0]:
        nist_only = st.checkbox(
            "No State Individual Income Tax",
            key="screen_nist",
        )
    with row4[1]:
        new_issue = st.checkbox("New issues", key="screen_new")
    with row4[2]:
        new_days = st.selectbox(
            "New issue window",
            [30, 60, 90, 180],
            index=1,
            disabled=not new_issue,
            key="screen_new_days",
        )

    screen_df = (
        df
        if cusip or not nist_only
        else df[df["State"].isin(NO_INDIVIDUAL_INCOME_TAX_STATES)].copy()
    )

    results = screen_munis(
        df=screen_df,
        cusip=cusip,
        states=states or None,
        purchase_face=purchase_face,
        price_min=price_min,
        price_max=price_max,
        coupon_min=coupon_min,
        coupon_max=coupon_max,
        ytw_min=ytw_min,
        ytw_max=ytw_max,
        maturity_from=maturity_from,
        maturity_to=maturity_to,
        investment_grade_only=ig_only,
        amt_exempt_only=amt_only,
        non_callable_only=noncall,
        new_issue_only=new_issue,
        new_issue_days=new_days,
        as_of=as_of,
        sort_by=sort_by,
    )

    st.divider()
    st.metric("Matches", f"{len(results):,}")

    if cusip and results.empty:
        st.info(
            f"CUSIP {cusip} was not found in the currently combined ETF universe."
        )

    cols = [
        "CUSIP", "Name", "State", "Price", "Coupon (%)", "YTM (%)",
        "Yield to Worst (%)", "Yield to Call (%)", "Maturity", "Rating",
        "Investment Grade", "AMT Exempt", "Source ETFs", "Source Count",
        "Purchase Face ($)", "Est. Principal Cost ($)",
        "Annual Coupon Income ($)", "NonCallableProxy",
    ]
    cols = [c for c in cols if c in results.columns]

    st.dataframe(
        results[cols],
        width="stretch",
        height=650,
        hide_index=True,
    )

    if "CUSIP" in results.columns:
        render_copy_cusips(
            results["CUSIP"].dropna().tolist(),
            "COPY FILTERED CUSIPs",
        )

    st.download_button(
        "Download filtered CSV",
        data=results.to_csv(index=False).encode("utf-8"),
        file_name="muni_screen_results.csv",
        mime="text/csv",
        type="primary",
        key="screen_download",
    )


def _format_gap(days):
    """Turn a day difference into a compact readable duration."""
    d = abs(int(days))

    if d == 0:
        return "0 days"
    if d == 1:
        return "1 day"
    if d < 14:
        return f"{d} days"

    if d < 60:
        weeks, rem = divmod(d, 7)
        if rem:
            return f"{weeks} wk {rem} d"
        return f"{weeks} wk"

    if d < 730:
        months, rem = divmod(d, 30)
        if rem:
            return f"{months} mo {rem} d"
        return f"{months} mo"

    years, rem = divmod(d, 365)
    months = rem // 30
    if months:
        return f"{years} yr {months} mo"
    return f"{years} yr"


def _candidate_selector(candidates, key):
    labels = []
    for _, row in candidates.iterrows():
        maturity = pd.Timestamp(row["Maturity"]).strftime("%Y-%m-%d")
        labels.append(
            f"{row['CUSIP']} | {maturity} | {row['State']} | "
            f"YTW {float(row['Yield to Worst (%)']):.3f}% | "
            f"gap {_format_gap(row['Maturity Gap Days'])}"
        )

    prior = st.session_state.get(key)
    if prior not in labels:
        st.session_state[key] = labels[0]

    chosen = st.selectbox(
        "Closest muni candidates — choose the bond to compare",
        labels,
        key=key,
    )
    return candidates.iloc[labels.index(chosen)]


def _render_stacked_matches(
    muni,
    treasury,
    treasury_gap,
    prefix,
    extra_muni=None,
):
    st.markdown("### Municipal Match")

    muni_row = {
        "CUSIP": muni["CUSIP"],
        "Name": muni["Name"],
        "State": muni["State"],
        "Maturity": pd.Timestamp(muni["Maturity"]).date(),
        "Client Date Gap": _format_gap(muni["Maturity Gap Days"]),
        "Price": muni.get("Price"),
        "Coupon (%)": muni.get("Coupon (%)"),
        "YTW (%)": muni.get("Yield to Worst (%)"),
        "Rating": muni.get("Rating"),
        "Source ETFs": muni.get("Source ETFs"),
    }

    if extra_muni:
        muni_row.update(extra_muni)

    st.table(pd.DataFrame([muni_row]))
    render_copy_cusips(
        [muni["CUSIP"]],
        "COPY SELECTED MUNI CUSIP",
    )

    st.markdown("### Treasury Match")

    treasury_view = pd.DataFrame([
        {
            "Type": treasury.get("Security Type"),
            "Maturity": pd.Timestamp(treasury["Maturity"]).date(),
            "Muni Date Gap": _format_gap(treasury_gap),
            "Coupon (%)": treasury.get("Coupon (%)"),
            "Bid": treasury.get("Bid"),
            "Asked": treasury.get("Asked"),
            "Asked Yield (%)": treasury.get("Asked Yield (%)"),
            "Source": treasury.get("Source"),
        }
    ])

    st.table(treasury_view)

    st.link_button(
        "OPEN WSJ TREASURY NOTES / BONDS / T-BILLS",
        "https://www.wsj.com/market-data/bonds",
        width="stretch",
    )


def render_nist_comparison(df):
    st.subheader("Muni vs U.S. Treasury // After-Tax Yield")
    st.caption("NIST stands for No Income State Tax.")

    st.markdown(
        """
        <div class="terminal-note">
        NIST STATES ONLY. ENTER CLIENT FEDERAL RATE + TARGET MATURITY.<br>
        MUNI = YIELD-TO-WORST. TREASURY = WSJ ASKED YIELD WHEN AVAILABLE.
        </div>
        """,
        unsafe_allow_html=True,
    )

    r = st.columns([1.0, 1.2, 1.8, 1.0])
    with r[0]:
        fed = st.number_input(
            "Client federal tax bracket (%)",
            0.0,
            60.0,
            value=None,
            step=0.1,
            key="nist_fed",
        )
    with r[1]:
        default_target = (
            pd.Timestamp.today().normalize() + pd.DateOffset(years=5)
        ).date()
        target = st.date_input(
            "Client target maturity",
            default_target,
            key="nist_target",
        )
    with r[2]:
        states = st.multiselect(
            "Muni state(s) — 9 NIST states",
            sorted(NO_INDIVIDUAL_INCOME_TAX_STATES),
            placeholder="Blank = all 9 states",
            key="nist_states",
        )
    with r[3]:
        ig_only = st.checkbox(
            "Investment grade only",
            key="nist_ig",
        )

    treasury_df, meta = render_treasury_loader("nist")
    if treasury_df is None or meta is None:
        st.info("Click **LOAD / REFRESH TREASURYS** once.")
        return

    render_treasury_source(meta)

    pool = df[df["State"].isin(NO_INDIVIDUAL_INCOME_TAX_STATES)].copy()
    if ig_only:
        pool = pool[pool["Investment Grade"].eq("Yes")].copy()

    candidates = nearest_muni_candidates(
        pool,
        target,
        states=states or None,
        limit=25,
    )

    if candidates.empty:
        st.warning("No usable municipal bonds were found near that maturity.")
        return

    muni = _candidate_selector(candidates, "nist_choice")
    treasury = nearest_treasury(
        treasury_df,
        pd.Timestamp(muni["Maturity"]),
    )

    if treasury is None:
        st.warning("No usable Treasury quote was available.")
        return

    treasury_gap = abs(
        (
            pd.Timestamp(treasury["Maturity"])
            - pd.Timestamp(muni["Maturity"])
        ).days
    )

    _render_stacked_matches(
        muni,
        treasury,
        treasury_gap,
        "nist",
    )

    if fed is None:
        st.info(
            "Enter the client's **federal marginal tax bracket** to calculate the winner."
        )
        return

    muni_ytw = float(muni["Yield to Worst (%)"])
    treasury_yield = float(treasury["Asked Yield (%)"])
    tax_rate = float(fed) / 100.0
    after_tax_factor = 1.0 - tax_rate

    comp = tax_equivalent_comparison(
        muni_ytw,
        treasury_yield,
        tax_rate,
    )

    st.subheader("Tax Exempt Status for NIST")
    st.caption("NIST stands for No Income State Tax.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Muni YTW / After-Tax",
        f"{comp['Muni After-Tax Yield (%)']:.3f}%",
    )
    c2.metric(
        "Taxable Yield Needed to Match Muni (TEY)",
        f"{comp['Muni Tax-Equivalent Yield (%)']:.3f}%",
    )
    c3.metric(
        "Treasury Gross Yield",
        f"{treasury_yield:.3f}%",
    )
    c4.metric(
        "Treasury After-Tax Yield",
        f"{comp['Treasury After-Tax Yield (%)']:.3f}%",
    )

    spread = comp["After-Tax Spread (bps)"]
    if comp["Winner"] == "MUNICIPAL":
        winner = (
            f"MUNICIPAL WINS // +{abs(spread):.1f} BPS AFTER FEDERAL TAX"
        )
    elif comp["Winner"] == "TREASURY":
        winner = (
            f"TREASURY WINS // +{abs(spread):.1f} BPS AFTER FEDERAL TAX"
        )
    else:
        winner = "TIE // SAME AFTER-TAX YIELD"

    st.markdown(
        f'<div class="winner-box">{html.escape(winner)}</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="terminal-note">
        TAXABLE YIELD NEEDED TO MATCH MUNI (TEY) = {comp['Muni Tax-Equivalent Yield (%)']:.3f}%<br>
        CLIENT FEDERAL RATE = {float(fed):.1f}%<br><br>
        FORMULA: MUNI TEY = MUNI YTW ÷ (1 − TAX RATE)<br>
        VALUES: {muni_ytw:.3f}% ÷ (1 − {float(fed):.1f}%) = {comp['Muni Tax-Equivalent Yield (%)']:.3f}%<br>
        SIMPLIFIED: {muni_ytw:.3f}% ÷ {after_tax_factor:.3f} = {comp['Muni Tax-Equivalent Yield (%)']:.3f}%<br><br>
        FORMULA: TREASURY AFTER-TAX = TREASURY YIELD × (1 − TAX RATE)<br>
        VALUES: {treasury_yield:.3f}% × (1 − {float(fed):.1f}%) = {comp['Treasury After-Tax Yield (%)']:.3f}%<br>
        SIMPLIFIED: {treasury_yield:.3f}% × {after_tax_factor:.3f} = {comp['Treasury After-Tax Yield (%)']:.3f}%
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Show 25 closest municipal candidates"):
        nearby = candidates.copy()
        nearby["Maturity Gap"] = nearby["Maturity Gap Days"].map(_format_gap)
        cols = [
            "CUSIP", "Name", "State", "Maturity", "Maturity Gap",
            "Price", "Coupon (%)", "Yield to Worst (%)", "Rating",
            "Source ETFs",
        ]
        cols = [c for c in cols if c in nearby.columns]
        st.dataframe(
            nearby[cols],
            width="stretch",
            hide_index=True,
        )
        render_copy_cusips(
            candidates["CUSIP"].tolist(),
            "COPY 25 CANDIDATE CUSIPs",
        )


def render_state_income_tax_comparison(df):
    st.subheader("State Income Tax // All 50 Munis vs U.S. Treasury")

    st.markdown(
        """
        <div class="terminal-note">
        FOR CLIENTS WHO LIVE IN A STATE WITH AN INCOME TAX.<br>
        ALL 50 MUNICIPAL-BOND STATES ARE AVAILABLE.<br>
        U.S. TREASURY INTEREST IS EXEMPT FROM STATE AND LOCAL INCOME TAX.<br>
        BY DEFAULT THE SELECTED MUNI IS TREATED AS STATE-TAXABLE.
        </div>
        """,
        unsafe_allow_html=True,
    )

    default_indiana = ALL_US_STATES.index("Indiana")

    r1 = st.columns([1.15, 1.1, 1.1, 1.2, 1.55])
    with r1[0]:
        client_state = st.selectbox(
            "Client state of residence",
            ALL_US_STATES,
            index=default_indiana,
            key="state_client_state",
        )
    with r1[1]:
        state_rate_pct = st.number_input(
            "Client state income tax rate (%)",
            0.0,
            20.0,
            value=None,
            step=0.01,
            placeholder="e.g. 2.95",
            key="state_rate",
        )
    with r1[2]:
        federal_pct = st.number_input(
            "Client federal tax bracket (%)",
            0.0,
            60.0,
            value=None,
            step=0.1,
            placeholder="e.g. 22",
            key="state_fed",
        )
    with r1[3]:
        default_target = (
            pd.Timestamp.today().normalize() + pd.DateOffset(years=5)
        ).date()
        target = st.date_input(
            "Client target maturity",
            default_target,
            key="state_target",
        )
    with r1[4]:
        muni_states = st.multiselect(
            "Muni state(s) — all 50 states",
            ALL_US_STATES,
            placeholder="Blank = all 50 states",
            key="state_muni_states",
        )

    r2 = st.columns([1.25, 1.75, 3.0])
    with r2[0]:
        ig_only = st.checkbox(
            "Investment grade only",
            value=True,
            key="state_ig",
        )
    with r2[1]:
        home_state_exempt = st.checkbox(
            "Apply home-state muni exemption",
            value=False,
            key="state_home_exempt",
            help=(
                "OFF treats the selected muni as state-taxable. ON applies "
                "0% state tax only when the muni state equals the client's "
                "residence state. Verify the bond's tax treatment."
            ),
        )

    treasury_df, meta = render_treasury_loader("state")
    if treasury_df is None or meta is None:
        st.info("Click **LOAD / REFRESH TREASURYS** once.")
        return

    render_treasury_source(meta)

    pool = df.copy()
    if ig_only:
        pool = pool[pool["Investment Grade"].eq("Yes")].copy()

    candidates = nearest_muni_candidates(
        pool,
        target,
        states=muni_states or None,
        limit=50,
    )

    if candidates.empty:
        st.warning(
            "No usable municipal bonds were found near that maturity for the selected state filter."
        )
        return

    muni = _candidate_selector(candidates, "state_choice")
    treasury = nearest_treasury(
        treasury_df,
        pd.Timestamp(muni["Maturity"]),
    )

    if treasury is None:
        st.warning("No usable Treasury quote was available.")
        return

    treasury_gap = abs(
        (
            pd.Timestamp(treasury["Maturity"])
            - pd.Timestamp(muni["Maturity"])
        ).days
    )

    is_home_state = str(muni.get("State", "")) == client_state
    state_exempt_applied = bool(home_state_exempt and is_home_state)

    _render_stacked_matches(
        muni,
        treasury,
        treasury_gap,
        "state",
        extra_muni={
            "Client Residence": client_state,
            "State Tax Treatment": (
                "HOME-STATE EXEMPT"
                if state_exempt_applied
                else "STATE-TAXABLE"
            ),
        },
    )

    if federal_pct is None or state_rate_pct is None:
        st.info(
            "Enter both the client's **federal tax bracket** and **state income-tax rate**."
        )
        return

    muni_ytw = float(muni["Yield to Worst (%)"])
    treasury_gross = float(treasury["Asked Yield (%)"])
    federal_rate = float(federal_pct) / 100.0
    state_rate = float(state_rate_pct) / 100.0
    muni_state_rate = 0.0 if state_exempt_applied else state_rate

    muni_after_tax = muni_ytw * (1.0 - muni_state_rate)
    treasury_after_tax = treasury_gross * (1.0 - federal_rate)
    treasury_needed = muni_after_tax / (1.0 - federal_rate)
    spread_bps = (muni_after_tax - treasury_after_tax) * 100.0

    if spread_bps > 0:
        winner = "MUNICIPAL"
    elif spread_bps < 0:
        winner = "TREASURY"
    else:
        winner = "TIE"

    st.subheader("State Income Tax Comparison")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Muni YTW", f"{muni_ytw:.3f}%")
    c2.metric(
        "Muni After-State-Tax Yield",
        f"{muni_after_tax:.3f}%",
    )
    c3.metric(
        "Treasury Gross Yield",
        f"{treasury_gross:.3f}%",
    )
    c4.metric(
        "Treasury After-Federal-Tax Yield",
        f"{treasury_after_tax:.3f}%",
    )

    if winner == "MUNICIPAL":
        winner_text = (
            f"MUNICIPAL WINS // +{abs(spread_bps):.1f} BPS AFTER TAX"
        )
    elif winner == "TREASURY":
        winner_text = (
            f"TREASURY WINS // +{abs(spread_bps):.1f} BPS AFTER TAX"
        )
    else:
        winner_text = "TIE // SAME AFTER-TAX YIELD"

    st.markdown(
        f'<div class="winner-box">{html.escape(winner_text)}</div>',
        unsafe_allow_html=True,
    )

    treatment = (
        "0.000% HOME-STATE EXEMPTION APPLIED"
        if state_exempt_applied
        else f"{float(state_rate_pct):.3f}% STATE TAX APPLIED"
    )

    st.markdown(
        f"""
        <div class="terminal-note">
        CLIENT RESIDENCE = {html.escape(client_state)}<br>
        MUNICIPAL STATE = {html.escape(str(muni['State']))}<br>
        MUNI STATE TAX TREATMENT = {treatment}<br>
        TREASURY STATE TAX = 0.000% (STATE/LOCAL INCOME-TAX EXEMPT)<br><br>
        MUNI AFTER-TAX FORMULA = MUNI YTW × (1 − STATE TAX RATE APPLIED)<br>
        VALUES: {muni_ytw:.3f}% × (1 − {muni_state_rate * 100:.3f}%) = {muni_after_tax:.3f}%<br><br>
        TREASURY AFTER-TAX FORMULA = TREASURY YIELD × (1 − FEDERAL TAX RATE)<br>
        VALUES: {treasury_gross:.3f}% × (1 − {float(federal_pct):.1f}%) = {treasury_after_tax:.3f}%<br><br>
        TREASURY GROSS YIELD NEEDED TO MATCH MUNI = {treasury_needed:.3f}%<br>
        FORMULA: {muni_after_tax:.3f}% ÷ (1 − {float(federal_pct):.1f}%) = {treasury_needed:.3f}%
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        "Tax assumptions: municipal interest is treated as federally tax-exempt; "
        "Treasury interest is federally taxable and state/local income-tax exempt. "
        "Municipal state-tax treatment varies by state, issuer, residency, and bond type. "
        "The optional home-state exemption is a user-controlled assumption. AMT, local "
        "taxes, special reciprocity rules, and territory-specific treatment are not modeled "
        "unless included in the entered state rate."
    )

    with st.expander("Show 50 closest municipal candidates"):
        nearby = candidates.copy()
        nearby["Maturity Gap"] = nearby["Maturity Gap Days"].map(_format_gap)
        cols = [
            "CUSIP", "Name", "State", "Maturity", "Maturity Gap",
            "Price", "Coupon (%)", "Yield to Worst (%)", "Rating",
            "Source ETFs",
        ]
        cols = [c for c in cols if c in nearby.columns]
        st.dataframe(
            nearby[cols],
            width="stretch",
            hide_index=True,
        )
        render_copy_cusips(
            candidates["CUSIP"].tolist(),
            "COPY 50 CANDIDATE CUSIPs",
        )


def _secret_value(section, key, default=""):
    try:
        return str(st.secrets.get(section, {}).get(key, default)).strip()
    except Exception:
        return default


def _etrade_credentials():
    return (
        _secret_value("etrade", "consumer_key"),
        _secret_value("etrade", "consumer_secret"),
        _secret_value("etrade", "environment", "live").lower(),
    )


def _etrade_client():
    consumer_key, consumer_secret, environment = _etrade_credentials()
    token = st.session_state.get("etrade_access_token")
    if not consumer_key or not consumer_secret or not token:
        return None
    return ETradeClient(
        consumer_key,
        consumer_secret,
        token["oauth_token"],
        token["oauth_token_secret"],
        environment,
    )


def _touch_etrade_session():
    now = time.time()
    st.session_state["etrade_last_activity_at"] = now
    token = st.session_state.get("etrade_access_token")
    if token is not None:
        token.setdefault("issued_at", now)


def _calculate_etrade_expirations(issued_at, last_activity):
    issued_et = datetime.fromtimestamp(issued_at, ETRADE_TIMEZONE)
    midnight_et = datetime.combine(
        issued_et.date() + timedelta(days=1),
        datetime_time.min,
        tzinfo=ETRADE_TIMEZONE,
    )
    inactivity_expiry = last_activity + ETRADE_INACTIVITY_SECONDS
    hard_expiry = midnight_et.timestamp()
    return min(inactivity_expiry, hard_expiry), hard_expiry, midnight_et


def _etrade_expirations():
    token = st.session_state.get("etrade_access_token") or {}
    now = time.time()
    issued_at = float(token.setdefault("issued_at", now))
    last_activity = float(
        st.session_state.setdefault("etrade_last_activity_at", issued_at)
    )
    return _calculate_etrade_expirations(issued_at, last_activity)


def _render_etrade_session_timer(connected):
    if not connected:
        st.markdown(
            '<div class="terminal-note">E*TRADE SESSION TIMER // NOT CONNECTED</div>',
            unsafe_allow_html=True,
        )
        return

    expiry, hard_expiry, midnight_et = _etrade_expirations()
    midnight_label = midnight_et.strftime("%I:%M %p ET").lstrip("0")
    components.html(
        f"""
        <style>
            html, body {{ margin:0; padding:0; background:#000; }}
            .timer {{
                height:54px; box-sizing:border-box; border:1px solid #FF8C00;
                background:#030303; color:#FF8C00; padding:7px 11px;
                font-family:'Courier New',monospace; display:flex;
                align-items:center; justify-content:space-between; gap:16px;
            }}
            .title {{ font-size:12px; font-weight:900; letter-spacing:.06em; }}
            .clock {{ font-size:24px; font-weight:900; color:#00D084; white-space:nowrap; }}
            .detail {{ font-size:11px; color:#FF8C00; text-align:right; }}
        </style>
        <div class="timer">
            <div>
                <div class="title" id="title">E*TRADE API SESSION // ACTIVE</div>
                <div class="detail">2-HOUR INACTIVITY LIMIT • HARD EXPIRY {midnight_label}</div>
            </div>
            <div class="clock" id="clock">--:--:--</div>
        </div>
        <script>
            const expiry = {int(expiry * 1000)};
            const hardExpiry = {int(hard_expiry * 1000)};
            const clock = document.getElementById('clock');
            const title = document.getElementById('title');
            function tick() {{
                const now = Date.now();
                const remaining = Math.max(0, expiry - now);
                if (now >= hardExpiry) {{
                    clock.textContent = 'EXPIRED';
                    clock.style.color = '#FF3B30';
                    title.textContent = 'E*TRADE API SESSION // RECONNECT REQUIRED';
                    return;
                }}
                if (remaining <= 0) {{
                    clock.textContent = 'INACTIVE';
                    clock.style.color = '#FF3B30';
                    title.textContent = 'E*TRADE API SESSION // CLICK RENEW';
                    return;
                }}
                const hours = Math.floor(remaining / 3600000);
                const minutes = Math.floor((remaining % 3600000) / 60000);
                const seconds = Math.floor((remaining % 60000) / 1000);
                clock.textContent = [hours, minutes, seconds]
                    .map(value => String(value).padStart(2, '0')).join(':');
                if (remaining <= 10 * 60000) {{
                    clock.style.color = '#FF3B30';
                    title.textContent = 'E*TRADE API SESSION // RENEW NOW';
                }} else if (remaining <= 30 * 60000) {{
                    clock.style.color = '#FF8C00';
                    title.textContent = 'E*TRADE API SESSION // EXPIRING SOON';
                }} else {{
                    clock.style.color = '#00D084';
                    title.textContent = 'E*TRADE API SESSION // ACTIVE';
                }}
            }}
            tick();
            setInterval(tick, 1000);
        </script>
        """,
        height=58,
        scrolling=False,
    )


def _masked_account(value):
    value = str(value or "")
    return f"••••{value[-4:]}" if len(value) >= 4 else value


def _account_label(account):
    name = (
        account.get("accountName")
        or account.get("accountDesc")
        or account.get("accountType")
        or "ACCOUNT"
    )
    return f"{name} // {_masked_account(account.get('accountId'))}"


def _refresh_accounts(client):
    accounts = client.list_accounts()
    _touch_etrade_session()
    st.session_state["etrade_accounts"] = accounts
    return accounts


def _default_account_index(accounts):
    for index, account in enumerate(accounts):
        account_id = str(account.get("accountId", ""))
        if account_id.endswith(DEFAULT_ETRADE_ACCOUNT_SUFFIX):
            return index
    return 0


def _account_picker(key):
    accounts = st.session_state.get("etrade_accounts", [])
    if not accounts:
        return None

    valid_indexes = range(len(accounts))
    default_index = _default_account_index(accounts)
    default_marker_key = f"_{key}_default_account_suffix"

    # Migrate any already-open Streamlit session to the requested default account.
    # After the one-time reset, a manual account change remains sticky for the session.
    if st.session_state.get(default_marker_key) != DEFAULT_ETRADE_ACCOUNT_SUFFIX:
        st.session_state.pop(key, None)
        st.session_state[default_marker_key] = DEFAULT_ETRADE_ACCOUNT_SUFFIX

    if key in st.session_state and st.session_state[key] not in valid_indexes:
        st.session_state.pop(key, None)

    selected = st.selectbox(
        "E*TRADE Account",
        valid_indexes,
        index=default_index,
        format_func=lambda index: _account_label(accounts[index]),
        key=key,
    )
    return accounts[selected]


def render_etrade_connection():
    consumer_key, consumer_secret, environment = _etrade_credentials()
    connected = _etrade_client() is not None
    status_text = "CONNECTED" if connected else (
        f"READY // {environment.upper()}" if consumer_key and consumer_secret else "SETUP REQUIRED"
    )

    _render_etrade_session_timer(connected)
    status_col, connect_col, renew_col, disconnect_col = st.columns([3.3, 1.7, 1.1, 1.2])
    with status_col:
        st.markdown(
            f'<div class="terminal-note">E*TRADE API // {html.escape(status_text)}</div>',
            unsafe_allow_html=True,
        )
    with connect_col:
        if st.button(
            "CONNECT E*TRADE",
            type="primary",
            width="stretch",
            disabled=not (consumer_key and consumer_secret),
            key="etrade_connect",
        ):
            try:
                request = begin_authorization(consumer_key, consumer_secret, environment)
                st.session_state["etrade_request"] = {
                    "oauth_token": request.oauth_token,
                    "oauth_token_secret": request.oauth_token_secret,
                    "authorization_url": request.authorization_url,
                }
            except ETradeError as exc:
                st.error(str(exc))
    with renew_col:
        if st.button("RENEW", width="stretch", disabled=not connected, key="etrade_renew"):
            try:
                _etrade_client().renew()
                _touch_etrade_session()
                st.success("E*TRADE session renewed.")
            except ETradeError as exc:
                st.error(str(exc))
    with disconnect_col:
        if st.button("DISCONNECT", width="stretch", disabled=not connected, key="etrade_disconnect"):
            for state_key in [
                "etrade_access_token", "etrade_accounts", "etrade_request",
                "etrade_holdings", "etrade_balances", "etrade_quote",
                "etrade_last_activity_at", "orders_account", "holdings_account",
                "_orders_account_default_account_suffix",
                "_holdings_account_default_account_suffix",
            ]:
                st.session_state.pop(state_key, None)
            st.rerun()

    if not consumer_key or not consumer_secret:
        st.warning(
            "Add E*TRADE consumer_key and consumer_secret in Streamlit App Settings → Secrets. "
            "Credentials are intentionally excluded from GitHub."
        )

    request = st.session_state.get("etrade_request")
    if request and not connected:
        with st.container(border=True):
            st.subheader("Complete E*TRADE Authorization")
            left, right = st.columns([1, 1.25])
            with left:
                st.link_button(
                    "1 // OPEN E*TRADE LOGIN",
                    request["authorization_url"],
                    type="primary",
                    width="stretch",
                )
                st.caption("Approve access and copy the verification code E*TRADE displays.")
            with right:
                verifier = st.text_input(
                    "2 // Verification Code",
                    placeholder="Enter the code shown by E*TRADE",
                    max_chars=12,
                    key="etrade_verifier",
                )
                if st.button(
                    "VERIFY AND CONNECT",
                    width="stretch",
                    disabled=not verifier.strip(),
                    key="etrade_verify",
                ):
                    try:
                        token = complete_authorization(
                            consumer_key,
                            consumer_secret,
                            request["oauth_token"],
                            request["oauth_token_secret"],
                            verifier,
                            environment,
                        )
                        token["issued_at"] = time.time()
                        st.session_state["etrade_access_token"] = token
                        _touch_etrade_session()
                        st.session_state.pop("etrade_request", None)
                        _refresh_accounts(_etrade_client())
                        st.rerun()
                    except ETradeError as exc:
                        st.error(str(exc))


def _account_balance(client, account, refresh=False):
    account_key = str(account.get("accountIdKey", ""))
    balances = st.session_state.setdefault("etrade_balances", {})
    if refresh or account_key not in balances:
        balances[account_key] = client.get_balance(account_key)
        _touch_etrade_session()
    return balances[account_key]


def _balance_snapshot(payload):
    total = total_account_value(payload)
    cash_available = find_number(
        payload,
        "cashAvailableForInvestment",
        "cashBuyingPower",
    )
    net_cash = find_number(
        payload,
        "netCash",
        "cashBalance",
        "moneyMktBalance",
    )
    market_value = find_number(
        payload,
        "netMv",
        "netMarketValue",
        "totalMarketValue",
    )
    if total is None and market_value is not None:
        total = market_value + (net_cash if net_cash is not None else (cash_available or 0.0))
    return (
        float(total or 0.0),
        float(cash_available or 0.0),
        float(market_value or 0.0),
    )


def _price_ladder(current, entry, stop, target, put_wall, call_wall):
    raw_levels = [
        ("STOP", stop, "#FF3B30"),
        ("ENTRY", entry, "#00A6FF"),
        ("CURRENT", current, "#FFFFFF"),
        ("TARGET", target, "#00D084"),
    ]
    if put_wall > 0:
        raw_levels.append(("PUT WALL", put_wall, "#B692F6"))
    if call_wall > 0:
        raw_levels.append(("CALL WALL", call_wall, "#FF8C00"))
    raw_levels.sort(key=lambda item: item[1])

    levels = []
    for label, value, color in raw_levels:
        if levels and abs(value - levels[-1][1]) < 0.005:
            prior_label, prior_value, prior_color = levels[-1]
            levels[-1] = (f"{prior_label} / {label}", prior_value, prior_color)
        else:
            levels.append((label, value, color))

    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=[value for _, value, _ in levels],
        y=[0] * len(levels),
        mode="lines",
        line={"color": "#7A4300", "width": 5},
        hoverinfo="skip",
        showlegend=False,
    ))
    label_positions = ["top center", "bottom center"]
    for index, (label, value, color) in enumerate(levels):
        figure.add_trace(go.Scatter(
            x=[value],
            y=[0],
            mode="markers+text",
            marker={"size": 16, "color": color},
            text=[f"{label}<br>${value:,.2f}"],
            textposition=label_positions[index % 2],
            textfont={"size": 10, "color": color, "family": "Courier New"},
            cliponaxis=False,
            hovertemplate=f"{label}: ${value:,.2f}<extra></extra>",
            showlegend=False,
        ))
    values = [value for _, value, _ in levels]
    padding = max((max(values) - min(values)) * 0.14, current * 0.01, 0.25)
    figure.update_layout(
        height=170,
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        font={"color": "#FF8C00", "family": "Courier New"},
        margin={"l": 22, "r": 22, "t": 25, "b": 25},
        xaxis={
            "showticklabels": False,
            "showgrid": False,
            "zeroline": False,
            "range": [min(values) - padding, max(values) + padding],
        },
        yaxis={"visible": False, "range": [-0.30, 0.30]},
        hovermode="closest",
        showlegend=False,
    )
    return figure


def _safe_widget_key(value):
    return "".join(character for character in str(value) if character.isalnum()) or "SYMBOL"


def _financial_metric(container, label, display_value, numeric_value, detail=""):
    if numeric_value > 0:
        tone = "bb-positive"
    elif numeric_value < 0:
        tone = "bb-negative"
    else:
        tone = "bb-neutral"
    detail_html = (
        f'<div class="bb-number-detail {tone}">{html.escape(str(detail))}</div>'
        if detail else ""
    )
    container.markdown(
        (
            '<div class="bb-number-card">'
            f'<div class="bb-number-label">{html.escape(str(label))}</div>'
            f'<div class="bb-number-value {tone}">{html.escape(str(display_value))}</div>'
            f'{detail_html}'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def _quote_tone(value, positive_is_green=True):
    if value is None or value == 0:
        return "bb-neutral"
    if not positive_is_green:
        return "bb-neutral"
    return "bb-positive" if value > 0 else "bb-negative"


def _render_quote_strip(quote_data=None):
    quote_data = quote_data or {}
    last = quote_data.get("last")
    change = quote_data.get("change")
    change_pct = quote_data.get("change_pct")
    cells = [
        (
            "E*TRADE Last",
            "—" if last is None else f"${last:,.2f}",
            "bb-neutral" if last is None else _quote_tone(change),
            "CLICK GET E*TRADE PRICE" if last is None else (
                "—" if change is None else f"{change:+.2f}"
            ),
            _quote_tone(change),
        ),
        (
            "Bid",
            "—" if quote_data.get("bid") is None else f"${quote_data['bid']:,.2f}",
            "bb-neutral" if quote_data.get("bid") is None else "bb-positive",
            "",
            "bb-neutral",
        ),
        (
            "Ask",
            "—" if quote_data.get("ask") is None else f"${quote_data['ask']:,.2f}",
            "bb-neutral" if quote_data.get("ask") is None else "bb-positive",
            "",
            "bb-neutral",
        ),
        (
            "Change",
            "—" if change_pct is None else f"{change_pct:+.2f}%",
            _quote_tone(change_pct),
            "",
            "bb-neutral",
        ),
    ]
    body = []
    for label, value, value_tone, detail, detail_tone in cells:
        body.append(
            '<div class="bb-quote-cell">'
            f'<div class="bb-quote-label">{html.escape(label)}</div>'
            f'<div class="bb-quote-value {value_tone}">{html.escape(value)}</div>'
            f'<div class="bb-quote-detail {detail_tone}">{html.escape(detail)}</div>'
            '</div>'
        )
    st.markdown(
        '<div class="bb-quote-strip">' + "".join(body) + '</div>',
        unsafe_allow_html=True,
    )


def _financial_dataframe(frame, columns=None):
    selected = columns or list(frame.select_dtypes(include="number").columns)
    selected = [column for column in selected if column in frame.columns]
    if not selected:
        return frame

    def color_value(value):
        if pd.isna(value) or value == 0:
            return "color: #FF8C00"
        return "color: #00D084" if value > 0 else "color: #FF3B30"

    return frame.style.map(color_value, subset=selected)


def render_order_simulator():
    st.subheader("Triggers – OCO Order Simulator")
    st.caption(
        "BUY LIMIT → when filled, activates a take-profit LIMIT and STOP-MARKET exit. "
        "SIMULATION ONLY // NO ORDER CAN BE TRANSMITTED."
    )

    client = _etrade_client()
    account = _account_picker("orders_account") if client else None
    symbol_col, fetch_col = st.columns(
        [4.6, 1.4],
        vertical_alignment="bottom",
    )
    with symbol_col:
        symbol = st.text_input(
            "Stock or ETF Symbol",
            value="XLF",
            key="order_symbol",
        ).strip().upper()
    with fetch_col:
        fetch_quote = st.button(
            "GET E*TRADE PRICE",
            type="primary",
            width="stretch",
            disabled=client is None,
            key="fetch_order_quote",
        )

    if fetch_quote and client:
        try:
            st.session_state["etrade_quote"] = quote_summary(client.get_quote(symbol))
            st.session_state["etrade_quote_symbol"] = symbol
            _touch_etrade_session()
        except ETradeError as exc:
            st.error(str(exc))

    quote_data = (
        st.session_state.get("etrade_quote")
        if st.session_state.get("etrade_quote_symbol") == symbol
        else None
    )
    # Keep the quote strip visible at all times. Before a live quote is loaded it
    # shows placeholders instead of disappearing, so the Bloomberg-style quote
    # panel is always present in the Orders workspace.
    _render_quote_strip(quote_data)
    if quote_data:
        current = float(quote_data["last"])
        if quote_data.get("description"):
            st.caption(quote_data["description"])
    else:
        current = float(st.number_input(
            "Current / Reference Price",
            min_value=0.01,
            value=60.00,
            step=0.01,
            format="%.2f",
            help="Manual reference until E*TRADE is connected and a quote is loaded.",
        ))

    slider_min = max(0.01, math.floor(current * 0.50 * 100) / 100)
    slider_max = max(slider_min + 1.0, math.ceil(current * 1.50 * 100) / 100)
    symbol_key = _safe_widget_key(symbol)

    st.markdown("**1 // PRIMARY ORDER**")
    entry = st.slider(
        "Buy Limit Price",
        slider_min,
        slider_max,
        value=min(slider_max, max(slider_min, round(current * 0.995, 2))),
        step=0.01,
        format="$%.2f",
        key=f"entry_{symbol_key}_{current:.2f}",
    )

    st.markdown("**2 // OCO EXITS ACTIVATED AFTER FILL**")
    stop_col, target_col = st.columns(2)
    with stop_col:
        stop = st.slider(
            "Stop-Market Trigger",
            slider_min,
            slider_max,
            value=min(slider_max, max(slider_min, round(current * 0.97, 2))),
            step=0.01,
            format="$%.2f",
            key=f"stop_{symbol_key}_{current:.2f}",
        )
    with target_col:
        target = st.slider(
            "Take-Profit Limit",
            slider_min,
            slider_max,
            value=min(slider_max, max(slider_min, round(current * 1.05, 2))),
            step=0.01,
            format="$%.2f",
            key=f"target_{symbol_key}_{current:.2f}",
        )

    put_col, call_col, rr_col = st.columns(3)
    with put_col:
        put_wall = float(st.number_input(
            "Put Wall",
            min_value=0.0,
            value=round(current * 0.95, 2),
            step=0.01,
            format="%.2f",
        ))
    with call_col:
        call_wall = float(st.number_input(
            "Call Wall",
            min_value=0.0,
            value=round(current * 1.05, 2),
            step=0.01,
            format="%.2f",
        ))
    with rr_col:
        target_rr = float(st.number_input(
            "Target Reward : Risk",
            min_value=0.25,
            value=2.0,
            step=0.25,
            format="%.2f",
        ))

    portfolio_total = 0.0
    if client and account:
        try:
            portfolio_total, _, _ = _balance_snapshot(_account_balance(client, account))
        except ETradeError as exc:
            st.warning(f"Portfolio value unavailable: {exc}")
    sizing_total, sizing_risk, sizing_quantity = st.columns([1.55, 1.25, 1.0])
    with sizing_total:
        portfolio_total = float(st.number_input(
            "Portfolio Total Value",
            min_value=0.0,
            value=portfolio_total,
            step=1000.0,
            format="%.2f",
            help="Uses E*TRADE totalAccountValue when connected; it remains editable for simulation.",
        ))
    with sizing_risk:
        risk_choice = st.selectbox(
            "Portfolio Risk Per Trade",
            ["0.5%", "1.0%", "Custom"],
            key="risk_choice",
        )
        if risk_choice == "Custom":
            risk_percent = float(st.number_input(
                "Custom Risk %",
                min_value=0.05,
                max_value=10.0,
                value=0.75,
                step=0.05,
            ))
        else:
            risk_percent = float(risk_choice.rstrip("%"))

    recommended_quantity = risk_sized_quantity(portfolio_total, risk_percent, entry, stop)
    with sizing_quantity:
        quantity = int(st.number_input(
            "Quantity",
            min_value=1,
            value=max(1, recommended_quantity),
            step=1,
            help="Defaults to whole-share sizing for the selected 0.5% or 1% portfolio risk.",
        ))

    try:
        metrics = calculate_trade_metrics(entry, stop, target, quantity, target_rr)
    except ValueError as exc:
        st.error(str(exc))
        st.plotly_chart(
            _price_ladder(current, entry, stop, target, put_wall, call_wall),
            width="stretch",
            config={"displayModeBar": False},
        )
        return

    ratio_gap = metrics.reward_risk - target_rr
    risk_budget = portfolio_total * risk_percent / 100.0
    position_pct = metrics.position_value / portfolio_total * 100 if portfolio_total else 0.0
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Reward : Risk", f"{metrics.reward_risk:.2f}:1", f"{ratio_gap:+.2f}x vs {target_rr:.2f}:1")
    _financial_metric(
        c2,
        "Planned Loss",
        f"-${metrics.max_loss:,.2f}",
        -metrics.max_loss,
        f"-${metrics.risk_per_share:,.2f} / share",
    )
    _financial_metric(
        c3,
        "Planned Profit",
        f"+${metrics.max_profit:,.2f}",
        metrics.max_profit,
        f"+${metrics.reward_per_share:,.2f} / share",
    )
    c4.metric("Risk-Sized Qty", f"{recommended_quantity:,}", f"${risk_budget:,.2f} budget")
    c5.metric("Position Value", f"${metrics.position_value:,.2f}", f"{position_pct:.2f}% of portfolio")

    if ratio_gap >= 0:
        st.success(f"MEETS {target_rr:.2f}:1 TARGET BY {ratio_gap:.2f}x")
    else:
        shortfall = -metrics.reward_gap_per_share
        st.warning(
            f"SHORT BY {abs(ratio_gap):.2f}x / ${shortfall:,.2f} REWARD PER SHARE. "
            f"EXACT {target_rr:.2f}:1 TARGET = ${metrics.target_for_target_rr:,.2f}; "
            f"OR EXACT STOP = ${metrics.stop_for_target_rr:,.2f}."
        )

    st.plotly_chart(
        _price_ladder(current, entry, stop, target, put_wall, call_wall),
        width="stretch",
        config={"displayModeBar": False},
    )
    w1, w2, w3, w4 = st.columns(4)
    _financial_metric(w1, "Entry vs Put Wall", f"${entry - put_wall:+,.2f}", entry - put_wall)
    _financial_metric(w2, "Stop vs Put Wall", f"${stop - put_wall:+,.2f}", stop - put_wall)
    _financial_metric(w3, "Target vs Call Wall", f"${target - call_wall:+,.2f}", target - call_wall)
    _financial_metric(w4, "Current vs Entry", f"${current - entry:+,.2f}", current - entry)

    st.subheader("Simulated E*TRADE Ticket")
    ticket = pd.DataFrame([
        {"Sequence": "1", "Action": "BUY", "Qty": quantity, "Symbol": symbol, "Price Type": "LIMIT", "Price": entry, "Condition": "PRIMARY"},
        {"Sequence": "2A", "Action": "SELL", "Qty": quantity, "Symbol": symbol, "Price Type": "LIMIT", "Price": target, "Condition": "OCO AFTER FILL"},
        {"Sequence": "2B", "Action": "SELL", "Qty": quantity, "Symbol": symbol, "Price Type": "STOP", "Price": stop, "Condition": "OCO AFTER FILL"},
    ])
    st.dataframe(_financial_dataframe(ticket), hide_index=True, width="stretch")
    st.caption(
        "If 2A or 2B executes, the other exit is canceled. A stop-market order can fill below "
        "its trigger during a gap. SIMULATION ONLY // NOTHING IS SENT TO E*TRADE."
    )


def _holdings_chart(frame, value_column, title, allocation=False):
    chart_data = frame[["Symbol", value_column]].copy()
    chart_data[value_column] = pd.to_numeric(chart_data[value_column], errors="coerce")
    chart_data = chart_data.dropna().sort_values(value_column)
    if chart_data.empty:
        return None
    if len(chart_data) > 12:
        if allocation:
            chart_data = chart_data.nlargest(12, value_column).sort_values(value_column)
        else:
            leaders = chart_data.nlargest(6, value_column)
            laggards = chart_data.nsmallest(6, value_column)
            chart_data = pd.concat([laggards, leaders]).drop_duplicates().sort_values(value_column)

    values = chart_data[value_column]
    colors = (
        ["#0068FF"] * len(chart_data)
        if allocation
        else ["#00D084" if value >= 0 else "#FF3B30" for value in values]
    )
    prefix = "$" if value_column != "% Portfolio" else ""
    suffix = "%" if value_column == "% Portfolio" else ""
    figure = go.Figure(go.Bar(
        x=values,
        y=chart_data["Symbol"],
        orientation="h",
        marker_color=colors,
        text=[f"{prefix}{value:,.2f}{suffix}" for value in values],
        textposition="outside",
        hovertemplate=(
            "%{y}<br>" + title + f": {prefix}%{{x:,.2f}}{suffix}<extra></extra>"
        ),
    ))
    figure.update_layout(
        title=title,
        height=max(310, min(560, 42 * len(chart_data) + 100)),
        paper_bgcolor="#000000",
        plot_bgcolor="#000000",
        font={"color": "#FF8C00", "family": "Courier New"},
        margin={"l": 20, "r": 80, "t": 55, "b": 30},
        xaxis={"gridcolor": "#3A2100", "zerolinecolor": "#FF8C00"},
        yaxis={"gridcolor": "#000000"},
        showlegend=False,
    )
    return figure


def _holdings_total_row(frame, label):
    total_market = pd.to_numeric(frame["Market Value"], errors="coerce").sum()
    total_cost = pd.to_numeric(frame["Total Cost"], errors="coerce").sum()
    total_gain = pd.to_numeric(frame["Gain/Loss"], errors="coerce").sum()
    day_gain = pd.to_numeric(frame["Day Gain/Loss"], errors="coerce").sum()
    prior_value = total_market - day_gain
    return {
        "Symbol": label,
        "Type": f"{len(frame):,} POSITIONS",
        "Quantity": None,
        "Last": None,
        "Price Paid": None,
        "Market Value": total_market,
        "Total Cost": total_cost,
        "Day Gain/Loss": day_gain,
        "Day Gain/Loss %": day_gain / prior_value * 100 if prior_value else 0.0,
        "Gain/Loss": total_gain,
        "Gain/Loss %": total_gain / total_cost * 100 if total_cost else 0.0,
        "% Portfolio": pd.to_numeric(frame["% Portfolio"], errors="coerce").sum(),
        "52W High": None,
        "52W Low": None,
        "% From 52W High": None,
    }


def render_etrade_holdings():
    st.subheader("E*TRADE Holdings")
    client = _etrade_client()
    if not client:
        st.info("Connect E*TRADE at the top of the page to retrieve holdings.")
        return
    if not st.session_state.get("etrade_accounts"):
        try:
            _refresh_accounts(client)
        except ETradeError as exc:
            st.error(str(exc))
            return
    account = _account_picker("holdings_account")
    if not account:
        st.info("No brokerage accounts were returned.")
        return
    account_key = str(account.get("accountIdKey", ""))
    if st.button("REFRESH HOLDINGS + BALANCE", type="primary", key="refresh_holdings"):
        try:
            _account_balance(client, account, refresh=True)
            st.session_state.setdefault("etrade_holdings", {})[account_key] = (
                client.get_portfolio(account_key)
            )
            _touch_etrade_session()
        except ETradeError as exc:
            st.error(str(exc))

    balance = st.session_state.get("etrade_balances", {}).get(account_key)
    holdings = st.session_state.get("etrade_holdings", {}).get(account_key)
    total = cash = market_value = 0.0
    if balance:
        total, cash, market_value = _balance_snapshot(balance)
        b1, b2, b3 = st.columns(3)
        b1.metric("Total Account Value", f"${total:,.2f}")
        b2.metric("Cash Available", f"${cash:,.2f}")
        b3.metric("Net Market Value", f"${market_value:,.2f}")
    if holdings is None:
        st.info("Select REFRESH HOLDINGS + BALANCE to load current positions.")
        return

    normalized = pd.DataFrame(normalize_position(position) for position in holdings)
    if normalized.empty:
        st.info("No positions were returned for this account.")
        return

    numeric_columns = [
        "Quantity", "Last", "Price Paid", "Market Value", "Total Cost",
        "Day Gain/Loss", "Day Gain/Loss %", "Gain/Loss", "Gain/Loss %",
        "% Portfolio", "52W High", "52W Low", "% From 52W High",
    ]
    for column in numeric_columns:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    total_market = normalized["Market Value"].sum()
    total_cost = normalized["Total Cost"].sum()
    total_gain = normalized["Gain/Loss"].sum()
    day_gain = normalized["Day Gain/Loss"].sum()
    total_return = total_gain / total_cost * 100 if total_cost else 0.0
    if not market_value:
        market_value = total_market
    if not total:
        total = total_market + cash
    cash_pct = cash / total * 100 if total else 0.0

    pnl_values = normalized["Gain/Loss"].dropna()
    winners = int((pnl_values > 0).sum())
    losers = int((pnl_values < 0).sum())
    decided_positions = winners + losers
    win_rate = winners / decided_positions * 100 if decided_positions else 0.0

    allocation = normalized[["Symbol", "Market Value"]].copy()
    allocation = allocation[allocation["Market Value"] > 0].sort_values("Market Value", ascending=False)
    largest_symbol = str(allocation.iloc[0]["Symbol"]) if not allocation.empty else "—"
    largest_pct = allocation.iloc[0]["Market Value"] / total * 100 if total and not allocation.empty else 0.0
    top_three_pct = allocation.head(3)["Market Value"].sum() / total * 100 if total else 0.0

    st.subheader("Portfolio Trader Analysis")
    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Total Cost", f"${total_cost:,.2f}")
    _financial_metric(a2, "Unrealized P&L", f"${total_gain:+,.2f}", total_gain)
    _financial_metric(a3, "Total Return", f"{total_return:+.2f}%", total_return)
    _financial_metric(a4, "Day P&L", f"${day_gain:+,.2f}", day_gain)
    a5.metric("Cash Allocation", f"{cash_pct:.2f}%", f"${cash:,.2f}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Positions", f"{len(normalized):,}")
    c2.metric("Winners / Losers", f"{winners} / {losers}")
    c3.metric("Win Rate", f"{win_rate:.1f}%")
    c4.metric("Largest Position", largest_symbol, f"{largest_pct:.2f}% of account")
    c5.metric("Top 3 Concentration", f"{top_three_pct:.2f}%")

    if largest_pct >= 25:
        st.warning(
            f"CONCENTRATION FLAG // {largest_symbol} is {largest_pct:.2f}% of total account value."
        )
    elif largest_pct >= 15:
        st.info(
            f"CONCENTRATION WATCH // {largest_symbol} is {largest_pct:.2f}% of total account value."
        )

    chart_frame = normalized.copy()
    if total:
        chart_frame["% Portfolio"] = chart_frame["Market Value"] / total * 100
    allocation_chart = _holdings_chart(
        chart_frame,
        "% Portfolio",
        "POSITION ALLOCATION",
        allocation=True,
    )
    pnl_chart = _holdings_chart(
        chart_frame,
        "Gain/Loss",
        "UNREALIZED P&L LEADERS / LAGGARDS",
    )
    chart_left, chart_right = st.columns(2)
    if allocation_chart is not None:
        chart_left.plotly_chart(allocation_chart, width="stretch", key="holdings_allocation_chart")
    if pnl_chart is not None:
        chart_right.plotly_chart(pnl_chart, width="stretch", key="holdings_pnl_chart")

    st.subheader("Position Controls")
    f1, f2, f3, f4 = st.columns([1.4, 1.4, 1.2, 1.1])
    with f1:
        symbol_search = st.text_input(
            "Find Symbol",
            placeholder="e.g. SGOL",
            key="holdings_symbol_search",
        ).strip().upper()
    with f2:
        selected_types = st.multiselect(
            "Security Type",
            sorted(normalized["Type"].dropna().astype(str).unique()),
            key="holdings_type_filter",
        )
    with f3:
        pnl_filter = st.selectbox(
            "P&L Filter",
            ["All Positions", "Winners", "Losers", "Breakeven"],
            key="holdings_pnl_filter",
        )
    with f4:
        sort_direction = st.selectbox(
            "Direction",
            ["Descending", "Ascending"],
            key="holdings_sort_direction",
        )

    sort_options = [
        "Market Value", "Gain/Loss", "Gain/Loss %", "Day Gain/Loss",
        "Day Gain/Loss %", "% Portfolio", "% From 52W High", "Last",
        "Price Paid", "Quantity", "Symbol", "Type",
    ]
    sort_by = st.selectbox(
        "Sort Positions By",
        sort_options,
        key="holdings_sort_column",
    )

    filtered = normalized.copy()
    if symbol_search:
        filtered = filtered[
            filtered["Symbol"].astype(str).str.upper().str.contains(symbol_search, regex=False)
        ]
    if selected_types:
        filtered = filtered[filtered["Type"].isin(selected_types)]
    if pnl_filter == "Winners":
        filtered = filtered[filtered["Gain/Loss"] > 0]
    elif pnl_filter == "Losers":
        filtered = filtered[filtered["Gain/Loss"] < 0]
    elif pnl_filter == "Breakeven":
        filtered = filtered[filtered["Gain/Loss"].fillna(0) == 0]

    filtered = filtered.sort_values(
        sort_by,
        ascending=sort_direction == "Ascending",
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)
    is_filtered = bool(symbol_search or selected_types or pnl_filter != "All Positions")
    total_label = "FILTERED TOTAL" if is_filtered else "PORTFOLIO TOTAL"
    total_row = pd.DataFrame([_holdings_total_row(filtered, total_label)])
    display_frame = pd.concat([filtered, total_row], ignore_index=True)

    st.caption(
        "Use the controls above for persistent sorting; column headers can also be clicked. "
        "The total row is calculated from the positions currently shown."
    )
    st.dataframe(
        _financial_dataframe(display_frame),
        hide_index=True,
        width="stretch",
        height=min(900, max(260, 36 * len(display_frame) + 42)),
        column_config={
            "Last": st.column_config.NumberColumn(format="$%.2f"),
            "Price Paid": st.column_config.NumberColumn(format="$%.2f"),
            "Market Value": st.column_config.NumberColumn(format="$%.2f"),
            "Total Cost": st.column_config.NumberColumn(format="$%.2f"),
            "Day Gain/Loss": st.column_config.NumberColumn(format="$%.2f"),
            "Day Gain/Loss %": st.column_config.NumberColumn(format="%.2f%%"),
            "Gain/Loss": st.column_config.NumberColumn(format="$%.2f"),
            "Gain/Loss %": st.column_config.NumberColumn(format="%.2f%%"),
            "% Portfolio": st.column_config.NumberColumn(format="%.2f%%"),
            "52W High": st.column_config.NumberColumn(format="$%.2f"),
            "52W Low": st.column_config.NumberColumn(format="$%.2f"),
            "% From 52W High": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

    st.markdown("**TOTALS // POSITIONS SHOWN**")
    t1, t2, t3, t4 = st.columns(4)
    visible_totals = _holdings_total_row(filtered, total_label)
    t1.metric("Market Value", f"${visible_totals['Market Value']:,.2f}")
    t2.metric("Total Cost", f"${visible_totals['Total Cost']:,.2f}")
    _financial_metric(
        t3,
        "Unrealized P&L",
        f"${visible_totals['Gain/Loss']:+,.2f}",
        visible_totals["Gain/Loss"],
    )
    _financial_metric(
        t4,
        "Total Return",
        f"{visible_totals['Gain/Loss %']:+.2f}%",
        visible_totals["Gain/Loss %"],
    )

    st.download_button(
        "DOWNLOAD HOLDINGS CSV",
        display_frame.to_csv(index=False).encode("utf-8"),
        file_name="etrade_holdings.csv",
        mime="text/csv",
    )


st.title("MuniX Screen — by Raj")
st.caption(
    "Bloomberg-style municipal analytics, E*TRADE holdings, and a live "
    "Triggers–OCO risk simulator."
)

render_etrade_connection()

orders_tab, holdings_tab, muni_screeners_tab = st.tabs(
    ["ORDERS", "HOLDINGS", "MUNI SCREENERS"]
)

with orders_tab:
    orders_left, orders_center, orders_right = st.columns([1.4, 5.2, 1.4])
    with orders_center:
        render_order_simulator()

with holdings_tab:
    render_etrade_holdings()

with muni_screeners_tab:
    load_col, refresh_col, _ = st.columns([1.5, 1.4, 3.1])
    with load_col:
        load_muni_clicked = st.button(
            "LOAD MUNI SCREENERS",
            type="primary",
            key="load_muni_data",
            width="stretch",
        )
    with refresh_col:
        refresh_muni_clicked = st.button(
            "REFRESH MUNI DATA",
            key="refresh_muni_data",
            width="stretch",
        )

    if refresh_muni_clicked:
        st.session_state.pop(MUNI_SESSION_KEY, None)
        st.session_state.pop(MUNI_SESSION_AT_KEY, None)

    muni_bundle = None
    should_load_munis = (
        load_muni_clicked
        or refresh_muni_clicked
        or _session_muni_cache_is_valid()
    )

    if should_load_munis:
        try:
            muni_bundle, used_session_cache = load_muni_universe()
        except Exception as exc:
            st.error(f"Municipal data load failed: {exc}")
    else:
        st.info(
            "Municipal data is paused. Select LOAD MUNI SCREENERS when you are ready."
        )

    if muni_bundle is not None:
        df, source_rows, etf_status, as_of = muni_bundle

        if used_session_cache:
            age_minutes = int(
                (time.time() - st.session_state[MUNI_SESSION_AT_KEY]) / 60
            )
            st.caption(
                f"DATA ENGINE // SESSION CACHE READY • {len(df):,} CUSIPs "
                f"• loaded {age_minutes} min ago"
            )

        tab1, tab2, tab3 = st.tabs(
            [
                "MUNI SCREENER",
                "TAX EXEMPT STATUS FOR NIST",
                "STATE INCOME TAX // MUNI vs UST",
            ]
        )

        with tab1:
            render_muni_screener(
                df,
                source_rows,
                etf_status,
                as_of,
            )

        with tab2:
            render_nist_comparison(df)

        with tab3:
            render_state_income_tax_comparison(df)

        st.caption(
            "Important: ETF holdings do not cover every outstanding U.S. municipal bond. "
            "Source prices/yields are not guaranteed executable broker quotes. Verify call schedules, "
            "tax treatment, AMT treatment, ratings, Treasury quotes, and official terms before trading."
        )
