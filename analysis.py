#!/usr/bin/env python3
"""Deterministic analysis for the My Plays dashboard.

Pure functions only: no network, no filesystem, no clock. Everything the
analysis needs arrives as an argument, which is what makes it testable
offline and reproducible for a given input series.

Sections:
  1. Indicators and option pricing
  2. The verdict ladder
  3. Prose templates
  4. The weekly read
"""
import math
from collections import namedtuple

# --- constants -------------------------------------------------------------
# Fitted judgments, not derived facts. See the spec for why each is set here.
BUDGET = 100.0            # the dollar budget the dashboard is built around
BUDGET_MULTIPLE = 3.0     # over this multiple of budget, a name is "Track only"
RICH_IV_MULTIPLE = 1.35   # implied over realized by this much = rich premium
IV_SANITY_MULTIPLE = 2.5  # implied over realized by this much = bad fill, discard

VOL_FLOOR = 0.1           # annualized; guards flat/degenerate series


# --- 1. indicators ---------------------------------------------------------
def sma(series, n):
    """Mean of the last n values. None if the series is shorter than n."""
    if len(series) < n:
        return None
    return sum(series[-n:]) / float(n)


def rsi(series, n=14):
    """RSI over the last n periods, using simple (not Wilder-smoothed) averages.

    Returns 0-100, or None if the series is too short. Three cases:
    - 50.0 when there is no movement in either direction (flat series)
    - 100.0 when there are gains but no losses (uptrend)
    - 0.0 when there are losses but no gains (downtrend)
    """
    if len(series) < n + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(series) - n, len(series)):
        delta = series[i] - series[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if gains == 0 and losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    if gains == 0:
        return 0.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def range_position(series, n=20):
    """Where the last close sits in the n-day high/low band, as 0-100.

    Returns 50.0 for a flat band (no meaningful position), None if too short.
    """
    if len(series) < n:
        return None
    window = series[-n:]
    low, high = min(window), max(window)
    if high <= low:
        return 50.0
    return (series[-1] - low) / (high - low) * 100.0


def realized_vol(series):
    """Annualized realized vol from the last 20 daily log returns."""
    rets = [math.log(series[i] / series[i - 1])
            for i in range(1, len(series)) if series[i - 1] > 0]
    if len(rets) < 5:
        return 0.8
    window = rets[-20:]
    mean = sum(window) / len(window)
    var = sum((r - mean) ** 2 for r in window) / len(window)
    return max(VOL_FLOOR, math.sqrt(var) * math.sqrt(252))


def pct_change(series, n):
    """Percent change over the last n bars. None if too short or prior is zero."""
    if len(series) < n + 1:
        return None
    prior = series[-(n + 1)]
    if prior == 0:
        return None
    return (series[-1] - prior) / prior * 100.0


# --- 1b. option pricing (for the budget gate) ------------------------------
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, iv, r=0.04):
    """Black-Scholes call. Falls back to intrinsic at zero time or zero vol."""
    if T <= 0 or iv <= 0:
        return max(0.0, S - K)
    v = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / v
    return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d1 - v)


def bs_put(S, K, T, iv, r=0.04):
    """Black-Scholes put, via put-call parity."""
    if T <= 0 or iv <= 0:
        return max(0.0, K - S)
    return bs_call(S, K, T, iv, r) - S + K * math.exp(-r * T)


def atm_strike(spot):
    """The ATM strike index.html would display: round(spot), floored at 0.5."""
    return max(0.5, float(round(spot)))


def contract_cost(direction, spot, dte, iv):
    """Dollar cost of one ATM contract at the given days-to-expiry."""
    K = atm_strike(spot)
    T = dte / 365.0
    premium = bs_put(spot, K, T, iv) if direction == "put" else bs_call(spot, K, T, iv)
    return premium * 100.0


# --- 2. the verdict ladder -------------------------------------------------
Decision = namedtuple("Decision", "verdict vlabel rule_id overlay")


def cost_for(direction, snap):
    """The ATM contract cost for this direction, in dollars."""
    return snap["put_cost"] if direction == "put" else snap["call_cost"]


def _iv_is_rich(snap):
    rvol = snap.get("rvol") or 0.0
    iv = snap.get("iv") or 0.0
    return rvol > 0 and iv > rvol * RICH_IV_MULTIPLE


def _call_rule(s):
    spot, sma20, sma50 = s["spot"], s["sma20"], s["sma50"]
    pos, rsi_v, hi20 = s["pos"], s["rsi"], s["hi20"]

    if pos >= 90.0 and rsi_v >= 70.0:
        return Decision("skip", "Extended", "extended", None)
    if spot < sma20 < sma50 and pos <= 25.0:
        return Decision("wait", "No base", "no_base", None)
    if spot > sma20 and sma20 >= sma50 and 55.0 <= pos <= 85.0 and 50.0 <= rsi_v <= 68.0:
        return Decision("go", "Looks solid", "clean_setup", None)
    if hi20 > 0 and (hi20 - spot) / hi20 * 100.0 <= 3.0 and sma20 >= sma50 and rsi_v < 70.0:
        return Decision("wait", "Watch", "breakout_pending", None)
    return Decision("wait", "Watch", "chop", None)


def _put_rule(s):
    spot, sma20, sma50 = s["spot"], s["sma20"], s["sma50"]
    pos, rsi_v = s["pos"], s["rsi"]

    if pos <= 10.0 and rsi_v <= 30.0:
        return Decision("wait", "Late", "washed_out", None)
    if spot < sma20 < sma50 and pos <= 40.0 and 30.0 <= rsi_v <= 45.0:
        return Decision("go", "Looks solid", "breakdown", None)
    if spot > sma20 > sma50:
        return Decision("skip", "Skip", "no_short_edge", None)
    return Decision("skip", "Skip", "chop", None)


def decide(direction, snap):
    """Pick a verdict for one name in one direction. First matching rule wins.

    Returns a Decision. `rule_id` selects the prose template; `overlay` carries
    the rich-premium downgrade separately so the setup's own read survives it.
    """
    if cost_for(direction, snap) > BUDGET * BUDGET_MULTIPLE:
        return Decision("mute", "Track only", "over_budget", None)

    d = _call_rule(snap) if direction == "call" else _put_rule(snap)

    # Direction is right but you'd be overpaying to express it.
    if d.verdict == "go" and _iv_is_rich(snap):
        return Decision("wait", "Rich", d.rule_id, "rich_iv")
    return d
