# Auto-Generated Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard's verdicts, "why" text, and weekly read regenerate from live price data every morning, so the analysis can never contradict the prices shown next to it.

**Architecture:** A new `analysis.py` holds pure functions — indicators, a first-match-wins verdict ladder, rule-keyed prose templates, and a weekly-read summarizer. It never touches the network or disk. `refresh.py` keeps sole ownership of I/O: it fetches, builds a snapshot dict per name, calls into `analysis.py`, and writes `data.json`. `index.html` stops holding analysis copy and reads the weekly text from `data.json`.

**Tech Stack:** Python 3.11 (stdlib only for `analysis.py`; `yfinance` for fetching, already a dependency), stdlib `unittest`, GitHub Actions, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-08-05-auto-analysis-design.md`

## Global Constraints

- **Python 3.9-compatible syntax.** The workflow pins 3.11 but the maintainer's local Python is 3.9.6. No `match`, no `X | Y` type unions, no `dict[str, int]` builtin generics in annotations.
- **`analysis.py` imports stdlib only** — `math` and `collections` are the entire allowance. No `yfinance`, no file or network access, no `datetime.date.today()`. Dates and fetched values arrive as arguments.
- **No new pip dependency.** Tests use stdlib `unittest`, run via `python -m unittest discover -s tests -t .`.
- **Verdict keys are exactly `go`, `wait`, `skip`, `mute`.** `index.html` maps these to CSS classes (`.v-go`, `.pip.wait`, etc.); any other string renders an unstyled pip.
- **`vlabel` is free display text** shown inside the verdict chip. Keep it to one or two words — the chip does not wrap.
- **All money and indicator values in prose come from the snapshot**, never from a template literal. Enforced mechanically by a test in Task 3.
- **`why` entries are `[tag, html]` pairs**, exactly two per direction, tagged `"The read"` and `"Watch for"` — `index.html` renders `w[0]` as a `.tag` span and `w[1]` as inline HTML.
- **Escape `&` as `&amp;`** in any generated HTML string, since these are injected via `innerHTML`.
- **Constants live at the top of `analysis.py`:** `BUDGET = 100.0`, `BUDGET_MULTIPLE = 3.0`, `RICH_IV_MULTIPLE = 1.35`, `IV_SANITY_MULTIPLE = 2.5`.

## Deviation from the spec

The spec's data-flow section lists volume as a fetched signal and says `refresh.py`
gains "the volume series alongside the closes it already downloads." **No rule in the
verdict ladder uses volume**, so fetching it would be dead data. It is omitted. Prose
still says things like "on volume" as advice to the reader, which needs no volume data.

If a volume-based rule is added later, `history()` is the one place to change.

---

### Task 1: Indicators and option pricing

**Files:**
- Create: `analysis.py`
- Create: `tests/test_analysis.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `sma(series, n)` → `float` or `None`
  - `rsi(series, n=14)` → `float` or `None`
  - `range_position(series, n=20)` → `float` (0–100) or `None`
  - `realized_vol(series)` → `float`
  - `pct_change(series, n)` → `float` or `None`
  - `bs_call(S, K, T, iv, r=0.04)` → `float`
  - `bs_put(S, K, T, iv, r=0.04)` → `float`
  - `contract_cost(direction, spot, dte, iv)` → `float` (dollars for one contract)
  - Constants `BUDGET`, `BUDGET_MULTIPLE`, `RICH_IV_MULTIPLE`, `IV_SANITY_MULTIPLE`

Every indicator returns `None` rather than raising when the series is too short. `refresh.py` treats a `None` in the snapshot as "cannot analyze this name" and keeps the previous verdict.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analysis.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analysis'`

- [ ] **Step 3: Write the implementation**

Create `analysis.py`:

