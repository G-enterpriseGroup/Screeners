"""Authentication-only encrypted GitHub state with atomic compare-and-swap writes.

No financial store is imported or read. A new authentication branch is an orphan
commit containing only ciphertext, never a copy of a financial-data branch.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
import requests


class SecurityStoreError(RuntimeError):
    """Sanitized authentication-state failure; callers must deny access."""


class _Conflict(Exception):
    pass


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\Z")
_BRANCH = re.compile(r"terminal-security-[A-Za-z0-9][A-Za-z0-9_-]{0,100}\Z")
_PATH = "auth-state.enc"
_MAX_HTTP_BYTES = 2 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 512 * 1024


def initial_state() -> dict:
    return {"version": 1, "identities": {}, "counters": {}, "recovery": {}}


def _validate_state(state: object) -> None:
    if (not isinstance(state, dict) or state.get("version") != 1
            or type(state.get("version")) is not int
            or any(not isinstance(state.get(key), dict) for key in ("identities", "counters"))
            or any(len(state[key]) > 10000 for key in ("identities", "counters"))):
        raise SecurityStoreError("Authentication state is invalid; access remains locked.")
    if (not isinstance(state.get("recovery", {}), dict)
            or len(state.get("recovery", {})) > 10000):
        raise SecurityStoreError("Authentication recovery state is invalid; access remains locked.")
    for key, row in state["identities"].items():
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(row, dict):
            raise SecurityStoreError("Authentication state is invalid; access remains locked.")
        if set(row) - {"failures", "locked_until", "pin_override"} or not {"failures", "locked_until"} <= set(row):
            raise SecurityStoreError("Authentication state is invalid; access remains locked.")
        override = row.get("pin_override")
        if override is not None:
            if (not isinstance(override, dict)
                    or set(override) != {"base_credential", "encoded_hash", "revision", "changed_at"}
                    or not re.fullmatch(r"[0-9a-f]{64}", str(override.get("base_credential", "")))
                    or not re.fullmatch(r"[0-9a-f]{32,64}", str(override.get("revision", "")))
                    or not re.fullmatch(r"pbkdf2_sha256\$600000\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}", str(override.get("encoded_hash", "")))
                    or type(override.get("changed_at")) not in (int, float)
                    or not 0 <= override["changed_at"] <= 253402300799):
                raise SecurityStoreError("Authentication credential state is invalid; access remains locked.")
        if (type(row["failures"]) is not int or not 0 <= row["failures"] <= 1000000
                or type(row["locked_until"]) not in (int, float)
                or not 0 <= row["locked_until"] <= 253402300799):
            raise SecurityStoreError("Authentication state is invalid; access remains locked.")
    for key, counter in state["counters"].items():
        if (not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) or type(counter) is not int
                or not -1 <= counter <= 10**12):
            raise SecurityStoreError("Authentication state is invalid; access remains locked.")


@dataclass(frozen=True)
class SecurityStoreConfig:
    github_token: str = field(repr=False)
    encryption_key: str = field(repr=False)
    repository: str = "G-enterpriseGroup/Portfolio"
    branch: str = "terminal-security-state"

    def __post_init__(self) -> None:
        if not isinstance(self.github_token, str) or not self.github_token.strip():
            raise SecurityStoreError("Authentication storage requires a server-side GitHub token.")
        if not isinstance(self.encryption_key, str):
            raise SecurityStoreError("Authentication storage requires a valid Fernet key.")
        try:
            Fernet(self.encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeError):
            raise SecurityStoreError("Authentication storage requires a valid Fernet key.") from None
        if (not isinstance(self.repository, str) or not _REPOSITORY.fullmatch(self.repository)
                or any(part in {".", ".."} for part in self.repository.split("/"))):
            raise SecurityStoreError("Authentication storage repository is invalid.")
        if not isinstance(self.branch, str) or not _BRANCH.fullmatch(self.branch):
            raise SecurityStoreError("Use a dedicated terminal-security- authentication branch.")

    @classmethod
    def from_runtime(cls, secrets: Mapping) -> SecurityStoreConfig:
        if not isinstance(secrets, Mapping):
            raise SecurityStoreError("Authentication storage configuration is missing.")
        security = secrets.get("security", {})
        networth = secrets.get("networth", {})
        if not isinstance(security, Mapping) or not isinstance(networth, Mapping):
            raise SecurityStoreError("Authentication storage configuration is invalid.")
        # Reading these configuration values does not import or access financial data.
        def setting(name: str, fallback: str, default: str = "") -> str:
            value = security[name] if name in security else networth.get(fallback, default)
            if not isinstance(value, str):
                raise SecurityStoreError("Authentication storage settings must be strings.")
            return value.strip()

        return cls(
            github_token=setting("auth_github_token", "github_token"),
            encryption_key=setting("auth_encryption_key", "encryption_key"),
            repository=setting("auth_repository", "repository", "G-enterpriseGroup/Portfolio"),
            branch=setting("auth_branch", "__no_financial_branch_fallback__", "terminal-security-state"),
        )


class GitHubSecurityStore:
    def __init__(self, config: SecurityStoreConfig):
        self.config = config
        master = base64.urlsafe_b64decode(config.encryption_key.encode("ascii"))
        derived = hmac.new(master, b"raj-terminal:authentication-store:v1", hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))
        context = (config.repository + ":" + config.branch).encode("utf-8")
        self._scope = hmac.new(derived, b"scope:" + context, hashlib.sha256).hexdigest()
        self._base = "https://api.github.com/repos/" + config.repository

    def _request(self, method: str, suffix: str = "", *, payload=None,
                 params=None, missing_ok=False, conflict_ok=False) -> dict | None:
        try:
            response = requests.request(
                method, self._base + suffix, json=payload, params=params,
                headers={"Authorization": "Bearer " + self.config.github_token,
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                timeout=(5, 25), allow_redirects=False, stream=True,
            )
            try:
                if response.status_code == 404 and missing_ok:
                    return None
                if response.status_code in (409, 422) and conflict_ok:
                    raise _Conflict()
                if response.status_code not in (200, 201):
                    raise SecurityStoreError("Authentication storage is unavailable; access remains locked.")
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    raw.extend(chunk)
                    if len(raw) > _MAX_HTTP_BYTES:
                        raise SecurityStoreError("Authentication storage response is invalid; access remains locked.")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise SecurityStoreError("Authentication storage response is invalid; access remains locked.")
                return value
            finally:
                response.close()
        except (requests.RequestException, ValueError, TypeError, UnicodeError):
            raise SecurityStoreError("Authentication storage is unavailable; access remains locked.") from None

    def _prepare(self) -> None:
        metadata = self._request("GET")
        if metadata.get("private") is not True:
            raise SecurityStoreError("Authentication storage requires a verified PRIVATE repository.")
        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise SecurityStoreError("Authentication storage repository metadata is invalid.")
        if self.config.branch == default_branch:
            raise SecurityStoreError("Authentication storage cannot use the repository default branch.")

    def _ref(self) -> str | None:
        result = self._request("GET", "/git/ref/heads/" + quote(self.config.branch, safe=""), missing_ok=True)
        if result is None:
            return None
        obj = result.get("object")
        if (result.get("ref") != "refs/heads/" + self.config.branch
                or not isinstance(obj, dict) or obj.get("type") != "commit"
                or not _SHA.fullmatch(str(obj.get("sha", "")))):
            raise SecurityStoreError("Authentication storage branch is invalid; access remains locked.")
        return obj["sha"]

    def _encode(self, state: dict) -> bytes:
        _validate_state(state)
        payload = json.dumps({"format": "terminal-security-fernet-v1", "scope": self._scope,
                              "state": state}, allow_nan=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_DOCUMENT_BYTES:
            raise SecurityStoreError("Authentication state is too large; access remains locked.")
        return self._fernet.encrypt(payload)

    def _read_at(self, commit_sha: str) -> tuple[dict, str, bytes]:
        value = self._request("GET", "/contents/" + _PATH, params={"ref": commit_sha})
        try:
            if (value.get("path") != _PATH or value.get("type") != "file"
                    or value.get("encoding") != "base64"
                    or not _SHA.fullmatch(str(value.get("sha", "")))):
                raise ValueError
            content = "".join(str(value["content"]).splitlines())
            ciphertext = base64.b64decode(content, validate=True)
            if len(ciphertext) > _MAX_HTTP_BYTES:
                raise ValueError
            envelope = json.loads(self._fernet.decrypt(ciphertext))
            if (not isinstance(envelope, dict) or envelope.get("scope") != self._scope
                    or envelope.get("format") != "terminal-security-fernet-v1"):
                raise ValueError
            state = envelope.get("state")
            _validate_state(state)
        except (KeyError, ValueError, TypeError, UnicodeError, InvalidToken):
            raise SecurityStoreError("Authentication state failed integrity verification; access remains locked.") from None
        return state, value["sha"], ciphertext

    def read(self) -> dict:
        self._prepare()
        ref = self._ref()
        return self._read_at(ref)[0] if ref is not None else initial_state()

    def _new_branch(self, state: dict) -> tuple[str, bytes]:
        ciphertext = self._encode(state)
        tree = self._request("POST", "/git/trees", payload={"tree": [{
            "path": _PATH, "mode": "100644", "type": "blob",
            "content": ciphertext.decode("ascii"),
        }]})
        if not _SHA.fullmatch(str(tree.get("sha", ""))):
            raise SecurityStoreError("Authentication storage initialization failed; access remains locked.")
        commit = self._request("POST", "/git/commits", payload={
            "message": "Initialize encrypted terminal authentication state",
            "tree": tree["sha"], "parents": [],
        })
        if not _SHA.fullmatch(str(commit.get("sha", ""))):
            raise SecurityStoreError("Authentication storage initialization failed; access remains locked.")
        self._request("POST", "/git/refs", payload={
            "ref": "refs/heads/" + self.config.branch, "sha": commit["sha"],
        }, conflict_ok=True)
        return commit["sha"], ciphertext

    def _write(self, state: dict, sha: str | None) -> None:
        if sha is None:
            commit_sha, ciphertext = self._new_branch(state)
        else:
            ciphertext = self._encode(state)
            result = self._request("PUT", "/contents/" + _PATH, payload={
                "message": "Update encrypted terminal authentication state",
                "branch": self.config.branch, "sha": sha,
                "content": base64.b64encode(ciphertext).decode("ascii"),
            }, conflict_ok=True)
            commit = result.get("commit")
            commit_sha = commit.get("sha") if isinstance(commit, dict) else None
            if not _SHA.fullmatch(str(commit_sha or "")):
                raise SecurityStoreError("Authentication state save was not verified; access remains locked.")
        restored, _, actual_ciphertext = self._read_at(commit_sha)
        if restored != state or actual_ciphertext != ciphertext:
            raise SecurityStoreError("Authentication state save was not verified; access remains locked.")

    def update(self, callback: Callable[[dict], object]) -> dict:
        for _ in range(5):
            self._prepare()
            ref = self._ref()
            current, sha = initial_state(), None
            if ref is not None:
                current, sha, _ = self._read_at(ref)
            next_state = deepcopy(current)
            replacement = callback(next_state)
            if replacement is not None:
                next_state = replacement
            _validate_state(next_state)
            if next_state == current:
                return deepcopy(current)
            try:
                self._write(next_state, sha)
                return deepcopy(next_state)
            except _Conflict:
                continue
        raise SecurityStoreError("Authentication state changed concurrently; wait and retry while locked.")
