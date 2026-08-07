import math
import re
import unittest

import analysis


class TestIndicators(unittest.TestCase):
    def test_sma_averages_last_n(self):
        self.assertAlmostEqual(analysis.sma([1, 2, 3, 4, 5], 3), 4.0)

    def test_sma_none_when_series_too_short(self):
        self.assertIsNone(analysis.sma([1, 2], 5))

    def test_rsi_all_gains_is_100(self):
        self.assertAlmostEqual(analysis.rsi(list(range(1, 20)), 14), 100.0)

    def test_rsi_all_losses_is_0(self):
        self.assertAlmostEqual(analysis.rsi(list(range(20, 1, -1)), 14), 0.0)

    def test_rsi_alternating_is_midrange(self):
        series = [10.0 + (1.0 if i % 2 else 0.0) for i in range(30)]
        self.assertTrue(40.0 < analysis.rsi(series, 14) < 60.0)

    def test_rsi_none_when_series_too_short(self):
        self.assertIsNone(analysis.rsi([1.0, 2.0, 3.0], 14))

    def test_rsi_flat_series_is_neutral(self):
        """A directionless series is neutral, not overbought."""
        self.assertAlmostEqual(analysis.rsi([10.0] * 30, 14), 50.0)

    def test_range_position_at_high_is_100(self):
        series = [float(i) for i in range(1, 21)]
        self.assertAlmostEqual(analysis.range_position(series, 20), 100.0)

    def test_range_position_at_low_is_0(self):
        series = [float(i) for i in range(20, 0, -1)]
        self.assertAlmostEqual(analysis.range_position(series, 20), 0.0)

    def test_range_position_flat_series_is_50(self):
        self.assertAlmostEqual(analysis.range_position([7.0] * 20, 20), 50.0)

    def test_realized_vol_flat_series_hits_floor(self):
        self.assertAlmostEqual(analysis.realized_vol([5.0] * 30), 0.1)

    def test_realized_vol_is_annualized_and_positive(self):
        series = [10.0 * (1.01 ** i) if i % 2 else 10.0 * (0.99 ** i) for i in range(40)]
        self.assertGreater(analysis.realized_vol(series), 0.1)

    def test_pct_change_computes_percent(self):
        self.assertAlmostEqual(analysis.pct_change([100.0, 50.0, 110.0], 2), 10.0)

    def test_pct_change_none_when_series_too_short(self):
        self.assertIsNone(analysis.pct_change([100.0], 5))


class TestOptionPricing(unittest.TestCase):
    def test_deep_itm_call_approaches_intrinsic(self):
        price = analysis.bs_call(100.0, 10.0, 30 / 365.0, 0.5)
        self.assertAlmostEqual(price, 90.0, delta=1.0)

    def test_atm_call_is_positive_and_bounded(self):
        price = analysis.bs_call(10.0, 10.0, 30 / 365.0, 0.8)
        self.assertTrue(0.0 < price < 10.0)

    def test_put_call_parity_holds(self):
        S, K, T, iv, r = 25.0, 26.0, 45 / 365.0, 0.6, 0.04
        lhs = analysis.bs_call(S, K, T, iv, r) - analysis.bs_put(S, K, T, iv, r)
        rhs = S - K * math.exp(-r * T)
        self.assertAlmostEqual(lhs, rhs, places=6)

    def test_zero_time_call_is_intrinsic(self):
        self.assertAlmostEqual(analysis.bs_call(12.0, 10.0, 0.0, 0.5), 2.0)

    def test_contract_cost_is_per_hundred_shares(self):
        cost = analysis.contract_cost("call", 10.0, 8, 0.8)
        premium = analysis.bs_call(10.0, 10.0, 8 / 365.0, 0.8)
        self.assertAlmostEqual(cost, premium * 100.0)

    def test_contract_cost_rounds_strike_to_atm(self):
        # spot 9.94 -> strike 10, matching index.html's Math.round(spot)
        cost = analysis.contract_cost("call", 9.94, 8, 0.67)
        premium = analysis.bs_call(9.94, 10.0, 8 / 365.0, 0.67)
        self.assertAlmostEqual(cost, premium * 100.0)


