"""Offline contract tests for Networth's private, encrypted durable ledger."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from cryptography.fernet import Fernet
import requests
from streamlit.runtime.secrets import AttrDict

from src.networth_store import GitHubNetworthStore, NetworthError, StoreConfig


def blob_sha(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class Response:
    def __init__(self, status, value):
        self.status_code = status
        self.raw = json.dumps(value).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.raw), chunk_size):
            yield self.raw[start:start + chunk_size]

    def close(self):
        self.closed = True


class GitHub:
    """GitHub content SHA compare-and-swap with immutable per-commit snapshots."""

    def __init__(self):
        self.private = True
        self.metadata_status = 200
        self.files = {"main": {}}
        self.commits = {}
        self.calls = []
        self.puts = 0
        self.denied_file = None
        self.before_put = None
        self.bad_readback = False
        self.disappear_on_missing = False

    def request(self, method, url, **kwargs):
        path = unquote(urlsplit(url).path.removeprefix("/repos/G-enterpriseGroup/Portfolio"))
        self.calls.append((method, path, deepcopy(kwargs)))
        params = kwargs.get("params") or {}
        payload = kwargs.get("json") or {}
        if path == "":
            return Response(self.metadata_status, {"private": self.private, "default_branch": "main"})
        if path.startswith("/git/ref/heads/"):
            branch = path.removeprefix("/git/ref/heads/")
            return Response(200, {"ref": "refs/heads/" + branch,
                                  "object": {"type": "commit", "sha": "a" * 40}}) if branch in self.files else Response(404, {})
        if path == "/git/refs":
            branch = payload["ref"].removeprefix("refs/heads/")
            if branch in self.files:
                return Response(422, {})
            self.files[branch] = deepcopy(self.files["main"])
            return Response(201, {"ref": payload["ref"]})
        document = path.removeprefix("/contents/")
        if method == "GET":
            if self.denied_file:
                return Response(self.denied_file, {})
            ref = params["ref"]
            files = self.files.get(ref, self.commits.get(ref, {}))
            if document not in files:
                if self.disappear_on_missing:
                    self.files.pop("networth-data", None)
                return Response(404, {})
            raw = files[document]
            if self.bad_readback and ref in self.commits:
                raw = b"invalid immutable readback"
            return Response(200, {"type": "file", "encoding": "base64", "sha": blob_sha(raw),
                                  "content": base64.b64encode(raw).decode()})
        if method == "PUT":
            if self.before_put is not None:
                callback, self.before_put = self.before_put, None
                callback()
            files = self.files[payload["branch"]]
            old = files.get(document)
            if (blob_sha(old) if old is not None else None) != payload.get("sha"):
                return Response(409, {})
            raw = base64.b64decode(payload["content"])
            files[document] = raw
            self.puts += 1
            commit_sha = f"{self.puts:040x}"
            self.commits[commit_sha] = deepcopy(files)
            return Response(201, {"commit": {"sha": commit_sha}})
        raise AssertionError("Unexpected GitHub request")


class NetworthStoreTests(unittest.TestCase):
    def setUp(self):
        self.config = StoreConfig(github_token="PRIVATE_FAKE_TOKEN", encryption_key=Fernet.generate_key().decode())
        self.store = GitHubNetworthStore(self.config)
        self.github = GitHub()
        self.request_patch = patch("src.networth_store.requests.request", side_effect=self.github.request)
        self.request_patch.start()
        self.addCleanup(self.request_patch.stop)

    def state_path(self, store=None):
        return "networth/" + (store or self.store).scope + "/state.enc"

    def test_unconfigured_and_partial_config_are_redacted(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(StoreConfig.from_runtime({}))
            for settings, expected in [
                ({"github_token": "SECRET_TOKEN"}, "encryption key missing"),
                ({"encryption_key": self.config.encryption_key}, "GitHub token missing"),
                ({"github_token": "SECRET_TOKEN", "encryption_key": "PRIVATE_BAD_KEY"}, "key invalid"),
            ]:
                with self.subTest(settings=list(settings)):
                    with self.assertRaises(NetworthError) as caught:
                        StoreConfig.from_runtime({"networth": settings})
                    self.assertIn(expected, str(caught.exception))
                    self.assertNotIn("SECRET_TOKEN", str(caught.exception))
                    self.assertNotIn("PRIVATE_BAD_KEY", str(caught.exception))
                    self.assertNotIn(self.config.encryption_key, str(caught.exception))
        self.assertNotIn(self.config.github_token, repr(self.config))
        self.assertNotIn(self.config.encryption_key, repr(self.config))

    def test_attrdict_secrets_precede_environment_and_no_other_feature_fallback(self):
        secrets = {"networth": AttrDict({"github_token": "EXPLICIT_TOKEN", "encryption_key": self.config.encryption_key,
                                         "ledger_id": "explicit-ledger"})}
        with patch.dict("os.environ", {"NETWORTH_GITHUB_TOKEN": "STALE_TOKEN", "NETWORTH_LEDGER_ID": "stale"}, clear=True):
            selected = StoreConfig.from_runtime(secrets)
        self.assertEqual(selected.github_token, "EXPLICIT_TOKEN")
        self.assertEqual(selected.ledger_id, "explicit-ledger")
        self.assertEqual(selected.repository, "G-enterpriseGroup/Portfolio")
        self.assertEqual(selected.branch, "networth-data")
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(StoreConfig.from_runtime({"github": {"bridge_token": "OTHER_FEATURE_TOKEN"},
                                                       "acquisition": {"archive_key": self.config.encryption_key}}))
        with patch.dict("os.environ", {"NETWORTH_GITHUB_TOKEN": "ENV_TOKEN", "NETWORTH_ENCRYPTION_KEY": self.config.encryption_key,
                                       "NETWORTH_REPOSITORY": "owner/private", "NETWORTH_BRANCH": "finance/data", "NETWORTH_LEDGER_ID": "other"}, clear=True):
            selected = StoreConfig.from_runtime({})
        self.assertEqual((selected.github_token, selected.repository, selected.branch, selected.ledger_id),
                         ("ENV_TOKEN", "owner/private", "finance/data", "other"))

    def test_no_arg_runtime_loads_streamlit_secrets(self):
        secrets = {"networth": AttrDict({"github_token": self.config.github_token, "encryption_key": self.config.encryption_key})}
        with patch("streamlit.secrets", secrets), patch.dict("os.environ", {}, clear=True):
            self.assertEqual(StoreConfig.from_runtime(), self.config)

    def test_explicit_empty_secret_does_not_reuse_stale_env(self):
        with patch.dict("os.environ", {"NETWORTH_GITHUB_TOKEN": "STALE"}, clear=True):
            with self.assertRaisesRegex(NetworthError, "GitHub token missing"):
                StoreConfig.from_runtime({"networth": {"github_token": "", "encryption_key": self.config.encryption_key}})

    def test_invalid_repository_branch_and_ledger_rejected_before_network(self):
        for field, value in [("repository", "https://other.host/x"), ("repository", "owner/../repo"),
                             ("branch", "../main"), ("branch", "data?ref=main"), ("branch", "refs//data"),
                             ("branch", "data.lock"), ("branch", "data%2Fmain"), ("branch", "data\x00branch"), ("ledger_id", "")]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(NetworthError):
                    StoreConfig(**{**self.config.__dict__, field: value})
        self.assertFalse(self.github.calls)

    def test_new_private_ledger_initializes_branch_and_state(self):
        self.assertEqual(self.store.read_state(), {"version": 1, "items": {}, "accounts": {}, "preferences": {}, "pending_links": {}})
        self.assertIn("networth-data", self.github.files)
        self.assertEqual(self.github.calls[0][1], "")
        self.assertEqual([method for method, path, _ in self.github.calls if path == "/git/refs"], ["POST"])
        self.assertEqual(self.github.puts, 0)
        self.store.read_state()
        self.assertEqual(sum(path == "/git/refs" for _, path, _ in self.github.calls), 1)

    def test_public_and_inaccessible_repo_never_read_or_write_data(self):
        self.github.private = False
        with self.assertRaisesRegex(NetworthError, "PRIVATE"):
            self.store.update_state(lambda state: state["accounts"].update({"ACCOUNT": {}}))
        self.assertEqual(len(self.github.calls), 1)
        self.github.private = True
        for status in (401, 403, 404):
            self.github.metadata_status = status
            with self.subTest(status=status), self.assertRaisesRegex(NetworthError, "access denied"):
                self.store.read_state()
        self.assertEqual(self.github.puts, 0)

    def test_missing_default_ref_is_not_an_empty_repository(self):
        self.github.files.clear()
        with self.assertRaisesRegex(NetworthError, "access denied"):
            self.store.read_state()
        self.assertFalse(any(method == "POST" for method, _, _ in self.github.calls))

    def test_default_branch_cannot_be_used_for_data(self):
        store = GitHubNetworthStore(StoreConfig(**{**self.config.__dict__, "branch": "main"}))
        with self.assertRaisesRegex(NetworthError, "dedicated data branch"):
            store.read_state()
        self.assertEqual(len(self.github.calls), 1)

    def test_encrypted_save_restores_without_plaintext_or_identifiers_in_paths(self):
        def mutate(state):
            state["items"]["PLAID_ITEM"] = {"access_token": "PLAID_ACCESS_SECRET"}
            state["accounts"]["ACCOUNT_5474"] = {"name": "Raj private account", "balance": 12345.67}
        saved = self.store.update_state(mutate)
        self.assertEqual(saved, GitHubNetworthStore(self.config).read_state())
        files = self.github.files["networth-data"]
        self.assertEqual(len(files), 1)
        self.assertTrue(repr(next(iter(files.values()))).startswith("b'gAAAA"))
        transmitted = json.dumps([(path, call["json"]) for _, path, call in self.github.calls])
        for secret in ("PLAID_ITEM", "PLAID_ACCESS_SECRET", "ACCOUNT_5474", "Raj private account", self.config.github_token):
            self.assertNotIn(secret, transmitted)
        saved["accounts"].clear()
        self.assertIn("ACCOUNT_5474", self.store.read_state()["accounts"])
        request = self.github.calls[0][2]
        self.assertEqual(request["timeout"], (5, 30))
        self.assertIs(request["stream"], True)
        self.assertIs(request["allow_redirects"], False)
        self.assertTrue(any(call["params"] and call["params"]["ref"] in self.github.commits for _, _, call in self.github.calls))

    def test_noop_update_skips_write_and_replacement_is_supported(self):
        self.store.update_state(lambda state: state["preferences"].update({"ACCOUNT": "Assets"}))
        self.store.update_state(lambda state: state)
        self.assertEqual(self.github.puts, 1)
        state = self.store.update_state(lambda state: {**state, "checkpoint": "saved"})
        self.assertEqual(state["checkpoint"], "saved")

    def test_competing_preference_and_link_updates_are_merged_by_cas(self):
        self.store.update_state(lambda state: state["preferences"].update({"FIRST": "Assets"}))
        self.github.before_put = lambda: self.store.update_state(
            lambda state: state["preferences"].update({"COMPETING": "Equity"}))
        calls = []
        def add_item(state):
            calls.append(deepcopy(state))
            state["items"]["NEW_ITEM"] = {"access_token": "NEW_ACCESS"}
        state = self.store.update_state(add_item)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state["preferences"], {"FIRST": "Assets", "COMPETING": "Equity"})
        self.assertIn("NEW_ITEM", state["items"])
        self.assertEqual(self.github.puts, 3)

    def test_corrupt_or_structurally_invalid_remote_state_never_overwrites(self):
        self.store.update_state(lambda state: state["accounts"].update({"ACCOUNT": {}}))
        path = self.state_path()
        for raw in (b"corrupt ciphertext", self.store._encode("state.enc", {"version": 1, "accounts": []})):
            self.github.files["networth-data"][path] = raw
            before = self.github.puts
            with self.subTest(raw_type=len(raw)), self.assertRaises(NetworthError):
                self.store.update_state(lambda state: state["accounts"].update({"OTHER": {}}))
            self.assertEqual(self.github.puts, before)
            self.assertEqual(self.github.files["networth-data"][path], raw)

    def test_envelope_prevents_cross_scope_cross_day_and_wrong_key_restore(self):
        self.store.update_state(lambda state: state["accounts"].update({"ACCOUNT": {}}))
        raw = self.github.files["networth-data"][self.state_path()]
        other = GitHubNetworthStore(StoreConfig(**{**self.config.__dict__, "ledger_id": "other-ledger"}))
        self.github.files["networth-data"][self.state_path(other)] = raw
        with self.assertRaisesRegex(NetworthError, "scope mismatch"):
            other.read_state()
        wrong_key = GitHubNetworthStore(StoreConfig(**{**self.config.__dict__, "encryption_key": Fernet.generate_key().decode()}))
        self.github.files["networth-data"][self.state_path(wrong_key)] = raw
        with self.assertRaisesRegex(NetworthError, "authenticated"):
            wrong_key.read_state()
        self.store.save_capture("2026-11-03", "09", {"balance": 10})
        prefix = "networth/" + self.store.scope + "/days/"
        self.github.files["networth-data"][prefix + "2026-11-04.enc"] = self.github.files["networth-data"][prefix + "2026-11-03.enc"]
        with self.assertRaisesRegex(NetworthError, "scope mismatch"):
            self.store.read_day("2026-11-04")

    def test_daily_captures_append_idempotently_and_refuse_correction_of_saved_id(self):
        self.assertIsNone(self.store.read_day("2026-11-03"))
        self.store.save_capture("2026-11-03", "09", {"balance": 10})
        self.store.save_capture("2026-11-03", "12", {"balance": 20})
        self.store.save_capture("2026-11-03", "09", {"balance": 10})
        self.assertEqual(self.github.puts, 2)
        self.assertEqual(self.store.read_day("2026-11-03"), {"version": 1, "captures": {"09": {"balance": 10}, "12": {"balance": 20}}})
        with self.assertRaisesRegex(NetworthError, "different data"):
            self.store.save_capture("2026-11-03", "09", {"balance": 99})
        self.assertEqual(self.github.puts, 2)
        self.assertEqual(self.store.read_state()["accounts"], {})

    def test_competing_daily_captures_preserve_both(self):
        self.github.before_put = lambda: self.store.save_capture("2026-11-03", "12", {"balance": 20})
        self.store.save_capture("2026-11-03", "09", {"balance": 10})
        self.assertEqual(set(self.store.read_day("2026-11-03")["captures"]), {"09", "12"})

    def test_missing_forbidden_and_deleted_branch_are_distinct(self):
        self.assertIsNone(self.store.read_day("2026-11-03"))
        self.github.denied_file = 403
        with self.assertRaisesRegex(NetworthError, "access denied"):
            self.store.read_day("2026-11-03")
        self.github.denied_file = None
        self.github.disappear_on_missing = True
        with self.assertRaisesRegex(NetworthError, "access denied"):
            self.store.update_state(lambda state: state["preferences"].update({"A": "Equity"}))
        self.assertEqual(self.github.puts, 0)

    def test_network_and_http_errors_never_include_provider_response_or_credentials(self):
        with patch("src.networth_store.requests.request", side_effect=requests.ConnectionError("PRIVATE_FAKE_TOKEN ACCOUNT_5474")):
            with self.assertRaises(NetworthError) as caught:
                self.store.read_state()
        self.assertNotIn("PRIVATE_FAKE_TOKEN", str(caught.exception))
        self.assertNotIn("ACCOUNT_5474", str(caught.exception))
        self.github.metadata_status = 500
        with self.assertRaisesRegex(NetworthError, "HTTP 500"):
            self.store.read_state()

    def test_save_requires_immutable_readback_before_reporting_success(self):
        self.github.bad_readback = True
        with self.assertRaises(NetworthError):
            self.store.update_state(lambda state: state["preferences"].update({"A": "Assets"}))
        self.assertEqual(self.github.puts, 1)
        self.github.bad_readback = False
        self.assertEqual(self.store.read_state()["preferences"], {"A": "Assets"})

    def test_invalid_dates_capture_and_nonfinite_values_fail_before_write(self):
        for day in ("2026-11-3", "2026-02-30", "../main", "20261103", None):
            with self.subTest(day=day), self.assertRaises(NetworthError):
                self.store.read_day(day)
        with self.assertRaises(NetworthError):
            self.store.save_capture("2026-11-03", "", {})
        with self.assertRaises(NetworthError):
            self.store.save_capture("2026-11-03", "09", {"balance": float("nan")})
        self.assertEqual(self.github.puts, 0)

    def test_oversize_response_and_documents_fail_closed(self):
        response = Response(200, {"private": True, "padding": "x" * (2 * 1024 * 1024)})
        with patch("src.networth_store.requests.request", return_value=response):
            with self.assertRaisesRegex(NetworthError, "too large"):
                self.store.read_state()
        self.assertTrue(response.closed)
        with self.assertRaisesRegex(NetworthError, "too large"):
            self.store.update_state(lambda state: state.update({"oversize": "x" * (512 * 1024)}))
        self.assertEqual(self.github.puts, 0)


if __name__ == "__main__":
    unittest.main()
