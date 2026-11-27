"""Fit the corners and cards distributions to reality (27 Nov 2026).

The calibration run showed both are badly miscalibrated:
  corners  predicted mean 9.95 vs actual 9.75;  residual variance 11.34 against the model's assumed 16.5 (r=15)
  cards    predicted mean 4.41 vs actual 4.19;  residual variance 4.51 against the model's assumed 5.38 (r=20)
A distribution that is too wide makes every probability wrong: too low near the middle, too high in the tails.

Here a grid of (mean multiplier, dispersion r) is fitted on 2021/22-2023/24 by log-loss over every over/under
line, and the winner is checked on 2024/25-2025/26, which the fit never saw. The team-corner shrink factor is
fitted the same way. Nothing is promoted automatically: the numbers go to the owner.
"""
import json
import math
import os
import sys
from statistics import mean

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import model  # noqa: E402
import corners_cards_calib as C  # noqa: E402

TRAIN = ("2122", "2223", "2324")
TEST = ("2425", "2526")


def ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -math.log(p if y else 1 - p)


def rows_with_mu():
    rows = C.build()
    for r in rows:
        inp = dict(r["inp"])
        if r["ph"] and r["pd"] and r["pa"]:
            inv = [1 / r["ph"], 1 / r["pd"], 1 / r["pa"]]
            s = sum(inv)
            inp["market"] = {"ph": inv[0] / s, "pa": inv[2] / s}
        exp = {}
        model.corners_v2(inp, inp["home"], inp["away"], "H", "A", inp["league"], exp)
        model.cards_v2(inp, inp["home"], inp["away"], inp["league"], exp)
        r["mu_c"], r["mu_k"] = exp["corners"], exp["cards"]
        r["mu_ch"], r["mu_ca"] = exp["corners_home"], exp["corners_away"]
    return rows


def score(rows, key, mukey, lines, mult, rr):
    s = n = 0
    for r in rows:
        mu = r[mukey] * mult
        for ln in lines:
            p = model.nb_over(ln, mu, rr)
            s += ll(p, r[key] > ln)
            n += 1
    return s / n


def fit(rows, key, mukey, lines, mults, rs):
    tr = [r for r in rows if r["season"] in TRAIN]
    te = [r for r in rows if r["season"] in TEST]
    best = min(((score(tr, key, mukey, lines, m_, rr), m_, rr) for m_ in mults for rr in rs))
    _, m_, rr = best
    prod_m, prod_r = 1.0, (15 if key == "corners" else 20)
    return {"best": {"mult": m_, "r": rr},
            "train_ll": {"fitted": round(score(tr, key, mukey, lines, m_, rr), 5),
                         "production": round(score(tr, key, mukey, lines, prod_m, prod_r), 5)},
            "test_ll": {"fitted": round(score(te, key, mukey, lines, m_, rr), 5),
                        "production": round(score(te, key, mukey, lines, prod_m, prod_r), 5)},
            "test_n": len(te)}


def reliability(rows, key, mukey, lines, mult, rr, nb=10):
    b = [[0, 0, 0.0] for _ in range(nb)]
    for r in rows:
        mu = r[mukey] * mult
        for ln in lines:
            p = model.nb_over(ln, mu, rr)
            i = min(int(p * nb), nb - 1)
            b[i][0] += 1
            b[i][1] += 1 if r[key] > ln else 0
            b[i][2] += p
    return [{"band": f"{i/nb:.1f}-{(i+1)/nb:.1f}", "n": x[0], "meanP": round(x[2] / x[0], 3),
             "actual": round(x[1] / x[0], 3), "err": round(x[1] / x[0] - x[2] / x[0], 3)} for i, x in enumerate(b) if x[0] >= 300]


def main():
    rows = rows_with_mu()
    out = {"rows": len(rows)}
    mults = [0.94 + i * 0.01 for i in range(10)]
    rs = [12, 15, 20, 25, 30, 40, 50, 70, 100, 150, 300]
    out["corners"] = fit(rows, "corners", "mu_c", C.CORNER_LINES, mults, rs)
    out["cards"] = fit(rows, "cards", "mu_k", C.CARD_LINES, mults, rs)
    te = [r for r in rows if r["season"] in TEST]
    for k, mukey, lines in (("corners", "mu_c", C.CORNER_LINES), ("cards", "mu_k", C.CARD_LINES)):
        b = out[k]["best"]
        out[k]["reliability_test_production"] = reliability(te, k, mukey, lines, 1.0, 15 if k == "corners" else 20)
        out[k]["reliability_test_fitted"] = reliability(te, k, mukey, lines, b["mult"], b["r"])
    json.dump(out, open(os.path.join(HERE, "cc_fit.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