class TestQuoteLiquidity(unittest.TestCase):
    def test_liquid_tight_quote_is_trusted(self):
        # SOUN 2-day ATM: heavily traded, 2-cent spread
        self.assertTrue(analysis.quote_is_liquid(0.40, 0.42, 7929))

    def test_dead_market_quote_is_rejected(self):
        # ODD 2-day ATM: 1 open interest, spread wider than the bid
        self.assertFalse(analysis.quote_is_liquid(0.30, 1.80, 1))

    def test_wide_but_traded_quote_is_trusted(self):
        # ELF 2-day ATM: 33% spread but real open interest
        self.assertTrue(analysis.quote_is_liquid(5.80, 8.10, 93))

    def test_missing_or_crossed_quotes_are_rejected(self):
        self.assertFalse(analysis.quote_is_liquid(None, 0.42, 7929))
        self.assertFalse(analysis.quote_is_liquid(0.0, 0.42, 7929))
        self.assertFalse(analysis.quote_is_liquid(0.50, 0.40, 7929))

    def test_cheap_option_with_wide_ratio_but_tight_spread_is_trusted(self):
        # AI 2-day ATM: 67% relative spread, but 12 cents wide on 6340 open interest
        self.assertTrue(analysis.quote_is_liquid(0.12, 0.24, 6340))

    def test_tight_spread_does_not_rescue_a_contract_nobody_holds(self):
        self.assertFalse(analysis.quote_is_liquid(0.12, 0.24, 1))

    def test_nan_open_interest_is_rejected(self):
        # nan < MIN_OPEN_INTEREST is False, so this must be checked explicitly
        # or a NaN-open-interest quote (a real yfinance shape) slips through.
        self.assertFalse(analysis.quote_is_liquid(0.40, 0.42, float("nan")))

    def test_quote_cost_is_the_mid_per_hundred_shares(self):
        self.assertAlmostEqual(analysis.quote_cost(5.80, 8.10), 695.0)


def snap(**kw):
    """A mid-range, unremarkable snapshot. Override only what a test cares about."""
    base = dict(
        sym="TEST", spot=10.0, sma20=10.0, sma50=10.0, rsi=50.0, pos=50.0,
        hi20=12.0, lo20=8.0, chg_1mo=0.0, chg_5d=0.0, iv=0.8, iv_far=0.8,
        rvol=0.8, call_cost=50.0, put_cost=50.0, near_dte=8,
        call_quoted=True, put_quoted=True,
    )
    base.update(kw)
    return base


