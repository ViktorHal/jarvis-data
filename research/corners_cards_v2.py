"""Corners and cards, second attempt: what actually predicts them? (football-data.co.uk 2021/22-2025/26)

Candidates, all built only from information available before kick-off (walk-forward within each season):
  league   : this season's league means so far (home corners, away corners, yellow cards per match)
  team     : the scans' team averages (venue-specific with 5+ venue games), shrunk toward the league venue mean
  market   : Pinnacle's pre-match prices (PSH/PSD/PSA, over/under 2.5 P>2.5/P<2.5; market average when missing):
             the favourite presses and wins corners; a high goals line means an open game
  referee  : his yellow cards per match so far this season (5+ games), shrunk toward the league mean
Corners per side: mu_side = league_side * exp(b * (p_own - p_opp)) * (1 + c * (p_over25 - 0.5)) blended with the
shrunk team rate (weight w). Cards: league * exp(d * closeness) blended with referee and teams.
Parameters are chosen on 2021/22-2023/24 and tested on 2024/25-2025/26 against the league-only baseline.
"""
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corners_cards_backtest as B  # noqa: E402

FIT, TEST = B.FIT, B.TEST
C_LINES, TC_LINES, Y_LINES = (7.5, 8.5, 9.5, 10.5, 11.5), (2.5, 3.5, 4.5, 5.5, 6.5), (2.5, 3.5, 4.5, 5.5)


def novig(*o):
    if any(x is None or x <= 1 for x in o):
        return None
    inv = [1 / x for x in o]
    s = sum(inv)
    return [x / s for x in inv]


def load_rows(path):
    import csv, glob, io
    from datetime import datetime
    out = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(path, "*.csv"))):
        s, code = os.path.basename(f)[:-4].split("_", 1)
        raw = open(f, "rb").read().decode("utf-8-sig", "replace")
        for r in csv.DictReader(io.StringIO(raw)):
            ds = (r.get("Date") or "").strip()
            try:
                d = datetime.strptime(ds, "%d/%m/%Y" if len(ds) == 10 else "%d/%m/%y")
            except Exception:
                continue
            v = {k: B.num(r.get(k)) for k in ("HC", "AC", "HY", "AY", "HR", "AR")}
            if None in (v["HC"], v["AC"], v["HY"], v["AY"]):
                continue
            g = lambda *ks: next((B.num(r.get(k)) for k in ks if B.num(r.get(k))), None)
            x12 = novig(g("PSH", "AvgH", "B365H"), g("PSD", "AvgD", "B365D"), g("PSA", "AvgA", "B365A"))
            ou = novig(g("P>2.5", "Avg>2.5", "B365>2.5"), g("P<2.5", "Avg<2.5", "B365<2.5"))
            out[(s, code)].append({"date": d, "home": r["HomeTeam"], "away": r["AwayTeam"], "ref": (r.get("Referee") or "").strip(),
                                   "p": x12, "pov": ou[0] if ou else None, **v})
    for k in out:
        out[k].sort(key=lambda r: r["date"])
    return out


