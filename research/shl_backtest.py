"""Backtest: hockey 1.1 vs 1.2 on the SHL regular seasons 2018-19 .. 2025-26 (stats.swehockey.se game pages).

Same walk-forward and scoring as research/hockey_backtest.py (NHL): every game is predicted only from earlier
games; 60-minute result (3-way) is the main score; moneyline incl. OT is scored on games that did not go to a
shootout whose winner is unknown in the data (the final score is kept from now on).
1.1 is scored twice: with the old draw inflation 1.08 and with production's 1.40.
1.2 is scored with the NHL parameters as they are, and with parameters chosen on the SHL FIT seasons only.

usage: python3 research/shl_backtest.py <dir with games_<season>.json>
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hockey12 as H  # noqa: E402
import hockey_backtest as B  # noqa: E402
import model  # noqa: E402

FIT = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23"]
TEST = ["2023-24", "2024-25", "2025-26"]


def load(path):
    out = []
    for f in sorted(glob.glob(os.path.join(path, "games_*.json"))):
        js = json.load(open(f, encoding="utf-8"))
        season = js["season"]
        for g in js["games"]:
            if not g.get("date") or not all(g.get("starters") or [None]):
                pass
            hg, ag = g["goals"]
            rh, ra = g["reg"]
            res3 = 0 if rh > ra else 2 if ra > rh else 1
            if "final" in g:
                hw = 1 if g["final"][0] > g["final"][1] else 0
            elif not g["shootout"]:
                hw = 1 if hg > ag else 0
            else:
                hw = None                                  # shootout winner not recorded
            row = lambda i: {"teamFullName": (g["home"], g["away"])[i], "shotsForPerGame": g["shots"][i],
                             "shotsAgainstPerGame": g["shots"][1 - i], "goalsFor": g["goals"][i], "goalsAgainst": g["goals"][1 - i]}
            gs = lambda i: ({"goalieFullName": g["starters"][i]["name"], "saves": g["starters"][i]["saves"],
                             "shotsAgainst": g["starters"][i]["shotsAgainst"],
                             "goalsAgainst": g["starters"][i]["shotsAgainst"] - g["starters"][i]["saves"]}
                            if g["starters"][i] else None)
            out.append({"id": g["id"], "season": season, "date": g["date"], "H": row(0), "A": row(1),
                        "hteam": g["home"], "ateam": g["away"], "gs_h": gs(0), "gs_a": gs(1),
                        "res3": res3, "homeWin": hw if hw is not None else 0, "mlKnown": hw is not None})
    return sorted(out, key=lambda g: (g["date"], g["id"]))


def scored(games, P, seasons, draw11):
    old = model.P["hockey_draw_inflation"]
    model.P["hockey_draw_inflation"] = draw11
    try:
        sc = B.run(games, P, set(seasons))
    finally:
        model.P["hockey_draw_inflation"] = old
    known = {g["id"] for g in games if g["mlKnown"]}
    return sc, [s for s in sc if s["id"] in known]


if __name__ == "__main__":
    games = load(sys.argv[1])
    print("games", len(games), {s: sum(g["season"] == s for g in games) for s in FIT + TEST}, flush=True)
    # 1.2 parameters chosen on the SHL FIT seasons only (small grid around the NHL choice)
    grid = [dict(H.PARAMS, half_life=hl, k_sh=ks, k_sv=kv, home_adv=ha, b2b=bb, draw_inflation=di)
            for hl in (25, 50, 90) for ks in (400, 800, 1600) for kv in (400, 800, 1600) for ha in (1.03, 1.06)
            for bb in (0.0, 0.10) for di in (1.25, 1.4, 1.5)]
    best = None
    for P in grid:
        sc, _ = scored(games, P, FIT, 1.40)
        v = sum(s["ll3_12"] for s in sc) / len(sc)
        if best is None or v < best[0]:
            best = (v, P)
    Pshl = best[1]
    res = {"fitSeasons": FIT, "testSeasons": TEST, "chosenParamsSHL": Pshl, "fitLogloss3": round(best[0], 5)}
    for name, P in (("nhlParams", dict(H.PARAMS)), ("shlParams", Pshl)):
        for d11 in (1.08, 1.40):
            sc, ml = scored(games, P, TEST, d11)
            s3, sm = B.summary(sc), B.summary(ml)
            res[f"{name}_vs_11draw{d11}"] = {"games": s3["games"], "logloss3": s3["logloss3"], "brier3": s3["brier3"],
                                             "m12_minus_m11_ll3": s3["m12_minus_m11_ll3"],
                                             "mlGames": sm["games"], "logloss_ml": sm["logloss_ml"],
                                             "m12_minus_m11_llml": sm["m12_minus_m11_llml"],
                                             "bySeason": {s: B.summary([x for x in sc if x["season"] == s])["logloss3"] for s in TEST},
                                             "drawCalibration_p11": s3["drawCalibration_p11"],
                                             "drawCalibration_p12": s3["drawCalibration_p12"]}
    print(json.dumps(res, indent=1))
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "shl_backtest.json"), "w"), indent=1)
