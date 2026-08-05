"""Offline tests for refresh.py's pure helpers.

refresh.py imports yfinance only inside history()/near_quotes()/atm_iv(), so
`import refresh` works fine without yfinance installed, and _num/_priced/
_resolve_iv can be exercised directly with plain numbers and dict/tuple
fixtures — no network, no pandas, no yfinance required.
"""
import unittest

import analysis
import refresh


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
        self.assertLess(bs_cost, analysis.BUDGET * analysis.BUDGET_MULTIPLE)
        self.assertGreater(priced, analysis.BUDGET * analysis.BUDGET_MULTIPLE)


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


if __name__ == "__main__":
    unittest.main()
