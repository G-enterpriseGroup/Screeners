"""Acquisition's plaintext private-GitHub long-history blotter regression tests."""
from __future__ import annotations

import base64
import csv
import io
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
from src.acquisition_ui import _build_vertical_cache, _equity_trade, _merge, _sync_history, _valid_vertical_cache, _vertical_result_fingerprint


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
            def read_vertical_cache(self, account, *, preverified=False):
                return None
            def save(self, account, snapshot, merge, parse, *, build_verticals=None):
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

    def test_latest_durable_archive_wins_same_id_correction_without_losing_old_ids(self):
        class Encrypted:
            def __init__(self, timestamp):
                self.timestamp = timestamp
            def read(self, key):
                return {
                    "rows": [tx(7, "2024-06-10", "BUY", "LMT", 10, 270)],
                    "covered_from": "2024-06-10",
                    "covered_through": "2026-10-27",
                    "synced_at": self.timestamp,
                }
            def save(self, key, snapshot, merge):
                return snapshot
        class Private:
            def _verify_private(self):
                pass
            def read_snapshot(self, key, *, preverified=False):
                return {
                    "rows": [tx(7, "2024-06-10", "BUY", "LMT", 10, 300),
                             tx(8, "2024-06-11", "SELL", "LMT", 2, 320)],
                    "covered_from": "2024-06-10",
                    "covered_through": "2026-10-27",
                    "synced_at": 2000,
                }
            def read_vertical_cache(self, key, *, preverified=False):
                return None
            def save(self, key, snapshot, merge, parse, *, build_verticals=None):
                return "https://github.com/private"
        for encrypted_ts, expected_cost in ((1000, 300), (3000, 270)):
            with self.subTest(encrypted_ts=encrypted_ts):
                vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
                with patch("src.acquisition_ui._history_vault", return_value=vault), patch(
                    "src.acquisition_ui._archive_client", return_value=Encrypted(encrypted_ts)
                ), patch("src.acquisition_ui.ArchiveConfig.from_runtime", return_value=self.config), patch(
                    "src.acquisition_ui.PrivateTradeBlotter", return_value=Private()
                ):
                    result, errors = _sync_history(
                        None, "SESSION", [{"accountIdKey": "RAJ"}],
                        now=datetime(2026, 10, 27, tzinfo=ET),
                    )
                self.assertFalse(errors)
                self.assertEqual(len(result["RAJ"]["rows"]), 2)
                self.assertEqual(
                    next(x for x in result["RAJ"]["rows"] if x["transactionId"] == 7)["brokerage"]["price"],
                    expected_cost,
                )

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

    def test_named_folders_mirror_canonical_history_and_are_collision_safe(self):
        snap = self.snapshot(self.older)
        snap.update(account_name="Raj Singh", account_last_four="5474")
        link = self.repo.save("RAJ", snap, _merge, _equity_trade)
        main_json, main_txt = self.repo.paths("RAJ")
        named_json, named_txt = self.repo.named_paths("RAJ", "Raj Singh", "5474")
        self.assertIn("Raj%20Singh%20-%205474", link)
        self.assertIn("/by-account/", named_json)
        self.assertEqual(self.repo.text(main_json), self.repo.text(named_json))
        self.assertEqual(self.repo.text(main_txt), self.repo.text(named_txt))
        self.assertEqual(self.repo.store["write_count"], 4)
        other_json, _ = self.repo.named_paths("OTHER", "Raj Singh", "5474")
        self.assertNotEqual(named_json, other_json)

    def test_name_change_keeps_stable_primary_and_original_named_backup(self):
        snap = self.snapshot(self.older)
        snap.update(account_name="Old Nickname", account_last_four="5474")
        self.repo.save("RAJ", snap, _merge, _equity_trade)
        old_json, _ = self.repo.named_paths("RAJ", "Old Nickname", "5474")
        snap.update(account_name="New Nickname")
        latest = self.repo.save("RAJ", snap, _merge, _equity_trade)
        new_json, _ = self.repo.named_paths("RAJ", "New Nickname", "5474")
        self.assertIn("New%20Nickname", latest)
        self.assertEqual(self.repo.text(old_json), self.repo.text(new_json))
        self.assertEqual(self.repo.text(self.repo.paths("RAJ")[0]), self.repo.text(new_json))
        self.assertEqual(self.repo.read_snapshot("RAJ")["rows"], self.older)

    def test_named_paths_strip_traversal_and_only_publish_to_private(self):
        h, t = self.repo.named_paths("RAJ", "../../Trading/../", "005474")
        self.assertTrue(h.startswith("acquisition/history/by-account/"))
        self.assertNotIn("../", h)
        self.assertNotIn("/../", h)
        self.assertIn("5474", h)
        self.assertEqual(h.rsplit("/", 1)[0], t.rsplit("/", 1)[0])
        self.repo.private = False
        with self.assertRaises(ArchiveError):
            self.repo.save("RAJ", {**self.snapshot(self.older),
                          "account_name": "Raj Singh"}, _merge, _equity_trade)
        self.assertEqual(self.repo.store["write_count"], 0)

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
        a_json, a_txt = self.repo.paths("RAJ")
        b_json, _ = self.repo.paths("AMEKA")
        self.assertEqual(a_json.rsplit("/", 1)[0], a_txt.rsplit("/", 1)[0])
        self.assertNotEqual(a_json.rsplit("/", 1)[0], b_json.rsplit("/", 1)[0])
        self.assertTrue(a_json.endswith("/history.json"))
        self.assertTrue(a_txt.endswith("/trades.txt"))
        self.assertNotIn("RAJ", a_json)
        self.assertEqual(self.repo.repo, PRIVATE_REPO)
        with self.assertRaises(ArchiveError):
            PrivateTradeBlotter(self.config, repo="G-enterpriseGroup/Screeners")

    def test_flat_legacy_files_restore_and_migrate_without_losing_trades(self):
        legacy_json, legacy_txt = self.repo._legacy_paths("RAJ")
        legacy = {
            "version": 1, "account": "RAJ", "rows": self.older,
            "covered_from": "2024-06-10", "covered_through": "2026-10-27",
        }
        contents = json.dumps(legacy) + "\n"
        self.repo.store["files"][legacy_json] = {
            "sha": "old-json", "content": base64.b64encode(contents.encode()).decode(),
        }
        self.repo.store["files"][legacy_txt] = {
            "sha": "old-txt", "content": base64.b64encode(b"legacy blotter").decode(),
        }
        self.assertEqual(len(self.repo.read_snapshot("RAJ")["rows"]), 4)
        self.repo.save("RAJ", self.snapshot([
            tx(11, "2026-10-26", "SELL", "MSFT", 1, 550),
        ]), _merge, _equity_trade)
        current_json, current_txt = self.repo.paths("RAJ")
        new_rows = json.loads(self.repo.text(current_json))["rows"]
        self.assertEqual({x["transactionId"] for x in new_rows}, {7, 8, 9, 10, 11})
        self.assertIn("MSFT 10.26.2026 550 1 S", self.repo.text(current_txt))
        self.assertEqual(self.repo.text(legacy_json), contents)
        self.assertEqual(self.repo.text(legacy_txt), "legacy blotter")
        self.assertEqual(len(self.repo.read_snapshot("RAJ")["rows"]), 5)

    def test_corrupt_flat_legacy_history_blocks_new_folder_write(self):
        legacy_json, _ = self.repo._legacy_paths("RAJ")
        self.repo.store["files"][legacy_json] = {
            "sha": "bad", "content": base64.b64encode(b"not json").decode(),
        }
        with self.assertRaises(ArchiveError):
            self.repo.save("RAJ", self.snapshot(self.older), _merge, _equity_trade)
        self.assertEqual(self.repo.store["write_count"], 0)

    def test_key_rotation_never_rewrites_original_account_path(self):
        rotated = PrivateGitHubInMemory(
            ArchiveConfig("TOKEN", Fernet.generate_key().decode()), self.repo.store,
        )
        self.assertNotEqual(self.repo.paths("RAJ"), rotated.paths("RAJ"))


