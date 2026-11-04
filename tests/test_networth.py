"""Offline Networth regressions: Plaid flow, signed balances, and safe snapshots."""

from __future__ import annotations

from collections import UserDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

import requests

from src.networth import (
    CLASSES, TIMEZONE, PlaidClient, PlaidConfig, NetworthError, account_rows,
    balance_sheet, default_classification, normalize_accounts, scheduled_slot,
    sync_networth,
)


NOW = datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)


def account(account_id="cash", value=100, kind="depository", currency="USD", **extra):
    return {"account_id": account_id, "name": account_id, "mask": "5474", "type": kind,
            "subtype": "checking", "balances": {"current": value, "available": 7,
            "iso_currency_code": currency, **extra}}


class MemoryStore:
    def __init__(self, state=None):
        self.state = deepcopy(state or {"version": 1, "items": {}, "accounts": {},
                                       "preferences": {}, "pending_links": {}})
        self.days = {}
        self.events = []
        self.before_update = None
        self.fail_capture = False
        self.fail_checkpoint = False

    def read_state(self):
        return deepcopy(self.state)

    def update_state(self, callback):
        if self.before_update and self.state.get("sync_lease"):
            hook, self.before_update = self.before_update, None
            hook(self.state)
        current = deepcopy(self.state)
        result = callback(current)
        self.events.append("state")
        if self.fail_checkpoint and current.get("last_capture_at"):
            raise NetworthError("CHECKPOINT_UNAVAILABLE")
        self.state = deepcopy(result if result is not None else current)
        return deepcopy(self.state)

    def save_capture(self, date, capture_id, capture):
        if self.fail_capture:
            raise NetworthError("ARCHIVE_UNAVAILABLE")
        self.events.append("capture")
        day = self.days.setdefault(date, {"version": 1, "captures": {}})
        if capture_id in day["captures"] and day["captures"][capture_id] != capture:
            raise NetworthError("CAPTURE_IMMUTABLE")
        day["captures"][capture_id] = deepcopy(capture)

    def read_day(self, date):
        return deepcopy(self.days.get(date))


class Client:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def balances(self, token, now=None):
        self.calls.append(token)
        result = self.replies[token]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def linked_store():
    store = MemoryStore()
    store.state["items"] = {"bank": {"access_token": "SENSITIVE_BANK_TOKEN", "label": "Bank"}}
    return store


