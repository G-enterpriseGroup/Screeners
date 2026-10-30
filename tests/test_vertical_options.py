"""Focused regression tests for the Vertical Options payload builder."""

from datetime import date
from pathlib import Path
import unittest

from src.vertical_options import (
    build_place_payload,
    build_vertical_preview_payload,
    scan_vertical_candidates,
    validate_vertical,
)


class VerticalOptionsTests(unittest.TestCase):
    def test_optional_prompt_migration_keeps_inputs_empty(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        state = {"_vo_presentation": {"vo_target_breakeven": vo._NO_BREAKEVEN,
                                       "vo_max_cost": vo._NO_COST_CEILING}}
        with patch.object(vo.st, "session_state", state):
            vo._restore_presentation()
            self.assertEqual(state["vo_target_breakeven"], "")
            self.assertEqual(state["vo_max_cost"], "")
            state["vo_target_breakeven"] = "125.50"
            vo._remember_scan_inputs()
            vo._restore_presentation()
            self.assertEqual(state["vo_target_breakeven"], "125.50")

    def test_scan_memory_restores_only_scan_state_for_same_vault(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        memories = {}
        with patch.object(vo, "_scan_memory", side_effect=lambda key: memories.setdefault(key, {})):
            state = {"vo_scan_result": {"symbol": "SPY", "loaded_at": 123},
                     "_vo_chain_cache": {"ETRADE:SPY": {"scan_id": "snapshot"}},
                     "vo_review_bundle": {"unsafe": True}, "vo_live_confirm": True}
            with patch.object(vo.st, "session_state", state):
                vo._restore_scan_memory("account-a")
                vo._restore_presentation()
                state["vo_symbol_value"] = "SPY"
                vo._remember_scan_inputs()
                vo._remember_scan_snapshot()
            restored = {}
            with patch.object(vo.st, "session_state", restored):
                vo._restore_scan_memory("account-a")
                vo._restore_presentation()
                self.assertEqual(restored["vo_scan_result"], {"symbol": "SPY", "loaded_at": 123})
                self.assertEqual(restored["vo_symbol_value"], "SPY")
                self.assertNotIn("vo_review_bundle", restored)
                self.assertNotIn("vo_live_confirm", restored)
            for account in ("account-b", "default", ""):
                with patch.object(vo.st, "session_state", {}):
                    vo._restore_scan_memory(account)
                    self.assertNotIn("vo_scan_result", vo.st.session_state)

    def test_continuation_saves_snapshot_and_removes_timer_after_completion(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        partial = {"symbol": "SPY", "scan_complete": False}
        state = {"vo_scan_result": partial, "vo_symbol_value": "SPY", "vo_auto_finish": True}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo, "_continue_vertical_scan",
                           return_value={"symbol": "SPY", "scan_complete": True}) as advance,
              patch.object(vo, "_remember_scan_snapshot") as remember,
              patch.object(vo.st, "rerun") as rerun):
            vo._render_vertical_scan_continuation.__wrapped__(None, None)
            advance.assert_called_once()
            remember.assert_called_once()
            rerun.assert_called_once()
            self.assertTrue(state["vo_scan_result"]["scan_complete"])

    def test_presentation_defaults_and_widget_cleanup_preserve_user_choices(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        state = {}
        with patch.object(vo.st, "session_state", state):
            vo._restore_presentation()
            self.assertEqual(state["vo_results_view"], "BEST PER YEAR")
            self.assertFalse(state["vo_be_filter_enabled"])
            self.assertEqual(state["vo_be_range_percent"], 5)
            self.assertEqual(state["vo_target_breakeven"], "")
            self.assertEqual(state["vo_max_cost"], "")
            self.assertEqual(state["vo_min_reward_risk"], "2")
            chosen = {"vo_results_view": "ALL QUALIFIED", "vo_be_filter_enabled": True,
                      "vo_be_range_percent": 0.0, "vo_all_pair_side": "PUT",
                      "vo_all_pair_direction": "DESC", "vo_auto_finish": False,
                      "vo_min_reward_risk": "1.5"}
            for key, value in chosen.items():
                state[key] = value
                vo._remember_presentation(key)
            # Simulate Streamlit deleting controls while another tab is active.
            for key in vo._PRESENTATION_DEFAULTS:
                state.pop(key, None)
            vo._restore_presentation()
            for key, value in chosen.items():
                self.assertEqual(state[key], value)
            # Reproduce defaults arriving from a newly mounted frontend.
            state.update({key: vo._PRESENTATION_DEFAULTS[key] for key in chosen})
            vo._restore_presentation()
            for key, value in chosen.items():
                self.assertEqual(state[key], value)
            vo._restore_presentation()  # Ordinary rerun cannot override choices.
            self.assertEqual(state["vo_results_view"], "ALL QUALIFIED")
        with patch.object(vo.st, "session_state", {}):
            vo._restore_presentation()
            self.assertEqual(vo.st.session_state["vo_results_view"], "BEST PER YEAR")
            self.assertEqual(vo.st.session_state["vo_min_reward_risk"], "2")

    def test_real_streamlit_widget_cleanup_restores_manual_presentation(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string("""
import streamlit as st
from src.vertical_options_ui import _restore_presentation, _remember_presentation
if st.session_state.get('away'):
    if st.button('RETURN'):
        st.session_state['away'] = False
        st.rerun()
else:
    if st.button('LEAVE'):
        st.session_state['away'] = True
        st.rerun()
    _restore_presentation()
    st.toggle('BE', key='vo_be_filter_enabled', on_change=_remember_presentation,
              args=('vo_be_filter_enabled',))
    st.radio('VIEW', ('BEST PER YEAR', 'ALL QUALIFIED'), key='vo_results_view',
             on_change=_remember_presentation, args=('vo_results_view',))
    st.text_input('MIN R:R', key='vo_min_reward_risk',
                  on_change=_remember_presentation, args=('vo_min_reward_risk',))
""").run()
        self.assertFalse(app.toggle[0].value)
        self.assertEqual(app.radio[0].value, 'BEST PER YEAR')
        self.assertEqual(app.text_input[0].value, '2')
        app.toggle[0].set_value(True).run()
        app.radio[0].set_value('ALL QUALIFIED').run()
        app.text_input[0].set_value('1.5').run()
        app.button[0].click().run()
        app.button[0].click().run()
        self.assertTrue(app.toggle[0].value)
        self.assertEqual(app.radio[0].value, 'ALL QUALIFIED')
        self.assertEqual(app.text_input[0].value, '1.5')
        self.assertFalse(app.exception)

    def test_bootstrap_timeout_is_actionable_without_overwriting_preferences(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        state = {"_vo_bootstrap_started": 1}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo, "_hydrate_vertical_options_pricing", return_value=False),
              patch.object(vo.time, "monotonic", return_value=14),
              patch.object(vo.st, "error") as error,
              patch.object(vo.st, "button", return_value=False),
              patch.object(vo.st, "rerun") as rerun):
            vo._render_vertical_options_bootstrap.__wrapped__("test")
            error.assert_not_called()
            rerun.assert_called_once()
            self.assertTrue(state["_vo_bootstrap_failed"])
            self.assertNotIn("_vo_workspace_ready", state)
            self.assertNotIn("_vo_pricing_mode_preference", state)

    def test_calculation_budget_resumes_all_pairs_without_repricing_old_expirations(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        rows = [self._row(expiry, side, 100 + k, 15 - .2 * k if side == "CALL" else 2 + .2 * k)
                for expiry in (date(2027, 1, 15), date(2028, 1, 21), date(2029, 1, 19))
                for side in ("CALL", "PUT") for k in range(20)]
        raw = {"symbol": "QQQ", "spot": 110, "rows": rows}
        with (patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "_SCAN_RANK_SECONDS", -1),
              patch.object(vo, "scan_vertical_candidates", wraps=scan_vertical_candidates) as calculate):
            for count in (1, 2, 3):
                store, winners, _, _ = vo._rank_cached_vertical_chains(raw, 110, None, "NORMAL")
                self.assertEqual(store.ranked_expirations, count)
                self.assertEqual(calculate.call_count, count)
            vo._rank_cached_vertical_chains(raw, 110, None, "NORMAL")
            self.assertEqual(calculate.call_count, 3)
            expected = scan_vertical_candidates(symbol="QQQ", spot_last=110, option_rows=rows,
                target_breakeven=110, max_cost=None, max_per_expiration=None)
            self.assertEqual((list(store.iter_ranked()), winners), expected)

    def test_streaming_preserves_every_price_and_annual_winner(self):
        from src.vertical_options_store import _rank
        rows = [self._row(date(2027, 1, 15), side, 100 + k,
                         15 - .2 * k if side == "CALL" else 2 + .2 * k,
                         bid=None if k % 3 else 3, ask=None if k % 3 else 3.02)
                for side in ("CALL", "PUT") for k in range(25)]
        rows += [dict(rows[0])]  # Duplicate transport contract.
        for mode in ("NORMAL", "SMART FILL"):
            args = dict(symbol="QQQ", spot_last=110, option_rows=rows,
                        target_breakeven=110, max_cost=None, max_per_expiration=None,
                        pricing_mode=mode)
            expected, winners = scan_vertical_candidates(**args)
            streamed = []
            retained, actual_winners = scan_vertical_candidates(**args, candidate_sink=streamed.append)
            self.assertEqual(retained, [])
            self.assertEqual(sorted(streamed, key=lambda row: _rank(row, 110)), expected)
            self.assertEqual(actual_winners, winners)

    def test_disk_store_all_sorts_filters_pages_selection_and_full_csv(self):
        import csv
        import io
        from src.vertical_options_store import VerticalResultStore
        from src.vertical_options_ui import _sort_qualified_spreads, _QUALIFIED_SORT_FIELDS, _qualified_store_csv
        rows = [self._row(expiry, side, 100 + k,
                         15 - .2 * k if side == "CALL" else 2 + .2 * k)
                for expiry in (date(2027, 1, 15), date(2028, 1, 21))
                for side in ("CALL", "PUT") for k in range(22)]
        # Exercise missing Last values and quoted-only candidates as well.
        rows.extend([self._row(date(2028, 1, 21), "CALL", 130, None, bid=.5, ask=.52),
                     self._row(date(2028, 1, 21), "CALL", 140, None, bid=.1, ask=.12)])
        store = VerticalResultStore("QQQ", 110, 110, None, "NORMAL")
        for expiry in sorted({r["expiry"] for r in rows}):
            store.add_expiration(expiry, [r for r in rows if r["expiry"] == expiry], scan_vertical_candidates)
        expected, annual = scan_vertical_candidates(symbol="QQQ", spot_last=110, option_rows=rows,
            target_breakeven=110, max_cost=None, max_per_expiration=None)
        self.assertGreater(len(store), 900)
        self.assertEqual(len(store), len(expected))
        self.assertEqual(list(store.iter_ranked()), expected)
        self.assertEqual(store.annual_ids, annual)
        self.assertEqual({r["id"] for r in store.annual_rows}, annual)
        for sort_by in _QUALIFIED_SORT_FIELDS:
            for direction in ("ASC", "DESC"):
                ordered = _sort_qualified_spreads(expected, sort_by, direction, 110)
                for offset in (0, 200, len(ordered) - 13):
                    self.assertEqual(store.page(sort_by=sort_by, direction=direction, offset=offset),
                                     ordered[offset:offset + 200], (sort_by, direction, offset))
        for expiry in ("ALL", date(2027, 1, 15), date(2028, 1, 21)):
            for side in ("CALL + PUT", "CALL", "PUT"):
                ordered = _sort_qualified_spreads(expected, "COST", "DESC", 110)
                filtered = [r for r in ordered if (expiry == "ALL" or r["expiry"] == expiry)
                            and (side == "CALL + PUT" or r["call_put"] == side)]
                self.assertEqual(store.count(expiry=expiry, side=side), len(filtered))
                self.assertEqual(store.page(expiry=expiry, side=side, sort_by="COST", direction="DESC"),
                                 filtered[:200])
                data = _qualified_store_csv(store, source="E*TRADE", loaded_at=1000, target=110,
                    max_cost=None, expiry=expiry, side=side, sort_by="COST", direction="DESC")
                exported = list(csv.DictReader(io.StringIO(data)))
                self.assertEqual(len(exported), len(filtered))
                self.assertEqual([float(r["Cost Per Contract ($)"]) for r in exported],
                                 [r["cost"] for r in filtered])
                self.assertEqual(sum(r["Annual Best"] == "YES" for r in exported),
                                 sum(r["id"] in annual for r in filtered))
        late = store.page(offset=800, limit=1)[0]
        self.assertEqual(store.get_many([late["id"], "unknown"]), {late["id"]: late})
        self.assertLessEqual(len(store._indexes), 3)
        from src.vertical_options_store import _rank
        # A display-only range must agree across count, all pages and full CSV.
        edge = abs(expected[0]["breakeven"] - 110)
        for distance in (0, 2.5, 5, edge, 999):
            filtered = _sort_qualified_spreads([r for r in expected
                if abs(r["breakeven"] - 110) <= distance + 1e-8], "TARGET DISTANCE", "ASC", 110)
            self.assertEqual(store.count(max_distance=distance), len(filtered))
            self.assertEqual(list(store.iter_rows(max_distance=distance, sort_by="TARGET DISTANCE")), filtered)
            for offset in range(0, len(filtered), 200):
                self.assertEqual(store.page(max_distance=distance, sort_by="TARGET DISTANCE", offset=offset),
                                 filtered[offset:offset + 200])
            exported = list(csv.DictReader(io.StringIO(_qualified_store_csv(store,
                source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
                max_distance=distance, sort_by="TARGET DISTANCE"))))
            self.assertEqual([float(r["Breakeven ($)"]) for r in exported], [r["breakeven"] for r in filtered])
            winners = {year: min((r for r in filtered if r["year"] == year), key=lambda r: _rank(r, 110))
                       for year in {r["year"] for r in filtered}}
            self.assertEqual({r["year"]: r for r in store.best_by_year(max_distance=distance)}, winners)
            self.assertTrue(all(r["reward_risk"] >= 2 - 1e-8 for r in filtered))
        self.assertEqual(store.count(), len(expected))  # Turning OFF restores every retained pair.
        for invalid in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                store.count(max_distance=invalid)
        with store._connect() as db:
            plan = str(db.execute("EXPLAIN QUERY PLAN SELECT count(*) FROM spreads WHERE distance<=?", [5]).fetchall())
            self.assertIn("target_distance", plan)

    def test_disk_store_keeps_249500_pairs_and_last_page_without_a_cutoff(self):
        from src.vertical_options_store import VerticalResultStore
        expiry = date(2027, 1, 15)
        rows = [self._row(expiry, side, 100 + k,
                         110 - .2 * k if side == "CALL" else 2 + .2 * k,
                         bid=109.99 - .2 * k if side == "CALL" else 1.99 + .2 * k,
                         ask=110.01 - .2 * k if side == "CALL" else 2.01 + .2 * k)
                for side in ("CALL", "PUT") for k in range(500)]
        store = VerticalResultStore("QQQ", 350, 350, None, "NORMAL")
        store.add_expiration(expiry, rows, scan_vertical_candidates)
        self.assertEqual(len(store), 249500)
        self.assertEqual(store.count(side="CALL"), 124750)
        self.assertEqual(store.count(side="PUT"), 124750)
        self.assertEqual(len(store.page(offset=249400)), 100)
        final = store.page(offset=249499, limit=1)[0]
        self.assertEqual(store.get_many([final["id"]]), {final["id"]: final})
        self.assertFalse(store.add_expiration(expiry, rows, scan_vertical_candidates))
        self.assertEqual(len(store), 249500)
        for key in ("CALL", "PUT"):
            self.assertEqual(sum(1 for _ in store.iter_rows(side=key)), 124750)

    def test_year_grouped_pages_keep_every_pair_and_one_annual_marker_per_year(self):
        import csv
        import io
        from src.vertical_options_store import VerticalResultStore, _rank
        from src.vertical_options_ui import _qualified_store_csv, _qualified_store_xlsx

        expiries = (date(2027, 1, 15), date(2027, 6, 18), date(2028, 1, 21))
        rows = [self._row(expiry, side, 100 + strike,
                          15 - .2 * strike if side == "CALL" else 2 + .2 * strike)
                for expiry in expiries for side in ("CALL", "PUT")
                for strike in range(20)]
        expected, annual = scan_vertical_candidates(
            symbol="QQQ", spot_last=110, option_rows=rows,
            target_breakeven=110, max_cost=None, max_per_expiration=None,
        )
        expected = sorted(expected, key=lambda row: (row["year"], _rank(row, 110)))
        store = VerticalResultStore("QQQ", 110, 110, None, "NORMAL")
        for expiry in expiries:
            self.assertTrue(store.add_expiration(
                expiry, [row for row in rows if row["expiry"] == expiry],
                scan_vertical_candidates,
            ))

        self.assertEqual(len(expected), 1140)
        self.assertEqual(store.count(), len(expected))
        self.assertEqual([row["id"] for row in store.iter_rows(sort_by="YEAR + FILL")],
                         [row["id"] for row in expected])
        pages = [store.page(sort_by="YEAR + FILL", direction="ASC", offset=offset, limit=200)
                 for offset in range(0, len(expected), 200)]
        self.assertEqual([len(page) for page in pages], [200, 200, 200, 200, 200, 140])
        self.assertEqual([row["id"] for page in pages for row in page],
                         [row["id"] for row in expected])
        self.assertEqual([row["year"] for row in expected].count(2027), 760)
        self.assertEqual([row["year"] for row in expected].count(2028), 380)
        self.assertEqual(store.counts_by_year(), {2027: 760, 2028: 380})
        self.assertEqual(store.annual_ids, annual)
        self.assertEqual(len(annual), 2)
        self.assertEqual({row["year"] for row in store.annual_rows}, {2027, 2028})

        year_rows = [row for row in expected if row["year"] == 2028]
        self.assertEqual(store.count(year=2028), 380)
        self.assertEqual([row["id"] for row in store.iter_rows(
            year=2028, sort_by="YEAR + FILL")], [row["id"] for row in year_rows])
        self.assertEqual([row["id"] for offset in (0, 200)
                          for row in store.page(year=2028, sort_by="YEAR + FILL",
                                                direction="ASC", offset=offset, limit=200)],
                         [row["id"] for row in year_rows])
        self.assertEqual([row["id"] for row in store.best_by_year(year=2028)],
                         [row["id"] for row in store.annual_rows if row["year"] == 2028])
        year_csv = list(csv.DictReader(io.StringIO(_qualified_store_csv(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL", year=2028,
        ))))
        self.assertEqual(len(year_csv), 380)
        self.assertEqual([row["Expiration"] for row in year_csv],
                         [row["expiry"].isoformat() for row in year_rows])
        self.assertEqual(sum(row["Annual Best"] == "YES" for row in year_csv), 1)

        xlsx_headers, xlsx_rows = self._xlsx_export_rows(_qualified_store_xlsx(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL",
        ))
        full_csv = list(csv.DictReader(io.StringIO(_qualified_store_csv(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL",
        ))))
        self.assertEqual(xlsx_headers, list(full_csv[0]))
        self.assertEqual(len(xlsx_rows), 1140)  # More than one displayed page.
        for xlsx_row, csv_row in zip(xlsx_rows, full_csv):
            for header in xlsx_headers:
                workbook_value, csv_value = xlsx_row["values"][header], csv_row[header]
                if workbook_value != csv_value:
                    try:
                        self.assertAlmostEqual(float(workbook_value), float(csv_value), places=8,
                                               msg=header)
                    except (TypeError, ValueError):
                        self.assertEqual(workbook_value, csv_value, header)
        self.assertEqual([(row["values"]["Type"], row["values"]["Expiration"])
                          for row in xlsx_rows],
                         [(row["Type"], row["Expiration"]) for row in full_csv])
        self.assertEqual([row["values"]["Annual Best"] for row in xlsx_rows],
                         [row["Annual Best"] for row in full_csv])
        self.assertEqual({row["values"]["Type"] for row in xlsx_rows}, {"CALL", "PUT"})
        for row in xlsx_rows:
            expected_fill = "A3E635" if row["values"]["Type"] == "CALL" else "EA0101"
            self.assertTrue(row["styles"], row)
            self.assertTrue(all(fill == expected_fill and font == "000000"
                                for fill, font in row["styles"]), row)

        year_xlsx_headers, year_xlsx_rows = self._xlsx_export_rows(_qualified_store_xlsx(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL", year=2028,
        ))
        self.assertEqual(year_xlsx_headers, xlsx_headers)
        self.assertEqual(len(year_xlsx_rows), len(year_csv))
        self.assertEqual([row["values"]["Annual Best"] for row in year_xlsx_rows],
                         [row["Annual Best"] for row in year_csv])
        self.assertEqual({row["values"]["Expiration"][:4] for row in year_xlsx_rows},
                         {"2028"})

        filters = {"side": ["PUT"], "cost": {"max": 100}}
        filtered = [row for row in expected
                    if row["call_put"] == "PUT" and row["cost"] <= 100 + 1e-8]
        self.assertEqual(len(filtered), 255)
        self.assertEqual(store.count(column_filters=filters), len(filtered))
        self.assertEqual(store.counts_by_year(column_filters=filters),
                         {2027: 170, 2028: 85})
        self.assertEqual([row["id"] for row in store.iter_rows(
            sort_by="YEAR + FILL", column_filters=filters)],
            [row["id"] for row in filtered])
        self.assertEqual([row["id"] for offset in (0, 200)
                          for row in store.page(sort_by="YEAR + FILL", direction="ASC",
                                                offset=offset, limit=200, column_filters=filters)],
                         [row["id"] for row in filtered])
        filtered_year = [row for row in filtered if row["year"] == 2028]
        self.assertEqual(store.count(year=2028, column_filters=filters), len(filtered_year))
        self.assertEqual([row["id"] for row in store.page(
            year=2028, sort_by="YEAR + FILL", column_filters=filters)],
            [row["id"] for row in filtered_year])
        filtered_year_csv = list(csv.DictReader(io.StringIO(_qualified_store_csv(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL", year=2028, column_filters=filters,
        ))))
        self.assertEqual(len(filtered_year_csv), len(filtered_year))
        self.assertEqual([(row["Type"], row["Expiration"], float(row["Buy Strike"]),
                           float(row["Sell Strike"])) for row in filtered_year_csv],
                         [(row["call_put"], row["expiry"].isoformat(), row["buy_strike"],
                           row["sell_strike"]) for row in filtered_year])
        _, filtered_year_xlsx = self._xlsx_export_rows(_qualified_store_xlsx(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL", year=2028, column_filters=filters,
        ))
        self.assertEqual(len(filtered_year_xlsx), len(filtered_year_csv))
        self.assertEqual([(row["values"]["Type"], row["values"]["Expiration"],
                           float(row["values"]["Buy Strike"]),
                           float(row["values"]["Sell Strike"]))
                          for row in filtered_year_xlsx],
                         [(row["Type"], row["Expiration"], float(row["Buy Strike"]),
                           float(row["Sell Strike"])) for row in filtered_year_csv])
        within_range = [row for row in filtered
                        if abs(row["breakeven"] - 110) <= 5 + 1e-8]
        self.assertTrue(0 < len(within_range) < len(filtered))
        self.assertEqual(store.count(max_distance=5, column_filters=filters), len(within_range))
        self.assertEqual([row["id"] for row in store.page(
            sort_by="YEAR + FILL", direction="ASC", offset=0, limit=200,
            max_distance=5, column_filters=filters)],
            [row["id"] for row in within_range[:200]])
        filtered_winners = store.best_by_year(max_distance=5, column_filters=filters)
        self.assertEqual(len(filtered_winners), len({row["year"] for row in within_range}))
        self.assertTrue({row["id"] for row in filtered_winners}.issubset(
            {row["id"] for row in within_range}))
        year_winner = store.best_by_year(max_distance=5, column_filters=filters, year=2028)
        self.assertEqual(len(year_winner), 1)
        self.assertEqual(year_winner[0]["year"], 2028)
        self.assertIn(year_winner[0]["id"], {row["id"] for row in within_range})

        # Excluding the unfiltered winner must move the CSV's annual marker to
        # the best remaining spread in this year and active breakeven range.
        original_winner = store.best_by_year(year=2028)[0]
        without_original = {"cost": {"min": original_winner["cost"] + 1}}
        scoped_rows = list(store.iter_rows(
            year=2028, sort_by="YEAR + FILL", max_distance=5,
            column_filters=without_original,
        ))
        self.assertTrue(scoped_rows)
        self.assertNotIn(original_winner["id"], {row["id"] for row in scoped_rows})
        scoped_winner = store.best_by_year(
            year=2028, max_distance=5, column_filters=without_original,
        )
        self.assertEqual(len(scoped_winner), 1)
        scoped_csv = list(csv.DictReader(io.StringIO(_qualified_store_csv(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL", year=2028, max_distance=5,
            column_filters=without_original,
        ))))
        self.assertEqual(len(scoped_csv), len(scoped_rows))
        self.assertEqual([
            (row["Type"], row["Expiration"], float(row["Buy Strike"]),
             float(row["Sell Strike"]))
            for row in scoped_csv if row["Annual Best"] == "YES"
        ], [
            (scoped_winner[0]["call_put"], scoped_winner[0]["expiry"].isoformat(),
             scoped_winner[0]["buy_strike"], scoped_winner[0]["sell_strike"])
        ])
        _, scoped_xlsx = self._xlsx_export_rows(_qualified_store_xlsx(
            store, source="E*TRADE", loaded_at=1000, target=110, max_cost=None,
            sort_by="YEAR + FILL", year=2028, max_distance=5,
            column_filters=without_original,
        ))
        self.assertEqual([row["values"]["Annual Best"] for row in scoped_xlsx],
                         [row["Annual Best"] for row in scoped_csv])

        for invalid_year in (0, 9999, "2028", True, 2028.0):
            with self.subTest(invalid_year=invalid_year):
                with self.assertRaises(ValueError):
                    store.count(year=invalid_year)
                with self.assertRaises(ValueError):
                    store.page(year=invalid_year)
                with self.assertRaises(ValueError):
                    list(store.iter_rows(year=invalid_year))
                with self.assertRaises(ValueError):
                    store.best_by_year(year=invalid_year)

    def test_xlsx_export_keeps_formula_like_ticker_as_text(self):
        from src.vertical_options_store import VerticalResultStore
        from src.vertical_options_ui import _qualified_store_xlsx

        expiry = date(2028, 1, 21)
        rows = [self._row(expiry, "CALL", 100, 2),
                self._row(expiry, "CALL", 103, 1)]
        store = VerticalResultStore("=1+1", 100, None, None, "NORMAL")
        store.add_expiration(expiry, rows, scan_vertical_candidates)
        _, exported = self._xlsx_export_rows(_qualified_store_xlsx(
            store, source="E*TRADE", loaded_at=1000, target=None, max_cost=None,
            sort_by="YEAR + FILL",
        ))
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["values"]["Ticker"], "=1+1")
        self.assertNotIn(0, exported[0]["formula_columns"])

    def test_minimum_expiry_prunes_cached_results_and_recomputes_annual_winners(self):
        from datetime import timedelta
        from unittest.mock import MagicMock
        from src.vertical_options_store import VerticalResultStore, _rank

        today = date(2027, 1, 1)
        expiries = [today + timedelta(days=days) for days in (0, 1, 6, 7, 8)]
        expiries.append(date(2028, 1, 21))
        rows = [self._row(expiry, side, 100 + k,
                         15 - .2 * k if side == "CALL" else 2 + .2 * k)
                for expiry in expiries for side in ("CALL", "PUT") for k in range(4)]
        store = VerticalResultStore("QQQ", 110, None, None, "NORMAL")
        for expiry in expiries:
            store.add_expiration(expiry, [row for row in rows if row["expiry"] == expiry],
                                 scan_vertical_candidates)
        original = list(store.iter_ranked())
        original_ids = {row["id"] for row in original}
        self.assertEqual(store.column_filter_choices()["expiry"],
                         [expiry.isoformat() for expiry in expiries])
        self.assertEqual(min(row["expiry"] for row in store.annual_rows), today)

        cutoff = today + timedelta(days=7)
        expected = [row for row in original if row["expiry"] >= cutoff]
        expected_ids = {row["id"] for row in expected}
        self.assertEqual(store.prune_before(cutoff), len(original) - len(expected))
        self.assertEqual(len(store), len(expected))
        self.assertEqual(store.count(), len(expected))
        self.assertEqual(store.ranked_expirations, 3)
        self.assertEqual(store.expirations, expiries[3:])
        self.assertEqual(store.column_filter_choices()["expiry"],
                         [expiry.isoformat() for expiry in expiries[3:]])
        self.assertEqual(list(store.iter_ranked()), expected)
        self.assertEqual({row["id"] for row in store.iter_rows()}, expected_ids)
        self.assertEqual({row["id"] for row in store.page(limit=1000)}, expected_ids)
        self.assertEqual(set(store.get_many(original_ids)), expected_ids)
        self.assertEqual(store.count(expiry=expiries[0]), 0)
        self.assertEqual(store.count(column_filters={"expiry": [expiries[0].isoformat()]}), 0)
        for side in ("CALL", "PUT"):
            self.assertEqual(store.count(side=side),
                             sum(row["call_put"] == side for row in expected))
        self.assertEqual(store.counts_by_year(),
                         {year: sum(row["year"] == year for row in expected)
                          for year in (2027, 2028)})
        self.assertEqual(store.count(year=2027),
                         sum(row["year"] == 2027 for row in expected))
        self.assertEqual({row["id"] for row in store.iter_rows(sort_by="YEAR + FILL")}, expected_ids)
        winners = {year: min((row for row in expected if row["year"] == year),
                             key=lambda row: _rank(row, None))
                   for year in {row["year"] for row in expected}}
        self.assertEqual({row["year"]: row for row in store.annual_rows}, winners)
        self.assertEqual({row["year"]: row for row in store.best_by_year()}, winners)
        self.assertEqual(store.annual_ids, {row["id"] for row in winners.values()})
        self.assertEqual(winners[2027]["expiry"], cutoff)

        calculator = MagicMock()
        self.assertFalse(store.add_expiration(expiries[0], rows[:8], calculator))
        calculator.assert_not_called()
        self.assertEqual(store.prune_before(cutoff), 0)
        self.assertEqual(store.prune_before(today), 0)
        self.assertEqual(len(store), len(expected))
        next_cutoff = cutoff + timedelta(days=1)
        self.assertEqual(store.prune_before(next_cutoff),
                         sum(row["expiry"] == cutoff for row in expected))
        self.assertEqual(min(row["expiry"] for row in store.annual_rows), next_cutoff)
        self.assertNotIn(cutoff.isoformat(), store.column_filter_choices()["expiry"])

        store.prune_before(date(2029, 1, 1))
        self.assertEqual(len(store), 0)
        self.assertEqual(store.count(), 0)
        self.assertEqual(store.ranked_expirations, 0)
        self.assertEqual(store.annual_ids, set())
        self.assertEqual(store.annual_rows, [])
        self.assertEqual(store.best_by_year(), [])
        self.assertEqual(store.expirations, [])
        self.assertEqual(store.column_filter_choices()["expiry"], [])
        self.assertEqual(store.get_many(original_ids), {})
        self.assertEqual(list(store.iter_rows()), [])
        self.assertEqual(store.page(), [])
        for invalid in (None, "2027-01-08"):
            with self.assertRaisesRegex(ValueError, "minimum expiration"):
                store.prune_before(invalid)

    def test_minimum_expiry_exports_preserve_call_put_highlights_and_year_scope(self):
        import csv
        import io
        from datetime import timedelta
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        from src.vertical_options_store import VerticalResultStore

        today = date(2026, 12, 24)
        cutoff = today + timedelta(days=7)
        expiries = [today + timedelta(days=days) for days in (0, 1, 6, 7, 8)]
        rows = [self._row(expiry, side, 100 + k,
                         15 - .2 * k if side == "CALL" else 2 + .2 * k)
                for expiry in expiries for side in ("CALL", "PUT") for k in range(4)]
        all_rows, all_annual = scan_vertical_candidates(
            symbol="QQQ", spot_last=110, option_rows=rows, target_breakeven=None,
            max_cost=None, max_per_expiration=None,
        )
        expected = [row for row in all_rows if row["expiry"] >= cutoff]
        metadata = dict(source="E*TRADE", loaded_at=1000, target=None, max_cost=None)
        with patch.object(vo, "_minimum_expiry_date", return_value=cutoff):
            direct_csv = list(csv.DictReader(io.StringIO(vo._qualified_spreads_csv(
                all_rows, all_annual, **metadata))))
            _, direct_xlsx = self._xlsx_export_rows(vo._qualified_spreads_xlsx(
                all_rows, all_annual, **metadata))
            self.assertEqual(len(direct_csv), len(expected))
            self.assertEqual(len(direct_xlsx), len(expected))
            for export in (vo._qualified_store_csv, vo._qualified_store_xlsx):
                store = VerticalResultStore("QQQ", 110, None, None, "NORMAL")
                for expiry in expiries:
                    store.add_expiration(expiry, [row for row in rows if row["expiry"] == expiry],
                                         scan_vertical_candidates)
                payload = export(store, sort_by="YEAR + FILL", **metadata)
                if isinstance(payload, str):
                    exported = [{"values": row} for row in csv.DictReader(io.StringIO(payload))]
                else:
                    _, exported = self._xlsx_export_rows(payload)
                self.assertEqual(len(exported), len(expected))
                self.assertEqual(len(store), len(expected))
                self.assertEqual(store.expirations, expiries[3:])
                self.assertEqual(sum(row["values"]["Annual Best"] == "YES" for row in exported), 2)
                self.assertEqual({row["values"]["Highlight"] for row in exported}, {"GREEN", "RED"})
                self.assertTrue(any(row["values"]["Annual Best"] == "NO" for row in exported))
                for row in exported:
                    values = row["values"]
                    self.assertGreaterEqual(date.fromisoformat(values["Expiration"]), cutoff)
                    self.assertEqual(values["Highlight"], "GREEN" if values["Type"] == "CALL" else "RED")
                    if "styles" in row:
                        color = "A3E635" if values["Type"] == "CALL" else "EA0101"
                        self.assertTrue(row["styles"])
                        self.assertEqual(set(row["styles"]), {(color, "000000")})
                filtered = export(store, sort_by="YEAR + FILL", year=2027,
                                  column_filters={"side": ["PUT"]}, **metadata)
                if isinstance(filtered, str):
                    filtered_rows = list(csv.DictReader(io.StringIO(filtered)))
                else:
                    _, filtered_workbook = self._xlsx_export_rows(filtered)
                    filtered_rows = [row["values"] for row in filtered_workbook]
                self.assertEqual(len(filtered_rows), 6)
                self.assertEqual({row["Highlight"] for row in filtered_rows}, {"RED"})
                self.assertEqual({row["Expiration"][:4] for row in filtered_rows}, {"2027"})
                self.assertEqual(sum(row["Annual Best"] == "YES" for row in filtered_rows), 1)

    def test_scan_skips_short_expiries_and_prunes_restored_cache_without_refetch(self):
        from datetime import timedelta
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        from src.vertical_options_store import VerticalResultStore

        today = date(2027, 1, 1)
        cutoff = today + timedelta(days=7)
        expiries = [today + timedelta(days=days) for days in (0, 1, 6, 7, 8)]
        rows = [self._row(expiry, side, 100 + k,
                         15 - .2 * k if side == "CALL" else 2 + .2 * k)
                for expiry in expiries for side in ("CALL", "PUT") for k in range(4)]
        client = MagicMock(is_offline=False)
        client.get_quote.return_value = {"lastTrade": 110}
        state = {}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "_minimum_expiry_date", return_value=cutoff),
              patch.object(vo, "options_market_client", return_value=client),
              patch.object(vo, "_expirations", return_value=expiries),
              patch.object(vo, "_chain", side_effect=lambda _, symbol, expiry, touch:
                           [row for row in rows if row["expiry"] == expiry]) as chain,
              patch.object(vo, "record_lookup"),
              patch.object(vo, "_SCAN_BATCH_EXPIRATIONS", 99),
              patch.object(vo, "_SCAN_BATCH_SECONDS", 999)):
            fresh = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertTrue(fresh["scan_complete"])
            self.assertEqual(fresh["expirations"], expiries[3:])
            self.assertEqual([call.args[2] for call in chain.call_args_list], expiries[3:])
            self.assertEqual(fresh["loaded_expirations"], 2)

            old_store = VerticalResultStore("QQQ", 110, None, None, "NORMAL")
            for expiry in expiries:
                old_store.add_expiration(expiry, [row for row in rows if row["expiry"] == expiry],
                                         scan_vertical_candidates)
            restored = {**fresh, "expirations": expiries, "rows": rows,
                        "loaded_expirations": len(expiries), "next_expiry_index": len(expiries),
                        "scan_complete": False, "failed_expirations": [expiries[0]],
                        "result_stores": {(None, None, "NORMAL", 2.0): old_store}}
            state["_vo_chain_cache"]["E*TRADE:QQQ"] = restored
            cached = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertIs(cached, restored)
            self.assertEqual(cached["expirations"], expiries[3:])
            self.assertEqual({row["expiry"] for row in cached["rows"]}, set(expiries[3:]))
            self.assertEqual(cached["loaded_expirations"], 2)
            self.assertEqual(cached["next_expiry_index"], 2)
            self.assertTrue(cached["scan_complete"])
            self.assertEqual(cached["failed_expirations"], [])
            store, annual, _, _ = vo._rank_cached_vertical_chains(cached, None, None, "NORMAL")
            self.assertIs(store, old_store)
            self.assertEqual(store.expirations, expiries[3:])
            self.assertEqual(store.ranked_expirations, 2)
            self.assertEqual(len(annual), 1)
            self.assertEqual(chain.call_count, 2)
            self.assertEqual(client.get_quote.call_count, 1)
            with patch.object(vo, "_minimum_expiry_date", return_value=cutoff + timedelta(days=1)):
                tomorrow = vo._scan_all_years(client, "QQQ", lambda: None)
                store, _, _, _ = vo._rank_cached_vertical_chains(tomorrow, None, None, "NORMAL")
            self.assertEqual(store.expirations, [expiries[-1]])
            self.assertEqual(store.ranked_expirations, 1)
            self.assertEqual(chain.call_count, 2)
            self.assertEqual(client.get_quote.call_count, 1)
            with patch.object(vo, "_expirations", return_value=expiries[:3]):
                with self.assertRaisesRegex(ValueError, "at least 7 days"):
                    vo._scan_all_years(client, "QQQ", lambda: None, refresh=True)
            self.assertEqual(chain.call_count, 2)
            client.preview_order.assert_not_called()
            client.place_order.assert_not_called()

    def test_column_filters_cover_full_store_pages_and_csv_without_target(self):
        import csv
        import io
        from src.vertical_options_store import VerticalResultStore, _rank
        from src.vertical_options_ui import _qualified_store_csv

        expiries = (date(2027, 1, 15), date(2028, 1, 21))
        rows = [self._row(expiry, side, 100 + strike,
                          30 - .3 * strike if side == "CALL" else 5 + .3 * strike)
                for expiry in expiries for side in ("CALL", "PUT")
                for strike in range(22)]
        store = VerticalResultStore("QQQ", 110, None, None, "NORMAL")
        for expiry in expiries:
            store.add_expiration(expiry, [row for row in rows if row["expiry"] == expiry],
                                 scan_vertical_candidates)
        expected, annual = scan_vertical_candidates(
            symbol="QQQ", spot_last=110, option_rows=rows,
            target_breakeven=None, max_cost=None, max_per_expiration=None,
        )
        self.assertEqual(len(expected), 924)
        self.assertEqual(len(store), len(expected))
        self.assertEqual(list(store.iter_ranked()), expected)
        self.assertEqual(store.annual_ids, annual)

        fields = {"buy": "buy_strike", "sell": "sell_strike",
                  "buy_last": "buy_last", "sell_last": "sell_last",
                  "cost": "cost", "breakeven": "breakeven",
                  "ratio": "reward_risk", "profit": "max_profit"}
        for column, field in fields.items():
            values = sorted(row[field] for row in expected)
            lower, upper = values[len(values) // 3], values[2 * len(values) // 3]
            matching = sum(lower <= row[field] <= upper for row in expected)
            self.assertEqual(store.count(column_filters={column: {"min": lower, "max": upper}}),
                             matching, column)
        for column, value, field in (("ticker", "QQQ", "symbol"),
                                     ("side", "PUT", "call_put"),
                                     ("expiry", expiries[1].isoformat(), "expiry"),
                                     ("basis", "LAST*", "basis")):
            matching = sum((row[field].isoformat() if field == "expiry" else row[field]) == value
                           for row in expected)
            self.assertEqual(store.count(column_filters={column: [value]}), matching, column)

        filters = {"side": ["PUT"], "expiry": [expiries[1].isoformat()],
                   "cost": {"max": 500}, "ratio": {"min": 2}}
        matching = list(store.iter_rows(column_filters=filters))
        self.assertEqual(len(matching), 216)
        self.assertEqual(store.count(column_filters=filters), len(matching))
        self.assertEqual(store.page(column_filters=filters, offset=200), matching[200:])
        self.assertEqual(store.page(column_filters=filters, offset=215, limit=1), matching[215:216])
        self.assertEqual([row["id"] for row in store.best_by_year(column_filters=filters)],
                         [min(matching, key=lambda row: _rank(row, None))["id"]])

        exported = list(csv.DictReader(io.StringIO(_qualified_store_csv(
            store, source="E*TRADE", loaded_at=1000, target=None,
            max_cost=None, column_filters=filters,
        ))))
        self.assertEqual(len(exported), len(matching))
        self.assertEqual([float(row["Cost Per Contract ($)"]) for row in exported],
                         [row["cost"] for row in matching])
        self.assertTrue(all(row["Type"] == "PUT"
                            and row["Expiration"] == expiries[1].isoformat()
                            and float(row["Risk Reward (to 1)"]) >= 2
                            for row in exported))
        self.assertTrue(all(row["Target Breakeven ($)"] == "" for row in exported))

    def test_disk_store_rolls_back_failed_expiration_and_cleans_up(self):
        import gc
        import weakref
        from src.vertical_options_store import VerticalResultStore
        expiry = date(2027, 1, 15)
        rows = [self._row(expiry, side, 100 + k, 15 - .2 * k if side == "CALL" else 2 + .2 * k)
                for side in ("CALL", "PUT") for k in range(30)]
        store = VerticalResultStore("QQQ", 110, 110, None, "NORMAL")
        def fail_after_flush(**kwargs):
            sink = kwargs.pop("candidate_sink")
            count = 0
            def failing_sink(row):
                nonlocal count
                sink(row)
                count += 1
                if count == 600:
                    raise RuntimeError("source failed mid-expiration")
            return scan_vertical_candidates(**kwargs, candidate_sink=failing_sink)
        with self.assertRaisesRegex(RuntimeError, "mid-expiration"):
            store.add_expiration(expiry, rows, fail_after_flush)
        self.assertEqual(len(store), 0)
        self.assertEqual(store.count(side="CALL"), 0)
        self.assertEqual(store.annual_ids, set())
        store.add_expiration(expiry, rows, scan_vertical_candidates)
        self.assertEqual(len(store), 870)
        directory = store.path.parent
        reference = weakref.ref(store)
        del store
        gc.collect()
        self.assertIsNone(reference())
        self.assertFalse(directory.exists())

    def test_incremental_ranking_matches_full_scan_and_only_prices_new_expiries(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, 1, 15), date(2027, 6, 18), date(2028, 1, 21)]
        rows = [self._row(expiry, side, 100 + k, 10 - .2 * k if side == "CALL" else 2 + .2 * k,
                          bid=9.98 - .2 * k if side == "CALL" else 1.98 + .2 * k,
                          ask=10.02 - .2 * k if side == "CALL" else 2.02 + .2 * k)
                for expiry in expiries for side in ("CALL", "PUT") for k in range(8)]
        raw = {"symbol": "QQQ", "spot": 103, "rows": rows[:16]}
        with (patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "scan_vertical_candidates", wraps=scan_vertical_candidates) as calculate):
            first = vo._rank_cached_vertical_chains(raw, 103, None, "NORMAL")
            self.assertEqual(calculate.call_count, 1)
            raw["rows"] = rows
            complete = vo._rank_cached_vertical_chains(raw, 103, None, "NORMAL")
            self.assertEqual(calculate.call_count, 3)
            self.assertEqual(vo._rank_cached_vertical_chains(raw, 103, None, "NORMAL"), complete)
            self.assertEqual(calculate.call_count, 3)
            for mode in ("SMART FILL", "NORMAL"):
                actual = vo._rank_cached_vertical_chains(raw, 103, None, mode)
                expected = scan_vertical_candidates(symbol="QQQ", spot_last=103, option_rows=rows,
                    target_breakeven=103, max_cost=None, max_per_expiration=None, pricing_mode=mode)
                self.assertEqual((list(actual[0].iter_ranked()), actual[1]), expected)
            self.assertEqual(calculate.call_count, 6)  # NORMAL reused after SMART.
            self.assertTrue(set(first[1]).issubset(complete[0].get_many(first[1])))
            vo._rank_cached_vertical_chains(raw, 104, 100, "NORMAL")
            self.assertEqual(calculate.call_count, 9)
            self.assertEqual(len(raw["result_stores"]), 2)

    def test_incremental_ranking_retains_every_pair_as_universe_grows(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, 1, 15), date(2028, 1, 21)]
        rows = [self._row(expiry, side, 100 + k, 10 - .2 * k if side == "CALL" else 2 + .2 * k)
                for expiry in expiries for side in ("CALL", "PUT") for k in range(8)]
        raw = {"symbol": "QQQ", "spot": 103, "rows": rows[:16]}
        with (patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "scan_vertical_candidates", wraps=scan_vertical_candidates) as calculate):
            self.assertIsNone(vo._rank_cached_vertical_chains(raw, 103, None, "NORMAL")[2])
            raw["rows"] = rows
            actual = vo._rank_cached_vertical_chains(raw, 103, None, "NORMAL")
            self.assertEqual(calculate.call_count, 2)
            self.assertEqual(actual[2:], (None, 112))
            expected = scan_vertical_candidates(symbol="QQQ", spot_last=103, option_rows=rows,
                target_breakeven=103, max_cost=None, max_per_expiration=None)
            self.assertEqual((list(actual[0].iter_ranked()), actual[1]), expected)

    def test_capped_ranking_preserves_full_scan_winners_and_exact_top_rows(self):
        import random
        rng = random.Random(2031)
        rows = []
        for expiry in (date(2027, 1, 15), date(2027, 6, 18), date(2028, 1, 21)):
            for side in ("CALL", "PUT"):
                for k in range(20):
                    last = 12 - .3 * k if side == "CALL" else 2 + .3 * k
                    rows.append(self._row(expiry, side, 100 + k, last,
                        bid=last - rng.uniform(.01, .08), ask=last + rng.uniform(.01, .08)))
        rng.shuffle(rows)
        for mode in ("NORMAL", "SMART FILL"):
            args = dict(symbol="QQQ", spot_last=110, option_rows=rows,
                        target_breakeven=110, max_cost=None, pricing_mode=mode)
            all_rows, all_winners = scan_vertical_candidates(**args, max_per_expiration=None)
            counts = {}
            expected = []
            for row in all_rows:
                count = counts.get(row["expiry"], 0)
                if count < 7:
                    expected.append(row)
                    counts[row["expiry"]] = count + 1
            actual, winners = scan_vertical_candidates(**args, max_per_expiration=7)
            self.assertEqual(actual, expected)
            self.assertEqual(winners, all_winners)

    def test_pair_count_never_caps_even_massive_universes(self):
        from src.vertical_options_ui import _safe_vertical_pair_limit
        expiry = date(2027, 1, 15)
        small = [
            {"expiry": expiry, "call_put": side, "strike": float(k), "osi": f"{side}{k}"}
            for side in ("CALL", "PUT") for k in range(1, 11)
        ]
        self.assertEqual(_safe_vertical_pair_limit(small), (None, 90))
        # One very liquid expiration: 500 strikes on both sides.
        massive = [
            {"expiry": expiry, "call_put": side, "strike": float(k), "osi": f"{side}{k}"}
            for side in ("CALL", "PUT") for k in range(1, 501)
        ]
        self.assertEqual(_safe_vertical_pair_limit(massive), (None, 249500))
        massive.append(dict(massive[0]))  # duplicate transport row not double-counted
        self.assertEqual(_safe_vertical_pair_limit(massive), (None, 249500))

    def test_auto_continuation_finishes_same_snapshot_without_broker_orders(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, month, 15) for month in (1, 2, 3)]
        client = MagicMock()
        client.is_offline = False
        client.get_quote.return_value = {"lastTrade": 100}
        def chain(client, symbol, expiry, touch):
            return [self._row(expiry, "CALL", 100, 1.2), self._row(expiry, "CALL", 105, .2)]
        state = {"_vo_checked_spreads": {"ids": {"preserve-me"}}}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "options_market_client", return_value=client),
              patch.object(vo, "quote_summary", return_value={"description": "Test"}),
              patch.object(vo, "find_number", return_value=100),
              patch.object(vo, "_expirations", return_value=expiries) as get_expiries,
              patch.object(vo, "_chain", side_effect=chain) as get_chain,
              patch.object(vo, "record_lookup"), patch.object(vo, "_SCAN_BATCH_EXPIRATIONS", 1)):
            raw = vo._scan_all_years(client, "QQQ", lambda: None)
            store, annual, _, _ = vo._rank_cached_vertical_chains(raw, 100, None, "NORMAL")
            result = {"symbol": "QQQ", "source": "E*TRADE", "raw_scan_id": raw["scan_id"],
                      "loaded_at": raw["loaded_at"], "scope": "stable", "target": 100,
                      "max_cost": None, "pricing_mode": "NORMAL", "candidates": store,
                      "annual_ids": annual, "scan_complete": False}
            original_time = result["loaded_at"]
            for _ in range(2):
                result = vo._continue_vertical_scan(result, client, lambda: None)
            self.assertTrue(result["scan_complete"])
            self.assertEqual(result["next_expiry_index"], 3)
            self.assertEqual(result["loaded_at"], original_time)
            self.assertEqual(result["scope"], "stable")
            self.assertEqual(len(result["candidates"]), 3)
            self.assertEqual(get_chain.call_count, 3)
            self.assertEqual(get_expiries.call_count, 1)
            self.assertEqual(client.get_quote.call_count, 1)
            self.assertEqual(state["_vo_checked_spreads"]["ids"], {"preserve-me"})
            client.preview_order.assert_not_called()
            client.place_order.assert_not_called()
            with patch.object(vo.time, "time", return_value=original_time + 301):
                with self.assertRaisesRegex(ValueError, "EXPIRED"):
                    vo._continue_vertical_scan(result, client, lambda: None)
            self.assertEqual(get_chain.call_count, 3)

    def test_chain_scan_returns_partial_result_and_resumes_without_refetch(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, i, 15) for i in (1, 2, 3)]
        client = MagicMock()
        client.is_offline = False
        client.get_quote.return_value = {"lastTrade": 100}
        rows_loaded = []
        def load_chain(client, symbol, expiry, touch_session):
            rows_loaded.append(expiry)
            return [{"expiry": expiry, "call_put": "CALL", "strike": 100.0,
                     "last": 1.0, "bid": .95, "ask": 1.05, "osi": "TEST"}]
        state = {}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "options_market_client", return_value=client),
              patch.object(vo, "quote_summary", return_value={"description": "Test"}),
              patch.object(vo, "find_number", return_value=100),
              patch.object(vo, "_expirations", return_value=expiries) as get_expiries,
              patch.object(vo, "_chain", side_effect=load_chain),
              patch.object(vo, "_tradingview_listing_exchange", return_value="NASDAQ"),
              patch.object(vo, "record_lookup"),
              patch.object(vo, "_SCAN_BATCH_SECONDS", -1)):
            partial = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertFalse(partial["scan_complete"])
            self.assertEqual(partial["next_expiry_index"], 1)
            self.assertEqual(len(partial["rows"]), 1)
            with patch.object(vo, "_SCAN_BATCH_SECONDS", 999):
                complete = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertTrue(complete["scan_complete"])
            self.assertEqual(complete["next_expiry_index"], 3)
            self.assertEqual(len(complete["rows"]), 3)
            self.assertEqual(complete["loaded_at"], partial["loaded_at"])
            self.assertEqual(complete["scan_id"], partial["scan_id"])
            self.assertEqual(rows_loaded, expiries)
            self.assertEqual(client.get_quote.call_count, 1)
            self.assertEqual(get_expiries.call_count, 1)
            self.assertIs(vo._scan_all_years(client, "QQQ", lambda: None), complete)

    def test_empty_first_batch_remains_resumable_and_expired_snapshot_restarts(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, 1, 15), date(2028, 1, 21)]
        client = MagicMock(is_offline=False)
        state = {}
        row = self._row(expiries[1], "CALL", 100, 1)
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "options_market_client", return_value=client),
              patch.object(vo, "quote_summary", return_value={"description": "Test"}),
              patch.object(vo, "find_number", return_value=100),
              patch.object(vo, "_expirations", return_value=expiries) as expirations,
              patch.object(vo, "_chain", side_effect=[[], [row], [], [row]]) as chains,
              patch.object(vo, "_tradingview_listing_exchange", return_value="NASDAQ"),
              patch.object(vo, "record_lookup"),
              patch.object(vo, "_SCAN_BATCH_EXPIRATIONS", 1),
              patch.object(vo.time, "time", return_value=1000)):
            partial = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertEqual(partial["rows"], [])
            self.assertFalse(partial["scan_complete"])
            complete = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertEqual(complete["rows"], [row])
            self.assertTrue(complete["scan_complete"])
            complete["result_stores"] = {"old": "must be discarded"}
            with patch.object(vo.time, "time", return_value=1301):
                restarted = vo._scan_all_years(client, "QQQ", lambda: None)
                self.assertFalse(restarted["scan_complete"])
                self.assertEqual(restarted["next_expiry_index"], 1)
                self.assertEqual(restarted["result_stores"], {})
                self.assertEqual(restarted["loaded_at"], 1301)
            self.assertEqual(expirations.call_count, 2)
            self.assertEqual(chains.call_count, 3)

    def test_empty_expiration_chains_do_not_hide_later_valid_chains(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, i, 15) for i in (1, 2, 3)]
        client = MagicMock()
        client.is_offline = False
        client.get_quote.return_value = {"lastTrade": 100}
        def chain_for_expiry(client, symbol, expiry, touch_session):
            if expiry == expiries[-1]:
                return [{"expiry": expiry, "call_put": "CALL", "strike": 100,
                         "last": 1, "bid": .9, "ask": 1.1, "osi": "TEST"}]
            return []
        with (patch.object(vo.st, "session_state", {}),
              patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "options_market_client", return_value=client),
              patch.object(vo, "quote_summary", return_value={"description": "Test"}),
              patch.object(vo, "find_number", return_value=100),
              patch.object(vo, "_expirations", return_value=expiries),
              patch.object(vo, "_chain", side_effect=chain_for_expiry) as chains,
              patch.object(vo, "_tradingview_listing_exchange", return_value="NASDAQ"),
              patch.object(vo, "record_lookup"),
              patch.object(vo, "_SCAN_BATCH_SECONDS", 999)):
            result = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertEqual(result["loaded_expirations"], 1)
            self.assertEqual(len(result["errors"]), 2)
            self.assertTrue(result["scan_complete"])
            self.assertEqual(chains.call_count, 3)

    def test_repeated_chain_failures_stop_without_hammering_broker(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, i, 15) for i in range(1, 7)]
        client = MagicMock()
        client.is_offline = False
        client.get_quote.return_value = {"lastTrade": 100}
        with (patch.object(vo.st, "session_state", {}),
              patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "options_market_client", return_value=client),
              patch.object(vo, "quote_summary", return_value={"description": "Test"}),
              patch.object(vo, "find_number", return_value=100),
              patch.object(vo, "_expirations", return_value=expiries),
              patch.object(vo, "_chain", side_effect=RuntimeError("HTTP 401")) as chains,
              patch.object(vo, "_tradingview_listing_exchange", return_value="NASDAQ"),
              patch.object(vo, "record_lookup"),
              patch.object(vo, "_SCAN_BATCH_SECONDS", 999)):
            with self.assertRaisesRegex(ValueError, "No option contracts loaded"):
                vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertEqual(chains.call_count, 2)

    def test_single_chain_failure_is_recorded_and_full_retry_clears_it(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        expiries = [date(2027, i, 15) for i in (1, 2, 3)]
        client = MagicMock(is_offline=False)
        rows = {expiry: [self._row(expiry, "CALL", 100, 1)] for expiry in expiries}
        attempts = []

        def chain(_client, _symbol, expiry, _touch):
            attempts.append(expiry)
            if expiry == expiries[1] and attempts.count(expiry) == 1:
                raise RuntimeError("temporary chain error")
            return rows[expiry]

        state = {}
        with (patch.object(vo.st, "session_state", state),
              patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "options_market_client", return_value=client),
              patch.object(vo, "quote_summary", return_value={"description": "Test"}),
              patch.object(vo, "find_number", return_value=100),
              patch.object(vo, "_expirations", return_value=expiries),
              patch.object(vo, "_chain", side_effect=chain),
              patch.object(vo, "_tradingview_listing_exchange", return_value="NASDAQ"),
              patch.object(vo, "record_lookup"),
              patch.object(vo, "_SCAN_BATCH_EXPIRATIONS", 4),
              patch.object(vo, "_SCAN_BATCH_SECONDS", 999)):
            incomplete = vo._scan_all_years(client, "QQQ", lambda: None)
            self.assertTrue(incomplete["scan_complete"])
            self.assertEqual(incomplete["failed_expirations"], [expiries[1]])
            self.assertEqual(incomplete["loaded_expirations"], 2)
            self.assertEqual(attempts, expiries)
            retried = vo._scan_all_years(client, "QQQ", lambda: None, refresh=True)
            self.assertEqual(retried["failed_expirations"], [])
            self.assertEqual(retried["loaded_expirations"], 3)
            self.assertEqual(attempts, expiries + expiries)

    def test_expiration_detail_uses_calendar_days_and_fractional_years(self):
        from src.vertical_options_ui import _expiry_time_label
        self.assertEqual(_expiry_time_label(date(2026, 10, 23), date(2026, 10, 21)), "2d · 0.01y")
        self.assertEqual(_expiry_time_label(date(2028, 10, 21), date(2026, 10, 21)), "731d · 2.00y")
        self.assertEqual(_expiry_time_label(date(2026, 10, 21), date(2026, 10, 21)), "0d · 0.00y")
        self.assertEqual(_expiry_time_label(date(2026, 10, 20), date(2026, 10, 21)), "0d · 0.00y")

    def test_default_breakeven_uses_nearest_valid_exact_ticker_cboe_wall(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        results = {"SPY": {"callWall": {"strike": 110}, "putWall": {"strike": 90}}}
        with patch.object(vo.st, "session_state", {}), patch.object(vo, "cboe_results", return_value=results):
            self.assertEqual(vo._default_breakeven("SPY", 108, "vault"), (110, "CBOE CALL WALL DEFAULT"))
            self.assertEqual(vo._default_breakeven("SPY", 92, "vault"), (90, "CBOE PUT WALL DEFAULT"))
            self.assertEqual(vo._default_breakeven("SPY", 100, "vault")[0], 110)
            self.assertEqual(vo._default_breakeven("SPYM", 99, "vault"), (99, "SPOT DEFAULT"))
            self.assertEqual(vo._default_breakeven("SPY", None, "vault")[0], None)
            results["SPY"]["callWall"]["strike"] = float("nan")
            self.assertEqual(vo._default_breakeven("SPY", 108, "vault")[0], 90)
            results["SPY"]["putWall"]["strike"] = -90
            self.assertEqual(vo._default_breakeven("SPY", 108, "vault"), (108, "SPOT DEFAULT"))

    def test_no_breakeven_default_preserves_manual_target_per_ticker(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        session = {}
        results = {"SPY": {"callWall": {"strike": 110}, "putWall": {"strike": 90}}}
        with (patch.object(vo.st, "session_state", session),
              patch.object(vo, "cboe_results", return_value=results) as cboe):
            vo._seed_breakeven_preset("SPY", 108, "vault")
            self.assertEqual(session["vo_target_breakeven"], "")
            session["vo_target_breakeven"] = "103"
            vo._seed_breakeven_preset("SPY", 108, "vault")
            self.assertEqual(session["vo_target_breakeven"], "103")
            vo._seed_breakeven_preset("QQQ", 400, "vault")
            self.assertEqual(session["vo_target_breakeven"], "")
            vo._seed_breakeven_preset("SPY", 108, "vault")
            self.assertEqual(session["vo_target_breakeven"], "103")
            session["vo_target_breakeven"] = ""
            vo._seed_breakeven_preset("SPY", 108, "vault")
            self.assertEqual(session["vo_target_breakeven"], "")
            vo._seed_breakeven_preset("MSFT", 400, "vault")
            self.assertEqual(session["vo_target_breakeven"], "")
            self.assertNotIn("_vo_auto_target", session)
            cboe.assert_not_called()

    def test_pasted_ticker_table_parses_deduplicates_and_preserves_order(self):
        from src.vertical_options_ui import _parse_ticker_list
        table = """| Ticker |
| ------ |
| FER |
| RELX |
| SONY |
| PEP |
| SCHW |
| LOW |
| GOOG |
| GOOGL |
| SPGI |
| APH |
| LMT |
| AMZN |
| BMY |
| RTX |
| MCD |
| MSFT |
| ABEV |
| AZN |
| LIN |
| SYK |
| RHHBY |
"""
        self.assertEqual(_parse_ticker_list(table), [
            "FER", "RELX", "SONY", "PEP", "SCHW", "LOW", "GOOG", "GOOGL",
            "SPGI", "APH", "LMT", "AMZN", "BMY", "RTX", "MCD", "MSFT",
            "ABEV", "AZN", "LIN", "SYK", "RHHBY",
        ])
        self.assertEqual(_parse_ticker_list("msft, aapl; BRK.B\nMSFT | spy"),
                         ["MSFT", "AAPL", "BRK.B", "SPY"])
        with self.assertRaisesRegex(ValueError, "Invalid ticker"):
            _parse_ticker_list("MSFT\nNOT_A_TICKER")
        with self.assertRaisesRegex(ValueError, "Paste at least one"):
            _parse_ticker_list("| Ticker |\n| ----- |")

    def test_etrade_3031_rejection_is_explained_without_claiming_a_saved_order(self):
        from src.vertical_options_ui import _vertical_preview_error
        raw = (
            'E*TRADE returned HTTP 400: {"Error":{"code":3031,'
            '"message":"We are unable to accept your order as entered since it '
            'would create an unapproved options position for your account when '
            'paired with current positions and/or open orders."}}'
        )
        message = _vertical_preview_error(ValueError(raw))
        self.assertIn("ERROR 3031", message)
        self.assertIn("Level 3", message)
        self.assertIn("existing", message.lower())
        self.assertIn("no order was placed", message.lower())
        self.assertNotIn("SUBMITTED", message)
        self.assertEqual(
            _vertical_preview_error(ValueError("Other brokerage failure")),
            "Other brokerage failure",
        )

    def test_live_place_cta_is_not_a_save_action(self):
        ui = (Path(__file__).resolve().parents[1] / "src" / "vertical_options_ui.py").read_text()
        self.assertIn("PLACE LIVE ORDER //", ui)
        self.assertIn("MAY QUEUE", ui)
        self.assertIn("TERMINAL-ONLY DRAFT", ui)
        self.assertIn("The E*TRADE public API cannot create broker-side Saved orders.", ui)
        self.assertIn("st.session_state.pop(\"vo_review_bundle\", None)", ui)

    def test_save_multiple_spreads_as_local_drafts_without_broker_actions(self):
        import csv
        import io
        from src.vertical_options_ui import _save_checked_spreads, _saved_drafts_csv
        candidates = [
            {
                "id": "MSFT|2029-01-19|CALL|500|520",
                "symbol": "MSFT", "call_put": "CALL",
                "expiry": date(2029, 1, 19), "buy_strike": 500,
                "sell_strike": 520, "basis": "ASK", "debit": 4.25,
                "cost": 425, "breakeven": 504.25, "reward_risk": 3.7,
            },
            {
                "id": "SONY|2028-01-21|PUT|30|25",
                "symbol": "SONY", "call_put": "PUT",
                "expiry": date(2028, 1, 21), "buy_strike": 30,
                "sell_strike": 25, "basis": "MID", "debit": 1.0,
                "cost": 100, "breakeven": 29, "reward_risk": 4.0,
            },
        ]
        drafts = {}
        self.assertEqual(_save_checked_spreads(drafts, candidates, "E*TRADE"), 2)
        self.assertEqual(_save_checked_spreads(drafts, candidates, "E*TRADE"), 0)
        self.assertEqual(len(drafts), 2)
        self.assertEqual(drafts[candidates[0]["id"]]["quantity"], 1)
        drafts[candidates[0]["id"]]["quantity"] = 3
        values = list(csv.DictReader(io.StringIO(_saved_drafts_csv(drafts))))
        self.assertEqual([item["Ticker"] for item in values], ["MSFT", "SONY"])
        self.assertEqual(values[0]["Quantity"], "3")
        self.assertEqual(values[0]["Est. Cost"], "1275.0")
        self.assertEqual(values[0]["Status"], "TERMINAL SESSION DRAFT - NOT SENT")
        self.assertEqual(values[1]["Expiration"], "2028-01-21")
        self.assertEqual(values[1]["Quote Source"], "E*TRADE")
        self.assertNotIn("preview_id", str(drafts))
        self.assertNotIn("place_payload", str(drafts))

    @staticmethod
    def _row(expiry, side, strike, last, *, bid=None, ask=None, osi=""):
        return {
            "expiry": expiry, "call_put": side, "strike": strike,
            "last": last, "bid": bid, "ask": ask,
            "osi": osi or f"{side}-{expiry}-{strike}",
        }

    @staticmethod
    def _xlsx_export_rows(payload):
        import io
        import zipfile
        from datetime import timedelta
        from xml.etree import ElementTree as ET

        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(book.read("xl/styles.xml"))
            shared = []
            if "xl/sharedStrings.xml" in book.namelist():
                strings = ET.fromstring(book.read("xl/sharedStrings.xml"))
                shared = ["".join(item.itertext()) for item in strings.findall("x:si", ns)]
        xfs = styles.find("x:cellXfs", ns)
        fills = styles.find("x:fills", ns)
        fonts = styles.find("x:fonts", ns)

        def column_index(reference):
            index = 0
            for character in reference:
                if not character.isalpha():
                    break
                index = 26 * index + ord(character) - ord("A") + 1
            return index - 1

        def cell_value(cell):
            if cell.get("t") == "inlineStr":
                inline = cell.find("x:is", ns)
                return "" if inline is None else "".join(inline.itertext())
            value = cell.find("x:v", ns)
            if value is None:
                return ""
            return shared[int(value.text)] if cell.get("t") == "s" else value.text

        def colors(style_index):
            style = xfs[int(style_index)]
            fill = fills[int(style.get("fillId", "0"))]
            font = fonts[int(style.get("fontId", "0"))]
            background = fill.find("x:patternFill/x:fgColor", ns)
            foreground = font.find("x:color", ns)
            return (background.get("rgb", "")[-6:].upper() if background is not None else "",
                    foreground.get("rgb", "")[-6:].upper() if foreground is not None else "")

        report = []
        for xml_row in sheet.findall("x:sheetData/x:row", ns):
            cells = {}
            row_colors = []
            row_formulas = []
            for cell in xml_row.findall("x:c", ns):
                index = column_index(cell.get("r", ""))
                cells[index] = cell_value(cell)
                if int(xml_row.get("r", "0")) > 1:
                    row_colors.append(colors(cell.get("s", xml_row.get("s", "0"))))
                    row_formulas.append(index if cell.find("x:f", ns) is not None else -1)
            report.append((cells, row_colors, row_formulas))
        headers = [report[0][0][index] for index in range(len(report[0][0]))]
        data = []
        for cells, row_colors, formulas in report[1:]:
            values = {header: cells.get(index, "") for index, header in enumerate(headers)}
            expiration = values.get("Expiration", "")
            if expiration and "-" not in expiration:
                values["Expiration"] = (date(1899, 12, 30)
                                        + timedelta(days=float(expiration))).isoformat()
            data.append({
                "values": values, "styles": row_colors,
                "formula_columns": [item for item in formulas if item >= 0],
            })
        return headers, data

    def test_scan_both_canonical_sides_use_ask_when_it_meets_constraints(self):
        expiry = date(2027, 1, 15)
        rows = [
            self._row(expiry, "CALL", 100, 3, bid=2.70, ask=3.10),
            self._row(expiry, "CALL", 105, 2, bid=1.95, ask=2.30),
            self._row(expiry, "PUT", 95, 2, bid=1.95, ask=2.30),
            self._row(expiry, "PUT", 100, 3, bid=2.70, ask=3.10),
        ]
        found, annual = scan_vertical_candidates(
            symbol="spy", spot_last=102.50, option_rows=rows,
            target_breakeven=101.15, max_cost=150,
        )
        self.assertEqual(len(found), 2)
        call, put = found
        self.assertEqual((call["call_put"], call["buy_strike"], call["sell_strike"]),
                         ("CALL", 100, 105))
        self.assertEqual((put["call_put"], put["buy_strike"], put["sell_strike"]),
                         ("PUT", 100, 95))
        self.assertEqual((call["basis"], call["orderable"], call["debit"]), ("ASK", True, 1.15))
        self.assertEqual((call["cost"], call["max_profit"], call["max_loss"]),
                         (115, 385, 115))
        self.assertEqual(call["breakeven"], 101.15)
        self.assertEqual(put["breakeven"], 98.85)
        self.assertEqual((call["buy_last"], call["buy_bid"], call["buy_ask"]),
                         (3, 2.70, 3.10))
        self.assertEqual((call["sell_last"], call["sell_bid"], call["sell_ask"]),
                         (2, 1.95, 2.30))
        self.assertEqual(annual, {call["id"]})

    def test_smart_fill_improves_on_natural_ask_without_changing_normal(self):
        from src.vertical_options import price_vertical_quote, smart_fill_ladder
        data = dict(
            buy_last=4.5, sell_last=3, buy_bid=4.0, buy_ask=5.0,
            sell_bid=3.0, sell_ask=3.7, width=5, max_cost=150,
        )
        original = price_vertical_quote(**data)
        self.assertEqual((original["basis"], original["debit"]), ("LAST", 1.5))
        smart = price_vertical_quote(**data, pricing_mode="SMART FILL")
        self.assertEqual((smart["basis"], smart["debit"], smart["cost"]),
                         ("SMART", 1.37, 137))
        self.assertEqual(smart["spread_mid"], 1.15)
        self.assertEqual(smart["spread_ask"], 2.0)
        self.assertTrue(smart["orderable"])
        self.assertGreaterEqual((5 - smart["debit"]) / smart["debit"], 2)
        self.assertEqual(
            smart_fill_ladder({**smart, "width": 5}, 150),
            [1.37, 1.42, 1.47],
        )

    def test_smart_fill_liquidity_signal_and_never_chases_above_budget(self):
        from src.vertical_options import price_vertical_quote, smart_fill_ladder
        data = dict(
            buy_last=4.5, sell_last=3.0, buy_bid=4.0, buy_ask=5.0,
            sell_bid=3.0, sell_ask=3.7, width=5, max_cost=120,
            pricing_mode="SMART FILL",
        )
        capped = price_vertical_quote(**data)
        self.assertEqual((capped["basis"], capped["debit"]), ("SMART", 1.20))
        self.assertEqual(smart_fill_ladder({**capped, "width": 5}, 120), [1.2])
        self.assertIsNone(price_vertical_quote(**{**data, "max_cost": 100}))
        active = price_vertical_quote(**{
            **data, "max_cost": 200, "buy_volume": 60, "sell_volume": 70,
            "buy_open_interest": 300, "sell_open_interest": 500,
        })
        self.assertEqual(active["liquidity_signal"], "ACTIVE LEGS")
        self.assertGreater(active["debit"], active["spread_mid"])
        thin = price_vertical_quote(**{
            **data, "max_cost": 200, "buy_volume": 1, "sell_volume": 2,
            "buy_open_interest": 10, "sell_open_interest": 15,
        })
        self.assertEqual(thin["liquidity_signal"], "THIN LEGS")
        self.assertLessEqual(thin["debit"], active["debit"])
        with self.assertRaisesRegex(ValueError, "pricing mode"):
            price_vertical_quote(**{**data, "pricing_mode": "WRONG"})

    def test_smart_mode_scan_keeps_pair_identity_and_recomputes_risk(self):
        expiry = date(2029, 1, 19)
        rows = [
            self._row(expiry, "CALL", 100, 4.5, bid=4.0, ask=5.0),
            self._row(expiry, "CALL", 105, 3.0, bid=3.0, ask=3.7),
        ]
        normal, _ = scan_vertical_candidates(
            symbol="SPY", spot_last=102, option_rows=rows,
            target_breakeven=101, max_cost=None, pricing_mode="NORMAL",
        )
        smart, annual = scan_vertical_candidates(
            symbol="SPY", spot_last=102, option_rows=rows,
            target_breakeven=101, max_cost=None, pricing_mode="SMART FILL",
        )
        self.assertEqual(len(normal), len(smart))
        self.assertEqual(normal[0]["id"], smart[0]["id"])
        self.assertEqual(smart[0]["basis"], "SMART")
        self.assertLess(smart[0]["cost"], normal[0]["cost"])
        self.assertEqual(smart[0]["breakeven"], 101.37)
        self.assertAlmostEqual(smart[0]["reward_risk"],
                               (5 - 1.37) / 1.37)
        self.assertEqual(annual, {smart[0]["id"]})
        self.assertEqual(smart[0]["pricing_mode"], "SMART FILL")

    def test_mode_help_icons_have_distinct_hover_and_keyboard_explanations(self):
        ui = (Path(__file__).resolve().parents[1] / "src" / "vertical_options_ui.py").read_text()
        self.assertIn('aria-label="Normal mode explanation"', ui)
        self.assertIn('aria-label="Smart Fill mode explanation"', ui)
        self.assertEqual(ui.count('role="tooltip"'), 2)
        self.assertIn('tabindex="0"', ui)
        self.assertIn(".vo-mode-help:hover .vo-mode-tooltip", ui)
        self.assertIn(".vo-mode-help:focus .vo-mode-tooltip", ui)
        self.assertIn("natural ASK first when it meets your selected reward-to-risk", ui)
        self.assertIn("Suggests a limit between spread MID and natural ASK", ui)
        self.assertIn("Does not guarantee a fill or place orders automatically.", ui)
        self.assertIn('pricing_mode = _pricing_mode_flip_switch()', ui)
        self.assertIn('st.toggle(', ui)
        self.assertIn('key="vo_smart_fill"', ui)

    def test_pricing_switch_keeps_native_control_visible_and_browser_frames_zero_height(self):
        ui = (Path(__file__).resolve().parents[1] / "src" / "vertical_options_ui.py").read_text()
        self.assertIn('.st-key-vertical_options_workspace [data-testid="stTextInput"] input,', ui)
        self.assertNotIn('.st-key-vertical_options_workspace input,', ui)
        self.assertNotIn('.st-key-vo_smart_fill label > div:first-of-type', ui)
        self.assertIn('label_visibility="collapsed"', ui)
        self.assertIn('mode_guide_col, mode_switch_col, smart_label_col = st.columns(', ui)
        self.assertIn('with mode_switch_col:', ui)
        self.assertIn('key="vo_pricing_preference_reader", height=0', ui)
        self.assertIn('key="vo_pricing_preference_writer", height=0', ui)

    def test_native_switch_theme_is_scoped_and_emitted_every_session(self):
        from unittest.mock import patch
        import src.theme as theme
        with patch.object(theme.st, "html") as output:
            theme.install_native_switch_theme("vo_smart_fill")
            theme.install_native_switch_theme("vo_smart_fill")
            self.assertEqual(output.call_count, 2)
            css = output.call_args.args[0]
            self.assertIn('.st-key-vo_smart_fill label:has(input[role="switch"])', css)
            self.assertIn("outline:1px solid #fb8b1e!important", css)
            self.assertIn("background-color:#fb8b1e!important", css)
            self.assertIn('input[role="switch"]:checked', css)
            self.assertIn('input[role="switch"]:focus-visible', css)
            self.assertNotIn(":hover", css)
            theme.install_native_switch_theme("risk_capital_source_tactical")
            self.assertIn(".st-key-risk_capital_source_tactical", output.call_args.args[0])
        with self.assertRaises(ValueError):
            theme.install_native_switch_theme("bad key > *")
        root = Path(__file__).resolve().parents[1]
        self.assertIn('install_native_switch_theme("risk_capital_source_tactical")',
                      (root / "src/risk_sizing_ui_v2.py").read_text())
        self.assertIn('install_native_switch_theme("vo_smart_fill")',
                      (root / "src/vertical_options_ui.py").read_text())

    def test_ticker_preview_is_last_only_cached_and_never_loads_chains(self):
        from unittest.mock import Mock, patch
        import src.vertical_options_ui as vo
        broker = Mock(is_offline=False)
        broker.get_quote.return_value = {"lastTrade": 123.45, "companyName": "Test Fund"}
        with patch.object(vo.st, "session_state", {}), patch.object(vo, "options_market_client", return_value=broker):
            first = vo._ticker_quote_preview(broker, "SPY", lambda: None)
            self.assertEqual(first["last"], 123.45)
            self.assertEqual(first["name"], "Test Fund")
            self.assertEqual(first, vo._ticker_quote_preview(broker, "SPY", lambda: None))
            broker.get_quote.assert_called_once_with("SPY")
            broker.get_option_chain.assert_not_called()
            broker.get_option_expirations.assert_not_called()
            self.assertEqual(vo._ticker_quote_preview(broker, "", lambda: None), {})

    def test_preview_falls_back_to_public_and_does_not_invent_last_from_mid(self):
        from unittest.mock import Mock, patch
        import src.vertical_options_ui as vo
        broker = Mock(is_offline=False)
        broker.get_quote.return_value = {"bid": 100, "ask": 102}
        public = Mock()
        public.get_quote.return_value = {"lastTrade": 101.25, "companyName": "Public Fund"}
        with patch.object(vo.st, "session_state", {}), patch.object(vo, "options_market_client", side_effect=[broker, public]):
            quote = vo._ticker_quote_preview(broker, "SPY", lambda: None)
            self.assertEqual((quote["last"], quote["source"]), (101.25, "YFINANCE"))
        with patch.object(vo.st, "session_state", {}), patch.object(vo, "options_market_client", return_value=broker):
            self.assertIsNone(vo._ticker_quote_preview(None, "SPY", lambda: None)["last"])

    def test_walls_use_exact_cboe_ticker_and_correct_vault_only(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        result = {"SPY": {"callWall": {"strike": 110}, "putWall": {"strike": 90}}}
        with patch.object(vo.st, "session_state", {}), patch.object(vo, "cboe_results", return_value=result) as read:
            markup = vo._cboe_wall_markup("SPY", "terminal-vault")
            self.assertIn("CALL WALL $110.00", markup)
            self.assertIn("PUT WALL $90.00", markup)
            read.assert_called_once_with("terminal-vault")
            self.assertIn("Not in GEX", vo._cboe_wall_markup("SPYM", "terminal-vault"))
        with patch.object(vo.st, "session_state", {"_gex_state": {"tickers": []}}), patch.object(vo, "cboe_results", return_value=result):
            self.assertIn("Not in GEX", vo._cboe_wall_markup("SPY", "terminal-vault"))

    def test_pricing_mode_is_hydrated_before_writing_and_restored_after_tab_change(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        session = {"vo_pricing_mode": "NORMAL"}  # Browser value takes precedence.
        with (patch.object(vo.st, "session_state", session),
              patch.object(vo, "_PRICING_PREFERENCE_COMPONENT") as memory,
              patch.object(vo.st, "toggle", return_value=True) as toggle):
            memory.side_effect = [
                None,  # Browser has not yet responded.
                {"ready": True, "mode": "SMART FILL", "available": True},
                None,  # Write the restored mode after hydration.
                None,  # Subsequent render write.
            ]
            self.assertIsNone(vo._pricing_mode_flip_switch())
            self.assertEqual(memory.call_count, 1)  # No default written!
            self.assertEqual(vo._pricing_mode_flip_switch(), "SMART FILL")
            self.assertTrue(session["vo_smart_fill"])
            self.assertTrue(session["_vo_pricing_mode_hydrated"])
            self.assertEqual(memory.call_args.kwargs["value"], "SMART FILL")
            self.assertEqual(toggle.call_count, 1)
            session["vo_smart_fill"] = False  # Returning frontend sends its default.
            self.assertEqual(vo._pricing_mode_flip_switch(), "SMART FILL")
            self.assertTrue(session["vo_smart_fill"])

    def test_pricing_mode_restores_normal_and_persists_explicit_flip(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        session = {}
        with (patch.object(vo.st, "session_state", session),
              patch.object(vo, "_PRICING_PREFERENCE_COMPONENT") as memory,
              patch.object(vo.st, "toggle", return_value=False)):
            memory.side_effect = [
                {"ready": True, "mode": "NORMAL", "available": True},
                None,
                None,
            ]
            self.assertEqual(vo._pricing_mode_flip_switch(), "NORMAL")
            self.assertFalse(session["vo_smart_fill"])
            self.assertEqual(memory.call_args.kwargs["value"], "NORMAL")
        session["vo_smart_fill"] = True
        with (patch.object(vo.st, "session_state", session),
              patch.object(vo, "_PRICING_PREFERENCE_COMPONENT") as memory,
              patch.object(vo.st, "toggle", return_value=True)):
            self.assertEqual(vo._pricing_mode_flip_switch(), "SMART FILL")
            self.assertEqual(session["_vo_pricing_mode_preference"], "SMART FILL")
            self.assertEqual(memory.call_args.kwargs["value"], "SMART FILL")

    def test_pricing_mode_browser_bridge_uses_persistent_local_storage(self):
        browser = (Path(__file__).resolve().parents[1]
                   / "src/components/vo_pricing_preference_v1/index.html").read_text()
        self.assertIn('localStorage.getItem(KEY)', browser)
        self.assertIn('localStorage.setItem(KEY, value)', browser)
        self.assertIn('args.action==="read"', browser)
        self.assertIn('args.action==="write"', browser)
        self.assertIn('streamlit:setFrameHeight",{height:0}', browser)
        self.assertIn('raj-terminal-vo-pricing-mode-v1', browser)

    def test_smart_mode_requotes_and_has_no_automatic_send_controls(self):
        ui = (Path(__file__).resolve().parents[1] / "src" / "vertical_options_ui.py").read_text()
        self.assertIn('mode = "SMART FILL" if smart else "NORMAL"', ui)
        self.assertIn("def _reprice_cached_vertical_scan(", ui)
        self.assertIn('["GOOD_FOR_DAY"] if smart_mode', ui)
        self.assertIn('checked_price["debit"] != candidate["debit"]', ui)
        self.assertIn('or limit_price > checked_price["spread_ask"] + .005', ui)
        self.assertIn('key="vo_preview"', ui)
        self.assertIn('key="vo_send"', ui)
        self.assertNotIn("client.change_order(", ui)

    def test_msft_stale_last_below_mid_is_not_ranked_as_likely_fill(self):
        expiry = date(2029, 1, 19)
        rows = [
            self._row(expiry, "CALL", 550, 128.20, bid=125.80, ask=128.60),
            self._row(expiry, "CALL", 560, 125.85, bid=120.90, ask=123.70),
        ]
        from src.vertical_options import price_vertical_quote
        priced = price_vertical_quote(
            buy_last=128.20, sell_last=125.85,
            buy_bid=125.80, buy_ask=128.60,
            sell_bid=120.90, sell_ask=123.70,
            width=10, max_cost=1000,
        )
        self.assertIsNone(priced)  # ASK=7.70 and MID=4.90 fail 1:2.
        found, annual = scan_vertical_candidates(
            symbol="MSFT", spot_last=545.34, option_rows=rows,
            target_breakeven=552.35, max_cost=1000,
        )
        self.assertEqual((found, annual), ([], set()))

    def test_in_market_last_is_chosen_ahead_of_mid_for_fill(self):
        from src.vertical_options import price_vertical_quote
        priced = price_vertical_quote(
            buy_last=4.5, sell_last=3.0, buy_bid=4.0, buy_ask=5.0,
            sell_bid=3.0, sell_ask=3.7, width=5, max_cost=150,
        )
        self.assertEqual((priced["basis"], priced["debit"], priced["cost"]), ("LAST", 1.5, 150))
        self.assertTrue(priced["orderable"])
        self.assertEqual(priced["spread_ask"], 2.0)
        self.assertEqual(priced["spread_mid"], 1.15)

    def test_mid_is_used_if_natural_and_stale_last_fail(self):
        from src.vertical_options import price_vertical_quote
        priced = price_vertical_quote(
            buy_last=4.6, sell_last=3.8, buy_bid=4.0, buy_ask=5.0,
            sell_bid=3.1, sell_ask=3.8, width=5, max_cost=150,
        )
        self.assertEqual((priced["basis"], priced["debit"], priced["orderable"]),
                         ("MID", 1.05, True))
        self.assertEqual(priced["spread_ask"], 1.9)

    def test_unquoted_last_is_informational_only(self):
        from src.vertical_options import price_vertical_quote
        priced = price_vertical_quote(
            buy_last=3.0, sell_last=2.0,
            buy_bid=None, buy_ask=None, sell_bid=None, sell_ask=None,
            width=5, max_cost=200,
        )
        self.assertEqual((priced["basis"], priced["orderable"], priced["debit"]),
                         ("LAST*", False, 1.0))

    def test_scan_ratio_boundary_and_cost_cap(self):
        exact = date(2027, 2, 19)
        below = date(2028, 2, 18)
        rows = [
            self._row(exact, "CALL", 100, 2),
            self._row(exact, "CALL", 103, 1),
            self._row(below, "CALL", 100, 2.01),
            self._row(below, "CALL", 103, 1),
        ]
        found, annual = scan_vertical_candidates(
            symbol="SPY", spot_last=100, option_rows=rows,
            target_breakeven=101, max_cost=100,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["expiry"], exact)
        self.assertEqual(found[0]["reward_risk"], 2)
        self.assertEqual(annual, {found[0]["id"]})
        over_budget, winners = scan_vertical_candidates(
            symbol="SPY", spot_last=100, option_rows=rows,
            target_breakeven=101, max_cost=99,
        )
        self.assertEqual((over_budget, winners), ([], set()))

    def test_custom_reward_risk_floor_changes_scan_and_annual_winners(self):
        rows = []
        for expiry, width, debit in (
            (date(2027, 1, 15), 3, 1),   # Exactly 1:2.
            (date(2028, 1, 21), 5, 2),   # Exactly 1:1.5.
            (date(2029, 1, 19), 4, 1),   # Exactly 1:3.
        ):
            rows.extend((
                self._row(expiry, "CALL", 100, debit + 1, bid=debit + 1, ask=debit + 1),
                self._row(expiry, "CALL", 100 + width, 1, bid=1, ask=1),
            ))
        controls = dict(symbol="SPY", spot_last=100, option_rows=rows,
                        target_breakeven=None, max_cost=None, max_per_expiration=None)
        expected_years = {2.0: {2027, 2029}, 1.5: {2027, 2028, 2029}, 3.0: {2029}}
        for minimum, years in expected_years.items():
            with self.subTest(minimum=minimum):
                found, annual = scan_vertical_candidates(**controls, min_reward_risk=minimum)
                self.assertEqual({row["year"] for row in found}, years)
                self.assertEqual({row["year"] for row in found if row["id"] in annual}, years)
                self.assertTrue(all(row["reward_risk"] >= minimum for row in found))
        default, default_annual = scan_vertical_candidates(**controls)
        explicit, explicit_annual = scan_vertical_candidates(**controls, min_reward_risk=2.0)
        self.assertEqual((default, default_annual), (explicit, explicit_annual))

    def test_disk_store_keeps_reward_risk_policy_across_expirations(self):
        from src.vertical_options_store import VerticalResultStore
        expiries = (date(2027, 1, 15), date(2028, 1, 21))
        rows = [self._row(expiry, "CALL", strike, last, bid=last, ask=last)
                for expiry, legs in zip(expiries,
                    (((100, 2), (103, 1)), ((100, 3), (105, 1))))
                for strike, last in legs]
        for minimum, years in ((2.0, {2027}), (1.5, {2027, 2028}), (3.0, set())):
            with self.subTest(minimum=minimum):
                store = VerticalResultStore("SPY", 100, None, None, "NORMAL",
                                            min_reward_risk=minimum)
                for expiry in expiries:
                    self.assertTrue(store.add_expiration(
                        expiry, [row for row in rows if row["expiry"] == expiry],
                        scan_vertical_candidates))
                self.assertEqual({row["year"] for row in store.iter_ranked()}, years)
                self.assertEqual({row["year"] for row in store.annual_rows}, years)
                self.assertEqual(store.count(), len(years))
        default = VerticalResultStore("SPY", 100, None, None, "NORMAL")
        self.assertEqual(default.min_reward_risk, 2.0)

    def test_cached_scan_does_not_mix_different_reward_risk_thresholds(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        rows = [self._row(expiry, "CALL", strike, last, bid=last, ask=last)
                for expiry, legs in (
                    (date(2027, 1, 15), ((100, 2), (103, 1))),
                    (date(2028, 1, 21), ((100, 3), (105, 1))),
                ) for strike, last in legs]
        raw = {"symbol": "SPY", "spot": 100, "rows": rows}
        with (patch.object(vo.st, "progress", return_value=MagicMock()),
              patch.object(vo, "scan_vertical_candidates", wraps=scan_vertical_candidates) as calculate):
            strict, _, _, _ = vo._rank_cached_vertical_chains(
                raw, None, None, "NORMAL", min_reward_risk=2.0)
            self.assertEqual(strict.count(), 1)
            permissive, _, _, _ = vo._rank_cached_vertical_chains(
                raw, None, None, "NORMAL", min_reward_risk=1.5)
            self.assertEqual(permissive.count(), 2)
            again, _, _, _ = vo._rank_cached_vertical_chains(
                raw, None, None, "NORMAL", min_reward_risk=2.0)
            self.assertIs(again, strict)
            self.assertIsNot(strict, permissive)
            self.assertEqual(strict.count(), 1)
            self.assertEqual(calculate.call_count, 4)  # Two expirations per policy.

    def test_no_target_breakeven_preserves_all_qualifying_pairs(self):
        expiries = (date(2027, 1, 15), date(2028, 1, 21))
        rows = [self._row(expiry, side, 100 + strike,
                          10 - .2 * strike if side == "CALL" else 2 + .2 * strike)
                for expiry in expiries for side in ("CALL", "PUT")
                for strike in range(11)]
        controls = dict(symbol="SPY", spot_last=105, option_rows=rows,
                        max_cost=None, max_per_expiration=None)
        no_target, annual = scan_vertical_candidates(**controls, target_breakeven=None)
        targeted, _ = scan_vertical_candidates(**controls, target_breakeven=105)
        self.assertEqual({row["id"] for row in no_target}, {row["id"] for row in targeted})
        self.assertEqual(len(annual), 2)
        self.assertEqual([row["cost"] for row in no_target],
                         sorted(row["cost"] for row in no_target))
        self.assertTrue(all(row["reward_risk"] >= 2 for row in no_target))

    def test_all_qualified_csv_includes_every_page_and_exact_filter_scope(self):
        import csv
        import io
        from src.vertical_options_ui import (
            _qualified_spreads_csv, _sort_qualified_spreads, _QUALIFIED_SORT_FIELDS,
        )
        expiry = date(2029, 1, 19)
        rows = (
            [self._row(expiry, "CALL", 100 + n, 30 - .3 * n) for n in range(22)]
            + [self._row(expiry, "PUT", 100 + n, 5 + .3 * n) for n in range(22)]
        )
        candidates, annual = scan_vertical_candidates(
            symbol="SPY", spot_last=103, option_rows=rows,
            target_breakeven=103, max_cost=None, max_per_expiration=None,
        )
        self.assertEqual(len(candidates), 462)  # 231 per side; exceeds 200-row screen page.
        self.assertIn("EXPIRATION", _QUALIFIED_SORT_FIELDS)
        ordered = _sort_qualified_spreads(candidates, "R:R", "DESC", 103)
        calls = [item for item in ordered if item["call_put"] == "CALL"]
        self.assertEqual(len(calls), 231)
        kwargs = dict(
            annual_ids=annual, source="E*TRADE", loaded_at=1800000000,
            target=103.0, max_cost=None,
        )
        full = list(csv.DictReader(io.StringIO(_qualified_spreads_csv(ordered, **kwargs))))
        subset = list(csv.DictReader(io.StringIO(_qualified_spreads_csv(calls, **kwargs))))
        self.assertEqual(len(full), 462)
        self.assertEqual(len(subset), 231)
        self.assertEqual(set(item["Type"] for item in full), {"CALL", "PUT"})
        self.assertTrue(all(item["Type"] == "CALL" for item in subset))
        self.assertEqual([item["Risk Reward (to 1)"] for item in full],
                         sorted([item["Risk Reward (to 1)"] for item in full],
                                key=float, reverse=True))
        self.assertEqual(full[0]["Quote Source"], "E*TRADE")
        self.assertEqual(full[0]["Max Cost Filter ($)"], "")
        self.assertEqual(full[0]["Orderable"], "NO")  # LAST* is informational only.
        self.assertEqual(full[0]["Fill Basis"], "LAST*")
        self.assertTrue(full[0]["OptionStrat URL"].startswith("https://optionstrat.com/"))
        self.assertEqual(sum(item["Annual Best"] == "YES" for item in full), len(annual))
        self.assertTrue(full[0]["Quote Snapshot"].startswith("2027-"))

    def test_qualified_sort_is_deterministic_and_does_not_mutate_scan_order(self):
        from src.vertical_options_ui import _sort_qualified_spreads
        expiry1, expiry2 = date(2027, 1, 15), date(2029, 1, 19)
        values = [
            dict(id="A", grade=2, cost=200, reward_risk=2.5,
                 breakeven=103, max_profit=500, expiry=expiry2,
                 buy_strike=100, sell_strike=105, buy_last=4, sell_last=2, width=5),
            dict(id="B", grade=0, cost=100, reward_risk=5.0,
                 breakeven=99, max_profit=900, expiry=expiry1,
                 buy_strike=90, sell_strike=105, buy_last=7, sell_last=6, width=15),
            dict(id="C", grade=1, cost=150, reward_risk=3.5,
                 breakeven=101, max_profit=600, expiry=expiry2,
                 buy_strike=95, sell_strike=110, buy_last=6, sell_last=4.5, width=15),
        ]
        original = [item["id"] for item in values]
        self.assertEqual(
            [item["id"] for item in _sort_qualified_spreads(values, "COST", "ASC", 100)],
            ["B", "C", "A"],
        )
        self.assertEqual(
            [item["id"] for item in _sort_qualified_spreads(values, "COST", "DESC", 100)],
            ["A", "C", "B"],
        )
        self.assertEqual(
            [item["id"] for item in _sort_qualified_spreads(values, "R:R", "DESC", 100)],
            ["B", "C", "A"],
        )
        self.assertEqual(
            [item["id"] for item in _sort_qualified_spreads(values, "FILL + TARGET", "ASC", 100)],
            ["B", "C", "A"],
        )
        self.assertEqual(
            [item["id"] for item in _sort_qualified_spreads(values, "TARGET DISTANCE", "ASC", 100)],
            ["B", "C", "A"],
        )
        self.assertEqual(
            [item["id"] for item in _sort_qualified_spreads(values, "EXPIRATION", "ASC", 100)],
            ["B", "A", "C"],
        )
        self.assertEqual([item["id"] for item in values], original)
        with self.assertRaises(ValueError):
            _sort_qualified_spreads(values, "UNSUPPORTED", "ASC", 100)
        with self.assertRaises(ValueError):
            _sort_qualified_spreads(values, "COST", "BACKWARD", 100)

    def test_exhaustive_pairing_returns_every_unique_call_and_put_vertical(self):
        # 8 distinct strikes -> nC2 = 28 legal combinations per side.
        # Reversing a leg pair would be a credit spread, not another debit.
        expiry = date(2029, 1, 19)
        rows = [
            self._row(expiry, "CALL", 100 + index, 30 - .3 * index)
            for index in range(8)
        ] + [
            self._row(expiry, "PUT", 100 + index, 5 + .3 * index)
            for index in range(8)
        ]
        limited, _ = scan_vertical_candidates(
            symbol="SPY", spot_last=103, option_rows=rows,
            target_breakeven=103, max_cost=None,
        )
        exhaustive, winners = scan_vertical_candidates(
            symbol="SPY", spot_last=103, option_rows=rows,
            target_breakeven=103, max_cost=None,
            max_per_expiration=None,
        )
        self.assertEqual(len(limited), 20)
        self.assertEqual(len(exhaustive), 56)
        self.assertEqual(
            {side: sum(row["call_put"] == side for row in exhaustive)
             for side in ("CALL", "PUT")},
            {"CALL": 28, "PUT": 28},
        )
        self.assertEqual(len({row["id"] for row in exhaustive}), 56)
        self.assertTrue(winners.issubset({row["id"] for row in exhaustive}))
        self.assertTrue(all(row["reward_risk"] >= 2 for row in exhaustive))
        self.assertTrue(all(
            row["buy_strike"] < row["sell_strike"] if row["call_put"] == "CALL"
            else row["buy_strike"] > row["sell_strike"] for row in exhaustive
        ))
        reversed_rows, reversed_winners = scan_vertical_candidates(
            symbol="SPY", spot_last=103, option_rows=reversed(rows),
            target_breakeven=103, max_cost=None, max_per_expiration=None,
        )
        self.assertEqual([row["id"] for row in exhaustive],
                         [row["id"] for row in reversed_rows])
        self.assertEqual(winners, reversed_winners)

    def test_exhaustive_pairing_respects_budget_and_exact_two_to_one_cutoff(self):
        expiry = date(2029, 1, 19)
        rows = [
            self._row(expiry, "CALL", 100, 3),
            self._row(expiry, "CALL", 103, 2),
            self._row(expiry, "CALL", 106, 1),
        ]
        candidates, _ = scan_vertical_candidates(
            symbol="SPY", spot_last=101, option_rows=rows,
            target_breakeven=101, max_cost=100, max_per_expiration=None,
        )
        self.assertEqual(len(candidates), 2)  # 100/103 and 103/106, not 100/106.
        self.assertTrue(all(row["reward_risk"] >= 2 for row in candidates))
        self.assertTrue(all(row["cost"] <= 100 for row in candidates))
        with self.assertRaisesRegex(ValueError, "positive integer"):
            scan_vertical_candidates(
                symbol="SPY", spot_last=101, option_rows=rows,
                target_breakeven=101, max_cost=None, max_per_expiration=0,
            )

    def test_scan_annual_winners_use_all_qualifying_pairs_before_cap(self):
        early = date(2026, 11, 20)
        late = date(2026, 12, 18)
        next_year = date(2027, 1, 15)
        rows = [
            self._row(early, "CALL", 100, 4),
            self._row(early, "CALL", 105, 3),
            self._row(early, "CALL", 110, 2),
            self._row(late, "CALL", 100, 3),
            self._row(late, "CALL", 110, 2),
            self._row(next_year, "CALL", 100, 3),
            self._row(next_year, "CALL", 105, 2),
        ]
        found, annual = scan_vertical_candidates(
            symbol="SPY", spot_last=101, option_rows=(row for row in rows),
            target_breakeven=102, max_cost=300, max_per_expiration=1,
        )
        self.assertEqual(len(found), 3)
        self.assertEqual(len({row["expiry"] for row in found}), 3)
        self.assertEqual(len(annual), 2)
        self.assertEqual({row["year"] for row in found if row["id"] in annual},
                         {2026, 2027})
        self.assertEqual(next(row for row in found if row["year"] == 2026
                              and row["id"] in annual)["expiry"], early)
        self.assertEqual(found[0]["expiry"], early)
        self.assertEqual(found[0]["breakeven"], 102)
        self.assertLess(found[0]["reward_risk"],
                        next(row for row in found if row["expiry"] == late)["reward_risk"])

    def test_scan_cost_breaks_equal_ratio_and_breakeven_ties(self):
        dear = date(2027, 5, 21)
        cheap = date(2027, 6, 18)
        rows = [
            self._row(dear, "CALL", 99, 3),
            self._row(dear, "CALL", 105, 1),
            self._row(cheap, "CALL", 100, 2),
            self._row(cheap, "CALL", 103, 1),
        ]
        found, annual = scan_vertical_candidates(
            symbol="SPY", spot_last=100, option_rows=rows,
            target_breakeven=101, max_cost=200,
        )
        self.assertEqual([row["cost"] for row in found], [100, 200])
        self.assertEqual([row["reward_risk"] for row in found], [2, 2])
        self.assertEqual([row["breakeven"] for row in found], [101, 101])
        self.assertEqual(annual, {found[0]["id"]})

    def test_scan_higher_ratio_breaks_equal_breakeven_and_cost_ties(self):
        low_ratio = date(2027, 7, 16)
        high_ratio = date(2027, 8, 20)
        rows = [
            self._row(low_ratio, "CALL", 100, 2),
            self._row(low_ratio, "CALL", 103, 1),
            self._row(high_ratio, "CALL", 100, 2),
            self._row(high_ratio, "CALL", 105, 1),
        ]
        found, annual = scan_vertical_candidates(
            symbol="SPY", spot_last=100, option_rows=rows,
            target_breakeven=101, max_cost=100,
        )
        self.assertEqual([row["reward_risk"] for row in found], [4, 2])
        self.assertEqual([row["breakeven"] for row in found], [101, 101])
        self.assertEqual([row["cost"] for row in found], [100, 100])
        self.assertEqual(annual, {found[0]["id"]})

    def test_scan_rejects_credit_and_debit_at_or_above_width(self):
        rows = [
            self._row(date(2027, 4, 16), "CALL", 100, 1),
            self._row(date(2027, 4, 16), "CALL", 105, 2),
            self._row(date(2027, 5, 21), "CALL", 100, 6),
            self._row(date(2027, 5, 21), "CALL", 105, 1),
            self._row(date(2027, 6, 18), "PUT", 100, 1),
            self._row(date(2027, 6, 18), "PUT", 105, 7),
        ]
        found, annual = scan_vertical_candidates(
            symbol="SPY", spot_last=100, option_rows=rows,
            target_breakeven=101, max_cost=1000,
        )
        self.assertEqual((found, annual), ([], set()))

    def test_scan_rejects_unusable_last_and_is_input_order_independent(self):
        expiry = date(2027, 3, 19)
        rows = [
            self._row(expiry, "CALL", 100, 3, osi="BUY-C"),
            self._row(expiry, "CALL", 105, 2, osi="SELL-C"),
            self._row(expiry, "PUT", 95, 2, osi="SELL-P"),
            self._row(expiry, "PUT", 100, 3, osi="BUY-P"),
            self._row(expiry, "CALL", 110, None),
            self._row(expiry, "CALL", 115, float("nan")),
            self._row(expiry, "CALL", 120, float("inf")),
            self._row(expiry, "CALL", 125, 0),
            self._row(expiry, "CALL", 130, -1),
        ]
        arguments = dict(symbol="SPY", spot_last=100, target_breakeven=100,
                         max_cost=100)
        first, first_annual = scan_vertical_candidates(option_rows=rows, **arguments)
        reverse, reverse_annual = scan_vertical_candidates(
            option_rows=reversed(rows), **arguments,
        )
        self.assertEqual([row["id"] for row in first],
                         [row["id"] for row in reverse])
        self.assertEqual(first_annual, reverse_annual)
        self.assertEqual(len(first), 2)

    def test_optional_cost_has_no_ceiling_but_preserves_two_to_one_ratio(self):
        from src.vertical_options import price_vertical_quote
        quote = dict(
            buy_last=5, sell_last=2, buy_bid=4.9, buy_ask=5,
            sell_bid=2, sell_ask=2.1, width=10,
        )
        self.assertIsNone(price_vertical_quote(**quote, max_cost=200))
        eligible = price_vertical_quote(**quote, max_cost=None)
        self.assertEqual(eligible["cost"], 300)
        self.assertEqual(eligible["basis"], "ASK")
        expiry = date(2029, 1, 19)
        rows = [self._row(expiry, "CALL", 100, 5, bid=4.9, ask=5),
                self._row(expiry, "CALL", 110, 2, bid=2, ask=2.1)]
        candidates, _ = scan_vertical_candidates(
            symbol="SPY", spot_last=105, option_rows=rows,
            target_breakeven=105, max_cost=None,
        )
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["cost"], 300)
        self.assertGreaterEqual(candidates[0]["reward_risk"], 2)

    def test_scan_validates_controls(self):
        arguments = dict(symbol="SPY", spot_last=100, option_rows=[],
                         target_breakeven=100, max_cost=100)
        for change in ({"spot_last": float("nan")},
                       {"target_breakeven": 0}, {"max_cost": float("inf")},
                       {"max_per_expiration": 0},
                       {"min_reward_risk": 0}, {"min_reward_risk": -1},
                       {"min_reward_risk": float("nan")},
                       {"min_reward_risk": float("inf")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                scan_vertical_candidates(**(arguments | change))

    def test_optionstrat_call_and_put_links_use_exact_leg_sides(self):
        from src.vertical_options_ui import _optionstrat_url

        base = {"symbol": "SPY", "expiry": date(2027, 1, 15), "buy_strike": 600}
        call = _optionstrat_url(base | {"call_put": "CALL", "sell_strike": 605})
        put = _optionstrat_url(base | {"call_put": "PUT", "sell_strike": 595})
        self.assertEqual(
            call,
            "https://optionstrat.com/build/bull-call-spread/SPY/"
            ".SPY270115C600,-.SPY270115C605",
        )
        self.assertEqual(
            put,
            "https://optionstrat.com/build/bear-put-spread/SPY/"
            "-.SPY270115P595,.SPY270115P600",
        )

    def test_tradingview_handoff_charts_exact_buy_minus_sell_legs(self):
        from src.vertical_options_ui import _tradingview_spread_url, _spread_table_row
        from urllib.parse import parse_qs, urlsplit

        soxx = {
            "symbol": "SOXX", "expiry": date(2029, 1, 19),
            "call_put": "CALL", "buy_strike": 395, "sell_strike": 890,
        }
        url = _tradingview_spread_url(soxx)
        self.assertEqual(urlsplit(url).scheme, "https")
        self.assertEqual(urlsplit(url).hostname, "www.tradingview.com")
        self.assertEqual(urlsplit(url).path, "/chart/")
        self.assertEqual(parse_qs(urlsplit(url).query)["symbol"],
                         ["OPRA:SOXX290119C395.0-OPRA:SOXX290119C890.0"])
        self.assertEqual(
            parse_qs(urlsplit(_tradingview_spread_url(soxx | {
                "call_put": "PUT", "buy_strike": 890, "sell_strike": 395,
            })).query)["symbol"],
            ["OPRA:SOXX290119P890.0-OPRA:SOXX290119P395.0"],
        )

        candidate = {
            **soxx, "id": "example", "buy_last": 6, "sell_last": 3,
            "cost": 300, "breakeven": 398, "reward_risk": 0.667,
            "max_profit": 200, "basis": "LAST",
        }
        rendered = _spread_table_row(candidate, set())
        self.assertEqual(rendered["tradingview_url"], url)
        self.assertIn("optionstrat.com", rendered["optionstrat_url"])
        self.assertEqual(len(rendered["values"]), 12)

    def test_tradingview_preserves_option_roots_fractional_strikes_and_identity(self):
        from src.vertical_options_ui import (
            _tradingview_spread_formula, _tradingview_spread_url,
        )
        base = {"symbol": "SPX", "expiry": date(2027, 1, 15), "call_put": "CALL",
                "buy_strike": 6000, "sell_strike": 6005,
                "buy_osi": "SPXW  270115C06000000", "sell_osi": "SPXW  270115C06005000"}
        self.assertEqual(
            _tradingview_spread_formula(base),
            "OPRA:SPXW270115C6000.0-OPRA:SPXW270115C6005.0",
        )
        fractional = {"symbol": "SPY", "expiry": date(2027, 1, 15), "call_put": "PUT",
                      "buy_strike": 600.5, "sell_strike": 595.125,
                      "buy_osi": "SPY270115P00600500", "sell_osi": ".SPY270115P595.125"}
        self.assertEqual(
            _tradingview_spread_formula(fractional),
            "OPRA:SPY270115P600.5-OPRA:SPY270115P595.125",
        )
        self.assertEqual(_tradingview_spread_url(base, exchange="NYSE"),
                         _tradingview_spread_url(base, exchange="UNKNOWN"))
        self.assertIn("C6000.125-", _tradingview_spread_formula(base | {
            "buy_strike": 6000.125, "buy_osi": ".SPXW270115C6000.125",
        }))
        for bad in ({"symbol": "SPY"}, base | {"buy_strike": float("nan")},
                    base | {"sell_strike": -1}, base | {"call_put": "OTHER"},
                    base | {"buy_osi": "SPXW270116C06000000"},
                    base | {"buy_osi": "SPXW270115P06000000"},
                    base | {"sell_osi": "SPXW270115C06010000"},
                    base | {"buy_osi": "bad-root"}):
            with self.subTest(bad=bad):
                self.assertEqual(_tradingview_spread_url(bad), "")

    def test_tradingview_existing_quote_listing_mapping(self):
        from src.vertical_options_ui import _tradingview_listing_exchange
        self.assertEqual(_tradingview_listing_exchange("SOXX"), "NASDAQ")
        self.assertEqual(_tradingview_listing_exchange("SPY"), "AMEX")
        self.assertEqual(_tradingview_listing_exchange("SPX"), "SP")
        for primary, expected in [("NYSE", "NYSE"), ("NASDAQ", "NASDAQ"),
                                  ("ARCA", "AMEX"), ("WRONG", "")]:
            quote = {"QuoteResponse": {"QuoteData": [{"All": {"primaryExchange": primary}}]}}
            self.assertEqual(_tradingview_listing_exchange("EXAMPLE", quote), expected)

    def test_tradingview_accepts_documented_etrade_dash_padding(self):
        from src.vertical_options_ui import _tradingview_spread_formula
        from src.option_book import extract_option_rows
        from src.vertical_options import scan_vertical_candidates
        expiry = date(2026, 10, 30)
        payload = {"OptionChainResponse": {"OptionPair": [{"Call": {
            "strikePrice": strike, "lastPrice": price,
            "osiKey": f"SOXX--261030C{strike * 1000:08d}",
        }} for strike, price in [(415, 100), (760, 5)]]}}
        rows = extract_option_rows(payload, expiry=expiry)
        candidates, _ = scan_vertical_candidates(symbol="SOXX", spot_last=511.06,
            option_rows=rows, target_breakeven=510, max_cost=None)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(_tradingview_spread_formula(candidates[0]),
                         "OPRA:SOXX261030C415.0-OPRA:SOXX261030C760.0")
        for root, padding in [("F", "-----"), ("GE", "----"), ("IBM", "---"), ("SPXW", "--")]:
            base = {"symbol": root, "expiry": expiry, "call_put": "PUT",
                    "buy_strike": 101.5, "sell_strike": 100,
                    "buy_osi": f"{root}{padding}261030P00101500",
                    "sell_osi": f"{root}{padding}261030P00100000"}
            self.assertEqual(_tradingview_spread_formula(base),
                             f"OPRA:{root}261030P101.5-OPRA:{root}261030P100.0")
            self.assertEqual(_tradingview_spread_formula(base | {"buy_strike": 102}), "")

    def test_tradingview_links_are_isolated_nontrading_actions(self):
        source = (Path(__file__).resolve().parents[1]
                  / "src" / "components" / "vo_spread_scroller_v1" / "index.html"
                  ).read_text(encoding="utf-8")
        ui = (Path(__file__).resolve().parents[1]
              / "src" / "vertical_options_ui.py").read_text(encoding="utf-8")
        self.assertIn('safeTradingViewUrl(row.tradingview_url)', source)
        self.assertIn('safeOptionStratUrl(row.optionstrat_url)', source)
        self.assertIn('url.hostname==="www.tradingview.com"', source)
        self.assertIn('url.pathname==="/chart/"', source)
        self.assertNotIn('/options/strategies/handbook/', source)
        self.assertIn('open.rel="noopener noreferrer"', source)
        self.assertIn('["SELECT","OS / TV","TICKER"', source)
        self.assertIn('st.columns(2, gap="small")', ui)
        self.assertIn('OPEN SPREAD CHART IN TRADINGVIEW', ui)
        self.assertIn('BUY premium minus SELL premium, per share.', ui)

    def test_scan_controls_accept_prices_and_reward_risk_ratio(self):
        from src.vertical_options_ui import _entered_price, _entered_reward_risk

        self.assertEqual(_entered_price("$1,234.50", "target breakeven"), 1234.5)
        self.assertEqual(_entered_price("$250", "maximum cost"), 250)
        for text, expected in (("2", 2.0), ("1:2", 2.0), ("1:1.5", 1.5), ("3", 3.0)):
            with self.subTest(text=text):
                self.assertEqual(_entered_reward_risk(text), expected)
        for value in ("", "0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _entered_price(value, "maximum cost")
            with self.subTest(ratio=value), self.assertRaises(ValueError):
                _entered_reward_risk(value)

    def test_optionstrat_uses_actual_weekly_option_root(self):
        from src.vertical_options_ui import _optionstrat_url

        url = _optionstrat_url({
            "symbol": "SPX", "expiry": date(2027, 1, 15), "call_put": "CALL",
            "buy_strike": 6000, "sell_strike": 6005,
            "buy_osi": "SPXW  270115C06000000",
            "sell_osi": "SPXW  270115C06005000",
        })
        self.assertEqual(
            url,
            "https://optionstrat.com/build/bull-call-spread/SPX/"
            ".SPXW270115C6000,-.SPXW270115C6005",
        )

    def test_vertical_dropdown_text_contrast(self):
        """Current Streamlit uses React Aria [data-tag], not BaseWeb chips."""
        source = (Path(__file__).resolve().parents[1]
                  / "src" / "vertical_options_ui.py").read_text(encoding="utf-8")
        self.assertIn(
            '.st-key-vertical_options_workspace [data-testid="stMultiSelect"] [data-tag]',
            source,
        )
        self.assertIn('[data-tag] [title]', source)
        self.assertIn('[data-testid="stMultiSelectDropdown"]', source)
        self.assertIn('background:#fb8b1e!important;', source)
        self.assertIn('-webkit-text-fill-color:#000000!important;', source)
        self.assertNotIn('[data-baseweb="tag"]', source)

    def test_call_debit_vertical_payload(self):
        payload = build_vertical_preview_payload(
            symbol="SPY",
            call_put="CALL",
            expiry=date(2026, 12, 18),
            buy_strike=700,
            sell_strike=705,
            quantity=3,
            limit_price=1.25,
            order_term="GOOD_FOR_DAY",
            client_order_id="VO123",
        )
        request = payload["PreviewOrderRequest"]
        self.assertEqual(request["orderType"], "SPREADS")
        order = request["Order"][0]
        self.assertEqual(order["priceType"], "NET_DEBIT")
        self.assertEqual(order["limitPrice"], 1.25)
        self.assertEqual(order["orderTerm"], "GOOD_FOR_DAY")
        instruments = order["Instrument"]
        self.assertEqual([leg["orderAction"] for leg in instruments], ["BUY_OPEN", "SELL_OPEN"])
        self.assertEqual([leg["Product"]["strikePrice"] for leg in instruments], [700.0, 705.0])
        self.assertTrue(all(leg["orderedQuantity"] == 3 for leg in instruments))

    def test_preview_limit_keeps_five_wide_spread_at_two_to_one_or_better(self):
        args = dict(symbol="SPY", call_put="CALL", expiry=date(2027, 1, 15),
                    buy_strike=100, sell_strike=105, quantity=1)
        accepted = build_vertical_preview_payload(**args, limit_price=1.66)
        self.assertEqual(accepted["PreviewOrderRequest"]["Order"][0]["limitPrice"], 1.66)
        precise = build_vertical_preview_payload(**args, limit_price=1.6666)
        self.assertEqual(precise["PreviewOrderRequest"]["Order"][0]["limitPrice"], 1.6666)
        with self.assertRaisesRegex(ValueError, "1:2"):
            build_vertical_preview_payload(**args, limit_price=1.66666)
        with self.assertRaisesRegex(ValueError, "1:2"):
            build_vertical_preview_payload(**args, limit_price=1.67)
        with self.assertRaises(ValueError):
            build_vertical_preview_payload(**args, limit_price=float("nan"))

    def test_preview_uses_user_reward_risk_floor_on_rounded_debit(self):
        args = dict(symbol="SPY", call_put="CALL", expiry=date(2027, 1, 15),
                    buy_strike=100, sell_strike=105, quantity=1)
        for minimum, edge in ((1.5, 2.0), (3.0, 1.25)):
            with self.subTest(minimum=minimum):
                payload = build_vertical_preview_payload(
                    **args, limit_price=edge, min_reward_risk=minimum)
                self.assertEqual(payload["PreviewOrderRequest"]["Order"][0]["limitPrice"], edge)
                with self.assertRaisesRegex(ValueError, f"1:{minimum:g}"):
                    build_vertical_preview_payload(
                        **args, limit_price=edge + 0.0001, min_reward_risk=minimum)
        rounded = build_vertical_preview_payload(
            **args, limit_price=2.00004, min_reward_risk=1.5)
        self.assertEqual(rounded["PreviewOrderRequest"]["Order"][0]["limitPrice"], 2.0)
        with self.assertRaisesRegex(ValueError, "1:1.5"):
            build_vertical_preview_payload(**args, limit_price=2.00006, min_reward_risk=1.5)
        for invalid in (0, -1, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_vertical_preview_payload(**args, limit_price=1, min_reward_risk=invalid)

    def test_put_debit_vertical_requires_buy_above_sell(self):
        validate_vertical(
            call_put="PUT",
            buy_strike=700,
            sell_strike=695,
            quantity=1,
        )
        with self.assertRaisesRegex(ValueError, "BUY strike above SELL strike"):
            validate_vertical(
                call_put="PUT",
                buy_strike=695,
                sell_strike=700,
                quantity=1,
            )

    def test_call_debit_vertical_requires_buy_below_sell(self):
        with self.assertRaisesRegex(ValueError, "BUY strike below SELL strike"):
            validate_vertical(
                call_put="CALL",
                buy_strike=705,
                sell_strike=700,
                quantity=1,
            )

    def test_place_payload_carries_preview_id_and_exact_order(self):
        preview = build_vertical_preview_payload(
            symbol="AAPL",
            call_put="CALL",
            expiry=date(2026, 11, 20),
            buy_strike=250,
            sell_strike=255,
            quantity=2,
            limit_price=.90,
            client_order_id="VOABC",
        )
        place = build_place_payload(preview, 987654)
        request = place["PlaceOrderRequest"]
        self.assertEqual(request["PreviewIds"], [{"previewId": 987654}])
        self.assertEqual(request["clientOrderId"], "VOABC")
        self.assertEqual(request["Order"], preview["PreviewOrderRequest"]["Order"])


if __name__ == "__main__":
    unittest.main()