class TestVerdicts(unittest.TestCase):
    def test_over_budget_mutes_regardless_of_setup(self):
        # a textbook call setup, but the contract costs 6x budget
        d = analysis.decide("call", snap(
            spot=89.66, sma20=85.0, sma50=80.0, rsi=60.0, pos=70.0, call_cost=597.0))
        self.assertEqual(d.verdict, "mute")
        self.assertEqual(d.rule_id, "over_budget")

    def test_quoted_cost_over_budget_mutes_even_when_black_scholes_would_not(self):
        """The ELF regression: a discarded/fallback iv can make Black-Scholes
        estimate a contract far cheaper than its real quoted price. The budget
        gate must key off whatever cost is actually in the snapshot — which
        refresh.py now sets from the quote when one is usable — not recompute
        its own, cheaper estimate."""
        bs_cost = analysis.contract_cost("call", 86.37, 2, 0.535)
        quote_cost = analysis.quote_cost(5.80, 8.10)  # ELF's real near-dated quote
        self.assertLess(bs_cost, analysis.BUDGET * analysis.BUDGET_MULTIPLE)
        self.assertGreater(quote_cost, analysis.BUDGET * analysis.BUDGET_MULTIPLE)

        d = analysis.decide("call", snap(
            spot=86.37, sma20=79.27, sma50=69.20, rsi=69.3, pos=90.6,
            hi20=87.84, call_cost=quote_cost, put_cost=quote_cost))
        self.assertEqual(d.verdict, "mute")
        self.assertEqual(d.rule_id, "over_budget")

    def test_budget_gate_uses_the_direction_specific_cost(self):
        d = analysis.decide("call", snap(call_cost=90.0, put_cost=900.0))
        self.assertNotEqual(d.rule_id, "over_budget")
        d = analysis.decide("put", snap(call_cost=90.0, put_cost=900.0))
        self.assertEqual(d.rule_id, "over_budget")

    def test_call_extended_at_top_of_range_with_hot_rsi(self):
        d = analysis.decide("call", snap(pos=95.0, rsi=74.0))
        self.assertEqual(d.verdict, "skip")
        self.assertEqual(d.rule_id, "extended")

    def test_call_no_base_under_both_averages_at_range_low(self):
        d = analysis.decide("call", snap(spot=9.0, sma20=10.0, sma50=11.0, pos=15.0, rsi=35.0))
        self.assertEqual(d.verdict, "wait")
        self.assertEqual(d.rule_id, "no_base")

    def test_call_clean_setup(self):
        d = analysis.decide("call", snap(spot=11.0, sma20=10.5, sma50=10.0, pos=70.0, rsi=60.0))
        self.assertEqual(d.verdict, "go")
        self.assertEqual(d.rule_id, "clean_setup")

    def test_call_breakout_pending_near_the_high(self):
        d = analysis.decide("call", snap(
            spot=11.9, sma20=11.0, sma50=10.0, hi20=12.0, pos=97.0, rsi=65.0))
        self.assertEqual(d.verdict, "wait")
        self.assertEqual(d.rule_id, "breakout_pending")

    def test_call_chop_is_the_default(self):
        d = analysis.decide("call", snap())
        self.assertEqual(d.verdict, "wait")
        self.assertEqual(d.rule_id, "chop")

    def test_extended_beats_breakout_pending_when_both_match(self):
        # near the high AND overbought: the earlier rule must win
        d = analysis.decide("call", snap(
            spot=11.9, sma20=11.0, sma50=10.0, hi20=12.0, pos=95.0, rsi=75.0))
        self.assertEqual(d.rule_id, "extended")

    def test_put_washed_out_at_range_low_and_oversold(self):
        d = analysis.decide("put", snap(spot=8.1, sma20=9.5, sma50=10.5, pos=5.0, rsi=25.0))
        self.assertEqual(d.verdict, "wait")
        self.assertEqual(d.rule_id, "washed_out")
        self.assertEqual(d.vlabel, "Late")

    def test_put_breakdown_is_the_green_short(self):
        d = analysis.decide("put", snap(spot=9.0, sma20=10.0, sma50=11.0, pos=30.0, rsi=38.0))
        self.assertEqual(d.verdict, "go")
        self.assertEqual(d.rule_id, "breakdown")

    def test_put_no_short_edge_in_an_uptrend(self):
        d = analysis.decide("put", snap(spot=12.0, sma20=11.0, sma50=10.0, pos=80.0, rsi=62.0))
        self.assertEqual(d.verdict, "skip")
        self.assertEqual(d.rule_id, "no_short_edge")

    def test_put_chop_is_the_default_and_skips(self):
        d = analysis.decide("put", snap())
        self.assertEqual(d.verdict, "skip")
        self.assertEqual(d.rule_id, "chop")

    def test_washed_out_beats_breakdown_when_both_match(self):
        d = analysis.decide("put", snap(spot=8.0, sma20=9.5, sma50=10.5, pos=8.0, rsi=28.0))
        self.assertEqual(d.rule_id, "washed_out")

    def test_rich_iv_downgrades_a_go_to_wait(self):
        setup = dict(spot=11.0, sma20=10.5, sma50=10.0, pos=70.0, rsi=60.0)
        cheap = analysis.decide("call", snap(iv_far=0.8, rvol=0.8, **setup))
        rich = analysis.decide("call", snap(iv_far=1.6, rvol=0.8, **setup))
        self.assertEqual(cheap.verdict, "go")
        self.assertIsNone(cheap.overlay)
        self.assertEqual(rich.verdict, "wait")
        self.assertEqual(rich.overlay, "rich_iv")
        self.assertEqual(rich.rule_id, "clean_setup")  # the setup read is unchanged

    def test_rich_iv_does_not_upgrade_or_touch_a_skip(self):
        d = analysis.decide("call", snap(pos=95.0, rsi=74.0, iv_far=1.6, rvol=0.8))
        self.assertEqual(d.verdict, "skip")
        self.assertIsNone(d.overlay)

    def test_rich_premium_is_judged_on_the_far_dated_vol_not_the_near_print(self):
        """A hot short-dated print is not evidence of a rich month-out premium.

        SOUN's real 2-day ATM printed 190% against ~100% realized and ELF's
        217% against 54% — both clear RICH_IV_MULTIPLE on the near reading
        alone, which would have made a `go` unreachable for either name on a
        number that measures gamma and event risk, not price. Only the ~30-day
        reading, whose term is close enough to 20-day realized for the ratio to
        mean anything, may drive the downgrade.
        """
        setup = dict(spot=11.0, sma20=10.5, sma50=10.0, pos=70.0, rsi=60.0, rvol=0.8)
        hot_near = analysis.decide("call", snap(iv=2.173, iv_far=0.8, **setup))
        self.assertEqual(hot_near.verdict, "go")
        self.assertIsNone(hot_near.overlay)

        hot_far = analysis.decide("call", snap(iv=0.5, iv_far=1.6, **setup))
        self.assertEqual(hot_far.verdict, "wait")
        self.assertEqual(hot_far.overlay, "rich_iv")

    def test_a_missing_far_iv_cannot_flag_a_premium_rich(self):
        s = snap(spot=11.0, sma20=10.5, sma50=10.0, pos=70.0, rsi=60.0, rvol=0.8)
        del s["iv_far"]
        self.assertFalse(analysis._iv_is_rich(s))

    def test_every_verdict_is_a_known_css_key(self):
        allowed = {"go", "wait", "skip", "mute"}
        for direction in ("call", "put"):
            for pos in (0.0, 25.0, 50.0, 75.0, 100.0):
                for r in (10.0, 35.0, 50.0, 65.0, 90.0):
                    d = analysis.decide(direction, snap(pos=pos, rsi=r))
                    self.assertIn(d.verdict, allowed)
                    self.assertTrue(0 < len(d.vlabel) <= 12, d.vlabel)


