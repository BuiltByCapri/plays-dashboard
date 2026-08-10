"""Offline tests for refresh.py's pure helpers.

refresh.py imports yfinance only inside history()/option_data(), so
`import refresh` works fine without yfinance installed, and _num/_priced/
_resolve_iv/_quoted/apply_snapshot can be exercised directly with plain numbers
and dict/tuple fixtures — no network, no pandas, no yfinance required.
"""
import json
import os
import re
import unittest

import analysis
import refresh

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestNum(unittest.TestCase):
    def test_reads_a_numeric_field(self):
        self.assertEqual(refresh._num({"bid": 1.5}, "bid"), 1.5)

    def test_missing_field_is_none(self):
        self.assertIsNone(refresh._num({}, "bid"))

    def test_pandas_style_nan_becomes_none(self):
        # A yfinance/pandas row reads a missing cell as float NaN, not None.
        # float("nan") reproduces that shape without requiring pandas locally.
        self.assertIsNone(refresh._num({"bid": float("nan")}, "bid"))

    def test_non_numeric_value_is_none(self):
        self.assertIsNone(refresh._num({"bid": "n/a"}, "bid"))


class TestPriced(unittest.TestCase):
    def test_prefers_the_quoted_mid_when_liquid(self):
        # SOUN's real 2-day ATM quote: iv, bid, ask, open interest
        quote = (0.62, 0.40, 0.42, 7929)
        cost = refresh._priced("call", quote, spot=6.43, dte=2, iv=0.58)
        self.assertAlmostEqual(cost, analysis.quote_cost(0.40, 0.42))

    def test_falls_back_to_black_scholes_when_illiquid(self):
        # ODD's real 2-day ATM quote: a dead market (1 open interest)
        quote = (1.72, 0.30, 1.80, 1)
        cost = refresh._priced("call", quote, spot=15.22, dte=2, iv=0.74)
        self.assertAlmostEqual(cost, analysis.contract_cost("call", 15.22, 2, 0.74))

    def test_elf_regression_quote_prices_past_the_gate_where_bs_would_not(self):
        """The exact ELF regression: refresh._priced must key off the real
        quote, not recompute a cheaper Black-Scholes estimate from a
        realized-vol fallback — that gap is what let ELF's budget gate slip
        through before the liquidity design change."""
        quote = (2.173, 5.80, 8.10, 93)  # ELF's real near-dated ATM quote
        spot, dte, iv = 86.37, 2, 0.535  # 0.535 = the realized-vol fallback iv
        bs_cost = analysis.contract_cost("call", spot, dte, iv)
        priced = refresh._priced("call", quote, spot, dte, iv)

        self.assertAlmostEqual(priced, analysis.quote_cost(5.80, 8.10))

        # Pinned to the gate in force when this shipped ($300), not the live
        # constant. The defect is the 4x gap between the model and the market,
        # which fools any gate between them — true regardless of how the
        # threshold is tuned later.
        LEGACY_GATE = 300.0
        self.assertLess(bs_cost, LEGACY_GATE)
        self.assertGreater(priced, LEGACY_GATE)
        self.assertGreater(priced, bs_cost * 4)


class TestResolveIv(unittest.TestCase):
    def test_liquid_quote_keeps_its_own_iv(self):
        quote = (1.898, 0.40, 0.42, 7929)  # SOUN
        self.assertAlmostEqual(refresh._resolve_iv(quote, realized=0.58), 1.898)

    def test_illiquid_quote_falls_back_to_realized(self):
        quote = (1.715, 0.30, 1.80, 1)  # ODD: dead market
        self.assertAlmostEqual(refresh._resolve_iv(quote, realized=0.74), 0.74)

    def test_out_of_band_iv_falls_back_to_realized_even_if_liquid(self):
        quote = (5.0, 0.40, 0.42, 7929)  # absurd iv, but a liquid-looking quote
        self.assertAlmostEqual(refresh._resolve_iv(quote, realized=0.58), 0.58)


