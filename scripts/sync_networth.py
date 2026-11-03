"""Networth's browser-independent scheduled balance capture (no trading APIs)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.networth import PlaidClient, PlaidConfig, scheduled_slot, sync_networth
from src.networth_store import GitHubNetworthStore, NetworthError, StoreConfig


def main() -> int:
    try:
        # Explicit empty secrets prevents a headless worker importing Streamlit.
        plaid = PlaidConfig.from_runtime({})
        storage = StoreConfig.from_runtime({})
        if plaid is None or storage is None:
            raise NetworthError("Configure Networth Plaid and encrypted-storage Actions secrets first.")
        now = datetime.now(timezone.utc)
        scheduled = os.environ.get("NETWORTH_TRIGGER", "schedule") != "workflow_dispatch"
        slot = scheduled_slot(now) if scheduled else None
        if scheduled and slot is None:
            print("Networth: no scheduled capture is due yet today.")
            return 0
        store = GitHubNetworthStore(storage)
        if not store.read_state().get("items"):
            print("Networth: no linked institutions. Complete Plaid linking in the Networth tab first.")
            return 0
        state = sync_networth(store, PlaidClient(plaid), now=now, scheduled_slot=slot)
        if (state.get("last_errors") or state.get("last_capture_complete") is False
                or (slot is not None and slot not in state.get("completed_slots", {}))):
            print("Networth: capture incomplete. Review connection and balance status in the Networth tab.")
            return 1
        print("Networth: balance capture saved and verified.")
        return 0
    except NetworthError as exc:
        print("Networth: " + str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Financial payloads and access tokens must never reach workflow logs.
        print("Networth: unexpected worker failure. Review configuration and retry.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
