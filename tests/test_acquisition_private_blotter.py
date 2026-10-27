"""Acquisition's plaintext private-GitHub long-history blotter regression tests."""
from __future__ import annotations

import base64
import json
import threading
import urllib.error
from unittest.mock import MagicMock
from datetime import date, datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from src.acquisition_private_blotter import PrivateTradeBlotter, share_blotter, PRIVATE_REPO, BRANCH
from src.acquisition_history_github import ArchiveConfig, ArchiveError
from src.acquisition_ui import _equity_trade, _merge, _sync_history


ET = ZoneInfo("America/New_York")


def tx(id, date, action, symbol, quantity, price, security="EQ"):
    return {
        "transactionId": id,
        "transactionDate": int(datetime.fromisoformat(date).replace(tzinfo=ET).timestamp() * 1000),
        "brokerage": {
            "transactionType": action, "quantity": quantity, "price": price,
            "product": {"symbol": symbol, "securityType": security},
        },
    }


class PrivateGitHubInMemory(PrivateTradeBlotter):
    def __init__(self, config, store=None):
        with patch.dict("os.environ", {}, clear=True), patch("streamlit.secrets", {}):
            super().__init__(config)
        self.store = store if store is not None else {"files": {}, "versions": {}, "write_count": 0}
        self.private = True
        self.denied = False

    def _request(self, path, *, payload=None, ref=BRANCH):
        if self.denied:
            raise ArchiveError("Private GitHub blotter access failed (HTTP 403).")
        if not path:
            return {"full_name": PRIVATE_REPO, "private": self.private, "archived": False}
        if payload is None:
            if ref != BRANCH:
                return self.store["versions"].get((path, ref))
            return self.store["files"].get(path)
        previous = self.store["files"].get(path)
        if (previous or {}).get("sha") != payload.get("sha"):
            from src.acquisition_private_blotter import _Conflict
            raise _Conflict()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        self.store["write_count"] += 1
        sha = "fakeblob-" + str(self.store["write_count"])
        data = {"sha": sha, "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
        self.store["files"][path] = data
        self.store["versions"][(path, "fakecommit-" + sha)] = data
        return {"commit": {"sha": "fakecommit-" + sha}}

    def text(self, path):
        return base64.b64decode(self.store["files"][path]["content"]).decode("utf-8")


class PrivateBlotterTests(unittest.TestCase):
    def setUp(self):
        self.config = ArchiveConfig("FAKE_GITHUB_TOKEN", Fernet.generate_key().decode())
        self.repo = PrivateGitHubInMemory(self.config)
        self.older = [
            tx(7, "2024-06-10", "BUY", "LMT", 10, 470.5),
            tx(8, "2024-10-11", "SELL", "LMT", 6, 522.25),
            tx(9, "2025-02-28", "BUY", "MSFT", 8, 310.333),
            tx(10, "2025-04-01", "BUY", "MSFT", 1, 4.5, "OPTN"),
        ]

    def snapshot(self, rows):
        return {
            "rows": rows, "covered_from": "2024-06-10",
            "covered_through": "2026-10-27",
        }

    def test_exact_pine_history_includes_closed_sells_but_excludes_options(self):
        blotter = share_blotter(self.older, _equity_trade)
        self.assertEqual(
            blotter,
            "LMT 06.10.2024 470.5 10 B\n"
            "LMT 10.11.2024 522.25 6 S\n"
            "MSFT 02.28.2025 310.333 8 B\n",
        )

    def test_read_private_snapshot_preserves_full_historical_checkpoint(self):
        self.repo.save("RAJ", self.snapshot(self.older), _merge, _equity_trade)
        restored = self.repo.read_snapshot("RAJ")
        self.assertEqual(restored["covered_from"], "2024-06-10")
        self.assertEqual({x["transactionId"] for x in restored["rows"]}, {7, 8, 9, 10})
        self.assertTrue(restored["rows"][0]["transactionDate"] < restored["rows"][-1]["transactionDate"])

    def test_six_account_restart_resumes_from_saved_checkpoint_with_overlap(self):
        accounts = [{"accountIdKey": k} for k in ("RAJ", "AMEKA", "BALJINDER", "PARMINDER", "BROKER5", "BROKER6")]
        snapshots = {
            key: {
                "rows": [tx(100 + i, "2023-06-10", "BUY", "LMT", 1, 300)] if i < 4 else [],
                "covered_from": "2023-06-10",
                "covered_through": "2026-10-26",
            } for i, key in enumerate(k["accountIdKey"] for k in accounts)
        }
        class Broker:
            def __init__(self):
                self.calls = []
            def get_transactions(self, account, **kwargs):
                self.calls.append((account, kwargs))
                return [tx(900, "2026-10-27", "BUY", "MSFT", 1, 400)] if account == "RAJ" else []
        class Encrypted:
            def read(self, key):
                return None
            def save(self, key, snapshot, merge):
                return snapshot
        class Private:
            def __init__(self):
                self.reads = []
                self.writes = []
                self.preflights = 0
            def _verify_private(self):
                self.preflights += 1
            def read_snapshot(self, account, *, preverified=False):
                self.reads.append((account, preverified))
                return snapshots[account]
            def save(self, account, snapshot, merge, parse):
                self.writes.append((account, snapshot))
                return "https://github.com/private/" + account
        private = Private()
        broker = Broker()
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        with patch("src.acquisition_ui._history_vault", return_value=vault), patch(
            "src.acquisition_ui._archive_client", return_value=Encrypted()
        ), patch("src.acquisition_ui.ArchiveConfig.from_runtime", return_value=self.config), patch(
            "src.acquisition_ui.PrivateTradeBlotter", return_value=private
        ):
            restored, errors = _sync_history(
                broker, "ACCESS_SCOPE", accounts,
                now=datetime(2026, 10, 27, tzinfo=ET),
            )
            self.assertFalse(errors)
            self.assertEqual(len(broker.calls), 6)
            self.assertEqual(len(private.reads), 6)
            self.assertTrue(all(pre for _, pre in private.reads))
            self.assertEqual(private.preflights, 1)
            self.assertEqual(len(private.writes), 6)
            self.assertEqual(len(restored["RAJ"]["rows"]), 2)
            self.assertEqual(restored["RAJ"]["covered_from"], date(2023, 6, 10))
            self.assertEqual(restored["RAJ"]["covered_through"], date(2026, 10, 27))
            self.assertEqual(len(restored["BROKER5"]["rows"]), 0)
            self.assertTrue(all(
                call["start_date"] == date(2026, 10, 19)
                for _, call in broker.calls
            ))
            _sync_history(broker, "ACCESS_SCOPE", accounts,
                          now=datetime(2026, 10, 27, tzinfo=ET))
            self.assertEqual(len(broker.calls), 6)
            self.assertEqual(len(private.reads), 6)

    def test_private_history_read_failure_never_full_imports_or_overwrites(self):
        class Broker:
            def __init__(self): self.calls = []
            def get_transactions(self, account, **kwargs):
                self.calls.append(account)
                return [tx(901, "2026-10-27", "BUY", "MSFT", 1, 300)]
        class Encrypted:
            def read(self, account): return None
            def save(self, account, snapshot, merge):
                raise AssertionError("No encrypted overwrite permitted")
        class Private:
            def _verify_private(self): pass
            def read_snapshot(self, account, *, preverified=False):
                raise ArchiveError("READ FAILED")
            def save(self, *args, **kwargs):
                raise AssertionError("No private overwrite permitted")
        broker = Broker()
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        with patch("src.acquisition_ui._history_vault", return_value=vault), patch(
            "src.acquisition_ui._archive_client", return_value=Encrypted()
        ), patch("src.acquisition_ui.ArchiveConfig.from_runtime", return_value=self.config), patch(
            "src.acquisition_ui.PrivateTradeBlotter", return_value=Private()
        ):
            result, errors = _sync_history(
                broker, "ACCESS_SCOPE", [{"accountIdKey":"RAJ"}],
                now=datetime(2026, 10, 27, tzinfo=ET),
            )
        self.assertFalse(result)
        self.assertFalse(broker.calls)
        self.assertIn("RESTORE FAILED", errors["RAJ"])

    def test_private_repo_has_complete_original_history_and_readable_txt(self):
        link = self.repo.save("RAJ", self.snapshot(self.older), _merge, _equity_trade)
        history_path, text_path = self.repo.paths("RAJ")
        self.assertEqual(link, self.repo.page_url("RAJ"))
        self.assertIn(PRIVATE_REPO + "/blob/" + BRANCH, link)
        self.assertNotIn("RAJ", link)
        self.assertEqual(
            self.repo.text(text_path),
            share_blotter(self.older, _equity_trade),
        )
        raw = json.loads(self.repo.text(history_path))
        self.assertEqual(len(raw["rows"]), 4)  # includes source option data
        self.assertEqual(raw["rows"][0]["transactionId"], 7)
        self.assertEqual(self.repo.store["write_count"], 2)

    def test_incremental_merge_preserves_older_buys_sells_and_distinct_ids(self):
        self.repo.save("RAJ", self.snapshot(self.older), _merge, _equity_trade)
        fresh = [
            tx(11, "2026-10-25", "SELL", "MSFT", 1, 555),
            tx(12, "2026-10-25", "SELL", "MSFT", 1, 555),
        ]
        self.repo.save("RAJ", self.snapshot(fresh), _merge, _equity_trade)
        h, t = self.repo.paths("RAJ")
        rows = json.loads(self.repo.text(h))["rows"]
        self.assertEqual({x["transactionId"] for x in rows}, {7, 8, 9, 10, 11, 12})
        text = self.repo.text(t)
        self.assertEqual(text.count("MSFT 10.25.2026 555 1 S"), 2)
        self.assertIn("LMT 06.10.2024 470.5 10 B", text)
        # Reimport of a repeated 7-day window must never add duplicate IDs.
        self.repo.save("RAJ", self.snapshot(fresh), _merge, _equity_trade)
        rows2 = json.loads(self.repo.text(h))["rows"]
        self.assertEqual(len(rows2), len(rows))
        self.assertEqual(self.repo.store["write_count"], 4)

    def test_corrected_transaction_updates_same_id_in_place(self):
        self.repo.save("RAJ", self.snapshot(self.older), _merge, _equity_trade)
        corrected = [tx(8, "2024-10-11", "SELL", "LMT", 6, 534.25)]
        self.repo.save("RAJ", self.snapshot(corrected), _merge, _equity_trade)
        h, t = self.repo.paths("RAJ")
        self.assertEqual(len(json.loads(self.repo.text(h))["rows"]), 4)
        self.assertIn("LMT 10.11.2024 534.25 6 S", self.repo.text(t))
        self.assertNotIn("LMT 10.11.2024 522.25 6 S", self.repo.text(t))

    def test_public_repository_rejected_without_single_write(self):
        self.repo.private = False
        with self.assertRaises(ArchiveError) as err:
            self.repo.save("RAJ", self.snapshot(self.older), _merge, _equity_trade)
        self.assertIn("private", str(err.exception))
        self.assertEqual(self.repo.store["write_count"], 0)

    def test_unauthorized_github_rejected_without_single_write(self):
        self.repo.denied = True
        with self.assertRaises(ArchiveError):
            self.repo.save("RAJ", self.snapshot(self.older), _merge, _equity_trade)
        self.assertEqual(self.repo.store["write_count"], 0)

    def test_streamlit_dedicated_token_overrides_stale_environment_token(self):
        # User's [acquisition] blotter_token should not be shadowed by an old
        # ACQUISITION_BLOTTER_TOKEN environment variable or GEX fallback token.
        with patch.dict("os.environ", {"ACQUISITION_BLOTTER_TOKEN": "STALE_ENV_TOKEN"}), patch(
            "streamlit.secrets", {"acquisition": {"blotter_token": "NEW_STREAMLIT_TOKEN"}}
        ):
            writer = PrivateTradeBlotter(self.config)
        self.assertEqual(writer.token, "NEW_STREAMLIT_TOKEN")
        self.assertEqual(writer.token_source, "Streamlit [acquisition] blotter_token")
        def blocked(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        with patch("urllib.request.urlopen", side_effect=blocked):
            with self.assertRaises(ArchiveError) as caught:
                writer._verify_private()
        message = str(caught.exception)
        self.assertIn("Credential source: Streamlit [acquisition] blotter_token", message)
        self.assertNotIn("NEW_STREAMLIT_TOKEN", message)
        self.assertNotIn("STALE_ENV_TOKEN", message)
        self.assertNotIn(self.config.token, message)

    def test_token_fallback_when_explicit_streamlit_value_absent(self):
        with patch.dict("os.environ", {"ACQUISITION_BLOTTER_TOKEN": "ENV_TOKEN"}), patch(
            "streamlit.secrets", {"acquisition": {}}
        ):
            via_env = PrivateTradeBlotter(self.config)
        self.assertEqual(via_env.token, "ENV_TOKEN")
        self.assertEqual(via_env.token_source, "ACQUISITION_BLOTTER_TOKEN environment")
        with patch.dict("os.environ", {"ACQUISITION_BLOTTER_TOKEN": ""}), patch(
            "streamlit.secrets", {"acquisition": {}}
        ):
            fallback = PrivateTradeBlotter(self.config)
        self.assertEqual(fallback.token, self.config.token)
        self.assertEqual(fallback.token_source, "fallback GitHub/GEX token")

    def test_private_repository_metadata_404_has_clear_scope_diagnostic(self):
        # Directly exercise HTTP translation, without any external request.
        instance = PrivateTradeBlotter(self.config)
        def blocked(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        with patch("urllib.request.urlopen", side_effect=blocked):
            with self.assertRaises(ArchiveError) as caught:
                instance._verify_private()
        warning = str(caught.exception)
        self.assertIn("STREAMLIT GITHUB TOKEN CANNOT ACCESS PRIVATE PORTFOLIO", warning)
        self.assertIn("blotter_token", warning)
        self.assertIn("Contents: Read and write", warning)
        self.assertNotIn(self.config.token, warning)

    def test_private_auth_denial_probes_token_identity_without_exposing_secret(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _maximum=None):
                return json.dumps(self.payload).encode("utf-8")
        with patch.dict("os.environ", {}, clear=True), patch(
            "streamlit.secrets", {"acquisition": {"blotter_token": "PRIVATE_TOKEN_FOR_TEST"}}
        ):
            writer = PrivateTradeBlotter(self.config)
        def denied(_path, **_kwargs):
            raise ArchiveError("STREAMLIT GITHUB TOKEN CANNOT ACCESS PRIVATE PORTFOLIO (HTTP 404)")
        with patch.object(writer, "_request", side_effect=denied), patch(
            "urllib.request.urlopen", return_value=Response({"login": "G-enterpriseGroup"})
        ):
            with self.assertRaises(ArchiveError) as caught:
                writer._verify_private()
        detail = str(caught.exception)
        self.assertIn("Token authenticates as @G-enterpriseGroup", detail)
        self.assertIn("Selected repositories", detail)
        self.assertNotIn("PRIVATE_TOKEN_FOR_TEST", detail)
        self.assertNotIn(self.config.token, detail)

    def test_wrong_github_account_is_identified_without_leaking_tokens(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _maximum=None):
                return b'{"login":"other-github-user"}'
        with patch.dict("os.environ", {}, clear=True), patch(
            "streamlit.secrets", {"acquisition": {"blotter_token": "PRIVATE_TOKEN_FOR_TEST"}}
        ):
            writer = PrivateTradeBlotter(self.config)
        def denied(_path, **_kwargs):
            raise ArchiveError("STREAMLIT GITHUB TOKEN CANNOT ACCESS PRIVATE PORTFOLIO (HTTP 404)")
        with patch.object(writer, "_request", side_effect=denied), patch(
            "urllib.request.urlopen", return_value=Response()
        ):
            with self.assertRaises(ArchiveError) as caught:
                writer._verify_private()
        msg = str(caught.exception)
        self.assertIn("@other-github-user", msg)
        self.assertIn("resource owner", msg)
        self.assertNotIn("PRIVATE_TOKEN_FOR_TEST", msg)

    def test_existing_archive_token_auto_fallback_only_if_private_is_verified(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "streamlit.secrets", {"acquisition": {"blotter_token": "PRIVATE_TOKEN_FOR_TEST"}}
        ):
            writer = PrivateTradeBlotter(self.config)
        attempted = []
        def private_metadata(path, **_kwargs):
            attempted.append((path, writer.token))
            if writer.token == "PRIVATE_TOKEN_FOR_TEST":
                raise ArchiveError("STREAMLIT GITHUB TOKEN CANNOT ACCESS PRIVATE PORTFOLIO (HTTP 404)")
            if writer.token == self.config.token:
                return {"full_name": PRIVATE_REPO, "private": True, "archived": False}
            raise AssertionError("unknown credential source")
        with patch.object(writer, "_request", side_effect=private_metadata):
            writer._verify_private()
        self.assertEqual([source for _path, source in attempted],
                         ["PRIVATE_TOKEN_FOR_TEST", self.config.token])
        self.assertEqual(writer.token, self.config.token)
        self.assertIn("verified private", writer.token_source)
        self.assertNotIn("PRIVATE_TOKEN_FOR_TEST", writer.token_source)

    def test_rejected_fallback_keeps_dedicated_token_and_blocks_plaintext(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "streamlit.secrets", {"acquisition": {"blotter_token": "PRIVATE_TOKEN_FOR_TEST"}}
        ):
            writer = PrivateTradeBlotter(self.config)
        tried = []
        def denied(path, **_kwargs):
            tried.append(writer.token)
            raise ArchiveError("STREAMLIT GITHUB TOKEN CANNOT ACCESS PRIVATE PORTFOLIO (HTTP 404)")
        with patch.object(writer, "_request", side_effect=denied), patch.object(
            writer, "_token_identity_diagnostic", return_value="TOKEN CHECK: repository not selected."
        ):
            with self.assertRaises(ArchiveError) as caught:
                writer._verify_private()
        self.assertEqual(tried, ["PRIVATE_TOKEN_FOR_TEST", self.config.token])
        self.assertEqual(writer.token, "PRIVATE_TOKEN_FOR_TEST")
        self.assertIn("repository not selected", str(caught.exception))
        self.assertEqual(writer.token_source, "Streamlit [acquisition] blotter_token")

    def test_private_permission_failure_checked_once_for_all_accounts(self):
        class EncryptedArchive:
            def read(self, key):
                return None
            def save(self, key, snapshot, merge):
                return snapshot
        existing = {
            ("LOCK", "RAJ"): {
                "rows": list(self.older),
                "covered_from": datetime(2024, 6, 10).date(),
                "covered_through": datetime(2026, 10, 27).date(),
                "synced_at": 123,
            },
            ("LOCK", "AMEKA"): {
                "rows": list(self.older),
                "covered_from": datetime(2024, 6, 10).date(),
                "covered_through": datetime(2026, 10, 27).date(),
                "synced_at": 123,
            },
        }
        vault = {"lock": threading.RLock(), "accounts": existing, "lots": {}}
        denied = MagicMock()
        denied._verify_private.side_effect = ArchiveError("PRIVATE PORTFOLIO PERMISSION DENIED")
        with patch("src.acquisition_ui._history_vault", return_value=vault), patch(
            "src.acquisition_ui._archive_client", return_value=EncryptedArchive()
        ), patch("src.acquisition_ui.ArchiveConfig.from_runtime", return_value=self.config), patch(
            "src.acquisition_ui.PrivateTradeBlotter", return_value=denied
        ):
            histories, errors = _sync_history(
                None, "LOCK",
                [{"accountIdKey": "RAJ"}, {"accountIdKey": "AMEKA"}],
                now=datetime(2026, 10, 27, tzinfo=ET),
            )
        self.assertEqual(denied._verify_private.call_count, 1)
        denied.save.assert_not_called()
        self.assertEqual(set(histories), {"RAJ", "AMEKA"})
        self.assertEqual(len(histories["RAJ"]["rows"]), len(self.older))
        self.assertEqual(list(errors), ["blotter"])
        self.assertIn("PERMISSION DENIED", errors["blotter"])
        self.assertFalse(vault["archive_dirty"][("LOCK", "RAJ")])
        self.assertFalse(vault["archive_dirty"][("LOCK", "AMEKA")])

    def test_isolated_account_paths_and_no_unrelated_repo(self):
        self.assertNotEqual(self.repo.paths("RAJ"), self.repo.paths("AMEKA"))
        self.assertEqual(self.repo.repo, PRIVATE_REPO)
        with self.assertRaises(ArchiveError):
            PrivateTradeBlotter(self.config, repo="G-enterpriseGroup/Screeners")

    def test_key_rotation_never_rewrites_original_account_path(self):
        rotated = PrivateGitHubInMemory(
            ArchiveConfig("TOKEN", Fernet.generate_key().decode()), self.repo.store,
        )
        self.assertNotEqual(self.repo.paths("RAJ"), rotated.paths("RAJ"))


if __name__ == "__main__":
    unittest.main()
