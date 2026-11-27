"""Backtest: hockey model 1.1 (production) vs candidate 1.2 on NHL regular seasons 2018/19-2025/26.

Every game is predicted only from games played BEFORE its date. Outcomes:
  60-minute result (3-way: home win in regulation / draw after 60 / away win in regulation)
  moneyline incl. OT (2-way)
Scored per game with log-loss and Brier; differences are paired per game with 95% intervals.
Model 1.1 is reproduced as the scans build it: gf = 0.5 * season + 0.5 * last 10 (per game), ga likewise,
then ga = mean(ga, starting goalie's season GAA) when the starter is known; home advantage 1.04.
1.2 parameters are chosen on the FIT seasons only and then frozen for the TEST seasons.
No historical market prices are available here, so this test cannot compare with the market.
"""
import glob
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import model  # noqa: E402
import hockey12 as H  # noqa: E402

FIT = ["20182019", "20192020", "20202021", "20212022", "20222023"]
TEST = ["20232024", "20242025", "20252026"]
MIN_GP = 10                         # both teams need 10 games this season before a game is scored


def load(path):
    games, goalies = {}, defaultdict(dict)
    for f in sorted(glob.glob(os.path.join(path, "team_*.json"))):
        season = f.split("_")[-1][:8]
        for r in json.load(open(f)):
            g = games.setdefault(r["gameId"], {"id": r["gameId"], "season": season, "date": r["gameDate"]})
            g["H" if r["homeRoad"] == "H" else "A"] = r
    for f in sorted(glob.glob(os.path.join(path, "goalie_*.json"))):
        for r in json.load(open(f)):
            if r.get("gamesStarted"):
                goalies[r["gameId"]][r["teamAbbrev"]] = r
    out = []
    for g in games.values():
        if "H" not in g or "A" not in g:
            continue
        h, a = g["H"], g["A"]
        if h["winsInRegulation"]:
            res3 = 0
        elif a["winsInRegulation"]:
            res3 = 2
        else:
            res3 = 1
        g.update({"res3": res3, "homeWin": 1 if h["wins"] else 0,
                  "hteam": h["teamFullName"], "ateam": a["teamFullName"],
                  "habbr": a["opponentTeamAbbrev"], "aabbr": h["opponentTeamAbbrev"],
                  "gs_h": goalies[g["id"]].get(a["opponentTeamAbbrev"]), "gs_a": goalies[g["id"]].get(h["opponentTeamAbbrev"])})
        out.append(g)
    return sorted(out, key=lambda g: (g["date"], g["id"]))


def p3_ml(lh, la, P=None):
    """3-way 60-minute and moneyline probabilities exactly as model.hockey prices them (1.2: with its own
    draw inflation via hockey12.price; 1.1: P=None, production settings)."""
    if P is None:
        out = model.run({"sport": "hockey", "home_adv": 1.0, "home": {"name": "H", "gf": lh, "ga": la},
                         "away": {"name": "A", "gf": la, "ga": lh}})["markets"]
    else:
        out = H.price(lh, la, ("H", "A"), P)["markets"]
    get = lambda grp, sel: next(x["prob"] for x in out if x["group"] == grp and x["selection"] == sel) / 100
    return (get("60-minute result", "H"), get("60-minute result", "Draw"), get("60-minute result", "A")), get("Moneyline (incl. OT)", "H")


def ll(p):
    return -math.log(min(max(p, 1e-6), 1))