class PlaidTests(unittest.TestCase):
    def setUp(self):
        self.config = PlaidConfig("SENSITIVE_CLIENT_ID", "SENSITIVE_SECRET")
        self.session = Mock()
        self.client = PlaidClient(self.config, self.session)

    def reply(self, value, status=200):
        self.session.post.return_value = Mock(status_code=status)
        self.session.post.return_value.json.return_value = value

    def test_nested_mapping_secrets_override_environment_without_leaking_repr(self):
        secrets = UserDict({"networth": UserDict({"plaid_client_id": "SECRET_ID",
            "plaid_secret": "SECRET_VALUE", "plaid_environment": "sandbox",
            "plaid_products": ["investments"]})})
        with patch.dict("os.environ", {"NETWORTH_PLAID_CLIENT_ID": "WRONG_ID",
                                     "NETWORTH_PLAID_SECRET": "WRONG_SECRET"}, clear=True):
            config = PlaidConfig.from_runtime(secrets)
        self.assertEqual(config.client_id, "SECRET_ID")
        self.assertEqual(config.secret, "SECRET_VALUE")
        self.assertEqual(config.environment, "sandbox")
        self.assertEqual(config.products, ("investments",))
        for secret in ("SECRET_ID", "SECRET_VALUE"):
            self.assertNotIn(secret, repr(config))

    def test_environment_fallback_and_explicit_blank_secret_are_not_shadowed(self):
        with patch.dict("os.environ", {"NETWORTH_PLAID_CLIENT_ID": "ENV_ID",
                "NETWORTH_PLAID_SECRET": "ENV_SECRET", "NETWORTH_PLAID_PRODUCTS": "transactions,liabilities"}, clear=True):
            config = PlaidConfig.from_runtime({})
            self.assertEqual(config.products, ("transactions", "liabilities"))
            with self.assertRaisesRegex(NetworthError, "CREDENTIALS_INCOMPLETE"):
                PlaidConfig.from_runtime({"networth": {"plaid_secret": ""}})

    def test_missing_and_invalid_configuration_are_safe(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(PlaidConfig.from_runtime({}))
        for kwargs in ({"environment": "https://host"}, {"products": ("balance",)}, {"products": ()}):
            with self.subTest(kwargs=kwargs), self.assertRaises(NetworthError) as error:
                PlaidConfig("SENSITIVE_ID", "SENSITIVE_SECRET", **kwargs)
            self.assertNotIn("SENSITIVE", str(error.exception))

    def test_hosted_link_create_uses_only_configured_products(self):
        self.reply({"hosted_link_url": "https://secure.plaid.com/hl/example", "link_token": "link-example", "expiration": "later"})
        result = self.client.create_link("opaque-ledger-id")
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["products"], ["transactions"])
        self.assertEqual(body["hosted_link"], {})
        self.assertEqual(body["country_codes"], ["US"])
        self.assertNotIn("optional_products", body)
        self.assertEqual(result["link_token"], "link-example")

    def test_update_link_omits_initial_products(self):
        self.reply({"hosted_link_url": "https://secure.plaid.com/hl/example", "link_token": "link-example"})
        self.client.create_link("user", "existing-access-token")
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["access_token"], "existing-access-token")
        self.assertNotIn("products", body)

    def test_investment_and_liability_connection_products_override_bank_default(self):
        self.reply({"hosted_link_url": "https://secure.plaid.com/hl/example", "link_token": "link-example"})
        for products in (["investments"], ("liabilities",)):
            self.client.create_link("user", products=products)
            self.assertEqual(self.session.post.call_args.kwargs["json"]["products"], list(products))
        with self.assertRaisesRegex(NetworthError, "PRODUCTS_INVALID"):
            self.client.create_link("user", products=["balance"])

    def test_hosted_nested_results_and_legacy_success_are_deduplicated(self):
        self.reply({"link_sessions": [{"results": {"item_add_results": [
            {"public_token": "public-one", "institution": {"name": "One"}, "accounts": []},
            {"public_token": "public-two", "institution": {"name": "Two"}, "accounts": []}]},
            "on_success": {"public_token": "public-one", "metadata": {}}}]})
        result = self.client.link_results("link-example")
        self.assertEqual(result["public_tokens"], ["public-one", "public-two"])
        self.assertEqual(result["items"][0]["institution"]["name"], "One")
        self.assertTrue(result["completed"])

    def test_link_exit_is_not_success_and_update_success_needs_no_token(self):
        self.reply({"link_sessions": [{"finished_at": "later", "on_exit": {"error": None}}]})
        result = self.client.link_results("link-example")
        self.assertFalse(result["completed"])
        self.assertTrue(result["exited"])
        self.reply({"link_sessions": [{"on_success": {"metadata": {}}}]})
        self.assertTrue(self.client.link_results("link-example")["completed"])

    def test_exchange_and_explicit_remove_transport(self):
        self.reply({"item_id": "bank", "access_token": "SENSITIVE_ACCESS"})
        self.assertEqual(self.client.exchange("public-example")["item_id"], "bank")
        self.assertTrue(self.session.post.call_args.args[0].endswith("/item/public_token/exchange"))
        self.reply({"request_id": "request"})
        self.client.remove("SENSITIVE_ACCESS")
        self.assertTrue(self.session.post.call_args.args[0].endswith("/item/remove"))

    def test_balance_requests_fresh_endpoint_and_explicit_cached_freshness_bound(self):
        self.reply({"accounts": [account()]})
        self.client.balances("SENSITIVE_ACCESS", NOW)
        self.assertTrue(self.session.post.call_args.args[0].endswith("/accounts/balance/get"))
        body = self.session.post.call_args.kwargs["json"]
        self.assertEqual(body["options"]["min_last_updated_datetime"], "2026-11-02T17:00:00Z")

    def test_api_error_emits_code_only_and_request_errors_are_redacted(self):
        self.reply({"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "SENSITIVE_SECRET",
                    "access_token": "SENSITIVE_ACCESS"}, 400)
        with self.assertRaisesRegex(NetworthError, "^PLAID_ITEM_LOGIN_REQUIRED$"):
            self.client.balances("SENSITIVE_ACCESS", NOW)
        self.reply({"error_code": "not-safe-SENSITIVE_SECRET"}, 500)
        with self.assertRaisesRegex(NetworthError, "^PLAID_HTTP_500$"):
            self.client.balances("SENSITIVE_ACCESS", NOW)
        self.session.post.side_effect = requests.ConnectionError("SENSITIVE_SECRET in request")
        with self.assertRaisesRegex(NetworthError, "^PLAID_CONNECTION_FAILED$"):
            self.client.balances("SENSITIVE_ACCESS", NOW)


