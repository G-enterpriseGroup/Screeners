"""Authentication-store encryption, independent bootstrap and CAS regressions."""

from copy import deepcopy
import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
import requests

from src.security_store import (
    GitHubSecurityStore, SecurityStoreConfig, SecurityStoreError, _Conflict, initial_state,
)


def config(**overrides):
    return SecurityStoreConfig("synthetic-token", base64.urlsafe_b64encode(b"k" * 32).decode(),
                               **overrides)


class FakeGitHub(GitHubSecurityStore):
    def __init__(self, cfg=None):
        super().__init__(cfg or config())
        self.private = True
        self.default = "main"
        self.head = None
        self.commits = {}
        self.trees = {}
        self.calls = []
        self.count = 0
        self.force_conflict = False
        self.corrupt_readback = False

    def sha(self):
        self.count += 1
        return f"{self.count:040x}"

    def _request(self, method, suffix="", **kwargs):
        payload, params = kwargs.get("payload"), kwargs.get("params")
        self.calls.append((method, suffix, deepcopy(payload), deepcopy(params)))
        if method == "GET" and suffix == "":
            return {"private": self.private, "default_branch": self.default}
        if method == "GET" and suffix.startswith("/git/ref/heads/"):
            return None if self.head is None else {"ref": "refs/heads/" + self.config.branch,
                                                   "object": {"sha": self.head, "type": "commit"}}
        if method == "GET" and suffix == "/contents/auth-state.enc":
            cipher = self.commits[params["ref"]]
            if self.corrupt_readback:
                cipher = b"bad-ciphertext"
            return {"path": "auth-state.enc", "type": "file", "encoding": "base64",
                    "sha": hashlib.sha1(cipher).hexdigest(), "content": base64.b64encode(cipher).decode()}
        if method == "POST" and suffix == "/git/trees":
            sha = self.sha()
            self.trees[sha] = payload["tree"][0]["content"].encode("ascii")
            return {"sha": sha}
        if method == "POST" and suffix == "/git/commits":
            sha = self.sha()
            self.commits[sha] = self.trees[payload["tree"]]
            return {"sha": sha}
        if method == "POST" and suffix == "/git/refs":
            if self.head is not None:
                raise _Conflict()
            self.head = payload["sha"]
            return {"ref": payload["ref"]}
        if method == "PUT" and suffix == "/contents/auth-state.enc":
            if self.force_conflict:
                self.force_conflict = False
                state = self._read_at(self.head)[0]
                state["counters"]["c" * 64] = 77
                self.head = self.sha()
                self.commits[self.head] = self._encode(state)
                raise _Conflict()
            if payload["sha"] != hashlib.sha1(self.commits[self.head]).hexdigest():
                raise _Conflict()
            self.head = self.sha()
            self.commits[self.head] = base64.b64decode(payload["content"])
            return {"commit": {"sha": self.head}}
        raise AssertionError((method, suffix))


class StoreTests(unittest.TestCase):
    def test_runtime_fallback_is_configuration_only_and_branch_is_independent(self):
        cfg = SecurityStoreConfig.from_runtime({"security": {}, "networth": {
            "github_token": "synthetic", "encryption_key": config().encryption_key,
            "repository": "example/private", "branch": "networth-data"}})
        self.assertEqual(cfg.branch, "terminal-security-state")
        self.assertEqual(cfg.repository, "example/private")
        self.assertNotIn("synthetic", repr(cfg))
        for branch in ("main", "networth-data", "gex-bridge-data", "terminal-security-../bad"):
            with self.assertRaises(SecurityStoreError):
                config(branch=branch)

    def test_private_and_default_branch_checks_precede_document_access(self):
        for private, default in [(False, "main"), (True, "terminal-security-state")]:
            store = FakeGitHub()
            store.private, store.default = private, default
            with self.assertRaises(SecurityStoreError):
                store.update(lambda state: state["counters"].update({"a" * 64: 1}))
            self.assertEqual(len(store.calls), 1)

    def test_orphan_bootstrap_contains_only_ciphertext_and_verifies_immutable_commit(self):
        store = FakeGitHub()
        state = store.update(lambda value: value["counters"].update({"a" * 64: 123}))
        self.assertEqual(state, store.read())
        tree = next(call[2] for call in store.calls if call[:2] == ("POST", "/git/trees"))
        self.assertEqual(len(tree["tree"]), 1)
        self.assertEqual(tree["tree"][0]["path"], "auth-state.enc")
        self.assertNotIn("counters", tree["tree"][0]["content"])
        commit = next(call[2] for call in store.calls if call[:2] == ("POST", "/git/commits"))
        self.assertEqual(commit["parents"], [])
        self.assertNotIn("base_tree", tree)
        self.assertFalse(any("networth" in call[1] for call in store.calls))
        self.assertTrue(any(call[3] == {"ref": store.head} for call in store.calls))

    def test_auth_key_is_domain_separated_and_scope_bound(self):
        store = FakeGitHub()
        ciphertext = store._encode(initial_state())
        with self.assertRaises(Exception):
            Fernet(config().encryption_key.encode()).decrypt(ciphertext)
        other = FakeGitHub(config(branch="terminal-security-other"))
        other.commits["a" * 40] = ciphertext
        with self.assertRaises(SecurityStoreError):
            other._read_at("a" * 40)

    def test_cas_conflict_rereads_and_preserves_competing_counter(self):
        store = FakeGitHub()
        store.update(lambda state: state["counters"].update({"a" * 64: 1}))
        store.force_conflict = True
        state = store.update(lambda value: value["counters"].update({"b" * 64: 2}))
        self.assertEqual(state["counters"], {"a" * 64: 1, "b" * 64: 2, "c" * 64: 77})

    def test_corruption_and_unverified_write_fail_closed(self):
        store = FakeGitHub()
        store.corrupt_readback = True
        with self.assertRaises(SecurityStoreError):
            store.update(lambda value: value["counters"].update({"a" * 64: 1}))

    def test_store_rejects_invalid_state_instead_of_overwriting_it(self):
        store = FakeGitHub()
        for update in (lambda state: state.update(version=2),
                       lambda state: state["identities"].update({"x": {}}),
                       lambda state: state["identities"].update({1: {}}),
                       lambda state: state["counters"].update({1: 1}),
                       lambda state: state["counters"].update({"a" * 64: True})):
            with self.assertRaises(SecurityStoreError):
                store.update(update)
        self.assertIsNone(store.head)

    def test_network_exception_does_not_echo_credentials(self):
        with patch("src.security_store.requests.request", side_effect=requests.Timeout("synthetic-token")):
            with self.assertRaises(SecurityStoreError) as caught:
                GitHubSecurityStore(config()).read()
        self.assertNotIn("synthetic-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
