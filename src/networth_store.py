"""Networth-only encrypted, private GitHub persistence with optimistic merges."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
import hashlib
import hmac
import json
import os
import re
from typing import Any
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
import requests


class NetworthError(RuntimeError):
    """A redacted, actionable Networth persistence failure."""


class _Conflict(Exception):
    pass


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\Z")
_MAX_DOCUMENT_BYTES = 512 * 1024
_MAX_HTTP_BYTES = 2 * 1024 * 1024


def _valid_branch(value: str) -> bool:
    return bool(
        value and len(value) <= 200 and value != "@"
        and not re.search(r"[\s\x00-\x1f\x7f~^:?*\[\\%#]", value)
        and ".." not in value and "@{" not in value
        and all(part and not part.startswith((".", "-"))
                and not part.endswith((".", ".lock")) for part in value.split("/"))
    )


@dataclass(frozen=True)
class StoreConfig:
    github_token: str = field(repr=False)
    encryption_key: str = field(repr=False)
    repository: str = "G-enterpriseGroup/Portfolio"
    branch: str = "networth-data"
    ledger_id: str = field(default="raj-terminal", repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.github_token, str) or not self.github_token.strip():
            raise NetworthError("Networth GitHub token missing; configure [networth].github_token.")
        if not isinstance(self.encryption_key, str) or not self.encryption_key.strip():
            raise NetworthError("Networth encryption key missing; configure [networth].encryption_key.")
        try:
            Fernet(self.encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeError):
            raise NetworthError("Networth encryption key invalid; use a Fernet key.") from None
        if (not isinstance(self.repository, str) or not _REPOSITORY.fullmatch(self.repository)
                or any(part in {".", ".."} for part in self.repository.split("/"))):
            raise NetworthError("Networth repository invalid; use owner/repository.")
        if not isinstance(self.branch, str) or not _valid_branch(self.branch):
            raise NetworthError("Networth data branch invalid.")
        if not isinstance(self.ledger_id, str) or not self.ledger_id.strip() or len(self.ledger_id) > 200:
            raise NetworthError("Networth ledger ID invalid; use a nonempty stable ID.")

    @classmethod
    def from_runtime(cls, secrets: Mapping | None = None) -> StoreConfig | None:
        if secrets is None:
            try:
                import streamlit as st

                secrets = st.secrets
                section = secrets.get("networth", {})
            except (FileNotFoundError, KeyError):
                section = {}
        else:
            section = secrets.get("networth", {})
        if not isinstance(section, Mapping):
            raise NetworthError("Networth settings invalid; use a [networth] secrets section.")
        fields = ("github_token", "encryption_key", "repository", "branch", "ledger_id")
        values = {}
        configured = False
        for name in fields:
            env_name = "NETWORTH_" + name.upper()
            if name in section:
                value = section[name]
                configured = True
            elif env_name in os.environ:
                value = os.environ[env_name]
                configured = True
            else:
                continue
            if not isinstance(value, str):
                raise NetworthError("Networth setting invalid; all store settings must be strings.")
            values[name] = value.strip()
        if not configured:
            return None
        values.setdefault("github_token", "")
        values.setdefault("encryption_key", "")
        return cls(**values)


def _initial_state() -> dict:
    return {"version": 1, "items": {}, "accounts": {}, "preferences": {}, "pending_links": {}}


def _validate_document(value: Any, *, day: bool) -> None:
    required = ("captures",) if day else ("items", "accounts", "preferences", "pending_links")
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
            or any(not isinstance(value.get(name), dict) for name in required)):
        raise NetworthError("Networth saved document invalid; existing data was preserved.")


class GitHubNetworthStore:
    """Encrypted state plus separate daily snapshots; never uses another feature's store."""

    def __init__(self, config: StoreConfig):
        self.config = config
        self._fernet = Fernet(config.encryption_key.encode("ascii"))
        key = base64.urlsafe_b64decode(config.encryption_key)
        self.scope = hmac.new(key, b"networth-ledger:v1:" + config.ledger_id.encode(), hashlib.sha256).hexdigest()
        self._base = "https://api.github.com/repos/" + config.repository

    def _request(self, method: str, suffix: str = "", *, params=None, payload=None,
                 missing_ok: bool = False, conflict_ok: bool = False) -> dict | None:
        try:
            response = requests.request(
                method, self._base + suffix,
                headers={"Authorization": "Bearer " + self.config.github_token,
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                params=params, json=payload, timeout=(5, 30), stream=True, allow_redirects=False,
            )
            try:
                status = response.status_code
                if status == 404 and missing_ok:
                    return None
                if status in (409, 422) and conflict_ok:
                    raise _Conflict()
                if status not in (200, 201):
                    if status in (401, 403, 404):
                        raise NetworthError(
                            "Networth private GitHub access denied (HTTP " + str(status)
                            + "); check the token's Portfolio repository and Contents read/write access."
                        )
                    raise NetworthError("Networth GitHub request failed (HTTP " + str(status) + "); retry later.")
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw) > _MAX_HTTP_BYTES:
                        raise NetworthError("Networth GitHub response too large; existing data was preserved.")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise NetworthError("Networth GitHub response invalid; existing data was preserved.")
                return result
            finally:
                response.close()
        except requests.RequestException:
            raise NetworthError("Networth GitHub unavailable; existing data was preserved. Retry later.") from None
        except (ValueError, UnicodeError):
            raise NetworthError("Networth GitHub response invalid; existing data was preserved.") from None

    def _ref(self, branch: str, *, missing_ok: bool = False) -> dict | None:
        result = self._request("GET", "/git/ref/heads/" + quote(branch, safe="/"), missing_ok=missing_ok)
        if result is not None:
            obj = result.get("object")
            if (result.get("ref") != "refs/heads/" + branch or not isinstance(obj, dict)
                    or obj.get("type") != "commit" or not _SHA.fullmatch(str(obj.get("sha", "")))):
                raise NetworthError("Networth GitHub branch verification failed; existing data was preserved.")
        return result

    def _prepare(self) -> None:
        metadata = self._request("GET")
        if metadata.get("private") is not True:
            raise NetworthError("Networth storage requires a verified PRIVATE repository; no data was read or written.")
        default = metadata.get("default_branch")
        if not isinstance(default, str) or not _valid_branch(default):
            raise NetworthError("Networth GitHub default branch invalid; no data was written.")
        if self.config.branch == default:
            raise NetworthError("Networth requires a dedicated data branch distinct from the repository default branch.")
        if self._ref(self.config.branch, missing_ok=True) is not None:
            return
        # A missing repository/default ref is never treated as an empty ledger.
        source = self._ref(default)
        try:
            self._request("POST", "/git/refs", payload={
                "ref": "refs/heads/" + self.config.branch, "sha": source["object"]["sha"],
            }, conflict_ok=True)
        except _Conflict:
            pass  # A competing initializer must still pass the exact reference check.
        self._ref(self.config.branch)

    def _path(self, document: str) -> str:
        return "networth/" + self.scope + "/" + document

    def _read_document(self, document: str, *, ref: str | None = None) -> tuple[dict | None, str | None, bytes | None]:
        path = self._path(document)
        result = self._request("GET", "/contents/" + quote(path, safe="/"),
                               params={"ref": ref or self.config.branch}, missing_ok=ref is None)
        if result is None:
            self._ref(self.config.branch)
            return None, None, None
        sha = result.get("sha")
        if (result.get("type") != "file" or result.get("encoding") != "base64"
                or not isinstance(sha, str) or not _SHA.fullmatch(sha)
                or not isinstance(result.get("content"), str)):
            raise NetworthError("Networth saved file invalid; existing data was preserved.")
        try:
            encoded = "".join(result["content"].splitlines())
            ciphertext = base64.b64decode(encoded, validate=True)
            if len(ciphertext) > _MAX_HTTP_BYTES:
                raise NetworthError("Networth saved file too large; existing data was preserved.")
            if hashlib.sha1(b"blob " + str(len(ciphertext)).encode() + b"\0" + ciphertext).hexdigest() != sha:
                raise NetworthError("Networth saved file verification failed; existing data was preserved.")
            plaintext = self._fernet.decrypt(ciphertext)
            if len(plaintext) > _MAX_DOCUMENT_BYTES:
                raise NetworthError("Networth saved document too large; existing data was preserved.")
            envelope = json.loads(plaintext)
        except (ValueError, UnicodeError, InvalidToken):
            raise NetworthError("Networth saved data could not be authenticated; check the encryption key. Existing data was preserved.") from None
        if (not isinstance(envelope, dict) or envelope.get("format") != "networth-fernet-v1"
                or envelope.get("scope") != self.scope or envelope.get("path") != path):
            raise NetworthError("Networth saved data scope mismatch; existing data was preserved.")
        value = envelope.get("data")
        _validate_document(value, day=document != "state.enc")
        return value, sha, ciphertext

    def _encode(self, document: str, value: dict) -> bytes:
        try:
            plaintext = json.dumps({"format": "networth-fernet-v1", "scope": self.scope,
                                    "path": self._path(document), "data": value},
                                   allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
        except (TypeError, ValueError, UnicodeError):
            raise NetworthError("Networth data invalid; no data was written.") from None
        if len(plaintext) > _MAX_DOCUMENT_BYTES:
            raise NetworthError("Networth document too large; existing data was preserved.")
        return self._fernet.encrypt(plaintext)

    def _write_document(self, document: str, value: dict, sha: str | None) -> None:
        ciphertext = self._encode(document, value)
        payload = {"message": "Update encrypted Networth ledger", "branch": self.config.branch,
                   "content": base64.b64encode(ciphertext).decode("ascii")}
        if sha is not None:
            payload["sha"] = sha
        result = self._request("PUT", "/contents/" + quote(self._path(document), safe="/"),
                               payload=payload, conflict_ok=True)
        commit = result.get("commit")
        commit_sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
            raise NetworthError("Networth save could not be verified; retry before treating it as saved.")
        restored, _, committed_ciphertext = self._read_document(document, ref=commit_sha)
        if committed_ciphertext != ciphertext or restored != value:
            raise NetworthError("Networth immutable save verification failed; retry before treating it as saved.")

    def read_state(self) -> dict:
        self._prepare()
        value, _, _ = self._read_document("state.enc")
        return value if value is not None else _initial_state()

    def update_state(self, callback: Callable[[dict], dict | None]) -> dict:
        """Apply a repeatable mutator to the latest state; preserve competing edits."""
        self._prepare()
        for _ in range(4):
            current, sha, _ = self._read_document("state.enc")
            current = current if current is not None else _initial_state()
            updated = deepcopy(current)
            replacement = callback(updated)
            if replacement is not None:
                updated = replacement
            _validate_document(updated, day=False)
            if updated == current:
                return deepcopy(updated)
            try:
                self._write_document("state.enc", updated, sha)
                return deepcopy(updated)
            except _Conflict:
                continue
        raise NetworthError("Networth state changed concurrently; existing data was preserved. Retry the action.")

    @staticmethod
    def _day_document(day: str) -> str:
        try:
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError:
            raise NetworthError("Networth date invalid; use YYYY-MM-DD.") from None
        return "days/" + day + ".enc"

    def read_day(self, day: str) -> dict | None:
        document = self._day_document(day)
        self._prepare()
        value, _, _ = self._read_document(document)
        return value

    def save_capture(self, day: str, capture_id: str, capture: dict) -> None:
        document = self._day_document(day)
        if not isinstance(capture_id, str) or not capture_id.strip() or len(capture_id) > 200 or not isinstance(capture, dict):
            raise NetworthError("Networth capture invalid; no data was written.")
        self._prepare()
        for _ in range(4):
            current, sha, _ = self._read_document(document)
            updated = deepcopy(current) if current is not None else {"version": 1, "captures": {}}
            if capture_id in updated["captures"]:
                if updated["captures"][capture_id] != capture:
                    raise NetworthError("Networth capture ID already saved with different data; existing capture was preserved.")
                return
            updated["captures"][capture_id] = deepcopy(capture)
            try:
                self._write_document(document, updated, sha)
                return
            except _Conflict:
                continue
        raise NetworthError("Networth daily captures changed concurrently; existing data was preserved. Retry the sync.")
