"""Gamma-exposure workspace for Raj's Terminal.

This first pass establishes the dedicated GEX tab and Bloomberg-style layout.
The analytics/data engine can be layered into this module without touching the
main terminal navigation framework.
"""

from __future__ import annotations

import streamlit as st


@st.fragment
def render_gex() -> None:
    st.markdown(
        """
        <style>
        .gex-header {
            background:#fb8b1e;
            color:#000 !important;
            border:1px solid #fb8b1e;
            padding:.48rem .72rem;
            margin:.42rem 0 .35rem 0;
            font-family:"Courier New",monospace;
            font-size:1.45rem;
            line-height:1.05;
            font-weight:900;
            letter-spacing:.035em;
            text-transform:uppercase;
        }
        .gex-sub {
            color:#c87816 !important;
            font-family:"Courier New",monospace;
            font-size:.82rem;
            font-weight:800;
            line-height:1.25;
            margin:0 0 .7rem 0;
        }
        .gex-ready {
            border:1px solid #fb8b1e;
            background:#020202;
            color:#4af6c3 !important;
            font-family:"Courier New",monospace;
            padding:.7rem .8rem;
            font-size:.86rem;
            font-weight:800;
        }
        </style>
        <div class="gex-header">GEX</div>
        <div class="gex-sub">GAMMA EXPOSURE // DEALER POSITIONING // OPTIONS STRUCTURE</div>
        <div class="gex-ready">GEX WORKSPACE READY // analytics engine will live here.</div>
        """,
        unsafe_allow_html=True,
    )
