"""FotMob's public JSON (the data behind fotmob.com): fixtures per league and per-match player data
(minutes, started, shots, shots on target). Used for player-shot calibration (history) and, later, inputs.

usage: python fetch/fotmob.py history <season e.g. 2024/2025> [league ids...]  -> history/fotmob/<lid>_<season>.json
"""
import json
import os
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, get_json  # noqa: E402

API = "https://www.fotmob.com/api/data"
LEAGUES = {47: "Premier League", 87: "La Liga", 55: "Serie A", 54: "Bundesliga", 53: "Ligue 1"}


def fixtures(lid, season=None):
    url = f"{API}/leagues?id={lid}" + (f"&season={quote(season, safe='')}" if season else "")
    js = get_json(url)
    return [m for m in js["fixtures"]["allMatches"] if m["status"].get("finished") and not m["status"].get("cancelled")]


def players(md):
    """matchDetails JSON -> [{id, name, team (home|away), started, minutes, shots, sot}] (players who played)."""
    c, g = md["content"], md["general"]
    hid = str(g["homeTeam"]["id"])
    out = {}
    for side in ("homeTeam", "awayTeam"):
        t = c["lineup"][side]
        team = "home" if str(t["id"]) == hid else "away"
        for p in t.get("starters", []):
            out[p["id"]] = {"id": p["id"], "name": p["name"], "team": team, "started": True, "minutes": None, "shots": 0, "sot": 0}
        for p in t.get("subs", []):
            ev = (p.get("performance") or {}).get("substitutionEvents") or []
            if any(e.get("type") == "subIn" for e in ev):
                out[p["id"]] = {"id": p["id"], "name": p["name"], "team": team, "started": False, "minutes": None, "shots": 0, "sot": 0}
    for pid, ps in (c.get("playerStats") or {}).items():
        pid = int(pid)
        if pid in out:
            for sec in ps.get("stats", []):
                v = (sec.get("stats") or {}).get("Minutes played")
                if v:
                    out[pid]["minutes"] = (v.get("stat") or {}).get("value")
    for s in (c.get("shotmap") or {}).get("shots", []):
        p = out.get(s.get("playerId"))
        if p and not s.get("isOwnGoal"):
            p["shots"] += 1
            p["sot"] += 1 if s.get("isOnTarget") and not s.get("isBlocked") else 0
    return list(out.values())


def history(season, lids):
    od = os.path.join(ROOT, "history", "fotmob")
    os.makedirs(od, exist_ok=True)
    for lid in lids:
        ms, errors = [], []
        for f in fixtures(lid, season):
            try:
                md = get_json(f"{API}/matchDetails?matchId={f['id']}")
                ms.append({"id": f["id"], "utc": f["status"]["utcTime"], "home": f["home"]["name"], "away": f["away"]["name"],
                           "score": f["status"].get("scoreStr"), "players": players(md)})
            except Exception as e:
                errors.append(f"{f['id']}: {str(e)[:80]}")
            time.sleep(0.6)
        with open(os.path.join(od, f"{lid}_{season.replace('/', '-')}.json"), "w", encoding="utf-8") as fh:
            json.dump({"league": LEAGUES.get(lid, lid), "season": season, "matches": ms, "errors": errors}, fh, ensure_ascii=False)
        print(lid, season, len(ms), "matches,", len(errors), "errors", flush=True)


if __name__ == "__main__" and sys.argv[1:2] == ["history"]:
    history(sys.argv[2], [int(x) for x in sys.argv[3:]] or list(LEAGUES))