class TestQuotedFlag(unittest.TestCase):
    """_quoted() must agree exactly with what _priced() actually did."""

    def test_true_when_priced_off_the_quote(self):
        quote = (0.62, 0.40, 0.42, 7929)   # SOUN: liquid
        self.assertTrue(refresh._quoted(quote))
        self.assertAlmostEqual(refresh._priced("call", quote, 6.43, 2, 0.58),
                               analysis.quote_cost(0.40, 0.42))

    def test_false_when_priced_off_the_model(self):
        quote = (1.72, 0.30, 1.80, 1)      # ODD: dead market
        self.assertFalse(refresh._quoted(quote))
        self.assertAlmostEqual(refresh._priced("call", quote, 15.22, 2, 0.74),
                               analysis.contract_cost("call", 15.22, 2, 0.74))


class TestIvTargets(unittest.TestCase):
    """ivNear is a display/fair-value term, not the tradeable-Friday term."""

    def test_display_iv_targets_about_a_week_out(self):
        # index.html prices everything out to 20 days off ivNear, so a 1-4 day
        # earnings-loaded print must not be what gets published there.
        self.assertEqual(refresh.DISPLAY_IV_DTE, 8)
        self.assertEqual(refresh.FAR_IV_DTE, 30)

    def test_near_friday_can_be_much_shorter_than_the_display_target(self):
        import datetime
        wednesday = datetime.date(2026, 8, 5)
        self.assertEqual(refresh.days_to_next_friday(wednesday), 2)
        self.assertLess(refresh.days_to_next_friday(wednesday), refresh.DISPLAY_IV_DTE)

    def test_nearest_expiry_picks_the_closest_listed_date(self):
        import datetime
        today = datetime.date.today()
        exps = [(today + datetime.timedelta(days=d)).isoformat()
                for d in (2, 9, 16, 30, 58)]
        self.assertEqual(refresh._nearest_expiry(exps, 2), exps[0])
        self.assertEqual(refresh._nearest_expiry(exps, refresh.DISPLAY_IV_DTE), exps[1])
        self.assertEqual(refresh._nearest_expiry(exps, refresh.FAR_IV_DTE), exps[3])


def _snap(**kw):
    base = dict(
        sym="ELF", spot=86.37, sma20=79.27, sma50=69.20, rsi=69.3, pos=90.6,
        hi20=87.84, lo20=72.25, chg_1mo=14.6, chg_5d=1.0,
        iv=0.61, iv_far=0.535, rvol=0.535,
        near_dte=2, near_expiry="2026-08-07",
        call_cost=695.0, put_cost=533.0, call_quoted=True, put_quoted=True,
    )
    base.update(kw)
    return base


class TestApplySnapshot(unittest.TestCase):
    def test_publishes_the_quoted_near_costs(self):
        """The contract row and the "why" text must price the same contract the
        same way. Before this, refresh computed call_cost from the real quoted
        mid, dropped it, and index.html re-derived its own Black-Scholes price
        for the row — ELF's card said $695 in prose above a row reading ~$572.
        """
        name = {"sym": "ELF", "yf": "ELF"}
        refresh.apply_snapshot(name, _snap())
        self.assertEqual(name["near"]["expiry"], "2026-08-07")
        self.assertEqual(name["near"]["dte"], 2)
        self.assertAlmostEqual(name["near"]["call_cost"], 695.0)
        self.assertAlmostEqual(name["near"]["put_cost"], 533.0)
        self.assertTrue(name["near"]["call_quoted"])
        self.assertTrue(name["near"]["put_quoted"])
        # the same number the prose cites
        self.assertIn("$695", name["call"]["why"][0][1])

    def test_records_a_modelled_cost_as_unquoted(self):
        """ODD's near call really quotes $0.30/$1.80 on 1 open interest. The
        model fallback published "~$0.45 · fits $100" with nothing behind it,
        so the flag that lets the page decline that badge must be published."""
        name = {"sym": "ODD", "yf": "ODD"}
        refresh.apply_snapshot(name, _snap(sym="ODD", call_quoted=False,
                                          put_quoted=False, call_cost=45.0))
        self.assertFalse(name["near"]["call_quoted"])
        self.assertFalse(name["near"]["put_quoted"])
        self.assertAlmostEqual(name["near"]["call_cost"], 45.0)

    def test_drops_the_frozen_june_anchors(self):
        name = {"sym": "ELF", "yf": "ELF",
                "anchor": {"K": 50, "dte": 8, "prem": 3.55},
                "anchorF": {"dte": 36, "prem": 6.63}}
        refresh.apply_snapshot(name, _snap())
        self.assertNotIn("anchor", name)
        self.assertNotIn("anchorF", name)

    def test_publishes_both_iv_terms(self):
        name = {"sym": "ELF", "yf": "ELF"}
        refresh.apply_snapshot(name, _snap(iv=0.61, iv_far=0.535))
        self.assertAlmostEqual(name["ivNear"], 0.61)
        self.assertAlmostEqual(name["ivFar"], 0.535)


