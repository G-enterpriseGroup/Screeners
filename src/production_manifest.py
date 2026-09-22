"""Single source of truth for Raj's Terminal production feature ownership.

READ THIS BEFORE EDITING FEATURE CODE.

This module is intentionally data-only. It does not import Streamlit or any
feature module, so the architecture validator can read it safely.

Rules:
- `entry` = file the terminal currently imports/calls for that feature.
- `owner` = primary file to edit for normal feature UI/behavior changes.
- `support` = files that may be edited only when the requested change belongs
  to that supporting responsibility.
- Historical version files not listed here should be treated as legacy unless
  a production owner explicitly imports them.
"""

from __future__ import annotations


FEATURE_ROUTES = {
    "risk_sizing": {
        "entry": "src/risk_sizing_ui_v7.py",
        "owner": "src/risk_sizing_ui_v10.py",
        "support": [
            "src/risk_sizing_ui_v9.py",
            "src/risk_sizing_ui_v2.py",
            "src/risk_sizing.py",
            "src/ticker_autocomplete.py",
        ],
    },
    "schwab_risk_sizing": {
        "entry": "src/schwab_risk_sizing_ui.py",
        "owner": "src/schwab_risk_sizing_ui.py",
        "support": ["src/risk_sizing.py"],
    },
    "gex": {
        "entry": "src/gex_workspace_v2.py",
        "owner": "src/gex_ui_v3.py",
        "support": ["src/gex_ui.py"],
    },
    "etrade_connection": {
        "entry": "src/etrade_connection_ui_v2.py",
        "owner": "src/etrade_connection_ui_v2.py",
        "support": ["src/etrade_client.py", "src/session_persistence.py"],
    },
    "holdings": {
        "entry": "src/holdings_snapshot_mode.py",
        "owner": "src/holdings_snapshot_mode.py",
        "support": [
            "src/stockanalysis_portfolio_v5.py",
            "src/stockanalysis_cache.py",
        ],
    },
    "option_book": {
        "entry": "src/option_book_ui.py",
        "owner": "src/option_book_ui.py",
        "support": [
            "src/option_book.py",
            "src/etrade_client.py",
            "src/ticker_autocomplete.py",
        ],
    },
    "navigation": {
        "entry": "src/tab_bar_v4.py",
        "owner": "src/tab_bar_v4.py",
        "support": ["src/components/terminal_tabs_v3/index.html"],
    },
    "bull_debit_spread": {
        "entry": "src/bull_debit_ui.py",
        "owner": "src/bull_debit_ui.py",
        "support": ["src/bull_debit_spread.py"],
    },
    "shared_theme": {
        "entry": "src/theme.py",
        "owner": "src/theme.py",
        "support": ["src/layout_guardrails.py", "src/visual_safety.py"],
    },
}


# Files that are shared infrastructure. Do not use them as the first place to
# implement a feature-specific visual/UI request.
SHARED_INFRASTRUCTURE = {
    "streamlit_app.py",
    "src/terminal_core.py",
    "src/etrade_data_cache.py",
}


__all__ = ["FEATURE_ROUTES", "SHARED_INFRASTRUCTURE"]
