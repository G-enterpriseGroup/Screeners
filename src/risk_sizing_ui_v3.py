"""Risk-sizing shell with a visible E*TRADE cash snapshot.

This wraps the enhanced v2 Crown risk-sizing UI and keeps the cash balance
prominent without duplicating the full sizing engine.
"""

from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from src.etrade_client import ETradeError
from src.risk_sizing_ui_v2 import render_risk_sizing as _render_risk_sizing_v2
from src.theme import BB_BLUE, BB_ORANGE


def _selected_account(accounts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not accounts:
        return None
    selected = st.session_state.get("risk_sizing_account")
    try:
        selected = int(selected)
    except (TypeError, ValueError):
        selected = None
    if selected is not None and 0 <= selected < len(accounts):
        return accounts[selected]
    for account in accounts:
        if str(account.get("accountId", "")).endswith("5474"):
            return account
    return accounts[0]


def _cash_strip(
    client,
    *,
    account_balance: Callable[..., dict[str, Any]],
    balance_snapshot: Callable[[dict[str, Any]], tuple[float, float, float]],
) -> None:
    if not client:
        return
    account = _selected_account(st.session_state.get("etrade_accounts", []))
    if not account:
        return
    try:
        payload = account_balance(client, account, refresh=False)
        account_total, cash_available, _ = balance_snapshot(payload)
    except ETradeError:
        return

    cash_pct = cash_available / account_total * 100.0 if account_total else 0.0
    st.markdown(
        f"""
        <div style="border:1px solid {BB_ORANGE};background:#000;padding:.55rem .8rem;
                    margin:0 0 .65rem 0;font-family:'Courier New',monospace;display:flex;
                    justify-content:space-between;align-items:center;gap:1rem;">
          <div style="color:{BB_ORANGE};font-weight:900;">E*TRADE CASH AVAILABLE</div>
          <div style="color:{BB_BLUE};font-size:1.35rem;font-weight:900;">
            ${cash_available:,.2f}
          </div>
          <div style="color:{BB_ORANGE};font-size:.78rem;">
            {cash_pct:.2f}% OF ACCOUNT // SHARED CACHE
          </div>
        </div>
        """,
        unsafe_allow_html=True,
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
    _cash_strip(
        client,
        account_balance=account_balance,
        balance_snapshot=balance_snapshot,
    )
    _render_risk_sizing_v2(
        client,
        account_picker=account_picker,
        refresh_accounts=refresh_accounts,
        account_balance=account_balance,
        balance_snapshot=balance_snapshot,
        touch_session=touch_session,
    )
