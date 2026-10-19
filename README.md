# My Plays

A rule-based options screening dashboard. Python pulls market data, a deterministic analysis engine scores each name, and a static page renders the result. It refreshes itself every 15 minutes during US market hours, with no server and no manual step.

**Live:** [builtbycapri.github.io/plays-dashboard](https://builtbycapri.github.io/plays-dashboard)

## Why it exists

I trade options on a small, fixed budget: $100 a contract. Most screeners tell you what looks interesting. This one tells you whether there's a trade you can actually take, and most days the honest answer is no. The dashboard is built to say that plainly instead of talking itself into a position.

Three principles drive the design:

- **No trade is the default.** A name has to earn a "go" by passing every rule. Anything ambiguous lands on wait or skip.
- **Every claim traces to a number.** Each line of analysis on the page is generated from that run's data. Nothing is hand-written, so nothing goes stale.
- **A wrong number is worse than no number.** If data is missing, thin, or unreliable, the page says so instead of showing a confident figure.

## What it does

For each name on the watchlist, every run:

1. **Pulls three months of daily prices and the live option chain** from Yahoo Finance.
2. **Computes indicators:** 20 and 50-day moving averages, 14-day RSI, position within the 20-day range, realized volatility, and implied volatility at roughly 8 and 30 days out.
3. **Prices the contract I would actually buy** at the real quoted bid/ask midpoint. If the quote is too thin to trust (low open interest or a wide spread), it falls back to a Black-Scholes estimate and labels it as an estimate.
4. **Runs a verdict ladder** separately for calls and puts. Rules are checked in order and the first match wins:
   - Over budget → **Track only**, regardless of the setup
   - Clean trend, healthy range position, momentum not stretched → **Looks solid**
   - Overextended, no base, or pinned at a range extreme → **Wait** or **Skip**
   - A good setup with expensive options (implied vol well above realized) is downgraded to **Rich**: right idea, wrong price
5. **Writes the explanation** for each verdict from a template keyed to the rule that fired, filled with that run's actual levels.
6. **Writes a weekly summary** across all names, leading with anything whose verdict changed today.

## Architecture

```
GitHub Action (every 15 min, market hours)
  └─ run test suite ──── fails? stop, nothing publishes
  └─ refresh.py ──────── fetch prices + option chains (all I/O lives here)
       └─ analysis.py ── indicators, verdicts, prose (pure functions)
  └─ commit data.json ── GitHub Pages serves index.html, which renders it
```

| File | Role |
|---|---|
| [`analysis.py`](analysis.py) | Pure decision logic. No network, no filesystem, no clock, standard library only. The same input always produces the same output. |
| [`refresh.py`](refresh.py) | Owns all I/O: fetching, building a snapshot per name, handling partial failures, writing `data.json`. |
| [`index.html`](index.html) | Renders `data.json`. Vanilla JavaScript, no build step. |
| [`tests/`](tests/) | 124 unit tests. They gate the Action, so a failing suite blocks publishing. |

Keeping the analysis pure was the key decision. It means every rule can be tested offline against fixed inputs, and a bug in fetching can't quietly change a verdict.

## Things I designed against

The first two came from real failures. All of them are covered by tests.

- **A model priced a $695 contract at $156** and published it as affordable. The budget gate now prices off the real market quote, and the page won't show a "fits budget" badge over a modelled cost.
- **The analysis text once quoted a price level from two months earlier.** Templates now ban literal numbers. Every figure is a placeholder filled from the current snapshot, and a test fails the build if one slips in.
- **Bad data that looks fine.** Yahoo can report open interest as NaN, and NaN compares false to everything, so a naive "too thin?" check quietly passes it. The liquidity filter rejects it explicitly.
- **Partial outages.** If one name fails to refresh, it keeps its last verdict and the summary names it as stale, so the rest of the board doesn't pretend to be current.

## Running it locally

Requires Python 3.9+.

```bash
pip install yfinance
python -m unittest discover -s tests -t .   # no network needed
python refresh.py                           # fetches live data, rewrites data.json
python -m http.server                       # then open localhost:8000
```

## Known limits

- **Thresholds are judgment calls.** The liquidity cutoffs were tuned against a small sample, and the "rich premium" multiple hasn't been backtested.
- **Free data, delayed.** Yahoo Finance quotes run about 15 minutes behind, and GitHub's scheduler can run late under load.
- **A failed run is silent.** Tests block bad data from publishing, but nothing alerts me. The page just shows an older "updated" time.
- **This is a screen, not a strategy.** It filters for setups worth a closer look. It doesn't size positions, manage exits, or claim an edge.

## Stack

Python (standard library for analysis, `yfinance` for data) · GitHub Actions · GitHub Pages · vanilla HTML/CSS/JS