class TestPublishedPayload(unittest.TestCase):
    """The committed data.json is what the page actually reads."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "data.json")) as f:
            cls.data = json.load(f)

    def test_every_name_publishes_its_near_costs(self):
        for name in self.data["names"]:
            near = name.get("near")
            self.assertIsInstance(near, dict, name["sym"])
            for key in ("dte", "call_cost", "put_cost"):
                self.assertIsInstance(near[key], (int, float), (name["sym"], key))
            for key in ("call_quoted", "put_quoted"):
                self.assertIsInstance(near[key], bool, (name["sym"], key))

    def test_every_name_publishes_a_well_formed_iso_expiry(self):
        """index.html matches the published quote to the contract it is showing
        by this string, so a malformed one silently un-matches every name."""
        import datetime
        for name in self.data["names"]:
            expiry = name["near"]["expiry"]
            self.assertIsInstance(expiry, str, name["sym"])
            parsed = datetime.date.fromisoformat(expiry)   # raises if malformed
            self.assertEqual(parsed.isoformat(), expiry, name["sym"])
            self.assertEqual(parsed.weekday(), 4, (name["sym"], expiry))  # a Friday

    def test_the_published_expiry_is_consistent_with_its_day_count(self):
        import datetime
        updated = datetime.date.fromisoformat(self.data["updated"])
        for name in self.data["names"]:
            near = name["near"]
            delta = (datetime.date.fromisoformat(near["expiry"]) - updated).days
            self.assertEqual(delta, near["dte"], name["sym"])

    def test_no_frozen_anchors_survive(self):
        for name in self.data["names"]:
            self.assertNotIn("anchor", name, name["sym"])
            self.assertNotIn("anchorF", name, name["sym"])

    def test_updated_at_is_a_timezone_aware_timestamp_matching_updated(self):
        """The page renders `updated_at` as the exact refresh moment in the
        viewer's own timezone, so it must be an aware instant (not a naive
        local time that would shift under conversion) and must land on the
        same trading-session date as `updated` -- allowing one calendar day
        of slack, because `updated` is stamped from the *local* system clock
        (UTC on the GitHub runner, but whatever the host's TZ is for a
        manual/local run) while `updated_at` is always UTC, and those two
        can legitimately disagree by a day right around midnight UTC."""
        import datetime
        updated_at = self.data["updated_at"]
        self.assertIsInstance(updated_at, str)
        parsed = datetime.datetime.fromisoformat(updated_at)
        self.assertIsNotNone(parsed.tzinfo)
        parsed_utc = parsed.astimezone(datetime.timezone.utc)
        updated = datetime.date.fromisoformat(self.data["updated"])
        self.assertLessEqual(abs((parsed_utc.date() - updated).days), 1)

    def test_reads_are_the_two_element_pairs_the_page_expects(self):
        for direction in ("call", "put"):
            read = self.data["reads"][direction]
            self.assertIsInstance(read, list)
            self.assertEqual(len(read), 2)
            self.assertTrue(all(isinstance(x, str) and x.strip() for x in read))


class TestPageContract(unittest.TestCase):
    """Source-level guards on index.html.

    There is no JS runtime in this repo (no node, no browser), so these assert
    on the source of the two seams a backend change can silently break. They
    are regression anchors, not a substitute for running the page.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "index.html")) as f:
            cls.src = f.read()

    def _fn(self, marker):
        """One function's source, from `marker` to its balancing brace.

        Keeps these assertions off the surrounding comments — a comment that
        merely *names* the thing being avoided shouldn't fail the test that
        checks it isn't used.
        """
        self.assertIn(marker, self.src)
        rest = self.src.split(marker, 1)[1]
        depth, opened, out = 0, False, []
        for ch in rest:
            out.append(ch)
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth == 0:
                    break
        return "".join(out)

    def test_reads_guard_checks_shape_not_just_truthiness(self):
        """A present-but-malformed reads block used to render the literal
        string "undefined" where the week's read belongs."""
        self.assertIn("Array.isArray(r)", self.src)
        self.assertIn("r.length===2", self.src)
        self.assertIn("okRead(p.reads.call)", self.src)
        self.assertIn("okRead(p.reads.put)", self.src)
        # the old truthiness-only guard must be gone
        self.assertNotIn("p.reads&&p.reads.call&&p.reads.put", self.src)

    def test_anchor_is_never_dereferenced_unconditionally(self):
        """The June anchors are gone from data.json; the fallback that reads
        them must tolerate their absence rather than throw on d.anchor.K."""
        for line in self.src.splitlines():
            if "d.anchor." in line:
                guarded = ("&&d.anchor)" in line or "(d.anchor&&" in line
                           or "d.anchor?" in line)
                self.assertTrue(guarded, "unguarded d.anchor deref: %r" % line.strip())

    def test_near_row_prefers_the_published_cost(self):
        self.assertIn("function nearCost(", self.src)
        self.assertIn("n[DIR+'_cost']", self.src)
        self.assertIn("n[DIR+'_quoted']===true", self.src)

    def test_the_quote_is_matched_on_expiry_date_not_a_day_count(self):
        """A day count is not a contract identity.

        The job runs weekdays at 13:30 UTC, so a Friday run publishes dte 7 for
        an expiry a Saturday visitor computes as 6 days out — the same contract.
        Comparing day counts rejected that still-valid quote and put a modelled
        price back on the card beside the quoted one in the prose, every weekend
        and every US morning before the action runs.
        """
        near_cost = self._fn("function nearCost(")
        self.assertIn("n.expiry!==SHORT_ISO", near_cost)
        self.assertIn("const SHORT_ISO=isoLocal(SHORT)", self.src)
        # the day-count comparison must be gone
        self.assertNotIn("n.dte", near_cost)

    def test_expiry_is_formatted_from_local_date_parts(self):
        """toISOString() on a Date built at local noon shifts the day for
        anyone far enough west of UTC, which would silently un-match every
        quote for those visitors."""
        iso_local = self._fn("function isoLocal(")
        self.assertIn("dt.getFullYear()", iso_local)
        self.assertIn("dt.getMonth()+1", iso_local)
        self.assertIn("dt.getDate()", iso_local)
        self.assertNotIn("toISOString", iso_local)

    def test_a_non_matching_expiry_still_falls_back_to_the_muted_estimate(self):
        """The genuine-mismatch branch must keep blocker 3's safety property:
        nearCost returns null, contractsFor takes the Black-Scholes path, and
        `quoted` is false — so the badge is `na`/`est`, never "fits $100"."""
        self.assertIn("return null", self._fn("function nearCost("))
        # null from nearCost => model price and quoted=false. Asserted on the
        # shared helper rather than a literal line of contractsFor, so renaming
        # a local can't fail the build while the behaviour is unchanged.
        near_fig = self._fn("function nearFig(")
        self.assertIn("nearCost(d)", near_fig)
        self.assertIn("bsPrice(", near_fig)
        self.assertIn("quoted: !!(q&&q.quoted)", near_fig)
        # contractsFor must take its near-row figure from that helper rather
        # than recomputing one, so the row and the strip can never disagree.
        contracts = self._fn("function contractsFor(")
        self.assertIn("nearFig(d)", contracts)

    def test_unquoted_rows_do_not_claim_a_fit(self):
        """"fits $100" may only appear on a branch gated by `quoted`."""
        self.assertIn("quoted?(cost<=105?'ok':'no'):'na'", self.src)
        self.assertIn("'est $'+cost.toFixed(0)", self.src)
        for hit in re.finditer(r"fits \$100", self.src):
            line = self.src[:hit.start()].rsplit("\n", 1)[-1]
            self.assertIn("quoted?", line)


if __name__ == "__main__":
    unittest.main()
