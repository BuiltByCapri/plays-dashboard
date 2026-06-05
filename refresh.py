#!/usr/bin/env python3
"""Daily price refresh for the My Plays dashboard.

Pulls fresh daily history (yfinance, free, no key) for each name and updates the
NUMERIC fields in data.json — price, 1-month change, support/resistance (20-day
low/high), and where it sits in range. Leaves the human analysis (verdict, vlabel,
why, the option anchors) untouched; those carry their own `analysis_date`.
"""
import json, datetime, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")
MINUS = "−"  # − typographic minus, matches the UI


def close_series(ticker):
    import yfinance as yf
    raw = yf.download(ticker, period="3mo", interval="1d",
                      progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        return None
    c = raw["Close"]
    if hasattr(c, "columns"):       # single-ticker MultiIndex frame
        c = c.iloc[:, 0]
    return [float(x) for x in c.values if x == x]   # drop NaNs


def refresh(name):
    c = close_series(name["yf"])
    if not c or len(c) < 22:
        print(f"  {name['sym']}: no/short data, keeping previous", flush=True)
        return False
    last = c[-1]
    mo = c[-21]
    pct = (last - mo) / mo * 100.0
    win = c[-20:]
    lo20, hi20 = min(win), max(win)
    pos = round((last - lo20) / (hi20 - lo20) * 100) if hi20 > lo20 else 50

    name["price"] = f"${last:.2f}"
    name["spot"] = round(last, 2)
    name["chg"] = ("+" if pct >= 0 else MINUS) + f"{abs(pct):.1f}% · 1mo"
    name["dir"] = "up" if pct >= 0 else "down"
    name["levels"] = [["Support", f"${lo20:.2f}"],
                      ["Resistance", f"${hi20:.2f}"],
                      ["In range", f"{pos}%"]]
    print(f"  {name['sym']}: ${last:.2f}  ({name['chg']})  range ${lo20:.2f}-${hi20:.2f}  pos {pos}%", flush=True)
    return True


def main():
    with open(DATA) as f:
        d = json.load(f)
    print("Refreshing prices...", flush=True)
    any_ok = False
    for n in d["names"]:
        try:
            if refresh(n):
                any_ok = True
        except Exception as e:
            print(f"  {n['sym']}: ERROR {type(e).__name__}: {e}", flush=True)
    if not any_ok:
        print("No tickers refreshed — leaving data.json unchanged.", flush=True)
        sys.exit(0)
    d["updated"] = datetime.date.today().isoformat()
    with open(DATA, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Done. updated = {d['updated']}", flush=True)


if __name__ == "__main__":
    main()
