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
MIN_OPEN_INTEREST = 10    # below this, an option quote is a dead market
MAX_SPREAD_RATIO = 0.5    # (ask-bid)/mid above this means the quote isn't trustworthy
MAX_ABS_SPREAD = 0.15     # cents-wide spreads are tick artifacts on cheap options,
                          # not evidence of a dead market

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


def quote_is_liquid(bid, ask, open_interest):
    """True when an option quote is tight and traded enough to price off.

    Liquidity, not the level of implied vol, is what separates a real elevated
    quote from a bad fill. Short-dated IV structurally runs far above 20-day
    realized vol for legitimate reasons, so comparing the two measures nothing.

    Open interest is a hard gate: below MIN_OPEN_INTEREST, missing, or NaN
    (a feed can report a NaN open interest for a listed-but-untraded strike,
    and `nan < MIN_OPEN_INTEREST` is silently False, not a rejection — this is
    checked for explicitly), a quote is a dead market no matter how tight it
    looks. Above that floor, the spread just needs to be tight by *either*
    measure: a small ratio to the mid (MAX_SPREAD_RATIO), or a small number of
    cents (MAX_ABS_SPREAD). The ratio alone would reject a 12-cent spread on an
    18-cent option as "67% wide" even with thousands of contracts open — that's
    a one-cent-tick artifact of a cheap contract, not a bad fill, and the
    absolute-cents test catches it.
    """
    if (open_interest is None or open_interest != open_interest
            or open_interest < MIN_OPEN_INTEREST):
        return False
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return False
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return False
    spread = ask - bid
    return (spread / mid <= MAX_SPREAD_RATIO) or (spread <= MAX_ABS_SPREAD)


def quote_cost(bid, ask):
    """Dollar cost of one contract at the quoted mid."""
    return (bid + ask) / 2.0 * 100.0


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
        # Cosmetic values get defaulted: they don't drive verdicts, so a None
        # from pct_change() or similar on short series must not crash rendering.
        # Decision-driving values (pos, rsi, sma20, sma50, hi20, lo20) do not get
        # defaulted; a None there means the analysis is genuinely broken and should
        # fail loudly during decide(), not silently render incorrect output.
        chg_1mo=(snap.get("chg_1mo") or 0.0),
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


# --- 4. the weekly read ----------------------------------------------------
def _join(syms):
    """'A', 'A & B', or 'A, B & C' — Oxford-free, HTML-escaped."""
    if not syms:
        return ""
    if len(syms) == 1:
        return syms[0]
    return ", ".join(syms[:-1]) + " &amp; " + syms[-1]


def summarize(direction, results, stale=()):
    """Assemble the week's read for one direction from all five verdicts.

    `stale` lists symbols that failed to refresh this run and so are showing
    a verdict from an earlier one — kept deliberately, but named so the read
    doesn't silently imply every card on the board is current.
    """
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

    # Exclude rich-IV names from the watching list to avoid naming them twice
    rich_set = set(rich)
    wait_only = [s for s in wait if s not in rich_set]

    parts = []
    if go:
        parts.append("<b>%s %s the clean %s%s.</b>" % (
            _join(go), "is" if len(go) == 1 else "are", word,
            "" if len(go) == 1 else "s"))
    elif rich:
        parts.append("<b>Nothing clean at these prices.</b> The %s setup's there — the premium isn't." % word
                     if len(rich) == 1 else
                     "<b>Nothing clean at these prices.</b> The %s setups are there — the premiums aren't." % word)
    else:
        parts.append("<b>Quiet for %s.</b> Nothing's a clean %s right now." % (side, word))

    if rich:
        parts.append("%s %s the setup but the premium's rich — right idea, wrong price." % (
            _join(rich), "has" if len(rich) == 1 else "have"))

    if skip:
        parts.append("%s %s no edge on this side." % (
            _join(skip), "has" if len(skip) == 1 else "have"))

    if wait_only and not go:
        parts.append("%s %s worth watching, not buying." % (
            _join(wait_only), "is" if len(wait_only) == 1 else "are"))

    if mute:
        parts.append("%s %s priced past the $%.0f budget — tracking only." % (
            _join(mute), "is" if len(mute) == 1 else "are", BUDGET))

    if stale:
        parts.append("%s didn't refresh today — %s rating below is from an earlier run." % (
            _join(list(stale)), "its" if len(stale) == 1 else "their"))

    parts.append("No setup = no trade." if is_call
                 else "Don't chase knives that already fell.")
    return [label, " ".join(parts)]
