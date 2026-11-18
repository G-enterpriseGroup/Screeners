"""Public policies must stay separate from authentication and account data."""

from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

from src.public_policies import POLICIES, policy_document


ROOT = Path(__file__).resolve().parents[1]


class PublicPolicyTests(unittest.TestCase):
    def test_allowlisted_documents_and_no_path_traversal(self):
        for slug, (title, _) in POLICIES.items():
            self.assertEqual(policy_document(slug)[0], title)
            self.assertIn("enterpriseghotra@gmail.com", policy_document(slug)[1])
        for slug in ("../.streamlit/secrets.toml", "/etc/passwd", "privacy/../../", "", "unknown"):
            self.assertIsNone(policy_document(slug))

    def test_public_routes_render_without_loading_terminal_core(self):
        for slug in (*POLICIES, "../secrets"):
            app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30)
            app.query_params["policy"] = slug
            app.run()
            self.assertFalse(app.exception, [error.message for error in app.exception])
            self.assertTrue(app.title)
            self.assertFalse(app.get("component_instance"))
            self.assertNotIn("etrade_access_token", app.session_state)
            self.assertFalse(any("terminal-page-header-shell" in item.value for item in app.get("html")))

    def test_public_route_precedes_core_and_broker_initialization(self):
        source = (ROOT / "streamlit_app.py").read_text()
        boundary = source.index("if render_public_policy_request():")
        self.assertLess(boundary, source.index("_CORE_PATH ="))
        self.assertLess(boundary, source.index("_restore_active_etrade_session_after_unlock()", boundary))


if __name__ == "__main__":
    unittest.main()
