import html
import re
import time
from contextlib import redirect_stdout
from datetime import date, datetime

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
    "Wyoming",
}

MUNI_SESSION_KEY = "_muni_data_bundle"
MUNI_SESSION_AT_KEY = "_muni_data_loaded_at"
MUNI_TTL_SECONDS = 12 * 60 * 60

st.set_page_config(
    page_title="Municipal Bond Screeners",
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

    h1 {
        color:var(--bb-orange) !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        letter-spacing:.04em;
        text-transform:uppercase;
    }

    h2, h3 {
        background:var(--bb-orange) !important;
        color:var(--bb-black) !important;
        padding:.32rem .55rem !important;
        border-radius:0 !important;
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
        border-radius:0 !important;
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
        border-radius:0 !important;
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
        border-radius:0 !important;
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
    .stDownloadButton > button {
        background:var(--bb-orange) !important;
        color:#000 !important;
        border:1px solid var(--bb-orange) !important;
        border-radius:0 !important;
        font-family:"Courier New",monospace !important;
        font-weight:900 !important;
        text-transform:uppercase;
    }

    .stButton > button *,
    .stDownloadButton > button * {
        color:#000 !important;
    }

    [data-testid="stExpander"],
    [data-testid="stStatusWidget"] {
        border:1px solid var(--bb-orange) !important;
        border-radius:0 !important;
        background:#000 !important;
    }

    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary * {
        color:var(--bb-orange) !important;
        font-weight:900 !important;
    }

    [data-testid="stDataFrame"] {
        border:1px solid var(--bb-orange) !important;
        border-radius:0 !important;
    }

    [data-testid="stDataFrame"] [role="columnheader"] {
        background:var(--bb-orange) !important;
        color:#000 !important;
        font-weight:900 !important;
        border-color:#000 !important;
    }

    [data-testid="stDataFrame"] [role="columnheader"] * {
        color:#000 !important;
        font-weight:900 !important;
    }

    [data-testid="stDataFrame"] [role="gridcell"] {
        background:#000 !important;
        color:var(--bb-orange) !important;
        border-color:#332000 !important;
    }

    [data-testid="stDataFrame"] [role="gridcell"] * {
        color:var(--bb-orange) !important;
    }

    [data-testid="stProgress"] > div > div > div > div {
        background:var(--bb-orange) !important;
    }

    button[data-baseweb="tab"] {
        border:1px solid var(--bb-orange) !important;
        border-radius:0 !important;
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
        border-radius:0 !important;
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
    """
    The municipal loader is intentionally NOT decorated with st.cache_data.

    The first load can update the live ProcessingConsole. Once complete, the
    full data bundle is stored in st.session_state for 12 hours. Widget reruns
    reuse that bundle and never replay Streamlit elements from a cached function.
    """
    if _session_muni_cache_is_valid():
        return st.session_state[MUNI_SESSION_KEY], True

    with st.status("DATA ENGINE // INITIALIZING", expanded=True) as loader_status:
        progress_bar = st.progress(
            0,
            text="Loading municipal ETF holdings...",
        )
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
            f"{failed_etfs} ETF source(s) failed to load. "
            "Open Source Status below to see which ones."
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
            help="Exact CUSIP lookup overrides the other filters.",
            key="screen_cusip",
        ).strip().upper()

    available_states = sorted(
        x for x in df["State"].dropna().astype(str).unique()
        if x and x != "Unknown"
    )

    with row1[1]:
        states = st.multiselect(
            "States",
            options=available_states,
            placeholder="All states",
            help="Select one or multiple states. Leave blank for all states.",
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
            placeholder="No minimum",
            key="screen_coupon_min",
        )

    with row2[1]:
        coupon_max = st.number_input(
            "Coupon max (%)",
            min_value=0.0,
            max_value=20.0,
            value=None,
            step=0.01,
            placeholder="No maximum",
            key="screen_coupon_max",
        )

    with row2[2]:
        ytw_min = st.number_input(
            "YTW min (%)",
            min_value=-10.0,
            max_value=50.0,
            value=None,
            step=0.01,
            placeholder="No minimum",
            key="screen_ytw_min",
        )

    with row2[3]:
        ytw_max = st.number_input(
            "YTW max (%)",
            min_value=-10.0,
            max_value=50.0,
            value=None,
            step=0.01,
            placeholder="No maximum",
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
        use_maturity_from = st.checkbox(
            "Minimum maturity",
            key="screen_use_mfrom",
        )
        maturity_from = (
            st.date_input("Maturity from", key="screen_maturity_from")
            if use_maturity_from
            else None
        )

    with row3[1]:
        use_maturity_to = st.checkbox(
            "Maximum maturity",
            key="screen_use_mto",
        )
        maturity_to = (
            st.date_input("Maturity to", key="screen_maturity_to")
            if use_maturity_to
            else None
        )

    with row3[2]:
        investment_grade_only = st.checkbox(
            "Investment grade",
            help=(
                "Uses investment-grade-only ETF/index eligibility as evidence. "
                "It does not invent an exact agency rating."
            ),
            key="screen_ig",
        )

    with row3[3]:
        amt_exempt_only = st.checkbox(
            "AMT-exempt evidence",
            help="Requires evidence from a source ETF/index identified as AMT-free.",
            key="screen_amt",
        )

    with row3[4]:
        non_callable_only = st.checkbox(
            "Non-callable proxy",
            help="Proxy only. Verify the official call schedule before purchase.",
            key="screen_noncall",
        )

    row4 = st.columns([1.25, 1, 1, 2.75])

    with row4[0]:
        no_state_income_tax_only = st.checkbox(
            "No State Individual Income Tax",
            help=(
                "Alaska, Florida, Nevada, New Hampshire, South Dakota, Tennessee, "
                "Texas, and Wyoming. Washington is excluded because it taxes "
                "certain capital gains."
            ),
            key="screen_no_state_tax",
        )

    with row4[1]:
        new_issue_only = st.checkbox(
            "New issues",
            key="screen_new",
        )

    with row4[2]:
        new_issue_days = st.selectbox(
            "New issue window",
            [30, 60, 90, 180],
            index=1,
            disabled=not new_issue_only,
            key="screen_new_days",
        )

    if no_state_income_tax_only:
        st.caption(
            "No-income-tax states: "
            + ", ".join(sorted(NO_INDIVIDUAL_INCOME_TAX_STATES))
            + ". Washington is intentionally excluded because it taxes certain capital gains."
        )

    screen_df = (
        df
        if cusip or not no_state_income_tax_only
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
        investment_grade_only=investment_grade_only,
        amt_exempt_only=amt_exempt_only,
        non_callable_only=non_callable_only,
        new_issue_only=new_issue_only,
        new_issue_days=new_issue_days,
        as_of=as_of,
        sort_by=sort_by,
    )

    st.divider()

    c1, c2 = st.columns([1, 4])
    with c1:
        st.metric("Matches", f"{len(results):,}")

    with c2:
        if cusip and results.empty:
            st.info(
                f"CUSIP {cusip} was not found in the currently combined ETF universe. "
                "That does not mean the municipal bond does not exist."
            )

    display_columns = [
        "CUSIP",
        "Name",
        "State",
        "Price",
        "Coupon (%)",
        "YTM (%)",
        "Yield to Worst (%)",
        "Yield to Call (%)",
        "Maturity",
        "Rating",
        "Investment Grade",
        "AMT Exempt",
        "Source ETFs",
        "Source Count",
        "Purchase Face ($)",
        "Est. Principal Cost ($)",
        "Annual Coupon Income ($)",
        "NonCallableProxy",
    ]
    display_columns = [c for c in display_columns if c in results.columns]

    st.dataframe(
        results[display_columns],
        use_container_width=True,
        height=650,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn(format="%.3f"),
            "Coupon (%)": st.column_config.NumberColumn(format="%.3f%%"),
            "YTM (%)": st.column_config.NumberColumn(format="%.3f%%"),
            "Yield to Worst (%)": st.column_config.NumberColumn(format="%.3f%%"),
            "Yield to Call (%)": st.column_config.NumberColumn(format="%.3f%%"),
            "Est. Principal Cost ($)": st.column_config.NumberColumn(format="$%.2f"),
            "Annual Coupon Income ($)": st.column_config.NumberColumn(format="$%.2f"),
        },
    )

    csv = results.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download filtered CSV",
        data=csv,
        file_name="muni_screen_results.csv",
        mime="text/csv",
        type="primary",
        key="screen_download",
    )


def render_tax_equivalent(df):
    st.subheader("Muni vs U.S. Treasury // After-Tax Yield")

    st.markdown(
        """
        <div class="terminal-note">
        ENTER CLIENT FEDERAL MARGINAL TAX RATE + TARGET MATURITY.<br>
        THE TOOL FINDS THE NEAREST MUNI, THEN MATCHES THE CLOSEST TREASURY MATURITY.<br>
        MUNI = YIELD-TO-WORST. TREASURY = WSJ ASKED YIELD WHEN WSJ QUOTES ARE AVAILABLE.
        </div>
        """,
        unsafe_allow_html=True,
    )

    available_states = sorted(
        x for x in df["State"].dropna().astype(str).unique()
        if x and x != "Unknown"
    )

    in1, in2, in3, in4 = st.columns([1.0, 1.2, 1.7, 1.1])

    with in1:
        federal_bracket = st.number_input(
            "Client federal tax bracket (%)",
            min_value=0.0,
            max_value=60.0,
            value=None,
            step=0.1,
            placeholder="e.g. 35",
            key="tey_tax_rate",
        )

    with in2:
        target_maturity = st.date_input(
            "Client target maturity",
            value=date.today().replace(year=date.today().year + 5),
            key="tey_target_date",
        )

    with in3:
        tey_states = st.multiselect(
            "Muni state(s)",
            options=available_states,
            placeholder="All states",
            help="Optional. Leave blank to search every state.",
            key="tey_states",
        )

    with in4:
        tey_ig_only = st.checkbox(
            "Investment grade only",
            value=False,
            key="tey_ig_only",
        )

    b1, b2, _ = st.columns([1.3, 1.2, 3.5])

    with b1:
        load_clicked = st.button(
            "Load / Refresh Treasurys",
            type="primary",
            use_container_width=True,
            key="tey_load_treasury",
        )

    with b2:
        if st.button(
            "Clear Treasury Cache",
            use_container_width=True,
            key="tey_clear_treasury",
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
                p.progress(
                    80,
                    text="Normalizing Treasury bills, notes and bonds...",
                )
                st.session_state["treasury_quotes"] = quotes
                st.session_state["treasury_meta"] = meta
                p.progress(
                    100,
                    text=f"READY • {len(quotes):,} Treasury rows",
                )
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

    treasury_df = st.session_state.get("treasury_quotes")
    treasury_meta = st.session_state.get("treasury_meta")

    if treasury_df is None or treasury_meta is None:
        st.info(
            "Click **LOAD / REFRESH TREASURYS** once. The Treasury dataset is cached "
            "for 30 minutes so the rest of the app stays fast."
        )
        return

    source_name = treasury_meta.get("source", "Unknown")
    source_date = treasury_meta.get("as_of", "")
    source_note = treasury_meta.get("note", "")

    if treasury_meta.get("fallback"):
        st.warning(
            "WSJ individual Treasury rows were not available to the server, so the official "
            "U.S. Treasury daily par yield curve is being used as a fallback. The screen "
            "labels this so it is not mistaken for an exact WSJ issue quote."
        )

    st.caption(
        f"Treasury source: {source_name} | Data as of: {source_date}. {source_note}"
    )

    muni_pool = df.copy()
    if tey_ig_only:
        muni_pool = muni_pool[
            muni_pool["Investment Grade"].eq("Yes")
        ].copy()

    candidates = nearest_muni_candidates(
        muni_pool,
        target_maturity=target_maturity,
        states=tey_states or None,
        limit=25,
    )

    if candidates.empty:
        st.warning("No usable municipal bonds were found near that maturity.")
        return

    candidate_labels = []
    for _, row in candidates.iterrows():
        maturity_text = pd.Timestamp(row["Maturity"]).strftime("%Y-%m-%d")
        ytw = float(row["Yield to Worst (%)"])
        gap = int(row["Maturity Gap Days"])
        candidate_labels.append(
            f"{row['CUSIP']} | {maturity_text} | {row['State']} | "
            f"YTW {ytw:.3f}% | gap {gap}d"
        )

    prior_choice = st.session_state.get("tey_muni_choice")
    if prior_choice not in candidate_labels:
        st.session_state["tey_muni_choice"] = candidate_labels[0]

    chosen_label = st.selectbox(
        "Closest muni candidates — choose the bond to compare",
        options=candidate_labels,
        key="tey_muni_choice",
    )

    chosen_index = candidate_labels.index(chosen_label)
    muni = candidates.iloc[chosen_index]

    treasury = nearest_treasury(
        treasury_df,
        target_maturity=pd.Timestamp(muni["Maturity"]),
    )

    if treasury is None:
        st.warning(
            "No usable Treasury quote was available for the selected maturity."
        )
        return

    treasury_gap = abs(
        (
            pd.Timestamp(treasury["Maturity"])
            - pd.Timestamp(muni["Maturity"])
        ).days
    )

    mcol, tcol = st.columns(2)

    with mcol:
        st.markdown("### Municipal Match")
        muni_view = pd.DataFrame(
            [{
                "CUSIP": muni["CUSIP"],
                "Name": muni["Name"],
                "State": muni["State"],
                "Maturity": pd.Timestamp(muni["Maturity"]).date(),
                "Client Date Gap": int(muni["Maturity Gap Days"]),
                "Price": muni.get("Price"),
                "Coupon (%)": muni.get("Coupon (%)"),
                "YTW (%)": muni.get("Yield to Worst (%)"),
                "Rating": muni.get("Rating"),
                "Source ETFs": muni.get("Source ETFs"),
            }]
        )
        st.dataframe(
            muni_view,
            use_container_width=True,
            hide_index=True,
        )

    with tcol:
        st.markdown("### Treasury Match")
        treasury_view = pd.DataFrame(
            [{
                "Type": treasury.get("Security Type"),
                "Maturity": pd.Timestamp(treasury["Maturity"]).date(),
                "Muni Date Gap": treasury_gap,
                "Coupon (%)": treasury.get("Coupon (%)"),
                "Bid": treasury.get("Bid"),
                "Asked": treasury.get("Asked"),
                "Asked Yield (%)": treasury.get("Asked Yield (%)"),
                "Source": treasury.get("Source"),
            }]
        )
        st.dataframe(
            treasury_view,
            use_container_width=True,
            hide_index=True,
        )

    if federal_bracket is None:
        st.info(
            "Enter the client's **federal marginal tax bracket** above to calculate "
            "the tax-equivalent and after-tax winner."
        )
        return

    comparison = tax_equivalent_comparison(
        muni_yield=float(muni["Yield to Worst (%)"]),
        treasury_yield=float(treasury["Asked Yield (%)"]),
        federal_tax_rate=float(federal_bracket) / 100.0,
    )

    st.subheader("Tax-Equivalent Comparison")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Muni YTW / After-Tax",
        f"{comparison['Muni After-Tax Yield (%)']:.3f}%",
    )
    c2.metric(
        "Muni Tax-Equivalent Yield",
        f"{comparison['Muni Tax-Equivalent Yield (%)']:.3f}%",
    )
    c3.metric(
        "Treasury Gross Yield",
        f"{float(treasury['Asked Yield (%)']):.3f}%",
    )
    c4.metric(
        "Treasury After-Tax Yield",
        f"{comparison['Treasury After-Tax Yield (%)']:.3f}%",
    )

    winner = comparison["Winner"]
    spread = comparison["After-Tax Spread (bps)"]

    if winner == "MUNICIPAL":
        winner_text = (
            f"MUNICIPAL WINS // +{abs(spread):.1f} BPS AFTER FEDERAL TAX"
        )
    elif winner == "TREASURY":
        winner_text = (
            f"TREASURY WINS // +{abs(spread):.1f} BPS AFTER FEDERAL TAX"
        )
    else:
        winner_text = "TIE // SAME AFTER-TAX YIELD"

    st.markdown(
        f'<div class="winner-box">{html.escape(winner_text)}</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="terminal-note">
        BREAK-EVEN TAXABLE YIELD = {comparison['Muni Tax-Equivalent Yield (%)']:.3f}%<br>
        CLIENT FEDERAL RATE = {float(federal_bracket):.1f}%<br>
        FORMULA: MUNI TEY = MUNI YTW ÷ (1 − TAX RATE)<br>
        TREASURY AFTER-TAX = TREASURY YIELD × (1 − TAX RATE)
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        "This first TEY version uses federal tax only. U.S. Treasury interest is generally "
        "exempt from state/local income tax, while state tax treatment of municipal interest "
        "depends on the client's residence and issuing state. AMT and other client-specific "
        "tax items are not included here."
    )

    with st.expander("Show 25 closest municipal candidates"):
        nearby_cols = [
            "CUSIP",
            "Name",
            "State",
            "Maturity",
            "Maturity Gap Days",
            "Price",
            "Coupon (%)",
            "Yield to Worst (%)",
            "Rating",
            "Source ETFs",
        ]
        nearby_cols = [
            c for c in nearby_cols if c in candidates.columns
        ]
        st.dataframe(
            candidates[nearby_cols],
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Show Treasury dataset"):
        st.dataframe(
            treasury_df.sort_values("Maturity"),
            use_container_width=True,
            hide_index=True,
        )


st.title("Municipal Bond Screeners")
st.caption(
    "Bloomberg-style municipal analytics using free public ETF holdings data, "
    "plus a tax-equivalent muni-vs-Treasury comparison."
)

top_left, top_right = st.columns([5, 1])
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
    (df, source_rows, etf_status, as_of), used_session_cache = (
        load_muni_universe()
    )
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

tab1, tab2 = st.tabs(
    [
        "MUNI SCREENER",
        "TAX EQUIVALENT // MUNI vs UST",
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
    render_tax_equivalent(df)

st.caption(
    "Important: ETF holdings do not cover every outstanding U.S. municipal bond. "
    "Source prices/yields are not guaranteed executable broker quotes. Verify call schedules, "
    "tax treatment, AMT treatment, ratings, Treasury quotes, and official terms before trading."
)
