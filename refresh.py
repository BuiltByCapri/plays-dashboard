#!/usr/bin/env python3
"""Daily refresh for the My Plays dashboard.

Pulls fresh daily history + ATM implied vol (yfinance, free, no key) for each
name and updates data.json: price, 1-month change, support/resistance (20-day
low/high), where it sits in range, AND ivNear/ivFar (so the price analyzer's
fair-value stays accurate as prices move). Leaves the human analysis (call/put
verdict + why) untouched; that carries its own `analysis_date`.
"""
import json, datetime, sys, os, math

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")
MINUS = "−"


def history(ticker):
    import yfinance as yf
    raw = yf.download(ticker, period="3mo", interval="1d",
                      progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        return None
    c = raw["Close"]
    if hasattr(c, "columns"):
        c = c.iloc[:, 0]
    return [float(x) for x in c.values if x == x]


def realized_vol(c):
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c)) if c[i - 1] > 0]
    if len(rets) < 5:
        return 0.8
    m = sum(rets[-20:]) / len(rets[-20:])
    var = sum((r - m) ** 2 for r in rets[-20:]) / len(rets[-20:])
    return max(0.1, math.sqrt(var) * math.sqrt(252))


def atm_iv(ticker, spot, target_days, fallback):
    """ATM implied vol from yfinance for the expiry nearest target_days."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        exps = tk.options
        if not exps:
            return fallback
        today = datetime.date.today()
        best = min(exps, key=lambda e: abs((datetime.date.fromisoformat(e) - today).days - target_days))
        ch = tk.option_chain(best).calls
        row = ch.iloc[(ch["strike"] - spot).abs().argmin()]
        iv = float(row["impliedVolatility"])
        if 0.1 < iv < 4.0:      # sane band; ignore garbage from illiquid strikes
            return round(iv, 3)
        return fallback
    except Exception:
        return fallback


def refresh(name):
    c = history(name["yf"])
    if not c or len(c) < 22:
        print(f"  {name['sym']}: no/short data, keeping previous", flush=True)
        return False
    last, mo = c[-1], c[-21]
    pct = (last - mo) / mo * 100.0
    win = c[-20:]
    lo20, hi20 = min(win), max(win)
    pos = round((last - lo20) / (hi20 - lo20) * 100) if hi20 > lo20 else 50
    rv = realized_vol(c)

    name["price"] = f"${last:.2f}"
    name["spot"] = round(last, 2)
    name["chg"] = ("+" if pct >= 0 else MINUS) + f"{abs(pct):.1f}% · 1mo"
    name["dir"] = "up" if pct >= 0 else "down"
    name["levels"] = [["Support", f"${lo20:.2f}"], ["Resistance", f"${hi20:.2f}"], ["In range", f"{pos}%"]]
    name["ivNear"] = atm_iv(name["yf"], last, 8, round(rv, 3))
    name["ivFar"] = atm_iv(name["yf"], last, 30, name["ivNear"])
    print(f"  {name['sym']}: ${last:.2f} ({name['chg']}) range ${lo20:.2f}-${hi20:.2f} pos {pos}%  ivN {name['ivNear']*100:.0f}% ivF {name['ivFar']*100:.0f}%", flush=True)
    return True


def main():
    with open(DATA) as f:
        d = json.load(f)
    print("Refreshing...", flush=True)
    any_ok = False
    for n in d["names"]:
        try:
            any_ok = refresh(n) or any_ok
        except Exception as e:
            print(f"  {n['sym']}: ERROR {type(e).__name__}: {e}", flush=True)
    if not any_ok:
        print("Nothing refreshed — leaving data.json unchanged.", flush=True)
        sys.exit(0)
    d["updated"] = datetime.date.today().isoformat()
    with open(DATA, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Done. updated = {d['updated']}", flush=True)


if __name__ == "__main__":
    main()