class BalanceTests(unittest.TestCase):
    def test_current_investment_value_nulls_signed_liabilities_and_currency(self):
        rows = normalize_accounts({"accounts": [account("broker", 1200, "investment"),
            account("missing", None, "investment"), account("credit", -25, "credit"),
            account("loan", 60, "loan", "CAD")]}, "bank", NOW)
        self.assertEqual(rows[0]["current"], 1200)
        self.assertIsNone(rows[1]["current"])
        self.assertEqual(rows[2]["current"], -25)
        self.assertEqual(rows[3]["currency"], "CAD")
        self.assertEqual(rows[0]["as_of"], "2026-11-03T17:00:00Z")

    def test_invalid_numbers_are_missing_and_institution_timestamp_is_retained(self):
        rows = normalize_accounts({"accounts": [account("nan", float("nan")), account("infinity", float("inf")),
            account("known", 3, last_updated_datetime="2026-11-03T10:00:00Z")]}, "bank", NOW)
        self.assertIsNone(rows[0]["current"])
        self.assertIsNone(rows[1]["current"])
        self.assertEqual(rows[2]["balance_updated_at"], "2026-11-03T10:00:00Z")

    def test_default_types_and_persisted_user_classification(self):
        self.assertEqual(default_classification({"type": "other"}), "Unclassified")
        self.assertEqual(default_classification({"type": "loan"}), "Liability")
        state = {"accounts": {"cash": normalize_accounts({"accounts": [account()]}, "bank", NOW)[0]},
                 "preferences": {"cash": {"classification": "Equity"}},
                 "items": {"bank": {"error": "PLAID_ITEM_LOGIN_REQUIRED", "label": "Bank"}}}
        rows = account_rows(state)
        self.assertEqual(rows[0]["classification"], "Equity")
        self.assertTrue(rows[0]["stale"])
        rows[0]["current"] = 999
        self.assertEqual(state["accounts"]["cash"]["current"], 100)
        self.assertIn("Excluded", CLASSES)

    def test_multi_currency_equity_reconciliation_and_signed_credits(self):
        rows = [{"currency": "USD", "current": 100, "classification": "Asset"},
                {"currency": "USD", "current": -20, "classification": "Liability"},
                {"currency": "USD", "current": 110, "classification": "Equity"},
                {"currency": "CAD", "current": 50, "classification": "Asset"},
                {"currency": "USD", "current": None, "classification": "Liability"},
                {"currency": "USD", "current": 999, "classification": "Excluded"}]
        result = {row["currency"]: row for row in balance_sheet(rows)}
        self.assertEqual(result["USD"], {"currency": "USD", "assets": 100.0, "liabilities": -20.0,
            "equity": 110.0, "net_worth": 120.0, "reconciliation": 10.0, "missing": 1})
        self.assertEqual(result["CAD"]["net_worth"], 50)

    def test_unknown_classification_and_missing_currency_are_not_silently_assets(self):
        rows = [{"current": 25, "classification": "Unclassified"}]
        self.assertEqual(balance_sheet(rows)[0]["assets"], 0)
        self.assertEqual(balance_sheet(rows)[0]["missing"], 1)
        self.assertEqual(balance_sheet(rows)[0]["currency"], "UNKNOWN")
        unknown_currency = balance_sheet([{"current": 500, "classification": "Asset", "currency": "UNKNOWN"}])[0]
        self.assertEqual(unknown_currency["assets"], 0)
        self.assertEqual(unknown_currency["missing"], 1)

    def test_duplicates_across_items_default_unclassified_until_explicitly_selected(self):
        first = normalize_accounts({"accounts": [account("first")]}, "bank", NOW)[0]
        second = {**first, "account_id": "second", "item_id": "duplicate-bank"}
        state = {"accounts": {"second": second, "first": first}, "preferences": {}, "items": {
            "bank": {"institution_id": "ins_test", "linked_at": "2026-11-01T10:00:00Z"},
            "duplicate-bank": {"institution_id": "ins_test", "linked_at": "2026-11-02T10:00:00Z"}}}
        rows = {row["account_id"]: row for row in account_rows(state)}
        self.assertEqual(rows["first"]["classification"], "Asset")
        self.assertTrue(rows["second"]["possible_duplicate"])
        self.assertEqual(rows["second"]["classification"], "Unclassified")
        self.assertEqual(balance_sheet(rows.values())[0]["assets"], 100)
        state["preferences"]["second"] = {"classification": "Asset"}
        self.assertEqual(balance_sheet(account_rows(state))[0]["assets"], 200)

    def test_persistent_duplicate_id_is_preserved_and_mask_alone_is_not_identity(self):
        first = account("first")
        first["persistent_account_id"] = "persistent-id"
        original = normalize_accounts({"accounts": [first]}, "bank", NOW)[0]
        duplicate = {**original, "account_id": "duplicate", "item_id": "second-bank", "name": "Changed name"}
        unrelated = {**original, "account_id": "unrelated", "item_id": "third-bank", "persistent_account_id": None}
        state = {"accounts": {"first": original, "duplicate": duplicate, "unrelated": unrelated}, "items": {
            "bank": {"linked_at": "1"}, "second-bank": {"linked_at": "2"}, "third-bank": {"linked_at": "3"}}}
        rows = {row["account_id"]: row for row in account_rows(state)}
        self.assertEqual(original["persistent_account_id"], "persistent-id")
        self.assertTrue(rows["duplicate"]["possible_duplicate"])
        self.assertFalse(rows["unrelated"]["possible_duplicate"])

    def test_fallback_detects_duplicate_when_only_one_item_has_persistent_id(self):
        original = normalize_accounts({"accounts": [account("first")]}, "bank", NOW)[0]
        original["persistent_account_id"] = "persistent-id"
        duplicate = {**original, "account_id": "second", "item_id": "second-bank", "persistent_account_id": None}
        state = {"accounts": {"first": original, "second": duplicate}, "items": {
            "bank": {"institution_id": "ins_test", "linked_at": "1"},
            "second-bank": {"institution_id": "ins_test", "linked_at": "2"}}}
        rows = {row["account_id"]: row for row in account_rows(state)}
        self.assertFalse(rows["first"]["possible_duplicate"])
        self.assertTrue(rows["second"]["possible_duplicate"])
        self.assertEqual(rows["second"]["classification"], "Unclassified")
        self.assertEqual(balance_sheet(rows.values())[0]["assets"], 100)


