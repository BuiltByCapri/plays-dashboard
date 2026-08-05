# Auto-generated analysis for the My Plays dashboard

**Date:** 2026-08-05
**Status:** Approved, pending implementation plan

## Problem

The dashboard refreshes prices every weekday morning but the analysis around them is
frozen. `refresh.py` updates `price`, `spot`, `chg`, `dir`, `levels`, `ivNear`, and
`ivFar`, and explicitly leaves the verdicts and "why" text alone. The weekly read is
worse off: it never reaches `data.json` at all, because it is hardcoded in `index.html`
as the `READS` object.

The result is a page whose numbers are live and whose reasoning is two months old. On
2026-08-05, `updated` is today while `analysis_date` is 2026-06-09. SOUN's card shows a
live $6.49 and, directly beneath it, a "why" that says the name "kept bleeding to $6.92"
and watches a $7.85 level chosen in June. The verdict pip, the weekly read, and the
price disagree with each other, and nothing on the page signals which one to trust.

Every field the user reads as *analysis* must be derived from the same snapshot as the
price, or it must not be shown.

## Approach

`refresh.py` becomes a price-fetcher **and** an analyzer. It computes the verdict from
the price history deterministically and renders the prose from templates keyed to the
matched rule.

No LLM call, no API key, no repo secret, no new runtime dependency. The existing
GitHub Actions cron runs it unchanged. The analysis is reproducible: the same input
series always produces the same verdict and the same sentence, which means a rating can
be audited against the data that produced it.

The tradeoff accepted here is prose variety. Templated copy reads more repetitively than
hand-written copy. This is judged the right trade, because a stale hand-written sentence
is actively misleading in a way a plain generated one is not.

### Rejected alternatives

**Claude API writes the prose (rules pick the verdict).** Better-reading copy, but adds
an `ANTHROPIC_API_KEY` secret, a per-day cost, and a network dependency in the critical
path of a cron job whose whole job is to not go stale.

**Claude API picks the verdict too.** Rejected outright. The rating would become
non-deterministic and unauditable — it could drift day to day on unchanged data, with
nothing to check it against.

## Data flow

```
yfinance (3mo daily OHLCV, option chain)
    │
    ├─ price fields ────────────────► data.json: price, spot, chg, dir, levels
    ├─ indicators ──────────────────► SMA20, SMA50, RSI(14), range position,
    │                                  distance to 20d high/low, 1mo/5d change,
    │                                  realized vol, IV vs realized
    ├─ budget probe ────────────────► near-dated ATM contract cost
    │
    ▼
verdict ladder (per name, per direction)
    │
    ├─ verdict + vlabel ────────────► data.json: names[].call/put.verdict, .vlabel
    ├─ template render ─────────────► data.json: names[].call/put.why[]
    │
    ▼
aggregate across the 5 names ───────► data.json: reads.call, reads.put
                                      data.json: analysis_date = today
```

`index.html` reads `p.reads` instead of its hardcoded `READS` const. Everything else in
the page is unchanged — the verdict pips, the "why" disclosure, and the Black-Scholes
analyzer already consume these fields and need no modification.

## Components

The repo is three files and has no package structure or tests today. Rather than expand
it into five modules, the analysis lands in **one new file, `analysis.py`**, holding
pure functions in four clearly separated sections, plus `tests/test_analysis.py`.

The boundary that actually matters is not file-to-file, it is I/O versus logic:
`analysis.py` never touches the network or the filesystem, and `refresh.py` owns all of
both. That is what makes the analysis testable offline, and it does not require four
files to enforce. If `analysis.py` outgrows a few hundred lines, splitting it along the
section boundaries below is mechanical.

### Section 1: indicators — series in, numbers out

`sma(series, n)`, `rsi(series, n=14)`, `range_position(series, n=20)`,
`realized_vol(series)`, `pct_change(series, n)`. No knowledge of verdicts or tickers.
Takes a list of floats, returns floats.

### Section 2: verdicts — numbers in, rating out

`decide(direction, snapshot) -> (verdict, vlabel, rule_id)`. `snapshot` is a plain dict
of the indicator values plus `contract_cost`. Returns one of the four existing verdict
keys (`go`, `wait`, `skip`, `mute`), a display label, and the id of the rule that fired.
The rule id is what selects the prose template, so the sentence can never describe a
different rule than the one that set the pip.

Rules are evaluated in order; first match wins.

**Calls:**

