"""NHL inputs for hockey model 1.2 (shot-based team strength + starting-goalie save records).

Writes data/nhl/h12_state.json: the 1.2 State after every completed NHL regular-season game, i.e. per team the
decay-weighted shots/goals for and against (sf, sa, gf, ga, n, lastDate, abbrev), per goalie [saves, shots
against] in starts since 2018/19, and the league totals. Production copies one match's numbers out of this file
with fetch/h12_match.py (never by hand) and decision.py prices the match with hockey12.py.

How it is built: data/nhl/h12_seed.json is the State after the 2018/19-2025/26 regular seasons, built once from
the "history" branch (fetch/h12_seed_nhl.py); this module replays the current season's games on top of it, so
the numbers are exactly those of the backtest's walk-forward. Source: api.nhle.com/stats/rest (free, keyless),
the same per-game team and goalie summaries as the history (about 20 requests per run).
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hockey12 as H  # noqa: E402
from common import DATA, Log, now_iso, write_json  # noqa: E402

SEASON = "20262027"
SEED = os.path.join(DATA, "nhl", "h12_seed.json")
URL = "https://api.nhle.com/stats/rest/en/{team,goalie}/summary?isGame=true (seasonId=%s, gameTypeId=2)" % SEASON


def games_from_rows(team_rows, goalie_rows, season):
    """Pair the two team rows of each game and attach the starting goalies (as research/hockey_backtest.load)."""
    games, starters = {}, defaultdict(dict)
    for r in team_rows:
        g = games.setdefault(r["gameId"], {"id": r["gameId"], "season": season, "date": r["gameDate"]})
        g["H" if r["homeRoad"] == "H" else "A"] = r
    for r in goalie_rows:
        if r.get("gamesStarted"):
            starters[r["gameId"]][r["teamAbbrev"]] = r
    out = []
    for g in games.values():
        if "H" not in g or "A" not in g:
            continue
        h, a = g["H"], g["A"]
        g["habbr"], g["aabbr"] = a["opponentTeamAbbrev"], h["opponentTeamAbbrev"]
        g["gs_h"], g["gs_a"] = starters[g["id"]].get(g["habbr"]), starters[g["id"]].get(g["aabbr"])
        out.append(g)
    return sorted(out, key=lambda g: (g["date"], g["id"]))


def replay(st, games):
    for g in games:
        st.update(g["season"], g["date"],
                  [(g[s]["teamFullName"], g[s]["shotsForPerGame"] or 0, g[s]["shotsAgainstPerGame"] or 0,
                    g[s]["goalsFor"], g[s]["goalsAgainst"]) for s in ("H", "A")],
                  [(x["goalieFullName"], x["saves"], x["shotsAgainst"]) for x in (g["gs_h"], g["gs_a"])
                   if x and x.get("shotsAgainst")])
        st.abbrev[g["H"]["teamFullName"]] = g["habbr"]
        st.abbrev[g["A"]["teamFullName"]] = g["aabbr"]
    return st


def build(seed, team_rows, goalie_rows, season=SEASON):
    st = H.State.from_snapshot(seed)
    games = games_from_rows(team_rows, goalie_rows, season)
    if seed.get("gamesThrough") and any(g["date"] <= seed["gamesThrough"] for g in games):
        raise ValueError("current-season games dated before the end of the seed")   # would count games twice
    return replay(st, games), games


def run(log):
    import history_nhl
    try:
        seed = json.load(open(SEED))
        team_rows, goalie_rows = history_nhl.pages("team", SEASON), history_nhl.pages("goalie", SEASON)
        st, games = build(seed, team_rows, goalie_rows)
        snap = st.snapshot()
        snap["meta"] = {"fetchedAt": now_iso(), "source": "api.nhle.com/stats/rest (NHL.com), per-game team and goalie summaries",
                        "season": SEASON, "seasonGames": len(games), "seedGamesThrough": seed.get("gamesThrough"),
                        "model": "hockey12.py " + H.VERSION, "hockey12Sha16": sha16()}
        write_json("nhl/h12_state.json", snap)
        log.ok("nhl h12 state", URL, len(games), f"{len(st.teams)} teams, {len(st.goalies)} goalies, through {st.through}")
    except Exception as e:
        log.fail("nhl h12 state", URL, e)


def sha16():
    import hashlib
    return hashlib.sha256(open(H.__file__, encoding="utf-8").read().encode("utf-8")).hexdigest()[:16]
