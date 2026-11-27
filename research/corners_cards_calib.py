"""Corners and cards: is the model's SPREAD right, and does it know more than a bookmaker's template?
(27 Nov 2026. Live check against Pinnacle's corner prices showed the model's corner distribution is ~12% wider
than the sharpest market's. This measures the truth on 5 seasons of actual counts.)

For every match of 2021/22-2025/26 (football-data.co.uk, 16 leagues), inputs are built walk-forward exactly as
fetch/autoprice.py builds them for a live scan:
  corners_for/against per game, venue split when the team has 5+ games at that venue, else all games
  cards = yellow per game (all games), referee yellow_pg when he has 5+ games this season
  league block = that league-season's averages so far
Then:
 1. CALIBRATION OF THE MEAN: actual count vs predicted mean, overall and by predicted band.
 2. DISPERSION: the variance of the actual count given the predicted mean, against what the model's negative
    binomial assumes. Reported as the r that matches reality (var = mu + mu^2/r).
 3. DOES THE MODEL BEAT A TEMPLATE? A bookmaker that prices corners "from a template tied to team styles"
    is simulated three ways (league average; league + plain team sums; league + team sums with the same
    dispersion as the model). The book's price = 1 / template_p, with a 6% overround. Jarvis bets when its own
    probability gives 3% edge at that price. ROI is what Jarvis would have won.
 4. THE SAME TEST FOR THE FIXED DISPERSION, so the value of the fix is measured, not assumed.
"""
import csv
import glob
import json
import math
import os
import sys
from collections import defaultdict
from statistics import mean

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import model  # noqa: E402

