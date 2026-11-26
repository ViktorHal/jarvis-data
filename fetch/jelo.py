"""Jarvis Elo: club ratings from FotMob results (replacement for ClubElo, which GitHub's servers cannot reach).

One rating system over the domestic leagues below and the three UEFA club competitions, so clubs from different
leagues are comparable (the European ties link the leagues). Elo with home advantage and a goal-difference
weight, run chronologically from 2024/25. Seeds: every club starts at 1500 in 2024/25; a club that first appears
later starts at its league's mean minus 50 (a promoted side).
Writes data/elo/jelo.json: {meta, clubs: {fotmob id: {name, elo, league, games, lastDate}}, leagues: {name: {mean, clubs}}}.
Past seasons are cached in data/elo/jelo_results.json (fetched once); the current season is refetched once a day.
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, Log, get_json, now_iso, write_json  # noqa: E402

API = "https://www.fotmob.com/api/data/leagues?id={lid}"
DOMESTIC = [47, 48, 87, 140, 55, 86, 54, 146, 53, 110, 57, 40, 61, 71, 135, 64, 67, 38, 69, 46, 59, 122, 196, 252, 182, 441,
            136, 127]
EUROPE = [42, 73, 10216]
PAST = ["2023/2024", "2024/2025", "2025/2026"]
K, K_EUROPE, HA = 20.0, 30.0, 65.0
# Starting ratings per league (a prior: roughly the UEFA order of the leagues). Results move every club from here;
# after three seasons of league and European games the prior matters little. Unknown league: 1420.
SEED = {47: 1650, 87: 1620, 54: 1600, 55: 1600, 53: 1570, 61: 1530, 57: 1530, 40: 1500, 71: 1500,
        48: 1460, 64: 1460, 135: 1460, 38: 1460, 69: 1460, 46: 1460, 122: 1460,
        140: 1420, 86: 1420, 146: 1420, 59: 1420, 67: 1420, 196: 1420, 252: 1420, 182: 1420, 441: 1420, 127: 1420,
        136: 1420, 110: 1400}
CACHE = os.path.join(DATA, "elo", "jelo_results.json")


def results(lid, season=None):
    url = API.format(lid=lid) + (f"&season={quote(season, safe='')}" if season else "")
    js = get_json(url, timeout=60)
    name = (js.get("details") or {}).get("name") or str(lid)
    out = []
    name = f"{name} ({lid})"                      # names repeat across countries ("Premier League", "Super League")
    for m in js["fixtures"]["allMatches"]:
        st = m["status"]
        if not st.get("finished") or st.get("cancelled") or st.get("awarded") or not st.get("scoreStr"):
            continue
        try:
            hg, ag = [int(x) for x in st["scoreStr"].replace(" ", "").split("-")[:2]]
        except ValueError:
            continue
        out.append({"utc": st["utcTime"], "lid": lid, "league": name, "home": m["home"]["id"], "away": m["away"]["id"],
                    "hn": m["home"]["name"], "an": m["away"]["name"], "hg": hg, "ag": ag})
    return name, out


def weight(gd):
    gd = abs(gd)
    return 1.0 if gd <= 1 else 1.5 if gd == 2 else (11 + gd) / 8


def rate(matches):
    """Chronological Elo. Domestic leagues set a club's league; European matches only move ratings."""
    clubs = {}
    home_lid = {}                                   # each club's first domestic league, for its seed
    for m in sorted(matches, key=lambda m: m["utc"]):
        if m["lid"] not in EUROPE:
            home_lid.setdefault(m["home"], (m["lid"], m["league"]))
            home_lid.setdefault(m["away"], (m["lid"], m["league"]))
    for m in sorted(matches, key=lambda m: m["utc"]):
        dom = m["lid"] not in EUROPE
        for side, nm in (("home", "hn"), ("away", "an")):
            cid = m[side]
            if cid not in clubs:
                lid, lg = home_lid.get(cid, (None, None))
                members = [c["elo"] for c in clubs.values() if lg and c["league"] == lg]
                first = not members or m["utc"] < "2024-01-01"          # the first season: league seed
                seed = float(SEED.get(lid, 1420)) if first else sum(members) / len(members) - 50
                clubs[cid] = {"name": m[nm], "elo": seed, "league": lg, "games": 0, "lastDate": None}
            if dom:
                clubs[cid]["league"] = m["league"]
        h, a = clubs[m["home"]], clubs[m["away"]]
        e = 1 / (1 + 10 ** (-(h["elo"] + HA - a["elo"]) / 400))
        s = 1.0 if m["hg"] > m["ag"] else 0.5 if m["hg"] == m["ag"] else 0.0
        d = (K if dom else K_EUROPE) * weight(m["hg"] - m["ag"]) * (s - e)
        h["elo"] += d
        a["elo"] -= d
        for c in (h, a):
            c["games"] += 1
            c["lastDate"] = m["utc"][:10]
    leagues = {}
    for c in clubs.values():
        if c["league"]:
            leagues.setdefault(c["league"], []).append(c["elo"])
    return clubs, {k: {"mean": round(sum(v) / len(v), 1), "clubs": len(v)} for k, v in leagues.items()}


def run(log: Log):
    url = "fotmob.com/api/data/leagues (results)"
    try:
        out = os.path.join(DATA, "elo", "jelo.json")
        if os.path.exists(out):
            old = json.load(open(out, encoding="utf-8"))
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(old["meta"]["fetchedAt"].replace("Z", "+00:00"))).total_seconds()
            if age < 20 * 3600:
                log.ok("jarvis elo", url, len(old["clubs"]), "fresh (refetched once a day)")
                return
        cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
        matches, failed = [], []
        for lid in DOMESTIC + EUROPE:
            for season in PAST:
                key = f"{lid}|{season}"
                if key not in cache:
                    try:
                        cache[key] = results(lid, season)[1]
                    except Exception as e:
                        failed.append(f"{key}: {str(e)[:60]}")
                        continue
                    time.sleep(0.5)
                matches += cache[key]
            try:
                matches += results(lid)[1]               # current season
            except Exception as e:
                failed.append(f"{lid}|current: {str(e)[:60]}")
            time.sleep(0.5)
        write_json("elo/jelo_results.json", cache)
        clubs, leagues = rate(matches)
        write_json("elo/jelo.json", {"meta": {"fetchedAt": now_iso(), "source": "FotMob results, Elo computed by jarvis-data",
                   "matches": len(matches), "K": K, "homeAdvantage": HA, "failed": failed,
                   "note": "own Elo scale (not ClubElo); a rating is a pre-match input only for matches after fetchedAt"},
                   "clubs": {str(k): dict(v, elo=round(v["elo"], 1)) for k, v in clubs.items()}, "leagues": leagues})
        log.ok("jarvis elo", url, len(clubs), f"{len(matches)} matches, {len(failed)} league-seasons failed")
    except Exception as e:
        log.fail("jarvis elo", url, e)
