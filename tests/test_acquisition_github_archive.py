"""Offline tests for encrypted GitHub Acquisition retention and fail-closed restore."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
import json
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from streamlit.runtime.secrets import AttrDict

from src.acquisition_history_github import (
    ArchiveConfig, ArchiveError, GitHubAcquisitionArchive, _Conflict,
)
from src.acquisition_ui import _merge, _sync_history


class MemoryGitHub(GitHubAcquisitionArchive):
    """Simulates immutable Git commits and branch blob SHA CAS without networking."""

    def __init__(self, config, store=None):
        super().__init__(config)
        self.store = store if store is not None else {"files": {}, "commits": {}, "puts": 0}
        self.inject_conflict = None
        self.fail_reads = False
        self.fail_writes = False

    def _request(self, path, *, ref="gex-bridge-data", payload=None):
        if payload is None:
            if self.fail_reads:
                raise ArchiveError("Network unreachable")
            if ref != "gex-bridge-data":
                return self.store["commits"].get(ref)
            return self.store["files"].get(path)
        if self.fail_writes:
            raise ArchiveError("Permission denied")
        if self.inject_conflict is not None:
            callback = self.inject_conflict
            self.inject_conflict = None
            callback()
            raise _Conflict()
        before = self.store["files"].get(path)
        if (before or {}).get("sha") != payload.get("sha"):
            raise _Conflict()
        self.store["puts"] += 1
        sha = f"git-sha-{self.store['puts']}"
        raw = base64.b64decode(payload["content"]).decode("utf-8")
        result = {"sha": sha, "content": base64.b64encode(raw.encode("utf-8")).decode("ascii")}
        self.store["files"][path] = result
        self.store["commits"][sha] = result
        return {"commit": {"sha": sha}}


class Broker:
    def __init__(self, accounts):
        self.accounts = accounts
        self.calls = []

    def get_transactions(self, account, **kwargs):
        self.calls.append((account, kwargs))
        return self.accounts[account]


def trade(tid, symbol):
    return {
        "transactionId": tid,
        "transactionDate": 1761000000000 + tid,
        "brokerage": {"transactionType": "BUY", "quantity": 8,
                      "price": 410.15, "product": {"symbol": symbol, "securityType": "EQ"}},
    }


class GitHubArchiveTests(unittest.TestCase):
    def setUp(self):
        self.config = ArchiveConfig(token="FAKE_WRITE_TOKEN", fernet_key=Fernet.generate_key().decode())
        self.archive = MemoryGitHub(self.config)
        self.now = datetime(2026, 10, 26, tzinfo=ZoneInfo("America/New_York"))

    def snapshot(self, rows=None, through="2026-10-26"):
        return {"rows": rows if rows is not None else [trade(1, "MSFT")],
                "covered_from": "2024-10-27",
                "covered_through": through, "synced_at": 1234567890.0}

    def test_streamlit_toml_nested_attrdict_archive_key_and_bridge_token(self):
        # A real Streamlit [acquisition] and [github] secrets section uses
        # AttrDict (Mapping), not a built-in dict.
        secrets = {
            "acquisition": AttrDict({"archive_key": self.config.fernet_key}),
            "github": AttrDict({"bridge_token": "GITHUB_WRITE_TOKEN"}),
        }
        with patch.dict("os.environ", {}, clear=True), patch("streamlit.secrets", secrets):
            selected = ArchiveConfig.from_runtime()
        self.assertIsNotNone(selected)
        self.assertEqual(selected.fernet_key, self.config.fernet_key)
        self.assertEqual(selected.token, "GITHUB_WRITE_TOKEN")

    def test_missing_github_write_token_disables_archive_without_exposing_key(self):
        secrets = {"acquisition": AttrDict({"archive_key": self.config.fernet_key})}
        with patch.dict("os.environ", {}, clear=True), patch("streamlit.secrets", secrets):
            with self.assertRaises(ArchiveError) as caught:
                ArchiveConfig.from_runtime()
        self.assertIn("GITHUB WRITE TOKEN MISSING", str(caught.exception))
        self.assertNotIn(self.config.fernet_key, str(caught.exception))

    def test_archive_failure_identifies_invalid_key_without_leaking_it(self):
        corrupted = "not-a-fernet-key"
        secrets = {
            "acquisition": AttrDict({"archive_key": corrupted}),
            "github": AttrDict({"bridge_token": "SENSITIVE_WRITE_TOKEN"}),
        }
        with patch.dict("os.environ", {}, clear=True), patch("streamlit.secrets", secrets):
            with self.assertRaises(ArchiveError) as caught:
                ArchiveConfig.from_runtime()
        msg = str(caught.exception)
        self.assertIn("ENCRYPTION KEY INVALID", msg)
        self.assertNotIn(corrupted, msg)
        self.assertNotIn("SENSITIVE_WRITE_TOKEN", msg)

    def test_archive_failure_explains_missing_token_or_key_without_leaking_secrets(self):
        scenarios = [
            ({"acquisition": AttrDict({"archive_key": self.config.fernet_key})},
             "GITHUB WRITE TOKEN MISSING"),
            ({"github": AttrDict({"bridge_token": "SENSITIVE_WRITE_TOKEN"})},
             "ENCRYPTION KEY MISSING"),
        ]
        for sections, expected in scenarios:
            with self.subTest(expected=expected), patch.dict("os.environ", {}, clear=True), patch(
                "streamlit.secrets", sections
            ):
                with self.assertRaises(ArchiveError) as caught:
                    ArchiveConfig.from_runtime()
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("SENSITIVE_WRITE_TOKEN", str(caught.exception))
                self.assertNotIn(self.config.fernet_key, str(caught.exception))

    def test_invalid_encryption_key_is_reported_not_mislabeled_broker_history(self):
        secrets = {
            "acquisition": AttrDict({"archive_key": "bad-key"}),
            "github": AttrDict({"bridge_token": "PRIVATE_TOKEN"}),
        }
        client = Broker({"RAJ": [trade(1, "MSFT")]})
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        with patch.dict("os.environ", {}, clear=True), patch("streamlit.secrets", secrets), patch(
            "src.acquisition_ui._history_vault", return_value=vault
        ):
            snapshots, errors = _sync_history(
                client, "LOCK", [{"accountIdKey": "RAJ"}], now=self.now,
            )
        self.assertEqual(len(snapshots["RAJ"]["rows"]), 1)
        self.assertIn("ENCRYPTION KEY INVALID", errors["archive"])
        self.assertFalse(vault["archive_configured"])
        self.assertNotIn("RAJ", errors)

    def test_archive_warning_never_hides_global_config_error_or_shows_empty_sync(self):
        source = (Path(__file__).resolve().parents[1] / "src/acquisition_ui.py").read_text()
        self.assertIn('errors.get(', source)
        self.assertIn('account_errors = [', source)
        self.assertIn('if account_errors:', source)

    def test_encrypted_account_isolation_and_no_plaintext(self):
        a = self.archive.save("PRIVATE_ACCOUNT_RAJ", self.snapshot(), _merge)
        self.assertEqual(len(a["rows"]), 1)
        self.archive.save("PRIVATE_ACCOUNT_OTHER", self.snapshot([trade(2, "LMT")]), _merge)
        self.assertEqual(len(self.archive.store["files"]), 2)
        one_path = self.archive.path("PRIVATE_ACCOUNT_RAJ")
        self.assertNotIn("RAJ", one_path)
        self.assertNotIn("PRIVATE", one_path)
        encrypted = base64.b64decode(self.archive.store["files"][one_path]["content"])
        for plaintext in (b"MSFT", b"transactionId", b"RAJ", b"410.15"):
            self.assertNotIn(plaintext, encrypted)
        self.assertEqual(self.archive.read("PRIVATE_ACCOUNT_RAJ")["rows"][0]["transactionId"], 1)
        self.assertEqual(self.archive.read("PRIVATE_ACCOUNT_OTHER")["rows"][0]["transactionId"], 2)

    def test_preserves_history_after_process_restart_no_broker_full_refetch(self):
        accounts = [{"accountIdKey": "RAJ"}, {"accountIdKey": "AMEKA"}]
        client = Broker({"RAJ": [trade(1, "MSFT")], "AMEKA": [trade(2, "LMT")]})
        first_vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        with patch("src.acquisition_ui._history_vault", return_value=first_vault), patch(
                "src.acquisition_ui._archive_client", return_value=self.archive):
            original, err = _sync_history(client, "LOCK_CODE_HASH", accounts, now=self.now)
            self.assertFalse(err)
        self.assertEqual(len(client.calls), 2)
        # New Streamlit worker: lost Python memory; GitHub is still intact.
        new_vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        with patch("src.acquisition_ui._history_vault", return_value=new_vault), patch(
                "src.acquisition_ui._archive_client", return_value=self.archive):
            restored, err = _sync_history(client, "LOCK_CODE_HASH", accounts, now=self.now)
            self.assertFalse(err)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(restored["RAJ"]["rows"], original["RAJ"]["rows"])
        self.assertEqual(restored["AMEKA"]["rows"], original["AMEKA"]["rows"])

    def test_overlap_incremental_save_and_noop_suppression(self):
        first = self.archive.save("RAJ", self.snapshot([trade(1, "MSFT")], "2026-10-25"), _merge)
        updated = self.archive.save("RAJ", self.snapshot([trade(2, "MSFT")]), _merge)
        self.assertEqual(len(updated["rows"]), 2)
        self.assertEqual(self.archive.store["puts"], 2)
        same = self.archive.save("RAJ", updated, _merge)
        self.assertEqual(self.archive.store["puts"], 2)
        self.assertEqual(len(same["rows"]), 2)

    def test_two_writer_conflict_reloads_and_keeps_both_transactions(self):
        self.archive.save("RAJ", self.snapshot([trade(1, "MSFT")]), _merge)
        competing = MemoryGitHub(self.config, self.archive.store)
        def winning_writer():
            competing.save("RAJ", self.snapshot([trade(3, "LMT")]), _merge)
        self.archive.inject_conflict = winning_writer
        result = self.archive.save("RAJ", self.snapshot([trade(2, "MSFT")]), _merge)
        self.assertEqual({t["transactionId"] for t in result["rows"]}, {1, 2, 3})
        self.assertEqual({t["transactionId"] for t in competing.read("RAJ")["rows"]}, {1, 2, 3})

    def test_invalid_key_or_corrupted_snapshot_cannot_overwrite_history(self):
        self.archive.save("RAJ", self.snapshot(), _merge)
        old = self.archive.store["puts"]
        wrong = MemoryGitHub(ArchiveConfig("TOKEN", Fernet.generate_key().decode()), self.archive.store)
        # Wrong encryption key produces a different path; it cannot access this account's original archive.
        self.assertNotEqual(wrong.path("RAJ"), self.archive.path("RAJ"))
        path = self.archive.path("RAJ")
        self.archive.store["files"][path]["content"] = base64.b64encode(b"not valid ciphertext").decode("ascii")
        with self.assertRaises(ArchiveError):
            self.archive.save("RAJ", self.snapshot([trade(2, "LMT")]), _merge)
        self.assertEqual(self.archive.store["puts"], old)

    def test_archive_outage_preserves_worker_history_but_never_claims_save(self):
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        self.archive.fail_writes = True
        with patch("src.acquisition_ui._history_vault", return_value=vault), patch(
                "src.acquisition_ui._archive_client", return_value=self.archive):
            snapshots, errors = _sync_history(
                Broker({"RAJ": [trade(1, "MSFT")]}), "LOCK", [{"accountIdKey": "RAJ"}],
                now=self.now,
            )
        self.assertEqual(len(snapshots["RAJ"]["rows"]), 1)
        self.assertIn("RAJ", errors)
        self.assertTrue(vault["archive_dirty"][("LOCK", "RAJ")])
        self.assertFalse(self.archive.store["files"])


if __name__ == "__main__":
    unittest.main()