```python
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

    Returns 0-100, or None if the series is too short. 100 when there are no
    losses in the window, 0 when there are no gains.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, 19 tests

- [ ] **Step 5: Commit**

```bash
git add analysis.py tests/test_analysis.py
git commit -m "feat: indicators and option pricing for generated analysis"
```

---

### Task 2: The verdict ladder

**Files:**
- Modify: `analysis.py` (append section 2)
- Modify: `tests/test_analysis.py` (append `TestVerdicts`)

**Interfaces:**
- Consumes: `BUDGET`, `BUDGET_MULTIPLE`, `RICH_IV_MULTIPLE` from Task 1.
- Produces:
  - `Decision = namedtuple("Decision", "verdict vlabel rule_id overlay")`
  - `decide(direction, snap)` → `Decision`
  - `cost_for(direction, snap)` → `float`
  - `snapshot(...)` keys, which every later task reads:
    `sym`, `spot`, `sma20`, `sma50`, `rsi`, `pos`, `hi20`, `lo20`,
    `chg_1mo`, `chg_5d`, `iv`, `rvol`, `call_cost`, `put_cost`, `near_dte`

**Note on the spec:** the spec described `decide` returning a 3-tuple
`(verdict, vlabel, rule_id)`. It returns a 4-field `Decision` instead. The extra
`overlay` field carries the rich-premium downgrade separately from `rule_id`,
because `rule_id` selects the prose template — folding the overlay into it would
either need a duplicate template per rule or produce a "looks solid" sentence
under a "wait" pip.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis.py`, above the `if __name__` block:

```python
def snap(**kw):
    """A mid-range, unremarkable snapshot. Override only what a test cares about."""
    base = dict(
        sym="TEST", spot=10.0, sma20=10.0, sma50=10.0, rsi=50.0, pos=50.0,
        hi20=12.0, lo20=8.0, chg_1mo=0.0, chg_5d=0.0, iv=0.8, rvol=0.8,
        call_cost=50.0, put_cost=50.0, near_dte=8,
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
        cheap = analysis.decide("call", snap(iv=0.8, rvol=0.8, **setup))
        rich = analysis.decide("call", snap(iv=1.6, rvol=0.8, **setup))
        self.assertEqual(cheap.verdict, "go")
        self.assertIsNone(cheap.overlay)
        self.assertEqual(rich.verdict, "wait")
        self.assertEqual(rich.overlay, "rich_iv")
        self.assertEqual(rich.rule_id, "clean_setup")  # the setup read is unchanged

    def test_rich_iv_does_not_upgrade_or_touch_a_skip(self):
        d = analysis.decide("call", snap(pos=95.0, rsi=74.0, iv=1.6, rvol=0.8))
        self.assertEqual(d.verdict, "skip")
        self.assertIsNone(d.overlay)

    def test_every_verdict_is_a_known_css_key(self):
        allowed = {"go", "wait", "skip", "mute"}
        for direction in ("call", "put"):
            for pos in (0.0, 25.0, 50.0, 75.0, 100.0):
                for r in (10.0, 35.0, 50.0, 65.0, 90.0):
                    d = analysis.decide(direction, snap(pos=pos, rsi=r))
                    self.assertIn(d.verdict, allowed)
                    self.assertTrue(0 < len(d.vlabel) <= 12, d.vlabel)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL with `AttributeError: module 'analysis' has no attribute 'decide'`

- [ ] **Step 3: Write the implementation**

Add `from collections import namedtuple` to the imports at the top of `analysis.py`,
then append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, 35 tests

- [ ] **Step 5: Commit**

```bash
git add analysis.py tests/test_analysis.py
git commit -m "feat: deterministic verdict ladder for calls and puts"
```

---

### Task 3: Prose templates

**Files:**
- Modify: `analysis.py` (append section 3)
- Modify: `tests/test_analysis.py` (append `TestProse`)

**Interfaces:**
- Consumes: `Decision` and the snapshot keys from Task 2.
- Produces:
  - `TEMPLATES` — `dict` keyed `(direction, rule_id)` → `(read_text, watch_text)`
  - `OVERLAY_TEMPLATES` — `dict` keyed `overlay` → `watch_text`
  - `render(direction, decision, snap)` → `[["The read", str], ["Watch for", str]]`

The `no digits in template literals` test is the mechanical enforcement of the spec's
"no hardcoded levels" rule — the failure being fixed is a June price frozen into copy.

- [ ] **Step 1: Write the failing tests**

Add `import re` to the imports at the top of `tests/test_analysis.py`, then append:

```python
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
        s = snap(spot=11.0, sma20=10.5, sma50=10.0, pos=70.0, rsi=60.0, iv=1.6, rvol=0.8)
        d = analysis.decide("call", s)
        why = analysis.render("call", d, s)
        self.assertIn("rich", why[1][1].lower())

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL with `AttributeError: module 'analysis' has no attribute 'TEMPLATES'`

