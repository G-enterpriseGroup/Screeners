"""Shared numeric display formatting for Raj's Terminal tables.

Keeps underlying DataFrame values numeric/sortable while using Streamlit's
NumberColumn formatting to add thousands separators consistently.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


_MONEY_HINTS = (
    "market value", "account value", "value", "cost", "basis", "gain/loss",
    "gain loss", "cash", "amount", "notional", "price", "debit", "credit",
    "profit", "loss", "risk", "exposure", "premium", "proceeds",
)
_COUNT_HINTS = ("quantity", "shares", "contracts", "count", "units")


def comma_column_config(data: Any, existing: dict | None = None) -> dict:
    """Return column_config with locale-aware commas for numeric DataFrames."""
    config = dict(existing or {})
    if not isinstance(data, pd.DataFrame):
        return config

    for column in data.columns:
        if column in config:
            continue
        series = data[column]
        if not pd.api.types.is_numeric_dtype(series):
            continue

        name = str(column)
        lower = name.casefold()
        if "%" in name or "percent" in lower or "yield" in lower or "coupon" in lower:
            config[column] = st.column_config.NumberColumn(format="%.2f%%")
        elif any(hint in lower for hint in _MONEY_HINTS):
            config[column] = st.column_config.NumberColumn(format="dollar")
        elif any(hint in lower for hint in _COUNT_HINTS):
            config[column] = st.column_config.NumberColumn(format="localized")
        else:
            # Default numeric display also gets locale separators without
            # changing the underlying sortable numeric value.
            config[column] = st.column_config.NumberColumn(format="localized")
    return config