class TestProse(unittest.TestCase):
    def test_render_returns_two_tagged_pairs(self):
        d = analysis.decide("call", snap())
        why = analysis.render("call", d, snap())
        self.assertEqual(len(why), 2)
        self.assertEqual(why[0][0], "The read")
        self.assertEqual(why[1][0], "Watch for")
        for _, text in why:
            self.assertTrue(text.strip())

    def test_every_rule_has_a_template_that_renders(self):
        cases = [
            ("call", "over_budget"), ("call", "extended"), ("call", "no_base"),
            ("call", "clean_setup"), ("call", "breakout_pending"), ("call", "chop"),
            ("put", "over_budget"), ("put", "washed_out"), ("put", "breakdown"),
            ("put", "no_short_edge"), ("put", "chop"),
        ]
        for direction, rule_id in cases:
            d = analysis.Decision("wait", "Watch", rule_id, None)
            why = analysis.render(direction, d, snap())
            self.assertEqual(len(why), 2, (direction, rule_id))
            for _, text in why:
                self.assertNotIn("{", text, (direction, rule_id))

    def test_overlay_replaces_the_watch_line(self):
        s = snap(spot=11.0, sma20=10.5, sma50=10.0, pos=70.0, rsi=60.0,
                 iv_far=1.6, rvol=0.8)
        d = analysis.decide("call", s)
        why = analysis.render("call", d, s)
        self.assertIn("rich", why[1][1].lower())

    def test_rich_premium_prose_cites_the_vol_it_compared(self):
        """The sentence must quote the far-dated vol that actually triggered the
        downgrade, not the short-dated print that didn't."""
        s = snap(spot=11.0, sma20=10.5, sma50=10.0, pos=70.0, rsi=60.0,
                 iv=2.5, iv_far=1.6, rvol=0.8)
        d = analysis.decide("call", s)
        line = analysis.render("call", d, s)[1][1]
        self.assertIn("160%", line)   # iv_far
        self.assertIn("80%", line)    # rvol
        self.assertNotIn("250%", line)  # the near print played no part in this

    # --- chop is the catch-all, so its copy must survive the whole range -----
    CHOP_CLAIMS = ("mid-range", "drifting", "two-sided", "no momentum")

    def _chop_lines(self, direction, pos):
        # Flat averages and a distant 20-day high, so no earlier rule can match
        # and `chop` is genuinely the rule under test at every position.
        s = snap(pos=pos, rsi=55.0, spot=9.91, sma20=10.0, sma50=10.0,
                 lo20=8.15, hi20=12.0)
        d = analysis.decide(direction, s)
        self.assertEqual(d.rule_id, "chop", (direction, pos))
        why = analysis.render(direction, d, s)
        return why, why[0][1] + " " + why[1][1]

    def test_chop_copy_makes_no_claim_the_rule_does_not_establish(self):
        """`chop` fires at ANY range position when nothing else matched, so it
        may not assert mid-range, drifting, or two-sided. The published AI card
        said "Mid-range at 93% ... Drifting, no momentum either way" beside a
        card reading In range 93%, +11.0% on the month and resistance 1.4%
        overhead — prose contradicting the numbers printed next to it.
        """
        for direction in ("call", "put"):
            for pos in (5.0, 50.0, 93.0):
                _, blob = self._chop_lines(direction, pos)
                for claim in self.CHOP_CLAIMS:
                    self.assertNotIn(claim, blob.lower(), (direction, pos, claim))

    def test_chop_copy_states_the_actual_range_position(self):
        for direction in ("call", "put"):
            for pos in (5.0, 50.0, 93.0):
                _, blob = self._chop_lines(direction, pos)
                self.assertIn("%d%%" % int(pos), blob, (direction, pos))
                self.assertNotIn("{", blob)

    def test_chop_copy_keeps_its_closing_discipline(self):
        _, call_blob = self._chop_lines("call", 93.0)
        self.assertIn("no setup = no trade", call_blob.lower())
        _, put_blob = self._chop_lines("put", 5.0)
        self.assertIn("chase", put_blob.lower())

    def test_templates_contain_no_hardcoded_price_levels(self):
        """Every price and measured value in the output must come from the snapshot.

        Strip the {placeholders} out of each template; whatever survives is the
        literal copy. Literal copy may not contain a dollar-prefixed number or a
        decimal — those are prices and measurements, and freezing one into copy is
        exactly how the hand-written June text ended up still citing $7.85 months
        later.

        Bare integers are allowed, because window sizes ("the 20-day high") are
        properties of the model rather than of any particular day's data.
        """
        placeholder = re.compile(r"\{[^}]*\}")
        banned = [
            (re.compile(r"\$\s*\d"), "hardcoded dollar amount"),
            (re.compile(r"\d+\.\d"), "hardcoded decimal value"),
        ]
        templates = []
        for pair in analysis.TEMPLATES.values():
            templates.extend(pair)
        templates.extend(analysis.OVERLAY_TEMPLATES.values())
        for text in templates:
            literal = placeholder.sub("", text)
            for pattern, why in banned:
                self.assertIsNone(pattern.search(literal),
                                  "%s in template: %r" % (why, text))

    def test_rendered_prose_reflects_the_actual_snapshot(self):
        s = snap(spot=9.0, sma20=10.0, sma50=11.0, pos=15.0, rsi=35.0)
        d = analysis.decide("call", s)
        why = analysis.render("call", d, s)
        blob = why[0][1] + why[1][1]
        self.assertIn("10.00", blob)   # the real sma20, not a frozen level

    def test_ampersands_are_escaped(self):
        bare = re.compile(r"&(?!amp;|lt;|gt;|#)")
        for pair in analysis.TEMPLATES.values():
            for text in pair:
                self.assertIsNone(bare.search(text),
                                  "unescaped & in template: %r" % text)

    def test_cosmetic_none_does_not_break_rendering(self):
        """chg_1mo is cosmetic and unguarded by decide() — it must not crash render()."""
        s = snap(spot=12.0, sma20=11.0, sma50=10.0, pos=80.0, rsi=62.0, chg_1mo=None)
        d = analysis.decide("put", s)
        self.assertEqual(d.rule_id, "no_short_edge")
        why = analysis.render("put", d, s)
        self.assertEqual(len(why), 2)
        self.assertNotIn("{", why[0][1] + why[1][1])