class PrivateVerticalCacheTests(unittest.TestCase):
    columns = ("ticker", "type", "expiry", "long", "short", "qty", "status", "opened", "closed",
               "open_net", "close_net", "fees", "pnl", "currency", "match", "basis", "open_ids", "close_ids")

    def setUp(self):
        self.config = ArchiveConfig("FAKE_GITHUB_TOKEN", Fernet.generate_key().decode())
        self.repo = PrivateGitHubInMemory(self.config)
        self.rows = [tx(1, "2024-06-10", "BUY", "SYN", 2, 10)]
        self.snapshot = {"rows": self.rows, "covered_from": "2024-06-10", "covered_through": "2026-10-27"}

    def publish(self, payload=None, **kwargs):
        return self.repo.save_vertical_cache(
            "SYN_ACCOUNT", payload if payload is not None else _build_vertical_cache(self.rows),
            covered_from=self.snapshot["covered_from"], covered_through=self.snapshot["covered_through"], **kwargs,
        )

    def inject(self, payload, account="SYN_ACCOUNT"):
        path, _ = self.repo.vertical_paths(account)
        content = payload if isinstance(payload, str) else json.dumps(payload)
        self.repo.store["files"][path] = {
            "sha": "fixture-blob", "content": base64.b64encode(content.encode()).decode(),
        }

    def sync(self, vault, broker, current):
        class Encrypted:
            def read(self, account):
                return None
            def save(self, account, snapshot, merge):
                return snapshot
        with patch("src.acquisition_ui._history_vault", return_value=vault), patch(
            "src.acquisition_ui._archive_client", return_value=Encrypted()
        ), patch("src.acquisition_ui.ArchiveConfig.from_runtime", return_value=self.config), patch(
            "src.acquisition_ui.PrivateTradeBlotter", return_value=self.repo
        ), patch("src.acquisition_ui.time.time", return_value=current):
            return _sync_history(broker, "SYN_ACCESS", [{"accountIdKey": "SYN_ACCOUNT"}],
                                 now=datetime(2026, 10, 27, tzinfo=ET))

    def test_paths_share_existing_account_folders_and_keep_accounts_separate(self):
        for name in ("", "../../Synthetic Account/"):
            with self.subTest(name=name):
                source, _ = (self.repo.named_paths("SYN_ACCOUNT", name, "1000") if name
                             else self.repo.paths("SYN_ACCOUNT"))
                report, csv_path = self.repo.vertical_paths("SYN_ACCOUNT", name, "1000")
                self.assertEqual(report.rsplit("/", 1)[0], source.rsplit("/", 1)[0])
                self.assertTrue(report.endswith("/vertical_blotter.json"))
                self.assertTrue(csv_path.endswith("/vertical_blotter.csv"))
                self.assertNotIn("../", report)
                self.assertNotEqual(report, self.repo.vertical_paths("OTHER_ACCOUNT", name, "1000")[0])

    def test_save_publishes_matching_json_csv_and_named_mirrors_with_zero_values(self):
        payload = _build_vertical_cache(self.rows)
        payload["blotter"] = [{
            "ticker": "SYN", "type": "CALL", "expiry": "2027-01-15", "long": 100, "short": 105,
            "qty": 1, "status": "CLOSED", "opened": "2026-10-01T10:00:00-04:00",
            "closed": "2026-10-02T10:00:00-04:00", "open_net": 2, "close_net": 2,
            "fees": 0, "pnl": 0, "currency": "USD", "match": "ORDER", "basis": "Synthetic, verified cash",
            "open_ids": ["101", "102"], "close_ids": ["103", "104"],
        }]
        payload["result_fingerprint"] = _vertical_result_fingerprint(payload)
        envelope = self.publish(payload, account_name="Synthetic Account", last_four="1000")
        canonical, readable = self.repo.vertical_paths("SYN_ACCOUNT")
        named_json, named_csv = self.repo.vertical_paths("SYN_ACCOUNT", "Synthetic Account", "1000")
        self.assertEqual(json.loads(self.repo.text(canonical)), envelope)
        self.assertEqual(self.repo.text(canonical), self.repo.text(named_json))
        self.assertEqual(self.repo.text(readable), self.repo.text(named_csv))
        csv_rows = list(csv.DictReader(io.StringIO(self.repo.text(readable))))
        self.assertEqual(tuple(csv_rows[0]), self.columns)
        self.assertEqual(csv_rows[0]["pnl"], "0")
        self.assertEqual(csv_rows[0]["fees"], "0")
        self.assertEqual(csv_rows[0]["basis"], payload["blotter"][0]["basis"])
        self.assertEqual(json.loads(csv_rows[0]["open_ids"]), ["101", "102"])
        self.assertEqual(self.repo.vertical_payload, envelope)
        self.assertEqual(self.repo.vertical_error, "")
        self.assertEqual(self.repo.read_vertical_cache("SYN_ACCOUNT"), envelope)
        self.assertIsNone(self.repo.read_snapshot("SYN_ACCOUNT"))  # Derived data cannot bootstrap raw history.

    def test_empty_cache_has_headers_and_unchanged_save_does_not_write(self):
        self.assertIsNone(self.repo.read_vertical_cache("SYN_ACCOUNT"))
        self.publish()
        self.assertEqual(self.repo.text(self.repo.vertical_paths("SYN_ACCOUNT")[1]), ",".join(self.columns) + "\n")
        writes = self.repo.store["write_count"]
        self.publish()
        self.assertEqual(self.repo.store["write_count"], writes)

    def test_private_gate_blocks_direct_read_and_publish(self):
        self.repo.private = False
        with self.assertRaises(ArchiveError):
            self.publish()
        with self.assertRaises(ArchiveError):
            self.repo.read_vertical_cache("SYN_ACCOUNT")
        self.assertEqual(self.repo.store["write_count"], 0)

    def test_corrupt_or_cross_account_envelopes_are_rejected(self):
        valid = self.publish()
        cases = [
            "not JSON", [], {**valid, "account": "OTHER_ACCOUNT"}, {**valid, "schema_version": True},
            {**valid, "schema_version": 2}, {**valid, "algorithm_version": True},
            {**valid, "algorithm_version": 1.5}, {**valid, "algorithm_version": 0},
            {**valid, "source_fingerprint": "bad"}, {**valid, "result_fingerprint": "bad"},
            {**valid, "result_fingerprint": "0" * 64},
            {**valid, "events": {}}, {**valid, "review": ["not a record"]},
            {**valid, "blotter": None}, {**valid, "covered_from": "bad"},
            {**valid, "covered_through": "2020-01-01"}, {**valid, "blotter": [{"pnl": float("nan")}]},
        ]
        for payload in cases:
            with self.subTest(payload_type=type(payload).__name__):
                self.inject(payload)
                with self.assertRaises(ArchiveError):
                    self.repo.read_vertical_cache("SYN_ACCOUNT")
        self.assertIsNone(self.repo.read_snapshot("SYN_ACCOUNT"))

    def test_non_native_payload_cannot_be_published(self):
        for value in (datetime(2026, 10, 1), ("tuple",), float("inf")):
            with self.subTest(value_type=type(value).__name__):
                payload = {**_build_vertical_cache(self.rows), "review": [{"bad": value}]}
                with self.assertRaises(ArchiveError):
                    self.publish(payload)
        self.assertEqual(self.repo.store["write_count"], 0)

    def test_stale_source_or_algorithm_is_never_accepted_by_owner(self):
        valid = self.publish()
        self.assertTrue(_valid_vertical_cache(valid, valid["source_fingerprint"]))
        for changed in ({**valid, "source_fingerprint": "0" * 64}, {**valid, "algorithm_version": 2}):
            self.inject(changed)
            restored = self.repo.read_vertical_cache("SYN_ACCOUNT")
            self.assertFalse(_valid_vertical_cache(restored, valid["source_fingerprint"]))
        self.assertIsNone(self.repo.read_snapshot("SYN_ACCOUNT"))

    def test_callback_failure_retains_verified_raw_receipt_and_is_redacted(self):
        builder = MagicMock(side_effect=RuntimeError("PRIVATE SYNTHETIC SOURCE SHOULD NOT DISPLAY"))
        url = self.repo.save("SYN_ACCOUNT", self.snapshot, _merge, _equity_trade, build_verticals=builder)
        self.assertEqual(url, self.repo.page_url("SYN_ACCOUNT"))
        self.assertEqual(self.repo.read_snapshot("SYN_ACCOUNT")["rows"], self.rows)
        self.assertEqual(self.repo.canonical_snapshot["rows"], self.rows)
        self.assertIn("VERTICAL BLOTTER BACKUP FAILED", self.repo.vertical_error)
        self.assertNotIn("PRIVATE SYNTHETIC", self.repo.vertical_error)
        self.assertIsNone(self.repo.vertical_payload)
        self.assertIsNone(self.repo.read_vertical_cache("SYN_ACCOUNT"))
        self.repo.save("SYN_ACCOUNT", self.snapshot, _merge, _equity_trade, build_verticals=_build_vertical_cache)
        self.assertEqual(self.repo.vertical_error, "")
        self.assertIsNotNone(self.repo.vertical_payload)

    def test_derived_upload_failure_does_not_hide_raw_success(self):
        request = self.repo._request
        def fail_derived(path, *, payload=None, ref=BRANCH):
            if path.endswith("vertical_blotter.csv") and payload is not None:
                raise ArchiveError("Synthetic derived upload failed")
            return request(path, payload=payload, ref=ref)
        with patch.object(self.repo, "_request", side_effect=fail_derived):
            url = self.repo.save("SYN_ACCOUNT", self.snapshot, _merge, _equity_trade,
                                 build_verticals=_build_vertical_cache)
        self.assertEqual(url, self.repo.page_url("SYN_ACCOUNT"))
        self.assertEqual(self.repo.read_snapshot("SYN_ACCOUNT")["rows"], self.rows)
        self.assertTrue(self.repo.vertical_error)
        self.assertIsNone(self.repo.vertical_payload)
        # A standalone retry completes the report without touching raw history.
        raw_before = self.repo.text(self.repo.paths("SYN_ACCOUNT")[0])
        self.publish()
        self.assertEqual(self.repo.text(self.repo.paths("SYN_ACCOUNT")[0]), raw_before)
        self.assertEqual(self.repo.vertical_error, "")

    def test_missing_or_wrong_immutable_upload_receipt_is_a_derived_failure(self):
        for missing_commit in (True, False):
            with self.subTest(missing_commit=missing_commit):
                repo = PrivateGitHubInMemory(self.config)
                request = repo._request
                def invalid_receipt(path, *, payload=None, ref=BRANCH):
                    if "vertical_blotter" in path:
                        if payload is not None and missing_commit:
                            return None
                        if payload is None and ref != BRANCH:
                            return None
                    return request(path, payload=payload, ref=ref)
                with patch.object(repo, "_request", side_effect=invalid_receipt):
                    url = repo.save("SYN_ACCOUNT", self.snapshot, _merge, _equity_trade,
                                    build_verticals=_build_vertical_cache)
                self.assertEqual(url, repo.page_url("SYN_ACCOUNT"))
                self.assertTrue(repo.vertical_error)
                self.assertIsNone(repo.vertical_payload)

    def test_raw_verification_or_raw_mirror_failure_still_raises(self):
        for suffix in ("history.json", "trades.txt"):
            with self.subTest(suffix=suffix):
                repo = PrivateGitHubInMemory(self.config)
                request = repo._request
                builder = MagicMock(side_effect=AssertionError("Must not build before raw receipt"))
                def invalid_raw(path, *, payload=None, ref=BRANCH):
                    if path.endswith(suffix) and payload is not None:
                        return None
                    return request(path, payload=payload, ref=ref)
                with patch.object(repo, "_request", side_effect=invalid_raw):
                    with self.assertRaises(ArchiveError):
                        repo.save("SYN_ACCOUNT", self.snapshot, _merge, _equity_trade, build_verticals=builder)
                builder.assert_not_called()
                self.assertIsNone(repo.canonical_snapshot)

    def test_canonical_cas_retry_builds_from_remote_older_rows_and_corrections(self):
        remote = PrivateGitHubInMemory(self.config, self.repo.store)
        old_rows = [tx(2, "2023-01-10", "BUY", "OLD", 1, 20), tx(1, "2024-06-10", "BUY", "SYN", 2, 9)]
        request = self.repo._request
        raw_path, _ = self.repo.paths("SYN_ACCOUNT")
        conflicted = False
        def race(path, *, payload=None, ref=BRANCH):
            nonlocal conflicted
            if path == raw_path and payload is not None and not conflicted:
                conflicted = True
                remote.save("SYN_ACCOUNT", {**self.snapshot, "rows": old_rows,
                                           "covered_from": "2023-01-10"}, _merge, _equity_trade)
            return request(path, payload=payload, ref=ref)
        builder = MagicMock(side_effect=_build_vertical_cache)
        with patch.object(self.repo, "_request", side_effect=race), patch("src.acquisition_private_blotter.time.sleep"):
            self.repo.save("SYN_ACCOUNT", self.snapshot, _merge, _equity_trade, build_verticals=builder)
        self.assertTrue(conflicted)
        self.assertEqual(builder.call_count, 1)
        canonical = self.repo.canonical_snapshot
        self.assertEqual({row["transactionId"] for row in canonical["rows"]}, {1, 2})
        self.assertEqual(canonical["covered_from"], "2023-01-10")
        self.assertEqual(next(row for row in canonical["rows"] if row["transactionId"] == 1)["brokerage"]["price"], 10)
        self.assertEqual(builder.call_args.args[0], canonical["rows"])
        self.assertEqual(self.repo.vertical_payload["source_fingerprint"],
                         _build_vertical_cache(canonical["rows"])["source_fingerprint"])

    def test_warm_restart_uses_durable_cache_and_unchanged_rerun_has_no_network(self):
        current = datetime(2026, 10, 27, tzinfo=ET).timestamp()
        self.repo.save("SYN_ACCOUNT", {**self.snapshot, "synced_at": current}, _merge, _equity_trade,
                       build_verticals=_build_vertical_cache)
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        broker = MagicMock(is_offline=False)
        with patch("src.acquisition_ui._build_vertical_cache", side_effect=AssertionError("Warm cache must be reused")), patch.object(
            self.repo, "_request", wraps=self.repo._request
        ) as requests:
            snapshots, errors = self.sync(vault, broker, current)
            self.assertFalse(errors)
            self.assertEqual(snapshots["SYN_ACCOUNT"]["rows"], self.rows)
            self.assertIn(("SYN_ACCESS", "SYN_ACCOUNT"), vault["vertical_cache_saved"])
            self.assertTrue(requests.called)
            requests.reset_mock()
            self.sync(vault, broker, current)
            requests.assert_not_called()
        broker.get_transactions.assert_not_called()

    def test_corrupt_warm_cache_rebuilds_without_refetching_source_history(self):
        current = datetime(2026, 10, 27, tzinfo=ET).timestamp()
        self.repo.save("SYN_ACCOUNT", {**self.snapshot, "synced_at": current}, _merge, _equity_trade,
                       build_verticals=_build_vertical_cache)
        self.inject("corrupted derived JSON")
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        broker = MagicMock(is_offline=False)
        with patch("src.acquisition_ui._build_vertical_cache", wraps=_build_vertical_cache) as build:
            snapshots, errors = self.sync(vault, broker, current)
        self.assertFalse(errors)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(snapshots["SYN_ACCOUNT"]["rows"], self.rows)
        self.assertEqual(self.repo.read_snapshot("SYN_ACCOUNT")["rows"], self.rows)
        self.assertIsNotNone(self.repo.read_vertical_cache("SYN_ACCOUNT"))
        self.assertFalse(vault["vertical_cache_error"])
        broker.get_transactions.assert_not_called()

    def test_empty_recent_sync_persists_advanced_checkpoint_without_full_refetch(self):
        current = datetime(2026, 10, 27, tzinfo=ET).timestamp()
        self.repo.save("SYN_ACCOUNT", {**self.snapshot, "covered_through": "2026-10-26", "synced_at": 1},
                       _merge, _equity_trade, build_verticals=_build_vertical_cache)
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        broker = MagicMock(is_offline=False)
        broker.get_transactions.return_value = []
        snapshots, errors = self.sync(vault, broker, current)
        self.assertFalse(errors)
        broker.get_transactions.assert_called_once_with("SYN_ACCOUNT", start_date=date(2026, 10, 19),
                                                        end_date=date(2026, 10, 27), sort_order="ASC")
        self.assertEqual(snapshots["SYN_ACCOUNT"]["rows"], self.rows)
        raw = self.repo.read_snapshot("SYN_ACCOUNT")
        self.assertEqual(raw["covered_through"], "2026-10-27")
        self.assertEqual(raw["synced_at"], current)
        self.assertEqual(self.repo.read_vertical_cache("SYN_ACCOUNT")["covered_through"], "2026-10-27")

    def test_sync_keeps_raw_receipt_and_visible_separate_derived_failure_with_retry_throttle(self):
        current = datetime(2026, 10, 27, tzinfo=ET).timestamp()
        self.repo.save("SYN_ACCOUNT", {**self.snapshot, "synced_at": current}, _merge, _equity_trade)
        vault = {"lock": threading.RLock(), "accounts": {}, "lots": {}}
        broker = MagicMock(is_offline=False)
        request = self.repo._request
        def fail_csv(path, *, payload=None, ref=BRANCH):
            if path.endswith("vertical_blotter.csv") and payload is not None:
                raise ArchiveError("Synthetic derived upload failed")
            return request(path, payload=payload, ref=ref)
        with patch.object(self.repo, "_request", side_effect=fail_csv) as requests:
            snapshots, errors = self.sync(vault, broker, current)
            self.assertFalse(errors)
            scope = ("SYN_ACCESS", "SYN_ACCOUNT")
            self.assertEqual(snapshots["SYN_ACCOUNT"]["rows"], self.rows)
            self.assertIn("VERTICAL BLOTTER BACKUP FAILED", vault["vertical_cache_error"][scope])
            self.assertEqual(vault["blotter_url"][scope], self.repo.page_url("SYN_ACCOUNT", "E*TRADE Account"))
            self.assertEqual(vault["blotter_retry_at"][scope], current + 300)
            requests.reset_mock()
            self.sync(vault, broker, current)
            requests.assert_not_called()
        broker.get_transactions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
