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


class DesktopWorkflowSafetyTests(unittest.TestCase):
    """Exercise the real renderer with inert broker/widget doubles."""
    def setUp(self):
        import time
        from copy import deepcopy
        from unittest.mock import MagicMock
        from tests.test_verti_phone import quoted_spread, ACCOUNTS
        self.candidate, self.rows = quoted_spread()
        self.result = {"source":"E*TRADE", "loaded_at":time.time()-900,
                       "max_cost":200, "pricing_mode":"SMART FILL", "scope":"test"}
        self.state = {"etrade_accounts":deepcopy(ACCOUNTS),"vo_accounts":["preferred"]}
        self.client = MagicMock(is_offline=False)
        self.client.preview_order.return_value = {"PreviewOrderResponse":{"PreviewIds":[{"previewId":123}]}}
        self.client.place_order.return_value = {"PlaceOrderResponse":{"OrderIds":[{"orderId":456}]}}

    def render(self, clicked=(), rows=None):
        from unittest.mock import MagicMock, patch
        from tests.test_verti_phone import order_ui
        import src.vertical_options_ui as vo
        ui=order_ui(self.state,clicked)
        with patch.object(vo,"st",ui), patch.object(vo,"_chain",return_value=self.rows if rows is None else rows):
            vo._render_selected_order(self.client,MagicMock(),self.candidate,self.result,True)
        return ui

    def test_preview_fresh_price_and_one_explicit_send_without_checkbox(self):
        self.render()
        self.client.preview_order.assert_not_called()
        self.client.place_order.assert_not_called()
        self.rows[0]["ask"] = 3.2  # Selected ASK moves from 1.20 to 1.30.
        self.render({"vo_preview"})
        self.client.preview_order.assert_called_once()
        order=self.client.preview_order.call_args.args[1]["PreviewOrderRequest"]["Order"][0]
        self.assertEqual(order["limitPrice"],1.3)
        self.assertEqual(self.state["_vo_ticket_quote"]["price"]["breakeven"],101.3)
        self.client.place_order.assert_not_called()
        ui=self.render({"vo_send"})
        ui.checkbox.assert_not_called()
        self.client.place_order.assert_called_once()
        self.assertEqual(self.client.place_order.call_args.args[1]["PlaceOrderRequest"]["Order"],[order])
        self.assertNotIn("vo_review_bundle",self.state)
        self.render({"vo_send"})
        self.render({"vo_preview"})
        self.assertEqual(self.client.place_order.call_count,1)
        self.assertEqual(self.client.preview_order.call_count,1)

    def test_uncertain_submission_cannot_be_repeated_with_another_preview(self):
        self.render({"vo_preview"})
        self.client.place_order.side_effect=TimeoutError("Broker response lost")
        self.render({"vo_send"})
        self.render({"vo_preview","vo_send"})
        self.assertEqual(self.client.place_order.call_count,1)
        self.assertEqual(self.client.preview_order.call_count,1)
        self.assertNotIn("vo_review_bundle",self.state)

    def test_price_risk_and_contract_changes_fail_closed(self):
        from copy import deepcopy
        for change in ("missing", "crossed", "risk"):
            with self.subTest(change=change):
                rows=deepcopy(self.rows)
                if change=="missing": rows[0]["osi"]="DIFFERENT"
                elif change=="crossed": rows[0]["bid"]=10
                else: rows[0]["ask"]=8
                ui=self.render({"vo_preview"},rows)
                self.assertTrue(ui.error.called)
        self.client.preview_order.assert_not_called()
        self.client.place_order.assert_not_called()

    def test_all_accounts_must_pass_and_expired_preview_cannot_send(self):
        import time
        self.state["vo_accounts"]=["preferred","other"]
        self.client.preview_order.side_effect=[self.client.preview_order.return_value,ValueError("rejected")]
        self.render({"vo_preview"})
        self.render({"vo_send"})
        self.client.place_order.assert_not_called()
        self.assertNotIn("vo_review_bundle",self.state)
        self.client.preview_order.side_effect=None
        self.state["vo_accounts"]=["preferred"]
        self.render({"vo_preview"})
        self.state["vo_review_bundle"]["previewed_at"]=time.time()-31
        self.render({"vo_send"})
        self.client.place_order.assert_not_called()

    def test_selection_event_invalidates_review_and_stale_ack_does_not(self):
        from unittest.mock import MagicMock, patch
        import src.vertical_options_ui as vo
        store=MagicMock();store.get_many.return_value={"new":{}}
        state={"_vo_checked_spreads":{"scope":"test","ids":{"old"},"revision":2},
               "_vo_column_filters":{"scope":"test","filters":{},"revision":3},
               "vo_review_bundle":{"approved":True},"vo_live_confirm":True,
               "_vo_ticket_quote":{"old":True},"vo_results_view":"BEST PER YEAR"}
        with patch.object(vo.st,"session_state",state):
            result={"scope":"test","candidates":store}
            self.assertFalse(vo._consume_vertical_table_update(
                {"scope":"test","revision":1,"selected_ids":["new"]},result,"SPY"))
            self.assertIn("vo_review_bundle",state)
            self.assertTrue(vo._consume_vertical_table_update(
                {"scope":"test","revision":3,"selected_ids":["new"]},result,"SPY"))
            self.assertNotIn("vo_review_bundle",state)
            self.assertNotIn("_vo_ticket_quote",state)

    def test_late_preference_cannot_override_explicit_choice_or_write_default(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        state={}
        with (patch.object(vo.st,"session_state",state),
              patch.object(vo.st,"toggle",side_effect=lambda *a,**k:state["vo_smart_fill"]),
              patch.object(vo,"_PRICING_PREFERENCE_COMPONENT",return_value=None) as component):
            self.assertEqual(vo._pricing_mode_flip_switch(),"NORMAL")
            self.assertEqual(component.call_count,1)
            self.assertEqual(component.call_args.kwargs["action"],"read")
            state["vo_smart_fill"]=True
            vo._remember_pricing_mode()
            component.side_effect=[{"ready":True,"mode":"NORMAL","available":True},None]
            self.assertEqual(vo._pricing_mode_flip_switch(),"SMART FILL")
            self.assertEqual(component.call_args.kwargs["value"],"SMART FILL")


if __name__ == "__main__":
    unittest.main()
