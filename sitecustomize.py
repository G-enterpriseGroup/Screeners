"""Small production UI patch for Raj's Terminal.

Loaded automatically by Python on app startup. It only intercepts the one
Streamlit subheader used by the E*TRADE OAuth completion panel and replaces it
with a compact marker/header plus tightly scoped CSS. No OAuth/session logic is
changed.
"""

from __future__ import annotations

import streamlit as st


if not hasattr(st, "_raj_original_subheader"):
    st._raj_original_subheader = st.subheader

_ORIGINAL_SUBHEADER = st._raj_original_subheader


def _compact_subheader(body, *args, **kwargs):
    text = str(body or "").strip()
    if text != "Complete E*TRADE Authorization":
        return _ORIGINAL_SUBHEADER(body, *args, **kwargs)

    st.markdown(
        """
        <style>
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker){
            padding:.32rem .50rem .38rem!important;
            border-color:#fb8b1e!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stVerticalBlock"]{
            gap:.22rem!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stHorizontalBlock"]{
            gap:.48rem!important;
            align-items:end!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stCaptionContainer"]{
            display:none!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stTextInput"] label{
            font-size:.68rem!important;
            margin:0 0 .08rem!important;
            line-height:1!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stTextInput"] div[data-baseweb="input"]>div,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stTextInput"] input{
            min-height:34px!important;
            height:34px!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        .stButton>button,
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        .stLinkButton>a{
            min-height:34px!important;
            height:34px!important;
            padding:.18rem .50rem!important;
            font-size:.72rem!important;
            line-height:1!important;
            margin:0!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        p{
            margin:0!important;
            line-height:1.05!important;
        }
        .etrade-auth-compact-marker{
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:.5rem;
            width:100%;
            box-sizing:border-box;
            background:#fb8b1e;
            color:#000!important;
            padding:.24rem .46rem;
            margin:0 0 .18rem 0;
            font:900 .78rem/1 "Courier New",monospace;
            letter-spacing:.035em;
            text-transform:uppercase;
        }
        .etrade-auth-compact-marker span{
            color:#000!important;
        }
        </style>
        <div class="etrade-auth-compact-marker">
          <span>COMPLETE E*TRADE AUTHORIZATION</span>
          <span>OAUTH</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return None


st.subheader = _compact_subheader
