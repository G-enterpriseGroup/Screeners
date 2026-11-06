"""Desktop-only execution contracts; synthetic quotes, no broker connections."""
from datetime import date
import unittest

from src.vertical_options import price_vertical_quote as legacy_price
from src.vertical_options_execution import price_vertical_quote, quote_ruler, refreshed_ticket


class DesktopExecutionTests(unittest.TestCase):
    def quote(self, **changes):
        values = dict(buy_last=3.7, sell_last=1.2,
                      buy_bid=3.5, buy_ask=4.0, sell_bid=1.0, sell_ask=1.5,
                      width=10, max_cost=None, min_reward_risk=2)
        values.update(changes)
        return values

    def test_smart_is_actual_natural_ask_and_normal_is_between_mid_and_ask(self):
        smart = price_vertical_quote(**self.quote(), pricing_mode="SMART FILL")
        normal = price_vertical_quote(**self.quote(), pricing_mode="NORMAL")
        self.assertEqual((smart["basis"], smart["debit"], smart["cost"]), ("ASK", 3.0, 300))
        self.assertGreaterEqual(normal["debit"], normal["spread_mid"])
        self.assertLess(normal["debit"], normal["spread_ask"])
        self.assertEqual(normal["basis"], "LIMIT")

    def test_ask_ineligible_never_falls_back_to_cheaper_price(self):
        self.assertIsNone(price_vertical_quote(**self.quote(width=8), pricing_mode="SMART FILL"))
        normal = price_vertical_quote(**self.quote(width=8), pricing_mode="NORMAL")
        self.assertIsNotNone(normal)
        self.assertGreaterEqual((8-normal["debit"])/normal["debit"], 2)
        self.assertIsNone(price_vertical_quote(**self.quote(max_cost=299), pricing_mode="SMART FILL"))

    def test_shared_phone_policy_is_not_reversed(self):
        legacy = legacy_price(**self.quote(), pricing_mode="SMART FILL")
        self.assertEqual(legacy["basis"], "SMART")
        self.assertLess(legacy["debit"], legacy["spread_ask"])
        self.assertEqual(legacy_price(**self.quote(), pricing_mode="NORMAL")["basis"], "ASK")

    def test_missing_crossed_and_below_two_to_one_quotes_never_smart_fill(self):
        for changes in ({"buy_bid": None}, {"sell_ask": None}, {"buy_bid": 4.1},
                        {"width": 5, "min_reward_risk": 1}):
            with self.subTest(changes=changes):
                self.assertIsNone(price_vertical_quote(**self.quote(**changes), pricing_mode="SMART FILL"))

    def test_screenshot_wide_market_is_rejected_at_ask(self):
        q = self.quote(buy_bid=101.84, buy_ask=105.5, sell_bid=101.26,
                       sell_ask=105.0, width=1, buy_last=102, sell_last=101.67)
        self.assertIsNone(price_vertical_quote(**q, pricing_mode="SMART FILL"))
        chart = quote_ruler({**q, "debit": .33, "orderable": False})
        self.assertAlmostEqual(chart["bid"], -3.16)
        self.assertAlmostEqual(chart["mid"], .54)
        self.assertAlmostEqual(chart["ask"], 4.24)
        self.assertAlmostEqual(chart["position"], 100*(.33+3.16)/(4.24+3.16))
        self.assertTrue(chart["quoted"])
        self.assertNotIn("estimate", chart)

    def test_quote_numbers_do_not_depend_on_orderability_or_probability(self):
        chart = quote_ruler({"spread_bid": 1, "spread_mid": 999, "spread_ask": 3,
                             "debit": 2.5, "orderable": False})
        self.assertEqual((chart["bid"], chart["mid"], chart["ask"], chart["position"]), (1, 2, 3, 75))
        self.assertFalse(chart["orderable"])
        for bad in (None, float("nan"), float("inf"), True):
            empty = quote_ruler({"spread_bid": bad, "spread_ask": 3, "debit": 1})
            self.assertFalse(empty["quoted"])
            self.assertIsNone(empty["position"])
        locked = quote_ruler({"spread_bid": 2, "spread_ask": 2, "debit": 2})
        self.assertEqual(locked["position"], 50)

    def test_fresh_ticket_updates_all_payoff_numbers_without_mutation(self):
        candidate = {"id": "synthetic", "expiry": date(2028,1,21), "width": 10,
                     "buy_strike": 100, "call_put": "CALL", "debit": 1,
                     "cost": 100, "breakeven": 101}
        before = dict(candidate)
        price = price_vertical_quote(**self.quote(), pricing_mode="SMART FILL")
        ticket = refreshed_ticket(candidate, price)
        self.assertEqual(candidate, before)
        self.assertEqual((ticket["debit"], ticket["cost"], ticket["max_loss"],
                          ticket["max_profit"], ticket["breakeven"]), (3,300,300,700,103))
        self.assertAlmostEqual(ticket["reward_risk"], 7/3)


if __name__ == "__main__":
    unittest.main()
