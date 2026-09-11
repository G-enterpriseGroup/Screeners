"""Raj's Terminal visual design system.

Source palette: Bloomberg palette from color-hex.com/color-palette/111776.
Keep all terminal modules aligned to these semantic colors.

Streamlit's interactive dataframe header is rendered by Glide Data Grid, not
normal DOM table-header elements. That means ordinary CSS selectors cannot
reliably recolor it. Raj's Terminal therefore uses Streamlit's native dataframe
header theme for the orange header band and keeps dataframe body text orange via
Pandas Styler, while preserving any existing red/green/blue financial styles.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

try:
    from pandas.io.formats.style import Styler
except Exception:  # pragma: no cover - defensive across pandas builds
    Styler = ()


BB_BLACK = "#000000"
BB_RED = "#ff433d"
BB_BLUE = "#0068ff"
BB_GREEN = "#4af6c3"
BB_ORANGE = "#fb8b1e"

BLOOMBERG_PALETTE = [
    BB_BLACK,
    BB_RED,
    BB_BLUE,
    BB_GREEN,
    BB_ORANGE,
]

CHART_COLORWAY = [
    BB_GREEN,
    BB_BLUE,
    BB_ORANGE,
    BB_RED,
]


def _style_dataframe_body(data):
    """Keep body text orange without overwriting existing financial colors.

    The app theme intentionally uses black as Streamlit's base text color so
    dataframe headers can render black text on the native orange header band.
    Interactive dataframe body cells therefore need an explicit orange default.
    Existing Styler colors (green gains, red losses, blue tactical percentages,
    etc.) are detected first and left untouched.
    """
    if isinstance(data, pd.DataFrame):
        return data.style.set_properties(**{"color": BB_ORANGE})

    if Styler and isinstance(data, Styler):
        try:
            # Compute the current style context before adding our fallback.
            # ctx is keyed by (row_position, column_position).
            data._compute()
            existing_ctx = dict(getattr(data, "ctx", {}) or {})
            body = data.data
            defaults = pd.DataFrame("", index=body.index, columns=body.columns)
            has_defaults = False

            for row_pos in range(len(body.index)):
                for col_pos in range(len(body.columns)):
                    declarations = existing_ctx.get((row_pos, col_pos), []) or []
                    has_color = any(
                        str(prop).strip().casefold() == "color"
                        for prop, _value in declarations
                    )
                    if not has_color:
                        defaults.iat[row_pos, col_pos] = f"color:{BB_ORANGE};"
                        has_defaults = True

            if has_defaults:
                data = data.apply(lambda _frame: defaults, axis=None)
        except Exception:
            # Styling must never make a functional table fail to render.
            pass
        return data

    return data


def install_bloomberg_dataframe_theme() -> None:
    """Install the interactive-table body-color adapter exactly once."""
    if getattr(st, "_raj_bloomberg_dataframe_theme", False):
        return

    original_dataframe = st.dataframe

    def bloomberg_dataframe(data=None, *args, **kwargs):
        try:
            data = _style_dataframe_body(data)
        except Exception:
            pass
        return original_dataframe(data, *args, **kwargs)

    st.dataframe = bloomberg_dataframe
    st._raj_bloomberg_dataframe_theme = True


# src.theme is imported before terminal renderers execute, so installing here
# guarantees one consistent dataframe treatment across every tab.
install_bloomberg_dataframe_theme()
