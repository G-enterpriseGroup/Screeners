"""Compatibility shim for the active Risk Sizing tab.

The terminal still imports v3, but the current implementation lives in v4.
v4 places the true E*TRADE cash balance beside Tactical Room and excludes
margin/buying-power fields from the displayed cash number.
"""

from src.risk_sizing_ui_v4 import render_risk_sizing

__all__ = ["render_risk_sizing"]