| # | Rule id | Condition | Verdict | Label |
|---|---------|-----------|---------|-------|
| 0 | `over_budget` | ATM contract cost > `BUDGET_MULTIPLE` × $100 | `mute` | Track only |
| 1 | `extended` | range position ≥ 90% and RSI ≥ 70 | `skip` | Extended |
| 2 | `no_base` | price < SMA20 < SMA50 and range position ≤ 25% | `wait` | No base |
| 3 | `clean_setup` | price > SMA20, SMA20 ≥ SMA50, position 55–85%, RSI 50–68 | `go` | Looks solid |
| 4 | `breakout_pending` | within 3% of 20d high, SMA20 ≥ SMA50, RSI < 70 | `wait` | Watch |
| 5 | `chop` | (default) | `wait` | Watch |

**Puts:**

| # | Rule id | Condition | Verdict | Label |
|---|---------|-----------|---------|-------|
| 0 | `over_budget` | ATM contract cost > `BUDGET_MULTIPLE` × $100 | `mute` | Track only |
| 1 | `washed_out` | range position ≤ 10% and RSI ≤ 30 | `wait` | Late |
| 2 | `breakdown` | price < SMA20 < SMA50, position ≤ 40%, RSI 30–45 | `go` | Looks solid |
| 3 | `no_short_edge` | price > SMA20 > SMA50 | `skip` | Skip |
| 4 | `chop` | (default) | `skip` | Skip |

**Overlay, applied after the ladder:** when implied vol sits well above the name's own
3-month realized vol, a `go` is downgraded to `wait` and the prose notes the premium is
rich. Direction right, price wrong.

### Section 3: prose — rating in, sentences out

`render(direction, rule_id, snapshot) -> [["The read", str], ["Watch for", str]]`.
One template pair per rule id, interpolating live values: today's price, the actual
SMA20, the actual 20-day high and low, the actual RSI, the actual percentage move to
break even.

Two constraints on the template text:

- **Every number cited must come from `snapshot`.** No literal price levels in template
  strings. This is the specific failure being fixed; a template with a hardcoded `$7.85`
  in it reintroduces it.
- **Confidence must match the model.** A 20-day-range-and-RSI model does not justify
  declarative sentences. Templates hedge in their wording, matching the existing voice
  ("no setup = no trade", "don't catch a falling knife") which already does this.

### Section 4: weekly read — five results in, summary out

`summarize(direction, results) -> [label, html]`. Counts how many names are `go` /
`wait` / `skip` on the given side and names the standouts (strongest setup, freshest
breakdown), assembling two sentences. Returns the same `[label, html]` shape the
`READS` const uses today, so `index.html`'s consumption is a one-line change.

### `refresh.py` — orchestration and I/O

Keeps its current structure. Gains: the volume series alongside the closes it already
downloads, the calls into `analysis.py`, `analysis_date` stamping, and `reads`
assembly. Its existing
"if nothing refreshed, change nothing and exit 0" guard extends to cover the analysis —
a name whose history fetch failed keeps both its old price and its old verdict, and
`analysis_date` is only advanced when at least one name refreshed successfully.

## Known data-quality issue

yfinance implied vol is unreliable on illiquid strikes, and a bad reading does real
damage: `index.html` feeds `ivNear`/`ivFar` straight into the Black-Scholes fair value
the analyzer shows the user, and the budget gate prices its contract off the same
number. `atm_iv`'s `0.1 < iv < 4.0` band is far too loose to catch it.

**Superseded approach (2026-08-05).** The original design rejected an implied vol
sitting more than `IV_SANITY_MULTIPLE` above the name's own realized vol. Task 5's
calibration run disproved the mechanism on live data — it failed in both directions
simultaneously:

```
SOUN  dte=2  iv=190%  vol=5563  oi=7929  bid=0.40 ask=0.42   -> DISCARDED (wrongly)
ODD   dte=2  iv=172%  vol=1     oi=1     bid=0.30 ask=1.80   -> KEPT      (wrongly)
ELF   dte=2  iv=217%  vol=7     oi=93    bid=5.80 ask=8.10   -> DISCARDED (wrongly)
```

SOUN's reading is liquid and real — 5,563 contracts traded on a 2-cent spread — and was
thrown away. ODD's is a dead market whose spread is wider than its own bid, and it
passed. The ratio measures the wrong thing: short-dated implied vol structurally runs
far above 20-day realized vol for legitimate reasons (event risk, gamma near expiry),
so no value of the constant separates a real elevated quote from a bad fill.

