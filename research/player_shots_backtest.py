"""Player shots / shots on target: how well does Jarvis' player model predict them? (FotMob, 2025/26, top 5 leagues)

For every player who STARTED a match and had 450+ minutes earlier in the season, the season-to-date rates
before the match (shots per 90, shots on target per 90) give the model's expectation:
  lam = rate_per90 * minutes_factor * scale
where minutes_factor is either 90/90 (the scans' rule for a fit starter), the player's average minutes per start
so far, or a fixed 80/90. Counts are Poisson (production) or negative binomial with dispersion r.
Scored: P(SOT >= 1), P(SOT >= 2), P(shots >= 1, 2, 3), log-loss and calibration. Restricted to regular shooters
(shots per 90 >= 1.5), the kind of player the scans pick. FIT = first half of the season's matches, TEST = second.

usage: python3 research/player_shots_backtest.py <dir with <lid>_<season>.json>
"""
import glob
import json
import math
import os
import sys
from collections import defaultdict

LINES = {"sot": (1, 2), "shots": (1, 2, 3)}


def load(path):
    samples = []
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        js = json.load(open(f, encoding="utf-8"))
        acc = defaultdict(lambda: {"min": 0.0, "shots": 0, "sot": 0, "starts": 0, "start_min": 0.0})
        ms = sorted(js["matches"], key=lambda m: m["utc"])
        for i, m in enumerate(ms):
            for p in m["players"]:
                a = acc[p["id"]]
                if p["started"] and a["min"] >= 450 and p.get("minutes") is not None:
                    samples.append({"league": js["league"], "half": "fit" if i < len(ms) / 2 else "test",
                                    "shots_p90": a["shots"] * 90 / a["min"], "sot_p90": a["sot"] * 90 / a["min"],
                                    "avg_start_min": a["start_min"] / a["starts"] if a["starts"] else 80.0,
                                    "shots": p["shots"], "sot": p["sot"], "minutes": p["minutes"]})
            for p in m["players"]:
                mins = p.get("minutes")
                if mins is None:
                    continue
                a = acc[p["id"]]
                a["min"] += mins
                a["shots"] += p["shots"]
                a["sot"] += p["sot"]
                if p["started"]:
                    a["starts"] += 1
                    a["start_min"] += mins
    return samples


def p_atleast(k, lam, r=None):
    if r is None:
        return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k))
    cdf = sum(math.exp(math.lgamma(i + r) - math.lgamma(i + 1) - math.lgamma(r) + r * math.log(r / (r + lam))
                       + i * math.log(lam / (r + lam))) if lam > 0 else (1.0 if i == 0 else 0.0) for i in range(k))
    return 1 - cdf


def lam_of(x, stat, P):
    f = {"full": 1.0, "avg": x["avg_start_min"] / 90, "80": 80 / 90}[P["minutes"]]
    return x[f"{stat}_p90"] * f * P["scale"]


def score(S, P, calib=False):
    ll, n = 0.0, 0
    cal = defaultdict(lambda: [0, 0.0, 0])
    for x in S:
        for stat, lines in LINES.items():
            lam = max(lam_of(x, stat, P), 1e-6)
            for k in lines:
                p = min(max(p_atleast(k, lam, P["r"]), 1e-6), 1 - 1e-6)
                y = x[stat] >= k
                ll -= math.log(p if y else 1 - p)
                n += 1
                if calib:
                    b = cal[(stat, k, min(int(p * 10), 9))]
                    b[0] += 1; b[1] += p; b[2] += y
    out = {"ll": round(ll / n, 5), "n": len(S)}
    if calib:
        out["calibration"] = {f"{s} {k}+ p{b/10:.1f}": [v[0], round(v[1] / v[0], 3), round(v[2] / v[0], 3)]
                              for (s, k, b), v in sorted(cal.items()) if v[0] >= 150}
    return out


if __name__ == "__main__":
    S = [x for x in load(sys.argv[1]) if x["shots_p90"] >= 1.5]
    fit, test = [x for x in S if x["half"] == "fit"], [x for x in S if x["half"] == "test"]
    print("player-starts fit", len(fit), "test", len(test), flush=True)
    prod = {"minutes": "full", "scale": 1.0, "r": None}
    best = None
    for mins in ("full", "avg", "80"):
        for sc in (0.85, 0.9, 0.95, 1.0):
            for r in (None, 3, 6, 12):
                P = {"minutes": mins, "scale": sc, "r": r}
                v = score(fit, P)["ll"]
                if best is None or v < best[0]:
                    best = (v, P)
    res = {"production": prod, "chosen": best[1], "fit": {"production": score(fit, prod), "chosen": score(fit, best[1])},
           "test": {"production": score(test, prod, True), "chosen": score(test, best[1], True)}}
    print(json.dumps(res, indent=1))
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "player_shots_backtest.json"), "w"), indent=1)
