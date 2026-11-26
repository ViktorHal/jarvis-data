"""Player shots and shots on target per 90 for the current season, from FotMob match data (all players, not only
a top-50 board): data/players_fm/<league slug>.json {meta, players: {name: {team, games, starts, minutes,
avg_start_minutes, shots, sot, shots_p90, sot_p90}}}.

Each finished match is fetched once and cached in data/players_fm/_matches_<slug>.json (about 10 new matches per
league per round). Counting rules as in research/player_shots_backtest.py: shots from FotMob's shot map (own goals
excluded), on target = isOnTarget and not blocked; minutes from FotMob's player stats.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fotmob  # noqa: E402
from common import DATA, Log, get_json, now_iso, write_json  # noqa: E402

LEAGUES = {"premier-league": 47, "championship": 48, "la-liga": 87, "serie-a": 55, "bundesliga": 54, "ligue-1": 53,
           "eredivisie": 57, "liga-portugal": 61, "scottish-premiership": 64, "allsvenskan": 67, "super-lig": 71,
           "belgian-pro-league": 40}
MAX_NEW = 400          # new match pages per run (the first run catches up over a few runs)


def table(matches):
    acc = {}
    for m in matches:
        for p in m["players"]:
            if p.get("minutes") is None:
                continue
            a = acc.setdefault(p["name"], {"team": None, "games": 0, "starts": 0, "minutes": 0, "start_minutes": 0,
                                           "shots": 0, "sot": 0, "lastDate": None})
            a["team"] = m["home"] if p["team"] == "home" else m["away"]
            a["games"] += 1
            a["minutes"] += p["minutes"]
            a["shots"] += p["shots"]
            a["sot"] += p["sot"]
            if p["started"]:
                a["starts"] += 1
                a["start_minutes"] += p["minutes"]
            a["lastDate"] = m["utc"][:10]
    out = {}
    for n, a in acc.items():
        if a["minutes"] <= 0:
            continue
        out[n] = {"team": a["team"], "games": a["games"], "starts": a["starts"], "minutes": a["minutes"],
                  "avg_start_minutes": round(a["start_minutes"] / a["starts"], 1) if a["starts"] else None,
                  "shots": a["shots"], "sot": a["sot"], "shots_p90": round(a["shots"] * 90 / a["minutes"], 3),
                  "sot_p90": round(a["sot"] * 90 / a["minutes"], 3), "lastDate": a["lastDate"]}
    return out


def run(log: Log):
    budget = MAX_NEW
    for slug, lid in LEAGUES.items():
        url = f"{fotmob.API}/leagues?id={lid}"
        try:
            cpath = os.path.join(DATA, "players_fm", f"_matches_{slug}.json")
            cache = {m["id"]: m for m in json.load(open(cpath, encoding="utf-8"))["matches"]} if os.path.exists(cpath) else {}
            fx = fotmob.fixtures(lid)
            new = 0
            for f in fx:
                if f["id"] in cache or budget <= 0:
                    continue
                md = get_json(f"{fotmob.API}/matchDetails?matchId={f['id']}")
                cache[f["id"]] = {"id": f["id"], "utc": f["status"]["utcTime"], "home": f["home"]["name"], "away": f["away"]["name"],
                                  "players": fotmob.players(md)}
                new += 1
                budget -= 1
                time.sleep(0.6)
            ms = sorted(cache.values(), key=lambda m: m["utc"])
            write_json(f"players_fm/_matches_{slug}.json", {"matches": ms})
            complete = all(f["id"] in cache for f in fx)
            write_json(f"players_fm/{slug}.json", {"meta": {"league": slug, "fotmobLeagueId": lid, "fetchedAt": now_iso(),
                       "matches": len(ms), "finished": len(fx), "complete": complete, "source": "FotMob match data",
                       "note": "shots from the shot map (own goals excluded); on target = on target and not blocked; "
                               "use only when complete is true (every finished match counted)"},
                       "players": table(ms)})
            log.ok(f"fotmob players {slug}", url, len(ms), f"{new} new matches" + ("" if complete else ", catching up"))
        except Exception as e:
            log.fail(f"fotmob players {slug}", url, e)
