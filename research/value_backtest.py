"""The question that decides whether Jarvis can make money (27 Nov 2026):

  Over five full seasons (2021/22-2025/26, 16 leagues, football-data.co.uk), if Jarvis had bet every line its rules
  pass, at the prices BET365 actually offered before kick-off, would it have made money?

Probabilities for 1X2 and over/under 2.5 come from the production goal model (model.py) fed walk-forward with what
was known before each match. football-data has no xG, so two inputs are tested: goals per game (the robot's fallback)
and a shots-on-target proxy (0.3 goals per shot on target, blended 50/50 with goals) - both weaker than live xG.

Strategies, flat 1 unit, at Bet365's pre-match price (B365H/D/A, B365>2.5/<2.5):
  M   model only: Jarvis v1 minimum odds (max(1.03/p, 0.98/(p - 0.05)), band B) and p >= 0.60 (hit-rate profile)
  Mv  model only, no hit-rate floor (every value line)
  Bw  blend: p = w * model + (1 - w) * Pinnacle no-vig (same time), w fitted on 2021-24, tested on 2024-26
  S   sharp vs soft: bet when Bet365 pays at least 3% more than Pinnacle's no-vig fair price (no model at all)
  S+  S at the best of Bet365 / William Hill / Bwin (a proxy for shopping three bookmakers)
Log-loss of model, Pinnacle and blends is reported too. Matches in the first 6 rounds of a season are skipped
(not enough games for ratings).
"""
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE)))
import model  # noqa: E402

HIST = "/home/claude/hist/history/football"
LEAGUE_GOALS = {}


def f(x):
    try:
        v = float(x)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def novig(*ps):
    inv = [1 / p for p in ps]
    s = sum(inv)
    return [x / s for x in inv]


def probs(lh, la):
    g = model.score_grid(lh, la)
    n = len(g)
    ph = sum(g[i][j] for i in range(n) for j in range(n) if i > j)
    pd = sum(g[i][i] for i in range(n))
    pa = sum(g[i][j] for i in range(n) for j in range(n) if j > i)
    po = sum(g[i][j] for i in range(n) for j in range(n) if i + j > 2)
    t = ph + pd + pa
    return ph / t, pd / t, pa / t, po / (sum(map(sum, g)))


def season_rows(path):
    rows = []
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for r in csv.DictReader(fh):
            if not r.get("HomeTeam") or r.get("FTHG") in (None, ""):
                continue
            rows.append(r)
    return rows


def team_rate(hist, team, venue, proxy):
    """xgf/xga per game for a team: venue split blended 60/40 with the last six (as the robot builds it)."""
    allg = hist[team]
    if len(allg) < 6:
        return None
    ven = [g for g in allg if g["venue"] == venue]
    use = ven if len(ven) >= 4 else allg
    last = allg[-6:]

    def m(gs, key):
        return sum(g[key] for g in gs) / len(gs)
    kf, ka = ("pf", "pa") if proxy else ("gf", "ga")
    return 0.6 * m(use, kf) + 0.4 * m(last, kf), 0.6 * m(use, ka) + 0.4 * m(last, ka)


def build(proxy):
    out = []
    for path in sorted(glob.glob(os.path.join(HIST, "*.csv"))):
        season, lg = os.path.basename(path)[:4], os.path.basename(path)[5:-4]
        hist = defaultdict(list)
        for r in season_rows(path):
            h, a = r["HomeTeam"], r["AwayTeam"]
            hg, ag = int(r["FTHG"]), int(r["FTAG"])
            rh, ra = team_rate(hist, h, "H", proxy), team_rate(hist, a, "A", proxy)
            if rh and ra:
                inp = {"home": {"xgf": max(rh[0], 0.2), "xga": max(rh[1], 0.2)}, "away": {"xgf": max(ra[0], 0.2), "xga": max(ra[1], 0.2)}}
                lh, la = model.goal_rates(inp)
                ph, pd, pa, po = probs(lh, la)
                out.append({"season": season, "lg": lg, "hg": hg, "ag": ag, "p": [ph, pd, pa], "po": po,
                            "b365": [f(r.get("B365H")), f(r.get("B365D")), f(r.get("B365A"))],
                            "b365ou": [f(r.get("B365>2.5")), f(r.get("B365<2.5"))],
                            "ps": [f(r.get("PSH")), f(r.get("PSD")), f(r.get("PSA"))],
                            "psou": [f(r.get("P>2.5")), f(r.get("P<2.5"))],
                            "wh": [f(r.get("WHH")), f(r.get("WHD")), f(r.get("WHA"))],
                            "bw": [f(r.get("BWH")), f(r.get("BWD")), f(r.get("BWA"))],
                            "maxou": [f(r.get("Max>2.5")), f(r.get("Max<2.5"))]})
            hst, ast = f(r.get("HST")) or 0.0, f(r.get("AST")) or 0.0
            hst = float(r.get("HST") or 0)
            ast = float(r.get("AST") or 0)
            pf_h, pf_a = 0.5 * hg + 0.5 * 0.3 * hst, 0.5 * ag + 0.5 * 0.3 * ast
            hist[h].append({"venue": "H", "gf": hg, "ga": ag, "pf": pf_h, "pa": pf_a})
            hist[a].append({"venue": "A", "gf": ag, "ga": hg, "pf": pf_a, "pa": pf_h})
    return out


