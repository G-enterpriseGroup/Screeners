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
        self.assertIn("natural ASK first when it meets your 2:1", ui)
        self.assertIn("Suggests a limit between spread MID and natural ASK", ui)
        self.assertIn("Does not guarantee a fill or place orders automatically.", ui)
        self.assertIn('pricing_mode = _pricing_mode_flip_switch()', ui)
        self.assertIn('st.toggle(', ui)
        self.assertIn('key="vo_smart_fill"', ui)

    def test_pricing_switch_keeps_native_control_visible_and_browser_frames_zero_height(self):
        ui = (Path(__file__).resolve().parents[1] / "src" / "vertical_options_ui.py").read_text()
        self.assertIn('.st-key-vertical_options_workspace [data-testid="stTextInput"] input,', ui)
        self.assertNotIn('.st-key-vertical_options_workspace input,', ui)
        self.assertIn('.st-key-vo_smart_fill label > div:first-of-type', ui)
        self.assertIn('label_visibility="collapsed"', ui)
        self.assertIn('mode_guide_col, mode_switch_col, _ = st.columns(', ui)
        self.assertIn('with mode_switch_col:', ui)
        self.assertIn('key="vo_pricing_preference_reader", height=0', ui)
        self.assertIn('key="vo_pricing_preference_writer", height=0', ui)

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
            del session["vo_smart_fill"]  # Widget is removed on a tab switch.
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
                       {"max_per_expiration": 0}):
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

    def test_three_scan_controls_accept_prices_and_reject_invalid_values(self):
        from src.vertical_options_ui import _entered_price

        self.assertEqual(_entered_price("$1,234.50", "target breakeven"), 1234.5)
        self.assertEqual(_entered_price("$250", "maximum cost"), 250)
        for value in ("", "0", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _entered_price(value, "maximum cost")

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
