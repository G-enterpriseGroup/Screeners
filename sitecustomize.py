"""Small production UI patches for Raj's Terminal.

This file is loaded automatically by Python on app startup.  The patches here
are deliberately narrow: they only affect the E*TRADE OAuth completion panel.
OAuth/session behavior is unchanged.
"""

from __future__ import annotations

import streamlit as st


# Keep stable references even when Streamlit reruns the app in the same process.
if not hasattr(st, "_raj_original_subheader"):
    st._raj_original_subheader = st.subheader
if not hasattr(st, "_raj_original_text_input"):
    st._raj_original_text_input = st.text_input
if not hasattr(st, "_raj_original_button"):
    st._raj_original_button = st.button

_ORIGINAL_SUBHEADER = st._raj_original_subheader
_ORIGINAL_TEXT_INPUT = st._raj_original_text_input
_ORIGINAL_BUTTON = st._raj_original_button

# Replaced every render when the verifier input is reached.  The following
# VERIFY button is then rendered into this exact adjacent column.
_ETRADE_VERIFY_BUTTON_SLOT = None


def _compact_subheader(body, *args, **kwargs):
    text = str(body or "").strip()
    if text != "Complete E*TRADE Authorization":
        return _ORIGINAL_SUBHEADER(body, *args, **kwargs)

    st.markdown(
        """
        <style>
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker){
            padding:.28rem .42rem .30rem!important;
            border-color:#fb8b1e!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stVerticalBlock"]{
            gap:.14rem!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stHorizontalBlock"]{
            gap:.38rem!important;
            align-items:end!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stCaptionContainer"]{
            display:none!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker)
        [data-testid="stTextInput"] label{
            display:none!important;
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
            padding:.16rem .46rem!important;
            font-size:.70rem!important;
            line-height:1!important;
            margin:0!important;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:has(.etrade-auth-compact-marker) p{
            margin:0!important;
            line-height:1!important;
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
            padding:.22rem .42rem;
            margin:0 0 .12rem 0;
            font:900 .76rem/1 "Courier New",monospace;
            letter-spacing:.03em;
            text-transform:uppercase;
        }
        .etrade-auth-compact-marker span{color:#000!important;}
        </style>
        <div class="etrade-auth-compact-marker">
          <span>COMPLETE E*TRADE AUTHORIZATION</span>
          <span>OAUTH</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return None


def _oauth_text_input(label, *args, **kwargs):
    """Render the verifier field and reserve an adjacent slot for its button."""
    global _ETRADE_VERIFY_BUTTON_SLOT

    if kwargs.get("key") != "etrade_verifier":
        return _ORIGINAL_TEXT_INPUT(label, *args, **kwargs)

    input_col, button_col = st.columns(
        [4.7, 1.55],
        gap="small",
        vertical_alignment="bottom",
    )
    _ETRADE_VERIFY_BUTTON_SLOT = button_col

    local_kwargs = dict(kwargs)
    local_kwargs["label_visibility"] = "collapsed"
    with input_col:
        return _ORIGINAL_TEXT_INPUT(label, *args, **local_kwargs)


def _oauth_button(label, *args, **kwargs):
    """Move VERIFY AND CONNECT into the reserved slot beside the verifier."""
    global _ETRADE_VERIFY_BUTTON_SLOT

    if kwargs.get("key") != "etrade_verify" or _ETRADE_VERIFY_BUTTON_SLOT is None:
        return _ORIGINAL_BUTTON(label, *args, **kwargs)

    slot = _ETRADE_VERIFY_BUTTON_SLOT
    _ETRADE_VERIFY_BUTTON_SLOT = None
    local_kwargs = dict(kwargs)
    local_kwargs["width"] = "stretch"
    with slot:
        return _ORIGINAL_BUTTON(label, *args, **local_kwargs)


st.subheader = _compact_subheader
st.text_input = _oauth_text_input
st.button = _oauth_button