class SyncTests(unittest.TestCase):
    def setUp(self):
        clock = patch("src.networth._sync_time", return_value=NOW)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def test_success_captures_before_checkpoint_and_daily_history_is_immutable(self):
        store = linked_store()
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        first = sync_networth(store, client, NOW)
        first_day = store.read_day("2026-11-03")
        first_capture = deepcopy(first_day["captures"][first["last_capture_id"]])
        self.assertEqual(store.events, ["state", "capture", "state"])
        self.assertTrue(first_capture["complete"])
        self.assertNotIn("SENSITIVE_BANK_TOKEN", repr(first_capture))
        client.replies["SENSITIVE_BANK_TOKEN"] = {"accounts": [account(value=150)]}
        second = sync_networth(store, client, NOW + timedelta(hours=1))
        captures = store.read_day("2026-11-03")["captures"]
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[first["last_capture_id"]], first_capture)
        self.assertNotEqual(first["last_capture_id"], second["last_capture_id"])

    def test_partial_failure_retains_last_good_balance_and_does_not_complete_slot(self):
        store = linked_store()
        store.state["items"]["card"] = {"access_token": "CARD", "label": "Card"}
        prior = normalize_accounts({"accounts": [account("card-row", 45, "credit")]}, "card", NOW - timedelta(days=1))[0]
        store.state["accounts"]["card-row"] = prior
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]},
                         "CARD": NetworthError("PLAID_ITEM_LOGIN_REQUIRED")})
        slot = scheduled_slot(NOW)
        result = sync_networth(store, client, NOW, slot)
        self.assertEqual(result["accounts"]["card-row"], prior)
        self.assertTrue(next(row for row in account_rows(result) if row["account_id"] == "card-row")["stale"])
        self.assertNotIn(slot, result.get("completed_slots", {}))
        capture = store.read_day(result["last_capture_date"])["captures"][result["last_capture_id"]]
        self.assertFalse(capture["complete"])
        self.assertEqual(capture["errors"], {"card": "PLAID_ITEM_LOGIN_REQUIRED"})

    def test_empty_response_preserves_accounts_and_exception_details_are_not_persisted(self):
        store = linked_store()
        store.state["accounts"]["cash"] = normalize_accounts({"accounts": [account()]}, "bank", NOW)[0]
        for reply in ({"accounts": []}, RuntimeError("SENSITIVE_BANK_TOKEN")):
            result = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": reply}), NOW)
            self.assertEqual(result["accounts"]["cash"]["current"], 100)
            self.assertTrue(result["items"]["bank"]["error"])
            self.assertNotIn("SENSITIVE_BANK_TOKEN", repr(result["last_errors"]))

    def test_null_does_not_complete_slot_and_never_becomes_available_cash(self):
        store = linked_store()
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=None, kind="investment")]}})
        result = sync_networth(store, client, NOW, scheduled_slot(NOW))
        self.assertIsNone(result["accounts"]["cash"]["current"])
        self.assertFalse(result.get("completed_slots"))

    def test_concurrent_preferences_pending_links_and_added_items_are_preserved(self):
        store = linked_store()

        def concurrent(state):
            state["preferences"]["cash"] = {"classification": "Equity"}
            state["pending_links"]["new-link"] = {"link_token": "LINK_SECRET"}
            state["items"]["new-bank"] = {"access_token": "NEW_SECRET", "label": "New bank"}

        store.before_update = concurrent
        result = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}}), NOW, scheduled_slot(NOW))
        self.assertEqual(result["preferences"]["cash"]["classification"], "Equity")
        self.assertIn("new-link", result["pending_links"])
        self.assertIn("new-bank", result["items"])
        self.assertEqual(result["last_errors"]["new-bank"], "ITEM_NOT_SYNCED")
        self.assertFalse(result.get("completed_slots"))
        capture = store.read_day(result["last_capture_date"])["captures"][result["last_capture_id"]]
        self.assertEqual(capture["totals"][0]["equity"], 100)
        self.assertNotIn("LINK_SECRET", repr(capture))

    def test_concurrent_disconnect_or_replaced_token_is_never_resurrected(self):
        for mode in ("remove", "replace"):
            with self.subTest(mode=mode):
                store = linked_store()

                def concurrent(state):
                    if mode == "remove":
                        state["items"].pop("bank")
                    else:
                        state["items"]["bank"]["access_token"] = "REPLACEMENT_SECRET"

                store.before_update = concurrent
                result = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}}), NOW)
                self.assertFalse(result["accounts"])
                if mode == "remove":
                    self.assertNotIn("bank", result["items"])
                else:
                    self.assertEqual(result["items"]["bank"]["access_token"], "REPLACEMENT_SECRET")

    def test_successful_slot_dedupes_without_network_but_partial_slot_retries(self):
        store = linked_store()
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        slot = scheduled_slot(NOW)
        first = sync_networth(store, client, NOW, slot)
        self.assertEqual(first["last_scheduled_slot"], slot)
        self.assertEqual(sync_networth(store, client, NOW, slot), first)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(store.days["2026-11-03"]["captures"]), 1)

    def test_winter_and_summer_fallback_attempts_share_one_successful_pull(self):
        for month in (1, 7):
            with self.subTest(month=month):
                store = linked_store()
                client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
                slots = []
                for minute in (0, 10, 20):
                    stamp = datetime(2026, month, 15, 9, minute, tzinfo=TIMEZONE)
                    slots.append(scheduled_slot(stamp))
                    sync_networth(store, client, stamp, slots[-1])
                self.assertEqual(len(set(slots)), 1)
                self.assertEqual(client.calls, ["SENSITIVE_BANK_TOKEN"])
                self.assertEqual(len(store.days[f"2026-{month:02d}-15"]["captures"]), 1)

    def test_successful_capture_recovers_failed_checkpoint_without_another_paid_pull(self):
        store = linked_store()
        store.state["accounts"]["closed"] = normalize_accounts({"accounts": [account("closed")]}, "bank", NOW)[0]
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        slot = scheduled_slot(NOW)
        store.fail_checkpoint = True
        with self.assertRaisesRegex(NetworthError, "CHECKPOINT_UNAVAILABLE"):
            sync_networth(store, client, NOW, slot)
        before = store.read_day("2026-11-03")
        self.assertFalse(store.state.get("completed_slots"))
        store.fail_checkpoint = False
        store.state["preferences"]["cash"] = {"classification": "Equity"}
        result = sync_networth(store, client, NOW + timedelta(minutes=1), slot)
        self.assertEqual(client.calls, ["SENSITIVE_BANK_TOKEN"])
        self.assertEqual(store.read_day("2026-11-03"), before)
        self.assertIn(slot, result["completed_slots"])
        self.assertEqual(result["accounts"]["cash"]["current"], 100)
        self.assertNotIn("closed", result["accounts"])
        self.assertEqual(result["preferences"]["cash"]["classification"], "Equity")

    def test_checkpoint_recovery_preserves_original_snapshot_after_reclassification(self):
        store = linked_store()
        store.state["preferences"]["cash"] = {"classification": "Excluded"}
        store.fail_checkpoint = True
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=None)]}})
        slot = scheduled_slot(NOW)
        with self.assertRaisesRegex(NetworthError, "CHECKPOINT_UNAVAILABLE"):
            sync_networth(store, client, NOW, slot)
        original = store.read_day("2026-11-03")
        store.fail_checkpoint = False
        store.state["preferences"]["cash"] = {"classification": "Asset"}
        result = sync_networth(store, client, NOW + timedelta(minutes=1), slot)
        self.assertEqual(client.calls, ["SENSITIVE_BANK_TOKEN"])
        self.assertIn(slot, result["completed_slots"])
        self.assertTrue(result["last_capture_complete"])
        self.assertEqual(store.read_day("2026-11-03"), original)
        saved = next(iter(original["captures"].values()))
        self.assertEqual(saved["accounts"][0]["classification"], "Excluded")
        self.assertEqual(account_rows(result)[0]["classification"], "Asset")
        self.assertIsNone(account_rows(result)[0]["current"])

    def test_overlap_is_rejected_before_a_second_paid_pull(self):
        for first_slot, second_slot in ((None, None), (scheduled_slot(NOW), scheduled_slot(NOW)),
                                       (scheduled_slot(NOW), None)):
            with self.subTest(first_slot=first_slot, second_slot=second_slot):
                store = linked_store()
                contender = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=200)]}})
                client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=100)]}})
                original = client.balances

                def overlap(token, now=None):
                    with self.assertRaisesRegex(NetworthError, "^NETWORTH_SYNC_IN_PROGRESS$"):
                        sync_networth(store, contender, NOW + timedelta(seconds=1), second_slot)
                    return original(token, now)

                client.balances = overlap
                result = sync_networth(store, client, NOW, first_slot)
                self.assertEqual(client.calls, ["SENSITIVE_BANK_TOKEN"])
                self.assertEqual(contender.calls, [])
                self.assertEqual(result["accounts"]["cash"]["current"], 100)
                self.assertNotIn("sync_lease", result)
                self.assertEqual(len(store.read_day("2026-11-03")["captures"]), 1)

    def test_expired_owner_cannot_overwrite_a_successor_or_release_its_claim(self):
        store = linked_store()
        old_client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=100)]}})
        new_client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=200)]}})
        newer = {}

        def newer_finishes_first(state):
            self.clock.return_value = NOW + timedelta(minutes=16)
            newer.update(sync_networth(store, new_client, NOW + timedelta(minutes=1)))

        store.before_update = newer_finishes_first
        with self.assertRaisesRegex(NetworthError, "^NETWORTH_SYNC_LEASE_LOST$"):
            sync_networth(store, old_client, NOW)
        self.assertEqual(store.state, newer)
        self.assertEqual(store.state["accounts"]["cash"]["current"], 200)
        self.assertEqual(store.state["last_capture_at"], "2026-11-03T17:01:00Z")
        self.assertEqual(len(store.read_day("2026-11-03")["captures"]), 1)

    def test_existing_newer_success_is_not_downloaded_or_replaced(self):
        store = linked_store()
        current = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=200)]}}),
                                NOW + timedelta(minutes=1))
        old_client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=100)]}})
        self.assertEqual(sync_networth(store, old_client, NOW), current)
        self.assertEqual(old_client.calls, [])
        self.assertEqual(len(store.read_day("2026-11-03")["captures"]), 1)

    def test_older_success_repairs_balance_after_a_newer_failed_attempt(self):
        store = linked_store()
        sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=40)]}}),
                      NOW - timedelta(minutes=1))
        failed = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": NetworthError("PLAID_CONNECTION_FAILED")}),
                              NOW + timedelta(minutes=1))
        result = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(value=100)]}}), NOW)
        self.assertEqual(result["accounts"]["cash"]["current"], 100)
        self.assertEqual(result["items"]["bank"]["last_sync"], "2026-11-03T17:00:00Z")
        self.assertEqual(result["items"]["bank"]["last_attempt"], "2026-11-03T17:01:00Z")
        self.assertEqual(result["items"]["bank"]["error"], "PLAID_CONNECTION_FAILED")
        self.assertTrue(account_rows(result)[0]["stale"])
        self.assertEqual(result["last_sync"], failed["last_sync"])
        self.assertEqual(result["last_capture_id"], failed["last_capture_id"])

    def test_crashed_lease_expires_and_backdated_snapshot_uses_real_lease_clock(self):
        store = linked_store()
        store.state["sync_lease"] = {"owner": "crashed", "expires_at": "2026-11-03T16:59:59Z"}
        self.clock.return_value = NOW + timedelta(days=1)
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        original = client.balances

        def inspect_lease(token, now=None):
            expires = datetime.fromisoformat(store.state["sync_lease"]["expires_at"].replace("Z", "+00:00"))
            self.assertEqual(expires, NOW + timedelta(days=1, minutes=15))
            return original(token, now)

        client.balances = inspect_lease
        result = sync_networth(store, client, NOW)
        self.assertNotIn("sync_lease", result)
        self.assertEqual(client.calls, ["SENSITIVE_BANK_TOKEN"])

    def test_lease_expiry_stops_remaining_paid_reads_and_releases_claim(self):
        store = linked_store()
        store.state["items"]["second"] = {"access_token": "SECOND"}
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]},
                         "SECOND": {"accounts": [account("second")]}})
        original = client.balances

        def expire(token, now=None):
            result = original(token, now)
            self.clock.return_value = NOW + timedelta(minutes=15)
            return result

        client.balances = expire
        with self.assertRaisesRegex(NetworthError, "^NETWORTH_SYNC_LEASE_EXPIRED$"):
            sync_networth(store, client, NOW)
        self.assertEqual(client.calls, ["SENSITIVE_BANK_TOKEN"])
        self.assertNotIn("sync_lease", store.state)
        self.assertFalse(store.days)

    def test_unverified_claim_write_never_performs_paid_reads_and_is_released(self):
        store = linked_store()
        original = store.update_state
        first = True

        def uncertain(callback):
            nonlocal first
            result = original(callback)
            if first:
                first = False
                raise NetworthError("CLAIM_VERIFICATION_FAILED")
            return result

        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        with patch.object(store, "update_state", side_effect=uncertain):
            with self.assertRaisesRegex(NetworthError, "^CLAIM_VERIFICATION_FAILED$"):
                sync_networth(store, client, NOW)
        self.assertEqual(client.calls, [])
        self.assertNotIn("sync_lease", store.state)

    def test_expiry_during_archive_preserves_capture_for_checkpoint_recovery(self):
        store = linked_store()
        original = store.save_capture

        def slow_archive(day, capture_id, capture):
            original(day, capture_id, capture)
            self.clock.return_value = NOW + timedelta(minutes=15)

        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        slot = scheduled_slot(NOW)
        with patch.object(store, "save_capture", side_effect=slow_archive):
            with self.assertRaisesRegex(NetworthError, "^NETWORTH_SYNC_LEASE_EXPIRED$"):
                sync_networth(store, client, NOW, slot)
        self.assertNotIn("sync_lease", store.state)
        self.assertFalse(store.state.get("completed_slots"))
        self.assertFalse(store.state["accounts"])
        before = store.read_day("2026-11-03")
        self.clock.return_value = NOW + timedelta(minutes=16)
        result = sync_networth(store, client, NOW + timedelta(minutes=16), slot)
        self.assertIn(slot, result["completed_slots"])
        self.assertEqual(client.calls, ["SENSITIVE_BANK_TOKEN"])
        self.assertEqual(before, store.read_day("2026-11-03"))

    def test_release_error_preserves_archive_failure_and_claim_expires(self):
        store = linked_store()
        store.fail_capture = True
        original = store.update_state

        def release_fails(callback):
            if callback.__name__ == "release":
                raise NetworthError("RELEASE_UNAVAILABLE")
            return original(callback)

        with patch.object(store, "update_state", side_effect=release_fails):
            with self.assertRaisesRegex(NetworthError, "^ARCHIVE_UNAVAILABLE$"):
                sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}}), NOW)
        self.assertIn("sync_lease", store.state)
        store.fail_capture = False
        self.clock.return_value = NOW + timedelta(minutes=16)
        self.assertNotIn("sync_lease", sync_networth(store,
            Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}}), NOW + timedelta(minutes=16)))

    def test_owner_lost_during_claim_cas_retry_preserves_competing_lease(self):
        store = linked_store()
        original = store.update_state
        competing = {"owner": "other-worker", "expires_at": "2026-11-03T17:15:00Z"}

        def retry(callback):
            if callback.__name__ == "claim":
                callback(deepcopy(store.state))
                store.state["sync_lease"] = deepcopy(competing)
            return original(callback)

        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        with patch.object(store, "update_state", side_effect=retry):
            with self.assertRaisesRegex(NetworthError, "^NETWORTH_SYNC_IN_PROGRESS$"):
                sync_networth(store, client, NOW)
        self.assertEqual(client.calls, [])
        self.assertEqual(store.state["sync_lease"], competing)

    def test_unknown_currency_incomplete_and_unclassified_known_balance_is_data_complete(self):
        store = linked_store()
        slot = scheduled_slot(NOW)
        result = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(currency=None)]}}), NOW, slot)
        self.assertNotIn(slot, result.get("completed_slots", {}))
        result = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account(kind="other")]}}), NOW, slot)
        self.assertIn(slot, result["completed_slots"])
        self.assertEqual(balance_sheet(account_rows(result))[0]["missing"], 1)

    def test_environment_mismatch_refuses_token_use(self):
        store = linked_store()
        store.state["plaid_environment"] = "sandbox"
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        client.config = PlaidConfig("SECRET_ID", "SECRET_VALUE", "production")
        with self.assertRaisesRegex(NetworthError, "ENVIRONMENT_MISMATCH"):
            sync_networth(store, client, NOW)
        self.assertEqual(client.calls, [])
        self.assertEqual(store.events, [])

    def test_backdated_or_future_schedule_slots_are_rejected(self):
        store = linked_store()
        client = Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}})
        for slot in ("2026-11-02T09:00:00-05:00", "2026-11-03T17:00:00-05:00", "invalid"):
            with self.subTest(slot=slot), self.assertRaisesRegex(NetworthError, "SCHEDULE_SLOT_INVALID"):
                sync_networth(store, client, NOW, slot)
        self.assertEqual(client.calls, [])

    def test_failed_capture_does_not_advance_state_or_slot(self):
        store = linked_store()
        before = store.read_state()
        store.fail_capture = True
        with self.assertRaises(NetworthError):
            sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}}), NOW, scheduled_slot(NOW))
        self.assertEqual(store.read_state(), before)

    def test_capture_date_is_real_local_date_not_historical_slot_date(self):
        store = linked_store()
        result = sync_networth(store, Client({"SENSITIVE_BANK_TOKEN": {"accounts": [account()]}}), NOW)
        self.assertEqual(result["last_capture_date"], "2026-11-03")
        self.assertEqual(set(store.days), {"2026-11-03"})


