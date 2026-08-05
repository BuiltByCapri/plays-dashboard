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

yfinance implied vol is unreliable on illiquid strikes. HIMS currently reports 129% IV
against a far lower realized vol — almost certainly a bad fill, not a real market price.
`atm_iv` already band-checks `0.1 < iv < 4.0`, which is too loose to catch this.

Tighten it: reject an implied vol that sits implausibly far from the name's own realized
vol and fall back to realized. This matters beyond the verdicts — `index.html` feeds
`ivNear`/`ivFar` straight into the Black-Scholes fair-value the analyzer shows the user,
so a garbage IV currently produces a garbage "fair ≈ $X" on screen.

## Constants

Named at the top of `refresh.py`, since several are fitted judgments rather than
derived facts:

- `BUDGET = 100` — the dollar budget the dashboard is built around.
- `BUDGET_MULTIPLE = 3` — how far over budget a contract must be before the name is
  demoted to "Track only". Set to 3 because it reproduces the current hand-made calls:
  ELF's near-dated ATM costs $597 (6× budget, muted) while HIMS costs $240 (2.4×, still
  tradeable). This is a fitted threshold, not a derived one.
- `RICH_IV_MULTIPLE = 1.35` — how far implied must exceed realized before the premium
  counts as rich and a `go` is downgraded.
- `IV_SANITY_MULTIPLE = 2.5` — how far implied may exceed realized before it is treated
  as a bad fill and discarded in favour of realized.

The two IV multiples are starting values, not measured ones. Implementation calibrates
them against the five names' actual realized vol — the check being that the sanity
multiple catches HIMS's current 129% reading without discarding legitimately high vol on
SOUN, which is a genuinely volatile small cap rather than a bad fill. If no single
threshold separates those two cases, the spec is wrong about the mechanism and the
comparison needs to be against the name's own vol history rather than a flat multiple.

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
