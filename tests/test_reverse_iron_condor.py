"""Independent payoff, market-band, complete-join and four-leg payload checks."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
import math
import random
import unittest

from src.reverse_iron_condor import (
    build_place_payload,
    build_ric_preview_payload,
    new_ric_client_order_id,
    price_ric_quote,
    ric_limit_bounds,
    scan_ric_candidates,
    validate_ric,
)


EXPIRY = date(2029, 1, 19)


def contract(side, strike, bid, ask, *, expiry=EXPIRY, root="XYZ", **extras):
    return dict(expiry=expiry, call_put=side, strike=strike, bid=bid, ask=ask,
                last=(bid + ask) / 2, volume=30, open_interest=200,
                osi=f"{root}{expiry:%y%m%d}{side[0]}{round(strike * 1000):08d}", **extras)


def example(*, call_width=5, put_width=5, locked=False):
    return {
        "buy_call": contract("CALL", 105, 1.15 if locked else 1.05, 1.15),
        "sell_call": contract("CALL", 105 + call_width, .4, .4 if locked else .5),
        "buy_put": contract("PUT", 95, 1.15 if locked else 1.05, 1.15),
        "sell_put": contract("PUT", 95 - put_width, .4, .4 if locked else .5),
    }


def scan(rows, **kwargs):
    return scan_ric_candidates(symbol="XYZ", spot_last=100, option_rows=rows,
                               target_breakeven=kwargs.pop("target_breakeven", None),
                               max_cost=kwargs.pop("max_cost", None), **kwargs)


def identity(row):
    return tuple(row[key] for key in ("expiry", "buy_strike", "sell_strike",
                                      "put_buy_strike", "put_sell_strike"))


class ReverseIronCondorMathTests(unittest.TestCase):
    def test_payoff_matches_independent_four_option_cashflows(self):
        quote = price_ric_quote(**example(locked=True), spot_last=100)
        self.assertEqual(quote["debit"], 1.5)
        self.assertEqual(quote["max_loss"], 150)
        self.assertEqual(quote["downside_profit"], 350)
        self.assertEqual(quote["upside_profit"], 350)
        self.assertEqual((quote["lower_breakeven"], quote["upper_breakeven"]), (93.5, 106.5))
        for final in (0, 88, 90, 93.5, 95, 100, 105, 106.5, 110, 112, 1000):
            payoff = 100 * (max(final - 105, 0) - max(final - 110, 0)
                            + max(95 - final, 0) - max(90 - final, 0) - quote["debit"])
            self.assertGreaterEqual(payoff, -quote["max_loss"] - 1e-8)
            self.assertLessEqual(payoff, quote["max_profit"] + 1e-8)
            if final in (93.5, 106.5):
                self.assertAlmostEqual(payoff, 0)

    def test_risk_gate_uses_smaller_wing_and_total_debit(self):
        asymmetric = price_ric_quote(**example(call_width=30, put_width=5, locked=True))
        self.assertEqual(asymmetric["width"], 5)
        self.assertEqual(asymmetric["upside_profit"], 2850)
        self.assertEqual(asymmetric["downside_profit"], 350)
        self.assertAlmostEqual(asymmetric["reward_risk"], 350 / 150)
        self.assertIsNone(price_ric_quote(**example(call_width=30, put_width=4, locked=True)))

    def test_exact_ratio_boundary_higher_floor_and_fee_rejection(self):
        legs = example(call_width=3, put_width=3, locked=True)
        for key in ("buy_call", "buy_put"):
            legs[key]["bid"] = legs[key]["ask"] = .9
        quote = price_ric_quote(**legs, min_reward_risk=.5)
        self.assertEqual(quote["debit"], 1)
        self.assertEqual(quote["reward_risk"], 2)
        self.assertIsNone(price_ric_quote(**legs, min_reward_risk=2.01))
        self.assertIsNone(price_ric_quote(**legs, fee_per_spread=.01))
        with self.assertRaises(ValueError):
            price_ric_quote(**legs, min_reward_risk=float("nan"))

    def test_fees_change_risk_breakevens_and_both_rewards(self):
        base = price_ric_quote(**example(locked=True))
        quote = price_ric_quote(**example(locked=True), fee_per_spread=2.6)
        self.assertEqual(quote["debit"], base["debit"])
        self.assertEqual(quote["gross_cost"], 150)
        self.assertEqual(quote["cost"], 152.6)
        self.assertEqual(quote["max_loss"], 152.6)
        self.assertEqual(quote["max_profit"], 347.4)
        self.assertEqual(quote["lower_breakeven"], 93.474)
        self.assertEqual(quote["upper_breakeven"], 106.526)
        self.assertIsNone(price_ric_quote(**example(locked=True), max_cost=152.59, fee_per_spread=2.6))
        self.assertIsNotNone(price_ric_quote(**example(locked=True), max_cost=152.6, fee_per_spread=2.6))
        for fee in (-1, float("nan"), float("inf"), "oops"):
            with self.subTest(fee=fee), self.assertRaises(ValueError):
                price_ric_quote(**example(), fee_per_spread=fee)

    def test_normal_and_smart_price_joint_market_on_whole_cents(self):
        normal = price_ric_quote(**example())
        smart = price_ric_quote(**example(), pricing_mode="SMART FILL")
        self.assertEqual((normal["spread_bid"], normal["spread_mid"], normal["spread_ask"]), (1.1, 1.3, 1.5))
        self.assertEqual(normal["debit"], 1.37)
        self.assertEqual(smart["debit"], 1.46)
        self.assertEqual(normal["basis"], "LIMIT")
        self.assertEqual(smart["basis"], "SMART")
        self.assertGreaterEqual(smart["debit"], normal["debit"])
        for quote in (normal, smart):
            self.assertEqual(quote["debit"] * 100, round(quote["debit"] * 100))
            self.assertGreaterEqual(quote["reward_risk"], 2)

    def test_bounds_satisfy_independent_decimal_market_risk_budget_constraints(self):
        rng = random.Random(917)
        for mode in ("NORMAL", "SMART FILL"):
            for _ in range(200):
                mid, ask = rng.uniform(.01, 5), rng.uniform(5, 7)
                width, ratio, fee = rng.uniform(10, 35), rng.uniform(2, 5), rng.uniform(0, 4)
                budget = rng.uniform(200, 600)
                bounds = ric_limit_bounds(width=width, max_cost=budget,
                                          spread_mid=mid, spread_ask=ask,
                                          pricing_mode=mode, min_reward_risk=ratio,
                                          fee_per_spread=fee)
                if bounds is None:
                    continue
                lower, upper = map(lambda value: Decimal(str(value)), bounds)
                self.assertGreaterEqual(lower, Decimal(str(mid)))
                self.assertLessEqual(upper, Decimal(str(ask)))
                self.assertLessEqual(upper * 100 + Decimal(str(fee)), Decimal(str(budget)))
                risk = upper * 100 + Decimal(str(fee))
                self.assertGreaterEqual(Decimal(str(width)) * 100 - risk, Decimal(str(ratio)) * risk)
                if mode == "SMART FILL":
                    self.assertGreaterEqual(lower, Decimal(".97") * Decimal(str(ask)))
        self.assertIsNone(ric_limit_bounds(width=9, max_cost=None, spread_mid=1.005,
                                           spread_ask=1.009))
        self.assertEqual(ric_limit_bounds(width=3, max_cost=100, spread_mid=1,
                                          spread_ask=1), (1, 1))
        self.assertIsNone(ric_limit_bounds(width=3, max_cost=100, spread_mid=1,
                                           spread_ask=1, fee_per_spread=.01))

    def test_missing_crossed_nonfinite_zero_and_nonstandard_quotes_rejected(self):
        for key in example():
            for field, value in (("bid", None), ("ask", float("nan")), ("ask", 0),
                                 ("bid", 999), ("multiplier", 10), ("adjusted", True)):
                with self.subTest(leg=key, field=field, value=value):
                    legs = example()
                    legs[key][field] = value
                    self.assertIsNone(price_ric_quote(**legs))
        legs = example()
        for row in legs.values():
            row["last"] = None
        self.assertIsNotNone(price_ric_quote(**legs))
        for row in legs.values():
            row["bid"] = row["ask"] = None
            row["last"] = 1
        self.assertIsNone(price_ric_quote(**legs))

    def test_supplied_expiry_side_strike_root_and_underlying_must_match(self):
        mutations = (
            ("expiry", EXPIRY + timedelta(days=1)), ("call_put", "PUT"),
            ("osi", "XYZ290119C00106000"), ("osi", "ABC290119C00105000"),
            ("osi", "XYZ1290119C00105000"), ("osi", ""),
            ("underlying", "OTHER"),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                legs = example()
                for row in legs.values():
                    row["underlying"] = "XYZ"
                legs["buy_call"][field] = value
                self.assertIsNone(price_ric_quote(**legs))

    def test_greeks_are_signed_four_leg_sums_and_unknown_is_not_invented(self):
        legs = example()
        for row, delta, theta in zip(legs.values(), (.3, .1, -.3, -.1), (-.1, -.04, -.12, -.05)):
            row["greeks"] = {"delta": delta, "theta": theta, "gamma": .02, "vega": .1}
        quote = price_ric_quote(**legs)
        self.assertAlmostEqual(quote["net_delta"], 0)
        self.assertAlmostEqual(quote["net_theta"], -.13)
        self.assertAlmostEqual(quote["net_vega"], 0)
        legs["sell_put"]["greeks"] = {}
        self.assertIsNone(price_ric_quote(**legs)["net_delta"])
        self.assertEqual(quote["liquidity_signal"], "ACTIVE LEGS")
        legs["sell_put"]["open_interest"] = None
        self.assertIsNone(price_ric_quote(**legs)["min_open_interest"])

    def test_structure_and_quantity_validation(self):
        args = dict(buy_strike=105, sell_strike=110, put_buy_strike=95,
                    put_sell_strike=90, quantity=1, spot_last=100)
        validate_ric(**args)
        for key, value in (("buy_strike", 95), ("sell_strike", 104),
                           ("put_sell_strike", 96), ("buy_strike", float("inf")),
                           ("quantity", 1.5), ("quantity", True), ("quantity", 0),
                           ("spot_last", 105), ("spot_last", float("nan"))):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_ric(**{**args, key: value})


class ReverseIronCondorScannerTests(unittest.TestCase):
    def test_small_chain_join_matches_unpruned_four_strike_reference(self):
        rng = random.Random(195)
        for mode in ("NORMAL", "SMART FILL"):
            for fee in (0, 2.6):
                calls, puts = [], []
                for side, output in (("CALL", calls), ("PUT", puts)):
                    for distance in (2, 5, 9, 14, 20, 27, 35):
                        premium = 5 * math.exp(-distance / 13)
                        gap = rng.uniform(.02, .15)
                        output.append(contract(side, 100 + distance if side == "CALL" else 100 - distance,
                                               max(.001, premium - gap), premium + gap))
                expected = {}
                for cb, cs in combinations(calls, 2):
                    for pb, ps in combinations(puts, 2):
                        # puts above are ordered from the inner strike outward.
                        quote = price_ric_quote(buy_call=cb, sell_call=cs, buy_put=pb, sell_put=ps,
                                                max_cost=200, pricing_mode=mode, fee_per_spread=fee)
                        if quote:
                            expected[(EXPIRY, cb["strike"], cs["strike"], pb["strike"], ps["strike"])] = quote
                actual, annual = scan(calls + puts, max_per_expiration=None, max_cost=200,
                                      pricing_mode=mode, fee_per_spread=fee)
                self.assertTrue(expected)
                self.assertEqual(len(actual), len(expected))
                self.assertEqual({identity(row) for row in actual}, set(expected))
                for row in actual:
                    self.assertEqual(row["debit"], expected[identity(row)]["debit"])
                self.assertEqual(annual, {actual[0]["id"]})

    def test_complete_sink_retains_more_than_the_visible_limit(self):
        rows = []
        for side in ("CALL", "PUT"):
            for step in range(1, 17):
                strike = 100 + 5 * step if side == "CALL" else 100 - 5 * step
                premium = .1 * (17 - step)
                rows.append(contract(side, strike, premium, premium))
        complete, annual = scan(rows, max_per_expiration=None)
        self.assertEqual(len(complete), math.comb(16, 2) ** 2)
        streamed = []
        retained, streamed_annual = scan(rows, max_per_expiration=None, candidate_sink=streamed.append)
        self.assertEqual(retained, [])
        self.assertEqual({row["id"] for row in streamed}, {row["id"] for row in complete})
        self.assertEqual(streamed_annual, annual)
        limited, limited_annual = scan(rows, max_per_expiration=7)
        self.assertEqual(limited, complete[:7])
        self.assertEqual(limited_annual, annual)

    def test_duplicate_conflicts_rejected_but_identical_rows_do_not_duplicate(self):
        rows = list(example().values())
        baseline, _ = scan(rows)
        repeated, _ = scan(rows + deepcopy(rows))
        self.assertEqual(repeated, baseline)
        conflicting = deepcopy(rows[0])
        conflicting["ask"] += .1
        self.assertEqual(scan(rows + [conflicting])[0], [])
        self.assertEqual(scan([conflicting] + rows)[0], [])

    def test_separate_option_roots_are_never_joined_and_padded_weeklies_work(self):
        rows = list(example().values())
        mixed = deepcopy(rows)
        for row in mixed[2:]:
            row["osi"] = row["osi"].replace("XYZ", "XYZW")
        self.assertEqual(scan(mixed)[0], [])
        for row in rows:
            row["osi"] = row["osi"].replace("XYZ", "XYZW---")
        result, _ = scan(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["option_root"], "XYZW")

    def test_scanner_rejects_missing_invalid_adjusted_and_mismatched_contract_evidence(self):
        for field, value in (("osi", ""), ("osi", "XYZ290119P00105000"),
                             ("osi", "XYZ1290119C00105000"), ("multiplier", 10),
                             ("multiplier", None), ("adjusted", True),
                             ("underlying", "OTHER")):
            rows = list(example().values())
            rows[0][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(scan(rows)[0], [])
        # Standard unadjusted OCC identities remain usable when the existing
        # production normalizer does not supply an explicit multiplier field.
        self.assertEqual(len(scan(list(example().values()))[0]), 1)

    def test_otm_and_same_expiry_are_mandatory(self):
        rows = list(example().values())
        rows[0] = contract("CALL", 99, 1.05, 1.15)
        self.assertEqual(scan(rows)[0], [])
        rows = list(example().values())
        rows[0] = contract("CALL", 105, 1.05, 1.15, expiry=EXPIRY + timedelta(days=7))
        self.assertEqual(scan(rows)[0], [])

    def test_target_can_match_lower_breakeven_and_annual_winners_are_complete(self):
        rows = list(example().values())
        rows.append(contract("PUT", 85, .1, .2))
        next_year = [dict(row, expiry=date(2030, 1, 18),
                          osi=row["osi"].replace("290119", "300118")) for row in rows]
        result, annual = scan(rows + next_year, target_breakeven=93.63, max_per_expiration=None)
        self.assertEqual(len(annual), 2)
        for year in (2029, 2030):
            yearly = [row for row in result if row["year"] == year]
            winner = min(yearly, key=lambda row: (min(abs(row["lower_breakeven"] - 93.63),
                                                        abs(row["upper_breakeven"] - 93.63)),
                                                   row["cost"], -row["reward_risk"],
                                                   row["expiry"], row["buy_strike"],
                                                   row["sell_strike"], row["put_buy_strike"],
                                                   row["put_sell_strike"], row["option_root"]))
            self.assertIn(winner["id"], annual)

    def test_input_validation_and_progress(self):
        for kwargs in ({"max_cost": 0}, {"target_breakeven": -1}, {"max_per_expiration": 0},
                       {"max_per_expiration": True}, {"pricing_mode": "FAST"},
                       {"candidate_sink": lambda row: None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                scan(list(example().values()), **kwargs)
        progress = []
        scan(list(example().values()), progress=progress.append)
        self.assertEqual(progress, [1])

    def test_joint_headroom_prunes_large_ineligible_cartesian_universe(self):
        rows = []
        for side in ("CALL", "PUT"):
            for step in range(1, 121):
                strike = 1000 + step if side == "CALL" else 1000 - step
                premium = round(.2 * (121 - step), 8)
                rows.append(contract(side, strike, premium, premium))
        stats = {}
        result, annual = scan_ric_candidates(
            symbol="XYZ", spot_last=1000, option_rows=rows,
            target_breakeven=None, max_cost=None, max_per_expiration=None,
            scan_stats=stats,
        )
        self.assertEqual((result, annual), ([], set()))
        self.assertEqual(stats["possible_condors"], math.comb(120, 2) ** 2)
        self.assertEqual(stats["possible_condors"], 50_979_600)
        self.assertEqual(stats["priced_condors"], 0)
        self.assertEqual(stats["pruned_combinations"], stats["possible_condors"])
        self.assertEqual(stats["qualified_condors"], 0)


class ReverseIronCondorPayloadTests(unittest.TestCase):
    def payload(self, **overrides):
        kwargs = dict(symbol="XYZ", expiry=EXPIRY, buy_strike=105, sell_strike=110,
                      put_buy_strike=95, put_sell_strike=90, quantity=2, limit_price=1.5,
                      client_order_id="RICREVIEW123")
        return build_ric_preview_payload(**{**kwargs, **overrides})

    def test_one_atomic_four_leg_payload_preserves_all_actions_and_quantities(self):
        request = self.payload()["PreviewOrderRequest"]
        self.assertEqual(request["orderType"], "SPREADS")
        self.assertEqual(len(request["Order"]), 1)
        order = request["Order"][0]
        self.assertEqual(order["priceType"], "NET_DEBIT")
        self.assertEqual(order["limitPrice"], 1.5)
        legs = order["Instrument"]
        self.assertEqual([(row["Product"]["callPut"], row["orderAction"], row["Product"]["strikePrice"])
                          for row in legs], [("PUT", "SELL_OPEN", 90), ("PUT", "BUY_OPEN", 95),
                                             ("CALL", "BUY_OPEN", 105), ("CALL", "SELL_OPEN", 110)])
        self.assertTrue(all(row["quantity"] == row["orderedQuantity"] == 2 for row in legs))
        self.assertTrue(all(row["Product"]["expiryYear"] == 2029 for row in legs))
        placed = build_place_payload({"PreviewOrderRequest": request}, 987654)
        self.assertEqual(placed["PlaceOrderRequest"]["Order"], request["Order"])
        self.assertEqual(placed["PlaceOrderRequest"]["PreviewIds"], [{"previewId": 987654}])

    def test_payload_rechecks_strict_floor_fee_cents_and_structure(self):
        for kwargs in ({"put_sell_strike": 94}, {"limit_price": 1.67}, {"limit_price": 1.501},
                       {"limit_price": float("inf")}, {"quantity": 1.5}, {"quantity": True},
                       {"order_term": "MARKET"}, {"client_order_id": "bad-id"},
                       {"client_order_id": "x" * 21}, {"fee_per_spread": 20},
                       {"min_reward_risk": 3}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.payload(**kwargs)
        self.payload(min_reward_risk=.5)
        self.payload(fee_per_spread=2.6)

    def test_payload_uses_exact_standard_weekly_root_and_rejects_mismatch(self):
        legs = example()
        kwargs = {prefix + "_osi": legs[key]["osi"].replace("XYZ", "XYZW---")
                  for prefix, key in (("buy", "buy_call"), ("sell", "sell_call"),
                                      ("put_buy", "buy_put"), ("put_sell", "sell_put"))}
        payload = self.payload(**kwargs)
        self.assertTrue(all(row["Product"]["symbol"] == "XYZW"
                            for row in payload["PreviewOrderRequest"]["Order"][0]["Instrument"]))
        with self.assertRaises(ValueError):
            self.payload(**{**kwargs, "put_sell_osi": "ABC290119P00090000"})
        with self.assertRaises(ValueError):
            self.payload(**{**kwargs, "buy_osi": "XYZW290119C00106000"})

    def test_order_ids_are_unique_alphanumeric_and_bounded(self):
        ids = {new_ric_client_order_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        self.assertTrue(all(value.startswith("RIC") and value.isalnum() and len(value) <= 20 for value in ids))


if __name__ == "__main__":
    unittest.main()
