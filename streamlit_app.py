import pandas as pd
import streamlit as st

from src.muni_data import load_all_ishares_munis, screen_munis

NO_INDIVIDUAL_INCOME_TAX_STATES = {
    "Alaska", "Florida", "Nevada", "New Hampshire",
    "South Dakota", "Tennessee", "Texas", "Wyoming",
}

ORANGE = "#FF8C00"
BLACK = "#000000"
DARK = "#0A0A0A"

st.set_page_config(
    page_title="Municipal Bond Screener",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    f"""
    <style>
    :root {{
        --bb-orange: {ORANGE};
        --bb-black: {BLACK};
        --bb-dark: {DARK};
    }}

    html, body, [data-testid="stAppViewContainer"],
    [data-testid="stMain"], [data-testid="stMainBlockContainer"] {{
        background: var(--bb-black) !important;
    }}

    [data-testid="stHeader"] {{
        background: var(--bb-black) !important;
    }}

    .stApp {{
        background: var(--bb-black) !important;
        color: var(--bb-orange) !important;
    }}

    h1 {{
        color: var(--bb-orange) !important;
        font-family: "Courier New", monospace !important;
        font-weight: 800 !important;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }}

    h2, h3 {{
        background: var(--bb-orange) !important;
        color: var(--bb-black) !important;
        padding: 0.32rem 0.55rem !important;
        border-radius: 0 !important;
        font-family: "Courier New", monospace !important;
        font-weight: 900 !important;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }}

    p, label, .stCaption, [data-testid="stMarkdownContainer"] {{
        color: var(--bb-orange) !important;
    }}

    [data-testid="stMetric"] {{
        background: var(--bb-black) !important;
        border: 1px solid var(--bb-orange) !important;
        border-radius: 0 !important;
        padding: 0.55rem 0.7rem !important;
    }}

    [data-testid="stMetricLabel"] *,
    [data-testid="stMetricValue"] * {{
        color: var(--bb-orange) !important;
        font-family: "Courier New", monospace !important;
    }}

    div[data-baseweb="input"] > div,
    div[data-baseweb="select"] > div,
    div[data-baseweb="base-input"] {{
        background: var(--bb-black) !important;
        border-color: var(--bb-orange) !important;
        color: var(--bb-orange) !important;
        border-radius: 0 !important;
    }}

    input, textarea {{
        color: var(--bb-orange) !important;
        -webkit-text-fill-color: var(--bb-orange) !important;
        caret-color: var(--bb-orange) !important;
        font-family: "Courier New", monospace !important;
    }}

    input::placeholder, textarea::placeholder {{
        color: #A85C00 !important;
        opacity: 1 !important;
    }}

    div[data-baseweb="select"] span {{
        color: var(--bb-orange) !important;
    }}

    div[data-baseweb="tag"] {{
        background: var(--bb-orange) !important;
        border-radius: 0 !important;
    }}

    div[data-baseweb="tag"] span {{
        color: var(--bb-black) !important;
        font-weight: 800 !important;
    }}

    [data-testid="stCheckbox"] label *,
    [data-testid="stRadio"] label * {{
        color: var(--bb-orange) !important;
    }}

    .stButton > button,
    .stDownloadButton > button {{
        background: var(--bb-orange) !important;
        color: var(--bb-black) !important;
        border: 1px solid var(--bb-orange) !important;
        border-radius: 0 !important;
        font-family: "Courier New", monospace !important;
        font-weight: 900 !important;
        text-transform: uppercase;
    }}

    .stButton > button *,
    .stDownloadButton > button * {{
        color: var(--bb-black) !important;
    }}

    .stButton > button:hover,
    .stDownloadButton > button:hover {{
        background: #FFA733 !important;
        color: var(--bb-black) !important;
        border-color: #FFA733 !important;
    }}

    [data-testid="stExpander"] {{
        border: 1px solid var(--bb-orange) !important;
        border-radius: 0 !important;
        background: var(--bb-black) !important;
    }}

    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary * {{
        color: var(--bb-orange) !important;
        font-weight: 800 !important;
    }}

    [data-testid="stDataFrame"] {{
        border: 1px solid var(--bb-orange) !important;
        border-radius: 0 !important;
    }}

    [data-testid="stDataFrame"] [role="columnheader"] {{
        background: var(--bb-orange) !important;
        color: var(--bb-black) !important;
        font-weight: 900 !important;
        border-color: var(--bb-black) !important;
    }}

    [data-testid="stDataFrame"] [role="columnheader"] * {{
        color: var(--bb-black) !important;
        font-weight: 900 !important;
    }}

    [data-testid="stDataFrame"] [role="gridcell"] {{
        background: var(--bb-black) !important;
        color: var(--bb-orange) !important;
        border-color: #332000 !important;
    }}

    [data-testid="stDataFrame"] [role="gridcell"] * {{
        color: var(--bb-orange) !important;
    }}

    hr {{
        border-color: var(--bb-orange) !important;
    }}

    [data-testid="stAlert"] {{
        background: var(--bb-dark) !important;
        border: 1px solid var(--bb-orange) !important;
        border-radius: 0 !important;
    }}

    [data-testid="stAlert"] * {{
        color: var(--bb-orange) !important;
    }}

    ::selection {{
        background: var(--bb-orange);
        color: var(--bb-black);
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Municipal Bond Screener")
st.caption(
    "Free public-data screener built from official municipal-bond ETF holdings. "
    "No API keys and no paid feed."
)

@st.cache_data(ttl="12h", show_spinner=False)
def load_data():
    return load_all_ishares_munis()

with st.spinner("Loading and combining municipal bond ETF holdings..."):
    df, source_rows, etf_status, as_of = load_data()

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
    )

with row1[2]:
    purchase_face = st.number_input(
        "Purchase face ($)",
        min_value=1000,
        max_value=10_000_000,
        value=5000,
        step=1000,
    )

with row1[3]:
    price_min = st.number_input(
        "Price min",
        min_value=0.0,
        max_value=200.0,
        value=None,
        step=0.01,
        placeholder="No minimum",
    )

with row1[4]:
    price_max = st.number_input(
        "Price max",
        min_value=0.0,
        max_value=200.0,
        value=None,
        step=0.01,
        placeholder="No maximum",
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
    )

with row2[1]:
    coupon_max = st.number_input(
        "Coupon max (%)",
        min_value=0.0,
        max_value=20.0,
        value=None,
        step=0.01,
        placeholder="No maximum",
    )

with row2[2]:
    ytw_min = st.number_input(
        "YTW min (%)",
        min_value=-10.0,
        max_value=50.0,
        value=None,
        step=0.01,
        placeholder="No minimum",
    )

with row2[3]:
    ytw_max = st.number_input(
        "YTW max (%)",
        min_value=-10.0,
        max_value=50.0,
        value=None,
        step=0.01,
        placeholder="No maximum",
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
    )

row3 = st.columns(5)

with row3[0]:
    use_maturity_from = st.checkbox("Minimum maturity")
    maturity_from = st.date_input("Maturity from", key="maturity_from") if use_maturity_from else None

with row3[1]:
    use_maturity_to = st.checkbox("Maximum maturity")
    maturity_to = st.date_input("Maturity to", key="maturity_to") if use_maturity_to else None

with row3[2]:
    investment_grade_only = st.checkbox(
        "Investment grade",
        help=(
            "Uses investment-grade-only ETF/index eligibility as evidence. "
            "It does not invent an exact agency rating."
        ),
    )

with row3[3]:
    amt_exempt_only = st.checkbox(
        "AMT-exempt evidence",
        help="Requires evidence from a source ETF/index identified as AMT-free.",
    )

with row3[4]:
    non_callable_only = st.checkbox(
        "Non-callable proxy",
        help="Proxy only. Verify the official call schedule before purchase.",
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
    )

with row4[1]:
    new_issue_only = st.checkbox("New issues")

with row4[2]:
    new_issue_days = st.selectbox(
        "New issue window",
        [30, 60, 90, 180],
        index=1,
        disabled=not new_issue_only,
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
    "CUSIP", "Name", "State", "Price", "Coupon (%)", "YTM (%)",
    "Yield to Worst (%)", "Yield to Call (%)", "Maturity", "Rating",
    "Investment Grade", "AMT Exempt", "Source ETFs", "Source Count",
    "Purchase Face ($)", "Est. Principal Cost ($)",
    "Annual Coupon Income ($)", "NonCallableProxy",
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
)

st.caption(
    "Important: ETF holdings do not cover every outstanding U.S. municipal bond. "
    "Prices and yields are source/vendor values, not guaranteed executable broker quotes. "
    "Non-callable, tax status, AMT treatment, and exact agency ratings should be verified "
    "against official bond documents or a licensed security-master source before trading."
)