- [ ] **Step 3: Write the implementation**

Append to `analysis.py`:

```python
# --- 3. prose --------------------------------------------------------------
# Every number is a {placeholder} filled from the snapshot. Literal digits are
# banned here and a test enforces it: a hardcoded level goes stale the moment
# the price moves, which is the bug this whole module exists to kill.
TEMPLATES = {
    ("call", "over_budget"): (
        "An ATM contract runs about <b>${cost:.0f}</b> — {mult:.1f}× the ${budget:.0f} "
        "you're working with. The setup doesn't matter if you can't buy it.",
        "Tracking only. Shares or a spread are the way in here, not a straight call.",
    ),
    ("call", "extended"): (
        "Ripped to <b>{pos:.0f}%</b> of its 20-day range with RSI at {rsi:.0f}. "
        "That's the top of a move, not the start of one.",
        "Chasing here is how you buy the high. Let it cool back toward ${sma20:.2f} "
        "before you look again.",
    ),
    ("call", "no_base"): (
        "Under both averages (${sma20:.2f} / ${sma50:.2f}) and sitting at "
        "<b>{pos:.0f}%</b> of its range, RSI {rsi:.0f}. Still falling, no buyer "
        "showing up yet.",
        "A real base plus a reclaim of ${sma20:.2f} on volume. Not yet — don't catch it.",
    ),
    ("call", "clean_setup"): (
        "Above the 20-day (${sma20:.2f}) with the longer trend behind it, "
        "<b>{pos:.0f}%</b> of range, RSI {rsi:.0f}. That's a real setup, not a hope.",
        "Wants to hold ${sma20:.2f}. Lose that and the setup's void — that's your line, "
        "not a feeling.",
    ),
    ("call", "breakout_pending"): (
        "Pressing the 20-day high at <b>${hi20:.2f}</b>, about {dist_hi:.1f}% away, "
        "with the trend up and RSI {rsi:.0f}. Coiled, not broken out.",
        "Take the break of ${hi20:.2f} on volume — not the anticipation of it.",
    ),
    ("call", "chop"): (
        "Mid-range at <b>{pos:.0f}%</b> between ${lo20:.2f} and ${hi20:.2f}, "
        "RSI {rsi:.0f}. Drifting, no momentum either way.",
        "A clean break of ${hi20:.2f} would start something. Until then, "
        "no setup = no trade.",
    ),
    ("put", "over_budget"): (
        "An ATM put runs about <b>${cost:.0f}</b> — {mult:.1f}× the ${budget:.0f} "
        "you're working with. Can't express it at this size.",
        "Tracking only. Nothing to do on the short side at this price.",
    ),
    ("put", "washed_out"): (
        "Already down at <b>{pos:.0f}%</b> of its 20-day range with RSI {rsi:.0f}. "
        "Puts were the trade getting here — the easy down-money is spent.",
        "A failed bounce into ${sma20:.2f} is a cleaner short than chasing fresh lows. "
        "Shorting down here is late.",
    ),
    ("put", "breakdown"): (
        "Under both averages (${sma20:.2f} / ${sma50:.2f}) at <b>{pos:.0f}%</b> of "
        "range, RSI {rsi:.0f}. Breaking down with room left toward ${lo20:.2f}.",
        "Void if it reclaims ${sma20:.2f}. That's the stop — set it before you're in, "
        "not after.",
    ),
    ("put", "no_short_edge"): (
        "Above both averages (${sma20:.2f} / ${sma50:.2f}) and {chg_1mo:+.1f}% on the "
        "month. Shorting strength is how you get run over.",
        "It has to lose ${sma20:.2f} first. No short edge until then.",
    ),
    ("put", "chop"): (
        "Mid-range at <b>{pos:.0f}%</b> between ${lo20:.2f} and ${hi20:.2f} — "
        "two-sided, not breaking down.",
        "No clean put edge here. A break of ${lo20:.2f} would change that.",
    ),
}

OVERLAY_TEMPLATES = {
    "rich_iv": (
        "One catch: premium's running about {iv:.0f}% against {rvol:.0f}% realized, so "
        "you'd be paying up for the move. Right idea, rich price — wait for vol to cool "
        "or spread it off.",
    )[0],
}


def _prose_values(direction, snap):
    """Snapshot values plus the few derived numbers the templates reference."""
    hi20 = snap["hi20"] or 0.0
    cost = cost_for(direction, snap)
    values = dict(snap)
    values.update(
        cost=cost,
        mult=cost / BUDGET if BUDGET else 0.0,
        budget=BUDGET,
        dist_hi=((hi20 - snap["spot"]) / hi20 * 100.0) if hi20 > 0 else 0.0,
        iv=(snap.get("iv") or 0.0) * 100.0,
        rvol=(snap.get("rvol") or 0.0) * 100.0,
    )
    return values


def render(direction, decision, snap):
    """Build the two [tag, html] pairs for one name in one direction."""
    read_t, watch_t = TEMPLATES[(direction, decision.rule_id)]
    values = _prose_values(direction, snap)
    if decision.overlay:
        watch_t = OVERLAY_TEMPLATES[decision.overlay]
    return [
        ["The read", read_t.format(**values)],
        ["Watch for", watch_t.format(**values)],
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, 41 tests

- [ ] **Step 5: Commit**

```bash
git add analysis.py tests/test_analysis.py
git commit -m "feat: rule-keyed prose templates with no hardcoded price levels"
```

---

### Task 4: The weekly read

**Files:**
- Modify: `analysis.py` (append section 4)
- Modify: `tests/test_analysis.py` (append `TestWeeklyRead`)

**Interfaces:**
- Consumes: `Decision` from Task 2.
- Produces: `summarize(direction, results)` → `[label, html]`, where `results` is a list of
  `{"sym": str, "decision": Decision}` dicts in watchlist order.

Returns the same `[label, html]` shape `index.html`'s `READS` const uses today, so Task 6
is a one-line change on the consuming side.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL with `AttributeError: module 'analysis' has no attribute 'summarize'`

- [ ] **Step 3: Write the implementation**

Append to `analysis.py`:

```python
# --- 4. the weekly read ----------------------------------------------------
def _join(syms):
    """'A', 'A & B', or 'A, B & C' — Oxford-free, HTML-escaped."""
    if not syms:
        return ""
    if len(syms) == 1:
        return syms[0]
    return ", ".join(syms[:-1]) + " &amp; " + syms[-1]


