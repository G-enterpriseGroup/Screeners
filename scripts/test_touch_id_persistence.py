"""Regression checks for reboot-safe Touch ID/passkey persistence."""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.passkey_auth as passkey


def main() -> None:
    app_url = "https://terminal8.streamlit.app"
    memory_secret = "stable-server-only-secret"
    record = {
        "credential_version": 2,
        "rp_id": "terminal8.streamlit.app",
        "origin": app_url,
        "credential_id": "credential-id-public",
        "public_key": "credential-public-key",
        "sign_count": 3,
        "device_type": "single_device",
        "backed_up": False,
        "authenticator_attachment": "platform",
        "discoverable": True,
        "transports": ["internal"],
    }

    original_dir = passkey._STORE_DIR
    original_file = passkey._CREDENTIAL_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            passkey._STORE_DIR = root
            passkey._CREDENTIAL_FILE = root / "touch_id_credential.json"

            envelope = passkey.seal_touch_id_record(app_url, record, memory_secret)
            assert envelope["memory_version"] == 1
            assert envelope["record"]["credential_id"] == record["credential_id"]
            assert memory_secret not in str(envelope)

            # Simulate a full Streamlit/container reboot: the server-side home
            # cache is gone, but the browser-memory envelope survives.
            assert not passkey._CREDENTIAL_FILE.exists()
            restored = passkey.restore_touch_id_record(app_url, envelope, memory_secret)
            assert restored is not None
            assert restored["credential_id"] == record["credential_id"]
            assert passkey.load_touch_id_record(app_url)["sign_count"] == 3

            # Browser storage is not a trust anchor by itself. Tampering, a
            # different server-only seal secret, or a different hostname is rejected.
            tampered = copy.deepcopy(envelope)
            tampered["record"]["public_key"] = "attacker-key"
            passkey._CREDENTIAL_FILE.unlink()
            assert passkey.restore_touch_id_record(app_url, tampered, memory_secret) is None
            assert not passkey._CREDENTIAL_FILE.exists()
            assert passkey.restore_touch_id_record(app_url, envelope, "wrong-server-secret") is None
            assert passkey.restore_touch_id_record(
                "https://other.streamlit.app", envelope, memory_secret
            ) is None
    finally:
        passkey._STORE_DIR = original_dir
        passkey._CREDENTIAL_FILE = original_file

    root = Path(__file__).resolve().parents[1]
    lock_source = (root / "src" / "lock_screen_v2.py").read_text(encoding="utf-8")
    component_source = (
        root / "src" / "components" / "lock_keypad_v2" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'action == "restore_touch_id_memory"' in lock_source
    assert 'action == "touch_id_memory_saved"' in lock_source
    assert 'secret_fn("security", "touch_id_memory_secret", "")' in lock_source
    assert 'secret_fn("etrade", "consumer_secret", "")' in lock_source
    assert "_touchid_persist_then_unlock" in lock_source
    assert "localStorage.getItem" in component_source
    assert "localStorage.setItem" in component_source
    assert "restore_touch_id_memory" in component_source
    assert "touch_id_memory_saved" in component_source

    app_source = (root / "streamlit_app.py").read_text(encoding="utf-8")
    assert "from src.lock_screen_v2 import render_seamless_lock_screen" in app_source
    assert "render_seamless_lock_screen(globals())" in app_source

    print("Touch ID reboot-safe sealed browser memory: PASS")


if __name__ == "__main__":
    main()
