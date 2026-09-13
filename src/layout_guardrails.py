"""Terminal-wide overlap and layout guardrails for Raj's Terminal.

This module only changes presentation. It is intentionally conservative: it
keeps Bloomberg styling, restores Streamlit/Material icon fonts when broad
terminal typography rules would otherwise turn icons into literal text, and
forces dense rows/cards/controls to wrap inside their own containers instead of
overlapping neighboring elements.
"""

from __future__ import annotations

import streamlit as st


_LAYOUT_GUARDRAIL_CSS = r"""
<style>
/* ---------- universal geometry ---------- */
*, *::before, *::after {
    box-sizing:border-box !important;
}

[data-testid="stMainBlockContainer"],
[data-testid="stVerticalBlock"],
[data-testid="stHorizontalBlock"],
[data-testid="column"],
[data-testid="stElementContainer"],
[data-testid="stMarkdownContainer"] {
    min-width:0 !important;
    max-width:100% !important;
}

[data-testid="stHorizontalBlock"] {
    align-items:stretch !important;
}

[data-testid="stHorizontalBlock"] > div {
    min-width:0 !important;
    max-width:100% !important;
}

/* Text may wrap, but should never invade the next control/card. */
h1, h2, h3, h4, h5, h6,
p, label,
[data-testid="stCaptionContainer"],
.stCaption,
.terminal-note,
[data-testid="stAlert"] {
    max-width:100% !important;
    overflow-wrap:anywhere !important;
    word-break:normal !important;
}

/* Restore icon fonts if broad terminal typography styles touch icon spans.
   Without this, Material icon names such as `arrow_right` render as literal
   overlapping text. */
span.material-symbols-rounded,
span.material-symbols-outlined,
span[class*="material-symbols"],
[data-testid*="Icon"] span,
span[data-testid*="Icon"] {
    font-family:"Material Symbols Rounded","Material Symbols Outlined","Material Icons" !important;
    font-weight:normal !important;
    font-style:normal !important;
    letter-spacing:normal !important;
    text-transform:none !important;
    white-space:nowrap !important;
    overflow:visible !important;
    flex:0 0 auto !important;
}

/* ---------- expanders ---------- */
[data-testid="stExpander"],
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary {
    width:100% !important;
    max-width:100% !important;
    min-width:0 !important;
}

[data-testid="stExpander"] summary {
    align-items:center !important;
    gap:.45rem !important;
    overflow:visible !important;
}

[data-testid="stExpander"] summary > * {
    min-width:0 !important;
}

[data-testid="stExpander"] summary p {
    margin:0 !important;
    line-height:1.18 !important;
    white-space:normal !important;
    overflow-wrap:anywhere !important;
}

[data-testid="stExpander"] summary svg,
[data-testid="stExpander"] summary [data-testid*="Icon"] {
    flex:0 0 auto !important;
}

/* ---------- native tabs ---------- */
[role="tablist"] {
    max-width:100% !important;
    min-width:0 !important;
    overflow-x:auto !important;
    overflow-y:hidden !important;
    flex-wrap:nowrap !important;
    scrollbar-width:thin;
}

[role="tab"] {
    flex:0 0 auto !important;
    min-width:max-content !important;
    max-width:none !important;
    white-space:nowrap !important;
}

/* ---------- buttons and input controls ---------- */
.stButton,
.stDownloadButton,
.stLinkButton,
.stFormSubmitButton,
[data-testid="stButton"],
[data-testid="stNumberInput"],
[data-testid="stTextInput"],
[data-testid="stSelectbox"],
[data-testid="stMultiSelect"] {
    min-width:0 !important;
    max-width:100% !important;
}

.stButton > button,
.stDownloadButton > button,
.stLinkButton > a,
.stFormSubmitButton > button,
[data-testid="stFormSubmitButton"] button {
    min-width:0 !important;
    max-width:100% !important;
    white-space:normal !important;
    overflow-wrap:anywhere !important;
    line-height:1.15 !important;
}

[data-baseweb="input"],
[data-baseweb="base-input"],
[data-baseweb="select"],
[data-baseweb="popover"] {
    min-width:0 !important;
    max-width:100% !important;
}

/* Select/popover text is allowed to truncate rather than cover the arrow or
   the next column. The full value is still available in the opened menu. */
[data-baseweb="select"] > div,
[data-baseweb="select"] > div > div {
    min-width:0 !important;
    max-width:100% !important;
}
[data-baseweb="select"] span:not([class*="material-symbols"]) {
    min-width:0 !important;
    overflow:hidden !important;
    text-overflow:ellipsis !important;
}

[data-baseweb="popover"] {
    width:min(680px, calc(100vw - 32px)) !important;
}

[data-testid="stNumberInput"] input,
[data-testid="stTextInput"] input {
    min-width:0 !important;
    width:100% !important;
}

/* ---------- cards / metrics ---------- */
.bb-number-card,
.rs-card,
[data-testid="stMetric"] {
    min-width:0 !important;
    max-width:100% !important;
}

.bb-number-value,
.bb-number-detail,
.rs-card-value,
.rs-card-detail,
[data-testid="stMetricValue"],
[data-testid="stMetricDelta"] {
    min-width:0 !important;
    max-width:100% !important;
    white-space:normal !important;
    overflow-wrap:anywhere !important;
}

.rs-card-head {
    min-width:0 !important;
}

.rs-card-head > :first-child {
    min-width:0 !important;
    overflow-wrap:anywhere !important;
}

.rs-help {
    flex:0 0 auto !important;
}

.rs-help-tip {
    width:min(300px, calc(100vw - 48px)) !important;
    max-width:calc(100vw - 48px) !important;
    overflow-wrap:anywhere !important;
    white-space:normal !important;
}

/* Quote cells should wrap if the browser is narrower than expected. */
.bb-quote-cell,
.bb-quote-value,
.bb-quote-detail {
    min-width:0 !important;
    max-width:100% !important;
}

.bb-quote-value,
.bb-quote-detail {
    white-space:normal !important;
    overflow-wrap:anywhere !important;
}

/* ---------- tables / charts / components ---------- */
[data-testid="stDataFrame"],
[data-testid="stDataEditor"],
[data-testid="stTable"],
[data-testid="stPlotlyChart"],
[data-testid="stPlotlyChart"] > div,
iframe {
    width:100% !important;
    max-width:100% !important;
    min-width:0 !important;
}

/* Do not force data-grid child icons into Courier New. */
[data-testid="stDataFrame"] [role="columnheader"] [data-testid*="Icon"],
[data-testid="stDataEditor"] [role="columnheader"] [data-testid*="Icon"] {
    font-family:inherit !important;
    flex:0 0 auto !important;
}

/* Dense inline HTML tables may scroll horizontally rather than overlap. */
.raj-ticket-shell,
.exposure-terminal-panel,
.exposure-terminal-body {
    max-width:100% !important;
}

/* ---------- responsive safety ---------- */
@media (max-width:1100px) {
    [data-testid="stHorizontalBlock"] {
        gap:.55rem !important;
    }

    .bb-number-value,
    .rs-card-value,
    [data-testid="stMetricValue"] {
        font-size:clamp(.95rem, 1.8vw, 1.35rem) !important;
    }
}

@media (max-width:760px) {
    h1 { font-size:clamp(1.55rem, 8vw, 2.45rem) !important; }
    h2, h3 { line-height:1.12 !important; }

    [data-testid="stExpander"] summary {
        gap:.30rem !important;
    }
}
</style>
"""


def install_layout_guardrails() -> None:
    """Install the anti-overlap stylesheet once per Streamlit Python process."""
    if getattr(st, "_raj_layout_guardrails_installed", False):
        return
    st.markdown(_LAYOUT_GUARDRAIL_CSS, unsafe_allow_html=True)
    st._raj_layout_guardrails_installed = True
