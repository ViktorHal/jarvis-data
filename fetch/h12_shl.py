"""SHL inputs for hockey model 1.2: data/shl/h12_state.json (same layout as data/nhl/h12_state.json).

data/shl/h12_seed.json = the 1.2 State (SHL parameters) after the regular seasons 2018-19..2025-26 from
stats.swehockey.se game pages (history-shl branch); each run replays this season's games from
data/shl/game_stats.json (written by shl_games.py just before) on top of it. No extra requests.
Goalie names as on stats.swehockey.se: "Surname, First".
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hockey12 as H  # noqa: E402
from common import DATA, Log, now_iso, write_json  # noqa: E402

SEASON = "2026-27"
SEED = os.path.join(DATA, "shl", "h12_seed.json")
STATS = os.path.join(DATA, "shl", "game_stats.json")


def replay(st, games, season):
    for g in sorted(games, key=lambda g: (g["date"], g["id"])):
        h, a = g["home"], g["away"]
        st.update(season, g["date"],
                  [(h, g["shots"][0], g["shots"][1], g["goals"][0], g["goals"][1]),
                   (a, g["shots"][1], g["shots"][0], g["goals"][1], g["goals"][0])],
                  [(s["name"], s["saves"], s["shotsAgainst"]) for s in g["starters"] if s and s.get("shotsAgainst")])
        st.abbrev[h], st.abbrev[a] = g["codes"][0], g["codes"][1]
    return st


def build(seed, games, season=SEASON):
    st = H.State.from_snapshot(seed)
    if seed.get("gamesThrough") and any(g["date"] <= seed["gamesThrough"] for g in games):
        raise ValueError("current-season games dated before the end of the seed")
    return replay(st, games, season)


def run(log: Log):
    try:
        stats = json.load(open(STATS, encoding="utf-8"))
        if stats["meta"].get("played") and stats["meta"]["games"] < stats["meta"]["played"]:
            raise ValueError(f"game_stats has {stats['meta']['games']} of {stats['meta']['played']} played games")
        st = build(json.load(open(SEED, encoding="utf-8")), stats["games"])
        snap = st.snapshot()
        snap["meta"] = {"fetchedAt": stats["meta"]["fetchedAt"], "builtAt": now_iso(), "season": SEASON,
                        "seasonGames": len(stats["games"]), "source": "stats.swehockey.se game pages via data/shl/game_stats.json",
                        "model": "hockey12.py " + H.VERSION}
        write_json("shl/h12_state.json", snap)
        log.ok("shl h12 state", "data/shl/game_stats.json", len(stats["games"]), f"{len(st.teams)} teams, through {st.through}")
    except Exception as e:
        log.fail("shl h12 state", "data/shl/game_stats.json", e)


if __name__ == "__main__" and sys.argv[1:2] == ["seed"]:
    import glob
    st = H.State(league="shl")
    files = sorted(glob.glob(os.path.join(sys.argv[2], "games_*.json")))
    allg = []
    for f in files:
        js = json.load(open(f, encoding="utf-8"))
        allg += [dict(g, _season=js["season"]) for g in js["games"]]
    for g in sorted(allg, key=lambda g: (g["date"], g["id"])):
        replay(st, [g], g["_season"])
    snap = st.snapshot()
    snap["meta"] = {"builtFrom": "history-shl branch (stats.swehockey.se game pages)", "files": [os.path.basename(f) for f in files]}
    write_json("shl/h12_seed.json", snap)
    print(len(st.teams), "teams", len(st.goalies), "goalies", st.games, "games through", st.through)
