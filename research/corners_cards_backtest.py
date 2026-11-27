"""Walk-forward check of the corners and cards models on football-data.co.uk seasons 2021/22-2025/26.

Inputs are built the way the scans build them from data/football/teams_<code>.json (current season only):
corners_for/against per game venue-specific when the team has 5+ games at that venue, else all games; cards =
yellow cards per game (all games); referee yellow_pg (both teams) when he has 5+ games this season.
Every match is predicted only from earlier matches of the same season; both teams need 5+ games.
Outcome: total corners (HC+AC), team corners, total yellow cards (HY+AY).
FIT seasons 2021/22-2023/24 choose the parameters; TEST seasons 2024/25-2025/26 report.

usage: python3 research/corners_cards_backtest.py <dir with <season>_<code>.csv>
"""
import csv
import glob
import io
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import model  # noqa: E402

FIT, TEST = ("2122", "2223", "2324"), ("2425", "2526")
C_LINES, TC_LINES, Y_LINES = (8.5, 9.5, 10.5, 11.5), (3.5, 4.5, 5.5), (2.5, 3.5, 4.5, 5.5)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load(path):
    seasons = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(path, "*.csv"))):
        s, code = os.path.basename(f)[:-4].split("_", 1)
        raw = open(f, "rb").read().decode("utf-8-sig", "replace")
        rows = []
        for r in csv.DictReader(io.StringIO(raw)):
            try:
                d = datetime.strptime(r["Date"].strip(), "%d/%m/%Y" if len(r["Date"].strip()) == 10 else "%d/%m/%y")
            except Exception:
                continue
            v = {k: num(r.get(k)) for k in ("HC", "AC", "HY", "AY", "FTHG", "FTAG")}
            if None in (v["HC"], v["AC"], v["HY"], v["AY"]):
                continue
            rows.append({"date": d, "home": r["HomeTeam"], "away": r["AwayTeam"], "ref": (r.get("Referee") or "").strip(), **v})
        rows.sort(key=lambda r: r["date"])
        seasons[(s, code)] = rows
    return seasons


def nb_lp(k, mu, r):
    return math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r) + r * math.log(r / (r + mu)) + k * math.log(mu / (r + mu))


def walk(seasons, which):
    """-> list of samples with the scans' inputs and the outcomes (no parameters applied yet)."""
    out = []
    for (s, code), rows in seasons.items():
        if s not in which:
            continue
        T = defaultdict(lambda: {"all": [0, 0.0, 0.0, 0.0], "home": [0, 0.0, 0.0], "away": [0, 0.0, 0.0]})
        R = defaultdict(lambda: [0, 0.0])
        lg = [0, 0.0, 0.0]
        for m in rows:
            h, a = T[m["home"]], T[m["away"]]
            if h["all"][0] >= 5 and a["all"][0] >= 5:
                def cr(t, venue):
                    v = t[venue] if t[venue][0] >= 5 else t["all"]
                    return v[1] / v[0], v[2] / v[0]
                cfh, cah = cr(h, "home")
                cfa, caa = cr(a, "away")
                ref = R.get(m["ref"])
                out.append({"season": s, "code": code, "cf_h": cfh, "ca_h": cah, "cf_a": cfa, "ca_a": caa,
                            "y_h": h["all"][3] / h["all"][0], "y_a": a["all"][3] / a["all"][0],
                            "lg_c": lg[1] / lg[0] if lg[0] else None, "lg_y": lg[2] / lg[0] if lg[0] else None,
                            "ref_y": ref[1] / ref[0] if ref and ref[0] >= 5 else None,
                            "n_h": h["all"][0], "n_a": a["all"][0],
                            "C": m["HC"] + m["AC"], "HC": m["HC"], "AC": m["AC"], "Y": m["HY"] + m["AY"]})
            for t, side, cf, ca, y in ((h, "home", m["HC"], m["AC"], m["HY"]), (a, "away", m["AC"], m["HC"], m["AY"])):
                t["all"][0] += 1; t["all"][1] += cf; t["all"][2] += ca; t["all"][3] += y
                t[side][0] += 1; t[side][1] += cf; t[side][2] += ca
            if m["ref"]:
                R[m["ref"]][0] += 1; R[m["ref"]][1] += m["HY"] + m["AY"]
            lg[0] += 1; lg[1] += m["HC"] + m["AC"]; lg[2] += m["HY"] + m["AY"]
    return out


def corner_mu(x, P):
    k = P.get("shrink", 0)
    def sh(v, n):            # pull a per-game rate toward half the league's match total
        return (n * v + k * x["lg_c"] / 2) / (n + k) if k and x["lg_c"] else v
    mh = (sh(x["cf_h"], x["n_h"]) + sh(x["ca_a"], x["n_a"])) / 2 * P["corner_scale"]
    ma = (sh(x["cf_a"], x["n_a"]) + sh(x["ca_h"], x["n_h"])) / 2 * P["corner_scale"]
    return mh, ma