The downstream cost was concrete. Discarding ELF's real 217% IV and substituting 54%
realized vol priced its 2-day ATM call at $156 instead of $572 — under the $300 budget
gate — so a name whose contracts genuinely cost $580–810 was published as a tradeable
`wait / breakout_pending` call. The hand-written data had it right as "Track only".

**Current approach.** Two changes, both using data already present in the option chain:

1. **The budget gate prices off the real quote, not a model.** "Can I afford this
   contract" is a question about what it actually costs, so it is answered by the quoted
   mid — `(bid + ask) / 2 × 100` — falling back to Black-Scholes only when no usable
   quote exists.
2. **IV sanity is judged on liquidity, not on a vol ratio.** A reading is discarded when
   its quote is dead: open interest below `MIN_OPEN_INTEREST`, or a bid/ask spread wider
   than `MAX_SPREAD_RATIO` of the mid. Against the table above this keeps SOUN (5%
   spread) and ELF (33%), and discards ODD (143%) — the intended outcome in all three.

The absolute `0.1 < iv < 4.0` band stays as a backstop.

## Constants

Named at the top of `refresh.py`, since several are fitted judgments rather than
derived facts:

- `BUDGET = 100` — the dollar budget the dashboard is built around.
- `BUDGET_MULTIPLE = 3` — how far over budget a contract must be before the name is
  demoted to "Track only". Set to 3 because it reproduces the current hand-made calls:
  ELF's near-dated ATM costs $597 (6× budget, muted) while HIMS costs $240 (2.4×, still
  tradeable). This is a fitted threshold, not a derived one.
- `RICH_IV_MULTIPLE = 1.35` — how far implied must exceed realized before the premium
  counts as rich and a `go` is downgraded. Still a starting value, not a measured one.
  It is a *judgment* about price, not a data-quality filter, so the failure that killed
  `IV_SANITY_MULTIPLE` does not apply to it — but it has not been validated either.
- `MIN_OPEN_INTEREST = 10` — below this, an option quote is a dead market and its
  implied vol is not trusted. Discards ODD's oi=1 fill; keeps ELF's oi=93.
- `MAX_SPREAD_RATIO = 0.5` — a bid/ask spread wider than half the mid means the quote
  is untrustworthy. Discards ODD (143% of mid); keeps SOUN (5%) and ELF (33%).

`IV_SANITY_MULTIPLE` is **removed**. See the data-quality section above for why the
mechanism it implemented was wrong rather than merely mistuned.

Both liquidity thresholds are fitted to a single day's observation of five names. They
separate the three cases seen on 2026-08-05 cleanly, with ELF's 33% spread the closest
call — a name quoting wider than that on a quiet day would be discarded where it
probably should not be. They are worth revisiting once the daily runs have accumulated
enough history to see the normal spread range per name.

## Testing

The I/O boundary above exists to make this possible without network access. Tests use
stdlib `unittest`, so the repo gains no dependency and they run with
`python -m unittest discover`. The workflow gains a step that runs them *before*
`refresh.py`, so a broken analyzer fails the job instead of publishing bad analysis to
the live page.

- **Indicators:** known series with hand-checked expected values, including the
  degenerate cases — flat series (zero range, division guard), series shorter than the
  window, and gaps.
- **Verdict ladder:** one synthetic snapshot per rule id, asserting both the verdict and
  that the *expected rule* fired. Ordering matters here — a snapshot that satisfies two
  rules must match the earlier one, so the tests assert `rule_id`, not just the verdict.
- **Prose:** every template renders for every rule id without a missing key, and no
  rendered string contains a number absent from its snapshot. The second assertion is
  what enforces the "no hardcoded levels" constraint mechanically rather than by review.
- **Weekly read:** all-green, all-skip, and mixed result sets.
- **End to end:** run against the committed `data.json` snapshot, offline, asserting the
  output is well-formed and that `analysis_date` equals `updated`.

## Success criteria

1. `analysis_date` equals `updated` after every successful run.
2. No verdict or "why" string cites a price level that is not in that run's snapshot.
3. The weekly read is served from `data.json`; `index.html` holds no analysis copy.
4. A failed fetch leaves the previous analysis intact rather than publishing a
   half-updated view.
5. The generated verdicts for the current five names are defensible against the
   hand-written ones they replace — not necessarily identical, but explainable.

## Out of scope

Changing the watchlist, the visual design, the Black-Scholes analyzer, the TradingView
embeds, or the refresh schedule. Adding a manual override channel for the verdicts.