def run(games, P, seasons_scored):
    """Walk through all games; return per-game scores for both models in the scored seasons."""
    st = H.State(P)                                       # 1.2: the same class the robot and production use
    season_rows = defaultdict(list)          # (season, team) -> [(gf, ga)] for 1.1
    goalie_season = defaultdict(lambda: [0.0, 0.0])       # (season, name) -> [goals_against, games]
    cur_season, last_date = None, {}
    scores = []
    for g in games:
        if g["season"] != cur_season:
            cur_season = g["season"]
        st.start_season(g["season"])                      # season carry happens before the first game is priced
        h, a = g["H"], g["A"]
        L = st.league()
        hs, as_ = season_rows[(cur_season, g["hteam"])], season_rows[(cur_season, g["ateam"])]
        if g["season"] in seasons_scored and len(hs) >= MIN_GP and len(as_) >= MIN_GP and st.lg["n"] > 500:
            # ---- 1.1 as the scans build it
            def team11(rows, gs):
                gf = 0.5 * sum(x[0] for x in rows) / len(rows) + 0.5 * sum(x[0] for x in rows[-10:]) / len(rows[-10:])
                ga = 0.5 * sum(x[1] for x in rows) / len(rows) + 0.5 * sum(x[1] for x in rows[-10:]) / len(rows[-10:])
                if gs:
                    rec = goalie_season[(cur_season, gs["goalieFullName"])]
                    if rec[1] >= 3:
                        ga = (ga + rec[0] / rec[1]) / 2
                return gf, ga
            gf_h, ga_h = team11(hs, g["gs_h"])
            gf_a, ga_a = team11(as_, g["gs_a"])
            lh11 = math.sqrt(gf_h * ga_a) * 1.04
            la11 = math.sqrt(gf_a * ga_h) / 1.04
            p11, ml11 = p3_ml(lh11, la11)
            # ---- 1.2
            gh = st.goalies.get(g["gs_h"]["goalieFullName"], [0.0, 0.0]) if g["gs_h"] else None
            ga_ = st.goalies.get(g["gs_a"]["goalieFullName"], [0.0, 0.0]) if g["gs_a"] else None
            b2b_h = last_date.get(g["hteam"]) == _prev(g["date"])
            b2b_a = last_date.get(g["ateam"]) == _prev(g["date"])
            lh12, la12 = H.goal_rates(st.teams.get(g["hteam"]) or H.Team(), st.teams.get(g["ateam"]) or H.Team(),
                                      gh, ga_, L, P, b2b_h, b2b_a)
            p12, ml12 = p3_ml(lh12, la12, P)
            y3, ym = g["res3"], g["homeWin"]
            scores.append({"season": g["season"], "id": g["id"], "ll3_11": ll(p11[y3]), "ll3_12": ll(p12[y3]),
                           "llm_11": ll(ml11 if ym else 1 - ml11), "llm_12": ll(ml12 if ym else 1 - ml12),
                           "br3_11": sum((p11[i] - (i == y3)) ** 2 for i in range(3)),
                           "br3_12": sum((p12[i] - (i == y3)) ** 2 for i in range(3)),
                           "p11": p11, "p12": p12, "y3": y3, "b2b": b2b_h or b2b_a,
                           "ml11": ml11, "ml12": ml12, "ym": ym})
        # ---- update states with this game
        st.update(g["season"], g["date"], game_rows(g), game_starters(g))
        for side in ("H", "A"):
            r = g[side]
            season_rows[(cur_season, r["teamFullName"])].append((r["goalsFor"], r["goalsAgainst"]))
            last_date[r["teamFullName"]] = g["date"]
        for gs in (g["gs_h"], g["gs_a"]):
            if gs and gs.get("shotsAgainst"):
                s = goalie_season[(cur_season, gs["goalieFullName"])]
                s[0] += gs["goalsAgainst"]; s[1] += 1
    return scores


def game_rows(g):
    """NHL stats REST team rows -> State.update rows (the per-game fields hold one game's totals)."""
    return [(g[s]["teamFullName"], g[s]["shotsForPerGame"] or 0, g[s]["shotsAgainstPerGame"] or 0,
             g[s]["goalsFor"], g[s]["goalsAgainst"]) for s in ("H", "A")]


def game_starters(g):
    return [(gs["goalieFullName"], gs["saves"], gs["shotsAgainst"]) for gs in (g["gs_h"], g["gs_a"])
            if gs and gs.get("shotsAgainst")]


