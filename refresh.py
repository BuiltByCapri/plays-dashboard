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


def _num(row, col):
    """Read a numeric field off a yfinance option row; NaN and missing become None."""
    try:
        v = float(row[col])
    except (KeyError, TypeError, ValueError):
        return None
    return None if v != v else v


def _atm_row(chain_side, spot):
    """The row (by nearest strike) closest to spot in one side of a chain."""
    return chain_side.iloc[(chain_side["strike"] - spot).abs().argmin()]


def _quote(row):
    """(iv, bid, ask, open_interest) off one ATM row, as plain numbers."""
    return (_num(row, "impliedVolatility"), _num(row, "bid"),
            _num(row, "ask"), _num(row, "openInterest"))


def _nearest_expiry(exps, target_days):
    today = datetime.date.today()
    return min(exps, key=lambda e: abs(
        (datetime.date.fromisoformat(e) - today).days - target_days))


def _resolve_iv(quote, realized, ticker=None, expiry=None):
    """The IV to trust for one quote: itself if sane and liquid, else realized.

    yfinance reports implied vol off illiquid strikes that can be wildly wrong.
    Liquidity — not distance from realized vol — is what separates a real
    elevated quote from a bad fill: short-dated IV structurally runs well above
    20-day realized vol for legitimate reasons, so that comparison alone proves
    nothing. A quote too thin or too wide to trade on is discarded and realized
    vol is used instead, which would otherwise flow straight into the fair-value
    estimate and the budget gate the page shows the user.

    `ticker`/`expiry` are for the discard log line only — without them, the job
    log can't say which name and expiry a discarded quote belonged to.
    """
    iv, bid, ask, oi = quote
    if iv is None or not (0.1 < iv < 4.0):
        return round(realized, 3)
    if not analysis.quote_is_liquid(bid, ask, oi):
        where = "%s %s" % (ticker, expiry) if ticker else "quote"
        print("    %s: iv %.2f (oi=%s bid=%s ask=%s) — illiquid quote, discarding"
              % (where, iv, oi, bid, ask), flush=True)
        return round(realized, 3)
    return round(iv, 3)


def _priced(direction, quote, spot, dte, iv):
    """Dollar cost for one direction: the real quoted mid if usable, else Black-Scholes."""
    _, bid, ask, oi = quote
    if analysis.quote_is_liquid(bid, ask, oi):
        return analysis.quote_cost(bid, ask)
    return analysis.contract_cost(direction, spot, dte, iv)


def atm_iv(ticker, spot, target_days, realized):
    """ATM implied vol for the expiry nearest target_days, sanity-checked.

    Used for the far expiry, which only feeds the page's fair-value display —
    see near_quotes() for the near expiry, which also prices the budget gate.
    A failure here just falls back to realized vol and is logged; it never
    blocks the refresh, since nothing downstream gates on the far reading.
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        exps = tk.options
        if not exps:
            print("    %s: no listed option expiries — far iv falls back to realized"
                  % ticker, flush=True)
            return round(realized, 3)
        best = _nearest_expiry(exps, target_days)
        quote = _quote(_atm_row(tk.option_chain(best).calls, spot))
        return _resolve_iv(quote, realized, ticker, best)
    except Exception as e:
        print("    %s: far option chain failed (%s: %s) — falls back to realized"
              % (ticker, type(e).__name__, e), flush=True)
        return round(realized, 3)


def near_quotes(ticker, spot, near_dte):
    """ATM call and put quotes for the expiry nearest near_dte.

    One option_chain() fetch returns both sides, so the near IV reading and
    both the call_cost and put_cost budget-gate prices come from a single
    round trip rather than three.

    Returns (call_quote, put_quote, expiry) on success — each quote an
    (iv, bid, ask, open_interest) tuple, even if a quote later turns out
    illiquid (handled downstream via the usual realized-vol/Black-Scholes
    fallback, which is fine — that's the design, not a failure).

    Returns None, not a tuple of empty quotes, if no near chain could be
    fetched at all. Falling through silently here would let build_snapshot
    price the budget gate off Black-Scholes at realized vol with zero real
    quote behind it — exactly the unbounded-cost bug the liquidity design
    change fixed — so a missing chain must be logged and treated by the
    caller as a failed refresh for the name, not a quiet downgrade.
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        exps = tk.options
    except Exception as e:
        print("    %s: could not list option expiries (%s: %s) — near chain unavailable"
              % (ticker, type(e).__name__, e), flush=True)
        return None
    if not exps:
        print("    %s: no listed option expiries — near chain unavailable"
              % ticker, flush=True)
        return None
    best = _nearest_expiry(exps, near_dte)
    try:
        chain = tk.option_chain(best)
        return (_quote(_atm_row(chain.calls, spot)),
                _quote(_atm_row(chain.puts, spot)), best)
    except Exception as e:
        print("    %s %s: near option chain failed (%s: %s) — near chain unavailable"
              % (ticker, best, type(e).__name__, e), flush=True)
        return None


def days_to_next_friday(today):
    """Calendar days to the coming Friday, matching index.html's SHORT expiry."""
    ahead = (4 - today.weekday()) % 7
    return ahead if ahead else 7


def build_snapshot(name, closes, near_dte):
    """Everything analysis.py needs for one name.

    None if the series is too short, or if the near option chain could not be
    fetched at all — in either case the name keeps its previous price and
    verdict rather than publish a cost estimate with no real quote behind it.
    """
    if len(closes) < 51:
        return None
    spot = closes[-1]
    rvol = analysis.realized_vol(closes)

    near = near_quotes(name["yf"], spot, near_dte)
    if near is None:
        return None
    call_quote, put_quote, near_expiry = near
    iv_near = _resolve_iv(call_quote, rvol, name["yf"], near_expiry)
    # Falls back to realized vol, not iv_near: iv_near can be an extreme
    # short-dated reading (e.g. a 2-day ATM print well above its annualized
    # realized vol for legitimate reasons), and using it as the far-expiry
    # fallback would let that spike leak into the ~1mo fair-value the page
    # shows, uncapped, if the far quote itself turns out to be unusable.
    iv_far = atm_iv(name["yf"], spot, 30, rvol)

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
        "call_cost": _priced("call", call_quote, spot, near_dte, iv_near),
        "put_cost": _priced("put", put_quote, spot, near_dte, iv_near),
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
    stale = []

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
            stale.append(name["sym"])
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
        "call": analysis.summarize("call", results["call"], stale=stale),
        "put": analysis.summarize("put", results["put"], stale=stale),
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