def walk(seasons, which):
    out = []
    for (s, code), rows in seasons.items():
        if s not in which:
            continue
        T = defaultdict(lambda: {"all": [0, 0.0, 0.0, 0.0], "home": [0, 0.0, 0.0], "away": [0, 0.0, 0.0]})
        R = defaultdict(lambda: [0, 0.0])
        L = [0, 0.0, 0.0, 0.0]                 # matches, home corners, away corners, yellows
        for m in rows:
            h, a = T[m["home"]], T[m["away"]]
            if h["all"][0] >= 5 and a["all"][0] >= 5 and L[0] >= 30 and m["p"] and m["pov"]:
                def cr(t, venue):
                    v = t[venue] if t[venue][0] >= 5 else t["all"]
                    return v[1] / v[0], v[2] / v[0], v[0]
                cfh, cah, nh = cr(h, "home")
                cfa, caa, na = cr(a, "away")
                ref = R.get(m["ref"])
                out.append({"season": s, "cf_h": cfh, "ca_h": cah, "n_h": nh, "cf_a": cfa, "ca_a": caa, "n_a": na,
                            "y_h": h["all"][3] / h["all"][0], "y_a": a["all"][3] / a["all"][0], "ny": min(h["all"][0], a["all"][0]),
                            "lh": L[1] / L[0], "la": L[2] / L[0], "ly": L[3] / L[0],
                            "ref_y": ref[1] / ref[0] if ref and ref[0] >= 5 else None, "ref_n": ref[0] if ref else 0,
                            "ph": m["p"][0], "pa": m["p"][2], "pov": m["pov"],
                            "C": m["HC"] + m["AC"], "HC": m["HC"], "AC": m["AC"], "Y": m["HY"] + m["AY"]})
            for t, side, cf, ca, y in ((h, "home", m["HC"], m["AC"], m["HY"]), (a, "away", m["AC"], m["HC"], m["AY"])):
                t["all"][0] += 1; t["all"][1] += cf; t["all"][2] += ca; t["all"][3] += y
                t[side][0] += 1; t[side][1] += cf; t[side][2] += ca
            if m["ref"]:
                R[m["ref"]][0] += 1; R[m["ref"]][1] += m["HY"] + m["AY"]
            L[0] += 1; L[1] += m["HC"]; L[2] += m["AC"]; L[3] += m["HY"] + m["AY"]
    return out


def corners(x, P):
    """-> expected corners (home, away)."""
    k = P["k"]
    # team part: own corners won and opponent's corners conceded, each shrunk toward the league venue mean
    th = ((x["n_h"] * x["cf_h"] + k * x["lh"]) / (x["n_h"] + k) + (x["n_a"] * x["ca_a"] + k * x["lh"]) / (x["n_a"] + k)) / 2
    ta = ((x["n_a"] * x["cf_a"] + k * x["la"]) / (x["n_a"] + k) + (x["n_h"] * x["ca_h"] + k * x["la"]) / (x["n_h"] + k)) / 2
    d = x["ph"] - x["pa"]
    tot = 1 + P["c"] * (x["pov"] - 0.5)
    mh, ma = x["lh"] * math.exp(P["b"] * d) * tot, x["la"] * math.exp(-P["b"] * d) * tot
    w = P["w"]
    return (w * th + (1 - w) * mh) * P["s"], (w * ta + (1 - w) * ma) * P["s"]


def cards(x, P):
    kt, kr = P["kt"], P["kr"]
    team = (x["ny"] * (x["y_h"] + x["y_a"]) + kt * x["ly"]) / (x["ny"] + kt)
    ref = (x["ref_n"] * x["ref_y"] + kr * x["ly"]) / (x["ref_n"] + kr) if x["ref_y"] else x["ly"]
    close = 1 - abs(x["ph"] - x["pa"])
    base = P["wt"] * team + P["wr"] * ref + (1 - P["wt"] - P["wr"]) * x["ly"]
    return base * math.exp(P["d"] * (close - 0.7)) * P["sy"]


def cdf_table(mu, r, kmax=30):
    ps, acc, out = 0.0, 0.0, []
    for k in range(kmax):
        acc += math.exp(B.nb_lp(k, mu, r))
        out.append(acc)
    return out


def ll_lines(k, lines, mu, r):
    cdf = cdf_table(mu, r)
    s = 0.0
    for L in lines:
        p = min(max(1 - cdf[int(L)], 1e-6), 1 - 1e-6)
        s -= math.log(p if k > L else 1 - p)
    return s / len(lines)


def score_c(S, P):
    tot = team = 0.0
    for x in S:
        mh, ma = corners(x, P)
        tot += ll_lines(x["C"], C_LINES, mh + ma, P["r"])
        team += (ll_lines(x["HC"], TC_LINES, mh, P["rt"]) + ll_lines(x["AC"], TC_LINES, ma, P["rt"])) / 2
    return round(tot / len(S), 5), round(team / len(S), 5)


