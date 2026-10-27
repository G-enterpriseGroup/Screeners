"""Acquisition's plaintext private-GitHub long-history blotter regression tests."""
from __future__ import annotations

import base64
import json
from datetime import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from src.acquisition_private_blotter import PrivateTradeBlotter, share_blotter, PRIVATE_REPO, BRANCH
from src.acquisition_history_github import ArchiveConfig, ArchiveError
from src.acquisition_ui import _equity_trade, _merge


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
