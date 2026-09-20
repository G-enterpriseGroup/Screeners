"""Publish the latest TradingView MASTER A6 payload to a data-only GitHub branch."""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request


REPO = "G-enterpriseGroup/Screeners"
BRANCH = "gex-bridge-data"
PATH = "bridge/latest_gex.txt"
API_URL = f"https://api.github.com/repos/{REPO}/contents/{PATH}"


def publish_latest_gex(token: str, payload: str) -> bool:
    """Replace the bridge payload on the dedicated data branch."""
    token = str(token or "").strip()
    payload = str(payload or "")
    if not token or not payload:
        return False

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "raj-terminal-gex-bridge",
    }

    metadata_url = API_URL + "?ref=" + urllib.parse.quote(BRANCH, safe="")
    req = urllib.request.Request(metadata_url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15) as response:
        metadata = json.loads(response.read().decode("utf-8"))

    body = {
        "message": "Update TradingView GEX bridge data",
        "content": base64.b64encode(payload.encode("utf-8")).decode("ascii"),
        "sha": metadata["sha"],
        "branch": BRANCH,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        response.read()

    return True
