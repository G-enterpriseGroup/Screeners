"""Plaid balance reads and balance-sheet snapshots owned only by Networth."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
import re
import uuid
from zoneinfo import ZoneInfo

import requests

from src.networth_store import NetworthError


TIMEZONE = ZoneInfo("America/Indiana/Indianapolis")
CLASSES = ("Asset", "Liability", "Equity", "Excluded", "Unclassified")
SYNC_HOURS = (9, 12, 17, 22)
_PRODUCTS = {"transactions", "investments", "liabilities", "auth", "identity", "assets"}


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise NetworthError("NETWORTH_TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc)


def _iso(value):
    return _now(value).isoformat().replace("+00:00", "Z")


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _newer(value, reference):
    try:
        return _now(datetime.fromisoformat(str(value).replace("Z", "+00:00"))) > _now(reference)
    except (ValueError, TypeError, NetworthError):
        return False


@dataclass(frozen=True)
class PlaidConfig:
    client_id: str = field(repr=False)
    secret: str = field(repr=False)
    environment: str = "production"
    products: tuple[str, ...] = ("transactions",)

    def __post_init__(self):
        if not self.client_id or not self.secret:
            raise NetworthError("PLAID_CREDENTIALS_INCOMPLETE")
        if self.environment not in ("production", "sandbox"):
            raise NetworthError("PLAID_ENVIRONMENT_INVALID")
        if not self.products or any(not isinstance(product, str) or product not in _PRODUCTS for product in self.products):
            raise NetworthError("PLAID_PRODUCTS_INVALID")

    @classmethod
    def from_runtime(cls, secrets=None):
        if secrets is None:
            try:
                import streamlit as st

                secrets = st.secrets
                section = secrets.get("networth", {})
            except Exception:
                section = {}
        else:
            section = secrets.get("networth", {}) if isinstance(secrets, Mapping) else {}
        section = section if isinstance(section, Mapping) else {}

        def setting(key, env, default=""):
            return section[key] if key in section else os.environ.get(env, default)

        client_id = str(setting("plaid_client_id", "NETWORTH_PLAID_CLIENT_ID") or "").strip()
        secret = str(setting("plaid_secret", "NETWORTH_PLAID_SECRET") or "").strip()
        if not client_id and not secret:
            return None
        environment = str(setting("plaid_environment", "NETWORTH_PLAID_ENVIRONMENT", "production")).strip().lower()
        products = setting("plaid_products", "NETWORTH_PLAID_PRODUCTS", ["transactions"])
        if isinstance(products, str):
            products = [part.strip() for part in products.split(",") if part.strip()]
        if not isinstance(products, (list, tuple)):
            raise NetworthError("PLAID_PRODUCTS_INVALID")
        return cls(client_id, secret, environment, tuple(str(product).strip() for product in products))


class PlaidClient:
    def __init__(self, config, session=None):
        self.config = config
        self.session = session or requests.Session()

    def _post(self, endpoint, body):
        payload = {"client_id": self.config.client_id, "secret": self.config.secret, **body}
        try:
            response = self.session.post(
                "https://" + self.config.environment + ".plaid.com" + endpoint,
                json=payload,
                timeout=(10, 65),
            )
        except requests.RequestException:
            raise NetworthError("PLAID_CONNECTION_FAILED") from None
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise NetworthError("PLAID_RESPONSE_INVALID") from None
        if not isinstance(data, dict):
            raise NetworthError("PLAID_RESPONSE_INVALID")
        if not 200 <= response.status_code < 300 or data.get("error_code"):
            code = data.get("error_code")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
                code = "HTTP_" + str(response.status_code)
            raise NetworthError("PLAID_" + code)
        return data

    def create_link(self, user_id, access_token=None, products=None):
        if not str(user_id or "").strip():
            raise NetworthError("PLAID_USER_REQUIRED")
        body = {
            "client_name": "Raj's Terminal",
            "user": {"client_user_id": str(user_id)},
            "country_codes": ["US"],
            "language": "en",
            "hosted_link": {},
        }
        if access_token:
            body["access_token"] = access_token
        else:
            selected = self.config.products if products is None else products
            if not isinstance(selected, (list, tuple)) or not selected or any(
                not isinstance(product, str) or product not in _PRODUCTS for product in selected
            ):
                raise NetworthError("PLAID_PRODUCTS_INVALID")
            body["products"] = list(selected)
        result = self._post("/link/token/create", body)
        if not result.get("link_token") or not result.get("hosted_link_url"):
            raise NetworthError("PLAID_LINK_RESPONSE_INVALID")
        return {key: result.get(key) for key in ("hosted_link_url", "link_token", "expiration")}

    def link_results(self, link_token):
        result = self._post("/link/token/get", {"link_token": link_token})
        items = []
        completed = False
        exited = False
        sessions = result.get("link_sessions") or []
        if not isinstance(sessions, list):
            raise NetworthError("PLAID_LINK_RESPONSE_INVALID")
        for session in sessions:
            if not isinstance(session, Mapping):
                continue
            results = session.get("results") or {}
            added = results.get("item_add_results", []) if isinstance(results, Mapping) else []
            for entry in added:
                if not isinstance(entry, Mapping) or not entry.get("public_token"):
                    continue
                items.append({key: entry.get(key) for key in ("public_token", "institution", "accounts")})
            success = session.get("on_success")
            if isinstance(success, Mapping):
                completed = True
                token = success.get("public_token")
                if token and not any(item["public_token"] == token for item in items):
                    metadata = success.get("metadata") or {}
                    metadata = metadata if isinstance(metadata, Mapping) else {}
                    items.append({"public_token": token, "institution": metadata.get("institution"),
                                  "accounts": metadata.get("accounts")})
            if session.get("on_exit") is not None or session.get("exit") is not None:
                exited = True
        tokens = list(dict.fromkeys(item["public_token"] for item in items))
        return {"public_tokens": tokens, "completed": completed or bool(tokens),
                "exited": exited, "items": items}

    def exchange(self, public_token):
        result = self._post("/item/public_token/exchange", {"public_token": public_token})
        if not result.get("item_id") or not result.get("access_token"):
            raise NetworthError("PLAID_EXCHANGE_RESPONSE_INVALID")
        return {key: result[key] for key in ("item_id", "access_token")}

    def balances(self, access_token, now=None):
        # Required for Capital One non-depository Items and Apple Card; other
        # institutions ignore this bound and perform a live extraction.
        return self._post("/accounts/balance/get", {
            "access_token": access_token,
            "options": {"min_last_updated_datetime": _iso(_now(now) - timedelta(days=1))},
        })

    def remove(self, access_token):
        self._post("/item/remove", {"access_token": access_token})


def default_classification(account):
    kind = str(account.get("type") or "").lower()
    if kind in ("depository", "investment", "brokerage"):
        return "Asset"
    if kind in ("credit", "loan"):
        return "Liability"
    return "Unclassified"


def normalize_accounts(payload, item_id, now=None):
    accounts = payload.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise NetworthError("PLAID_ACCOUNTS_EMPTY")
    rows = []
    for account in accounts:
        if not isinstance(account, Mapping) or not account.get("account_id"):
            raise NetworthError("PLAID_ACCOUNTS_INVALID")
        balances = account.get("balances")
        if not isinstance(balances, Mapping):
            raise NetworthError("PLAID_BALANCES_INVALID")
        current = _number(balances.get("current"))
        rows.append({
            "account_id": str(account["account_id"]), "item_id": str(item_id),
            "name": str(account.get("name") or account.get("official_name") or "Account"),
            "mask": str(account.get("mask") or ""), "type": str(account.get("type") or ""),
            "subtype": str(account.get("subtype") or ""),
            "current": float(current) if current is not None else None,
            "currency": str(balances.get("iso_currency_code") or balances.get("unofficial_currency_code") or "UNKNOWN"),
            "as_of": _iso(now), "balance_updated_at": balances.get("last_updated_datetime"),
            "persistent_account_id": account.get("persistent_account_id"),
        })
    if len({row["account_id"] for row in rows}) != len(rows):
        raise NetworthError("PLAID_ACCOUNTS_DUPLICATED")
    return rows


def account_rows(state):
    rows = []
    preferences = state.get("preferences", {})
    items = state.get("items", {})
    seen = {}
    accounts = state.get("accounts", {})
    ordered = sorted(accounts.items(), key=lambda pair: (
        str(items.get(pair[1].get("item_id"), {}).get("linked_at") or ""), str(pair[1].get("item_id")), pair[0]
    ))
    for account_id, account in ordered:
        row = deepcopy(account)
        pref = preferences.get(account_id) or {}
        classification = pref.get("classification") if isinstance(pref, Mapping) else pref
        item = items.get(row.get("item_id"), {})
        institution_id = item.get("institution_id")
        persistent_id = row.get("persistent_account_id")
        keys = [("persistent", persistent_id)] if persistent_id else []
        if institution_id and row.get("mask") and row.get("name"):
            keys.append(("account", institution_id, row["mask"], " ".join(row["name"].lower().split()),
                         row.get("type"), row.get("subtype")))
        duplicate = any(key in seen and seen[key] != row.get("item_id") for key in keys)
        row["possible_duplicate"] = duplicate
        for key in keys:
            seen.setdefault(key, row.get("item_id"))
        row["classification"] = classification if classification in CLASSES else (
            "Unclassified" if duplicate else default_classification(row)
        )
        row["stale"] = bool(item.get("error"))
        row["item_label"] = item.get("label") or "Linked institution"
        rows.append(row)
    return rows


def balance_sheet(accounts):
    totals = {}
    for account in accounts:
        classification = account.get("classification", default_classification(account))
        if classification == "Excluded":
            continue
        currency = str(account.get("currency") or "UNKNOWN")
        row = totals.setdefault(currency, {"currency": currency, "assets": Decimal(0),
                                           "liabilities": Decimal(0), "equity": Decimal(0), "missing": 0})
        value = _number(account.get("current"))
        if value is None or currency == "UNKNOWN" or classification not in ("Asset", "Liability", "Equity"):
            row["missing"] += 1
            continue
        key = {"Asset": "assets", "Liability": "liabilities", "Equity": "equity"}[classification]
        row[key] += value
    result = []
    for currency in sorted(totals):
        row = totals[currency]
        row["net_worth"] = row["assets"] - row["liabilities"]
        row["reconciliation"] = row["net_worth"] - row["equity"]
        result.append({key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()})
    return result


def scheduled_slot(now=None):
    local = _now(now).astimezone(TIMEZONE)
    due = [hour for hour in SYNC_HOURS if hour <= local.hour]
    return local.replace(hour=due[-1], minute=0, second=0, microsecond=0).isoformat() if due else None


def sync_networth(store, client, now=None, scheduled_slot=None):
    """Read all linked Items, archive the observation, then CAS its checkpoint."""
    now = _now(now)
    captured_at = _iso(now)
    date = now.astimezone(TIMEZONE).date().isoformat()
    initial = store.read_state()
    environment = getattr(getattr(client, "config", None), "environment", None)
    if environment and initial.get("plaid_environment") not in (None, "", environment):
        raise NetworthError("PLAID_LEDGER_ENVIRONMENT_MISMATCH")
    if scheduled_slot and scheduled_slot in initial.get("completed_slots", {}):
        return initial
    if scheduled_slot:
        try:
            slot_time = datetime.fromisoformat(scheduled_slot)
            local_slot = _now(slot_time).astimezone(TIMEZONE)
            if (local_slot.date().isoformat() != date or local_slot.hour not in SYNC_HOURS
                    or any((local_slot.minute, local_slot.second, local_slot.microsecond)) or slot_time > now):
                raise ValueError
        except (ValueError, TypeError, NetworthError):
            raise NetworthError("NETWORTH_SCHEDULE_SLOT_INVALID") from None
        day = store.read_day(date) or {}
        captures = day.get("captures", {})
        recoverable = [(capture_id, capture) for capture_id, capture in captures.items()
                       if capture.get("scheduled_slot") == scheduled_slot and capture.get("complete")
                       and {row.get("item_id") for row in capture.get("accounts", [])} == set(initial.get("items", {}))]
        if recoverable:
            capture_id, capture = max(recoverable, key=lambda pair: pair[1]["captured_at"])

            def recover(latest):
                if environment and latest.get("plaid_environment") not in (None, "", environment):
                    raise NetworthError("PLAID_LEDGER_ENVIRONMENT_MISMATCH")
                if set(latest.get("items", {})) != set(initial.get("items", {})) or any(
                    latest["items"][key].get("access_token") != item.get("access_token")
                    for key, item in initial.get("items", {}).items()
                ):
                    return latest
                if not _newer(latest.get("last_sync"), datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00"))):
                    latest["accounts"] = {key: row for key, row in latest.get("accounts", {}).items()
                                          if row.get("item_id") not in latest["items"]}
                    for row in capture["accounts"]:
                        account = {key: value for key, value in row.items()
                                   if key not in ("classification", "stale", "item_label", "possible_duplicate")}
                        latest.setdefault("accounts", {})[row["account_id"]] = account
                    for item in latest["items"].values():
                        item.update({"last_sync": capture["captured_at"], "last_attempt": capture["captured_at"], "error": None})
                    latest.update({"last_capture_at": capture["captured_at"], "last_capture_date": date,
                                   "last_capture_id": capture_id, "last_capture_complete": True,
                                   "last_sync": capture["captured_at"], "last_errors": {}})
                latest.setdefault("completed_slots", {})[scheduled_slot] = capture["captured_at"]
                if not _newer(latest.get("last_scheduled_at"), slot_time):
                    latest["last_scheduled_slot"] = scheduled_slot
                    latest["last_scheduled_at"] = capture["captured_at"]
                return latest

            return store.update_state(recover)
    fetched = {}
    for item_id, item in initial.get("items", {}).items():
        try:
            fetched[item_id] = {"accounts": normalize_accounts(client.balances(item["access_token"], now=now), item_id, now)}
        except NetworthError as exc:
            # Only our own short error codes may enter persisted state.
            code = str(exc)
            fetched[item_id] = {"error": code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", code) else "PLAID_SYNC_FAILED"}
        except Exception:
            fetched[item_id] = {"error": "PLAID_SYNC_FAILED"}

    def apply(latest):
        if environment and latest.get("plaid_environment") not in (None, "", environment):
            raise NetworthError("PLAID_LEDGER_ENVIRONMENT_MISMATCH")
        if scheduled_slot and scheduled_slot in latest.get("completed_slots", {}):
            return latest
        # A later complete batch already observed all Items. Do not let a slow
        # older request roll balances backwards or create a backdated capture.
        if _newer(latest.get("last_sync"), now):
            return latest
        errors = {}
        for item_id, item in latest.get("items", {}).items():
            original = initial.get("items", {}).get(item_id)
            if original is None or original.get("access_token") != item.get("access_token"):
                errors[item_id] = "ITEM_NOT_SYNCED"
                item["error"] = "ITEM_NOT_SYNCED"
                continue
            if _newer(item.get("last_sync"), now) or _newer(item.get("last_attempt"), now):
                if item.get("error"):
                    errors[item_id] = item["error"]
                continue
            observed = fetched[item_id]
            item["last_attempt"] = captured_at
            if observed.get("error"):
                errors[item_id] = observed["error"]
                item["error"] = observed["error"]
                continue
            item["error"] = None
            item["last_sync"] = captured_at
            # Only a valid nonempty response can retire missing current rows.
            previous = latest.setdefault("accounts", {})
            for account_id in list(previous):
                if previous[account_id].get("item_id") == item_id:
                    del previous[account_id]
            previous.update({row["account_id"]: deepcopy(row) for row in observed["accounts"]})
        rows = account_rows(latest)
        totals = balance_sheet(rows)
        complete = bool(latest.get("items")) and not errors and all(
            row["current"] is not None and row["currency"] != "UNKNOWN"
            for row in rows if row["classification"] != "Excluded"
        )
        capture_id = now.strftime("%H%M%S%f") + "-" + uuid.uuid4().hex
        capture = {"captured_at": captured_at, "scheduled_slot": scheduled_slot,
                   "accounts": rows, "totals": totals, "errors": errors, "complete": complete}
        store.save_capture(date, capture_id, capture)
        latest.update({"last_capture_at": captured_at, "last_capture_date": date,
                       "last_capture_id": capture_id, "last_capture_complete": complete,
                       "last_sync": captured_at, "last_errors": errors})
        if scheduled_slot and complete:
            latest.setdefault("completed_slots", {})[scheduled_slot] = captured_at
            latest["last_scheduled_slot"] = scheduled_slot
            latest["last_scheduled_at"] = captured_at
        return latest

    return store.update_state(apply)
