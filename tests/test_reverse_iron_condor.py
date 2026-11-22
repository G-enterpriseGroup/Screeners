"""Independent payoff, market-band, complete-join and four-leg payload checks."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations, product
import math
import random
import gc
import weakref
import unittest
from unittest.mock import patch

import numpy as np

from src import reverse_iron_condor as ric
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


def reference_candidates(rows, *, mode="NORMAL", fee=0, budget=None, minimum=2, target=None,
                         max_breakeven_move_pct=None, spot=100):
    """Unpruned public-price oracle for unique, standard test contracts."""
    def number(value, *, positive=False):
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None

    classes = {}
    for row in rows:
        expiry = row["expiry"]
        root = row["osi"].upper().replace(" ", "").lstrip(".").split(expiry.strftime("%y%m%d"))[0].rstrip("-")
        classes.setdefault((expiry, root), []).append(row)
    expected = []
    for (expiry, root), contracts in classes.items():
        calls = sorted((row for row in contracts if row["call_put"].upper() == "CALL" and float(row["strike"]) > spot),
                       key=lambda row: float(row["strike"]))
        puts = sorted((row for row in contracts if row["call_put"].upper() == "PUT" and float(row["strike"]) < spot),
                      key=lambda row: -float(row["strike"]))
        for cb, cs in combinations(calls, 2):
            for pb, ps in combinations(puts, 2):
                candidate_budget = budget
                if max_breakeven_move_pct is not None:
                    center = Decimal(str(spot))
                    move = center*Decimal(str(max_breakeven_move_pct))/100
                    cap = min(Decimal(str(pb["strike"]))-center+move,
                              center+move-Decimal(str(cb["strike"])))*100
                    if budget is not None:
                        cap = min(cap,Decimal(str(budget)))
                    if cap <= 0:
                        continue
                    candidate_budget = float(cap)
                price = price_ric_quote(buy_call=cb, sell_call=cs, buy_put=pb, sell_put=ps,
                                        spot_last=spot, max_cost=candidate_budget, pricing_mode=mode,
                                        min_reward_risk=minimum, fee_per_spread=fee)
                if price is None:
                    continue
                row = {"symbol":"XYZ", "spot_last":float(spot), "expiry":expiry, "year":expiry.year,
                       "call_put":"RIC", "option_root":root, "pricing_mode":mode, **price}
                for prefix, leg in (("buy", cb), ("sell", cs), ("put_buy", pb), ("put_sell", ps)):
                    row[prefix+"_strike"] = float(leg["strike"])
                    row[prefix+"_osi"] = str(leg["osi"]).strip()
                    row[prefix+"_bid"], row[prefix+"_ask"] = float(leg["bid"]), float(leg["ask"])
                    row[prefix+"_last"] = number(leg.get("last"), positive=True)
                    row[prefix+"_open_interest"] = number(leg.get("open_interest"))
                    row[prefix+"_volume"] = number(leg.get("volume"))
                row["id"] = "|".join(("XYZ", expiry.isoformat(), "RIC", root,
                                      *(f'{row[key+"_strike"]:.6f}' for key in ("put_sell","put_buy","buy","sell"))))
                expected.append(row)
    return sorted(expected, key=lambda row: reference_rank(row, target))


def reference_rank(row, target):
    distance = (min(abs(row["upper_breakeven"]-target), abs(row["lower_breakeven"]-target))
                if target is not None else 0)
    return (row["grade"], distance, row["cost"], -row["reward_risk"], row["expiry"],
            row["buy_strike"], row["sell_strike"], row["put_buy_strike"], row["put_sell_strike"], row["option_root"])


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


class ReverseIronCondorPreparedScanTests(unittest.TestCase):
    def varied_chain(self, *, expiry=EXPIRY, root="XYZ"):
        rng = random.Random(2731)
        rows = []
        for side in ("CALL", "PUT"):
            for index, distance in enumerate((.125, .75, 1.25, 2.375, 4.5, 7.625)):
                premium = 1.2 * math.exp(-distance/2.1) + .015
                gap = rng.uniform(.002, .025)
                row = contract(side, 100+distance if side == "CALL" else 100-distance,
                               max(0, premium-gap), premium+gap, expiry=expiry, root=root)
                patterns = (
                    {"greeks":{"delta":.3,"gamma":.02,"theta":-.04,"vega":.1}},
                    {"OptionGreeks":{"delta":"-.2","gamma":".01","theta":"-.03","vega":".05"}},
                    {"greeks":{},"OptionGreeks":{"delta":.15,"theta":-.015}},
                    {"delta":None,"gamma":"nan","theta":"-.02","vega":"bad",
                     "greeks":{"delta":.7,"gamma":.4,"theta":-.4,"vega":.5}},
                    {"greeks":{"delta":float("inf"),"gamma":-.005,"theta":-.001,"vega":True}},
                    {"greeks":"unavailable","OptionGreeks":{"delta":.8}},
                )
                row.update(deepcopy(patterns[index]))
                row["open_interest"] = (0,"1000",None,-1,float("inf"),200.5)[index]
                row["volume"] = (30,"40.5",None,-1,float("nan"),True)[index]
                row["last"] = (premium,".25",None,0,-1,"nan")[index]
                if index in (1,3):
                    for key in ("strike","bid","ask"):
                        row[key] = str(row[key])
                rows.append(row)
        return rows

    def test_full_candidate_dictionaries_match_unpruned_public_quote_oracle(self):
        rows = self.varied_chain()
        before = repr(rows)
        rng = random.Random(7139)
        qualified = 0
        for mode,fee,minimum in product(("NORMAL","SMART FILL"),(0,.01,2.6,10),(2,2.5,3.25)):
            options = dict(mode=mode,fee=fee,
                           budget=(None,40,100,250)[rng.randrange(4)],
                           minimum=minimum,
                           target=(None,98.875,102.5)[rng.randrange(3)])
            with self.subTest(options=options):
                expected = reference_candidates(rows, **options)
                actual, annual = scan(rows, pricing_mode=options["mode"], fee_per_spread=options["fee"],
                                      max_cost=options["budget"], min_reward_risk=options["minimum"],
                                      target_breakeven=options["target"], max_per_expiration=None)
                self.assertEqual(actual,expected)
                self.assertEqual(annual,{expected[0]["id"]} if expected else set())
                qualified += len(actual)
        self.assertGreater(qualified,0)
        self.assertEqual(repr(rows),before)

    def test_fractional_half_cent_and_cancellation_preserve_every_public_price_field(self):
        legs = {
            "buy_call":contract("CALL",105.125,1,1.005),
            "sell_call":contract("CALL",108.170,.5,.5),
            "buy_put":contract("PUT",94.875,1,1.005),
            "sell_put":contract("PUT",91.830,.5,.5),
        }
        for row, delta, last in zip(legs.values(),(.3,.2,.1,.01),(1e8+.015,1e8+.01,.2,.1)):
            row["delta"] = delta
            row["gamma"] = delta/10
            row["theta"] = -delta
            row["vega"] = delta*2
            row["last"] = last
        self.assertNotEqual(.3-.2+.1-.01,(.3-.2)+(.1-.01))
        seen = 0
        for mode in ("NORMAL","SMART FILL"):
            for fee in (0,.5,.51,2.605):
                for budget in (None,101.5,104):
                    expected = reference_candidates(list(legs.values()),mode=mode,fee=fee,budget=budget)
                    actual, _ = scan(list(legs.values()),pricing_mode=mode,fee_per_spread=fee,
                                      max_cost=budget,max_per_expiration=None)
                    self.assertEqual(actual,expected)
                    seen += len(actual)
                    if actual:
                        self.assertEqual(actual[0]["net_delta"],.3-.2+.1-.01)
        self.assertGreater(seen,0)

    def test_root_classes_duplicate_metadata_and_conflicting_quotes_preserve_exact_results(self):
        base = self.varied_chain()
        weekly = self.varied_chain(root="XYZW---")
        rows = base+weekly
        expected = reference_candidates(rows)
        actual, _ = scan(rows,max_per_expiration=None)
        self.assertEqual(actual,expected)
        self.assertEqual({row["option_root"] for row in actual},{"XYZ","XYZW"})
        duplicate = deepcopy(base[0])
        duplicate.update(last=999,open_interest=99999,greeks={"delta":.999})
        self.assertEqual(scan(rows+[duplicate],max_per_expiration=None)[0],expected)
        conflict = deepcopy(base[0])
        conflict["ask"] += .01
        surviving = [row for row in rows if row is not base[0]]
        expected_conflict = reference_candidates(surviving)
        self.assertEqual(scan(rows+[conflict],max_per_expiration=None)[0],expected_conflict)
        self.assertEqual(scan([conflict]+rows,max_per_expiration=None)[0],expected_conflict)
        for field,value in (("osi",""),("osi","XYZ1290119C00105125"),("underlying","OTHER"),
                            ("multiplier",10),("adjusted",True),("bid",None),("ask",float("nan"))):
            altered = deepcopy(base)
            altered[0][field] = value
            with self.subTest(field=field,value=value):
                self.assertEqual(scan(altered,max_per_expiration=None)[0],reference_candidates(base[1:]))

    def test_annual_top_k_and_complete_sink_keep_full_dictionary_parity(self):
        rows = self.varied_chain()+self.varied_chain(expiry=date(2030,1,18),root="XYZW---")
        target = 97.625
        expected = reference_candidates(rows,target=target,fee=2.6)
        expected_annual = {next(row["id"] for row in expected if row["year"]==year)
                           for year in {row["year"] for row in expected}}
        complete, annual = scan(rows,target_breakeven=target,fee_per_spread=2.6,max_per_expiration=None)
        self.assertEqual(complete,expected)
        self.assertEqual(annual,expected_annual)
        streamed, progress = [],[]
        retained, sink_annual = scan(rows,target_breakeven=target,fee_per_spread=2.6,max_per_expiration=None,
                                     candidate_sink=streamed.append,progress=progress.append)
        self.assertEqual(retained,[])
        self.assertEqual(sorted(streamed,key=lambda row:reference_rank(row,target)),expected)
        self.assertEqual(sink_annual,expected_annual)
        limited, limited_annual = scan(rows,target_breakeven=target,fee_per_spread=2.6,max_per_expiration=3)
        winners, per_expiry = [],{}
        for row in expected:
            count = per_expiry.get(row["expiry"],0)
            if count<3:
                winners.append(row)
                per_expiry[row["expiry"]] = count+1
        self.assertEqual(limited,winners)
        self.assertEqual(limited_annual,expected_annual)
        self.assertTrue(progress)
        self.assertGreaterEqual(progress[-1],len(streamed))

    def test_scan_quote_identity_and_metadata_work_scale_with_input_contracts(self):
        for per_side in (8,12):
            rows = []
            for side in ("CALL","PUT"):
                for step in range(1,per_side+1):
                    premium = .01*(per_side+1-step)
                    row = contract(side,100+step if side=="CALL" else 100-step,premium,premium)
                    row["greeks"] = {"delta":.3,"gamma":.02,"theta":-.01,"vega":.1}
                    rows.append(row)
            count = 0
            def discard(row):
                nonlocal count
                count += 1
            with (patch.object(ric,"_quotes",wraps=ric._quotes) as quotes,
                  patch.object(ric,"_greek",wraps=ric._greek) as greeks,
                  patch.object(ric,"_matching_quote_identities",wraps=ric._matching_quote_identities) as identities,
                  patch.object(ric,"validate_ric",wraps=ric.validate_ric) as strikes):
                retained, _ = scan(rows,fee_per_spread=2.6,max_per_expiration=None,candidate_sink=discard)
            self.assertEqual(retained,[])
            self.assertEqual(count,math.comb(per_side,2)**2)
            self.assertGreater(count,10*len(rows))
            self.assertLessEqual(quotes.call_count,len(rows))
            self.assertLessEqual(greeks.call_count,4*len(rows))
            self.assertLessEqual(identities.call_count,len(rows))
            self.assertLessEqual(strikes.call_count,len(rows))

    def test_breakeven_scope_matches_unpruned_public_price_and_geometry(self):
        rows = self.varied_chain()
        before = repr(rows)
        seen = 0
        for mode,fee,minimum,move,budget in product(
                ("NORMAL","SMART FILL"),(0,2.6),(2,3),(1.5,3,7),(None,40)):
            with self.subTest(mode=mode,fee=fee,minimum=minimum,move=move,budget=budget):
                expected = reference_candidates(rows,mode=mode,fee=fee,minimum=minimum,
                                                 budget=budget,max_breakeven_move_pct=move)
                actual,annual = scan(rows,pricing_mode=mode,fee_per_spread=fee,
                                     min_reward_risk=minimum,max_cost=budget,
                                     max_breakeven_move_pct=move,max_per_expiration=None)
                self.assertEqual(actual,expected)
                self.assertEqual(annual,{expected[0]["id"]} if expected else set())
                for row in actual:
                    risk = Decimal(str(row["debit"]))*100+Decimal(str(fee))
                    self.assertGreaterEqual(Decimal(str(row["put_buy_strike"]))-risk/100,100-Decimal(str(move)))
                    self.assertLessEqual(Decimal(str(row["buy_strike"]))+risk/100,100+Decimal(str(move)))
                seen += len(actual)
        self.assertGreater(seen,0)
        self.assertEqual(repr(rows),before)
        shifted = deepcopy(rows)
        for row in shifted:
            row["strike"] = float(Decimal(str(row["strike"]))+Decimal(".005"))
            row["osi"] = f"XYZ{row['expiry']:%y%m%d}{row['call_put'][0]}{round(row['strike']*1000):08d}"
        for mode,fee in product(("NORMAL","SMART FILL"),(0,2.6)):
            expected = reference_candidates(shifted,spot=100.005,mode=mode,fee=fee,
                                             minimum=3,budget=40,max_breakeven_move_pct=3)
            actual,_ = scan_ric_candidates(symbol="XYZ",spot_last=100.005,option_rows=shifted,
                                            target_breakeven=None,max_cost=40,min_reward_risk=3,
                                            pricing_mode=mode,fee_per_spread=fee,
                                            max_breakeven_move_pct=3,max_per_expiration=None)
            self.assertEqual(actual,expected)

    def test_breakeven_scope_unlimited_preserves_full_rows_annual_and_sink(self):
        rows = self.varied_chain()+self.varied_chain(expiry=date(2030,1,18))
        for mode,fee in product(("NORMAL","SMART FILL"),(0,2.6)):
            with self.subTest(mode=mode,fee=fee):
                options = dict(pricing_mode=mode,fee_per_spread=fee,max_per_expiration=None)
                baseline,annual = scan(rows,**options)
                explicit,explicit_annual = scan(rows,max_breakeven_move_pct=None,**options)
                self.assertEqual((explicit,explicit_annual),(baseline,annual))
                self.assertEqual(baseline,reference_candidates(rows,mode=mode,fee=fee))
                expected = reference_candidates(rows,mode=mode,fee=fee,max_breakeven_move_pct=3)
                streamed = []
                retained,sink_annual = scan(rows,max_breakeven_move_pct=3,candidate_sink=streamed.append,**options)
                self.assertEqual(retained,[])
                self.assertEqual(sorted(streamed,key=lambda row:reference_rank(row,None)),expected)
                self.assertEqual(sink_annual,{next(row["id"] for row in expected if row["year"]==year)
                                              for year in {row["year"] for row in expected}})

    def test_breakeven_scope_inclusive_cent_boundary_and_asymmetric_wings(self):
        rows = [contract("PUT",93,.5,.5),contract("PUT",98,1,1),
                contract("CALL",102,1,1),contract("CALL",110,.5,.5)]
        for mode in ("NORMAL","SMART FILL"):
            with self.subTest(mode=mode):
                exact,_ = scan(rows,fee_per_spread=2.6,pricing_mode=mode,
                               max_breakeven_move_pct=3.026,max_per_expiration=None)
                self.assertEqual(len(exact),1)
                self.assertEqual(exact[0]["cost"],102.6)
                self.assertEqual((exact[0]["lower_breakeven"],exact[0]["upper_breakeven"]),(96.974,103.026))
                self.assertEqual(scan(rows,fee_per_spread=2.6,pricing_mode=mode,
                                      max_breakeven_move_pct=3.025999999)[0],[])
                self.assertEqual(scan(rows,fee_per_spread=2.6,pricing_mode=mode,
                                      max_breakeven_move_pct=3.026,max_cost=102.59)[0],[])
        variable = [dict(row,bid=row["bid"]-.05,ask=row["ask"]+.05) for row in rows]
        clipped,_ = scan(variable,fee_per_spread=2.6,max_breakeven_move_pct=3.056,max_per_expiration=None)
        ordinary,_ = scan(variable,fee_per_spread=2.6,max_per_expiration=None)
        self.assertEqual(len(clipped),1)
        self.assertLess(clipped[0]["debit"],ordinary[0]["debit"])
        self.assertEqual(clipped,reference_candidates(variable,fee=2.6,max_breakeven_move_pct=3.056))
        distant_shorts = [dict(row,bid=.9) if row["strike"] in (98,102) else row for row in rows]
        for mode in ("NORMAL","SMART FILL"):
            scoped,_ = scan(distant_shorts,fee_per_spread=2.6,pricing_mode=mode,
                            max_breakeven_move_pct=3,max_per_expiration=None)
            self.assertEqual(len(scoped),1)
            self.assertGreaterEqual(scoped[0]["lower_breakeven"],97)
            self.assertLessEqual(scoped[0]["upper_breakeven"],103)
            self.assertLess(scoped[0]["put_sell_strike"],97)
            self.assertGreater(scoped[0]["sell_strike"],103)

    def test_breakeven_scope_reduces_pricing_without_capping_qualifying_rows(self):
        rows = [contract(side,100+step*.25 if side=="CALL" else 100-step*.25,
                         .01*(17-step),.01*(17-step))
                for side in ("CALL","PUT") for step in range(1,17)]
        all_stats,scope_stats = {},{}
        full,_ = scan(rows,max_per_expiration=None,scan_stats=all_stats)
        scoped,_ = scan(rows,max_per_expiration=None,scan_stats=scope_stats,max_breakeven_move_pct=3)
        expected = reference_candidates(rows,max_breakeven_move_pct=3)
        self.assertEqual(scoped,expected)
        self.assertGreater(len(scoped),200)
        self.assertLess(len(scoped),len(full))
        self.assertEqual(scope_stats["qualified_condors"],len(scoped))
        self.assertLess(scope_stats["priced_condors"],all_stats["priced_condors"])
        self.assertEqual(scope_stats["input_contracts"],len(rows))

    def test_breakeven_cap_helper_uses_fixed_spot_and_strict_positive_scope(self):
        values = dict(spot_last=100,put_buy_strike=98,buy_strike=102)
        self.assertIsNone(ric.ric_breakeven_max_cost(**values))
        self.assertEqual(ric.ric_breakeven_max_cost(**values,max_breakeven_move_pct=3.026),102.6)
        self.assertEqual(ric.ric_breakeven_max_cost(spot_last=100.005,put_buy_strike=99.005,
                                                 buy_strike=101.005,max_breakeven_move_pct=2),100.01)
        self.assertEqual(ric.ric_breakeven_max_cost(spot_last=100,put_buy_strike=99,
                                                 buy_strike=102,max_breakeven_move_pct=3),100)
        self.assertLessEqual(ric.ric_breakeven_max_cost(**values,max_breakeven_move_pct=2),0)
        self.assertEqual(scan(list(example().values()),max_breakeven_move_pct=3)[0],[])
        for value in (0,-1,True,"",float("nan"),float("inf"),"bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    scan(list(example().values()),max_breakeven_move_pct=value)
                with self.assertRaises(ValueError):
                    ric.ric_breakeven_max_cost(**values,max_breakeven_move_pct=value)


class ReverseIronCondorBatchTests(unittest.TestCase):
    def batches(self, rows, **options):
        batches, stats = [], {}
        ric.scan_ric_batches(symbol="XYZ",spot_last=options.pop("spot_last",100),option_rows=rows,
                             batch_sink=batches.append,scan_stats=stats,**options)
        return batches,stats

    def decoded(self, batches):
        rows = []
        for batch in batches:
            self.assertTrue(all(not value.flags.writeable for value in
                                (batch.call_wing,batch.put_wing,batch.debit_cents,*batch.scalars.values())))
            for index in range(batch.count):
                row = ric.ric_batch_candidate(batch.manifest,batch.call_wing[index],batch.put_wing[index],batch.debit_cents[index])
                for key,values in batch.scalars.items():
                    if key != "basis_code":
                        self.assertEqual(row[key],values[index],key)
                rows.append(row)
        return rows

    def test_typed_rows_match_independent_full_quote_oracle_across_scopes_and_fees(self):
        rows = ReverseIronCondorPreparedScanTests().varied_chain()
        before = repr(rows)
        seen = 0
        for mode,fee,minimum,scope,budget in product(("NORMAL","SMART FILL"),(0,2.6),(2,3.25),(None,3),(None,40)):
            with self.subTest(mode=mode,fee=fee,minimum=minimum,scope=scope,budget=budget):
                options = dict(pricing_mode=mode,fee_per_spread=fee,min_reward_risk=minimum,
                               max_breakeven_move_pct=scope,max_cost=budget,max_chunk=17)
                batches,stats = self.batches(rows,**options)
                actual = self.decoded(batches)
                expected = reference_candidates(rows,mode=mode,fee=fee,minimum=minimum,
                                                  max_breakeven_move_pct=scope,budget=budget)
                self.assertEqual(sorted(actual,key=lambda row:reference_rank(row,None)),expected)
                self.assertEqual(stats["qualified_condors"],len(expected))
                self.assertTrue(all(batch.count<=17 for batch in batches))
                seen += len(actual)
        self.assertGreater(seen,0)
        self.assertEqual(repr(rows),before)

    def test_cent_grid_and_float_fallback_preserve_exact_fields_and_sink_order(self):
        rng = random.Random(99573)
        seen = grid = 0
        for case in range(30):
            rows = []
            for side in ("CALL","PUT"):
                for index,distance in enumerate((1,2.25,4.5,7.75,10.5,15)):
                    premium,gap = 200-25*index,rng.randrange(5)
                    rows.append(contract(side,100+distance if side=="CALL" else 100-distance,
                                         (premium-gap)/100,(premium+gap)/100))
            options = dict(pricing_mode=rng.choice(("NORMAL","SMART FILL")),
                           fee_per_spread=rng.choice((0,.01,1,2.6)),
                           min_reward_risk=rng.choice((2,3,7)),max_cost=rng.choice((None,100,250)))
            if case%3==0:
                rows[0]["ask"] += .005
            batches,stats = self.batches(rows,max_chunk=31,**options)
            actual = self.decoded(batches)
            expected,expected_stats = [],{}
            scan(rows,max_per_expiration=None,candidate_sink=expected.append,scan_stats=expected_stats,**options)
            self.assertEqual(actual,expected)
            self.assertTrue(all(stats[key]==value for key,value in expected_stats.items()))
            grid += stats["grid_classes"]
            seen += len(actual)
        self.assertGreater(grid,0)
        self.assertGreater(seen,0)
        dense = [contract(side,100+step if side=="CALL" else 100-step,(17-step)/100,(17-step)/100)
                 for side in ("CALL","PUT") for step in range(1,17)]
        expected = []
        scan(dense,max_per_expiration=None,candidate_sink=expected.append)
        batches,_ = self.batches(dense,max_chunk=97)
        self.assertEqual(self.decoded(batches),expected)
        self.assertEqual(len(expected),14400)

    def test_round8_matches_python_bits_at_half_boundaries_and_extremes(self):
        rng = random.Random(4107)
        values = [0.0,-0.0,float("inf"),-float("inf"),float("nan"),1e300,-1e-300]
        for _ in range(2000):
            value = (rng.randrange(-10**14,10**14)+.5)/1e8
            values.extend((value,math.nextafter(value,-math.inf),math.nextafter(value,math.inf)))
        expected = np.asarray([round(float(value),8) for value in values])
        actual = ric._ric_round8(np.asarray(values))
        self.assertTrue(np.all((expected.view(np.uint64)==actual.view(np.uint64))
                               | (np.isnan(expected)&np.isnan(actual))))
        rows = [contract("CALL",105,.3,.5),contract("CALL",110,.1,.2),
                contract("PUT",95,.1,.3),contract("PUT",90,.05,.2)]
        batches,_ = self.batches(rows)
        self.assertEqual(len(batches),1)
        self.assertTrue(np.signbit(batches[0].scalars["spread_bid"][0]))
        row = self.decoded(batches)[0]
        self.assertTrue(np.signbit(row["spread_bid"]))

    def test_fast_grid_avoids_per_candidate_scalar_pricing_and_fields_are_lazy(self):
        rows = [contract(side,100+step if side=="CALL" else 100-step,(9-step)/100,(9-step)/100)
                for side in ("CALL","PUT") for step in range(1,9)]
        with (patch.object(ric,"_price_prepared_ric",side_effect=AssertionError("scalar row materialized")),
              patch.object(ric,"_ric_round8",side_effect=AssertionError("unused float rounding"))):
            batches,stats = self.batches(rows,scalar_fields={"cost","reward_risk"})
        self.assertEqual(stats["qualified_condors"],784)
        self.assertTrue(all(set(batch.scalars)=={"cost","reward_risk"} for batch in batches))
        batch = batches[0]
        for fields in ({"cost"},{"spread_mid"},{"lower_breakeven","reward_risk"}):
            values = ric.ric_batch_values(batch.manifest,batch.call_wing,batch.put_wing,batch.debit_cents,fields=fields)
            self.assertEqual(set(values),fields)
            for key,array in values.items():
                self.assertEqual(array[0],ric.ric_batch_candidate(batch.manifest,batch.call_wing[0],batch.put_wing[0],batch.debit_cents[0])[key])

    def test_checkpoint_reports_live_counts_and_cancels_without_false_completion(self):
        rows = [contract(side,100+step if side=="CALL" else 100-step,(13-step)/100,(13-step)/100)
                for side in ("CALL","PUT") for step in range(1,13)]
        stats,observed,retained = {},[],[]
        def checkpoint():
            observed.append(stats.get("priced_condors",0))
            if retained:
                raise TimeoutError("two-minute budget expired")
        with self.assertRaisesRegex(TimeoutError,"budget expired"):
            ric.scan_ric_batches(symbol="XYZ",spot_last=100,option_rows=rows,batch_sink=retained.append,
                                 max_chunk=31,scan_stats=stats,checkpoint=checkpoint)
        self.assertGreater(stats["priced_condors"],0)
        self.assertEqual(observed,sorted(observed))
        self.assertEqual(len(retained),1)
        self.assertLess(stats["qualified_condors"],4356)

    def test_field_helper_does_not_retain_native_arrays_until_cyclic_gc(self):
        rows = [contract(side,100+step if side=="CALL" else 100-step,(9-step)/100,(9-step)/100)
                for side in ("CALL","PUT") for step in range(1,9)]
        batches,_ = self.batches(rows,scalar_fields={"cost"})
        batch = batches[0]
        indices = batch.call_wing[:2].copy()
        reference = weakref.ref(indices)
        enabled = gc.isenabled()
        gc.disable()
        try:
            result = ric.ric_batch_values(batch.manifest,indices,batch.put_wing[:2],
                                         batch.debit_cents[:2],fields={"cost","reward_risk"})
            del result,indices
            self.assertIsNone(reference())
        finally:
            if enabled:
                gc.enable()

    def test_unsupported_magnitude_and_id_collision_request_legacy_fallback(self):
        source = list(example().values())
        with self.assertRaises(ric.RICBatchUnsupported):
            self.batches(source,spot_last=2000000)
        high_quotes = list(example(locked=True).values())
        for row in high_quotes:
            row["bid"] += 100000000
            row["ask"] += 100000000
        self.assertEqual(len(scan(high_quotes,max_per_expiration=None)[0]),1)
        with self.assertRaises(ric.RICBatchUnsupported):
            self.batches(high_quotes)
        expiry = EXPIRY
        rows = [contract("PUT",95,1.05,1.15),contract("PUT",90,.4,.5),
                contract("CALL",105.0000001,1.05,1.15),contract("CALL",105.0000002,1.05,1.15),
                contract("CALL",110,.4,.5)]
        for row in rows[2:4]:
            row["osi"] = f"XYZ{expiry:%y%m%d}C{row['strike']}"
        expected,_ = scan(rows,max_per_expiration=None)
        self.assertGreater(len(expected),len({row["id"] for row in expected}))
        emitted = []
        with self.assertRaises(ric.RICBatchUnsupported):
            ric.scan_ric_batches(symbol="XYZ",spot_last=100,option_rows=rows,batch_sink=emitted.append)
        self.assertEqual(emitted,[])
        self.assertIs(ric.scan_ric_candidates._ric_batch_calculator,ric.scan_ric_batches)
        self.assertIs(ric.scan_ric_batches._ric_scalar_calculator,ric.scan_ric_candidates)


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
