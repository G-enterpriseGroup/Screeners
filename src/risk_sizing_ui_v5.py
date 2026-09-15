"""Compatibility entrypoint for the active Risk Sizing UI.

Risk Sizing v6 restores the complete always-visible SIZE THE NEXT TRADE workflow
while preserving ASK defaults, 5% stop logic, ticker autocomplete/history,
unused-risk percentage, true-cash handling, compact Bloomberg styling, and the
cached sector/industry context. Keep this v5 import path because streamlit_app
already imports it.
"""

from src.risk_sizing_ui_v6 import render_risk_sizing

__all__ = ["render_risk_sizing"]
