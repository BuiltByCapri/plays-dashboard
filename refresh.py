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