def score_y(S, P):
    return round(sum(ll_lines(x["Y"], Y_LINES, cards(x, P), P["ry"]) for x in S) / len(S), 5)


def calib(S, P, kind, line):
    bins = defaultdict(lambda: [0, 0.0, 0])
    for x in S:
        if kind == "C":
            mh, ma = corners(x, P); mu, r, k = mh + ma, P["r"], x["C"]
        elif kind == "HC":
            mu, r, k = corners(x, P)[0], P["rt"], x["HC"]
        else:
            mu, r, k = cards(x, P), P["ry"], x["Y"]
        p = 1 - cdf_table(mu, r)[int(line)]
        b = bins[min(int(p * 10), 9)]; b[0] += 1; b[1] += p; b[2] += k > line
    return {f"{i/10:.1f}": [v[0], round(v[1] / v[0], 3), round(v[2] / v[0], 3)] for i, v in sorted(bins.items()) if v[0] >= 100}


if __name__ == "__main__":
    rows = load_rows(sys.argv[1])
    fit, test = walk(rows, FIT), walk(rows, TEST)
    print("samples", len(fit), len(test), flush=True)
    LEAGUE = {"k": 0, "w": 0.0, "b": 0.0, "c": 0.0, "s": 1.0, "r": 25, "rt": 15}
    # corners: dispersion first on the league baseline, then the predictors
    best = None
    for r in (10, 15, 25, 40):
        P = dict(LEAGUE, r=r, rt=r)
        v = score_c(fit, P)
        if best is None or v[0] < best[0][0]:
            best = (v, P)
    base_c = best[1]
    best = None
    for b in (0.0, 0.3, 0.6, 0.9):
        for c in (0.0, 0.3, 0.6):
            for w in (0.0, 0.3, 0.6):
                for k in (10, 20):
                    P = dict(base_c, b=b, c=c, w=w, k=k)
                    v = score_c(fit, P)
                    if best is None or v[0] + v[1] < best[0][0] + best[0][1]:
                        best = (v, P)
    PC = best[1]
    for rt in (8, 12, 20, 40):
        P = dict(PC, rt=rt)
        v = score_c(fit, P)
        if v[1] < score_c(fit, PC)[1]:
            PC = P
    # cards
    LEAGUE_Y = {"kt": 10, "kr": 10, "wt": 0.0, "wr": 0.0, "d": 0.0, "sy": 1.0, "ry": 10}
    best = None
    for ry in (5, 10, 20, 40):
        P = dict(LEAGUE_Y, ry=ry)
        v = score_y(fit, P)
        if best is None or v < best[0]:
            best = (v, P)
    base_y = best[1]
    best = None
    for wt in (0.0, 0.25, 0.5):
        for wr in (0.0, 0.25, 0.5):
            for d in (0.0, 0.3, 0.6):
                for sy in (0.95, 1.0):
                    P = dict(base_y, wt=wt, wr=wr, d=d, sy=sy)
                    v = score_y(fit, P)
                    if best is None or v < best[0]:
                        best = (v, P)
    PY = best[1]
    res = {"corners": {"params": PC, "league_params": base_c, "fit": {"league": score_c(fit, base_c), "model": score_c(fit, PC)},
                       "test": {"league": score_c(test, base_c), "model": score_c(test, PC)},
                       "calib_total_9.5": calib(test, PC, "C", 9.5), "calib_total_7.5": calib(test, PC, "C", 7.5),
                       "calib_home_4.5": calib(test, PC, "HC", 4.5)},
           "cards": {"params": PY, "league_params": base_y, "fit": {"league": score_y(fit, base_y), "model": score_y(fit, PY)},
                     "test": {"league": score_y(test, base_y), "model": score_y(test, PY)},
                     "calib_3.5": calib(test, PY, "Y", 3.5), "calib_4.5": calib(test, PY, "Y", 4.5)},
           "samples": {"fit": len(fit), "test": len(test)}}
    print(json.dumps(res, indent=1))
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "corners_cards_v2.json"), "w"), indent=1)