def _prev(d):
    from datetime import date, timedelta
    return (date.fromisoformat(d) - timedelta(days=1)).isoformat()


def summary(scores):
    def ci(diff):
        n = len(diff)
        mu = sum(diff) / n
        sd = math.sqrt(sum((x - mu) ** 2 for x in diff) / (n - 1))
        return {"n": n, "mean": round(mu, 5), "lo95": round(mu - 1.96 * sd / math.sqrt(n), 5), "hi95": round(mu + 1.96 * sd / math.sqrt(n), 5)}
    out = {"games": len(scores),
           "logloss3": {"m11": round(sum(s["ll3_11"] for s in scores) / len(scores), 5), "m12": round(sum(s["ll3_12"] for s in scores) / len(scores), 5)},
           "logloss_ml": {"m11": round(sum(s["llm_11"] for s in scores) / len(scores), 5), "m12": round(sum(s["llm_12"] for s in scores) / len(scores), 5)},
           "brier3": {"m11": round(sum(s["br3_11"] for s in scores) / len(scores), 5), "m12": round(sum(s["br3_12"] for s in scores) / len(scores), 5)},
           "m12_minus_m11_ll3": ci([s["ll3_12"] - s["ll3_11"] for s in scores]),
           "m12_minus_m11_llml": ci([s["llm_12"] - s["llm_11"] for s in scores])}
    # calibration of the 60-minute draw (the hardest outcome)
    for k in ("p11", "p12"):
        bins = defaultdict(lambda: [0, 0.0, 0])
        for s in scores:
            p = s[k][1]
            b = min(int(p * 20), 19)
            bins[b][0] += 1; bins[b][1] += p; bins[b][2] += s["y3"] == 1
        out[f"drawCalibration_{k}"] = {f"{b/20:.2f}": [v[0], round(v[1] / v[0], 3), round(v[2] / v[0], 3)] for b, v in sorted(bins.items()) if v[0] >= 50}
    return out


if __name__ == "__main__":
    path = sys.argv[1]
    games = load(path)
    if "--check" in sys.argv:          # re-run the frozen choice on the test seasons only
        ref = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hockey_backtest.json")))
        t = summary(run(games, ref["chosenParams"], set(TEST)))
        print(json.dumps({"now": t["logloss3"], "stored": ref["test"]["logloss3"], "nowMl": t["logloss_ml"],
                          "storedMl": ref["test"]["logloss_ml"], "games": t["games"]}))
        sys.exit(0)
    print("games", len(games), {s: sum(g["season"] == s for g in games) for s in FIT + TEST})
    grid = [dict(H.PARAMS, half_life=hl, k_sh=ks, k_sv=kv, home_adv=ha, b2b=bb, draw_inflation=di)
            for hl in (15, 25, 40) for ks in (800, 2000) for kv in (800, 2000) for ha in (1.03, 1.05)
            for bb in (0.0, 0.06) for di in (1.08, 1.25, 1.40)]
    best = None
    for P in grid:
        sc = run(games, P, set(FIT))
        v = sum(s["ll3_12"] for s in sc) / len(sc)
        if best is None or v < best[0]:
            best = (v, P)
        print(f"fit {v:.5f} hl={P['half_life']} k_sh={P['k_sh']} k_sv={P['k_sv']} ha={P['home_adv']} b2b={P['b2b']} di={P['draw_inflation']}", flush=True)
    P = best[1]
    print("chosen on FIT seasons:", P)
    test = run(games, P, set(TEST))
    res = {"chosenParams": P, "fitLogloss3": round(best[0], 5), "test": summary(test),
           "bySeason": {s: summary([x for x in test if x["season"] == s]) for s in TEST},
           "b2bGames": summary([x for x in test if x["b2b"]])}
    print(json.dumps(res, indent=1))
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hockey_backtest.json"), "w"), indent=1)
