"""Compatibility shim for the production Risk Sizing renderer.

streamlit_app.py keeps importing risk_sizing_ui_v7. Route that stable import to
the fail-safe v10 interaction layer.
"""

from src.risk_sizing_ui_v10 import render_risk_sizing

__all__ = ["render_risk_sizing"]
