"""Offline smoke check of every production tab and a second browser session.

Run: python scripts/test_tab_layout.py
Requires the production requirements. No broker credentials or live orders.
AppTest supplies unlocked session state only inside its isolated test session.
Use a browser as well to verify the 48px navigation frame and 4px header gap.
"""
from pathlib import Path
import ast
from copy import deepcopy
import hashlib
import sys
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.security_store import initial_state
from src.terminal_security import AuthService, load_policy, totp_code


class _SyntheticAuthStore:
    def __init__(self):
        self.state = initial_state()

    def read(self):
        return deepcopy(self.state)

    def update(self, callback):
        state = self.read()
        callback(state)
        self.state = state


def _test_authentication():
    # Test-only credentials and CAS state; production never recognizes a flag
    # that bypasses MFA. Exercise the same two factors used by the live gate.
    policy = load_policy({"security": {
        "owner_email": "owner@example.test",
        "trade_access_code_sha256": hashlib.sha256(b"998877665544").hexdigest(),
        "totp_secret": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    }})
    service = AuthService(policy, _SyntheticAuthStore())
    session = {}
    assert service.begin_first_factor(session, "owner", "998877665544")
    assert service.verify_second_factor(session, totp_code(policy.owner.totp_secret))
    return service, session

nav = ast.parse((ROOT / "src/tab_bar_v4.py").read_text())
TABS = next(
    ast.literal_eval(node.value)
    for node in nav.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "DEFAULT_TAB_ORDER" for target in node.targets)
)


def check_session(tabs):
    service, authenticated_state = _test_authentication()
    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60)
    app.session_state["etrade_access_unlocked"] = True
    for key, value in authenticated_state.items():
        app.session_state[key] = value
    for tab in tabs:
        app.session_state["terminal_tab_state"] = {"order": TABS, "active": tab}
        with patch("src.security_runtime.get_auth_service", return_value=service):
            app.run()
        assert not app.exception, [e.message for e in app.exception]
        headers = [m for m in app.get("html") if 'class="terminal-page-header-shell"' in m.value]
        assert len(headers) == 1, (tab, len(headers))
        # Import-cached navigation CSS must still be sent on every rerun/session.
        assert any(".st-key-terminal_navigation" in h.value for h in app.get("html")), tab

        # Shared editable-field styling must be emitted in every tab/session.
        theme_blocks = [
            h.value
            for h in app.get("html")
            if "TERMINAL-WIDE EDITABLE FIELD SURFACES" in h.value
        ]
        assert len(theme_blocks) == 1, (tab, len(theme_blocks))
        editable_css = theme_blocks[0]
        assert "background:#444444 !important;" in editable_css, tab
        for selector in (
            '[data-testid="stTextInput"]',
            '[data-testid="stNumberInput"]',
            '[data-testid="stTextArea"]',
            '[data-testid="stDateInput"]',
            '[data-testid="stTimeInput"]',
            '[data-testid="stSelectbox"]',
            '[data-testid="stMultiSelect"]',
            '[contenteditable="true"]',
        ):
            assert selector in editable_css, (tab, selector)
        print(f"PASS: {tab}")


if __name__ == "__main__":
    check_session(TABS + ["GEX", "RISK SIZING"])
    check_session(["MUNI SCREENERS", "GEX"])