class ScheduleTests(unittest.TestCase):
    def test_latest_same_day_slot_and_before_open(self):
        for hour, minute, expected in ((8, 59, None), (9, 0, 9), (11, 59, 9),
                (12, 0, 12), (17, 0, 17), (21, 59, 17), (22, 0, 22), (23, 59, 22)):
            value = datetime(2026, 11, 3, hour, minute, tzinfo=TIMEZONE)
            slot = scheduled_slot(value)
            self.assertEqual(datetime.fromisoformat(slot).hour if slot else None, expected)
            if slot:
                self.assertTrue(slot.startswith("2026-11-03T"))

    def test_daylight_saving_time_keeps_requested_local_hours(self):
        winter = scheduled_slot(datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc))
        summer = scheduled_slot(datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc))
        spring = scheduled_slot(datetime(2026, 3, 8, 13, 0, tzinfo=timezone.utc))
        fall = scheduled_slot(datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc))
        self.assertTrue(winter.endswith("09:00:00-05:00"))
        self.assertTrue(summer.endswith("09:00:00-04:00"))
        self.assertTrue(spring.endswith("09:00:00-04:00"))
        self.assertTrue(fall.endswith("09:00:00-05:00"))

    def test_naive_time_is_rejected_instead_of_assuming_server_timezone(self):
        with self.assertRaisesRegex(NetworthError, "TIMEZONE_REQUIRED"):
            scheduled_slot(datetime(2026, 11, 3, 9))


if __name__ == "__main__":
    unittest.main()
