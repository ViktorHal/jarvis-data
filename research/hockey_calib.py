"""Does hockey model 1.2 underrate favourites? (owner's observation, 26 Nov 2026: all six SHL favourites won
while the model rated every one of them below the market.)

No historical hockey odds are available here, so the test is calibration instead, which answers the same
question without a market: over the test seasons, when the model says a side wins (incl. overtime) with
probability p, how often does it actually win? A model that underrates favourites shows actual > predicted in
the high bands. Run walk-forward, test seasons only, both 1.1 and 1.2.

usage: python3 research/hockey_calib.py nhl <dir>   |   python3 research/hockey_calib.py shl <dir>
"""
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import hockey12 as H  # noqa: E402
import hockey_backtest as B  # noqa: E402


def bands(scores, key, nb=10):
    b = defaultdict(lambda: [0, 0.0, 0])
    for s in scores:
        for p, y in ((s[key], s["ym"]), (1 - s[key], not s["ym"])):
            i = min(int(p * nb), nb - 1)
            b[i][0] += 1
            b[i][1] += p
            b[i][2] += 1 if y else 0
    return [{"band": f"{i/nb:.1f}", "n": v[0], "pred": round(v[1] / v[0], 3), "actual": round(v[2] / v[0], 3),
             "err": round(v[2] / v[0] - v[1] / v[0], 3)} for i, v in sorted(b.items()) if v[0] >= 60]


def main():
    sport, path = sys.argv[1], sys.argv[2]
    if sport == "nhl":
        games = B.load(path)
        test = set(B.TEST)
        P = json.load(open(os.path.join(HERE, "hockey_backtest.json")))["chosenParams"]
    else:
        import shl_backtest as S
        games = S.load(path)
        test = set(S.TEST)
        P = json.load(open(os.path.join(HERE, "shl_backtest.json"))).get("chosenParams") or H.PARAMS_SHL
    sc = B.run(games, P, test)
    out = {"sport": sport, "games": len(sc), "params": P,
           "moneyline_1_1": bands(sc, "ml11"), "moneyline_1_2": bands(sc, "ml12")}
    fav = [s for s in sc if max(s["ml12"], 1 - s["ml12"]) >= 0.60]
    out["favourites_p>=60"] = {"n": len(fav),
                               "pred": round(sum(max(s["ml12"], 1 - s["ml12"]) for s in fav) / len(fav), 3),
                               "actual": round(sum(1 for s in fav if (s["ml12"] >= 0.5) == s["ym"]) / len(fav), 3)}
    print(json.dumps(out, indent=1))
    json.dump(out, open(os.path.join(HERE, f"hockey_calib_{sport}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()


def slope_test(sc, key):
    """Calibration slope: logistic regression of the outcome on logit(p). Slope > 1 means the model is
    under-confident (its probabilities should be more extreme), < 1 over-confident."""
    import math
    xs = [(math.log(min(max(s[key], 1e-6), 1 - 1e-6) / (1 - min(max(s[key], 1e-6), 1 - 1e-6))), 1 if s["ym"] else 0) for s in sc]
    a, b = 0.0, 1.0
    for _ in range(400):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in xs:
            p = 1 / (1 + math.exp(-(a + b * x)))
            g0 += y - p
            g1 += (y - p) * x
            w = p * (1 - p)
            h00 += w
            h01 += w * x
            h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        a += (h11 * g0 - h01 * g1) / det
        b += (h00 * g1 - h01 * g0) / det
    return {"intercept": round(a, 4), "slope": round(b, 4), "n": len(xs)}
