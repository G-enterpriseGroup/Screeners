"""Risk-sizing UI v6: Crown range notes + portfolio-growth behavior.

Extends v5 without changing its ASK-entry / editable 5%-stop behavior.
The notes section now makes the guide ranges explicit and distinguishes fixed
user-selected percentages from the dollar amounts that automatically scale as
the portfolio changes.
"""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd
import streamlit as st

import src.risk_sizing_ui_v4 as _v4
import src.risk_sizing_ui_v5 as _v5


_ORIGINAL_CROWN_REFERENCE = _v4._render_crown_reference


def _render_crown_reference_with_ranges() -> None:
    """Render the existing Crown notes plus a clear ranges/scaling reference."""
    _ORIGINAL_CROWN_REFERENCE()

    st.markdown("**CROWN GUIDE RANGES // DEFAULTS // WHAT AUTO-SCALES**")
    current_sleeve = float(st.session_state.get("risk_tactical_sleeve_pct", 15.0) or 15.0)
    current_risk = float(st.session_state.get("risk_full_position_pct", 1.5) or 1.5)

    ranges = pd.DataFrame(
        [
            {
                "Setting": "Tactical Sleeve %",
                "Guide Range": "10% - 20%",
                "Terminal Default": "15%",
                "Current": f"{current_sleeve:.1f}%",
                "Changes Automatically?": "NO",
                "What Does Scale": "Tactical sleeve $",
            },
            {
                "Setting": "Risk % of Sleeve for 1.00",
                "Guide Range": "1% - 2%",
                "Terminal Default": "1.5%",
                "Current": f"{current_risk:.1f}%",
                "Changes Automatically?": "NO",
                "What Does Scale": "1.00 dollar-risk budget",
            },
            {
                "Setting": "Crown Size Multiplier",
                "Guide Range": "1.00 / 0.75 / 0.50 / 0.25",
                "Terminal Default": "1.00",
                "Current": f"{float(st.session_state.get('risk_size_multiplier', 1.0) or 1.0):.2f}",
                "Changes Automatically?": "NO",
                "What Does Scale": "Selected trade risk $",
            },
        ]
    )
    st.dataframe(
        ranges,
        hide_index=True,
        width="stretch",
    )

    st.markdown(
        "**IMPORTANT // the percentages do not automatically drift as your portfolio grows.** "
        "They stay at the values you selected until you change them. What changes automatically "
        "is the dollar amount produced by those percentages."
    )
    st.code(
        "Target Tactical Sleeve $ = Investable Assets × Tactical Sleeve %\n"
        "1.00 Risk Budget $ = Target Tactical Sleeve $ × Risk % of Sleeve\n"
        "Selected Trade Risk $ = 1.00 Risk Budget $ × Crown Size Multiplier",
        language=None,
    )
    st.caption(
        "Example: if the account grows while you leave 15% and 1.5% unchanged, "
        "the 15% and 1.5% settings remain the same but the tactical-sleeve dollars "
        "and maximum dollar risk increase automatically with the account value."
    )


def render_risk_sizing(
    client,
    *,
    account_picker: Callable[[str], dict[str, Any] | None],
    refresh_accounts: Callable[[Any], list[dict[str, Any]]],
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
    touch_session: Callable[[], None],
) -> None:
    """Render v5 while temporarily upgrading its Crown notes section."""
    previous_reference = _v4._render_crown_reference
    _v4._render_crown_reference = _render_crown_reference_with_ranges
    try:
        _v5.render_risk_sizing(
            client,
            account_picker=account_picker,
            refresh_accounts=refresh_accounts,
            account_balance=account_balance,
            balance_snapshot=balance_snapshot,
            touch_session=touch_session,
        )
    finally:
        _v4._render_crown_reference = previous_reference
