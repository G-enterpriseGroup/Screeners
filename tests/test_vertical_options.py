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
    @staticmethod
    def _row(expiry, side, strike, last, *, bid=None, ask=None, osi=""):
        return {
            "expiry": expiry, "call_put": side, "strike": strike,
            "last": last, "bid": bid, "ask": ask,
            "osi": osi or f"{side}-{expiry}-{strike}",
        }

    def test_scan_both_canonical_sides_with_last_not_bid_ask(self):
        expiry = date(2027, 1, 15)
        rows = [
            self._row(expiry, "CALL", 100, 3, bid=.10, ask=10),
            self._row(expiry, "CALL", 105, 2, bid=9, ask=12),
            self._row(expiry, "PUT", 95, 2, bid=.01, ask=11),
            self._row(expiry, "PUT", 100, 3, bid=8, ask=13),
        ]
        found, annual = scan_vertical_candidates(
            symbol="spy", spot_last=102.50, option_rows=rows,
            target_breakeven=101, max_cost=100,
        )
        self.assertEqual(len(found), 2)
        call, put = found
        self.assertEqual((call["call_put"], call["buy_strike"], call["sell_strike"]),
                         ("CALL", 100, 105))
        self.assertEqual((put["call_put"], put["buy_strike"], put["sell_strike"]),
                         ("PUT", 100, 95))
        self.assertEqual((call["debit"], call["cost"], call["max_profit"],
                          call["max_loss"], call["breakeven"], call["reward_risk"]),
                         (1, 100, 400, 100, 101, 4))
        self.assertEqual(put["breakeven"], 99)
        self.assertEqual((call["buy_last"], call["buy_bid"], call["buy_ask"]),
                         (3, .10, 10))
        self.assertEqual((call["sell_last"], call["sell_bid"], call["sell_ask"]),
                         (2, 9, 12))
        self.assertEqual(annual, {call["id"]})

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