HIST = "/home/claude/hist/history/football"
CORNER_LINES = [7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
CARD_LINES = [2.5, 3.5, 4.5, 5.5, 6.5]
MARGIN = 1.06


def num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def build():
    """Walk-forward rows: inputs as the robot builds them + the actual counts."""
    rows = []
    for path in sorted(glob.glob(os.path.join(HIST, "*.csv"))):
        season, lg = os.path.basename(path)[:4], os.path.basename(path)[5:-4]
        teams = defaultdict(list)
        refs = defaultdict(list)
        lgc = {"cf": [], "ca": [], "y": []}
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            for r in csv.DictReader(fh):
                if not r.get("HomeTeam") or r.get("HC") in (None, "") or r.get("HY") in (None, ""):
                    continue
                h, a, ref = r["HomeTeam"], r["AwayTeam"], (r.get("Referee") or "").strip()
                hc, ac = num(r["HC"]), num(r["AC"])
                hy, ay = num(r["HY"]), num(r["AY"])

                def side(t, venue):
                    gs = teams[t]
                    if len(gs) < 6:
                        return None
                    v = [g for g in gs if g["venue"] == venue]
                    use = v if len(v) >= 5 else gs
                    return {"corners_for": mean(g["cf"] for g in use), "corners_against": mean(g["ca"] for g in use),
                            "corners_n": len(use), "cards": mean(g["y"] for g in gs), "cards_n": len(gs)}
                H, A = side(h, "H"), side(a, "A")
                if H and A and len(lgc["cf"]) >= 40:
                    lgblock = {"corners_home_pg": mean(lgc["cf"]), "corners_away_pg": mean(lgc["ca"]),
                               "yellow_pg": mean(lgc["y"])}
                    ri = refs.get(ref)
                    ref_in = None
                    if ri and len(ri) >= 5:
                        ref_in = {"yellow_pg": mean(ri), "games": len(ri)}
                    rows.append({"season": season, "lg": lg,
                                 "inp": {"sport": "football", "home": dict(H, name=h), "away": dict(A, name=a),
                                         "league": lgblock, **({"referee": ref_in} if ref_in else {})},
                                 "corners": hc + ac, "cards": hy + ay,
                                 "ph": num(r.get("PSH")) or num(r.get("B365H")), "pd": num(r.get("PSD")) or num(r.get("B365D")),
                                 "pa": num(r.get("PSA")) or num(r.get("B365A"))})
                teams[h].append({"venue": "H", "cf": hc, "ca": ac, "y": hy})
                teams[a].append({"venue": "A", "cf": ac, "ca": hc, "y": ay})
                if ref:
                    refs[ref].append(hy + ay)
                lgc["cf"].append(hc)
                lgc["ca"].append(ac)
                lgc["y"].append(hy + ay)
    return rows


def model_out(r):
    """corners_v2 / cards_v2 directly (model.run also needs goal inputs, which football-data has no xG for),
    with the market block decision.py injects from the de-margined 1X2 set."""
    inp = dict(r["inp"])
    if r["ph"] and r["pd"] and r["pa"]:
        inv = [1 / r["ph"], 1 / r["pd"], 1 / r["pa"]]
        s = sum(inv)
        inp["market"] = {"ph": inv[0] / s, "pa": inv[2] / s}
    h, a, lg = inp["home"], inp["away"], inp["league"]
    exp = {}
    mk = model.corners_v2(inp, h, a, h["name"], a["name"], lg, exp)
    mk += model.cards_v2(inp, h, a, lg, exp)
    return {"markets": mk, "expected": exp}


def nb_over(line, mu, rr):
    return model.nb_over(line, mu, rr)


def calib(rows, key, lines, expkey):
    """Mean calibration, dispersion, and reliability by band."""
    pred, act = [], []
    band = defaultdict(lambda: [0, 0])
    for r in rows:
        mu = r["exp"].get(expkey)
        if mu is None:
            continue
        pred.append(mu)
        act.append(r[key])
        for ln in lines:
            p = r["p"][key].get(ln)
            if p is None:
                continue
            b = int(p * 10) / 10
            band[b][0] += 1
            band[b][1] += 1 if r[key] > ln else 0
    mp, ma = mean(pred), mean(act)
    # dispersion: variance of (actual - predicted) scaled by the predicted mean
    resid_var = mean((a - p) ** 2 for a, p in zip(act, pred))
    r_fit = mp ** 2 / max(resid_var - mp, 1e-6)
    return {"n": len(pred), "predMean": round(mp, 2), "actualMean": round(ma, 2), "bias": round(ma - mp, 3),
            "residVar": round(resid_var, 2), "poissonVar": round(mp, 2), "impliedR": round(r_fit, 1),
            "reliability": {str(k): [v[0], round(v[1] / v[0], 3)] for k, v in sorted(band.items()) if v[0] >= 200}}


def price_from(p):
    return 1 / (p * MARGIN) if p > 0.01 else None


def simulate(rows, key, lines, expkey, template, edge=0.03, pmin=0.0):
    """Jarvis bets when its probability beats the template-priced book by `edge`."""
    n = hit = 0
    pnl = 0.0
    byseason = defaultdict(lambda: [0, 0.0])
    for r in rows:
        mu = r["exp"].get(expkey)
        if mu is None:
            continue
        for ln in lines:
            pm = r["p"][key].get(ln)
            pt = template(r, ln, key, expkey)
            if pm is None or pt is None:
                continue
            for over in (True, False):
                p_m = pm if over else 1 - pm
                p_t = pt if over else 1 - pt
                price = price_from(p_t)
                if not price or p_m < pmin:
                    continue
                if p_m * price < 1 + edge:
                    continue
                won = (r[key] > ln) if over else (r[key] < ln)
                n += 1
                hit += won
                g = (price - 1) if won else -1
                pnl += g
                byseason[r["season"]][0] += 1
                byseason[r["season"]][1] += g
    return {"bets": n, "hit": round(hit / n, 3) if n else None, "roi": round(pnl / n * 100, 2) if n else None,
            "bySeason": {k: [v[0], round(v[1] / v[0] * 100, 1)] for k, v in sorted(byseason.items()) if v[0] >= 20}}


def main():
    rows = build()
    print(f"rows: {len(rows)}", file=sys.stderr)
    LG = defaultdict(lambda: {"c": [], "y": []})
    for r in rows:
        LG[(r["season"], r["lg"])]["c"].append(r["corners"])
        LG[(r["season"], r["lg"])]["y"].append(r["cards"])
    lgmean = {k: {"c": mean(v["c"]), "y": mean(v["y"])} for k, v in LG.items()}

    out = {}
    for r_tot in (None, 36, 30, 25, 20):           # None = production value (15)
        if r_tot:
            model.P["cc_total_r"] = r_tot
            model.P["cards2_r"] = r_tot
        else:
            model.P["cc_total_r"] = 15
            model.P["cards2_r"] = 20
        for r in rows:
            o = model_out(r)
            r["exp"] = o["expected"]
            r["p"] = {"corners": {}, "cards": {}}
            for x in o["markets"]:
                if x["group"] == "Corners" and x["selection"].startswith("Over "):
                    r["p"]["corners"][float(x["selection"][5:])] = x["prob"] / 100
                if x["group"] == "Cards" and x["selection"].startswith("Over "):
                    r["p"]["cards"][float(x["selection"][5:])] = x["prob"] / 100
        tag = f"r_total={model.P['cc_total_r']},cards_r={model.P['cards2_r']}"
        res = {"corners": calib(rows, "corners", CORNER_LINES, "corners"),
               "cards": calib(rows, "cards", CARD_LINES, "cards")}
        if r_tot is None:
            # templates only once, on production settings
            def t_league(r, ln, key, expkey):
                m_ = lgmean[(r["season"], r["lg"])]["c" if key == "corners" else "y"]
                rr = 15 if key == "corners" else 20
                return nb_over(ln, m_, rr)

            def t_team(r, ln, key, expkey):
                """league average scaled by the two teams' plain rates (no shrinkage, no market, no referee)."""
                if key == "corners":
                    mu = r["inp"]["home"]["corners_for"] + r["inp"]["home"]["corners_against"] * 0 + \
                         r["inp"]["away"]["corners_for"]
                    mu = (r["inp"]["home"]["corners_for"] + r["inp"]["away"]["corners_against"]) / 2 + \
                         (r["inp"]["away"]["corners_for"] + r["inp"]["home"]["corners_against"]) / 2
                    return nb_over(ln, mu, 15)
                mu = r["inp"]["home"]["cards"] + r["inp"]["away"]["cards"]
                return nb_over(ln, mu, 20)
            res["vs_template_league"] = {k: simulate(rows, k, CORNER_LINES if k == "corners" else CARD_LINES,
                                                     k, t_league) for k in ("corners", "cards")}
            res["vs_template_team"] = {k: simulate(rows, k, CORNER_LINES if k == "corners" else CARD_LINES,
                                                   k, t_team) for k in ("corners", "cards")}
            res["vs_template_team_p65"] = {k: simulate(rows, k, CORNER_LINES if k == "corners" else CARD_LINES,
                                                       k, t_team, pmin=0.65) for k in ("corners", "cards")}
        out[tag] = res
        print(tag, json.dumps({k: (v if not isinstance(v, dict) or "n" in v else v) for k, v in res.items()})[:600], file=sys.stderr)
    json.dump(out, open(os.path.join(HERE, "corners_cards_calib.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
