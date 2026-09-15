"""Compatibility shim for the production Risk Sizing renderer.

streamlit_app.py still imports risk_sizing_ui_v7. Keep that public import stable
while routing the actual UI to the seamless v9 implementation.
"""

from src.risk_sizing_ui_v9 import render_risk_sizing

__all__ = ["render_risk_sizing"]
