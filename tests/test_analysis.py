import math
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


if __name__ == "__main__":
    unittest.main()