def result(sym, verdict, rule_id="chop", vlabel="Watch", overlay=None):
    return {"sym": sym, "decision": analysis.Decision(verdict, vlabel, rule_id, overlay)}


class TestWeeklyRead(unittest.TestCase):
    def test_label_names_the_direction(self):
        self.assertEqual(analysis.summarize("call", [result("A", "wait")])[0],
                         "This week · calls")
        self.assertEqual(analysis.summarize("put", [result("A", "wait")])[0],
                         "This week · puts")

    def test_green_names_are_called_out_by_ticker(self):
        results = [result("SOUN", "go", "clean_setup"), result("HIMS", "wait")]
        _, html = analysis.summarize("call", results)
        self.assertIn("SOUN", html)

    def test_all_quiet_says_no_setup(self):
        results = [result(s, "wait") for s in ("A", "B", "C")]
        _, html = analysis.summarize("call", results)
        self.assertIn("no", html.lower())
        self.assertNotIn("None", html)

    def test_all_skip_reads_as_stand_down(self):
        results = [result(s, "skip", "no_short_edge", "Skip") for s in ("A", "B", "C")]
        _, html = analysis.summarize("put", results)
        self.assertTrue(html.strip())
        self.assertNotIn("{", html)

    def test_muted_names_are_reported_as_out_of_budget(self):
        results = [result("ELF", "mute", "over_budget", "Track only"), result("AI", "wait")]
        _, html = analysis.summarize("call", results)
        self.assertIn("ELF", html)

    def test_handles_an_empty_result_set(self):
        label, html = analysis.summarize("call", [])
        self.assertTrue(label)
        self.assertTrue(html.strip())

    def test_output_has_no_unescaped_ampersand_or_stray_braces(self):
        results = [result("SOUN", "go", "clean_setup"), result("ELF", "mute", "over_budget"),
                   result("AI", "skip", "extended"), result("ODD", "wait")]
        for direction in ("call", "put"):
            _, html = analysis.summarize(direction, results)
            self.assertNotIn("{", html)
            self.assertIsNone(re.search(r"&(?!amp;|lt;|gt;|#)", html))

    def test_rich_iv_name_is_named_once_and_not_contradicted(self):
        results = [result("SOUN", "wait", "clean_setup", "Rich", overlay="rich_iv"),
                   result("HIMS", "skip", "no_short_edge", "Skip")]
        _, html = analysis.summarize("call", results)
        self.assertEqual(html.count("SOUN"), 1)
        self.assertNotIn("Nothing's a clean", html)
        self.assertIn("rich", html.lower())

    def test_rich_iv_alongside_a_clean_go(self):
        results = [result("AI", "go", "clean_setup", "Looks solid"),
                   result("SOUN", "wait", "clean_setup", "Rich", overlay="rich_iv")]
        _, html = analysis.summarize("call", results)
        self.assertIn("AI", html)
        self.assertEqual(html.count("SOUN"), 1)

    def test_rich_iv_name_excluded_from_the_watching_list(self):
        results = [result("SOUN", "wait", "clean_setup", "Rich", overlay="rich_iv"),
                   result("ODD", "wait", "chop", "Watch")]
        _, html = analysis.summarize("call", results)
        self.assertEqual(html.count("SOUN"), 1)
        self.assertIn("ODD", html)

    def test_stale_name_gets_a_note_about_its_earlier_verdict(self):
        results = [result("SOUN", "go", "clean_setup", "Looks solid")]
        _, html = analysis.summarize("call", results, stale=["ODD"])
        self.assertIn("ODD", html)
        self.assertIn("didn't refresh today", html)

    def test_multiple_stale_names_use_plural_pronoun(self):
        results = [result("SOUN", "go", "clean_setup", "Looks solid")]
        _, html = analysis.summarize("call", results, stale=["ODD", "AI"])
        self.assertIn("ODD", html)
        self.assertIn("AI", html)
        self.assertIn("their rating", html)

    def test_no_stale_note_when_nothing_is_stale(self):
        results = [result("SOUN", "go", "clean_setup", "Looks solid")]
        _, html = analysis.summarize("call", results)
        self.assertNotIn("didn't refresh today", html)


