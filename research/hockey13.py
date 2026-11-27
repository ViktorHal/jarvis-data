"""Hockey 1.3 candidate: sharpen 1.2's goal rates (27 Nov 2026).

research/hockey_calib.py found model 1.2 under-confident on both leagues: the calibration slope of its
moneyline is 1.34 (SHL) and 1.41 (NHL) where 1.0 is right, and it almost never prices a side above 70%, so
every clear favourite looks cheap against the market. 1.1 is slightly over-confident (slope 0.85-0.90).

The fix keeps the model's structure and sharpens the gap between the two goal rates, which carries through to
every market it prices (result, totals, puck line) instead of patching one number:
    m = (lam_h + lam_a) / 2
    lam' = m * (lam / m) ** g         then rescaled so the two rates still add up to lam_h + lam_a
g = 1 is today's model. g is chosen on the FIT seasons only and checked on the TEST seasons.
"""
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import hockey12 as H  # noqa: E402
import hockey_backtest as B  # noqa: E402
import hockey_calib as K  # noqa: E402
import shl_backtest as S  # noqa: E402


def sharpen(lh, la, g):
    if g == 1.0:
        return lh, la
    m = (lh + la) / 2
    a, b = m * (lh / m) ** g, m * (la / m) ** g
    s = (lh + la) / (a + b)
    return a * s, b * s


def rescore(games, P, seasons, g):
    """Re-run the walk-forward with sharpened rates. Mirrors hockey_backtest.run's 1.2 path."""
    out = []
    orig = H.goal_rates

    def patched(*a, **kw):
        lh, la = orig(*a, **kw)
        return sharpen(lh, la, g)
    H.goal_rates = patched
    try:
        sc = B.run(games, P, seasons)
    finally:
        H.goal_rates = orig
    return sc


def summarise(sc):
    n = len(sc)
    return {"games": n, "ll3": round(sum(s["ll3_12"] for s in sc) / n, 5),
            "llml": round(sum(s["llm_12"] for s in sc) / n, 5),
            "brier3": round(sum(s["br3_12"] for s in sc) / n, 5),
            "slope": K.slope_test(sc, "ml12")["slope"],
            "maxFavP": round(max(max(s["ml12"], 1 - s["ml12"]) for s in sc), 3)}


def main():
    grid = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8]
    out = {}
    for sport in ("shl", "nhl"):
        if sport == "shl":
            games, fit, test, P = S.load("/home/claude/hist2/history/shl"), set(S.FIT), set(S.TEST), H.PARAMS_SHL
        else:
            games, fit, test = B.load("/home/claude/hist2/history/nhl"), set(B.FIT), set(B.TEST)
            P = json.load(open(os.path.join(HERE, "hockey_backtest.json")))["chosenParams"]
        fits = {g: summarise(rescore(games, P, fit, g)) for g in grid}
        best = min(grid, key=lambda g: fits[g]["ll3"])
        tests = {g: summarise(rescore(games, P, test, g)) for g in (1.0, best)}
        out[sport] = {"fitSeasons": sorted(fit), "testSeasons": sorted(test), "fit": fits,
                      "chosen_g": best, "test": tests}
        print(sport, "chosen g", best, "fit", {g: fits[g]["ll3"] for g in grid},
              "test", {g: tests[g] for g in tests}, file=sys.stderr)
    json.dump(out, open(os.path.join(HERE, "hockey13.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
