"""Player shots and shots on target, fitted SEPARATELY (27 Nov 2026).

research/player_shots_backtest.json still shows real miscalibration of the lines Jarvis actually offers:
  shots 1+   predicted 0.75, happened 0.78      (too little mass low down)
  shots 3+   predicted 0.44, happened 0.41
  SoT 2+     predicted 0.44, happened 0.30      (13.8 points too confident - a losing bet at any price)
One dispersion and one scale were fitted for both statistics together; they behave differently, so each gets
its own here, plus a separate rate multiplier. FIT = first half of the 2025/26 season, TEST = second half.
"""
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import player_shots_backtest as B  # noqa: E402

LINES = {"sot": (1, 2), "shots": (1, 2, 3)}


def score(S, stat, mult, r, calib=False):
    ll = n = 0
    cal = defaultdict(lambda: [0, 0.0, 0])
    for x in S:
        lam = max(x[f"{stat}_p90"] * x["avg_start_min"] / 90 * mult, 1e-6)
        for k in LINES[stat]:
            p = min(max(B.p_atleast(k, lam, r), 1e-6), 1 - 1e-6)
            y = x[stat] >= k
            ll -= math.log(p if y else 1 - p)
            n += 1
            if calib:
                b = cal[(k, min(int(p * 10), 9))]
                b[0] += 1
                b[1] += p
                b[2] += y
    out = {"ll": round(ll / n, 5), "n": n}
    if calib:
        out["calibration"] = {f"{k}+ p{b/10:.1f}": [v[0], round(v[1] / v[0], 3), round(v[2] / v[0], 3),
                                                    round(v[2] / v[0] - v[1] / v[0], 3)]
                              for (k, b), v in sorted(cal.items()) if v[0] >= 150}
    return out


def main():
    S = [x for x in B.load(sys.argv[1]) if x["shots_p90"] >= 1.5]
    fit = [x for x in S if x["half"] == "fit"]
    test = [x for x in S if x["half"] == "test"]
    out = {"starts": {"fit": len(fit), "test": len(test)}}
    for stat in ("shots", "sot"):
        best = min(((score(fit, stat, m_, r)["ll"], m_, r)
                    for m_ in [0.80 + i * 0.025 for i in range(13)]
                    for r in (2, 3, 4, 5, 6, 8, 10, 15, 25, 50, None)))
        _, m_, r = best
        out[stat] = {"production": {"mult": 1.0, "r": 3}, "fitted": {"mult": round(m_, 3), "r": r},
                     "fit_ll": {"production": score(fit, stat, 1.0, 3)["ll"], "fitted": round(best[0], 5)},
                     "test": {"production": score(test, stat, 1.0, 3, True),
                              "fitted": score(test, stat, m_, r, True)}}
        print(stat, out[stat]["fitted"], "test ll", out[stat]["test"]["production"]["ll"], "->",
              out[stat]["test"]["fitted"]["ll"], file=sys.stderr)
    json.dump(out, open(os.path.join(HERE, "player_fit2.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