class TestSnapshotIntegration(unittest.TestCase):
    """The full path from a price series to a rendered verdict, offline."""

    @staticmethod
    def series(values):
        return [float(v) for v in values]

    def build(self, closes, iv=None, iv_far=None):
        rv = analysis.realized_vol(closes)
        spot = closes[-1]
        return dict(
            sym="TEST", spot=spot,
            sma20=analysis.sma(closes, 20), sma50=analysis.sma(closes, 50),
            rsi=analysis.rsi(closes, 14),
            pos=analysis.range_position(closes, 20),
            hi20=max(closes[-20:]), lo20=min(closes[-20:]),
            chg_1mo=analysis.pct_change(closes, 21),
            chg_5d=analysis.pct_change(closes, 5),
            iv=iv if iv is not None else rv,
            iv_far=iv_far if iv_far is not None else rv,
            rvol=rv,
            call_cost=analysis.contract_cost("call", spot, 8, iv or rv),
            put_cost=analysis.contract_cost("put", spot, 8, iv or rv),
            near_dte=8, call_quoted=True, put_quoted=True,
        )

    def test_steady_uptrend_produces_a_renderable_call_verdict(self):
        closes = self.series([10.0 * (1.004 ** i) for i in range(60)])
        s = self.build(closes)
        d = analysis.decide("call", s)
        why = analysis.render("call", d, s)
        self.assertIn(d.verdict, {"go", "wait", "skip", "mute"})
        self.assertEqual(len(why), 2)
        self.assertNotIn("{", why[0][1] + why[1][1])

    def test_sustained_downtrend_is_not_a_call(self):
        closes = self.series([100.0 * (0.985 ** i) for i in range(60)])
        s = self.build(closes)
        self.assertNotEqual(analysis.decide("call", s).verdict, "go")

    def test_expensive_name_is_muted_on_both_sides(self):
        closes = self.series([90.0 + i * 0.1 for i in range(60)])
        s = self.build(closes, iv=1.15)
        self.assertEqual(analysis.decide("call", s).verdict, "mute")
        self.assertEqual(analysis.decide("put", s).verdict, "mute")

    def test_every_name_in_the_committed_data_renders(self):
        """Guards against a snapshot key the templates reference but refresh omits."""
        import json
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "data.json")) as f:
            data = json.load(f)
        for name in data["names"]:
            closes = self.series([name["spot"]] * 60)
            s = self.build(closes)
            s["sym"] = name["sym"]
            for direction in ("call", "put"):
                d = analysis.decide(direction, s)
                why = analysis.render(direction, d, s)
                self.assertEqual(len(why), 2, name["sym"])
                self.assertNotIn("{", why[0][1] + why[1][1])


