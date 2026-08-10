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
from collections import namedtuple

import analysis

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")
MINUS = "−"

# The two horizons the *published* implied vols target. These are display and
# fair-value numbers: index.html prices anything out to 20 days off ivNear and
# anything beyond off ivFar, so ivNear has to be a term that plausibly covers
# that span. It is deliberately NOT the near-Friday expiry — that quote can be
# 1-4 days out and carry an earnings-loaded, gamma-inflated print, which priced
# a 16-day contract several times too high when the frontend read it as ivNear.
# The Friday quote still exists and still prices the budget gate; it just no
# longer gets published as the term structure.
DISPLAY_IV_DTE = 8
FAR_IV_DTE = 30

# call_quote/put_quote and `expiry` are the near-Friday contract the budget gate
# prices off. iv_near/iv_far are the published ~8d and ~30d ATM prints.
# price_iv is the near expiry's own resolved vol, used only to Black-Scholes the
# near contract when its quote is unusable.
OptionData = namedtuple(
    "OptionData", "call_quote put_quote expiry iv_near iv_far price_iv")


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


def _quoted(quote):
    """True when this quote is usable enough that _priced() will price off it.

    The single source of truth for "is there a real price behind this number",
    shared by the cost calculation and the flag published to the page so the
    frontend can decline to show a confident fit badge over a modelled cost.
    """
    _, bid, ask, oi = quote
    return analysis.quote_is_liquid(bid, ask, oi)


def _priced(direction, quote, spot, dte, iv):
    """Dollar cost for one direction: the real quoted mid if usable, else Black-Scholes."""
    if _quoted(quote):
        return analysis.quote_cost(quote[1], quote[2])
    return analysis.contract_cost(direction, spot, dte, iv)


