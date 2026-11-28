"""Learn from the odds the owner types (owner, 27 Nov 2026): which kinds of "check the price" pick the bookmakers
actually pay. Only 1 of 15 checked picks was playable on 27 Nov; picks of a kind that the bookmakers nearly never
price at Jarvis' minimum waste his time.

  stats(bets)            -> {kind: {n, playable, share, medianRatio}}   ratio = best typed price / Jarvis' minimum
  verdict(kind, stats)   -> "show" | "lower" | "hide"
  python3 jarvis/bookfit.py stats  <bets.json>                 -> stats JSON
  python3 jarvis/bookfit.py filter <board.json> <bets.json>    -> board products with bookFit added; "hide" removed

A kind = market group + direction or player threshold, e.g. "Corners Under", "Player shots on target 1+".
An observation = a Jarvis pick without a public price (needsPrice) where he typed at least one bookmaker price
(BetMGM, Bet365, Svenska Spel) or marked the line as not offered (counts as not playable). Probabilities and
minimum odds are never changed: this only decides what is shown first, later or not at all.
"""
import json
import re
import sys
from statistics import median

BOOK_FIELDS = ("mgmOdds", "b365Odds", "svsOdds")
# 28 Nov 2026: four observations, not eight. On 27 Nov every single one of ten observations was unplayable and the
# best price was 84-91% of Jarvis' minimum (two card lines were not offered at all), yet eight-per-kind meant
# nothing could be acted on and the same picks filled the board again. Showing an unplayable pick costs him a slot
# a playable one could have had; hiding a kind costs almost nothing, because it comes straight back as soon as his
# own log shows a bookmaker paying. Hiding also needs the prices to be clearly off (median under 95% of the
# minimum) or the line to have been missing twice, so a kind that merely keeps just missing is only lowered.
MIN_N = 4            # observations of a kind before it can be lowered or hidden
HIDE_SHARE = 0.10    # playable in under 10% of the observations -> hidden
HIDE_RATIO = 0.95    # ... and the best price typically under 95% of the minimum
LOWER_SHARE = 0.25   # under 25% -> shown after everything else
MISSING_N = 2        # a line not offered at all this many times -> hidden whatever the prices did


def kind_of(group, selection):
    s = str(selection or "")
    m = re.search(r"(\d+)\+?\s*$", s)
    if m and str(group).startswith("Player"):
        return f"{group} {m.group(1)}+"
    m = re.search(r"\b(Over|Under)\b", s, re.I)
    if m:
        return f"{group} {m.group(1).title()}"
    if " over " in s.lower():
        return f"{group} Over"
    return str(group)


def _num(x):
    try:
        v = float(x)
        return v if v > 1 else None
    except (TypeError, ValueError):
        return None


def _legs(b):
    """(group, selection, minOdds) rows of a pick: a single, or each leg of a combo/builder is not used (their price
    is one price for the whole product)."""
    if b.get("legs"):
        return []
    g, sel = b.get("marketType"), b.get("selection") or b.get("pick")
    if b.get("key"):                     # the line key is exact; the pick text is free English
        try:
            from settle import parse_key
            kg, ks = parse_key(b["key"])
            if kg:
                g, sel = kg, ks.replace("_", " ")
        except Exception:
            pass
    return [(g, sel, _num(b.get("minOdds")))]


def stats(bets):
    obs = {}
    for b in bets:
        if b.get("source") != "jarvis" or not b.get("needsPrice"):
            continue
        prices = [p for p in (_num(b.get(f)) for f in BOOK_FIELDS) if p]
        not_off = b.get("notOffered") or []
        if not prices and not not_off:
            continue
        for g, sel, mo in _legs(b):
            if not g or not mo:
                continue
            k = kind_of(g, sel)
            best = max(prices) if prices else None
            obs.setdefault(k, []).append((best / mo) if best else 0.0)
    out = {}
    for k, rs in obs.items():
        pl = sum(1 for r in rs if r >= 1.0)
        out[k] = {"n": len(rs), "playable": pl, "share": round(pl / len(rs), 3),
                  "medianRatio": round(median(rs), 3), "missing": sum(1 for r in rs if r == 0.0),
                  "bestRatio": round(max(rs), 3)}
    return out


def verdict(kind, st):
    s = st.get(kind)
    if not s:
        return "show"
    if s.get("missing", 0) >= MISSING_N and s["playable"] == 0:
        return "hide"                      # the line itself is not on offer at his bookmakers
    if s["n"] < MIN_N:
        return "show"
    if s["share"] < HIDE_SHARE and s["medianRatio"] < HIDE_RATIO:
        return "hide"
    if s["share"] < HIDE_SHARE:
        return "lower"                     # it keeps just missing: worth showing last, not dropping
    if s["share"] < LOWER_SHARE:
        return "lower"
    return "show"


def _product_kinds(p):
    rows = [p["line"]] if "line" in p else [l.get("line", l) for l in p.get("legs", []) if isinstance(l, dict)]
    return [kind_of(r.get("group"), r.get("selection")) for r in rows if r.get("group")]


def filter_board(board, bets):
    st = stats(bets)
    keep, hidden = [], []
    for p in board.get("products", []):
        # only unpriced picks are judged: a pick with a market price is checked against that price already
        unpriced = "line" in p and p["line"].get("odds") is None or p.get("needsPrice")
        v = "show"
        if unpriced:
            vs = [verdict(k, st) for k in _product_kinds(p)]
            v = "hide" if "hide" in vs else "lower" if "lower" in vs else "show"
        p["bookFit"] = v
        (hidden if v == "hide" else keep).append(p)
    keep.sort(key=lambda p: (p["bookFit"] == "lower", p.get("rank", 0)))
    return {"products": keep, "hidden": [{"rank": p.get("rank"), "kinds": _product_kinds(p)} for p in hidden], "stats": st}


if __name__ == "__main__":
    if sys.argv[1] == "stats":
        print(json.dumps(stats(json.load(open(sys.argv[2], encoding="utf-8"))), indent=1, ensure_ascii=False))
    else:
        out = filter_board(json.load(open(sys.argv[2], encoding="utf-8")), json.load(open(sys.argv[3], encoding="utf-8")))
        print(json.dumps(out, indent=1, ensure_ascii=False))
