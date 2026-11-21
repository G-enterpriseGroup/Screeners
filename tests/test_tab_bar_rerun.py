"""A changed top tab must not render content with stale component props."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src import tab_bar_v4 as nav


class RerunRequested(Exception):
    pass


class TabBarRerunTest(TestCase):
    def test_ric_migrates_beside_verticals_and_performance_stays_hidden(self):
        saved = ["GEX", "PERFORMANCE", "VERTICAL OPTIONS", "VERTI PHONE", "HOLDINGS"]
        migrated = nav._clean_order(saved)
        self.assertNotIn("PERFORMANCE", migrated)
        self.assertIn("PERFORMANCE", nav.DISABLED_TAB_ROUTES)
        self.assertEqual(migrated.index("R.I.C."), migrated.index("VERTI PHONE") + 1)
        self.assertEqual(nav._clean_active("PERFORMANCE", migrated), "GEX")
        self.assertEqual(nav._clean_order(migrated), migrated)

    def test_explicit_ric_placement_survives_migration(self):
        saved = ["R.I.C.", "GEX", "HOLDINGS", "VERTICAL OPTIONS", "VERTI PHONE"]
        migrated = nav._clean_order(saved)
        self.assertEqual(migrated[0], "R.I.C.")
        self.assertLess(migrated.index("GEX"), migrated.index("VERTICAL OPTIONS"))

    def test_selection_reruns_before_rendering_new_route(self):
        session = {"terminal_tab_state": {
            "order": list(nav.DEFAULT_TAB_ORDER), "active": "HOLDINGS",
        }}
        rendered = []

        def component(**kwargs):
            rendered.append(kwargs["active"])
            return {"order": kwargs["tabs"], "active": "GEX"}

        def rerun():
            raise RerunRequested

        fake_st = SimpleNamespace(
            session_state=session, html=lambda _: None,
            container=lambda **_: nullcontext(), rerun=rerun,
        )
        with (patch.object(nav, "st", fake_st),
              patch.object(nav, "_terminal_tabs", component),
              patch.object(nav, "_tab_state_vault", lambda: {})):
            with self.assertRaises(RerunRequested):
                nav.render_terminal_tab_bar("synthetic-vault")
            self.assertEqual(rendered, ["HOLDINGS"])
            self.assertEqual(session["terminal_tab_state"]["active"], "GEX")

            self.assertEqual(nav.render_terminal_tab_bar("synthetic-vault")[1], "GEX")
            self.assertEqual(rendered, ["HOLDINGS", "GEX"])