def option_data(ticker, spot, near_dte, realized):
    """Everything one name needs out of the option chain, in one pass.

    Three horizons come off the same expiry list:

    - the expiry nearest `near_dte` (the coming Friday), whose ATM call and put
      quotes price the budget gate — that gate asks "can I actually buy this",
      so it must key off the contract the user would actually buy;
    - the expiry nearest DISPLAY_IV_DTE, whose ATM call IV is published as
      `ivNear` for the page's fair-value math;
    - the expiry nearest FAR_IV_DTE, published as `ivFar`.

    Chains are cached by expiry, so when two targets resolve to the same
    contract — the common case early in the week, where the coming Friday *is*
    the ~8-day expiry — it is fetched once rather than twice.

    Returns an OptionData, or None if the *near* chain could not be fetched at
    all. Falling through silently there would let build_snapshot price the
    budget gate off Black-Scholes at realized vol with zero real quote behind
    it — exactly the unbounded-cost bug the liquidity design change fixed — so
    a missing near chain is logged and treated by the caller as a failed
    refresh for the name, not a quiet downgrade. A missing *display* or *far*
    chain is not fatal: nothing gates on those, so they fall back to realized
    vol and are logged.
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

    cache = {}

    def quotes(expiry):
        """(call_quote, put_quote) for one expiry, fetched at most once."""
        if expiry not in cache:
            chain = tk.option_chain(expiry)
            cache[expiry] = (_quote(_atm_row(chain.calls, spot)),
                             _quote(_atm_row(chain.puts, spot)))
        return cache[expiry]

    near_expiry = _nearest_expiry(exps, near_dte)
    try:
        call_quote, put_quote = quotes(near_expiry)
    except Exception as e:
        print("    %s %s: near option chain failed (%s: %s) — near chain unavailable"
              % (ticker, near_expiry, type(e).__name__, e), flush=True)
        return None

    def iv_at(target_days, label):
        expiry = _nearest_expiry(exps, target_days)
        try:
            call_side = quotes(expiry)[0]
        except Exception as e:
            print("    %s %s: %s option chain failed (%s: %s) — falls back to realized"
                  % (ticker, expiry, label, type(e).__name__, e), flush=True)
            return round(realized, 3)
        return _resolve_iv(call_side, realized, ticker, expiry)

    iv_near = iv_at(DISPLAY_IV_DTE, "display")
    iv_far = iv_at(FAR_IV_DTE, "far")

    # The near contract's Black-Scholes fallback wants the near expiry's own
    # vol, not the 8-day display print — a 2-day contract modelled at an 8-day
    # vol is the same term mismatch in miniature. Reuse the display reading
    # only when the two targets landed on the same expiry, which also keeps
    # the discard log from printing the same line twice.
    if _nearest_expiry(exps, DISPLAY_IV_DTE) == near_expiry:
        price_iv = iv_near
    else:
        price_iv = _resolve_iv(call_quote, realized, ticker, near_expiry)

    return OptionData(call_quote, put_quote, near_expiry, iv_near, iv_far, price_iv)


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

    opt = option_data(name["yf"], spot, near_dte, rvol)
    if opt is None:
        return None

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
        "iv": opt.iv_near,
        "iv_far": opt.iv_far,
        "rvol": rvol,
        "near_dte": near_dte,
        # The expiry date itself, not just its distance in days. A day count is
        # not a contract identity: it decays every hour the page sits open and
        # every day the action doesn't run, so a Saturday visitor computing 6
        # days to the same Friday the job published as 7 would reject a
        # perfectly valid quote.
        "near_expiry": opt.expiry,
        "call_cost": _priced("call", opt.call_quote, spot, near_dte, opt.price_iv),
        "put_cost": _priced("put", opt.put_quote, spot, near_dte, opt.price_iv),
        # Whether each cost above is a real quoted mid or a Black-Scholes
        # estimate. Published, because the page must not badge a modelled cost
        # as a confident "fits $100".
        "call_quoted": _quoted(opt.call_quote),
        "put_quoted": _quoted(opt.put_quote),
    }


def apply_snapshot(name, snap, today):
    """Write price fields and generated analysis back onto one name.

    Returns (decisions, moved) where `moved` lists the directions whose verdict
    class changed today. Each verdict carries a `since` date so a change stays
    reported for the rest of the day rather than only on the run that made it.
    """
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
    # The near expiry's real cost, so the contract row and the "why" text quote
    # the same number for the same contract instead of the page re-deriving its
    # own Black-Scholes price beside the quoted one.
    name["near"] = {
        # `expiry` is what decides whether the quote still applies; `dte` is
        # display only and goes stale between runs.
        "expiry": snap["near_expiry"],
        "dte": snap["near_dte"],
        "call_cost": round(snap["call_cost"], 2),
        "put_cost": round(snap["put_cost"], 2),
        "call_quoted": bool(snap["call_quoted"]),
        "put_quoted": bool(snap["put_quoted"]),
    }
    # Frozen June premiums that nothing refreshes. They were only ever reachable
    # through index.html's calibration fallback, which now tolerates their
    # absence — nothing should calibrate today's fair value off a June print.
    name.pop("anchor", None)
    name.pop("anchorF", None)

    decisions = {}
    moved = []
    for direction in ("call", "put"):
        d = analysis.decide(direction, snap)
        decisions[direction] = d
        prev = (name.get(direction) or {})
        # Only a change of verdict class counts. vlabel moves within a class
        # ("Watch" to "At highs") are not worth announcing.
        if prev.get("verdict") == d.verdict:
            # Carry the existing date forward. Left absent when there is none,
            # rather than stamped with today, which would report every name as
            # having moved on the first run that adds this field.
            since = prev.get("since")
        else:
            since = today.isoformat()
            if prev.get("verdict"):
                moved.append(direction)
        name[direction] = {
            "verdict": d.verdict,
            "vlabel": d.vlabel,
            "why": analysis.render(direction, d, snap),
        }
        if since:
            name[direction]["since"] = since
    return decisions, moved


def main():
    with open(DATA) as f:
        data = json.load(f)

    today = datetime.date.today()
    near_dte = days_to_next_friday(today)
    print("Refreshing (near expiry %dd)..." % near_dte, flush=True)

    # The moment this run actually happened, distinct from `today`/`updated`
    # (the trading session the data represents). With the job now running
    # every 15 minutes, several runs share the same `updated` date, and the
    # page needs the real wall-clock time to say how fresh it is.
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    results = {"call": [], "put": []}
    changed = {"call": [], "put": []}
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

        decisions, moved = apply_snapshot(name, snap, today)
        refreshed += 1
        for direction in moved:
            changed[direction].append((name["sym"], decisions[direction].verdict))
        for direction in ("call", "put"):
            results[direction].append({"sym": name["sym"],
                                       "decision": decisions[direction]})
        print("  %s: $%.2f (%s) range $%.2f-$%.2f pos %d%% ivN %.0f%% ivF %.0f%% "
              "rv %.0f%% | near %dd call $%.0f%s put $%.0f%s "
              "| call %s/%s put %s/%s" % (
                  name["sym"], snap["spot"], name["chg"], snap["lo20"], snap["hi20"],
                  round(snap["pos"]), snap["iv"] * 100, snap["iv_far"] * 100,
                  snap["rvol"] * 100, snap["near_dte"],
                  snap["call_cost"], "" if snap["call_quoted"] else " (est)",
                  snap["put_cost"], "" if snap["put_quoted"] else " (est)",
                  decisions["call"].verdict, decisions["call"].rule_id,
                  decisions["put"].verdict, decisions["put"].rule_id), flush=True)

    if not refreshed:
        print("Nothing refreshed, leaving data.json unchanged.", flush=True)
        sys.exit(0)

    # Name the expiry these verdicts are about, read from what was actually
    # published rather than recomputed — _nearest_expiry picks the nearest
    # LISTED expiry, which is not today+near_dte in a holiday week. Dates live
    # here, not in analysis.py, which stays clock-free.
    expiry_label = None
    for _n in data["names"]:
        _e = (_n.get("near") or {}).get("expiry")
        if _e:
            _d = datetime.date.fromisoformat(_e)
            expiry_label = "%s %d" % (_d.strftime("%b"), _d.day)
            break

    # Anything whose verdict changed at any point today, not just on this run,
    # so a move made at the open is still reported in the afternoon.
    for _n in data["names"]:
        for _dir in ("call", "put"):
            _b = _n.get(_dir) or {}
            if _b.get("since") == today.isoformat():
                _entry = (_n["sym"], _b.get("verdict"))
                if _entry not in changed[_dir]:
                    changed[_dir].append(_entry)

    data["reads"] = {
        "call": analysis.summarize("call", results["call"], stale=stale,
                                   expiry_label=expiry_label,
                                   changes=changed["call"]),
        "put": analysis.summarize("put", results["put"], stale=stale,
                                  expiry_label=expiry_label,
                                  changes=changed["put"]),
    }
    data["updated"] = today.isoformat()
    data["analysis_date"] = today.isoformat()
    # Seconds-precision, timezone-aware UTC timestamp of this run. `updated`
    # stays the "which trading session" date other code and tests key off;
    # this is the "how many minutes ago" instant the page renders.
    data["updated_at"] = now_utc.isoformat(timespec="seconds")

    with open(DATA, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Done. %d/%d refreshed. updated = analysis_date = %s, updated_at = %s"
          % (refreshed, len(data["names"]), data["updated"], data["updated_at"]),
          flush=True)


if __name__ == "__main__":
    main()