def summarize(direction, results):
    """Assemble the week's read for one direction from all five verdicts."""
    is_call = direction == "call"
    label = "This week · " + ("calls" if is_call else "puts")
    word = "call" if is_call else "put"
    side = "longs" if is_call else "shorts"

    if not results:
        return [label, "<b>Nothing tracked.</b> No names on the board this week."]

    def syms(verdict):
        return [r["sym"] for r in results if r["decision"].verdict == verdict]

    go, wait, skip, mute = syms("go"), syms("wait"), syms("skip"), syms("mute")
    rich = [r["sym"] for r in results if r["decision"].overlay == "rich_iv"]

    parts = []
    if go:
        parts.append("<b>%s %s the clean %s%s.</b>" % (
            _join(go), "is" if len(go) == 1 else "are", word,
            "" if len(go) == 1 else "s"))
    else:
        parts.append("<b>Quiet for %s.</b> Nothing's a clean %s right now." % (side, word))

    if rich:
        parts.append("%s %s the setup but the premium's rich — right idea, wrong price." % (
            _join(rich), "has" if len(rich) == 1 else "have"))

    if skip:
        parts.append("%s %s no edge on this side." % (
            _join(skip), "has" if len(skip) == 1 else "have"))

    if wait and not go:
        parts.append("%s %s worth watching, not buying." % (
            _join(wait), "is" if len(wait) == 1 else "are"))

    if mute:
        parts.append("%s %s priced past the $%.0f budget — tracking only." % (
            _join(mute), "is" if len(mute) == 1 else "are", BUDGET))

    parts.append("No setup = no trade." if is_call
                 else "Don't chase knives that already fell.")
    return [label, " ".join(parts)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, 48 tests

- [ ] **Step 5: Commit**

```bash
git add analysis.py tests/test_analysis.py
git commit -m "feat: generate the weekly calls/puts read from all five verdicts"
```

---

### Task 5: Wire the analyzer into refresh.py

**Files:**
- Modify: `refresh.py` (whole file)
- Modify: `tests/test_analysis.py` (append `TestSnapshotIntegration`)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: a `data.json` that gains a top-level `reads` key and whose
  `names[].call/put.{verdict,vlabel,why}` and `analysis_date` are regenerated each run.

Two behaviours matter more than the wiring:

1. **IV sanity.** `atm_iv`'s current `0.1 < iv < 4.0` band is too loose — it passes
   HIMS's 129% reading straight into the Black-Scholes fair value the page shows the
   user. Reject implied vol above `realized * IV_SANITY_MULTIPLE` and fall back to
   realized.
2. **Partial-failure safety.** A name whose fetch failed keeps its previous price *and*
   its previous verdict. `analysis_date` only advances if at least one name refreshed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis.py`:

```python
class TestSnapshotIntegration(unittest.TestCase):
    """The full path from a price series to a rendered verdict, offline."""

    @staticmethod
    def series(values):
        return [float(v) for v in values]

    def build(self, closes, iv=None):
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
            iv=iv if iv is not None else rv, rvol=rv,
            call_cost=analysis.contract_cost("call", spot, 8, iv or rv),
            put_cost=analysis.contract_cost("put", spot, 8, iv or rv),
            near_dte=8,
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
```

- [ ] **Step 2: Run the tests**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: **PASS**, 52 tests.

Unlike Tasks 1–4 these are not red-first tests, and that is deliberate. They exercise
only `analysis.py`, which Tasks 1–4 already built, so they pass on arrival. Their job is
to pin the contract *before* `refresh.py` is rewritten around it: if the Step 3 rewrite
builds a snapshot missing a key the templates reference, or feeds indicators a series
too short to compute, these fail. The genuinely new code in Step 3 is I/O, which is
verified by running it for real in Step 5 rather than by unit test.

- [ ] **Step 3: Rewrite refresh.py**

Replace the whole of `refresh.py`:

```python
#!/usr/bin/env python3
"""Daily refresh for the My Plays dashboard.

Pulls fresh daily history and ATM implied vol (yfinance, free, no key) for each
name, then regenerates everything the page treats as analysis: price, 1-month
change, support/resistance, implied vol, the call/put verdicts, the "why" text,
and the week's read.

This module owns all I/O. The analysis itself lives in analysis.py, which is
pure and tested offline.
"""
import datetime
import json
import math
import os
import sys

import analysis

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")
MINUS = "−"


def history(ticker):
    """Three months of daily closes, oldest first. None if unavailable."""
    import yfinance as yf
    raw = yf.download(ticker, period="3mo", interval="1d",
                      progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        return None
    c = raw["Close"]
    if hasattr(c, "columns"):
        c = c.iloc[:, 0]
    return [float(x) for x in c.values if x == x]


def atm_iv(ticker, spot, target_days, realized):
    """ATM implied vol for the expiry nearest target_days, sanity-checked.

    yfinance reports implied vol off illiquid strikes that can be wildly wrong.
    A reading far above the name's own realized vol is treated as a bad fill and
    discarded — it would otherwise flow straight into the fair-value estimate the
    page shows the user.
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        exps = tk.options
        if not exps:
            return round(realized, 3)
        today = datetime.date.today()
        best = min(exps, key=lambda e: abs(
            (datetime.date.fromisoformat(e) - today).days - target_days))
        chain = tk.option_chain(best).calls
        row = chain.iloc[(chain["strike"] - spot).abs().argmin()]
        iv = float(row["impliedVolatility"])
        if not (0.1 < iv < 4.0):
            return round(realized, 3)
        if iv > realized * analysis.IV_SANITY_MULTIPLE:
            print("    implied %.2f vs realized %.2f — discarding as a bad fill"
                  % (iv, realized), flush=True)
            return round(realized, 3)
        return round(iv, 3)
    except Exception:
        return round(realized, 3)


def days_to_next_friday(today):
    """Calendar days to the coming Friday, matching index.html's SHORT expiry."""
    ahead = (4 - today.weekday()) % 7
    return ahead if ahead else 7


def build_snapshot(name, closes, near_dte):
    """Everything analysis.py needs for one name. None if the series is too short."""
    if len(closes) < 51:
        return None
    spot = closes[-1]
    rvol = analysis.realized_vol(closes)
    iv_near = atm_iv(name["yf"], spot, near_dte, rvol)
    iv_far = atm_iv(name["yf"], spot, 30, iv_near)
    window = closes[-20:]
    return {
        "sym": name["sym"],
        "spot": spot,
        "sma20": analysis.sma(closes, 20),
        "sma50": analysis.sma(closes, 50),
        "rsi": analysis.rsi(closes, 14),
        "pos": analysis.range_position(closes, 20),
        "hi20": max(window),
        "lo20": min(window),
        "chg_1mo": analysis.pct_change(closes, 21) or 0.0,
        "chg_5d": analysis.pct_change(closes, 5) or 0.0,
        "iv": iv_near,
        "iv_far": iv_far,
        "rvol": rvol,
        "near_dte": near_dte,
        "call_cost": analysis.contract_cost("call", spot, near_dte, iv_near),
        "put_cost": analysis.contract_cost("put", spot, near_dte, iv_near),
    }


def apply_snapshot(name, snap):
    """Write price fields and generated analysis back onto one name."""
    spot, pct = snap["spot"], snap["chg_1mo"]
    name["price"] = "$%.2f" % spot
    name["spot"] = round(spot, 2)
    name["chg"] = ("+" if pct >= 0 else MINUS) + "%.1f%% · 1mo" % abs(pct)
    name["dir"] = "up" if pct >= 0 else "down"
    name["levels"] = [
        ["Support", "$%.2f" % snap["lo20"]],
        ["Resistance", "$%.2f" % snap["hi20"]],
        ["In range", "%d%%" % round(snap["pos"])],
    ]
    name["ivNear"] = snap["iv"]
    name["ivFar"] = snap["iv_far"]

    decisions = {}
    for direction in ("call", "put"):
        d = analysis.decide(direction, snap)
        decisions[direction] = d
        name[direction] = {
            "verdict": d.verdict,
            "vlabel": d.vlabel,
            "why": analysis.render(direction, d, snap),
        }
    return decisions


def main():
    with open(DATA) as f:
        data = json.load(f)

    today = datetime.date.today()
    near_dte = days_to_next_friday(today)
    print("Refreshing (near expiry %dd)..." % near_dte, flush=True)

    results = {"call": [], "put": []}
    refreshed = 0

    for name in data["names"]:
        try:
            closes = history(name["yf"])
            snap = build_snapshot(name, closes, near_dte) if closes else None
        except Exception as e:
            print("  %s: ERROR %s: %s" % (name["sym"], type(e).__name__, e), flush=True)
            snap = None

        if snap is None:
            print("  %s: no/short data, keeping previous price and verdict"
                  % name["sym"], flush=True)
            continue

        decisions = apply_snapshot(name, snap)
        refreshed += 1
        for direction in ("call", "put"):
            results[direction].append({"sym": name["sym"],
                                       "decision": decisions[direction]})
        print("  %s: $%.2f (%s) range $%.2f-$%.2f pos %d%% iv %.0f%% rv %.0f%% "
              "| call %s/%s put %s/%s" % (
                  name["sym"], snap["spot"], name["chg"], snap["lo20"], snap["hi20"],
                  round(snap["pos"]), snap["iv"] * 100, snap["rvol"] * 100,
                  decisions["call"].verdict, decisions["call"].rule_id,
                  decisions["put"].verdict, decisions["put"].rule_id), flush=True)

    if not refreshed:
        print("Nothing refreshed — leaving data.json unchanged.", flush=True)
        sys.exit(0)

    data["reads"] = {
        "call": analysis.summarize("call", results["call"]),
        "put": analysis.summarize("put", results["put"]),
    }
    data["updated"] = today.isoformat()
    data["analysis_date"] = today.isoformat()

    with open(DATA, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Done. %d/%d refreshed. updated = analysis_date = %s"
          % (refreshed, len(data["names"]), data["updated"]), flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, 52 tests

- [ ] **Step 5: Run it for real and calibrate the IV constants**

```bash
python3 -m venv /tmp/plays-venv
/tmp/plays-venv/bin/pip install --quiet yfinance
/tmp/plays-venv/bin/python refresh.py
```

Expected: five lines of per-name output, each showing a verdict and the rule that fired.

Then check the spec's open question — read the printed `iv` and `rv` columns:

- If HIMS prints a "discarding as a bad fill" line while SOUN keeps its high implied
  vol, `IV_SANITY_MULTIPLE = 2.5` works. Leave it.
- If HIMS's bad fill slips through, or SOUN's legitimate vol gets discarded, **the flat
  multiple is the wrong mechanism.** Do not tune the constant to fit. Stop and report:
  the spec flags this as the case where the comparison needs to be against the name's
  own implied-vol history rather than a flat multiple, which is a design change, not a
  number change.

- [ ] **Step 6: Sanity-check the generated verdicts against the hand-written ones**

```bash
git diff data.json | head -80
```

Read the new verdicts against what they replaced (SOUN call/wait, HIMS call/watch,
AI call/skip, ODD call/skip, ELF call/track-only). They need not match — the success
criterion is that each is *defensible*, and disagreement with a two-month-old
hand-written call is expected. Note any verdict you cannot explain from the printed
indicators; that is a rule bug, not a judgment call.

Confirm `ELF` is `mute` on both sides. If it is not, the budget gate is broken.

- [ ] **Step 7: Commit**

```bash
git add refresh.py tests/test_analysis.py data.json
git commit -m "feat: generate verdicts, why text, and weekly read on every refresh"
```

---

### Task 6: Serve the weekly read from data.json

**Files:**
- Modify: `index.html:166-169` (the `READS` const) and `index.html:289-298` (the fetch)

**Interfaces:**
- Consumes: the `reads` key written by Task 5.
- Produces: nothing downstream.

**The hardcoded copy is deleted outright, not kept as a fallback.** It contains
`watch $8.65`, a June price level, which collides with the Global Constraint that no
price level may live in a template literal — keeping it would preserve in `index.html`
exactly the bug this change exists to remove. When `reads` is absent the page shows a
visible error state instead, so a refresh that silently stopped running is noticed and
fixed rather than papered over with plausible-looking stale text. (Maintainer's ruling,
2026-08-05.)

- [ ] **Step 1: Replace the hardcoded const with an empty holder**

Replace the whole `READS` block at `index.html:166-169` with:

```javascript
let READS=null;   // populated from data.json; null renders the error state below
```

- [ ] **Step 2: Add the error-state styling**

The read block currently has one look. Add a variant that is unmistakably not a normal
read. Immediately after the `.read{...}` rule in the `<style>` block, add:

```css
  .read.err{background:var(--skip-bg);border-color:var(--skip-line)}
  .read.err .lab{color:var(--skip)}
```

- [ ] **Step 3: Render the read or the error in setDir**

In `setDir(dir)`, replace these two lines:

```javascript
  document.getElementById('readlab').textContent=READS[dir][0];
  document.getElementById('readtxt').innerHTML=READS[dir][1];
```

with:

```javascript
  const rd=READS&&READS[dir], block=document.querySelector('.read');
  block.classList.toggle('err',!rd);
  document.getElementById('readlab').textContent=rd?rd[0]:'This week · '+(dir==='call'?'calls':'puts');
  document.getElementById('readtxt').innerHTML=rd?rd[1]
    :"<b>This week's read didn't load.</b> The morning refresh may not have run — check the Refresh prices action on GitHub. Prices and verdicts below may be stale too.";
```

- [ ] **Step 4: Read `reads` in the fetch handler**

In the `.then(p=>{...})` block at `index.html:289`, immediately after the
`NAMES=p.names;` line, add:

```javascript
  if(p.reads&&p.reads.call&&p.reads.put) READS=p.reads;
```

- [ ] **Step 5: Verify both states in a browser**

```bash
python3 -m http.server 8765 --directory .
```

Open `http://localhost:8765/`. Confirm the healthy state:
- The "This week · calls" text matches `reads.call[1]` in `data.json` exactly.
- Toggling to Puts swaps to `reads.put[1]`.
- The read block has its normal background — no error styling.
- Each card's "Why this call rating" expands and shows the generated read, with prices
  matching the card's own displayed price.
- The as-of line and "analysis refreshed" line show the same date.

Then confirm the error state actually fires:

```bash
python3 -c "
import json
d = json.load(open('data.json'))
d.pop('reads', None)
json.dump(d, open('/tmp/data-noreads.json','w'), ensure_ascii=False, indent=2)
"
cp data.json /tmp/data-backup.json && cp /tmp/data-noreads.json data.json
```

Reload. Confirm the read block turns red-tinted and shows the "didn't load" message on
both Calls and Puts, and that the cards below still render. Then restore:

```bash
cp /tmp/data-backup.json data.json
```

Reload once more and confirm the normal read is back. Stop the server with Ctrl-C.

Verify the restore took — the file must be byte-identical to what Task 5 committed:

```bash
git diff --exit-code data.json && echo "data.json restored cleanly"
```

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat: read the weekly calls/puts text from data.json"
```

---

### Task 7: Run the tests in CI before publishing

**Files:**
- Modify: `.github/workflows/refresh.yml:23-26`

**Interfaces:**
- Consumes: `tests/test_analysis.py` from Tasks 1–5.
- Produces: nothing downstream.

A broken analyzer must fail the job rather than push bad analysis to the live page.

- [ ] **Step 1: Add the test step**

Insert between the "Install deps" and "Refresh data.json" steps:

```yaml
      - name: Run analysis tests
        run: python -m unittest discover -s tests -t . -v
```

- [ ] **Step 2: Verify the workflow file parses**

Run: `python3 -c "import json,sys; print('ok')" && python3 -c "
import re
text = open('.github/workflows/refresh.yml').read()
assert 'unittest discover' in text
assert text.index('Run analysis tests') < text.index('Refresh data.json')
print('test step is ordered before the refresh')
"`
Expected: `test step is ordered before the refresh`

- [ ] **Step 3: Run the full suite one last time**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, 52 tests

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/refresh.yml
git commit -m "ci: run analysis tests before writing data.json"
```

- [ ] **Step 5: Report what needs pushing**

The maintainer pushes manually — this machine has no credentials. Print the commits
awaiting push and say so explicitly:

```bash
git log --oneline origin/main..HEAD
```

---

## Verification against the spec's success criteria

Run through these after Task 7, against the real `data.json` produced in Task 5:

1. **`analysis_date` equals `updated`.** `python3 -c "import json; d=json.load(open('data.json')); print(d['updated'], d['analysis_date']); assert d['updated']==d['analysis_date']"`
2. **No prose cites a level absent from the snapshot.** Enforced by
   `test_templates_contain_no_hardcoded_numbers`; confirm it is in the passing run.
3. **The weekly read is served from `data.json`.** Confirm `data.json` has a `reads` key
   and that `index.html` contains no analysis copy beyond the fallback block.
4. **A failed fetch leaves the previous analysis intact.** Confirm by reading `main()`:
   a `None` snapshot `continue`s before `apply_snapshot`, and `analysis_date` is only
   written when `refreshed > 0`.
5. **Generated verdicts are defensible.** The Task 5 Step 6 review; report any verdict
   that cannot be explained from the printed indicators.
