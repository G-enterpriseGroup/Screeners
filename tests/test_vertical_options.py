"""Focused regression tests for the Vertical Options payload builder."""

from datetime import date
import unittest

from src.vertical_options import (
    build_place_payload,
    build_vertical_preview_payload,
    validate_vertical,
)


class VerticalOptionsTests(unittest.TestCase):
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