def card_mu(x, P):
    k = P.get("shrink_y", 0)
    def sh(v, n):
        return (n * v + k * x["lg_y"] / 2) / (n + k) if k and x["lg_y"] else v
    teams = sh(x["y_h"], x["n_h"]) + sh(x["y_a"], x["n_a"])
    w = P["ref_weight"]
    return P["card_scale"] * (w * x["ref_y"] + (1 - w) * teams if x["ref_y"] else teams)


def ll_over(k, line, mu, r):
    p = 1 - sum(math.exp(nb_lp(i, mu, r)) for i in range(int(line) + 1))
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -math.log(p if k > line else 1 - p), p


def score(samples, P):
    s = {"n": len(samples), "corners_ll": 0.0, "team_ll": 0.0, "cards_ll": 0.0, "bias_c": 0.0, "bias_y": 0.0}
    calib = defaultdict(lambda: [0, 0.0, 0])
    for x in samples:
        mh, ma = corner_mu(x, P)
        mu = mh + ma
        s["bias_c"] += mu - x["C"]
        for L in C_LINES:
            l, p = ll_over(x["C"], L, mu, P["corners_r"])
            s["corners_ll"] += l / len(C_LINES)
            b = calib[("C", L, min(int(p * 10), 9))]; b[0] += 1; b[1] += p; b[2] += x["C"] > L
        for L in TC_LINES:
            for k_, m_ in ((x["HC"], mh), (x["AC"], ma)):
                s["team_ll"] += ll_over(k_, L, m_, P["team_corners_r"])[0] / (2 * len(TC_LINES))
        my = card_mu(x, P)
        s["bias_y"] += my - x["Y"]
        for L in Y_LINES:
            l, p = ll_over(x["Y"], L, my, P["cards_r"])
            s["cards_ll"] += l / len(Y_LINES)
            b = calib[("Y", L, min(int(p * 10), 9))]; b[0] += 1; b[1] += p; b[2] += x["Y"] > L
    for k in ("corners_ll", "team_ll", "cards_ll", "bias_c", "bias_y"):
        s[k] = round(s[k] / max(s["n"], 1), 5)
    s["calibration"] = {f"{t} {L} p{b/10:.1f}": [v[0], round(v[1] / v[0], 3), round(v[2] / v[0], 3)]
                        for (t, L, b), v in sorted(calib.items()) if v[0] >= 200}
    return s


BASE = {k: model.P[k] for k in ("corner_scale", "corners_r", "team_corners_r", "card_scale", "cards_r", "ref_weight")}

if __name__ == "__main__":
    seasons = load(sys.argv[1])
    fit, test = walk(seasons, FIT), walk(seasons, TEST)
    print("samples fit", len(fit), "test", len(test), flush=True)
    base_fit, base_test = score(fit, BASE), score(test, BASE)
    best_c, best_t, best_y = None, None, None
    for sc in (0.85, 0.9, 0.95, 1.0):
        for r in (8, 12, 18, 25, 40):
            for k in (0, 5, 10, 20):
                P = dict(BASE, corner_scale=sc, corners_r=r, team_corners_r=r, shrink=k)
                v = score(fit, P)
                if best_c is None or v["corners_ll"] < best_c[0]:
                    best_c = (v["corners_ll"], P)
    for r in (6, 10, 15, 25):
        P = dict(best_c[1], team_corners_r=r)
        v = score(fit, P)
        if best_t is None or v["team_ll"] < best_t[0]:
            best_t = (v["team_ll"], P)
    for sc in (0.9, 1.0, 1.1):
        for r in (5, 10, 20, 40):
            for w in (0.0, 0.25, 0.5):
                for k in (0, 5, 10):
                    P = dict(best_t[1], card_scale=sc, cards_r=r, ref_weight=w, shrink_y=k)
                    v = score(fit, P)
                    if best_y is None or v["cards_ll"] < best_y[0]:
                        best_y = (v["cards_ll"], P)
    P = best_y[1]
    res = {"baseParams": BASE, "chosenParams": P, "fit": {"base": {k: base_fit[k] for k in base_fit if k != "calibration"},
                                                          "chosen": {k: v for k, v in score(fit, P).items() if k != "calibration"}},
           "test": {"base": base_test, "chosen": score(test, P)}}
    print(json.dumps({k: res[k] for k in ("baseParams", "chosenParams", "fit")}, indent=1))
    t = res["test"]
    print("TEST base", {k: t["base"][k] for k in ("n", "corners_ll", "team_ll", "cards_ll", "bias_c", "bias_y")})
    print("TEST new ", {k: t["chosen"][k] for k in ("n", "corners_ll", "team_ll", "cards_ll", "bias_c", "bias_y")})
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "corners_cards_backtest.json"), "w"), indent=1)