def minodds(p, band=0.05):
    a = 1.03 / p
    b = 0.98 / (p - band) if p - band > 0.01 else 99
    return math.ceil(max(a, b) * 100) / 100


def lines(r):
    """(market, outcome won?, model p, pinnacle novig p, b365 price, best-of-3 price)"""
    out = []
    y = [r["hg"] > r["ag"], r["hg"] == r["ag"], r["hg"] < r["ag"]]
    if all(r["ps"]) and all(r["b365"]):
        pn = novig(*r["ps"])
        for i in range(3):
            best = max(x for x in (r["b365"][i], r["wh"][i], r["bw"][i]) if x)
            out.append(("1x2", y[i], r["p"][i], pn[i], r["b365"][i], best))
    if all(r["psou"]) and all(r["b365ou"]):
        pn = novig(*r["psou"])
        o = r["hg"] + r["ag"] > 2
        out.append(("ou", o, r["po"], pn[0], r["b365ou"][0], r["b365ou"][0]))
        out.append(("ou", not o, 1 - r["po"], pn[1], r["b365ou"][1], r["b365ou"][1]))
    return out


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -math.log(p if y else 1 - p)


def sim(rows, rule):
    n = hits = 0
    pnl = 0.0
    per_season = defaultdict(lambda: [0, 0.0])
    for r in rows:
        for mk, y, pm, pp, price, best in lines(r):
            o = rule(pm, pp, price, best)
            if not o:
                continue
            n += 1
            hits += y
            g = (o - 1) if y else -1
            pnl += g
            per_season[r["season"]][0] += 1
            per_season[r["season"]][1] += g
    sd = math.sqrt(n) * 1.0 if n else 0
    return {"bets": n, "hit": round(hits / n, 3) if n else None, "roi": round(pnl / n * 100, 2) if n else None,
            "profit": round(pnl, 1), "roi95": [round((pnl - 1.96 * sd) / n * 100, 1), round((pnl + 1.96 * sd) / n * 100, 1)] if n else None,
            "bySeason": {k: {"bets": v[0], "roi": round(v[1] / v[0] * 100, 1)} for k, v in sorted(per_season.items()) if v[0]}}


def main():
    res = {}
    for proxy in (False, True):
        rows = build(proxy)
        name = "sot_proxy" if proxy else "goals"
        train = [r for r in rows if r["season"] in ("2122", "2223", "2324")]
        test = [r for r in rows if r["season"] in ("2425", "2526")]
        # log-loss and blend weight (fitted on train)
        def tot_ll(rs, w, mk):
            s = c = 0
            for r in rs:
                for m_, y, pm, pp, _, _ in lines(r):
                    if m_ != mk:
                        continue
                    s += ll(w * pm + (1 - w) * pp, y)
                    c += 1
            return s / c
        blend = {}
        for mk in ("1x2", "ou"):
            ws = [i / 20 for i in range(21)]
            best_w = min(ws, key=lambda w: tot_ll(train, w, mk))
            blend[mk] = {"w": best_w, "test_ll_model": round(tot_ll(test, 1, mk), 5), "test_ll_pinnacle": round(tot_ll(test, 0, mk), 5),
                         "test_ll_blend": round(tot_ll(test, best_w, mk), 5)}
        wb = {"1x2": blend["1x2"]["w"], "ou": blend["ou"]["w"]}

        rules = {
            "M (model, p>=0.60)": lambda pm, pp, price, best: price if pm >= 0.60 and price >= minodds(pm) else None,
            "Mv (model, any value)": lambda pm, pp, price, best: price if price >= minodds(pm) and price < 4 else None,
            "S (Bet365 >= Pinnacle fair +3%)": lambda pm, pp, price, best: price if price >= 1.03 / pp else None,
            "S+ (best of 3 >= Pinnacle fair +3%)": lambda pm, pp, price, best: best if best >= 1.03 / pp else None,
            "S2 (Bet365 >= Pinnacle fair +2%, p>=0.5)": lambda pm, pp, price, best: price if pp >= 0.5 and price >= 1.02 / pp else None,
        }
        out = {"matches": len(rows), "blend": blend}
        for label, rule in rules.items():
            out[label] = {"all": sim(rows, rule), "test_2425_2526": sim(test, rule)}
        for mk in ("1x2", "ou"):
            pass
        bl = lambda pm, pp, price, best, w=None: None
        def rule_blend(w1x2, wou):
            def r_(pm, pp, price, best, _w={"v": None}):
                return None
            return r_
        # blended rule needs the market type: run per market
        bres = {"bets": 0, "pnl": 0.0, "hits": 0}
        for r in test:
            for mk, y, pm, pp, price, best in lines(r):
                p = wb[mk] * pm + (1 - wb[mk]) * pp
                if p >= 0.55 and price >= minodds(p, 0.03):
                    bres["bets"] += 1
                    bres["hits"] += y
                    bres["pnl"] += (price - 1) if y else -1
        out["B (blend, p>=0.55, test seasons)"] = {"bets": bres["bets"], "hit": round(bres["hits"] / max(bres["bets"], 1), 3),
                                                     "roi": round(bres["pnl"] / max(bres["bets"], 1) * 100, 2)}
        res[name] = out
    os.makedirs(HERE, exist_ok=True)
    json.dump(res, open(os.path.join(HERE, "value_backtest.json"), "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
