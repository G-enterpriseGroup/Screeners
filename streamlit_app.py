import html
import re
import time
from contextlib import redirect_stdout
from datetime import datetime

import pandas as pd
import streamlit as st

from src.muni_data import load_all_ishares_munis, screen_munis
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


st.set_page_config(
    page_title="Municipal Bond Screeners — by Raj",
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
        color:var(--bb-orange) !important;
        font-family:"Courier New",monospace !important;
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
        background:var(--bb-orange) !important;
        color:#000 !important;
        border:1px solid var(--bb-orange) !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        text-transform:uppercase;
    }

    .stButton > button *,
    .stDownloadButton > button *,
    .stLinkButton > a * {
        color:#000 !important;
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
        color:var(--bb-orange) !important;
        border-color:#7a4300 !important;
    }

    [data-testid="stDataFrame"] [role="gridcell"] * {
        color:var(--bb-orange) !important;
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
            use_container_width=True,
            key=f"{prefix}_load_treasury",
        )

    with c2:
        if st.button(
            "Clear Treasury Cache",
            use_container_width=True,
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
            use_container_width=True,
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
        use_container_width=True,
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
        use_container_width=True,
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
            use_container_width=True,
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
            use_container_width=True,
            hide_index=True,
        )
        render_copy_cusips(
            candidates["CUSIP"].tolist(),
            "COPY 50 CANDIDATE CUSIPs",
        )


st.title("Municipal Bond Screeners — by Raj")
st.caption(
    "Bloomberg-style municipal analytics using free public ETF holdings data, "
    "NIST tax-equivalent analysis, and all-state muni-vs-Treasury comparisons."
)

_, top_right = st.columns([5, 1])
with top_right:
    if st.button(
        "Refresh Muni Data",
        key="refresh_muni_data",
        use_container_width=True,
    ):
        st.session_state.pop(MUNI_SESSION_KEY, None)
        st.session_state.pop(MUNI_SESSION_AT_KEY, None)
        st.rerun()


try:
    (df, source_rows, etf_status, as_of), used_session_cache = load_muni_universe()
except Exception:
    st.stop()


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
