"""Networth-owned Plaid linking, classifications and daily balance sheets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pandas as pd
import streamlit as st

from src.networth import (
    CLASSES, TIMEZONE, PlaidClient, PlaidConfig, account_rows, balance_sheet,
    sync_networth,
)
from src.networth_store import GitHubNetworthStore, NetworthError, StoreConfig


def _timestamp(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TIMEZONE).strftime("%b %d, %Y %I:%M %p %Z")
    except (ValueError, TypeError):
        return "Unknown"


def _setup() -> None:
    st.info("Networth needs its Plaid production credentials and encrypted private storage before accounts can be linked.")
    st.caption("Add the dedicated [networth] settings to Streamlit Secrets, then configure the matching GitHub Actions secrets for browser-independent syncs. See src/NETWORTH_SETUP.md in the repository.")
    with st.expander("Networth configuration"):
        st.code('''[networth]
plaid_client_id = "YOUR_CLIENT_ID"
plaid_secret = "YOUR_PRODUCTION_SECRET"
plaid_environment = "production"
plaid_products = ["transactions"]
github_token = "PRIVATE_PORTFOLIO_CONTENTS_WRITE_TOKEN"
encryption_key = "YOUR_FERNET_KEY"
repository = "G-enterpriseGroup/Portfolio"
branch = "networth-data"
ledger_id = "raj-terminal"''', language="toml")
        st.caption("Keep keys in secret settings. Use the same encryption key and ledger in Streamlit and Actions.")


def _start_link(store, client, item_id: str = "", products=None) -> None:
    state = store.read_state()
    if state.get("plaid_environment") and state["plaid_environment"] != client.config.environment:
        raise NetworthError("Plaid environment differs from the saved ledger. Restore the original environment or use a separate ledger_id.")
    item = state.get("items", {}).get(item_id, {})
    link = client.create_link(store.scope, access_token=item.get("access_token") if item_id else None, products=products)
    link_id = uuid.uuid4().hex

    def save(latest):
        if latest.get("plaid_environment") and latest["plaid_environment"] != client.config.environment:
            raise NetworthError("Plaid environment differs from the saved ledger. Use a separate ledger_id.")
        latest["plaid_environment"] = client.config.environment
        latest.setdefault("pending_links", {})[link_id] = {
            "link_token": link["link_token"], "hosted_link_url": link["hosted_link_url"],
            "expiration": link.get("expiration"), "item_id": item_id,
        }

    store.update_state(save)


def _complete_link(store, client, link_id: str, pending: dict) -> bool:
    result = client.link_results(pending["link_token"])
    if not result.get("completed"):
        return False
    owner = st.session_state.setdefault("_networth_link_owner", uuid.uuid4().hex)
    now = datetime.now(timezone.utc)

    def claim(latest):
        entry = latest.get("pending_links", {}).get(link_id)
        if entry is None:
            return
        lease = entry.get("processing_until")
        if lease and lease > now.isoformat() and entry.get("processing_owner") != owner:
            return
        entry.update(processing_owner=owner, processing_until=(now + timedelta(minutes=5)).isoformat())

    claimed = store.update_state(claim)
    entry = claimed.get("pending_links", {}).get(link_id)
    if entry is None or entry.get("processing_owner") != owner:
        return False
    # Exchange once; retain a server-memory recovery copy until the encrypted
    # write verifies, because Plaid public tokens are single use.
    recovery = st.session_state.setdefault("_networth_link_exchange", {})
    for token in result.get("public_tokens", []):
        if token in entry.get("processed_tokens", []):
            continue
        key = (link_id, token)
        if key not in recovery:
            recovery[key] = client.exchange(token)
        exchanged = recovery[key]
        details = next((row for row in result.get("items", []) if row.get("public_token") == token), {})
        institution = details.get("institution") or {}
        label = institution.get("name", "Linked institution") if isinstance(institution, dict) else str(institution)

        def save(latest):
            item_id = exchanged["item_id"]
            old = latest.setdefault("items", {}).get(item_id, {})
            latest["items"][item_id] = {
                **old, "item_id": item_id, "access_token": exchanged["access_token"],
                "label": label, "error": "", "linked_at": datetime.now(timezone.utc).isoformat(),
                "institution_id": institution.get("institution_id", "") if isinstance(institution, dict) else "",
            }
            current = latest.get("pending_links", {}).get(link_id)
            if current is None or current.get("processing_owner") != owner:
                raise NetworthError("Plaid connection changed during linking; reload and retry.")
            current.setdefault("processed_tokens", []).append(token)

        store.update_state(save)
        recovery.pop(key, None)
    if result.get("completed"):
        def finish(latest):
            latest.setdefault("pending_links", {}).pop(link_id, None)
            item = latest.get("items", {}).get(pending.get("item_id"))
            if item:
                item["error"] = ""
        store.update_state(finish)
        sync_networth(store, client)
        return True
    return False


@st.fragment(run_every="5s")
def _pending_links(store, client) -> None:
    state = store.read_state()
    for link_id, pending in state.get("pending_links", {}).items():
        expiration = pending.get("expiration")
        expired = expiration and datetime.fromisoformat(expiration.replace("Z", "+00:00")) < datetime.now(timezone.utc)
        try:
            if _complete_link(store, client, link_id, pending):
                st.rerun()
        except NetworthError as exc:
            st.warning(str(exc))
        if expired:
            st.warning("This Plaid link expired. Dismiss it and start a new connection.")
        else:
            st.link_button("OPEN PLAID TO CONNECT" if not pending.get("item_id") else "OPEN PLAID TO RECONNECT", pending["hosted_link_url"])
            st.caption("Complete Plaid in the new window. This tab checks for completion every 5 seconds.")
        if st.button("DISMISS LINK", key="networth_dismiss_" + link_id):
            def dismiss(latest):
                latest.setdefault("pending_links", {}).pop(link_id, None)
            store.update_state(dismiss)
            st.rerun()


def _classification_editor(store, state: dict) -> None:
    rows = account_rows(state)
    if not rows:
        st.info("Link an institution to pull its accounts and start the balance sheet.")
        return
    display = pd.DataFrame([{
        "Account ID": row["account_id"], "Institution": row.get("item_label", ""),
        "Account": row.get("name", "Account") + (" • " + row["mask"] if row.get("mask") else ""),
        "Type": row.get("subtype") or row.get("type"), "Classification": row["classification"],
        "Balance": row.get("current"), "Currency": row.get("currency", "Unknown"),
        "Captured": _timestamp(row.get("as_of")),
        "Bank updated": _timestamp(row.get("balance_updated_at")) if row.get("balance_updated_at") else "Not provided",
        "Status": "Refresh failed; prior balance" if row.get("stale") else ("Balance unavailable" if row.get("current") is None else "Available"),
    } for row in rows]).set_index("Account ID")
    st.caption("Classify each account. Equity is shown separately; Net Worth = Assets − Liabilities. Excluded accounts do not enter totals.")
    if any(row.get("possible_duplicate") for row in rows):
        st.warning("Possible duplicate accounts were linked. Review them before including them in totals; new suspected duplicates start Unclassified.")
    st.dataframe(display, hide_index=True, width="stretch",
                 column_order=["Account", "Classification", "Balance", "Currency", "Status", "Captured"],
                 column_config={"Balance": st.column_config.NumberColumn(format="%.2f")})
    options = list(display.index)
    default = [row["account_id"] for row in rows if str(row.get("mask") or "").endswith("5474")] or options[:1]
    saved = [key for key in state.get("selected_accounts", default) if key in options]
    selection_key = "networth_selected_accounts"
    if selection_key not in st.session_state:
        st.session_state[selection_key] = saved
    else:
        st.session_state[selection_key] = [key for key in st.session_state[selection_key] if key in options]
    selected = st.multiselect(
        "Accounts to classify", options, key=selection_key,
        format_func=lambda key: str(display.loc[key, "Institution"]) + " // " + str(display.loc[key, "Account"]),
    )
    st.html('''<style>
.st-key-networth_selected_accounts [data-tag],
.st-key-networth_selected_accounts [data-tag] [title],
.st-key-networth_selected_accounts [data-tag] button,
.st-key-networth_selected_accounts [data-tag] button svg {
    color:#000000!important;-webkit-text-fill-color:#000000!important;
}
</style>''')
    if "selected_accounts" not in state or selected != state["selected_accounts"]:
        def save_selection(latest):
            latest["selected_accounts"] = list(selected)
        store.update_state(save_selection)
    class_col, save_col = st.columns([2, 1], vertical_alignment="bottom")
    classification = class_col.selectbox("Classification", list(CLASSES), key="networth_apply_classification")
    if save_col.button("SAVE CLASSIFICATIONS", disabled=not selected, width="stretch"):
        def save(latest):
            for account_id in selected:
                if account_id in latest.get("accounts", {}):
                    latest.setdefault("preferences", {}).setdefault(account_id, {})["classification"] = classification
        store.update_state(save)
        st.rerun()


def _totals(rows: list[dict]) -> None:
    totals = balance_sheet(rows)
    if not totals:
        return
    st.dataframe(pd.DataFrame(totals).rename(columns={
        "currency": "Currency", "assets": "Assets", "liabilities": "Liabilities",
        "equity": "Classified Equity", "net_worth": "Net Worth",
        "reconciliation": "Reconciliation", "missing": "Omitted",
    }), hide_index=True, width="stretch",
                 column_order=["Currency", "Assets", "Liabilities", "Classified Equity", "Net Worth", "Reconciliation", "Omitted"],
                 column_config={name: st.column_config.NumberColumn(format="%.2f")
                                for name in ("Assets", "Liabilities", "Classified Equity", "Net Worth", "Reconciliation")})
    if any(row.get("missing") for row in totals):
        st.warning("Totals omit unavailable balances, unknown currencies, or unclassified accounts.")
    if any(row.get("classification") == "Unclassified" for row in rows):
        st.warning("Unclassified accounts are omitted from totals. Choose a classification above.")
    st.caption("Reconciliation = Assets − Liabilities − Classified Equity.")


def _daily_sheet(store, state: dict) -> None:
    st.markdown("**DAILY BALANCE SHEET**")
    today = datetime.now(TIMEZONE).date()
    selected = st.date_input("Balance sheet date", today, max_value=today, key="networth_sheet_date")
    day = store.read_day(selected.isoformat())
    captures = (day or {}).get("captures", {})
    if not captures:
        st.info("No saved balance sheet for this date. Historical balances begin with the first successful pull.")
        return
    ordered = sorted(captures, key=lambda key: captures[key].get("captured_at", ""), reverse=True)
    capture_id = st.selectbox(
        "Saved capture", ordered,
        format_func=lambda key: _timestamp(captures[key].get("captured_at")) + (" • Scheduled" if captures[key].get("scheduled_slot") else " • Manual"),
        key="networth_capture_" + selected.isoformat(),
    )
    capture = captures[capture_id]
    rows = capture.get("accounts", [])
    _totals(rows)
    if not capture.get("complete"):
        st.warning("This capture is incomplete. Previous balances may be present; check account timestamps.")
    frame = pd.DataFrame([{
        "Account": row.get("name"), "Last 4": row.get("mask"),
        "Classification": row.get("classification"), "Balance": row.get("current"),
        "Currency": row.get("currency"), "Captured": _timestamp(row.get("as_of")),
        "Bank updated": _timestamp(row.get("balance_updated_at")) if row.get("balance_updated_at") else "Not provided",
        "Status": "Stale" if row.get("stale") else ("Unavailable" if row.get("current") is None else "Available"),
    } for row in rows])
    st.dataframe(frame, hide_index=True, width="stretch")
    st.download_button("DOWNLOAD BALANCE SHEET CSV", frame.to_csv(index=False).encode(),
                       file_name="networth-" + selected.isoformat() + ".csv", mime="text/csv")
    st.caption("Daily view defaults to the latest saved capture. Each snapshot keeps the classification used when it was captured; current edits apply to subsequent captures. Currencies are reported separately.")


def _connections(store, client, state: dict) -> None:
    if not state.get("items"):
        return
    with st.expander("Manage linked institutions"):
        for item_id, item in state["items"].items():
            st.write(item.get("label") or "Linked institution")
            if item.get("error"):
                st.warning("Connection needs attention: " + str(item["error"]))
            if st.button("RECONNECT", key="networth_reconnect_" + item_id):
                _start_link(store, client, item_id)
                st.rerun()
            confirmed = st.checkbox("Disconnect this institution and stop future syncs (saved history remains)", key="networth_disconnect_confirm_" + item_id)
            if st.button("DISCONNECT", disabled=not confirmed, key="networth_disconnect_" + item_id):
                try:
                    client.remove(item["access_token"])
                except NetworthError as exc:
                    if str(exc) != "PLAID_ITEM_NOT_FOUND":
                        raise

                def remove(latest):
                    latest.get("items", {}).pop(item_id, None)
                    latest["accounts"] = {key: account for key, account in latest.get("accounts", {}).items()
                                          if account.get("item_id") != item_id}
                    latest["pending_links"] = {key: link for key, link in latest.get("pending_links", {}).items()
                                               if link.get("item_id") != item_id}
                store.update_state(remove)
                st.rerun()


def render_networth() -> None:
    st.caption("Scheduled pulls: 9:00 AM · 12:00 PM · 5:00 PM · 10:00 PM — America/Indiana/Indianapolis. Actual capture times are recorded; institutions may provide delayed balances.")
    try:
        plaid_config = PlaidConfig.from_runtime()
        store_config = StoreConfig.from_runtime()
        if plaid_config is None or store_config is None:
            _setup()
            return
        client = PlaidClient(plaid_config)
        store = GitHubNetworthStore(store_config)
        state = store.read_state()
        if state.get("plaid_environment") and state["plaid_environment"] != plaid_config.environment:
            raise NetworthError("Plaid environment differs from the saved ledger. Restore the original environment or use a separate ledger_id.")
        st.caption("PLAID " + plaid_config.environment.upper() + " // Last pull: " + _timestamp(state.get("last_sync")))
        if state.get("last_scheduled_at"):
            st.caption("Last verified scheduled capture: " + _timestamp(state["last_scheduled_at"]))
        else:
            st.info("Scheduled syncing is not yet verified. Configure the matching Actions secrets and verify the first scheduled worker run.")
        connection_types = {
            "Bank / credit accounts": ("transactions",),
            "Investment accounts": ("investments",),
            "Loans / liabilities": ("liabilities",),
            "Configured Plaid products": plaid_config.products,
        }
        connection_type = st.selectbox("Connection type", list(connection_types), key="networth_connection_type",
                                       help="Choose a product enabled for your Plaid production application and supported by the institution.")
        link_col, sync_col, reload_col = st.columns(3)
        if link_col.button("LINK ACCOUNTS WITH PLAID", width="stretch"):
            _start_link(store, client, products=connection_types[connection_type])
            st.rerun()
        if sync_col.button("SYNC BALANCES NOW", disabled=not state.get("items"), width="stretch"):
            with st.spinner("Pulling linked account balances…"):
                sync_networth(store, client)
            st.rerun()
        if reload_col.button("RELOAD SAVED BALANCES", width="stretch"):
            st.rerun()
        if state.get("pending_links"):
            _pending_links(store, client)
        for error in state.get("last_errors", {}).values():
            st.warning("Balance refresh: " + str(error))
        _classification_editor(store, state)
        _totals(account_rows(state))
        _daily_sheet(store, state)
        _connections(store, client, state)
    except NetworthError as exc:
        st.error(str(exc))
    except Exception:
        st.error("Networth could not load. Check the dedicated Plaid/storage configuration and retry.")