if __name__ == "__main__":
    unittest.main()


class TestRangeExtremes(unittest.TestCase):
    """The catch-all used to tell you to wait for a level you were already at."""

    def test_call_at_range_high_without_trend_gets_its_own_rule(self):
        s = snap(spot=7.16, sma20=6.80, sma50=7.00, pos=100.0, rsi=63.0,
                 hi20=7.16, lo20=5.70)
        d = analysis.decide("call", s)
        self.assertEqual(d.rule_id, "at_range_high")
        self.assertEqual(d.verdict, "wait")

    def test_at_range_high_prose_does_not_await_the_current_price(self):
        s = snap(spot=7.16, sma20=6.80, sma50=7.00, pos=100.0, rsi=63.0,
                 hi20=7.16, lo20=5.70)
        d = analysis.decide("call", s)
        blob = " ".join(t for _, t in analysis.render("call", d, s))
        self.assertNotIn("break of $7.16", blob)
        self.assertIn("6.80", blob)

    def test_breakout_pending_still_wins_when_the_trend_has_turned(self):
        s = snap(spot=7.16, sma20=7.00, sma50=6.80, pos=100.0, rsi=63.0, hi20=7.16)
        self.assertEqual(analysis.decide("call", s).rule_id, "breakout_pending")

    def test_extended_still_wins_when_overbought(self):
        s = snap(spot=7.16, sma20=6.80, sma50=7.00, pos=100.0, rsi=76.0, hi20=7.16)
        self.assertEqual(analysis.decide("call", s).rule_id, "extended")

    def test_put_at_range_low_gets_its_own_rule(self):
        s = snap(spot=5.70, sma20=6.50, sma50=6.20, pos=0.0, rsi=38.0,
                 hi20=7.16, lo20=5.70)
        d = analysis.decide("put", s)
        self.assertEqual(d.rule_id, "at_range_low")
        self.assertEqual(d.verdict, "skip")

    def test_washed_out_still_wins_when_oversold(self):
        s = snap(spot=5.70, sma20=6.50, sma50=6.20, pos=0.0, rsi=25.0, lo20=5.70)
        self.assertEqual(analysis.decide("put", s).rule_id, "washed_out")

    def test_both_new_templates_render_without_placeholders(self):
        for direction, rule in (("call", "at_range_high"), ("put", "at_range_low")):
            d = analysis.Decision("wait", "x", rule, None)
            why = analysis.render(direction, d, snap())
            self.assertEqual(len(why), 2)
            self.assertNotIn("{", why[0][1] + why[1][1])
