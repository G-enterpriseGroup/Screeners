"""Networth-owned Plaid linking, classifications and daily balance sheets."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import uuid

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.networth import (
    CLASSES, TIMEZONE, PlaidClient, PlaidConfig, account_rows, balance_sheet,
    sync_networth,
)
from src.networth_store import GitHubNetworthStore, NetworthError, StoreConfig


_networth_buckets = components.declare_component(
    "networth_account_buckets_v1",
    path=str(Path(__file__).parent / "components" / "networth_buckets_v1"),
)


def _remember_verified(cache_key, state: dict) -> None:
    if cache_key is not None:
        st.session_state.setdefault("_networth_verified_balances", {})[cache_key] = _readonly_snapshot(state)


def _bucket_revision(rows: list[dict]) -> str:
    identities = sorted((row["account_id"], row["classification"]) for row in rows)
    return hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()


def _save_bucket_move(store, event: dict, *, view_id: str, revision: str, cache_key=None) -> dict:
    fields = {"action", "account_id", "target_class", "base_class", "event_id", "view_id", "revision"}
    if not isinstance(event, dict) or set(event) != fields or any(not isinstance(value, str) for value in event.values()):
        raise NetworthError("Invalid account move. Reload and try again.")
    try:
        valid_id = str(uuid.UUID(event["event_id"])) == event["event_id"]
    except (ValueError, AttributeError):
        valid_id = False
    if (not valid_id or event["action"] != "classify" or event["view_id"] != view_id
            or event["revision"] != revision or event["target_class"] not in CLASSES
            or event["base_class"] not in CLASSES):
        raise NetworthError("This account view changed. Reload and try the move again.")

    def save(latest):
        row = next((row for row in account_rows(latest) if row["account_id"] == event["account_id"]), None)
        if row is None:
            raise NetworthError("This account is no longer linked. Reload accounts and try again.")
        if row["classification"] == event["target_class"]:
            return
        if row["classification"] != event["base_class"]:
            raise NetworthError("This account was changed in another window. Reload and try again.")
        preferences = latest.setdefault("preferences", {})
        previous = preferences.get(event["account_id"], {})
        preferences[event["account_id"]] = {
            **(previous if isinstance(previous, Mapping) else {}), "classification": event["target_class"],
        }

    updated = store.update_state(save)
    _remember_verified(cache_key, updated)
    return updated


def _bucket_editor(store, state: dict, *, cache_key=None) -> None:
    rows = account_rows(state)
    if not rows:
        return
    views = st.session_state.setdefault("_networth_bucket_views", {})
    view = views.setdefault(cache_key or store.scope, {"view_id": uuid.uuid4().hex, "seen": {}, "ack": None})
    revision = _bucket_revision(rows)
    event = _networth_buckets(
        accounts=[{"account_id": row["account_id"], "label": _account_label(row, state.get("preferences", {})),
                   "balance": row.get("current"), "currency": row.get("currency"),
                   "classification": row["classification"], "stale": bool(row.get("stale"))} for row in rows],
        view_id=view["view_id"], revision=revision, ack=view["ack"], default=None,
        key="networth_account_buckets",
    )
    if not isinstance(event, dict):
        return
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or len(event_id) > 64 or event_id in view["seen"]:
        return
    try:
        _save_bucket_move(store, event, view_id=view["view_id"], revision=revision, cache_key=cache_key)
    except NetworthError as exc:
        ack = {"event_id": event_id, "ok": False, "message": str(exc)}
    except Exception:
        ack = {"event_id": event_id, "ok": False, "message": "Could not verify the save. Reload saved balances and try again."}
    else:
        ack = {"event_id": event_id, "ok": True, "message": "Saved"}
    view["ack"] = ack
    view["seen"][event_id] = True
    while len(view["seen"]) > 64:
        view["seen"].pop(next(iter(view["seen"])))
    st.rerun()


def _balance_status(accounts: list[dict], currency: str) -> dict:
    rows = [row for row in accounts if (row.get("currency") or "UNKNOWN") == currency
            and row.get("classification") != "Excluded"]
    result = {"state": "incomplete", "gap": None, "stale": any(row.get("stale") for row in rows)}
    if not rows or currency == "UNKNOWN":
        return result
    values = {kind: [] for kind in ("Asset", "Liability", "Equity")}
    for row in rows:
        value = row.get("current")
        if row.get("classification") not in values or isinstance(value, bool):
            return result
        try:
            value = float(value)
        except (TypeError, ValueError):
            return result
        if not math.isfinite(value):
            return result
        values[row["classification"]].append(value)
    if not values["Equity"]:
        return {**result, "state": "calculated"}
    gap = round(math.fsum(values["Asset"]) - math.fsum(values["Liability"]) - math.fsum(values["Equity"]), 2)
    return {**result, "state": "balanced" if gap == 0 else "unbalanced", "gap": gap}


def _balance_status_html(accounts: list[dict], currency: str) -> str:
    status = _balance_status(accounts, currency)
    labels = {"balanced": "✓ BALANCED", "calculated": "EQUITY CALCULATED", "incomplete": "CHECK INCOMPLETE"}
    if status["state"] == "unbalanced":
        label = "OUT OF BALANCE · " + html.escape(currency) + f' {status["gap"]:+,.2f} equity allocation gap'
    else:
        label = labels[status["state"]]
    note = " · saved balances" if status["stale"] else ""
    title = "Assets = Liabilities + Equity. Checks classified equity before the automatic unallocated remainder."
    return '<div class="nw-balance-check nw-check-' + status["state"] + '" role="status" title="' + title + '"><span class="nw-check-dot" aria-hidden="true"></span>' + label + ' · A = L + E' + note + '</div>'


def _timestamp(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TIMEZONE).strftime("%b %d, %Y %I:%M %p %Z")
    except (ValueError, TypeError):
        return "Unknown"


def _readonly_snapshot(state: dict) -> dict:
    account_fields = ("account_id", "item_id", "name", "mask", "type", "subtype", "current", "currency",
                      "as_of", "balance_updated_at", "persistent_account_id")
    item_fields = ("label", "error", "last_sync", "last_attempt", "linked_at", "institution_id")
    return {
        "accounts": {key: {field: deepcopy(value[field]) for field in account_fields if field in value}
                     for key, value in state.get("accounts", {}).items()},
        "items": {key: {field: deepcopy(value[field]) for field in item_fields if field in value}
                  for key, value in state.get("items", {}).items()},
        "preferences": {key: ({field: deepcopy(value[field]) for field in ("nickname", "classification") if field in value}
                              if isinstance(value, Mapping) else {"classification": value})
                        for key, value in state.get("preferences", {}).items()
                        if isinstance(value, Mapping) or (isinstance(value, str) and value in CLASSES)},
        "last_sync": state.get("last_sync"), "last_errors": deepcopy(state.get("last_errors", {})),
        "plaid_environment": state.get("plaid_environment"),
    }


def _latest_account_update(state: dict) -> str | None:
    values = []
    for item in state.get("items", {}).values():
        value = item.get("last_sync")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                values.append((parsed, value))
        except (AttributeError, TypeError, ValueError):
            continue
    return max(values, default=(None, None))[1]


def _account_label(account: dict, preferences: dict) -> str:
    preference = preferences.get(account.get("account_id"), {})
    nickname = str(preference.get("nickname") or "").strip() if isinstance(preference, Mapping) else ""
    if nickname:
        return nickname
    return str(account.get("name") or "Account") + (" • " + str(account["mask"]) if account.get("mask") else "")


def _save_account_details(store, account_id: str, nickname: str, classification: str | None = None, *, cache_key=None) -> None:
    nickname = " ".join(str(nickname).split())
    if len(nickname) > 60 or (classification is not None and classification not in CLASSES):
        raise NetworthError("Use a nickname of 60 characters or fewer and a valid classification.")

    def save(latest):
        if account_id not in latest.get("accounts", {}):
            raise NetworthError("This account is no longer linked. Reload accounts and try again.")
        all_preferences = latest.setdefault("preferences", {})
        previous = all_preferences.get(account_id)
        preferences = dict(previous) if isinstance(previous, Mapping) else (
            {"classification": previous} if previous in CLASSES else {}
        )
        all_preferences[account_id] = preferences
        if nickname:
            preferences["nickname"] = nickname
        else:
            preferences.pop("nickname", None)
        if classification is not None:
            preferences["classification"] = classification

    updated = store.update_state(save)
    _remember_verified(cache_key, updated)


def _statement_rows(accounts: list[dict], preferences: dict, currency: str) -> list[dict]:
    normalized = []
    for row in accounts:
        if row.get("currency") != currency:
            continue
        try:
            value = float(row["current"]) if not isinstance(row.get("current"), bool) else None
        except (TypeError, ValueError, KeyError):
            value = None
        normalized.append({**row, "current": value if value is not None and math.isfinite(value) else None})
    accounts = normalized
    included = [row for row in accounts if row.get("classification") in ("Asset", "Liability", "Equity")]
    totals = next((row for row in balance_sheet(included) if row["currency"] == currency), {})
    groups = {kind: [row for row in included if row["classification"] == kind]
              for kind in ("Asset", "Liability", "Equity")}
    assets = totals.get("assets")
    liabilities = totals.get("liabilities")
    if currency == "UNKNOWN" or any(row.get("current") is None for row in groups["Asset"]):
        assets = None
    if currency == "UNKNOWN" or any(row.get("current") is None for row in groups["Liability"]):
        liabilities = None
    equity = assets - liabilities if assets is not None and liabilities is not None else None
    rows = []

    def add(label, amount=None, kind="account", **extra):
        share = amount / assets * 100 if amount is not None and assets else None
        rows.append({"label": label, "amount": amount, "share": share, "kind": kind, **extra})

    def account(row, liability=False):
        amount = row.get("current") if currency != "UNKNOWN" else None
        if liability and amount is not None:
            amount = -amount
        add(_account_label(row, preferences), amount, account_id=row["account_id"],
            classification=row["classification"], status="Stale" if row.get("stale") else "")

    add("Assets:", kind="section")
    for row in groups["Asset"]:
        account(row)
    add("Total Assets:", assets, "total")
    add("Liabilities:", kind="section")
    for row in groups["Liability"]:
        account(row, liability=True)
    add("Total Liabilities:", liabilities, "total")
    if groups["Equity"]:
        add("Equity:", kind="section")
        for row in groups["Equity"]:
            account(row)
        allocated = totals.get("equity", 0) if all(row.get("current") is not None for row in groups["Equity"]) else None
        add("Unallocated equity", equity - allocated if equity is not None and allocated is not None else None)
        add("Total Equity:", equity, "equity")
    else:
        add("Equity:", equity, "equity")
    add("Total Liabilities & Equity:", liabilities + equity if liabilities is not None and equity is not None else None,
        "grand_total")
    return rows


def _statement_html(rows: list[dict], currency: str) -> str:
    def money(value):
        if value is None:
            return "—"
        prefix = "$" if currency == "USD" else ""
        return ("-" if value < 0 else "") + prefix + f"{abs(value):,.2f}"

    body = []
    for row in rows:
        label = html.escape(str(row["label"]))
        if row.get("status"):
            label += ' <span title="Previous balance; refresh failed">*</span>'
        kind = row["kind"]
        if kind == "section":
            body.append('<tr class="nw-section"><th colspan="3">' + label + '</th></tr>')
        else:
            share = "—" if row["share"] is None else f'{row["share"]:,.2f}%'
            body.append('<tr class="nw-' + kind + '"><td>' + label + '</td><td>'
                        + money(row["amount"]) + '</td><td>' + share + '</td></tr>')
    return '''<style>
.networth-statement{width:100%;border-collapse:collapse;color:#fb8b1e;font-family:"Courier New",monospace;font-size:16px;line-height:1.25}
.networth-statement th,.networth-statement td{padding:5px 8px;text-align:left}
.networth-statement td:first-child{overflow-wrap:anywhere}
.networth-statement thead th{font-size:12px;font-weight:700;border-bottom:1px solid #b87621}
.networth-statement td:nth-child(2),.networth-statement td:nth-child(3),.networth-statement thead th:nth-child(2),.networth-statement thead th:nth-child(3){text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.networth-statement .nw-section th{padding-top:12px;font-weight:900}
.networth-statement .nw-total td,.networth-statement .nw-equity td{border-top:1px solid #b87621;font-weight:900}
.networth-statement .nw-equity td{padding-top:9px;padding-bottom:9px}
.networth-statement .nw-grand_total td{border-top:2px solid #fb8b1e;border-bottom:2px solid #fb8b1e;font-weight:900}
@media(max-width:600px){.networth-statement{font-size:13px}.networth-statement th,.networth-statement td{padding:5px 3px}}
</style><table class="networth-statement" aria-label="Networth balance sheet"><thead><tr><th>Account</th><th>Balance (''' + html.escape(currency) + ''')</th><th>% of Assets</th></tr></thead><tbody>''' + "".join(body) + '</tbody></table>'


def _render_statement(accounts: list[dict], preferences: dict, *, key: str) -> None:
    currencies = sorted({str(row.get("currency") or "UNKNOWN") for row in accounts}) or ["USD"]
    currency = st.selectbox("Currency", currencies, key=key + "_currency") if len(currencies) > 1 else currencies[0]
    st.html(_balance_status_html(accounts, currency))
    st.html(_statement_html(_statement_rows(accounts, preferences, currency), currency))
    omitted = [row for row in accounts if row.get("classification") == "Unclassified"]
    incomplete = [row for row in accounts if row.get("classification") not in ("Excluded", "Unclassified")
                  and (row.get("current") is None or row.get("currency") == "UNKNOWN")]
    if omitted or incomplete:
        st.warning("This statement is partial. Review unclassified accounts or unavailable balances in Accounts & connections.")
    if any(row.get("stale") for row in accounts):
        st.caption("* Previous saved balance; the latest refresh failed for this institution.")
    if any(row.get("classification") == "Equity" for row in accounts):
        st.caption("Equity = Assets − Liabilities. Named equity accounts are allocations within equity; the remainder is unallocated.")


def _setup() -> None:
    st.info("Networth needs its Plaid production credentials and encrypted private storage before accounts can be linked.")
    st.caption("Streamlit: Manage app → Settings → Secrets. Add the [networth] section below and keep your existing secrets. For scheduled pulls, add matching values in GitHub → Screeners → Settings → Secrets and variables → Actions.")
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
        st.caption("GitHub repository secrets: NETWORTH_PLAID_CLIENT_ID, NETWORTH_PLAID_SECRET, NETWORTH_GITHUB_TOKEN, NETWORTH_ENCRYPTION_KEY. Full instructions: src/NETWORTH_SETUP.md.")


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
    pending_links = state.get("pending_links", {})
    if pending_links:
        st.caption("Finish Plaid in its window. Connection status checks automatically.")
    for index, (link_id, pending) in enumerate(pending_links.items(), 1):
        expiration = pending.get("expiration")
        expired = expiration and datetime.fromisoformat(expiration.replace("Z", "+00:00")) < datetime.now(timezone.utc)
        status, open_link, dismiss_link = st.columns([3, 2, 1], gap="small", vertical_alignment="center")
        try:
            if _complete_link(store, client, link_id, pending):
                st.rerun()
        except NetworthError as exc:
            status.caption(str(exc))
        if expired:
            status.caption("Connection " + str(index) + " · Expired; start a new link.")
        else:
            status.caption("Connection " + str(index) + " · Waiting for Plaid")
            open_link.link_button("OPEN PLAID" if not pending.get("item_id") else "RECONNECT", pending["hosted_link_url"])
        if dismiss_link.button("DISMISS", key="networth_dismiss_" + link_id):
            def dismiss(latest):
                latest.setdefault("pending_links", {}).pop(link_id, None)
            store.update_state(dismiss)
            st.rerun()


def _classification_editor(store, state: dict, *, cache_key=None) -> None:
    rows = account_rows(state)
    if not rows:
        st.info("Link an institution to pull its accounts and start the balance sheet.")
        return
    display = pd.DataFrame([{
        "Account ID": row["account_id"], "Institution": row.get("item_label", ""),
        "Account": _account_label(row, state.get("preferences", {})),
        "Original account": row.get("name", "Account"),
        "Type": row.get("subtype") or row.get("type"), "Classification": row["classification"],
        "Balance": row.get("current"), "Currency": row.get("currency", "Unknown"),
        "Captured": _timestamp(row.get("as_of")),
        "Bank updated": _timestamp(row.get("balance_updated_at")) if row.get("balance_updated_at") else "Not provided",
        "Status": "Refresh failed; prior balance" if row.get("stale") else ("Balance unavailable" if row.get("current") is None else "Available"),
    } for row in rows]).set_index("Account ID")
    st.caption("Select accounts to rename or batch-classify. Excluded accounts stay linked and sync, but do not enter totals.")
    if any(row.get("possible_duplicate") for row in rows):
        st.warning("Possible duplicate accounts were linked. Review them before including them in totals; new suspected duplicates start Unclassified.")
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
        _remember_verified(cache_key, store.update_state(save_selection))
    if len(selected) == 1:
        account_id = selected[0]
        preference = state.get("preferences", {}).get(account_id, {})
        nickname = preference.get("nickname", "") if isinstance(preference, Mapping) else ""
        with st.form("networth_nickname_form_" + account_id, border=False):
            nickname_col, nickname_save = st.columns([2, 1], vertical_alignment="bottom")
            value = nickname_col.text_input("Account nickname", value=nickname, max_chars=60,
                                             key="networth_nickname_" + account_id,
                                             placeholder=str(display.loc[account_id, "Original account"]))
            if nickname_save.form_submit_button("SAVE NICKNAME", width="stretch"):
                _save_account_details(store, account_id, value, cache_key=cache_key)
                st.rerun()
    else:
        st.caption("Select one account to edit its nickname.")
    class_col, save_col = st.columns([2, 1], vertical_alignment="bottom")
    classification = class_col.selectbox("Classification", list(CLASSES), key="networth_apply_classification")
    if save_col.button("SAVE CLASSIFICATIONS", disabled=not selected, width="stretch"):
        def save(latest):
            for account_id in selected:
                if account_id in latest.get("accounts", {}):
                    preferences = latest.setdefault("preferences", {})
                    previous = preferences.get(account_id, {})
                    preferences[account_id] = {
                        **(previous if isinstance(previous, Mapping) else {}), "classification": classification,
                    }
        _remember_verified(cache_key, store.update_state(save))
        st.rerun()


def _daily_sheet(store, state: dict) -> None:
    today = datetime.now(TIMEZONE).date()
    date_col, capture_col, csv_col = st.columns([1, 2, 1], gap="small", vertical_alignment="bottom")
    selected = date_col.date_input("Balance sheet date", today, max_value=today, key="networth_sheet_date")
    day = store.read_day(selected.isoformat())
    captures = (day or {}).get("captures", {})
    if not captures:
        st.info("No saved balance sheet for this date. Historical balances begin with the first successful pull.")
        return
    ordered = sorted(captures, key=lambda key: captures[key].get("captured_at", ""), reverse=True)
    capture_id = capture_col.selectbox(
        "Saved capture", ordered,
        format_func=lambda key: _timestamp(captures[key].get("captured_at")) + (" • Scheduled" if captures[key].get("scheduled_slot") else " • Manual"),
        key="networth_capture_" + selected.isoformat(),
    )
    capture = captures[capture_id]
    rows = capture.get("accounts", [])
    _render_statement(rows, state.get("preferences", {}), key="networth_history")
    if not capture.get("complete"):
        st.warning("This capture is incomplete. Previous balances may be present; check account timestamps.")
    frame = pd.DataFrame([{
        "Account": _account_label(row, state.get("preferences", {})), "Last 4": row.get("mask"),
        "Classification": row.get("classification"), "Balance": row.get("current"),
        "Currency": row.get("currency"), "Captured": _timestamp(row.get("as_of")),
        "Bank updated": _timestamp(row.get("balance_updated_at")) if row.get("balance_updated_at") else "Not provided",
        "Status": "Stale" if row.get("stale") else ("Unavailable" if row.get("current") is None else "Available"),
    } for row in rows])
    csv_col.download_button("DOWNLOAD CSV", frame.to_csv(index=False).encode(),
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


def _render_networth_content() -> None:
    try:
        plaid_config = PlaidConfig.from_runtime()
        store_config = StoreConfig.from_runtime()
        if plaid_config is None or store_config is None:
            _render_statement([], {}, key="networth_current")
            with st.expander("Connect Plaid"):
                _setup()
            return
        client = PlaidClient(plaid_config)
        store = GitHubNetworthStore(store_config)
        cache = st.session_state.setdefault("_networth_verified_balances", {})
        cache_key = (getattr(store_config, "repository", ""), getattr(store_config, "branch", ""),
                     store.scope, plaid_config.environment)
        readonly = False
        try:
            state = store.read_state()
        except Exception:
            if cache_key not in cache:
                raise
            state = deepcopy(cache[cache_key])
            readonly = True
        if state.get("plaid_environment") and state["plaid_environment"] != plaid_config.environment:
            raise NetworthError("Plaid environment differs from the saved ledger. Restore the original environment or use a separate ledger_id.")
        if readonly:
            st.warning("Saved storage is unavailable. Showing the last verified balances from this browser session in read-only mode; reload when storage recovers.")
            st.caption("Last saved attempt: " + _timestamp(state.get("last_sync")) + " // Most recent account balance update: " + _timestamp(_latest_account_update(state)))
            _render_statement(account_rows(state), state.get("preferences", {}), key="networth_current")
            return
        cache[cache_key] = _readonly_snapshot(state)
        status_col, sync_col, reload_col = st.columns([6, 1, 1], gap="small", vertical_alignment="center")
        status_col.caption("PLAID " + plaid_config.environment.upper() + " // Last attempt: " + _timestamp(state.get("last_sync")))
        status_col.caption("Schedule: 9 AM · noon · 5 PM · 10 PM · Indianapolis")
        if reload_col.button("RELOAD", key="networth_reload", help="Reload saved settings and balances without another Plaid pull"):
            st.rerun()
        if sync_col.button("SYNC NOW", disabled=not state.get("items")):
            try:
                with st.spinner("Pulling linked account balances…"):
                    updated = sync_networth(store, client)
                cache[cache_key] = _readonly_snapshot(updated)
            except NetworthError as exc:
                if str(exc) == "NETWORTH_SYNC_IN_PROGRESS":
                    st.info("A balance sync is already running. Your saved balances remain visible; reload shortly.")
                elif str(exc) in ("NETWORTH_SYNC_LEASE_EXPIRED", "NETWORTH_SYNC_LEASE_LOST"):
                    st.warning("This sync ended before it could save. Your saved balances remain visible; reload and retry shortly.")
                else:
                    st.warning("Balance sync could not finish. Showing the last saved balances. " + str(exc))
            except Exception:
                st.warning("Balance sync could not finish. Showing the last saved balances; retry shortly.")
            else:
                st.rerun()
        for error in state.get("last_errors", {}).values():
            st.warning("Balance refresh: " + str(error))
        if state.get("last_errors") or state.get("last_capture_complete") is False:
            status_col.caption("Most recent account balance update: " + _timestamp(_latest_account_update(state)) + "; some accounts may be older.")
        _render_statement(account_rows(state), state.get("preferences", {}), key="networth_current")
        _bucket_editor(store, state, cache_key=cache_key)
        with st.expander("Accounts & connections"):
            st.caption("Account settings are saved automatically after verified changes. All linked accounts sync, including excluded accounts.")
            connection_types = {
                "Bank / credit accounts": ("transactions",),
                "Investment accounts": ("investments",),
                "Loans / liabilities": ("liabilities",),
                "Configured Plaid products": plaid_config.products,
            }
            type_col, link_col, reload_col = st.columns([2, 1, 1], gap="small", vertical_alignment="bottom")
            connection_type = type_col.selectbox("Connection type", list(connection_types), key="networth_connection_type",
                                           help="Choose a product enabled for your Plaid production application and supported by the institution.")
            if link_col.button("CONNECT PLAID"):
                _start_link(store, client, products=connection_types[connection_type])
                st.rerun()
            if reload_col.button("RELOAD BALANCES"):
                st.rerun()
            if state.get("pending_links"):
                _pending_links(store, client)
            _classification_editor(store, state, cache_key=cache_key)
            _connections(store, client, state)
            if state.get("last_scheduled_at"):
                st.caption("Last verified scheduled capture: " + _timestamp(state["last_scheduled_at"]))
            else:
                st.info("Scheduled syncing is not yet verified. Configure matching GitHub Actions secrets and verify the first scheduled run.")
        with st.expander("Daily history & CSV"):
            _daily_sheet(store, state)
    except NetworthError as exc:
        st.error(str(exc))
    except Exception:
        st.error("Networth could not load. Check the dedicated Plaid/storage configuration and retry.")


def render_networth() -> None:
    with st.container(key="networth_workspace", gap="small"):
        st.html('''<style>
.st-key-networth_workspace [data-testid="stVerticalBlock"]{gap:4px!important}
.st-key-networth_workspace [data-testid="stCaptionContainer"]{margin:0!important}
.st-key-networth_workspace [data-testid="stCaptionContainer"] p{font-size:12px!important;line-height:1.25!important;margin:0!important}
.st-key-networth_workspace [data-testid="stButton"] button,
.st-key-networth_workspace [data-testid="stFormSubmitButton"] button,
.st-key-networth_workspace [data-testid="stDownloadButton"] button,
.st-key-networth_workspace [data-testid="stLinkButton"] a{min-height:30px!important;height:30px!important;padding:0 9px!important;white-space:nowrap!important}
.st-key-networth_workspace [data-testid="stButton"] button p,
.st-key-networth_workspace [data-testid="stFormSubmitButton"] button p,
.st-key-networth_workspace [data-testid="stDownloadButton"] button p,
.st-key-networth_workspace [data-testid="stLinkButton"] a p{font-size:12px!important;line-height:1!important}
.st-key-networth_workspace [data-testid="stExpander"] summary{min-height:30px!important;padding:4px 8px!important}
.st-key-networth_workspace [data-testid="stExpander"] summary p{font-size:13px!important}
.st-key-networth_workspace .networth-statement{font-size:14px}
.st-key-networth_workspace .networth-statement th,.st-key-networth_workspace .networth-statement td{padding:3px 7px}
.st-key-networth_workspace .networth-statement .nw-section th{padding-top:7px}
.st-key-networth_workspace .networth-statement .nw-equity td{padding-top:5px;padding-bottom:5px}
.nw-balance-check{display:flex;align-items:center;gap:6px;font:11px/1.3 "Courier New",monospace;color:#b87621;padding:2px 0}
.nw-check-balanced{color:#49c587}.nw-check-unbalanced{color:#ff4848;font-weight:700}
.nw-check-dot{height:6px;width:6px;border-radius:50%;background:currentColor;flex:none}
.nw-check-unbalanced .nw-check-dot{animation:nw-check-pulse .9s ease-in-out 3}
@keyframes nw-check-pulse{50%{opacity:.25;box-shadow:0 0 5px #ff3030}}
@media(prefers-reduced-motion:reduce){.nw-check-unbalanced .nw-check-dot{animation:none}}
@media(max-width:600px){.st-key-networth_workspace .networth-statement{font-size:12px}.st-key-networth_workspace .networth-statement th,.st-key-networth_workspace .networth-statement td{padding:3px}}
</style>''')
        _render_networth_content()
