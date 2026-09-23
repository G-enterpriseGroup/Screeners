"""Offline smoke check of every production tab and a second browser session.

Run: python scripts/test_tab_layout.py
Requires the production requirements. No broker credentials or live orders.
AppTest supplies unlocked session state only inside its isolated test session.
Use a browser as well to verify the 48px navigation frame and 4px header gap.
"""
from pathlib import Path
import ast

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
nav = ast.parse((ROOT / "src/tab_bar_v4.py").read_text())
TABS = next(
    ast.literal_eval(node.value)
    for node in nav.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "DEFAULT_TAB_ORDER" for target in node.targets)
)


def check_session(tabs):
    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60)
    app.session_state["etrade_access_unlocked"] = True
    for tab in tabs:
        app.session_state["terminal_tab_state"] = {"order": TABS, "active": tab}
        app.run()
        assert not app.exception, [e.message for e in app.exception]
        headers = [m for m in app.markdown if 'class="terminal-page-header-shell"' in m.value]
        assert len(headers) == 1, (tab, len(headers))
        # Import-cached navigation CSS must still be sent on every rerun/session.
        assert any(".st-key-terminal_navigation" in h.value for h in app.get("html")), tab
        print(f"PASS: {tab}")


if __name__ == "__main__":
    check_session(TABS + ["GEX", "RISK SIZING"])
    check_session(["MUNI SCREENERS", "GEX"])
